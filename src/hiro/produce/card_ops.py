"""授業换卡（两阶段）、删卡 / 强化页（全屏选卡）、P 道具背包三选一的执行器（原在 hiro.loop.kaa_patches，去 KAA 第 5/6 步搬出来）。

设备、睡眠走 hiro.devices.active（KAA 主循环下就是 kotonebot，新主循环下是 runtime 的带拦截设备）。
kaa_patches.apply 仍把 change_stage1 / change_stage2 / pitem_commit 挂到 KAA 的上下文上（旧主循环用）。
部分逻辑改写自 KAA（kotones-auto-assistant，GPL-3.0）的同名流程。
"""

import logging
import time
from collections.abc import Callable

from hiro import imgio
from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep
from hiro.vision.ocr import screen_texts  # noqa: F401

logger = logging.getLogger(__name__)

# 授業换卡
CHANGE_LETTERS_BOX = (40, 860, 640, 160)  # (x, y, w, h)； 候选卡行的角标带；KAA 参考图上下平移 ±30px 都能找到 3 张
CHANGE_STAGE_TIMEOUT = 15.0  # 单个阶段最多等多久（含入场动画）
CHANGE_POLL = 0.5
CHANGE_LETTER_THRESHOLD = 0.65  # 与 KAA 阶段 1 相同
CHANGE2_CARD_AREA = (66, 623, 641, 1106)  # KAA DialogCardChange2.BoxCardArea（x1, y1, x2, y2）
CHANGE_CANCEL_TAP = (211, 1160)  # 阶段 2 左下「キャンセル」（KAA 参考图 dialog_card_change_2.png）
CHANGE1_MARK_X = (213, 352, 491)  # 阶段 1 三张候选的角标 x（参考图实测，列距 139）
CHANGE1_COL_TOL = 40

# 删卡 / 强化页（全屏选卡）
CARD_OP_REGION = {
    "remove": (0, 380, 720, 1110),
    "enhance": (0, 690, 720, 1115),
}  # 同识别 deck.REMOVAL_GRID / ENHANCE_GRID  # 强化页上方两格详情栏里也有卡面，卡格从 y≈700 起
CARD_OP_BUTTON = {"remove": ("削除",), "enhance": ("強化",)}
CARD_OP_BUTTON_TAP = {"remove": (360, 1158), "enhance": (364, 1160)}  # 没有取消键时底部居中的唯一按钮（参考图）
CARD_OP_BUTTON_TAP_WITH_CANCEL = (520, 1161)  # 相談删卡页：左「キャンセル」右「削除」
CARD_OP_CONFIRM = {
    "remove": ("削除する", "削除", "決定", "はい", "OK"),
    "enhance": ("強化する", "強化", "決定", "はい", "OK"),
}  # 相談删卡确认框的按钮就叫「削除」
CARD_OP_DONE = {"remove": ("削除しました",), "enhance": ("強化しました",)}
CARD_OP_LABEL = {"remove": "删除", "enhance": "强化"}
CARD_OP_TIMEOUT = 5.0  # 等卡格出现 / 选中
CARD_OP_SCROLL_PX = 160  # 卡格内一次滑约 1 行多（行距 147）：露 3 行时相邻两屏重叠 2 行。
# 以前滑 294（2 行），第十六、十七局轮间强化都滑过了头，「第 2 屏和上一屏没有重叠的行」→ 拼接不可信
CARD_OP_SWIPE_DURATION, CARD_OP_SCROLL_SETTLE, CARD_OP_MAX_SCREENS = 1.0, 0.8, 10
CARD_OP_DONE_TIMEOUT = 10.0
# 卡图延迟加载（DMM 版 2026-11-10 dmm1m 相談删 眠気：对话框刚打开 / 刚滑出来的几行卡面还是白色占位，认不出 →
# 相邻两屏对不上「没有重叠的行」、拼出 38 张；第 3 次卡图已缓存才拼对）。有格子认不出就隔一会儿重读，直到全认出或不再变好
CARD_ART_WAIT_S, CARD_ART_POLL, CARD_ART_STALL = 4.0, 0.4, 3
# 点卡后先等选中框 / 上方详情切过去再核对，且至少看 3 帧（第二十三局录像时读一次卡格数秒：5 秒里只看了点完立刻截的一帧，
# 读到的总是上一张选中的卡，连续 3 次「选中不符」停机——画面上其实已经选中了目标）
CARD_OP_PICK_SETTLE, CARD_OP_PICK_CHECKS = 0.6, 3

# H.I.F P 道具背包三选一
PITEM_CENTERS = (
    (125, 870),
    (341, 870),
    (557, 870),
)  # 720x1280；r2 实机帧按红 / 黄 / 绿内框测得（框宽 146），误差约 ±3px
PITEM_ACQUIRE_BTN = (240, 1045, 240, 70)  # 「受け取る」按钮区（x, y, w, h）
PITEM_LIT_TIMEOUT = 4.0  # 点包后等按钮变亮
PITEM_ACQUIRE_TIMEOUT = 5.0  # 点「受け取る」后等按钮消失
PITEM_TRIES = 2


def read_loaded(
    shoot, region, sleep=_sleep, img=None, *, clock=time.monotonic, timeout: float = CARD_ART_WAIT_S
) -> tuple:
    """read_card_dialog，但有格子认不出（卡图还在加载）时隔 CARD_ART_POLL 秒重截重读：全认出、连续 CARD_ART_STALL 次
    没变好（真认不出的卡）或到 timeout 就停，返回认得最全的那一次 (img, dialog)。"""
    from hiro.vision.deck import read_card_dialog

    img = shoot() if img is None else img
    d = read_card_dialog(img, region)

    def unknown(dd):
        return sum(c.get("card_id") is None for c in dd["cards"])

    t0, stall, waited = clock(), 0, False
    while unknown(d) and stall < CARD_ART_STALL and clock() - t0 < timeout:
        sleep(CARD_ART_POLL)
        waited = True
        im2 = shoot()
        d2 = read_card_dialog(im2, region)
        if unknown(d2) < unknown(d) or len(d2["cards"]) > len(d["cards"]):
            img, d, stall = im2, d2, 0
        else:
            stall += 1
    if waited:
        logger.info("卡格等卡图加载 %.1fs：还有 %d 格认不出", clock() - t0, unknown(d))
    return img, d


