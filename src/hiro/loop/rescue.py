"""认不出的画面交给外部 LLM 看图决策（用户 2026-11-03：「遇到了无法识别的弹窗 / 页面，就用外部的 LLM 来决策」）。

每遇到一种新弹窗 / 新页面就卡 3 分钟左右等人发现（第二十一局停机 34 分钟、第二十二局 28 分钟：通信エラー、轮间徽章偏移、
再開对话框、再挑戦确认框）。现在主循环在快速判页 / KAA / 补判链都认不出、画面持续没进展超过 RESCUE_AFTER 秒
（有认不出的带标题条弹窗时 RESCUE_AFTER_DIALOG 秒）时：
1. 截图（缩到 540×960）+ 整帧 OCR 文字框（编号、文字、坐标）+ 当前阶段和最近几步 → hiro.brain.llm 适配器 ask_json_image；
2. LLM 只能选：点第 k 个 OCR 框 / 点空白（BLANK_TAP）/ 等待 N 秒 / 停机报警，并写一句理由和它认为这是什么页面；
   「停机报警」= 报警（WARNING + trace）并放手：同一画面不再问，仍由原来的 30 秒报告 / 180 秒停机兜底。
   不直接停局：离线评测里一半的卡住画面是认不出的选择页（选卡 / 饮料满），以前等 30–70 秒会自己恢复，LLM 在这类页上都会说停；
3. 代码层安全规则（check）：画面任何一处含 SCREEN_DANGER（購入 / ジュエル / 破棄 / リタイア / 開始する / 課金 / 有償）就一律不点
   （连キャンセル也不点，停机交给人）；目标框含 TARGET_DANGER（再加「タイトルへ / 本戦へ / 選抜試験へ / 除去する / 削除する」）不点；
   选择页（「選んでください / 選択してください」）不点框，只能等待 / 点空白 / 停机（选择归决策层，不能变成默认选第一个）；点框前重新截图 OCR，框里文字和位置还对得上才点（LLM 要 25–40 秒，画面可能已经变了）；
   同一画面最多连续 MAX_PER_SCREEN 次、或连续两次给出同一动作画面没变，就停机；10 分钟内超过 MAX_PER_WINDOW 次也停；
   考试画面不走这条（主循环先判考试）；
4. 每次留档：reports/live/rescue/<时间>/{screen.jpg, record.json}，并写进 trace（recover:llm_rescue）；
   同类画面攒多了交识屏补签名。
开关：环境变量 HIRO_RESCUE_LLM=0 关掉（默认开）；模型同 HIRO_LLM（缺省 claude:sonnet）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from hiro import imgio
from hiro.vision.textnorm import contains, contains_any

logger = logging.getLogger(__name__)

LLM_UNKNOWN = os.environ.get("HIRO_RESCUE_LLM", "1") != "0"
RESCUE_AFTER = 12.0  # 认不出、画面没进展这么久才找 LLM
RESCUE_AFTER_DIALOG = 3.0  # 认不出的带标题条弹窗：这么久就找
RESCUE_EVERY = 6.0  # 两次 LLM 兜底之间至少隔这么久（让上一次动作生效）
MAX_PER_SCREEN = 3  # 同一画面最多连续几次
LLM_TIMEOUT = 60.0  # claude -p 带图平时 6–9 秒；第二十三局录像、负载 60–90 时 50–70 秒（两次 40 秒超时）
MAX_PER_WINDOW = (8, 600.0)  # 10 分钟内最多 8 次（画面在几个认不出的页之间来回）
VERIFY_DIST = 40  # 点框前复核：同样文字的框中心偏差不超过这么多像素
BLANK_TAP = (12, 12)  # 与 KAA skip() / 过场推进器同一点（ALT_TAP 附近有文字时才用；主循环本来就一直在点这里）
SAME_TEXT = 0.5  # 等回复期间：画面像素变了、但 OCR 文字有一半以上还在 = 同一画面（获得演出背景一直闪，第二十三局 4 次回复都被当成「画面已变」作废）
IMAGE_SIZE = (540, 960)
SAME_SCREEN_DIFF = 6.0  # 1/8 缩图灰度平均差小于这个算同一画面
OUT_DIR = Path("reports/live/rescue")

# 画面上有就一律不点（协调 2026-11-03 规则；「開始確認」：开局确认页的按钮是「H.I.F本戦へ」，没有「開始する」字样）
SCREEN_DANGER = ("購入", "ジュエル", "破棄", "リタイア", "開始する", "課金", "有償", "開始確認")
# 目标框含这些不点（通信エラー 有「タイトルへ / リトライ」：只禁点タイトルへ）
TARGET_DANGER = SCREEN_DANGER + ("タイトルへ", "本戦へ", "選抜試験へ", "除去する", "削除する")  # 删卡确认（决策 c39）
# 选择页（选卡 / 饮料满 / P 道具 / 轮间商店 / 定制…）是决策层的事：认不出时只能等待 / 点空白 / 停机，不点框（否则就是替决策层默认选择）
CHOICE_HINTS = ("選んでください", "選択してください", "選んで下さい")
ACTIONS = ("tap_box", "tap_blank", "wait", "stop")


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def _center(b) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def check(reply: dict | None, boxes) -> tuple[dict, str | None]:
    """校验 LLM 回复 → (要执行的动作, 拦截原因或 None)。动作：{action, box?, xy?, seconds?}；被拦截时 action=stop。"""
    if not isinstance(reply, dict) or reply.get("action") not in ACTIONS:
        return {"action": "stop"}, f"回复不合规：{reply!r}"[:200]
    texts = [_norm(t) for t, _, _ in boxes]
    danger = [w for w in SCREEN_DANGER if any(contains(t, w) for t in texts)]
    act = reply["action"]
    from hiro.loop.advance import EXAM_MARKS

    if act in ("tap_box", "tap_blank") and any(m in t for t in texts for m in EXAM_MARKS):
        return {"action": "stop"}, "考试画面不点（出牌 / SKIP 归考试决策器；(360,250) 是排名区）"
    if act in ("tap_box", "tap_blank") and danger:
        return {"action": "stop"}, f"画面有危险词 {danger}，不点任何地方"
    if act == "tap_box":
        k = reply.get("box")
        if not isinstance(k, int) or not 0 <= k < len(boxes):
            return {"action": "stop"}, f"框编号 {k!r} 不在列表里"
        text = texts[k]
        if contains_any(text, TARGET_DANGER):
            return {"action": "stop"}, f"目标框「{text}」含危险词"
        if any(h in t for t in texts for h in CHOICE_HINTS):
            return {
                "action": "stop"
            }, f"选择页（{next(t for t in texts if any(h in t for h in CHOICE_HINTS))}）交给决策层，不点「{text}」"
        return {"action": "tap_box", "box": k, "xy": _center(boxes[k][2]), "text": text}, None
    if act == "tap_blank":
        from hiro.loop.advance import ALT_TAP, alt_clear

        # 角落 (12,12) 主循环一直在点；LLM 判「点空白」时多半是角落点不掉的演出（第二十三局 P 道具效果说明条），点上方中间空白
        return {"action": "tap_blank", "xy": ALT_TAP if alt_clear(boxes) else BLANK_TAP}, None
    if act == "wait":
        s = reply.get("seconds")
        s = float(s) if isinstance(s, (int, float)) else 3.0
        return {"action": "wait", "seconds": max(1.0, min(10.0, s))}, None
    return {"action": "stop"}, None


def build_user(boxes, context: dict) -> str:
    lines = [f"{k}: {t.strip()} @ {_center(b)}" for k, (t, _, b) in enumerate(boxes)]
    return (f"程序当前：{json.dumps(context, ensure_ascii=False)}\n\n"
            f"OCR 文字框（编号: 文字 @ 坐标）：\n" + ("\n".join(lines) if lines else "（没读到文字）"))  # fmt: skip


def _thumb(img):
    import cv2

    return cv2.cvtColor(cv2.resize(img, (90, 160), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype("float32")


def _key(action: dict) -> tuple:
    return action.get("action"), action.get("box"), action.get("text")


class Rescuer:
    """主循环（HiroController）在认不出画面时调用 due() / tick()。llm / ocr / shoot / click / sleep / clock 可注入（离线回放、测试）。
    ocr(img) → [(text, conf, (x1, y1, x2, y2))]（整帧坐标，hiro.produce.ops.ocr_boxes 格式）。"""

    def __init__(self, llm=None, *, ocr: Callable | None = None, shoot: Callable | None = None,
                 click: Callable[[int, int], None] | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, out_dir: Path | None = OUT_DIR, enabled: bool | None = None):  # fmt: skip
        self._llm = llm
        self.ocr = ocr or _default_ocr
        self.shoot = shoot or _default_shoot
        self.click = click or _default_click
        self.sleep, self.clock = sleep, clock
        self.out_dir = out_dir
        self.enabled = LLM_UNKNOWN if enabled is None else enabled
        self.last_at = -1e9
        self.screen = None  # 上一次兜底时的画面缩图
        self.attempts = 0  # 同一画面连续兜底次数
        self.last_action: tuple | None = None
        self._job: dict | None = None  # 正在等的 LLM 回复
        self.gave_up = None  # 放弃了的画面缩图（同一画面不再问，交回原来的 30 秒报告 / 180 秒停机）
        self._pool = None
        self.times: list[float] = []
        self.log: list[dict] = []

    @property
    def llm(self):
        if self._llm is None:
            from hiro.brain.llm import from_spec

            try:
                self._llm = from_spec(None, timeout=LLM_TIMEOUT)
            except TypeError:  # mock 不收 timeout
                self._llm = from_spec(None)
        return self._llm

    def due(self, stuck_s: float, *, dialog: bool = False, img=None) -> bool:
        """该不该问 LLM：开着、认不出够久、离上次够远，且不是已经放弃的画面（LLM 说停 / 被拦截 / 次数用完后，同一画面不再问）。"""
        import numpy as np

        if not self.enabled or self.clock() - self.last_at < RESCUE_EVERY:
            return False
        if stuck_s < (RESCUE_AFTER_DIALOG if dialog else RESCUE_AFTER):
            return False
        if self.gave_up is not None and img is not None:
            if float(np.abs(_thumb(img) - self.gave_up).mean()) < SAME_SCREEN_DIFF:
                return False
            self.gave_up = None
        return True

    def tick(self, img, context: dict, boxes=None) -> dict:
        """同步版（离线评测 / 测试）：begin + 等回复 + 完成。"""
        rec = self.begin(img, context, boxes)
        if rec is not None:
            return rec
        self._job["future"].result()
        return self.poll()

    @property
    def pending(self) -> bool:
        return self._job is not None

    def begin(self, img, context: dict, boxes=None) -> dict | None:
        """开始一次兜底：次数超限直接返回停机记录；否则后台线程问 LLM（主循环照常跑），返回 None，之后每轮 poll()。
        boxes 为 None 时自己 OCR。"""
        import numpy as np

        now = self.clock()
        self.last_at = now
        self.times = [t for t in self.times if now - t < MAX_PER_WINDOW[1]] + [now]
        thumb = _thumb(img)
        same = self.screen is not None and float(np.abs(thumb - self.screen).mean()) < SAME_SCREEN_DIFF
        self.attempts = self.attempts + 1 if same else 1
        if not same:
            self.last_action = None
        self.screen = thumb
        rec: dict = {"attempts": self.attempts, "context": context, "page": None, "reason": None, "changed": False}
        if self.attempts > MAX_PER_SCREEN:
            rec.update(action={"action": "stop"}, blocked=f"同一画面已连续 {MAX_PER_SCREEN} 次 LLM 兜底仍没进展")
            return self._finish(rec, img)
        if len(self.times) > MAX_PER_WINDOW[0]:
            rec.update(
                action={"action": "stop"},
                blocked=f"{MAX_PER_WINDOW[1] / 60:.0f} 分钟内第 {len(self.times)} 次 LLM 兜底，画面在认不出的页之间打转",
            )
            return self._finish(rec, img)
        boxes = self.ocr(img) if boxes is None else boxes
        rec["boxes"] = [(t, list(b)) for t, _, b in boxes]
        if self._pool is None:
            from concurrent.futures import ThreadPoolExecutor

            self._pool = ThreadPoolExecutor(1, thread_name_prefix="llm-rescue")
        self._job = {"rec": rec, "img": img, "thumb": thumb, "boxes": boxes, "same": same,
                     "future": self._pool.submit(self._ask, img, boxes, context)}  # fmt: skip
        return None

    def poll(self) -> dict | None:
        """LLM 回复到了：在调用线程（主循环）里复核画面、校验、执行，返回记录；还没到返回 None。
        画面在等回复期间变了（有进展）：作废这次回复，不执行也不停机（changed=True）。"""
        import numpy as np

        job = self._job
        if job is None or not job["future"].done():
            return None
        self._job = None
        self.last_at = self.clock()  # 两次兜底的间隔从回复执行完算
        rec, img, thumb, boxes = job["rec"], job["img"], job["thumb"], job["boxes"]
        try:
            res, user = job["future"].result()
        except Exception as e:  # noqa: BLE001  _ask 自己兜了，这里只是保险
            from hiro.brain.llm import LLMResult

            res, user = LLMResult(None, "", 0.0, f"{type(e).__name__}: {e}", "error", None), ""
        rec.update(user=user, reply=res.data, raw=(res.raw or "")[:2000], error=res.error, seconds=round(res.seconds, 1),
                   model=f"{res.adapter}:{res.model}", prompt=_prompt_version())  # fmt: skip
        if res.data is not None:
            rec.update(page=res.data.get("page"), reason=res.data.get("reason"))
        now_img = self.shoot()
        if (
            now_img is not None
            and float(np.abs(_thumb(now_img) - thumb).mean()) >= SAME_SCREEN_DIFF
            and not self._same_texts(boxes, now_img)
        ):
            rec.update(action={"action": "none"}, changed=True, skipped="等 LLM 回复期间画面已变，作废这次回复")
            self.progressed()
            return self._finish(rec, img)
        if res.data is None:
            rec.update(action={"action": "stop"}, blocked=f"LLM 调用失败：{res.error}")
            return self._finish(rec, img)
        action, why = check(res.data, boxes)
        rec.update(action=action, blocked=why)
        if why is None and action["action"] != "stop" and job["same"] and self.last_action == _key(action):
            rec.update(action={"action": "stop"}, blocked=f"同一画面连续两次给出同一动作 {_key(action)}，画面没变")
            return self._finish(rec, img)
        if why is None:
            self.last_action = _key(action)
            rec["changed"] = self._execute(action, rec, thumb, now_img)
        return self._finish(rec, img)

    def _ask(self, img, boxes, context):
        from hiro.brain.llm import load_prompt

        import cv2

        small = cv2.resize(img, IMAGE_SIZE, interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        user = build_user(boxes, context)
        try:
            return self.llm.ask_json_image(load_prompt("rescue.md"), user, buf.tobytes()), user
        except Exception as e:  # noqa: BLE001  适配器不该抛，兜一下：按调用失败停机
            from hiro.brain.llm import LLMResult

            return LLMResult(None, "", 0.0, f"{type(e).__name__}: {e}", "error", None), user

    def _same_texts(self, boxes, img) -> bool:
        """像素变了时再看文字：原画面有 ≥3 个文字框、其中一半以上在新画面上还在 → 仍是同一画面（只是背景动画）。"""
        old = {_norm(t) for t, _, _ in boxes if len(_norm(t)) >= 2}
        if len(old) < 3:
            return False
        try:
            new = {_norm(t) for t, _, _ in self.ocr(img)}
        except Exception:  # noqa: BLE001
            return False
        return len(old & new) / len(old) >= SAME_TEXT

    def _execute(self, action: dict, rec: dict, before, now_img) -> bool:
        """执行动作，返回动作后画面是否变了。点框前用刚截的图复核框里文字和位置（LLM 想了 25–40 秒）。"""
        import numpy as np

        if action["action"] == "wait":
            self.sleep(action["seconds"])
        elif action["action"] == "tap_box":
            x, y = action["xy"]
            fresh = self.ocr(now_img) if now_img is not None else []
            if not any(_norm(t) == action["text"] and abs(_center(b)[0] - x) <= VERIFY_DIST and abs(_center(b)[1] - y) <= VERIFY_DIST
                       for t, _, b in fresh):  # fmt: skip
                rec["skipped"] = f"复核时没找到「{action['text']}」框，不点"
                return False
            self.click(x, y)
            self.sleep(1.0)
        elif action["action"] == "tap_blank":
            self.click(*action["xy"])
            self.sleep(1.0)
        after = self.shoot()
        return after is not None and float(np.abs(_thumb(after) - before).mean()) >= SAME_SCREEN_DIFF

    def _finish(self, rec: dict, img) -> dict:
        a = rec["action"]
        if a["action"] == "stop":
            self.gave_up = _thumb(img)
        logger.warning("LLM 兜底（同屏第 %d 次）：%s → %s%s（%s）%s", rec["attempts"], rec.get("page"), a.get("action"),
                       f"「{a.get('text')}」" if a.get("text") else "", rec.get("blocked") or rec.get("reason"),
                       f"；{rec['skipped']}" if rec.get("skipped") else "")  # fmt: skip
        if self.out_dir is not None:
            try:
                import cv2  # noqa: F401

                d = self.out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{len(self.log)}"
                d.mkdir(parents=True, exist_ok=True)
                imgio.imwrite(str(d / "screen.jpg"), img)
                rec["dir"] = str(d)
                (d / "record.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
                )
            except Exception:
                logger.warning("LLM 兜底留档失败", exc_info=True)
        self.log.append(rec)
        return rec

    def progressed(self) -> None:
        """画面认出来了 / 变了：清零同屏计数，丢掉还没回来的回复（回来也不执行）。"""
        self.screen, self.attempts, self.last_action = None, 0, None
        self._job, self.gave_up = None, None

    def summary(self) -> dict:
        """本局兜底统计：次数、各动作次数、被拦截次数、LLM 认为的页面。"""
        from collections import Counter

        return {
            "count": len(self.log),
            "actions": dict(Counter(r["action"]["action"] for r in self.log)),
            "blocked": sum(1 for r in self.log if r.get("blocked")),
            "pages": dict(Counter(r.get("page") or "?" for r in self.log)),
        }


def _prompt_version() -> str:
    from hiro.brain.llm import prompt_version

    return prompt_version("rescue.md")


def _default_ocr(img):
    from hiro.produce.ops import ocr_boxes

    return ocr_boxes(img)


def _default_shoot():
    from hiro.devices.active import device

    return device.screenshot()


def _default_click(x, y):
    from hiro.devices.active import device

    device.click(x, y)
