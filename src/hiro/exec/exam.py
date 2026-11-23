# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""考试执行器：替代 KAA 的 do_cards。截图 → 等画面稳定 → 读局面（ExamStateSource 插件）→ 出招（ExamAdvisor 插件）→ 点击 → 核对画面变了。

只放「和局面从哪来、怎么决策无关」的执行部分：帧（HUD / 手牌 / 饮料定位）、等稳（轻量签名 + 舞台空白推进）、弹窗处理（危险弹窗只点
キャンセル、开局设置框按用户设置）、出牌（第一下没被接收补点、SELECT 确认）、喝饮料（演出中没响应重点）、过时帧检查、执行后等变化、
回合自动结束核对（D34）、防空转（同一画面反复点、同一局面同一动作）。

局面与决策走插件（hiro.plugins）：
- ExamStateSource.read(frame, memory) → (BattleState | None, 原因)；默认实现（hiro.defaults.exam_state）= 画面识别 + 记账。
  存档读取 / 模拟器续推是私有实现，不在本仓库。
- ExamAdvisor.decide(state, memory, previews, legal) → Decision（契约）；默认实现（hiro.defaults.advisor）= 社区规则 + LLM。
"""

import logging
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from hiro import master
from hiro.brain.battle import BattleBrain
from hiro.brain.cards import card_view, drink_view
from hiro.brain.rules import Decision
from hiro.brain.state import BattleState, ExamMemory
from hiro.contracts import Action, ExecutionResult
from hiro.contracts import Decision as DecisionContract  # noqa: F401
from hiro.devices.active import Loop, device, sleep
from hiro.exec import schedule_probe
from hiro.perception.lesson import _read_int
from hiro.produce import ops
from hiro.recorder import Recorder
from hiro.vision.hif import EXAM_CACHE, hand_cards
from hiro.vision.ocr import Box, Text, find_text, screen_texts
from hiro.vision.textnorm import contains  # OCR 容错匹配（用户 11-21）
from hiro.vision.ui_templates import box, tap, tpl

logger = logging.getLogger(__name__)

SKIP_BUTTON = Box(621, 739, 706, 824)  # 与 KAA do_cards 相同的跳过回合位置（720x1280）
STABLE_GAP = 0.4  # 两次读取间隔；两次一致才算稳定
CHANGE_TIMEOUT = 8.0  # 执行后等画面变化的最长时间
LIVE_WAIT_S = 2.0  # 存档暂时对不上（文件正在写、存档落后画面不止一步）时最多等这么久，之后走截图兜底
# 立即走截图、不等的情况：存档停在卡牌结算中（同一回合内不会重写）、存档手牌比画面差 1 张（刚出完牌还没写）
NO_WAIT = ("save_playing", "hand_count_1", "sim_error", "hand_cards", "save_stale")
CHANGE_POLL = 0.2  # 等变化时的轮询间隔（每轮一张截图 + 一次 HUD 读取）
DIALOG_SETTLE = 2.0  # 处理弹窗后等画面稳定的上限
STABLE_WAIT_S = 6.0  # 画面一直不稳定时最多等这么久，之后用单帧出决策（第十八局 12 次等满 10s，见 _stable_frame）
EXAM_STALL_S = 180.0  # 考试里这么久没有进展（没有一个动作生效、残りターン 也没变）就退出 run_exam，交回主循环（主循环有自己的看门狗）。
# 偶像之路不合格名次页认不出「结束」时 run_exam 空转 5 分钟没日志（Mac 稳定性测试 11-14）；不看画面变不变：名次页背景是动的
SLOW_DIALOG_AFTER = 5.0  # 画面不像考试帧超过这么久（回合切换动画约 2–3s 不算），弹窗检查才退回整帧 OCR（横屏 LIVE 开始提示等没有标题条的页面）


def _hud_int(img, box, kaa_box) -> int | None:
    """H.I.F 考试 HUD 数字：用识别层的读数（hiro.vision.hif，与 read_hif_exam 同一读法，约 10ms）；读不到再退回 KAA。
    实机（2026-10-31 选拔①）KAA 的 _read_int 在同一帧读出剩余回合 0、体力 None，识别层读出 10、33。"""
    from hiro.vision import hif

    try:
        v = hif._digits(img, box)
    except Exception:  # 识别异常不能打断考试
        v = None
    return v if v is not None else _read_int(img, kaa_box)


_DRINK_CACHE = None  # 饮料栏区域缓存（考试中饮料栏很少变，命中时约 1ms）


def _drinks(img) -> list[tuple[master.Drink, tuple]]:
    """饮料栏 → [(master.Drink, (x, y, w, h))]，从左到右。用识别层的素材包原版图标（hiro.vision.items.read_drink_slots，带区域缓存）；
    认不出的格不给（这一步就不用那瓶）。原来没把握且一瓶没认出时退回 KAA 的饮料匹配，但 KAA 认不出 H.I.F 饮料（实机 2026-10-31），
    73 张实机考试帧里这条退路一次都没触发过，去 KAA 时删掉。"""
    global _DRINK_CACHE
    from hiro.vision.items import DRINK_SIZE, read_drink_slots
    from hiro.vision.region_cache import RegionCache

    if _DRINK_CACHE is None:
        _DRINK_CACHE = RegionCache()
    out = []
    try:
        slots = read_drink_slots(img, cache=_DRINK_CACHE)
        for d in slots:
            if d["state"] != "drink":
                continue
            drink = master.drink_by_asset(d["asset"])
            if drink is None:
                continue
            cx, cy = d["center"]
            out.append((drink, (cx - DRINK_SIZE // 2, cy - DRINK_SIZE // 2, DRINK_SIZE, DRINK_SIZE)))
    except Exception:  # 识别异常不能打断考试
        out = []
    return out


def drink_slot_rect(slot: int) -> tuple | None:
    """饮料栏第 slot 格（从左数）的固定位置，识别不到图标时按存档顺序点这里。"""
    from hiro.vision.items import DRINK_CENTERS, DRINK_SIZE

    if not 0 <= slot < len(DRINK_CENTERS):
        return None
    cx, cy = DRINK_CENTERS[slot]
    return (cx - DRINK_SIZE // 2, cy - DRINK_SIZE // 2, DRINK_SIZE, DRINK_SIZE)


class Frame:
    """一帧考试画面的读取结果。剩余回合 / 体力 / 元气用识别层的 HUD 读数；手牌、饮料的位置用 KAA 定位（点击按它）。"""

    def __init__(self, img):
        from hiro.vision import hif

        self.img = img
        self.t = time.monotonic()  # 这帧截图的时刻（度量截图→点击）
        self.remaining = _hud_int(img, hif.BOX_TURNS, box("InProduce.InLesson.BoxRemainingTurns"))
        self.hp = _hud_int(img, hif.BOX_HP, box("InProduce.InLesson.BoxHp"))
        self.genki = _hud_int(img, hif.BOX_GENKI, box("InProduce.InLesson.BoxGenki"))
        self.cards = hand_cards(img, cache=EXAM_CACHE)  # 位置 / 能不能打 / 卡面认出的卡（hiro.vision.hif.hand_cards）
        self.drinks: list[tuple[master.Drink, tuple]] = _drinks(img)
        self._hand: list[dict] | bool | None = False  # 识别层手牌，按需读（见 hand）

    @property
    def hand(self) -> list[dict] | None:
        """识别层手牌（hiro.vision.hif.read_hand：卡面 + 卡名栏核对，带强化次数与预计得分徽章），与 KAA 定位同一组卡名首字母框，
        槽位一一对应。只在决策时读（带区域缓存）；与 KAA 张数不一致（动画中）或读取异常时为 None。"""
        if self._hand is False:
            try:
                from hiro.vision.hif import EXAM_CACHE, read_hand

                hand, _ = read_hand(self.img, cache=EXAM_CACHE)
                self._hand = hand if len(hand) == len(self.cards) else None
            except Exception:  # 识别异常不能打断考试：退回 KAA
                self._hand = None
        return self._hand

    def hand_views(self) -> list:
        """手牌 CardView：识别层可用时用它（强化次数、徽章），否则用 KAA（只有基础版）。"""
        if self.hand is not None:
            from hiro.vision.observe import battle_observation

            return battle_observation({"hand": self.hand})["hand"]
        return [
            card_view(i, c.card if c else None, available=bool(c and c.available)) for i, c in enumerate(self.cards)
        ]

    @property
    def signature(self) -> tuple:
        hand = tuple((c.card.name if c and c.card else None, c.available if c else None) for c in self.cards)
        return (self.remaining, self.hp, self.genki, hand, tuple(d.name if d else None for d, _ in self.drinks))


# ---------------------------------------------------------------- 分段计时
# 每步（上一步执行完 → 这一步执行完）各段耗时累加到 PHASES，写 trace 时取出清零（timings_ms 里 phase_* 各项）。
PHASES: dict[str, float] = {}


@contextmanager
def _phase(name: str):
    t = time.monotonic()
    try:
        yield
    finally:
        PHASES[name] = PHASES.get(name, 0.0) + (time.monotonic() - t) * 1000


# 读帧 → 点击之间的阶段（一步「截图→点击」由这些组成）。重新决策（决策帧过时）/ 等存档重来时，这一轮的耗时挪进 redo，
# 不和最终那一轮的同名阶段相加（第二十四局：累加后好几步「截图→点击 − 各阶段」是负数，分不清预演和读帧）
DECIDE_PHASES = ("save_read", "settle_playing", "live_game", "reconcile", "preview", "badges", "track_state", "screen_rebuild",
                 "screen_read", "observe", "tap_check")  # fmt: skip


def _move_to_redo(extra_ms: float = 0.0) -> None:
    """这一轮决策作废（continue）：本轮的读帧 / 预演 / 核对耗时记进 PHASES["redo"]，次数记进 redo_n。"""
    PHASES["redo"] = PHASES.get("redo", 0.0) + extra_ms + sum(PHASES.pop(k, 0.0) for k in DECIDE_PHASES)
    PHASES["redo_n"] = PHASES.get("redo_n", 0.0) + 1


def _take_phases() -> dict[str, float]:
    out = {f"phase_{k}": round(v, 1) for k, v in PHASES.items()}
    PHASES.clear()
    return out


# ---------------------------------------------------------------- 局部 OCR（不做整帧 OCR）
# 实测（第十六局本战帧，离线）：kotonebot 整帧 OCR 2.8–5.2s；Vision 整帧 0.5–0.9s；手牌下方条带 0.05–0.2s；弹窗区 0.2–0.5s。
SELECT_BAND = (1040, 1200)  # 手牌下方「SELECT」标签（实测 y≈1120）
DIALOG_BAND_BOTTOM = 1240  # 弹窗按钮行实测 y≈1160（定制页「実行する」y≈1081）


def _roi_texts(img, y1: int, y2: int) -> list[Text]:
    """img[y1:y2] 整行宽的 OCR（hiro.vision.ocr.screen_texts），返回整帧坐标。img 为 None（测试替身）或识别异常时返回空。"""
    if img is None:
        return []
    try:
        return screen_texts(img, (0, y1, img.shape[1], y2 - y1))
    except Exception:  # 识别异常不能打断考试
        logger.warning("局部 OCR 出错（y %d–%d）", y1, y2, exc_info=True)
        return []


def _selects(img) -> list[Text]:
    """手牌下方的「SELECT」标签（只识别条带）。"""
    return _select_markers(img, _roi_texts(img, *SELECT_BAND))


def _select_markers(img, boxes: list[Text]) -> list[Text]:
    """SIMD 偶尔把卡名和 SELECT 合框。只用复核过的按钮位置。"""
    hits = [t for t in boxes if _is_select(t.text)]
    if hits or not boxes or img is None:
        return hits
    from hiro.vision.ocr import engine, ocr_line

    if engine() != "simd":
        return hits
    import cv2

    for t in boxes:
        if (
            not t.text.strip().upper().replace(" ", "").endswith("SELECT")
            or t.rect.w < 105
            or not 1080 < t.rect.center.y < 1140
        ):
            continue
        right, y = int(t.rect.x2), int(t.rect.center.y)
        x1, x2, y1, y2 = max(0, right - 94), min(img.shape[1], right + 5), y - 10, y + 25
        crop = img[y1:y2, x1:x2]
        if not crop.size:
            continue
        orange = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), (14, 130, 178), (30, 255, 255))
        if cv2.countNonZero(orange) <= 200:
            continue
        try:
            crop_text = ocr_line(crop)
        except Exception:  # noqa: BLE001  识别出错（辅助进程重启后仍失败）不能打断出牌：这个候选当作读不出
            logger.warning("局部 OCR 出错（SELECT 候选 y %d–%d）", y1, y2, exc_info=True)
            continue
        if _is_select(crop_text):
            hits.append(Text("SELECT", Box(x1, y1, x2, y2)))
    return hits


def _dialog_top(img) -> int | None:
    """有橙色标题条的弹窗 → 标题条上沿 y；没有 → None（像素判据约 20ms，与识别层 read_dialog 同一判据）。
    第十六、十五、十七局考试帧 181 张零误报；7 种弹窗样例（ターン終了 / 使用確認 / ドリンク詳細 / 相談終了 / カスタマイズ / リタイア / 排序）全部命中。"""
    if img is None:
        return None
    try:
        from hiro.vision.pages import dialog_header

        band = dialog_header(img)
    except Exception:
        logger.warning("弹窗标题条判据出错", exc_info=True)
        return None
    return None if band is None else band[0]


_last_dialog_ocr = 0.0
DIALOG_OCR_EVERY = 3.0  # 整帧 OCR 较慢，弹窗兜底检查按时间节流


def _button_text(t: str, text: str) -> bool:
    return ops.button_text(t, text)  # 第二十五局「くはい」：见 hiro.produce.ops.button_text


def _ocr_button(text: str, *, min_y: int, boxes=None) -> list[int] | None:
    """在 OCR 结果（默认现截一帧整帧 OCR）里找按钮文字，找到就点，返回点击位置。"""
    boxes = screen_texts(device.screenshot()) if boxes is None else boxes
    hit = next((t for t in boxes if _button_text(t.text, text) and t.rect.center.y >= min_y), None)
    if hit is None:
        return None
    x, y = int(hit.rect.center.x), int(hit.rect.center.y)
    device.click(x, y)
    return [x, y]


# H.I.F 考试中 KAA 模板认不出的确认框：(标题关键字, 要点的按钮, 按钮最小 y)。都是确认已经做出的决策。
HIF_DIALOGS = (
    ("使用確認", "決定", 1000),  # Pドリンク使用確認：效果对象不存在，仍执行
    ("ターン終了", "はい", 1000),  # 还有可打的牌时跳过回合
    ("ドリンク詳細", "使う", 500),  # 点饮料后的详情框
    ("相談終了確認", "決定", 1000),  # 相談还能买时点「終了」的确认（决策器已决定离开）
    ("横画面で開始", "TAP TO START", 800),  # 选拔赛三场全过后的 LIVE（横屏）开始提示
    ("ボイス再生確認", "決定", 1000),  # 本战开局的语音设置：先交 skip_settings 按用户设置选，读不出才按默认项点
    ("早送り設定", "決定", 1000),  # 本战开局的剧情快进设置：同上
    ("スキップ設定", "決定", 1000),  # 本战开局的演出跳过设置：同上
    ("カスタマイズ確認", "実行する", 1000),  # 定制页已选中一项（决策器已选），按「実行する」
    ("カスタマイズを実行", "決定", 1000),  # 定制的二次确认弹窗「以下のPアイテムにカスタマイズを実行します」
)
DANGER_WORDS = (
    "リタイア",
    "破棄",
)  # 危险弹窗（リタイア / データ破棄）：绝不点确认，只点キャンセル（识别 DIALOG.danger 同一判据）


SELECT_RECHECK_S = 0.8  # SELECT 重点前等画面稳定再核对一次


def _is_select(text: str) -> bool:
    """卡下方的「SELECT」标签。OCR 常读歪：「SELEC1」（main13 R2）、「SELECTCm!」（q15，后面粘了别的字），只认 SELEC 开头的短词。"""
    t = text.strip().upper().replace(" ", "")
    return t.startswith("SELEC") and len(t) <= 10


def _confirm_dialog(boxes, texts) -> bool:
    """按 OCR 结果处理确认框：危险弹窗只点キャンセル；HIF_DIALOGS 里的点对应按钮。返回是否点了。"""
    if any(contains(t, w) for t in texts for w in DANGER_WORDS):
        pos = _ocr_button("キャンセル", min_y=600, boxes=boxes)
        if pos:
            logger.warning("危险弹窗（リタイア / 破棄）→ 只点キャンセル %s", pos)
            sleep(1.0)
            return True
        return False
    # 开局的一次性设置框（演出スキップ設定 / コミュ早送り設定 / ボイス再生確認）：按用户设置选好再点「決定」，不用默认项
    # （第二十五局：重新登录后弹出，以前这里对三者都直接点「決定」，コミュ早送り 留在「早送りしない」）
    from hiro.loop import skip_settings

    if skip_settings.is_skip_settings(texts):
        from hiro.produce.ops import ocr_boxes

        # 调用方给的可能只是弹窗区的局部 OCR（_exam_dialogs），开关在整个弹窗里：重新整帧 OCR 一次
        img = device.screenshot()
        r = (
            skip_settings.handle(
                ocr_boxes(img), shoot=device.screenshot, ocr=ocr_boxes, click=lambda x, y: device.click(x, y)
            )
            if img is not None
            else None
        )
        if r is not None and r["ok"]:
            return True
    # 顺序要紧：确认框叠在详情框上面，先处理上层
    for title, button, min_y in HIF_DIALOGS:
        if any(title in t for t in texts):
            pos = _ocr_button(button, min_y=min_y, boxes=boxes)
            if pos:
                logger.info("H.I.F「%s」→ %s %s", title, button, pos)
                sleep(1.0)
                return True
    return False


RETAP_MAX = 2  # 同一位置 RETAP_WINDOW_S 内最多再点几次
RETAP_WINDOW_S = 30.0
_RETAP: dict = {"pos": None, "n": 0, "t0": 0.0}
UNPLAYABLE: dict = {"hit": False}  # 再点上限触发：主循环把本次动作本回合禁用


def _select_retap(sel: Text) -> bool:
    """卡停在选中态（卡下方「SELECT」）但没打出 → 再点一次这张卡。
    上一帧的 SELECT 可能是卡已打出、手牌重排前的残影（q11 选拔③ seq 39：再点落在重排后的 イメトレ 上，多打一张）。
    等一下重新截图，SELECT 还在原位（±30px）才点。"""
    sx, sy = int(sel.rect.center.x), int(sel.rect.center.y)
    sleep(SELECT_RECHECK_S)
    img = device.screenshot()
    again = next(
        (t for t in _selects(img) if abs(t.rect.center.x - sx) <= 30 and abs(t.rect.center.y - sy) <= 30), None
    )
    if again is None:
        logger.info("H.I.F SELECT 已消失或移位（卡已打出 / 手牌重排），不再点")
        return False
    now = time.monotonic()
    if (
        _RETAP["pos"] is not None
        and abs(_RETAP["pos"][0] - sx) <= 30
        and abs(_RETAP["pos"][1] - sy) <= 30
        and now - _RETAP["t0"] <= RETAP_WINDOW_S
    ):
        _RETAP["n"] += 1
    else:
        _RETAP.update(pos=(sx, sy), n=1, t0=now)
    if _RETAP["n"] > RETAP_MAX:
        # 同一张卡反复停在选中态：多半是「使用可能になる条件を満たしていません」（第二十一局选拔① T1 デイドリーミング+，再点 12 分钟）。
        # 点舞台取消选中，交给主循环把这个动作本回合禁用，不当作已打出
        logger.warning(
            "H.I.F 同一张卡连续 %d 次停在选中态（使用条件不满足？）→ 取消选中，本回合禁用该动作", _RETAP["n"]
        )
        device.click(*DESELECT_TAP)
        _RETAP.update(pos=None, n=0, t0=0.0)
        UNPLAYABLE["hit"] = True
        sleep(0.8)
        return True
    x, y = sx, sy - 110
    logger.info("H.I.F 卡牌停在选中态（SELECT）→ 再点一次 (%d, %d)", x, y)
    device.click(x, y)
    sleep(1.0)
    return True


def _exam_dialogs(img, *, slow: bool = False, select: bool = True) -> bool:
    """考试中的 H.I.F 弹窗与 SELECT 兜底，只做局部识别：
    1. 画面有橙色标题条（像素判据，约 20ms）才 OCR 弹窗区（标题条到按钮行），按 HIF_DIALOGS / 危险词处理；
    2. 没有弹窗时 OCR 手牌下方条带（约 0.15s）找停在选中态的卡（SELECT），重新核对后再点；
    select=False 时跳过第 2 步（刚出完牌：卡还在选中态 / 飞出动画里，SELECT 是残影）；
    3. slow=True（画面不像考试帧，例如横屏 LIVE 开始提示、定制页这类没有标题条的页面）时，按节流做一次整帧 OCR 兜底。"""
    top = _dialog_top(img)
    if top is not None:
        boxes = _roi_texts(img, max(0, top - 10), DIALOG_BAND_BOTTOM)
        texts = [t.text for t in boxes]
        if _confirm_dialog(boxes, texts):
            return True
        logger.debug("有标题条的弹窗没有匹配项：%s", texts[:8])
        return False
    sel = None
    if select:
        sels = _selects(img)
        _STRIP["img"], _STRIP["sel"] = id(img), sels  # 动画点击（_anim_tap）复用同一张图的条带结果
        sel = next(iter(sels), None)
    if sel is not None:
        return _select_retap(sel)
    if slow:  # 整帧 Vision（约 0.5s；kotonebot 整帧 2–5s，第十八局长尾里 dialogs 一步 7s+）
        return _hif_drink_dialogs(boxes=_roi_texts(img, 0, img.shape[0]) if img is not None else None)
    return False


_STRIP: dict = {"img": None, "sel": []}  # 最近一次条带识别：(图的 id, SELECT 列表)

# ---------------------------------------------------------------- 动画点击推进
# 用户确认（2026-11-02）：考试里的出牌 / 回合开始结束结算 / 饮料 / P 道具触发动画都能点屏幕跳过。
# 动作生效后到下一帧稳定之间，每 ANIM_TAP_EVERY 秒点一次舞台空白处。位置与 DESELECT_TAP 相同（点了最多取消选中，main13 R2 实测），
# 第十五到十七局考试帧上核对过：不在手牌（y≥640）、饮料栏、SKIP（621,739）、左侧状态栏、右侧牌库图标、顶部名次条上。
ANIM_TAP = (360, 330)  # 与 DESELECT_TAP 同一点（舞台空白处）
ANIM_TAP_EVERY = 0.4
_anim = {"last": 0.0, "taps": 0}


SETTLE_FAST_MAX = 4.0  # 等动画结束的上限；超时交回 _stable_frame
SETTLE_SIG_GAP = 0.15  # 两次读数之间等多久（轻量签名本身约 25ms）


def _settle_fast(img, *, on_tick=None, clock=time.monotonic):
    """动作生效后等动画结束，返回稳定的那一帧（Frame）或 None（超时 / 出弹窗 / 读不到，交回 _stable_frame）。
    连续读帧（HUD + 手牌 + 饮料签名，与 _stable_frame 同一判据，HUD 一边读成 None 也算一致），两次一致就直接用第二帧决策，
    不再另做一轮两帧核对；每读一帧之前按 _anim_tap 的规则点一下舞台推进。
    第十九局（eeb60b2）按像素判「静」：可打出的卡在发光，151 步次次等满（settle_timeout 229 次）、上限缩到 1.2s 后再做两帧核对，
    settle 中位仍 5.2s。on_tick：每轮回调（预演提前算读存档）。"""
    start, prev, calm, calm_t = clock(), None, 0, None
    while clock() - start < SETTLE_FAST_MAX:
        if img is None or _dialog_top(img) is not None:
            return None
        _count("settle_iters")
        with _phase("settle_tap"):  # 点舞台推进（含 SELECT 条带 OCR）
            _anim_tap(img)
        if on_tick is not None:
            with _phase("settle_kick"):  # 预演提前算：读存档，存档变了就还原局面、起后台预演
                on_tick()
        # 轻量签名（HUD 三个数 + 手牌字母角标位置，约 25ms；完整 Frame 要 0.15–0.45s，含 KAA 卡名匹配）：
        # 第二十局（负载约 30）一次 Frame 约 0.4s，settle_fast 中位 3.2s 里大半是读帧。连续 SETTLE_CALM 次一致再读一次完整 Frame 交决策
        with _phase("settle_lite"):
            sig = _lite_sig(img)
        now = clock()
        if sig is None:
            _count("settle_sig_none")
            calm, prev, calm_t = 0, None, None
        else:
            same = prev is not None and _lite_same(prev, sig)
            if prev is not None and not same:
                _count(
                    "settle_sig_hud" if sig[1] == prev[1] else "settle_sig_hand"
                )  # 画面还在变：HUD 数字变 / 手牌位置变
            calm = calm + 1 if same else 0
            calm_t = (calm_t if same and calm_t is not None else now) if prev is not None else now
            prev = sig
            # HIRO_SETTLE_CALM_S（默认关）：两次一致的读数相隔达到这么久也算稳（负载高时一轮 0.6–0.9s，3 次读数要 2–3s）
            timed = SETTLE_CALM_S > 0 and calm >= 1 and now - calm_t >= SETTLE_CALM_S
            # HIRO_SETTLE_MIN_S（默认关）：一致的读数还要持续这么久才算稳。截图很快的后端（DMM PrintWindow 约 0.19s，MuMu adb 约 1.3s）
            # 3 次读数不到 1 秒，会落在两段动画之间的停顿里：回合还没切完就决策，点之前发现回合变了（dmm1m-r3 190 次决策 23 次过时、
            # 重决策平均 12.5s；MuMu 第十八 / 二十一局 0–1 次）
            settled = calm >= SETTLE_CALM and (SETTLE_MIN_S <= 0 or now - calm_t >= SETTLE_MIN_S)
            if settled or timed:
                f = Frame(img)
                if _usable_fast(f):
                    return f
                calm, calm_t = 0, None
        sleep(SETTLE_SIG_GAP)
        with _phase("settle_shot"):
            img = device.screenshot()
    _count("settle_timeout")
    return None


SETTLE_CALM = 2  # 轻量签名连续几次一致（3 次读数）才读完整 Frame
# 开关（默认关 = 0，由总控 / 用户打开）：两次一致读数相隔 ≥ 这么多秒也算稳。第二十四局截图中位 229ms（第二十一局 63ms），
# 稳帧检测一轮 0.6–0.9s，37% 的步等满 SETTLE_FAST_MAX 4s 再退回 _stable_frame（约 2.3s）
SETTLE_CALM_S = float(os.environ.get("HIRO_SETTLE_CALM_S") or 0)
# 开关（默认关 = 0）：稳帧还要求一致读数持续 ≥ 这么多秒（见 _settle_fast）。DMM 版建议 1.5（≈ MuMu 三次读数自然跨过的时长的一半）
SETTLE_MIN_S = float(os.environ.get("HIRO_SETTLE_MIN_S") or 0)
STALE_MAX = 3


def _stale_before_tap(frame) -> str | None:
    """点击前重新截图，与决策帧比剩余回合和手牌位置（_lite_sig，约 25ms）。不一致返回原因，一致或判断不了返回 None。
    决策帧或点击前的画面里刚打出的牌还停在中间（hand_slots.played_card_floating）：那是出牌 / 回合切换的过渡帧，也算过时
    （偶像之路 Mac 15 层 11-14：100 个决策帧里 24 个是这样，回合正在结束时又点了一次）。"""
    from hiro.vision.hand_slots import played_card_floating

    try:
        a = _lite_sig(frame.img)
        if a is None:
            return None  # 决策帧本身读不出（截图兜底等）：不拦
        if played_card_floating(frame.img):
            return "刚打出的牌还停在画面中间（过渡帧）"
        img = device.screenshot()
        if played_card_floating(img):
            return "点击前刚打出的牌还停在画面中间（过渡帧）"
        b = _lite_sig(img)
    except Exception:
        return None
    if b is None:
        return "点击前读不到剩余回合 / 手牌（动画或结算中）"
    if a[0][0] != b[0][0]:
        return f"剩余回合 {a[0][0]}→{b[0][0]}"
    if a[1] != b[1]:
        return f"手牌位置变了（{len(a[1])}→{len(b[1])} 张）"
    return None


def _guard_repeated_decision(session, memory, d, exam) -> None:
    """通用防空转（hiro.loop.repeat_guard）：同一局面同一动作连续 REPEAT_MAX 次（Session.record 计数）→ 本回合禁用它；
    被禁的是跳过回合（没有别的可做）就停机报警。第二十三局 R2：饮料栏已空、存档还当有饮料，drink:0 反复 175 次、40 分钟。"""
    from hiro.loop.repeat_guard import REPEAT_MAX, RepeatedNoEffect

    if session is None or getattr(session, "repeat_streak", 0) < REPEAT_MAX:
        return
    session.guard.reset_streak()
    if d.kind == "skip":
        raise RepeatedNoEffect(f"[{exam}] 跳过回合连续 {REPEAT_MAX} 次局面不变，停机报警")
    memory.block(d.action)
    logger.warning("[%s] %s 在同一局面下连续 %d 次没效果，本回合禁用", exam, d.action, REPEAT_MAX)


_DLG: dict = {"thumb": None, "token": 0}


def _guard_dialog_loop(img, session, memory, last_action, exam) -> None:
    """通用防空转：考试循环弹窗阶段每轮都点了东西又回来、手牌 / HUD 不变（_lite_sig 相同）、上一个动作也没变 → 连续 REPEAT_MAX 轮
    就点舞台空白取消选中、本回合禁用上一个动作。第二十一局选拔①：卡停在选中态再点 404 次、17.8 分钟，期间没有一个决策步。
    HUD 被弹窗挡住（_lite_sig 读不到）时不计数：那是真弹窗，交给弹窗处理和空转看门狗。"""
    from hiro.loop.repeat_guard import REPEAT_MAX, SCREEN_DIFF, _thumb, action_key

    import numpy as np

    if session is None or last_action is None or getattr(session, "guard", None) is None:
        return
    lite = _lite_sig(img)
    if lite is None:
        return
    # 整帧也要和上一轮一样（回合开始连着几个效果弹窗时手牌 / HUD 不变但画面不同，不能算重复）
    th = _thumb(img)
    if _DLG["thumb"] is None or float(np.abs(th - _DLG["thumb"]).mean()) >= SCREEN_DIFF:
        _DLG["token"] += 1
    _DLG["thumb"] = th
    if session.guard.attempt("HIF_EXAM:dialogs", f"{lite!r}#{_DLG['token']}", action_key(last_action)) < REPEAT_MAX:
        return
    session.guard.reset_streak()
    device.click(*DESELECT_TAP)
    memory.block(last_action)
    msg = (
        f"重复无效动作：[{exam}] 弹窗阶段同一画面连续 {REPEAT_MAX} 轮（上一个动作 {last_action}），取消选中、本回合禁用"
    )
    logger.warning(msg)
    session.recover("tap_no_effect", msg)
    session.note("blunder:repeat_no_effect " + msg)


def _lite_sig(img):
    """(剩余回合, 体力, 元気, 手牌字母角标位置)；剩余回合读不到或没有手牌时 None（动画 / 结算中）。"""
    from hiro.vision import hif
    from hiro.vision.hand_slots import card_letters

    try:
        hud = tuple(
            _hud_int(img, hb, box(f"InProduce.InLesson.{kb}"))
            for hb, kb in ((hif.BOX_TURNS, "BoxRemainingTurns"), (hif.BOX_HP, "BoxHp"), (hif.BOX_GENKI, "BoxGenki"))
        )
        letters = tuple(sorted((int(r.x1) // 8, int(r.y1) // 8) for r in card_letters(img)))
    except Exception:
        return None
    if hud[0] is None or not letters:
        return None
    return hud, letters


def _lite_same(a, b) -> bool:
    """手牌位置一致、剩余回合一致；体力 / 元気 一边读成 None（加分特效压暗）也算一致。"""
    (ha, la), (hb, lb) = a, b
    return la == lb and ha[0] == hb[0] and all(x == y or x is None or y is None for x, y in zip(ha[1:], hb[1:]))


def _usable_fast(f) -> bool:
    """同 _usable，但手牌为空时不做「0枚」OCR（约 1s）：等静阶段手牌为空就当作还没稳。"""
    return f.remaining is not None and bool(f.cards)


def _anim_tap(img) -> bool:
    """动画中点舞台空白处推进。不点：有弹窗标题条（交给弹窗处理）、手牌下方有 SELECT（卡停在选中态，点了会取消选中）、距上次不到 ANIM_TAP_EVERY。"""
    now = time.monotonic()
    if now - _anim["last"] < ANIM_TAP_EVERY or img is None:
        return False
    if _dialog_top(img) is not None:
        return False
    sels = _STRIP["sel"] if _STRIP["img"] == id(img) else _selects(img)
    if sels:
        return False
    device.click(*ANIM_TAP)
    _anim["last"] = now
    _anim["taps"] += 1
    return True


def _hif_drink_dialogs(*, force: bool = False, boxes=None) -> bool:
    """整帧 OCR 版弹窗处理（主循环 controller 的兜底、考试中画面不像考试帧时用），按 DIALOG_OCR_EVERY 节流：
    「Pドリンク使用確認」（效果对象不存在，问是否仍执行）→ 点「決定」，与已做的决策一致；只剩「Pドリンク詳細」→ 点「使う」；
    卡停在选中态（SELECT）→ 重新核对后再点。"""
    global _last_dialog_ocr
    if boxes is None:  # boxes：调用方本轮已有的整帧 OCR（主循环 controller 共用），给了就不节流、不再 OCR
        now = time.monotonic()
        if not force and now - _last_dialog_ocr < DIALOG_OCR_EVERY:
            return False
        _last_dialog_ocr = now
        boxes = screen_texts(device.screenshot())
    texts = [t.text for t in boxes]
    if any(contains(t, w) for t in texts for w in DANGER_WORDS):
        return _confirm_dialog(boxes, texts)
    sel = next((t for t in boxes if _is_select(t.text) and t.rect.center.y > 1000), None)
    if sel is not None and not any("ドリンク" in t or "確認" in t or "ターン終了" in t for t in texts):
        return _select_retap(sel)
    return _confirm_dialog(boxes, texts)


MOVE_CLOSED_S = 3.0  # 技能卡移动对话框消失后再看这么久，确认真关了


def _skill_card_move() -> bool:
    """技能卡移动对话框（照 KAA handle_skill_card_move 移植，行为不变）：全屏找卡名首字母框（A、M、T 依次），对话框还在就双击
    最后一个、点 ✔，直到对话框消失 MOVE_CLOSED_S 秒。没找到卡返回 False；处理完和 KAA 一样返回 None（调用方照旧往下查别的弹窗）。
    注意：选哪张是盲选（沿用 KAA，取找到的最后一张），出牌决策没有接管这个对话框。"""

    def letters(img):
        return [m for name in ("InProduce.A", "InProduce.M", "InProduce.T") for m in tpl(name).find_all(img)]

    cards = letters(device.screenshot())
    if not cards:
        logger.info("技能卡移动：没找到卡")
        return False
    closed_at = None
    for _ in Loop():
        img = device.screenshot()
        if not tpl("InProduce.IconTitleSkillCardMove").exists(img):
            closed_at = closed_at if closed_at is not None else time.monotonic()
            if time.monotonic() - closed_at >= MOVE_CLOSED_S:
                logger.info("技能卡移动：对话框已关闭")
                break
            continue
        closed_at = None
        if not cards:
            cards = letters(img)
            if not cards:
                continue  # KAA 这里会对空列表 pop 抛异常；这里等下一帧
        card = cards.pop()
        device.double_click(*card.center)
        sleep(1)
        tap("Common.IconButtonCheck", device.screenshot(), device.click)
    return None


def _handle_dialogs(img=None, *, slow: bool = False, select: bool = True) -> bool:
    """处理出牌过程中会弹出的对话框（读最近一次截图 img）。返回是否处理了什么。
    处理后等弹窗区域变化 / 画面稳定再返回，不固定睡 2s。"""
    img = img if img is not None else device.screenshot()
    if tpl("InProduce.IconTitleSkillCardMove").exists(img):  # noqa: SIM102
        if _skill_card_move():
            ops.wait_stable(timeout=DIALOG_SETTLE)
            return True
    if _exam_dialogs(img, slow=slow, select=select):
        return True
    if tpl("InProduce.DialogSkillCardUseConfirm.Title").exists(img):
        btn = tpl("InProduce.DialogSkillCardUseConfirm.ButtonConfirm").find(img)
        if btn:
            before = device.screenshot()
            device.click(*btn.center)
            ops.wait_change(before, roi=btn.rect.xywh, timeout=DIALOG_SETTLE, use_signature=False)
            return True
    return False


def _usable(f: Frame) -> bool:
    return f.remaining is not None and (
        bool(f.cards) or find_text(f.img, "0枚", rect=box("InProduce.BoxNoSkillCard")) is not None
    )


SIG_FIELDS = ("remaining", "hp", "genki", "hand", "drinks")
HUD_FIELDS = {"remaining", "hp", "genki"}


def _sig_diff(a: Frame, b: Frame) -> list[str]:
    return [f for f, x, y in zip(SIG_FIELDS, a.signature, b.signature) if x != y]


def _count(name: str) -> None:
    """不稳定原因计数，随本步 timings_ms 写进 trace（phase_unstable_*，是次数不是毫秒）。"""
    PHASES[f"unstable_{name}"] = PHASES.get(f"unstable_{name}", 0.0) + 1


def _same_sig(a, b) -> bool:
    """两帧签名一致；手牌、饮料一致而 HUD 只是一边读成 None（加分特效压暗）也算，缺的数从 a 补进 b。"""
    if a.signature == b.signature:
        return True
    diff = _sig_diff(a, b)
    if set(diff) <= HUD_FIELDS and all(getattr(a, f) is None or getattr(b, f) is None for f in diff):
        for f in diff:
            if getattr(b, f) is None:
                setattr(b, f, getattr(a, f))
        _count("hud_none")
        return True
    return False


def _stable_frame(*, accept_unstable: bool = False, first=None) -> Frame | None:
    """两帧签名一致才算稳定。accept_unstable=True（等太久）时，第二帧能用就直接用。first：本轮已截的图（不再截第一张）。
    手牌、饮料都一致，只是 HUD 数字一边读成 None（加分特效压暗文字）也算稳定，缺的数取另一帧的。
    第十八局选拔 12 次「连续 10s 不稳定」（exam_unstable_frame），同时段两帧卡名、匹配距离完全一致：
    对不上的是 HUD 或饮料，具体哪项记进 trace（phase_unstable_<项>）。"""
    a = Frame(first if first is not None else device.screenshot())
    if not _usable(a):
        _count("unusable")
        return None
    sleep(STABLE_GAP)
    b = Frame(device.screenshot())
    if _same_sig(a, b):
        return b
    for f in _sig_diff(a, b):
        _count(f)
    if accept_unstable and _usable(b):
        return b
    return None


class BoundedWait:
    """有界等待：start() 后 expired() 在 limit 秒后为 True；reset() 清零。clock 可注入（测试）。"""

    def __init__(self, limit: float, clock=time.monotonic):
        self.limit, self.clock, self.since = limit, clock, None

    def start(self) -> None:
        if self.since is None:
            self.since = self.clock()

    def reset(self) -> None:
        self.since = None

    def expired(self) -> bool:
        return self.since is not None and self.clock() - self.since >= self.limit

    @property
    def waited(self) -> float:
        return 0.0 if self.since is None else self.clock() - self.since


DRINK_USE_TIMEOUT = 8.0  # 含一次重点（第二十五局：演出期间点饮料没响应）
DRINK_RETAP_AFTER = 2.5
DRINK_DIALOG_TOP = 1000  # 「Pドリンク詳細」弹窗底部按钮（キャンセル / 使う）实测 y≈1160


def _screen_calm(max_s: float = 3.0) -> bool:
    """等画面停在可操作的考试帧：没有弹窗标题条、两次轻量签名（剩余回合 / 体力 / 元気 / 手牌位置）一致，最多 max_s 秒。
    第二十五局：两次在出牌演出 / 「スキルカードサポート」横幅期间点饮料，游戏不响应，6 秒后被当成「使用条件不满足」本回合禁用。"""
    deadline, prev = time.monotonic() + max_s, None
    while time.monotonic() < deadline:
        img = device.screenshot()
        sig = _lite_sig(img) if img is not None and _dialog_top(img) is None else None
        if sig is not None and prev is not None and _lite_same(prev, sig):
            return True
        prev = sig
        sleep(0.3)
    return False


def _drink_detail(img) -> tuple[bool, object | None]:
    """（详情框是否出来了，「使う」按钮框或 None）。按钮读成「く使う」也算（_button_text）。"""
    btn = tpl("InProduce.ButtonUse").find(img)
    if btn:
        return True, btn
    boxes = _roi_texts(img, DRINK_DIALOG_TOP - 300, DIALOG_BAND_BOTTOM)
    hit = next((t for t in boxes if _button_text(t.text, "使う") and t.rect.center.y >= DRINK_DIALOG_TOP), None)
    shown = hit is not None or _dialog_top(img) is not None or any("ドリンク詳細" in t.text for t in boxes)
    return shown, hit


def _confirm_drink_use(retap=None) -> list[int] | None:
    """点饮料后的「Pドリンク詳細」弹窗点「使う」。KAA 的 ButtonUse 模板认不出 H.I.F 的按钮，退回按钮行局部 OCR。
    retap()：点了 DRINK_RETAP_AFTER 秒还没出详情框（演出期间点的，游戏没响应）→ 等画面停稳后再点一次饮料。
    做不成时 UNPLAYABLE 置位（主循环本回合禁用这个动作），UNPLAYABLE["why"] 区分：详情框出来了但没有「使う」= 使用条件不满足；
    两次都没打开详情框 = 点饮料没反应（不是使用条件）。注意：kotonebot 的 Loop(timeout=...) 不检查超时，这里自己按时间封顶。"""
    start = time.monotonic()
    deadline, retapped, seen = start + DRINK_USE_TIMEOUT, False, False
    while time.monotonic() < deadline:
        sleep(0.3)
        img = device.screenshot()
        shown, btn = _drink_detail(img)
        seen = seen or shown
        if btn is not None:
            x, y = int(btn.rect.center.x), int(btn.rect.center.y)  # btn：KAA 模板结果或 OCR 文字框
            device.click(x, y)
            _after_use()
            return [x, y]
        if not shown and not retapped and retap is not None and time.monotonic() - start >= DRINK_RETAP_AFTER:
            retapped = True
            logger.info("点饮料 %.1fs 没出详情框（演出中？）：等画面停稳再点一次", DRINK_RETAP_AFTER)
            if _screen_calm():
                retap()
    if seen:
        why = "详情框出来了但没有可点的「使う」（使用条件不满足）"
    else:
        # 第二十三局 R2：画面饮料栏已空，存档 / 记忆里还当 ブーストエキス 在，同一动作反复 163 次（40 分钟）
        why = "点饮料" + ("两次" if retapped else "") + "都没打开详情框（演出中没响应 / 饮料栏已空）"
    logger.warning("点饮料后 %.0fs 内没点到「使う」：%s", DRINK_USE_TIMEOUT, why)
    UNPLAYABLE["hit"], UNPLAYABLE["why"] = True, why  # 主循环本回合禁用这个动作，换别的
    return None


def _after_use() -> None:
    """「使う」之后可能再弹「Pドリンク使用確認」，最多看 1.5s（有标题条才 OCR）。"""
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        sleep(0.5)
        img = device.screenshot()
        top = _dialog_top(img)
        if top is not None:
            boxes = _roi_texts(img, max(0, top - 10), DIALOG_BAND_BOTTOM)
            if _confirm_dialog(boxes, [t.text for t in boxes]):
                return


PLAY_CONFIRM_TIMEOUT = 1.6  # 第一下之后最多等多久看 SELECT
PLAY_RESULT = {
    "confirmed": False
}  # 上一次 _play_card 是否看到 SELECT 并点了确认（D34 核对用：没确认的才可能「没打出去」）
PLAY_RETAPS = 1  # 没看到 SELECT 且手牌没变时再点几次（第二十六局：上一张牌还在结算时点击不被接收）
PLAY_POLL = 0.2
DESELECT_TAP = (360, 330)  # 舞台空白处：点了会取消手牌选中（main13 R2 实测）
DESELECT_WAIT = 0.6
SELECT_DX = 70  # 「SELECT」标签与卡中心的横向容差


def _play_card(x: int, y: int, img=None) -> list[list[int]]:
    """出牌：点一下，然后在 PLAY_CONFIRM_TIMEOUT 内每 PLAY_POLL 秒截图找这张卡下方的「SELECT」，找到就点第二下。
    一直没出现（第一下已打出，或标签出得慢）就不再点：卡若停在选中态，由 _exam_dialogs 的 SELECT 兜底（重新核对后再点）。
    img：决策用的那张截图，出牌前「另一张牌在选中态」的检查直接读它（不再截图）。SELECT 只识别手牌下方条带。
    - 以前 KAA double_click（两下间隔 0.4s）：第一下有时已打出、手牌重排，第二下落到相邻牌（审计「重复点」6 次）。
    - main13 R1（d0 版只等 0.35s 看一次）：标签还没出来就放弃，卡停在选中态十多次，第 4 回合反复决策 6 次。"""
    taps: list[list[int]] = []
    # 出牌前已有别的牌停在选中态（main13 R2 再挑戦 T3：止められない想い+ 选中，决策点 ゆめみごこち，
    # 两张牌来回切换选中 7 次没打出）：先点舞台空白处取消选中，再点目标
    with _phase("pre_check"):
        other = next(
            (
                t
                for t in _selects(img if img is not None else device.screenshot())
                if abs(t.rect.center.x - x) > SELECT_DX
            ),
            None,
        )
    if other is not None:
        logger.info("出牌前另一张牌停在选中态（SELECT x=%d）→ 点舞台空白处取消", int(other.rect.center.x))
        device.click(*DESELECT_TAP)
        taps.append(list(DESELECT_TAP))
        sleep(DESELECT_WAIT)
    before = _lite_sig(img) if img is not None else None
    PLAY_RESULT["confirmed"] = False
    with _phase("click_poll"):
        for attempt in range(1 + PLAY_RETAPS):
            device.click(x, y)
            taps.append([x, y])
            t0 = time.monotonic()
            seen: list[Text] = []
            markers: list[Text] = []
            now = None
            while time.monotonic() - t0 < PLAY_CONFIRM_TIMEOUT:
                sleep(PLAY_POLL)
                now = device.screenshot()
                band = _roi_texts(now, *SELECT_BAND)
                seen = band or seen
                markers = _select_markers(now, band)
                sel = next((t for t in markers if abs(t.rect.center.x - x) <= SELECT_DX), None)
                if sel is not None:
                    # 点下去 → 看到 SELECT 的时间（第三十局：第一下丢点 21 次、每次等满 1.6s 才补点；按这个分布再定能不能早点判「没收到」）
                    PHASES["play_select_ms"] = PHASES.get("play_select_ms", 0.0) + (time.monotonic() - t0) * 1000
                    _count(f"play_select_try{attempt + 1}")
                    device.click(x, y)
                    PLAY_RESULT["confirmed"] = True
                    return taps + [[x, y]]
            # 第二十六局：剪枝预演后决策快了，点击常落在上一张牌还在结算的 1.5–3.5s 里，游戏不收（34 次，以前空等 10s 再决策）。
            # 手牌没变（卡没打出去）、条带上也没有任何 SELECT（没选中别的）：马上再点一次
            if (
                attempt >= PLAY_RETAPS
                or before is None
                or now is None
                or markers
                or any(_is_select(t.text) for t in seen)
            ):
                break
            after = _lite_sig(now)
            if after is None or not _lite_same(before, after):
                break  # 手牌变了（第一下其实打出去了 / 动画中）：不再点，交给主循环
            logger.info(
                "出牌 (%d,%d)：%.1fs 内没看到 SELECT、手牌没变（点击没被接收？）→ 再点一次", x, y, PLAY_CONFIRM_TIMEOUT
            )
            _count("play_retap")
    # 诊断（main13 R2：常常 1.6s 内看不到 SELECT、靠兜底补点）：记下最后一次条带识别到的文字和位置（不再整帧 OCR）
    logger.info("出牌 (%d,%d)：%.1fs 内没看到 SELECT，手牌下方文字 %s", x, y, PLAY_CONFIRM_TIMEOUT,
                [(t.text, int(t.rect.center.x), int(t.rect.center.y)) for t in seen][:12])  # fmt: skip
    return taps


def _execute(d: Decision, frame: Frame) -> list[list[int]]:
    """执行并返回点击位置（720x1280 逻辑坐标）。"""
    if d.kind == "play":
        c = frame.cards[d.slot]
        return _play_card(int(c.rect.center.x), int(c.rect.center.y), frame.img)
    if d.kind == "drink":
        _, rect = frame.drinks[d.slot]
        x, y, w, h = rect
        r = Box(x, y, x + w, y + h)
        with _phase("drink_calm"):
            _screen_calm()  # 演出 / 横幅期间点饮料游戏不响应（第二十五局 R1、R2 各一次）：先等画面停稳
        device.click(*r.center)
        taps = [[int(r.center.x), int(r.center.y)]]

        def retap():
            device.click(*r.center)
            taps.append([int(r.center.x), int(r.center.y)])

        with _phase("drink_confirm"):
            use = _confirm_drink_use(retap)
        if use is not None:
            taps.append(use)
        return taps
    device.click(*SKIP_BUTTON.center)
    return [[int(SKIP_BUTTON.center.x), int(SKIP_BUTTON.center.y)]]


SELECT_GRACE = 1.5  # 执行后这么久内不做 SELECT 兜底（第十六局每步都在这里看到残影、多等 0.8s 复核）


def _wait_change(before: tuple) -> bool:
    """执行后轮询：每轮一张截图，先读 HUD / 手牌看动作是否生效，没变才查弹窗与 SELECT（都读这一张）。立即开始，不先睡；
    返回得早不会误读中间帧——主循环下一步还要 _stable_frame 两帧一致才决策。
    先看签名再查弹窗：出牌成功后手牌已变，不必再识别卡下方残留的 SELECT（第十六局每步都在这里多等 0.8s 复核残影）；
    弹出的确认框（ターン終了 / 使用確認）压暗画面、签名不变时照常处理，签名变了的由主循环下一轮处理。"""
    start = time.monotonic()
    deadline = start + CHANGE_TIMEOUT
    while time.monotonic() < deadline:
        img = device.screenshot()
        with _phase("wait_frame"):
            f = Frame(img)
        if f.remaining is None or f.signature != before:
            # 读不到 HUD（动画 / 结算中）也说明动作生效了
            return True
        with _phase("wait_dialogs"):
            # 出牌第二下之后卡还在选中态 / 飞出动画里，SELECT 是残影：SELECT_GRACE 内只查弹窗，之后还在才算卡住
            if _handle_dialogs(img, select=time.monotonic() - start >= SELECT_GRACE):
                return True
        sleep(CHANGE_POLL)
    return False


_LAST_EXEC = {"t": 0.0}  # 上一个考试动作执行完（等完画面变化）的时刻


ACTIONS_FIX_SETTLE = 3.0  # 离上一步不到这么久，行动数为 0 时先确认回合没在自动结束


TURN_END_CHECK_S = 3.0  # 只点了一下的出牌：点完最多等这么久读 HUD，核对回合有没有在没打出的情况下结束


def _turn_ending(frame) -> bool:
    """行动数为 0、画面还停在出牌界面：离上一步不到 ACTIONS_FIX_SETTLE 秒就等到那时再看一眼，剩余回合 / 手牌变了（或读不到）= 回合正在自动结束。"""
    since = time.monotonic() - _LAST_EXEC["t"]
    if since >= ACTIONS_FIX_SETTLE:
        return False
    try:
        a = _lite_sig(frame.img)
        sleep(ACTIONS_FIX_SETTLE - since)
        b = _lite_sig(device.screenshot())
    except Exception:  # noqa: BLE001
        return False
    return a is None or b is None or not _lite_same(a, b)


def _play_observable(card_id: str | None, upgrade: int | None) -> bool:
    """打出这张牌会不会动元気 / 体力（D34 拿它们没变当「没打出去」的证据）。0 消耗、不加元気的牌（眠気等）打出后两者都不变，
    用掉本回合最后一次出牌时回合自然结束，不能据此判没打出去（偶像之路 Mac 15 层 11-14：除外区的眠気一步步从 2 涨到 8）。
    主数据里查不到的牌按「会动」算（保持原判法）。"""
    from hiro import master

    c = master.card(card_id, upgrade or 0) if card_id else None
    if c is None:
        return True
    text = (c.produce_descriptions or "") + c.effect_display_text
    other_cost = bool(c.cost_value) and (c.cost_type or "").rsplit("_", 1)[-1] not in (
        "",
        "Unknown",
    )  # 消耗好印象 / やる気 等
    return bool(c.stamina or c.force_stamina or other_cost) or "元気" in text or "体力" in text


def _play_missed(frame, confirmed: bool, observable: bool = True) -> bool:
    """D34：没看到 SELECT、没点确认的出牌（只点了一下，或第一下没被接收补点了一下），点完读 HUD：
    剩余回合变少、元気 / 体力都没变 = 回合自动结束了，牌没打出去。confirmed：_play_card 看到 SELECT 并点了确认。
    observable=False（打这张牌本来就不动元気 / 体力，见 _play_observable）：没有证据，不判没打出去。"""
    if confirmed or not observable:
        return False
    try:
        a = _lite_sig(frame.img)
        if a is None:
            return False
        deadline, b = time.monotonic() + TURN_END_CHECK_S, None
        while time.monotonic() < deadline:
            b = _lite_sig(device.screenshot())
            if b is not None:
                break
            sleep(0.3)
    except Exception:  # noqa: BLE001
        return False
    if b is None:
        return False
    (rem_a, hp_a, genki_a), (rem_b, hp_b, genki_b) = a[0], b[0]
    return rem_a is not None and rem_b is not None and rem_b < rem_a and hp_a == hp_b and genki_a == genki_b


def _screen_hand_names(frame) -> list | None:
    """识别层手牌（live_slot_mismatch 记录用）：[[card_id, upgrade, name]…]；没有识别层手牌时退回 KAA 的卡。"""
    if getattr(frame, "hand", None) is not None:
        return [[c.get("card_id"), c.get("upgrade"), c.get("name")] for c in frame.hand]
    return [
        [c.card._id, None, c.card.name] if c is not None and c.card is not None else [None, None, None]
        for c in frame.cards or []
    ]


def _screen_slot(frame, state, d) -> int | None:
    """存档里的槽位 → 屏幕槽位。手牌顺序应一致，不一致时按卡 ID 找；饮料按饮料 ID 找。"""
    if d.kind == "play":
        want, up = state.hand[d.slot].card_id, state.hand[d.slot].upgrade
        if getattr(frame, "hand", None) is not None:
            # 识别层手牌（KAA 会把 紙吹雪 认成 ふれんどめもりーず 等，按它找会找不到或错位）
            ids = [(c.get("card_id"), c.get("upgrade")) for c in frame.hand]
        else:
            ids = [(c.card._id, None) if c is not None and c.card is not None else (None, None) for c in frame.cards]

        def same(i: int, strict: bool) -> bool:
            cid, u = ids[i]
            return cid == want and (not strict or u is None or up is None or u == up)

        if d.slot < len(ids) and same(d.slot, True):
            return d.slot
        found = next(
            (i for i in range(len(ids)) if same(i, True)), next((i for i in range(len(ids)) if same(i, False)), None)
        )
        if (
            found is None
            and getattr(frame, "hand", None) is not None
            and len(ids) == len(state.hand)
            and d.slot < len(ids)
        ):
            # 排除法（只用识别层手牌；KAA 兜底在动画中可能错位，不用）：张数一致、其余位置都和存档对得上，只有这一格认不出（main12 R2 T2/T3 エウレカ / えいえいおー 认不出被禁用，
            # 期望 19.9 万 → 11.8 万）：手牌顺序与存档一致，按存档槽位点
            # 只在那一格真的认不出（card_id None）时用；认得出但不是目标（续推抽错的牌）不能点（识屏 c573b72 提醒）
            others = [i for i in range(len(ids)) if i != d.slot]
            if ids[d.slot][0] is None and all(ids[i][0] == state.hand[i].card_id for i in others):
                return d.slot
        return found
    if d.kind == "drink":
        target = next((x for x in state.drinks if x.slot == d.slot), None)
        if target is None:
            return None
        want = target.drink_id
        found = next((i for i, (dr, _) in enumerate(frame.drinks) if dr is not None and dr.id == want), None)
        if found is not None:
            return found
        # 画面上没认出这瓶：存档的饮料顺序与饮料栏从左到右一致（本局 trace 14 步全部核实），按固定槽位点
        rect = drink_slot_rect(d.slot)
        if rect is None:
            return None
        frame.drinks.append((None, rect))
        return len(frame.drinks) - 1
    return None


def _frame_fields(frame: Frame, exam: str) -> dict:
    """一帧画面 → HIF_EXAM 的契约字段（与 hiro.vision.observe.battle_observation 同名）。"""
    return {
        "exam_label": exam,
        "remaining_turns": frame.remaining,
        "hp": frame.hp,
        "genki": frame.genki,
        "hand": [v.to_dict() for v in frame.hand_views()],
        "drinks": [drink_view(i, dr).to_dict() for i, (dr, _) in enumerate(frame.drinks)],
    }


def _save_fields(state: BattleState) -> dict:
    return {
        "remaining_turns": state.remaining_turns, "hp": state.hp, "genki": state.genki, "motivation": state.motivation,
        "impression": state.impression, "score": state.score,
        "hand": [c.to_dict() for c in state.hand], "drinks": [d.to_dict() for d in state.drinks],
    }  # fmt: skip


MEMORY_FILE = "exam_memory.json"
MEMORY_MAX_AGE_S = 3 * 3600


def save_exam_memory(trace_dir, memory: ExamMemory, *, finished: bool = False) -> None:
    """考试记忆落盘到 trace 目录：崩溃 / 重启续跑时读回本场的计划、历史、复制饮料目标、道具剩余次数。"""
    import json

    path = Path(trace_dir) / MEMORY_FILE
    data = {"label": memory.exam, "saved_at": time.time(), "finished": finished, "memory": memory.to_dict()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_exam_memory(trace_dir, label: str) -> tuple[ExamMemory, Path] | None:
    """在当前 trace 目录和同一局的续跑目录（同名前缀 + -rN）里找本场考试未结束的记忆，取最近写入的一份。"""
    import json
    import re

    here = Path(trace_dir)
    base = re.sub(r"-r\d+$", "", here.name)
    dirs = {here, here.parent / base, *here.parent.glob(f"{base}-r*")}
    found = []
    for d in dirs:
        f = d / MEMORY_FILE
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            data.get("label") != label
            or data.get("finished")
            or time.time() - data.get("saved_at", 0) > MEMORY_MAX_AGE_S
        ):
            continue
        found.append((data["saved_at"], d, data["memory"]))
    if not found:
        return None
    _, d, mem = max(found, key=lambda x: x[0])
    return ExamMemory.from_dict(mem), d


def _plugin(kind: str):
    from hiro import plugins

    return plugins.get(kind)


def r1_deck_to_note(exam: str, session, context: dict) -> list | None:
    """R1 结束时要记进 finals_context 的牌组（轮间模拟合成 R2 开局用）：本场已经记过牌组就不覆盖（返回 None）——
    私有存档插件开考时从存档记的、续跑读回的都比培育记账准；没记过才用培育记账的牌组（state.produce.deck）兜底。"""
    if exam != "r1" or session is None or context.get("deck"):
        return None
    rows = (getattr(session.state, "produce", None) or {}).get("deck") or []
    return [
        (c["card_id"], int(c.get("upgrade") or 0)) for c in rows if isinstance(c, dict) and c.get("card_id")
    ] or None


def _exam_family(label: str | None, session) -> str | None:
    """这场考试属于哪个模式家族（hif / hajime / nia / tower）：先看局面的 produce.mode，再看考试标签。"""
    from hiro.state.modes import family

    produce = (getattr(getattr(session, "state", None), "produce", None) or {}) if session is not None else {}
    if (f := family(produce.get("mode"))) is not None:
        return f
    lab = str(label or "").lower()
    if lab in ("r1", "r2") or lab.startswith("qualify"):
        return "hif"
    for f in ("hajime", "nia", "tower"):
        if lab.startswith(f):
            return f
    return None


def seed_max_hp(memory: ExamMemory, session, rec=None, profile=None) -> int | None:
    """开考时给本场考试记忆填最大体力：取培育页头部 OCR 读到的「体力 x/上限」（主循环状态 produce.max_stamina）。
    最大体力随偶像卡等级、开花、账号等级、支援卡等浮动，不写死；考试画面只显示当前体力，所以要在培育页读。
    记忆里已经有（续跑读回）就不覆盖；读不到时用构筑表里这个模式已知的值兜底（IdolProfile.known_max_hp，广 H.I.F 33，
    来源记 profile_known，协调 11-20：续跑 / 开考前没经过培育页 / DMM 页头没读出时，广的固有道具和本战配额模板全失效）；
    再没有就留 None（不猜），日志写明。"""
    if getattr(memory, "max_hp", None):
        return memory.max_hp
    if not hasattr(memory, "set_max_hp"):
        return None  # 不是 ExamMemory（插件自带的记忆）：不管
    produce = (getattr(getattr(session, "state", None), "produce", None) or {}) if session is not None else {}
    value = produce.get("max_stamina")
    if memory.set_max_hp(value, "produce_screen"):
        logger.info("[%s] 最大体力 %s（培育页读数）", memory.exam, memory.max_hp)
    elif (
        known := (getattr(profile, "known_max_hp", None) or {}).get(_exam_family(memory.exam, session))
    ) and memory.set_max_hp(known, "profile_known"):
        logger.warning(
            "[%s] 培育页没读出体力上限：最大体力按构筑表兜底 %s（%s 的已知值）",
            memory.exam,
            memory.max_hp,
            getattr(profile, "name", "?"),
        )
    else:
        logger.warning("[%s] 最大体力未读到（培育页没读出体力上限）：半血 / 道具判断按未知处理", memory.exam)
    if rec is not None:
        rec.log("exam_max_hp", exam=memory.exam, max_hp=memory.max_hp, source=memory.max_hp_source)
    return memory.max_hp


def _round_deck(session) -> list:
    """这一场考试的牌组 [(card_id, 强化次数)]：培育记账（R2 含轮间买的）；没有时用 R1 记下的（finals_context）。"""
    rows = []
    if session is not None:
        rows = (getattr(session.state, "produce", None) or {}).get("deck") or []
    deck = [(c["card_id"], int(c.get("upgrade") or 0)) for c in rows if isinstance(c, dict) and c.get("card_id")]
    if deck:
        return deck
    from hiro.state.finals_context import CONTEXT

    return [tuple(x[:2]) for x in CONTEXT.get("deck") or []]


SCHEDULE_PROBED: set[str] = set()  # 已经探测过审查日程的考试标签（再挑戦同一场不再点）


def _not_executed(advisor, action, why: str) -> None:
    """决策给出的动作没在游戏里生效（帧过时没点 / 牌没打出去 / 点了画面没变）：告诉出招插件（可选钩子 not_executed），
    搜索类算法据此把引擎退回出牌前（N.I.A MASTER 第一局 11-18：帧过时没点的 センブリソーダ 在引擎里喝了两次，对账引擎高估 4.56 倍）。"""
    hook = getattr(advisor, "not_executed", None)
    if hook is None:
        return
    try:
        hook(action, why)
    except Exception as e:  # noqa: BLE001  钩子出错不影响执行
        logger.info("出招插件 not_executed 出错：%s: %s", type(e).__name__, e)


def run_exam(
    brain: BattleBrain,
    exam: str,
    rec: Recorder,
    ended: Callable[[object], bool],  # 传本轮截图
    *,
    target_score: int | None = None,
    session=None,
    source=None,
    advisor=None,
    round_no: int | None = None,
) -> ExamMemory:
    """一场考试的主循环。brain：记忆管理（new_memory / commit）与偶像档案；source / advisor：局面来源与出招（不给时取
    hiro.plugins 注册 / 默认的实现）。session 给出时每个决策点写一条 trace Step（观测 → 状态 → 决策 → 执行结果，带分阶段耗时）。"""
    source = source or _plugin("exam_state_source")
    advisor = advisor or _plugin("exam_advisor")
    if source is None or advisor is None:
        raise RuntimeError("考试需要 ExamStateSource 与 ExamAdvisor 插件（hiro.plugins），一个都没拿到")
    memory = brain.new_memory(exam)
    restored = None
    if session is not None:
        session.state.exam_session.label = exam
        if (got := load_exam_memory(session.trace.dir, exam)) is not None:
            memory, restored = got
            rec.log(
                "exam_memory_restored",
                exam=exam,
                source=str(restored),
                turn=memory.turn,
                last_remaining=memory.last_remaining,
            )
            session.note(f"读回本场考试记忆（{restored.name}）：T{memory.turn}")
    seed_max_hp(memory, session, rec, brain.profile)
    source.begin(
        {
            "exam": exam,
            "profile": brain.profile,
            "target_score": target_score,
            "session": session,
            "brain": brain,
            "rec": rec,
        }
    )
    advisor.on_round_start(round_no if round_no is not None else (2 if exam == "r2" else 1),
                           {"exam": exam, "idol": brain.profile.idol_id if brain.profile else None, "brain": brain, "max_hp": getattr(memory, "max_hp", None),
                            "deck": _round_deck(session), "session": session})  # fmt: skip
    rec.log(
        "exam_brain_start",
        exam=exam,
        profile=brain.profile.idol_id if brain.profile else None,
        source=source.confidence(),
    )
    failures = 0
    wait_state = BoundedWait(LIVE_WAIT_S)  # 局面来源暂时给不出（返回 None）时最多等这么久，之后换帧重读
    stable_wait = BoundedWait(STABLE_WAIT_S)
    PHASES.clear()
    animating = True  # 开场 / 动作生效后：到下一帧稳定决策之前都算动画，点舞台空白处推进
    stale_streak = 0
    _anim["taps"] = 0
    unusable_since = None
    t_cycle = time.monotonic()
    last_action = None
    UNPLAYABLE["hit"] = False
    result_info: dict = {}
    last_state = None
    progress_t, progress_remaining, stalled = time.monotonic(), None, False
    try:
        for _ in Loop(interval=0.1):
            if (idle := time.monotonic() - progress_t) >= EXAM_STALL_S:
                stalled = True
                logger.error(
                    "[%s] 考试 %.0fs 没有进展（没有动作生效、残りターン 没变），退出考试循环交回主循环", exam, idle
                )
                rec.log(
                    "exam_stalled", exam=exam, idle_s=round(idle, 1), turn=memory.turn, remaining=progress_remaining
                )
                if session is not None:
                    session.recover("unhandled", f"考试 {idle:.0f}s 没有进展，退出考试循环交回主循环")
                break
            img = device.screenshot()
            with _phase("dialogs"):
                slow = unusable_since is not None and time.monotonic() - unusable_since >= SLOW_DIALOG_AFTER
                if _handle_dialogs(img, slow=slow):
                    _guard_dialog_loop(img, session, memory, last_action, exam)
                    continue
            with _phase("ended"):
                if ended(img):
                    break
            frame = None
            if animating and not slow:
                with _phase("settle_fast"):
                    frame = _settle_fast(img)
                if frame is None:
                    img = device.screenshot()
            stable_wait.start()
            if frame is None:
                with _phase("stable_frame"):
                    frame = _stable_frame(accept_unstable=stable_wait.expired(), first=img)
            if frame is not None and animating:
                animating = False
                PHASES["settle"] = PHASES.get("settle", 0.0) + (time.monotonic() - t_cycle) * 1000
            if frame is None:
                if unusable_since is None:
                    unusable_since = time.monotonic()
                continue
            unusable_since = None
            if frame.remaining is not None and frame.remaining != progress_remaining:
                progress_t, progress_remaining = time.monotonic(), frame.remaining
            if UNPLAYABLE.pop("hit", False) and last_action is not None:
                memory.block(last_action)
                why = UNPLAYABLE.pop("why", None) or "使用条件不满足（一直停在选中态）"
                logger.warning("[%s] %s %s，本回合禁用", exam, last_action, why)
                if session is not None:
                    session.recover("tap_no_effect", f"{last_action} {why}，本回合禁用")
            if restored is not None:
                if (
                    frame.remaining is not None
                    and memory.last_remaining is not None
                    and frame.remaining > memory.last_remaining
                ):
                    rec.log(
                        "exam_memory_discarded",
                        exam=exam,
                        screen_remaining=frame.remaining,
                        memory_remaining=memory.last_remaining,
                    )
                    memory = brain.new_memory(exam)  # 画面剩余回合比记忆多：不是同一场（再挑戦 / 新开）
                    seed_max_hp(memory, session, rec, brain.profile)
                restored = None
            if stable_wait.waited >= STABLE_WAIT_S and session is not None:
                session.note(f"画面 {stable_wait.waited:.0f}s 不稳定，用单帧出决策")
            stable_wait.reset()
            if (schedule_probe.enabled() and schedule_probe.should_probe(exam) and exam not in SCHEDULE_PROBED and memory.turn <= 1
                    and restored is None and frame.remaining is not None):  # fmt: skip
                # 审查日程探测（默认关）：每场考试第 1 回合出牌前点开一次，存帧、关掉、记耗时，回到考试画面后重新截图再决策
                SCHEDULE_PROBED.add(exam)
                from hiro.vision.schedule import read_schedule_panel

                with _phase("schedule_probe"):
                    got = schedule_probe.probe(frame.img, exam=exam, shoot=device.screenshot, click=lambda x, y: device.click(x, y),
                                               ocr_boxes=ops.ocr_boxes, is_exam=schedule_probe.back_in_exam, log=rec.log,
                                               note=session.note if session is not None else None, read_panel=read_schedule_panel,
                                               swipe=lambda *a: device.swipe(*a), total_turns=frame.remaining)  # fmt: skip
                schedule_probe.apply_panel(got.get("panel"), session, memory)
                animating = True
                continue
            # ---- 局面（插件）
            with _phase("state_read"):
                state, why = source.read(frame, memory)
            if state is None:
                # 来源暂时给不出（过场白光 hand_overlay、存档还没写到这一步…）：有界等待，换帧重读
                wait_state.start()
                _count(f"state_none_{(why or 'unknown')[:20]}")
                if not wait_state.expired():
                    _move_to_redo()
                    animating = True
                    continue
                rec.log(
                    "exam_state_unavailable",
                    exam=exam,
                    reason=why,
                    waited=round(wait_state.waited, 1),
                    remaining=frame.remaining,
                )
                if session is not None:
                    session.note(f"局面来源 {wait_state.waited:.1f}s 给不出局面（{why}），换帧重读")
                wait_state.reset()
                _move_to_redo()
                animating = True
                continue
            wait_state.reset()
            last_state = state
            obs = session.last if session is not None else None
            # ---- 决策（插件）
            t_decide = time.monotonic()
            previews = source.previews(state)
            legal = [Action.from_legal(a) for a in state.legal_actions()]
            contract = advisor.decide(state, memory, previews, legal)
            d = contract.to_battle()
            rec.log(
                "battle_decision", image=frame.img, exam=exam, source=source.confidence(), contract=contract.to_dict()
            )
            logger.info("[%s T%s] %s ← %s：%s", exam, memory.turn, d.action, d.source, d.reason)
            todo = d
            if d.kind == "drink" or (source.confidence() == "save" and d.kind == "play"):
                # 局面槽位（存档顺序 / 饮料栏格位）→ 画面槽位：按卡 / 饮料 ID 映射
                slot = _screen_slot(frame, state, d)
                if slot is None:
                    what = (
                        next((x.name for x in state.drinks if x.slot == d.slot), d.action)
                        if d.kind == "drink"
                        else state.hand[d.slot].name
                    )
                    memory.block(d.action)
                    contract.warnings.append(f"{what} 在画面上定位不到，本回合不再选它")
                    if session is not None:
                        session.recover("page_misjudged", f"局面与画面对不上：{d.action}（{what}），本回合禁用该动作")
                        session.record(obs, decision=contract)
                    _move_to_redo((time.monotonic() - t_decide) * 1000)
                    continue
                todo = Decision(f"{d.kind}:{slot}", d.reason, d.source, d.plan, d.line, d.warnings)
            with _phase("tap_check"):
                stale = _stale_before_tap(frame) if stale_streak < STALE_MAX else None
            if stale is not None:
                stale_streak += 1
                _count("stale_frame")  # 决策帧过时（报告 exam_settle.stale_frames）
                rec.log("exam_stale_frame", exam=exam, action=d.action, reason=stale, remaining=frame.remaining)
                logger.warning("[%s] 决策帧已过时（%s）：不点 %s，重新决策", exam, stale, d.action)
                _not_executed(advisor, d.action, f"决策帧已过时（{stale}）")
                if session is not None:
                    session.recover(
                        "page_misjudged", f"决策帧与点击前画面不一致（{stale}），{d.action} 不执行，重新决策"
                    )
                animating = True
                _move_to_redo((time.monotonic() - t_decide) * 1000)
                continue
            stale_streak = 0
            # ---- 执行
            t_tap = time.monotonic()
            with _phase("execute"):
                taps = _execute(todo, frame)
            last_action = d.action
            with _phase("wait_change"):
                changed = _wait_change(frame.signature)
            missed = None
            if changed and todo.kind == "play":
                with _phase("play_verify"):
                    card = state.hand[d.slot] if d.kind == "play" and 0 <= d.slot < len(state.hand) else None
                    observable = (
                        _play_observable(getattr(card, "card_id", None), getattr(card, "upgrade", None))
                        if card
                        else True
                    )
                    if _play_missed(frame, PLAY_RESULT["confirmed"], observable):
                        missed = "回合自动结束了，牌没打出去（没看到 SELECT、没点确认；剩余回合减少、元気 / 体力没变）"
            t_done = time.monotonic()
            _LAST_EXEC["t"] = t_done
            result = ExecutionResult(action=Action.from_legal(todo.action), ok=changed and missed is None, taps=taps,
                                     waited_s=round(t_done - t_tap, 3), page_before="HIF_EXAM", page_after="HIF_EXAM", expected_transition=True,
                                     retries=failures, error=missed if missed is not None else None if changed else "画面未变化",
                                     seconds=round(t_done - t_tap, 3))  # fmt: skip
            if missed is not None:
                logger.warning("[%s] %s：%s，回到主循环重读", exam, d.action, missed)
                _not_executed(advisor, d.action, missed)
                rec.log("battle_play_missed_turn_end", exam=exam, action=d.action, remaining=frame.remaining)
                memory.plan = []
                if session is not None:
                    session.recover("tap_no_effect", f"{d.action} {missed}")
                    session.note(f"blunder:play_missed_turn_end D34 {d.action}")
            elif changed:
                progress_t = time.monotonic()
                brain.commit(state, memory, d)
                source.executed(Action.from_legal(d.action), result)
                failures = 0
                if session is not None:
                    save_exam_memory(session.trace.dir, memory)
            else:
                failures += 1
                memory.plan = []
                _not_executed(advisor, d.action, "执行后画面未变化")
                rec.log("battle_action_no_effect", exam=exam, action=d.action, failures=failures)
                if session is not None:
                    session.recover(
                        "tap_no_effect", f"{d.action} 执行后画面 {CHANGE_TIMEOUT:.0f}s 未变（第 {failures} 次）"
                    )
                if failures >= 3:
                    logger.warning("连续 %d 次动作无效，跳过回合", failures)
                    device.click(*SKIP_BUTTON.center)
                    if session is not None:
                        session.note("连续 3 次动作无效，强制跳过回合")
                    failures = 0
            if session is not None:
                session.record(obs, decision=contract, previews=previews or [], execution=result,
                               timings_ms={"shot_to_tap": (t_tap - frame.t) * 1000, "decide": (t_tap - t_decide) * 1000,
                                           "wait_change": (t_done - t_tap) * 1000, "cycle": (t_done - t_cycle) * 1000,
                                           "anim_taps": _anim["taps"], **_take_phases()})  # fmt: skip
                _guard_repeated_decision(session, memory, d, exam)
            PHASES.clear()
            _anim["taps"] = 0
            t_cycle = t_done
            animating = animating or changed
        if not stalled:
            result_info = {"exam": exam, "turns": memory.turn, "history": memory.history}
    finally:
        rec.log("exam_brain_end", exam=exam, turns=memory.turn, history=memory.history)
        if last_state is not None:  # 本战 R1：剩下的饮料和三属性倍率记进 finals_context，轮间 / R2 决策用（只记 r1）
            from hiro.state.finals_context import CONTEXT, note_exam

            seen = getattr(memory, "attr_permil", None) or {}
            permil = {**{k: v for k, v in seen.items() if v}, **(CONTEXT.get("permil") or {})} if exam == "r1" else seen
            deck = r1_deck_to_note(exam, session, CONTEXT)
            note_exam(exam, deck=deck, drinks=[x.drink_id for x in last_state.drinks if getattr(x, "drink_id", None)], permil=permil,
                      max_hp=getattr(memory, "max_hp", None), trace_dir=session.trace.dir if session is not None else None)  # fmt: skip
        source.end(result_info or None)
        advisor.on_round_end(result_info or None)
        if session is not None:
            save_exam_memory(
                session.trace.dir, memory, finished=not stalled
            )  # 卡住退出的不算打完：主循环再进考试时读回记忆
    return memory