def find_card_marks(img, region, threshold: float | None = None) -> list:
    """在 img 的 region（x, y, w, h）内找技能卡角标 A / M，按行（40px 内算同一行）再按 x 排序。threshold=None 用模板缺省阈值。"""
    from hiro.vision.ui_templates import tpl

    found = [
        m
        for name in ("InProduce.A", "InProduce.M")
        for m in tpl(name).find_all(img, region=region, threshold=threshold)
    ]
    return sorted(found, key=lambda m: (m.rect.y1 // 40, m.rect.x1))


def _poll(check, timeout: float):
    """截图后调用 check(img)，直到返回真值或超时；返回最后一次结果。"""
    deadline = time.monotonic() + timeout
    while True:
        result = check(device.screenshot())
        if result or time.monotonic() >= deadline:
            return result
        _sleep(CHANGE_POLL)


def _stage1_mark(marks: list, index: int):
    """阶段 1 候选：第 index 张 = 从左到右第 index 列（与识别 candidates 的 index 一致）。
    角标漏检时不能直接按列表序号取（会错位），按列位置 x ≈ CHANGE1_MARK_X[index] 找；找不到返回 None。"""
    if not 0 <= index < len(CHANGE1_MARK_X):
        return None
    row = sorted(marks, key=lambda m: m.rect.x1)
    if len(row) == len(CHANGE1_MARK_X):
        return row[index]
    return next((m for m in row if abs(m.rect.x1 - CHANGE1_MARK_X[index]) <= CHANGE1_COL_TOL), None)


def change_stage1(self, index: int = 0, cancel: bool = False) -> bool:
    """换卡阶段 1：点第 index 张候选新卡（从左到右）→ 次へ，直到出现阶段 2。返回是否到了阶段 2。

    阶段 1 没有「キャンセル」：KAA 参考图 dialog_card_change_1.png 底部只有「カード除去 / 次へ / 再抽選」（授業的セレクトチェンジ
    选定档位后就进了换卡流程）。所以 cancel=True 时照常选候选进入阶段 2，由 change_stage2(cancel=True) 在阶段 2 点「キャンセル」。
    """
    from hiro.vision.ui_templates import find_enabled, tpl

    if cancel:
        logger.info("换卡阶段 1：决策不换，但阶段 1 没有取消键——照常进入阶段 2，在阶段 2 取消")
    deadline = time.monotonic() + CHANGE_STAGE_TIMEOUT
    while time.monotonic() < deadline:
        img = device.screenshot()
        if tpl("InProduce.DialogCardChange2.Title").exists(img):
            return True
        if (nxt := find_enabled("InProduce.DialogCardChange1.ButtonNext", img)) is not None:
            device.click(*nxt.center)
            logger.info("换卡阶段 1：已点「次へ」")
            _sleep(CHANGE_POLL)
            continue
        marks = find_card_marks(img, CHANGE_LETTERS_BOX, CHANGE_LETTER_THRESHOLD)
        target = _stage1_mark(marks, index) if marks else None
        if target is not None:
            _change_run().snap(img, "stage1")
            device.click(*target.center)
            logger.info(
                "换卡阶段 1：换入第 %d 张候选「%s」（找到 %d 个角标）", index, _mark_card_name(img, target), len(marks)
            )
        elif marks:
            logger.info("换卡阶段 1：第 %d 列的候选角标没找到（找到 %d 个），等下一帧", index, len(marks))
        else:
            logger.info("换卡阶段 1：候选卡还没出现，等入场动画")
        _sleep(CHANGE_POLL)
    logger.warning("换卡阶段 1：%.0fs 内没进入阶段 2，交回主循环", CHANGE_STAGE_TIMEOUT)
    return False


PROTECTED_PROVIDER: Callable[[], set[str]] | None = (
    None  # 策略层注入的保护名单（set_protected_provider）；None = 用 knowledge 的
)


def set_protected_provider(fn: Callable[[], set[str]] | None) -> None:
    """策略层注入「不能被换掉 / 删掉的卡」名单的来源（每次换卡时调用，返回卡 ID 集合）；传 None 恢复默认（hiro.state.knowledge）。"""
    global PROTECTED_PROVIDER
    PROTECTED_PROVIDER = fn


def protected_card_ids() -> set[str]:
    """不能被换掉 / 删掉的卡：注入的名单优先；默认取 knowledge 里的必拿、末段循环、S / A 级、记忆想带的卡（不在这里写死名单）。"""
    if PROTECTED_PROVIDER is not None:
        return set(PROTECTED_PROVIDER())
    from hiro.state.knowledge import protected_card_ids as from_knowledge

    return from_knowledge()  # 决策-07：名单由 knowledge 维护（含 protect 标记的 がむしゃら 与 CORE_CARDS）


def pick_change_target(cards: list[dict], index: int = 0, card_id: str | None = None, protected: set[str] | None = None,
                       remove_index: int | None = None, remove_upgrade: int | None = None):  # fmt: skip
    """阶段 2 卡格里选要换掉的卡。返回 (卡, 说明)；不该换 / 认不出时卡为 None。
    同一 card_id 有多张（强化次数不同）时按决策的 remove_index（read_card_dialog 的格子序号）/ remove_upgrade 选：
    第十九局 R1 牌组里 えいえいおー +1 / 0 / 0，决策要换第三张没强化的（index 10、upgrade 0），只按 card_id 点到了 +1 那张。"""
    protected = protected_card_ids() if protected is None else protected
    if card_id:
        same = [c for c in cards if c.get("card_id") == card_id]
        if not same:
            return None, f"卡格里没找到决策要换的 {card_id}"
        hit = next((c for c in same if remove_index is not None and c.get("index") == remove_index
                    and (remove_upgrade is None or c.get("upgrade") in (remove_upgrade, None))), None)  # fmt: skip
        if hit is not None:
            return hit, f"按决策 card_id + 格子 {remove_index}"
        if remove_upgrade is not None:
            hit = next((c for c in same if c.get("upgrade") == remove_upgrade), None)
            if hit is not None:
                return hit, f"按决策 card_id + 强化 {remove_upgrade}"
            # 强化次数认不准（None：「+」位置的红色像素在阈值之间）的那张就是候选：其余几张都明确认出了别的强化次数。
            # DMM dmm1m：4 张 ゆるふわおしゃべり 实为 +1 / +1 / +1 / 0，没强化那张卡面橙色描边在「+」区域里有零星红色，
            # 原图读成 None（存下的 JPEG 读成 0）；整副扫描过、不按格子号找时，以前只认强化恰好 0 的，取消后绕回阶段 1 十几分钟
            unsure = [c for c in same if c.get("upgrade") is None]
            if unsure:
                return unsure[0], f"按决策 card_id，强化 {remove_upgrade} 没有明确认出，取强化认不准的那张"
            if len(same) > 1:
                return None, f"{card_id} 有 {len(same)} 张，没有强化 {remove_upgrade} 的那张"
        return same[0], "按决策 card_id"
    known = [c for c in cards if c.get("card_id")]
    if not known:
        return None, "卡格一张都认不出"
    start = max(0, min(index, len(cards) - 1))
    for c in cards[start:] + cards[:start]:
        if c.get("card_id") and c["card_id"] not in protected:
            return c, "跳过保护卡后的第一张" if c is not cards[start] else "默认位置"
    return None, "可见的卡全是保护卡"


def scan_dialog_deck(region: tuple[int, int, int, int], *, expected: int | None = None, shoot=None, swipe=None,
                     sleep=_sleep, max_screens: int | None = None) -> dict:  # fmt: skip
    """选卡对话框（换卡阶段 2 / 删卡 / 强化）往下滑着读整副牌组，拼完滑回顶部。
    返回 {cards（整副，index = 牌组序号 deck_index）, complete, screens, problems}；一屏放得下时 screens=1、cards 就是这一屏。
    complete=False（拼接对不上 / 张数与记账差太多）时 cards 仍是拼出来的结果，调用方不要当整副用。"""
    from hiro.vision.deck import dialog_scroll, merge_dialog_screens, read_card_dialog  # noqa: F401

    shoot = shoot or device.screenshot
    swipe = swipe or (lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION))
    max_screens = max_screens or CARD_OP_MAX_SCREENS
    img, d0 = read_loaded(shoot, region, sleep)
    first = d0["cards"]
    if not first or not dialog_scroll(img, region)["scrollable"]:
        return {"cards": first, "complete": bool(first), "screens": 1, "problems": [] if first else ["卡格没读到"]}
    screens = [first]
    frames = [img]
    merged = merge_dialog_screens(screens, expected=expected)
    for _ in range(max_screens):
        _dialog_swipe(region, True, swipe)
        sleep(CARD_OP_SCROLL_SETTLE)
        im, d = read_loaded(shoot, region, sleep)
        frames.append(im)
        screens.append(d["cards"])
        merged = merge_dialog_screens(screens, expected=expected)
        if merged["new_rows"][-1] == 0:
            break
    if not merged["complete"]:
        _save_scan_frames(frames, merged["problems"])
    for _ in range(len(screens)):  # 滑回顶部（点卡时按内容找，不依赖精确位置）
        if dialog_scroll(shoot(), region).get("at_top"):
            break
        _dialog_swipe(region, False, swipe)
        sleep(CARD_OP_SCROLL_SETTLE)
    cards = [{**c, "index": c["deck_index"]} for c in merged["cards"]]
    return {"cards": cards, "complete": merged["complete"], "screens": len(screens), "problems": merged["problems"]}


def _save_scan_frames(frames, problems) -> None:
    """整副拼接不可信时把每一屏存下来（Windows 第三局：MuMu 上换卡阶段 2 每局都「没有重叠的行」，要离线复现）。只观察，失败不影响流程。"""
    try:
        from hiro.produce.ops import CALIB_DIR

        import cv2  # noqa: F401

        d = CALIB_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-牌组拼接"
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            imgio.imwrite(
                str(d / f"screen{i:02d}.png"), f
            )  # PNG：JPEG 压缩后卡图识别结果会变（实机认出的卡离线成了「?」），复现不了
        (d / "problems.txt").write_text("\n".join(map(str, problems)), encoding="utf-8")
        logger.info("牌组拼接不可信，%d 屏存到 %s", len(frames), d)
    except Exception as e:  # noqa: BLE001
        logger.warning("牌组拼接存帧失败：%s", e)


def _dialog_swipe(region, down: bool, swipe) -> None:
    """卡格内滑 CARD_OP_SCROLL_PX（down=True 往下看）。"""
    y = region[3] - 60 if down else region[1] + 60
    swipe(360, y, 360, y - CARD_OP_SCROLL_PX if down else y + CARD_OP_SCROLL_PX)


def _change_run():
    from hiro.contracts import Action, ActionKind
    from hiro.produce.ops import Run

    if getattr(_change_run, "run", None) is None or time.monotonic() - _change_run.t > 60:
        _change_run.run = Run(Action(ActionKind.CONTINUE, {"skill_card_change": True}), "SKILL_CARD_CHANGE", device.screenshot,
                              device.click, time.monotonic, _sleep, label="换卡", calib=True)  # fmt: skip
    _change_run.t = time.monotonic()
    return _change_run.run


def _mark_card_name(img, mark) -> str:
    """阶段 1 候选：角标在卡格左下，往上取卡面认名字（只用于日志）。"""
    from hiro import master
    from hiro.vision import card_db

    x, y = int(mark.rect.x1), int(mark.rect.y1)
    asset = card_db.match(
        img[max(0, y - 95) : y + 25, max(0, x - 5) : x + 115]
    )  # 卡格缩略图：卡面图库（卡面相关在这类小图上认不准）
    card = master.card_by_asset(asset) if asset else None
    return card.name if card else "?"


STAGE2_MAX_CANCELS = 2
_STAGE2_CANCELS: dict = {}  # (card_id, remove_upgrade) → 本进程里阶段 2 因找不到那张而取消的次数


LAST_CHANGED: dict | None = (
    None  # 最近一次阶段 2 实际换出的卡 {card_id, upgrade}（成功时写，开始时清空）：给记账取实际强化次数
)
CHANGE_GUESS_ART_MIN = (
    0.40  # 同 deck.GRID_AGREE_MIN：已知要找哪张卡时，卡图相关第一是它、分数够就当候选（还要详情栏卡名核对）
)


def guess_unread_target(img, cards: list[dict], card_id: str, remove_upgrade: int | None = None) -> dict | None:
    """阶段 2 要换的卡没在卡格里认出来时，在认不出的格子里按目标卡核对：卡图相关第一正是 card_id、分数 ≥ CHANGE_GUESS_ART_MIN，
    强化次数不冲突，且只有一格符合 → 返回这一格（guessed=True，点了以后要详情栏卡名确认才换）；否则 None。
    mainz3（11-18 03:47，DMM 559x993）：ひなたぼっこ 左侧两个角标盖住卡面，票数 2:2 并列、卡图 0.494 只领先 0.086，
    通用识别认不出；账本挑出它，执行侧找不到就点キャンセル，绕回换卡页死循环。不放宽通用门槛，只在知道找哪张时核对。"""
    from hiro import master
    from hiro.vision.deck import _plus, grid_art_scores

    hits = []
    for c in cards:
        if c.get("card_id") is not None or not c.get("rect"):
            continue
        x, y, w, h = c["rect"]
        crop = img[y : y + h, x : x + w]
        art = grid_art_scores(crop)
        if not art or art[0][1] < CHANGE_GUESS_ART_MIN:
            continue
        card = master.card_by_asset(art[0][0])
        if card is None or card._id != card_id:
            continue
        up = _plus(crop)
        if remove_upgrade is not None and up is not None and up != remove_upgrade:
            continue
        hits.append(
            {**c, "card_id": card_id, "name": card.name, "upgrade": up, "guessed": True, "art": round(art[0][1], 3)}
        )
    return hits[0] if len(hits) == 1 else None


def change_stage2(self, index: int = 0, card_id: str | None = None, cancel: bool = False, remove_index: int | None = None,
                  remove_upgrade: int | None = None) -> bool:  # fmt: skip
    """换卡阶段 2：选被换掉的原卡 → チェンジ → 确认。认不出 / 只剩保护卡时点キャンセル，返回 False；画面不在阶段 2 也返回 False。

    阶段 2 点「キャンセル」之后去哪：没有实机帧，[推断] 回到阶段 1（换入候选的选择页）。理由：阶段 1 没有取消键，セレクトチェンジ
    是选定授業档位时就决定的，游戏不会让这次换卡作废；KAA 参考图 dialog_card_change_2 的「キャンセル」与相談删卡页同款，
    相談里它只退回上一层。所以 cancel=True 可能让主循环在「阶段 1 → 阶段 2 取消」之间打转：调用方需要限次，超过后改为按决策
    换一张非保护卡（或停下报警）。calib 帧 stage2-cancel 会记录取消后的第一帧，用来确认。
    """
    from hiro.vision.deck import read_card_dialog
    from hiro.vision.ui_templates import tap, tpl

    global LAST_CHANGED
    LAST_CHANGED = None
    if not _poll(lambda img: tpl("InProduce.DialogCardChange2.Title").exists(img), CHANGE_STAGE_TIMEOUT):
        logger.warning("换卡阶段 2：画面不在阶段 2（阶段 1 未完成？），交回主循环")
        return False
    run = _change_run()
    deadline = time.monotonic() + CHANGE_STAGE_TIMEOUT
    dialog = {"cards": []}
    while time.monotonic() < deadline:
        img = device.screenshot()
        dialog = read_card_dialog(img, CHANGE2_CARD_AREA)
        if dialog["cards"]:
            break
        _sleep(CHANGE_POLL)
    run.snap(img, "stage2-deck")
    target, why = (None, "决策判定不换") if cancel else pick_change_target(dialog["cards"], index, card_id, remove_index=remove_index,
                                                                         remove_upgrade=remove_upgrade)  # fmt: skip
    key = (card_id, remove_upgrade)
    if target is None and not cancel and card_id and _STAGE2_CANCELS.get(key, 0) >= STAGE2_MAX_CANCELS:
        # 阶段 1 没有取消键，阶段 2 取消只会回到阶段 1 再选同一张：同一张卡已取消 STAGE2_MAX_CANCELS 次，
        # 改换这张卡强化次数最低的那份（DMM 版 2026-11-10 dmm1m：4 张 ゆるふわおしゃべり 都认成已强化，转了十几分钟）
        same = [c for c in dialog["cards"] if c.get("card_id") == card_id]
        if same:
            # 认不准（None）排在明确 0 之后、明确 +1 之前：认不准的多半是没强化的（「+」区域只有零星红色），别换掉强化过的
            target = min(same, key=lambda c: 0.5 if c.get("upgrade") is None else c["upgrade"])
            why = f"{why}；已取消 {_STAGE2_CANCELS[key]} 次，改换强化最低的那张（{target.get('upgrade')}）"
    if target is None and not cancel and card_id and not any(c.get("card_id") == card_id for c in dialog["cards"]):
        # 牌组一屏放不下、目标不在这一屏（决策按整副选的）：往下、再往上翻屏找同 card_id + 强化次数的那张
        from hiro.vision.deck import dialog_scroll

        if dialog_scroll(img, CHANGE2_CARD_AREA)["scrollable"]:
            swipe = lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION)  # noqa: E731
            for down in (True, False):
                seen = None
                for _ in range(CARD_OP_MAX_SCREENS):
                    _dialog_swipe(CHANGE2_CARD_AREA, down, swipe)
                    _sleep(CARD_OP_SCROLL_SETTLE)
                    img = device.screenshot()
                    dialog = read_card_dialog(img, CHANGE2_CARD_AREA)
                    target, why = pick_change_target(dialog["cards"], index, card_id, remove_upgrade=remove_upgrade)
                    if target is not None or any(c.get("card_id") == card_id for c in dialog["cards"]):
                        break
                    ids = [(c.get("card_id"), c.get("upgrade")) for c in dialog["cards"]]
                    if ids == seen or (not down and dialog_scroll(img, CHANGE2_CARD_AREA).get("at_top")):
                        break  # 到底（画面没变）/ 到顶
                    seen = ids
                if target is not None:
                    why += "（翻屏找到）"
                    break
            run.snap(img, "stage2-scrolled")
    if target is None and not cancel and card_id:
        guess = guess_unread_target(img, dialog["cards"], card_id, remove_upgrade)
        if guess is not None:
            target, why = (
                guess,
                f"{why}；认不出的格子 {guess['index']} 卡图最像它（{guess['art']}），点后按详情栏卡名核对",
            )
    if target is None:
        if card_id and not cancel:
            _STAGE2_CANCELS[key] = _STAGE2_CANCELS.get(key, 0) + 1
        logger.warning(
            "换卡阶段 2：%s，不换，点「キャンセル」（卡格 %s）", why, [c.get("name") for c in dialog["cards"]]
        )
        device.click(*CHANGE_CANCEL_TAP)
        _sleep(CHANGE_POLL)
        run.snap(device.screenshot(), "stage2-cancel")
        return False
    device.click(*target["center"])
    _sleep(CHANGE_POLL)
    from hiro.vision.deck import CHANGE2_DETAIL_BOX

    picked = read_card_dialog(device.screenshot(), CHANGE2_CARD_AREA, expect=target["card_id"] if target.get("guessed") else None,
                              detail_box=CHANGE2_DETAIL_BOX)  # fmt: skip
    run.snap(device.screenshot(), "stage2-picked")
    sel = picked.get("selected_card") or {}
    chosen, chosen_up = sel.get("card_id"), sel.get("upgrade")
    if target.get("guessed") and chosen != target["card_id"]:
        logger.warning(
            "换卡阶段 2：猜的格子 %s 详情栏读到 %s，不是要换的 %s，点「キャンセル」",
            target["index"],
            chosen,
            target["card_id"],
        )
        device.click(*CHANGE_CANCEL_TAP)
        return False
    if chosen and (
        chosen != target["card_id"]
        or (target.get("upgrade") is not None and chosen_up is not None and chosen_up != target["upgrade"])
    ):
        logger.warning("换卡阶段 2：选中的是 %s（强化 %s），不是要换的 %s（强化 %s），点「キャンセル」", chosen, chosen_up, target["card_id"],
                       target.get("upgrade"))  # fmt: skip
        device.click(*CHANGE_CANCEL_TAP)
        return False
    logger.info(
        "换卡阶段 2：换出「%s」（%s，强化 %s，%s）", target.get("name"), target["card_id"], target.get("upgrade"), why
    )
    deadline = time.monotonic() + CHANGE_STAGE_TIMEOUT
    gone = 0
    while time.monotonic() < deadline:
        _sleep(CHANGE_POLL)
        img = device.screenshot()
        if confirm_change_dialog(img, run):
            gone = 0
        elif tap("InProduce.DialogCardChange2.ButtonConfirm", img, device.click):
            logger.info("换卡阶段 2：已点「チェンジ」")
            gone = 0
        elif tpl("InProduce.DialogCardChange2.Title").exists(img):
            gone = 0  # 还在阶段 2、确认框还没弹出来：接着等（第二十局 03:21：这里曾被当成完成，确认框晚弹出没人点）
        else:
            gone += 1
            if gone >= 2:  # 连续两帧阶段 2 标题和确认框都不在：换完了
                run.snap(img, "stage2-done")
                LAST_CHANGED = {"card_id": target["card_id"], "upgrade": target.get("upgrade")}
                return True
    logger.warning("换卡阶段 2：%.0fs 内确认框没有关闭", CHANGE_STAGE_TIMEOUT)
    return False


