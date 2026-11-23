"""N.I.A 采样模式（新主循环）：从プロデュース选 N.I.A PRO 开局，共用页走现有处理器 + 3940a9 的 hajime_nia 规则，
认不出 / 没有处理器的页存帧、停下交人，人手动处理后 resume 接着跑。边跑边存帧，N.I.A 的识别从这批帧长起来。

    PYTHONPATH=src python -m hiro.runtime.nia_sample new    --idol i_card-skin-shro-3-007 [--difficulty pro] [--main-attr Da]
    PYTHONPATH=src python -m hiro.runtime.nia_sample resume --idol i_card-skin-shro-3-007

- KAA 不支持 N.I.A（produce.py 四处直接抛 UnsupportedProduceScenarioError），所以只能走新主循环。
- 开局：培育入口 → 剧本页翻到 N.I.A（Produce.LogoNia）→ OCR 点「プロ / PRO」（或 --difficulty master）→ 第 1–4 步同 H.I.F 选拔
  （选偶像 / 支援卡 + 租借 / 回忆核对 / 加成，编成号按开局编成方案）→ プロデュース開始。
- 状态里写 produce.mode = produce-004（PRO）/ produce-005（MASTER），决策器按它走 hiro.brain.hajime_nia。
- 认不出的画面：不点（Runtime 原来会连点空白，N.I.A 的新页面上不安全），连续 UNKNOWN_STOP_S 秒还认不出就存帧停下；
  没有处理器、也不在「点一下就过」名单里的页同样处理。横屏 LIVE 不算。
- 考试：判成 HIF_EXAM / EXAM / TOWER_EXAM 都按考试打，标签 nia-mid1 / nia-mid2 / nia-final（按进了几场，8a3a1e7 的中转层认）。
- 存帧：画面变了（灰度缩略图平均差 ≥ FRAME_DIFF）或页面名变了就存一张 JPEG q95，frames.jsonl 记 {seq, t, file, page, source}。
退出码：0 打完，3 停下交人（ResumeBlocked / 设备错误），2 参数 / 编成号缺。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import nav, opening

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MODES = {"pro": "produce-004", "master": "produce-005"}
DIFFICULTY_WORDS = {"pro": ("プロ", "PRO"), "master": ("マスター", "MASTER")}
DIFFICULTY_BAND = (700, 1000)  # 剧本页难度按钮的 y 范围（N.I.A 剧本页「プロ / マスター」约 y 876）
AP_COST = 20
UNKNOWN_STOP_S = 15.0
FRAME_DIFF = 4.0
DEFAULT_OUT = Path(".temp") / "nia-mac" / "frames"  # 相对仓库根目录（已被 .gitignore 挡住）
STAGES = ("nia-mid1", "nia-mid2", "nia-final")


# ---------------------------------------------------------------- 开局
def choose_difficulty(
    rt, difficulty: str, timeout: float = opening.MODE_TIMEOUT, *, words: tuple[str, ...] | None = None
) -> None:
    """剧本页点难度按钮（OCR 找「プロ / PRO」），直到进了第 1 步。AP 不足只喝 APドリンク（同 opening.choose_mode）。
    words：按钮字样（初的「レギュラー / REGULAR」由 hiro.runtime.hajime 传），不给按 DIFFICULTY_WORDS。"""
    from hiro.loop import ap_refill

    words = words or DIFFICULTY_WORDS[difficulty]
    state = {"refilled": False}

    def step(f):
        if nav.find(f, "Produce.TextStepIndicator1") is not None:
            return True
        if nav.find(f, "Produce.TextAPInsufficient") is not None:
            if state["refilled"]:
                raise ResumeBlocked("喝过 APドリンク 后仍然 AP 不足，停下")
            r = ap_refill.refill(lambda: rt.screen.shot().img, rt.device.tap, opening._ocr, extra_ap=0, sleep=rt.sleep, clock=rt.clock,
                                 log=logger.warning)  # fmt: skip
            if not r["ok"]:
                raise ResumeBlocked(f"AP 不足，APドリンク 没补上：{r['error']}")
            state["refilled"] = True
            return None
        lo, hi = DIFFICULTY_BAND
        btn = next(
            (t for t in f.texts if lo <= t.rect.center.y <= hi and any(w == t.text.replace(" ", "") for w in words)),
            None,
        )
        if btn is not None:
            logger.info("剧本页难度：点「%s」%s", btn.text, tuple(btn.rect.center))
            rt.device.tap(*btn.rect.center)
            rt.sleep(1.5)
        return None

    rt.drive(step, timeout, f"进 {difficulty}")


def new_produce(rt, difficulty: str, **prep) -> None:
    """培育入口 → N.I.A → 难度 → 第 1–4 步（同 H.I.F 选拔）→ プロデュース開始（使用者放行开局后才跑）。"""
    if opening.enter_produce(rt, new=True) != "new":
        raise ResumeBlocked("没进到剧本选择页")
    opening.select_scenario(rt, "Produce.LogoNia")
    choose_difficulty(rt, difficulty)
    opening.prepare(rt, "qualify", **prep)
    opening.start(rt)


# ---------------------------------------------------------------- 采样：存帧 + 判页改名 + 认不出就停
class Sampler:
    """包住 classify：存帧（画面或页面名变了）；TOWER_EXAM 当考试（N.I.A 试镜可能被偶像之路的考试判据认中），其余 TOWER_* 当认不出。"""

    def __init__(self, classify, out: Path, clock=time.monotonic, *, nia: bool = True):
        self.classify, self.out, self.clock, self.nia = (
            classify,
            Path(out),
            clock,
            nia,
        )  # nia=False：不整帧 OCR 找 N.I.A 特有页（初用）
        self.out.mkdir(parents=True, exist_ok=True)
        self.index = self.out / "frames.jsonl"
        self.seq = sum(1 for _ in self.index.open(encoding="utf-8")) if self.index.exists() else 0
        self.last_thumb, self.last_page = None, None

    NIA_RECHECK = ("unknown", "DIALOG", "commu", "IDLE", "INITIAL_CHOICE")  # 这些判法下再整帧 OCR 看是不是 N.I.A 特有页

    def __call__(self, img):
        hit = self.classify(img)
        if not self.nia and hit.name == "unknown" and img is not None and img.shape[0] > img.shape[1]:
            # 初：认不出时只查考前说明页（審査基準 + 合格条件 + 倍率）。广那几局靠「タップして次へ」模板当过场点过去；
            # ことね（初 PRO 11-19）亮色衣装压住了提示，模板 0.23，判 unknown 180 秒停机
            from hiro.vision.classify import PageHit
            from hiro.vision.nia import read_briefing
            from hiro.vision.ocr import screen_texts

            if (brief := read_briefing(screen_texts(img), img)) is not None:
                hit = PageHit("HIF_EXAM_BRIEFING", "briefing_ocr", "produce", brief)
        if self.nia and hit.name in self.NIA_RECHECK and img is not None and img.shape[0] > img.shape[1]:
            from hiro.vision.classify import PageHit
            from hiro.vision.nia import read_nia_page
            from hiro.vision.ocr import screen_texts

            # 営業场所的黄色卡片被当成弹窗标题条（DIALOG）、试镜选档被当成剧情（commu）、営業话题认不出（N.I.A 采样 11-17）
            if (nia := read_nia_page(screen_texts(img), img)) is not None:
                hit = PageHit(nia[0], "nia_ocr", "produce", nia[1])
        if hit.name == "TOWER_EXAM":
            import dataclasses

            hit = dataclasses.replace(hit, name="EXAM")
        elif hit.name.startswith("TOWER_"):
            from hiro.vision.classify import UNKNOWN

            hit = UNKNOWN
        self.save(img, hit)
        return hit

    def save_tap(self, frame, x: int, y: int) -> None:
        """每次点击前存一帧（点之前看到的画面），页面名记成 tap-x-y（开局阶段 rt.drive 不走 classify，原来一帧都没存）。"""
        from hiro.vision.classify import PageHit

        if frame is not None and frame.img is not None:
            self.save(frame.img, PageHit(f"tap-{x}-{y}", "tap"), force=True)

    def save(self, img, hit, force: bool = False) -> None:
        if img is None:
            return
        thumb = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (90, 160)).astype(np.float32)
        d = 255.0 if self.last_thumb is None else float(np.abs(thumb - self.last_thumb).mean())
        if not force and d < FRAME_DIFF and hit.name == self.last_page:
            return
        self.seq += 1
        name = f"{self.seq:05d}-{time.strftime('%H%M%S')}-{hit.name}.jpg"
        cv2.imwrite(str(self.out / name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        with self.index.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": self.seq, "t": round(time.time(), 2), "file": name, "page": hit.name, "source": getattr(hit, "source", None),
                                "diff": round(d, 2)}, ensure_ascii=False) + "\n")  # fmt: skip
        self.last_thumb, self.last_page = thumb, hit.name


def make_advance(sampler: Sampler, stop_s: float = UNKNOWN_STOP_S):
    """采样用的 advance：认不出的画面不点，连续 stop_s 秒还认不出就存帧停下；没处理器、也点不过去的已知页同样计时。
    结算流程（回忆照片 / 突破证书）和其余已知页照旧交给 play.advance。"""
    from hiro.runtime import play

    state = {"since": None, "page": None, "quiet": 0, "unknown_since": None}

    def advance(rt, frame, hit) -> bool:
        landscape = frame.img is not None and frame.img.shape[1] > frame.img.shape[0]
        if hit.name != "unknown":
            state["quiet"], state["unknown_since"] = 0, None
        if hit.name == "unknown" and not landscape and _connecting(frame.img):
            state["since"] = None  # 右下「CONNECTING」：通信中，当加载等（N.I.A 试镜后 11-17：认不出，119 秒才停）
            return False
        if hit.name == "unknown" and not play._produce_finished(frame) and not landscape:
            did = _nia_bubble(rt, frame) or _event_art(rt, frame) or _quiet_tap(rt, frame, state)
        else:
            did = play.advance(rt, frame, hit)
        if did or landscape:
            state["since"] = None
            return did
        now = rt.clock()
        if state["since"] is None or state["page"] != hit.name:
            state["since"], state["page"] = now, hit.name
            return False
        if now - state["since"] >= stop_s:
            shot = _save_stop_frame(rt, sampler, frame, hit)
            raise ResumeBlocked(
                f"N.I.A 采样：{hit.name} 页 {now - state['since']:.0f}s 认不出 / 没有处理器，存帧 {shot}，停下交人"
            )
        return False

    return advance


QUIET_WAIT_S = 3.0  # 同一段认不出持续这么久才点（转场自己会走完的不点）
QUIET_TAP_MAX = 6  # 一段认不出最多点这么多下；还没变就照旧 stop_s 后停下交人
QUIET_BUTTON_Y = (1050, 1180)  # 底部按钮区：这里有字就不点


def _quiet_tap(rt, frame, state) -> bool:
    """没有任何界面元素的过场 / 剧情（あさり先生白板讲解、休む后偶像站着、「最終オーディションまで」过场…，协调 11-18：N.I.A 第二局
    这类判 unknown 停了 4 次，点空白就过去）：同一段认不出持续 QUIET_WAIT_S 秒、画面上没有按钮危险词（同主循环 advance.BLOCK_WORDS）、
    没有弹窗标题条、底部按钮区没有字，点左上 (12,12)。一段最多 QUIET_TAP_MAX 下。初 / H.I.F 的主循环本来就这样点认不出的画面。"""
    from hiro.loop import advance as adv
    from hiro.vision.pages import dialog_header

    now = rt.clock()
    if state["unknown_since"] is None:
        state["unknown_since"] = now
        return False
    if now - state["unknown_since"] < QUIET_WAIT_S or state["quiet"] >= QUIET_TAP_MAX or frame.img is None:
        return False
    texts = frame.texts
    if adv.blocked([t.text for t in texts]) is not None or dialog_header(frame.img) is not None:
        return False
    if any(QUIET_BUTTON_Y[0] <= t.rect.center.y <= QUIET_BUTTON_Y[1] for t in texts):
        return False
    state["quiet"] += 1
    state["unknown_since"] = now
    logger.info("N.I.A 采样：认不出、没有按钮的过场（第 %d 下），点 %s", state["quiet"], BUBBLE_TAP)
    rt.device.tap(*BUBBLE_TAP)
    return True


BUBBLE_TAP = (12, 12)
BUBBLE_WORDS = ("上昇した", "を強化しました", "をカスタマイズしまし", "を削除しまし", "を除去しまし", "獲得しました")


def _connecting(img) -> bool:
    """右下角「CONNECTING」（游戏通信中）。普通页面加载时也会闪，所以只在认不出的画面上当加载用，不改通用判页。"""
    from hiro.vision.ui_templates import tpl

    return (
        img is not None
        and img.shape[:2] == (1280, 720)
        and tpl("Common.TextConnecting").find(img, threshold=0.9) is not None
    )


ART_BANDS = ((0, 380), (520, 820), (900, 1280))  # 上 / 中 / 下
ART_CYAN_MIN, ART_MID_MAX = 0.6, 0.6


def _cyan(img, y1: int, y2: int) -> float:
    hsv = cv2.cvtColor(img[y1:y2], cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 0] >= 88) & (hsv[..., 0] <= 108) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 150)).mean())


def _event_art(rt, frame) -> bool:
    """支援卡事件的插画过场：满屏蓝绿底 + 白色几何线条，中间一张横幅插画，没有字（N.I.A 采样 11-17：差し入れ后判 unknown，15 秒停）。
    上下两段大半是蓝绿、中间不是（纯蓝绿是转场闪光 flash，快速判页已认、不点）→ 点 (12,12)。
    H.I.F 认不出的画面本来就连点空白过去，所以只在采样里加，不改通用判页。"""
    img = frame.img
    if img is None or img.shape[:2] != (1280, 720):
        return False
    top, mid, bottom = (_cyan(img, *b) for b in ART_BANDS)
    if top < ART_CYAN_MIN or bottom < ART_CYAN_MIN or mid >= ART_MID_MAX:
        return False
    logger.info("N.I.A 采样：支援事件插画过场（蓝绿 %.2f / %.2f / %.2f），点一下", top, mid, bottom)
    rt.device.tap(*BUBBLE_TAP)
    return True


def _nia_bubble(rt, frame) -> bool:
    """认不出的画面底部有白色消息气泡、写的是结算类的话（「Da.が147上昇した!」等，N.I.A 自主レッスン结算）：点一下就过。
    快速判页认气泡要右上「審査基準」（H.I.F 的 HUD），N.I.A 右上是投票数面板，认不出（11-17 N.I.A 采样停在这一页）。"""
    from hiro.vision.quick import bottom_bubble

    if frame.img is None:
        return False
    bubble = bottom_bubble(frame.img)
    if bubble is None:
        return False
    page, fields = bubble
    text = (fields or {}).get("text") or ""
    if page == "TRANSITION" and not any(w in text for w in BUBBLE_WORDS):
        return False
    logger.info("N.I.A 采样：底部气泡「%s」（%s），点一下", text or page, page)
    rt.device.tap(*BUBBLE_TAP)
    return True


def _save_stop_frame(rt, sampler: Sampler, frame, hit) -> str:
    path = sampler.out / f"stop-{time.strftime('%H%M%S')}-{hit.name}.jpg"
    try:
        cv2.imwrite(str(path), frame.img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if rt.session is not None:
            rt.session.note(f"N.I.A 采样停下：{hit.name}（帧 {path}）")
    except Exception:  # noqa: BLE001  存帧失败不影响停下
        pass
    return str(path)


def nia_core(scenario_mode: str):
    """HifCore 的 N.I.A 版：考试标签按进了几场给 nia-mid1 / nia-mid2 / nia-final；produce.mode 写成 N.I.A。"""
    from hiro.produce.hif import HifCore

    class NiaSampleCore(HifCore):
        nia_audition_number = (
            None  # 试镜选档页选的档（NiaPages 设）：标签带上「#audition-N」，8a3a1e7 的中转层按它查战斗配置
        )
        nia_stage = None  # 试镜选档页左上读到的场次（mid1 / mid2 / final）；读不到才按进了几场数
        next_exam_file = None  # NiaPages 选档时写的文件（resume 后内存里的场次丢了，从这里读）

        def _battle_label(self, ctx) -> str:
            n = self.exam_count
            self.exam_count += 1
            type(self).exams_done = self.exam_count
            if self.nia_stage is None and self.next_exam_file is not None and self.next_exam_file.exists():
                import json

                try:
                    saved = json.loads(self.next_exam_file.read_text(encoding="utf-8"))
                    self.nia_stage, self.nia_audition_number = (
                        saved.get("stage"),
                        self.nia_audition_number or saved.get("audition_number"),
                    )
                    self.next_exam_file.unlink()
                except (OSError, ValueError) as e:
                    logger.warning("N.I.A：读下一场考试的场次失败：%s", e)
            label = f"nia-{self.nia_stage}" if self.nia_stage else STAGES[min(n, len(STAGES) - 1)]
            self.nia_stage = None
            if self.nia_audition_number is not None:
                label = f"{label}#audition-{self.nia_audition_number}"
                self.nia_audition_number = None
            return label

    NiaSampleCore.__name__ = NiaSampleCore.__qualname__ = "NiaSampleCore"
    return NiaSampleCore


def set_mode(session, mode: str) -> None:
    prod = dict(session.state.produce or {})
    prod["mode"] = mode
    session.state.produce = prod


def run(args) -> int:
    from hiro import kaa_env
    from hiro.brain import guides
    from hiro.contracts import TraceWriter
    from hiro.devices import active, open_device
    from hiro.devices.base import DeviceError
    from hiro.loop.landscape import LandscapeGuard
    from hiro.loop.session import Session
    from hiro.produce.handlers import make_handlers
    from hiro.runtime import finish, play
    from hiro.runtime.loop import Runtime
    from hiro.vision.classify import classify

    guides.refresh_if_stale()  # 偶像卡攻略包：没有或超过 24 小时就下载，失败不阻塞（协调 11-20）
    kaa_env.apply_formation_plan(args.idol)
    support_set = args.support_set or kaa_env.SUPPORT_SET
    memory_set = args.memory_set or kaa_env.MEMORY_SET
    if args.command == "new" and (support_set is None or memory_set is None):
        logger.error("new 开局要编成号：--support-set / --memory-set，或开局编成方案")
        return 2
    mode = MODES[args.difficulty]
    from hiro.runtime.segments import trace_name

    session = Session(
        TraceWriter(name=trace_name("nia", args.command, args.trace))
    )  # 续跑接上一段（体力 / P / 属性 / step 打底）
    Core = nia_core(mode)
    for k, v in {
        "session": session,
        "use_llm": not args.no_llm,
        "llm_spec": args.llm,
        "main_attr": args.main_attr,
        "exams_done": 0,
    }.items():
        setattr(Core, k, v)
    core = Core(scenario="qualify", idol=args.idol)
    set_mode(session, mode)
    sampler = Sampler(classify, Path(args.out))
    from hiro.produce.nia import NiaPages

    Core.next_exam_file = Path(args.out) / "next-exam.json"
    nia_pages = NiaPages(core, persist=Core.next_exam_file)
    device = open_device()
    device.connect()
    rt = Runtime(raw_device=device, classify=sampler, handlers={**make_handlers(core), **nia_pages.handlers()}, advance=make_advance(sampler),
                 session=session, rescue=not args.no_llm,
                 landscape=LandscapeGuard(active=getattr(device, "name", "") == "maatools"), idle_stop_s=play.IDLE_STOP_S)  # fmt: skip
    active.use(rt)
    logger.info("N.I.A 采样：%s（%s），帧存到 %s", args.difficulty, mode, sampler.out)
    from hiro.vision.classify import PageHit

    rt.device.on_tap = lambda x, y: sampler.save_tap(rt.screen.frame, x, y)  # 每次点击都存一帧（开局、处理器、advance）
    raw_shot = rt.screen.shot

    def opening_shot():  # 开局阶段（rt.drive 不调 classify）：画面变了就存，页面名 opening
        f = raw_shot()
        sampler.save(f.img, PageHit("opening", "opening"))
        return f

    try:
        rt.screen.shot = opening_shot
        if args.command == "new":
            finish.finish_produce_end(rt, go_home=True)
            new_produce(rt, args.difficulty, idol=args.idol, support_set=support_set, memory_set=memory_set, plan=kaa_env.ACTIVE_PLAN,
                        rental_attr=args.main_attr, main_attr=args.main_attr)  # fmt: skip
        else:
            finish.resume_produce(rt)
        rt.screen.shot = raw_shot
        set_mode(session, mode)
        rt.run(until=lambda: getattr(rt, "finished", False))
        return 0
    except ResumeBlocked as e:
        logger.error("%s", e)
        print(str(e))
        return 3
    except DeviceError as e:
        logger.error("设备出错，停机：%s: %s", type(e).__name__, e)
        print(f"设备出错，停机：{e}")
        return 3
    finally:
        active.use(None)
        device.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="N.I.A 采样（新主循环）")
    ap.add_argument("command", choices=["new", "resume"])
    ap.add_argument("--idol", required=True, help="偶像卡 skin id（同 run_hif）")
    ap.add_argument("--difficulty", choices=sorted(MODES), default="pro")
    ap.add_argument("--main-attr", default=None)
    ap.add_argument("--support-set", type=int, default=None)
    ap.add_argument("--memory-set", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm", default=os.environ.get("HIRO_LLM"))
    ap.add_argument("--trace", default=None)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="采样帧目录")
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