CHANGE_CONFIRM_TITLE = ("チェンジ確認",)  # 「スキルカードチェンジ確認」（第二十局 calib 20261103-032052/05）
CHANGE_CONFIRM_OK = ("決定", "はい", "OK")
CHANGE_CONFIRM_TAP = (520, 1160)  # 确认框右下「決定」


def confirm_change_dialog(img, run=None) -> bool:
    """画面上是换卡确认框（「スキルカードチェンジ確認」）就点「決定」，返回是否点了。先试 KAA 模板，认不出再用橙色标题条 + 局部 OCR。
    换出哪张在点「チェンジ」之前已按决策选好，确认框只是确认这一步。"""
    from hiro.vision.ui_templates import tpl

    shot = img if img is not None else device.screenshot()
    if tpl("InProduce.DialogCardChangeConfirm.TItle").exists(shot):
        if run is not None:
            run.snap(shot, "stage2-confirm")
        btn = tpl("InProduce.DialogCardChangeConfirm.ButtonConfirm").find(shot)
        if btn:
            device.click(*btn.center)
            logger.info("换卡阶段 2：已确认")
            return True
    if img is None:
        return False
    try:
        from hiro.produce.ops import find_text, ocr_boxes
        from hiro.vision.pages import dialog_header

        band = dialog_header(img)
        if band is None:
            return False
        y0 = max(0, band[0] - 10)
        boxes = [(t, c, (x1, y1 + y0, x2, y2 + y0)) for t, c, (x1, y1, x2, y2) in ocr_boxes(img[y0:1240])]
        if find_text(boxes, CHANGE_CONFIRM_TITLE) is None:
            return False
        if run is not None:
            run.snap(img, "stage2-confirm-ocr")
        btn = find_text(boxes, CHANGE_CONFIRM_OK, region=(300, 1080, 720, 1240))
        device.click(*(btn or CHANGE_CONFIRM_TAP))
        logger.info("换卡阶段 2：确认框（OCR）点「決定」%s", btn or CHANGE_CONFIRM_TAP)
        return True
    except Exception:
        logger.debug("换卡确认框 OCR 判断失败", exc_info=True)
        return False


SKIP_CONFIRM = ("選択せず次へ",)  # 强化 / 删卡页点「キャンセル」后的「スキップ確認：選択せずスキップしますか？」
SKIP_CONFIRM_WAIT = 3.0
RETAP_HINT = ("再タップで選択", "再タップ")  # 这种强化页第一下点卡只是预览，卡上出现这几个字，要再点一下才算选中


def confirm_skip_after_cancel(*, shoot=None, click=None, sleep=_sleep, clock=time.monotonic, ocr=None) -> bool:
    """点了强化 / 删卡页的「キャンセル」之后：游戏弹「スキップ確認：選択せずスキップしますか？」（対象選択画面に戻る /
    選択せず次へ）时点「選択せず次へ」（= 不选、继续，正是取消的本意）。11-24 mao4：认不出这个框，回到选卡页再来，
    同一页转了 25 分钟。没弹框（相談的删卡页取消就是回相談）返回 False。"""
    from hiro.produce.ops import find_text

    shoot = shoot or device.screenshot
    click = click or (lambda x, y: device.click(x, y))
    ocr = ocr or (lambda im: [(t.text, 1.0, tuple(int(v) for v in t.rect)) for t in screen_texts(im)])
    end = clock() + SKIP_CONFIRM_WAIT
    while clock() < end:
        sleep(0.5)
        b = find_text(ocr(shoot()), SKIP_CONFIRM)
        if b is not None:
            click(*b)
            logger.info("取消后弹出「スキップ確認」：点「選択せず次へ」（不选，继续）")
            return True
    return False


def _card_op(
    kind: str,
    card_id: str | None,
    *,
    shoot=None,
    click=None,
    clock=time.monotonic,
    sleep=_sleep,
    ocr=None,
    calib: bool = True,
    choose=None,
    swipe=None,
    expected_size: int | None = None,
) -> bool:
    """全屏选卡页（删卡 / 强化）：read_card_dialog 定位 card_id → 点卡 → 核对选中 → 点「削除 / 強化」→（确认框）→ 页面关闭。

    取消键：只有相談里点「削除」进的删卡页有「キャンセル」（数据集 20261029-224103 第 141–148 帧）；
    事件 / 授業给的删卡页（KAA screenshot_remove_skill_card.png）和强化页（screenshot_skill_card_enhance_dialog*.png）
    底部只有一个按钮，没有取消。card_id 为 None、找不到、或点了没选中时：有取消键就点取消，没有就不点任何卡，返回 False。
    choose(cards) -> card_id | None：card_id 没给时，读出卡格后交给它（决策 enhance_card / remove_card）选。
    卡格区按版式定：上方有两格「スキルカードが選択されていません」= 强化版式（卡格 y≥690），一格 = 删卡版式（y≥380）——
    相談的强化页没有样本，靠这个判断自适应。
    牌组一屏放不下（右侧有滚动条，强化对话框只露约 3 行）时：先往下滑着把整副读完（deck.merge_dialog_screens 拼接，
    某屏没有新行 = 到底），整副交给 choose；再往回滑到含目标卡的那一屏点它。swipe(x1, y1, x2, y2) 可注入。
    """
    from hiro.contracts import Action, ActionKind
    from hiro.produce import shop
    from hiro.produce.ops import Run, find_text
    from hiro.vision.deck import read_card_dialog

    label = CARD_OP_LABEL[kind]
    run = Run(Action(ActionKind.CONTINUE, {f"card_{kind}": card_id}), f"SKILL_CARD_{'REMOVAL' if kind == 'remove' else 'ENHANCE'}",
              shoot or device.screenshot, click or (lambda x, y: device.click(x, y)), clock, sleep, label=label, ocr=ocr, calib=calib)  # fmt: skip
    region = CARD_OP_REGION[kind]
    got = {}

    def has_cards(im):
        nonlocal region
        empty_boxes = sum("選択されていません" in t for t, _, b in run.ocr(im) if (b[1] + b[3]) // 2 < 690)
        if empty_boxes:
            region = CARD_OP_REGION["enhance" if empty_boxes >= 2 else "remove"]
        got["d"] = read_card_dialog(im, region)
        return bool(got["d"]["cards"])

    ok, img = run.until(has_cards, CARD_OP_TIMEOUT)
    if ok:
        img, got["d"] = read_loaded(run.shoot, region, sleep, img)  # 卡图还在加载的格子等它出来
    run.snap(img, "deck")
    cards = got.get("d", {}).get("cards") or []
    can_cancel = shop.cancel_visible(img)

    def give_up(why: str) -> bool:
        if can_cancel:
            run.tap(find_text(run.ocr(img), ("キャンセル",), region=(0, 1100, 360, 1220)) or shop.CANCEL_TAP)
            run.snap(run.shoot(), "cancel")
            logger.warning("%s：%s，点「キャンセル」", label, why)
            confirm_skip_after_cancel(shoot=run.shoot, click=run.tap, sleep=sleep, clock=clock, ocr=run.ocr)
        else:
            logger.warning("%s：%s；这一页没有取消键，不点任何卡，交回主循环", label, why)
        return False

    if not ok:
        return give_up(f"{CARD_OP_TIMEOUT:.0f}s 内卡格没出现")
    from hiro.vision.deck import dialog_scroll, merge_dialog_screens

    swipe = swipe or (lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION))
    scrollable = dialog_scroll(img, region)["scrollable"]

    def scroll(up: bool):
        """卡格内滑约 2 行（up=True 往下看），等停稳后读这一屏。"""
        y = region[3] - 60 if up else region[1] + 60
        swipe(360, y, 360, y - CARD_OP_SCROLL_PX if up else y + CARD_OP_SCROLL_PX)
        sleep(CARD_OP_SCROLL_SETTLE)
        im, d = read_loaded(run.shoot, region, sleep)
        return im, d["cards"]

    deck_cards = cards
    need_deck = not card_id and choose is not None  # 决策要在整副牌里挑：先读完整副，拼接可信才交给它
    if scrollable and need_deck:
        screens = [cards]
        for _ in range(CARD_OP_MAX_SCREENS):
            im, more = scroll(True)
            screens.append(more)
            merged = merge_dialog_screens(screens, expected=expected_size)
            if merged["new_rows"][-1] == 0:
                break
        run.snap(im, "deck-bottom")
        deck_cards = merged["cards"]
        if merged["problems"]:
            logger.warning("%s：牌组拼接有问题 %s", label, merged["problems"])
        if not merged["complete"]:
            return give_up(
                f"牌组拼接不可信（{len(deck_cards)} 张，{merged['problems']}），不交决策"
            )  # 第十四局拼成 36 张
        logger.info("%s：牌组 %d 屏共 %d 张", label, len(screens), len(deck_cards))
    if not card_id and choose is not None:
        card_id = choose(deck_cards)
        run.action.args[f"card_{kind}"] = card_id
    if not card_id:
        return give_up("决策没有给 card_id")
    want = next((c.get("upgrade") for c in deck_cards if c.get("card_id") == card_id), None)

    def find(cs):
        return next(
            (c for c in cs if c.get("card_id") == card_id and (want is None or c.get("upgrade") in (want, None))), None
        )

    if not scrollable:
        target = find(cards)
    elif need_deck:
        # 已滑到底：从当前屏往回滑找目标所在的那一屏
        screen_cards = read_card_dialog(run.shoot(), region)["cards"]
        target = find(screen_cards)
        for _ in range(CARD_OP_MAX_SCREENS if target is None else 0):
            _, screen_cards = scroll(False)
            target = find(screen_cards)
            if target is not None:
                break
    else:
        # 决策已给 card_id（轮间 / 相談强化）：不需要整副牌，在当前屏找，找不到再一屏屏往下找，到底（没有新行）还没有再往回找。
        # 第十六、十七局本战轮间：目标就在第一屏，却因为整副拼接「滑过了头」被判不可信、直接取消，强化没做成
        target, seen = find(cards), [cards]
        if target is None and not dialog_scroll(img, region).get("at_top", True):
            # 不在顶部（前面整副扫描没滑回顶 / 回弹）：先往上找到顶，再往下（第二十三局目标在第 1 行，却先往下滑满 10 屏）
            for _ in range(CARD_OP_MAX_SCREENS):
                im, more = scroll(False)
                target, seen = find(more), [more]  # 往下找时从这一屏开始拼
                if target is not None or dialog_scroll(im, region).get("at_top", True):
                    break
        for _ in range(CARD_OP_MAX_SCREENS if target is None else 0):
            _, more = scroll(True)
            seen.append(more)
            target = find(more)
            if target is not None or merge_dialog_screens(seen)["new_rows"][-1] == 0:
                break
        for _ in range(CARD_OP_MAX_SCREENS if target is None else 0):
            _, more = scroll(False)
            target = find(more)
            if target is not None:
                break
    if target is None:
        return give_up(f"卡格里没找到 {card_id}（牌组 {[c.get('name') for c in deck_cards]}）")
    run.tap(target["center"])
    sleep(CARD_OP_PICK_SETTLE)
    if find_text(run.ocr(run.shoot()), RETAP_HINT) is not None:
        # 「再タップで選択」：第一下只是预览（11-24 mao4 强化 0/2 页：没再点，按钮一直 0/2，点了不动，判「页面没关」）
        logger.info("%s：卡上是「再タップで選択」，再点一下「%s」选中", label, target.get("name"))
        run.tap(target["center"])

    def picked(im):
        d = read_card_dialog(im, region)
        got["sel"] = d
        sel = (d.get("selected_card") or {}).get("card_id")
        return sel == card_id or (
            sel is None and d.get("selected") is not None and d["cards"][d["selected"]].get("card_id") == card_id
        )

    sleep(CARD_OP_PICK_SETTLE)
    ok, img = run.until(picked, CARD_OP_TIMEOUT, min_checks=CARD_OP_PICK_CHECKS)
    if not ok:
        # 还是没选中目标：在当前画面里重新找目标再点一次（选中别的卡不要紧，这一页再点就换选中，不会执行）
        again = find(read_card_dialog(img, region)["cards"])
        if again is not None:
            logger.info("%s：点了「%s」核对没过，再点一次", label, target.get("name"))
            run.tap(again["center"])
            sleep(CARD_OP_PICK_SETTLE)
            ok, img = run.until(picked, CARD_OP_TIMEOUT, min_checks=CARD_OP_PICK_CHECKS)
    run.snap(img, "picked")
    if not ok:
        sel = (got.get("sel", {}).get("selected_card") or {}).get("name")
        return give_up(f"点了「{target.get('name')}」但选中的是 {sel}")
    btn = find_text(run.ocr(img), CARD_OP_BUTTON[kind], region=(240 if can_cancel else 0, 1100, 720, 1220), exact=True)
    run.tap(btn or (CARD_OP_BUTTON_TAP_WITH_CANCEL if can_cancel else CARD_OP_BUTTON_TAP[kind]))
    last = [run.clock() - 1.0]

    def done(im):
        boxes = run.ocr(im)
        if find_text(boxes, CARD_OP_DONE[kind]) is not None:
            return True
        if run.clock() - last[0] >= 1.0 and (
            b := find_text(boxes, CARD_OP_CONFIRM[kind], region=(360, 1080, 720, 1240))
        ):
            run.snap(im, "confirm")
            run.tap(b)
            last[0] = run.clock()
            return False
        return not read_card_dialog(im, region)["cards"] and not shop.cancel_visible(im)

    ok, img = run.until(done, CARD_OP_DONE_TIMEOUT)
    run.snap(img, "done" if ok else "not-done")
    if not ok:
        logger.warning("%s「%s」：点了按钮后 %.0fs 页面没关", label, target.get("name"), CARD_OP_DONE_TIMEOUT)
        return False
    logger.info("%s「%s」（%s，按决策 card_id）", label, target.get("name"), card_id)
    return True


def remove_card(ctx, card_id: str | None, **kw) -> bool:
    """删卡页（SKILL_CARD_REMOVAL）按决策删 card_id。取消键与失败处理见 _card_op。"""
    return _card_op("remove", card_id, **kw)


def enhance_card(ctx, card_id: str | None, **kw) -> bool:
    """强化页（SKILL_CARD_ENHANCE）按决策强化 card_id。强化页没有取消键：读不出就不点、返回 False。"""
    return _card_op("enhance", card_id, **kw)


def _button_sv(img) -> tuple[float, float]:
    from hiro.produce.ops import button_sv

    return button_sv(img, PITEM_ACQUIRE_BTN)


def acquire_lit(img, base_val: float | None = None) -> bool:
    """「受け取る」是否已变亮（选中了某个包）。阈值见 ops.orange_lit。"""
    from hiro.produce.ops import orange_lit

    return orange_lit(img, PITEM_ACQUIRE_BTN, base_val)


def pitem_commit(self, index: int, *, shoot=None, click=None, clock=time.monotonic, sleep=_sleep) -> bool:
    """H.I.F P 道具三选一：点第 index 个背包 → 等「受け取る」变亮 → 点它 → 等按钮消失。成功返回 True，点不动返回 False。"""
    from hiro.produce.ops import wait_change

    if not 0 <= index < len(PITEM_CENTERS):
        raise ValueError(f"Invalid PItem index: {index}")
    shoot = shoot or device.screenshot
    click = click or device.click
    kw = {"shoot": shoot, "clock": clock, "sleep": sleep, "use_signature": False}
    x, y = PITEM_CENTERS[index]
    img = shoot()
    base_val = _button_sv(img)[1]
    for attempt in range(PITEM_TRIES):
        if not acquire_lit(img, base_val):
            click(x, y)
            w = wait_change(img, done=lambda im: acquire_lit(im, base_val), timeout=PITEM_LIT_TIMEOUT, **kw)
            img = w.img
            if not w.changed:
                logger.info(
                    "P 道具：点第 %d 个背包 (%d, %d) 后 %.1fs「受け取る」没变亮（第 %d 次）",
                    index,
                    x,
                    y,
                    w.waited_s,
                    attempt + 1,
                )
                continue
        bx, by, bw, bh = PITEM_ACQUIRE_BTN
        click(bx + bw // 2, by + bh // 2)
        w = wait_change(img, roi=PITEM_ACQUIRE_BTN, timeout=PITEM_ACQUIRE_TIMEOUT, **kw)  # 按钮区变了 = 按钮消失 / 换页
        img = w.img
        if w.changed:
            logger.info("P 道具：已拿第 %d 个背包（%.1fs）", index, w.waited_s)
            return True
        logger.info("P 道具：点「受け取る」后 %.1fs 按钮没消失（第 %d 次）", w.waited_s, attempt + 1)
    logger.warning("P 道具：%d 次都没拿到第 %d 个背包，交回主循环", PITEM_TRIES, index)
    return False


# ---------------------------------------------------------------- 选饮料（差し入れ / 活動支給 等三选一，替换 KAA DrinkSelectContext）
DRINK_SELECT_BOXES = (
    (157, 820, 285, 948),
    (296, 820, 424, 948),
    (435, 820, 563, 948),
)  # 三瓶的位置（同 KAA POSTIONS，x1 y1 x2 y2）
DRINK_SELECT_MAX_TRIES = 8  # KAA 的提交循环没有上限；这里点这么多轮还是灰的「受け取る」就交回主循环


def drink_select_can_skip(img) -> bool:
    """有没有「受け取らない」（可以不拿）。同 KAA fetch_select_drink 的 can_skip。"""
    from hiro.vision.ui_templates import tpl

    return tpl("InProduce.TextDontClaim").exists(img)


def drink_select_commit(index: int | None, *, sleep=None) -> bool:
    """选第 index 瓶并「受け取る」；index=None 不拿（点「受け取らない」再点按钮）。同 KAA DrinkSelectContext.commit。返回是否做完。"""
    from hiro.vision.ocr import Box
    from hiro.vision.ui_templates import tpl

    sleep = sleep or _sleep
    if index is None:
        img = device.screenshot()
        skip = tpl("InProduce.TextDontClaim").find(img)
        if skip is None:
            raise ValueError("不能不拿：没有「受け取らない」")
        device.click(*skip.center)
        sleep(0.5)
        img = device.screenshot()
        btn = tpl("InProduce.ButtonDontClaim").find(img) or tpl("InProduce.AcquireBtnDisabled").find(img)
        if btn is not None:
            device.click(*btn.center)
        logger.debug("选饮料：不拿")
        return True
    if not 0 <= index < len(DRINK_SELECT_BOXES):
        raise ValueError(f"饮料下标 {index} 不对")
    target = Box(*DRINK_SELECT_BOXES[index])
    for _ in range(DRINK_SELECT_MAX_TRIES):
        device.click(*target.center)
        btn = tpl("InProduce.AcquireBtnDisabled").find(device.screenshot())
        if btn is None:
            return True
        sleep(0.5)
        device.click(*btn.center)
        sleep(2)
    logger.warning("选饮料：点第 %d 瓶 %d 轮「受け取る」还是灰的，交回主循环", index, DRINK_SELECT_MAX_TRIES)
    return False


# ---------------------------------------------------------------- 三选一选卡的提交（替换 KAA CardSelectContext.commit）
CARD_SELECT_MAX_TRIES = 8  # KAA 的循环没有上限


def card_select_commit(rect, *, sleep=None) -> bool:
    """点卡（字母框中心），「受け取る」还灰就点它再等 2 秒，直到按钮不再是灰的（同 KAA CardSelectContext.commit）。返回是否做完。"""
    from hiro.vision.ui_templates import tpl

    sleep = sleep or _sleep
    for _ in range(CARD_SELECT_MAX_TRIES):
        device.click(*rect.center)
        btn = tpl("InProduce.AcquireBtnDisabled").find(device.screenshot())
        if btn is None:
            return True
        sleep(0.5)
        device.click(*btn.center)
        sleep(2)
    logger.warning("选卡：点卡 %d 轮「受け取る」还是灰的，交回主循环", CARD_SELECT_MAX_TRIES)
    return False
