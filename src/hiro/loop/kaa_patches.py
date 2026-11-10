# 来源：digital-hiro hiro/loop/kaa_patches.py（c3fc27f）
# KAA 派生代码：对 kotones-auto-assistant（GPL-3.0）流程的运行时补丁，部分逻辑改写自 KAA 源码，按 GPL-3.0 发布
"""对 KAA 流程的运行时补丁（不改 vendor）。

- locate_idol：KAA 靠滚动条翻偶像总览，本机 PlayCover 9:16 下滚动条滑块检测失败（thumb height 1），只看第一页就报
  IdolCardNotFoundError。这里改为在偶像区域内上滑翻页，每页做同样的图像匹配。
- H.I.F 授業换卡（SkillCardChangeContext，先选新卡、再选被换掉的原卡）：KAA 阶段 1 只在 20px 高的
  BoxCardLetters（y 943–963）里找卡角标 A/M，画面差 8px 或还在入场动画就找不到，记一条错误后直接返回；
  hif_grind 随后无条件调用阶段 2，在阶段 1 的画面上找不到卡，抛 UnrecoverableError（2026-10-31 实机 r5）。
  这里放宽搜索区域、找不到就等动画重试；阶段 2 先确认画面已到阶段 2，找不到卡也不抛，交回主循环重新认场景。
  阶段 2（换掉牌组里哪张）不再盲点第 0 张：第四局本战两次授業セレクトチェンジ 换掉了 デイドリーミング、がむしゃら
  （reports/analysis/q4-daydreaming-missing-exec.md；牌组页按卡 id 排序，act-1 的卡排在最前）。现在用识别
  read_card_dialog 读卡格：给了 card_id 就换那张；没给就从 index 起找第一张认得出、且不在保护名单（hiro.state.knowledge
  的必拿 / 循环 / S·A 级 / 记忆想带的卡）里的卡；认不出或全是保护卡就点「キャンセル」不换。两个阶段都存 calib 帧、
  日志写明换出 / 换入了哪张。
- H.I.F P 道具背包三选一（PItemSelectContext）：KAA 写死的 PITEM_POSTIONS 是初模式中間試験的位置，H.I.F 背包间距更宽，
  左右两个点压在包的边缘（2026-10-31 r2 实机帧）。KAA commit 还在 Loop() 里无限重试。这里按 H.I.F 实测中心点包，
  等「受け取る」变亮再点，等待按时间封顶，点不动返回 False 交回主循环。
"""

import importlib
import time
from collections.abc import Callable

from kotonebot import device, logging
from kotonebot.primitives import Rect
from hiro import imgio

logger = logging.getLogger(__name__)

MAX_PAGES = 20
TOP_SWIPES = 8
SWIPE_PX = 220  # 约 1 行，页间有重叠
SETTLE = 1.2
COLS = (30, 200, 371, 541)  # 720x1280 下四列卡片的左边缘
CARD_W, CARD_H = 149, 197
MIN_H = 120  # 裁剪高度不足时不查（被底部遮住的行）
ROW_STEP = 16
MATCH_THRESHOLD = 12  # 实测命中距离 2–8，误匹配 ≥ 10

# 授業换卡
CHANGE_LETTERS_BOX = Rect(40, 860, 640, 160)  # 候选卡行的角标带；KAA 参考图上下平移 ±30px 都能找到 3 张
CHANGE_STAGE_TIMEOUT = 15.0  # 单个阶段最多等多久（含入场动画）
CHANGE_POLL = 0.5
CHANGE_LETTER_THRESHOLD = 0.65  # 与 KAA 阶段 1 相同
CHANGE2_CARD_AREA = (66, 623, 641, 1106)  # KAA DialogCardChange2.BoxCardArea（x1, y1, x2, y2）
CHANGE_CANCEL_TAP = (211, 1160)  # 阶段 2 左下「キャンセル」（KAA 参考图 dialog_card_change_2.png）
CHANGE1_MARK_X = (213, 352, 491)  # 阶段 1 三张候选的角标 x（参考图实测，列距 139）
CHANGE1_COL_TOL = 40

# 删卡 / 强化页（全屏选卡）
CARD_OP_REGION = {"remove": (0, 380, 720, 1110), "enhance": (0, 690, 720, 1115)}  # 同识别 deck.REMOVAL_GRID / ENHANCE_GRID  # 强化页上方两格详情栏里也有卡面，卡格从 y≈700 起
CARD_OP_BUTTON = {"remove": ("削除",), "enhance": ("強化",)}
CARD_OP_BUTTON_TAP = {"remove": (360, 1158), "enhance": (364, 1160)}  # 没有取消键时底部居中的唯一按钮（参考图）
CARD_OP_BUTTON_TAP_WITH_CANCEL = (520, 1161)  # 相談删卡页：左「キャンセル」右「削除」
CARD_OP_CONFIRM = {"remove": ("削除する", "削除", "決定", "はい", "OK"), "enhance": ("強化する", "強化", "決定", "はい", "OK")}  # 相談删卡确认框的按钮就叫「削除」
CARD_OP_DONE = {"remove": ("削除しました",), "enhance": ("強化しました",)}
CARD_OP_LABEL = {"remove": "删除", "enhance": "强化"}
CARD_OP_TIMEOUT = 5.0  # 等卡格出现 / 选中
CARD_OP_SCROLL_PX = 160  # 卡格内一次滑约 1 行多（行距 147）：露 3 行时相邻两屏重叠 2 行。
# 以前滑 294（2 行），第十六、十七局轮间强化都滑过了头，「第 2 屏和上一屏没有重叠的行」→ 拼接不可信
CARD_OP_SWIPE_DURATION, CARD_OP_SCROLL_SETTLE, CARD_OP_MAX_SCREENS = 1.0, 0.8, 10
CARD_OP_DONE_TIMEOUT = 10.0
# 点卡后先等选中框 / 上方详情切过去再核对，且至少看 3 帧（第二十三局录像时读一次卡格数秒：5 秒里只看了点完立刻截的一帧，
# 读到的总是上一张选中的卡，连续 3 次「选中不符」停机——画面上其实已经选中了目标）
CARD_OP_PICK_SETTLE, CARD_OP_PICK_CHECKS = 0.6, 3

# H.I.F P 道具背包三选一
PITEM_CENTERS = ((125, 870), (341, 870), (557, 870))  # 720x1280；r2 实机帧按红 / 黄 / 绿内框测得（框宽 146），误差约 ±3px
PITEM_ACQUIRE_BTN = (240, 1045, 240, 70)  # 「受け取る」按钮区（x, y, w, h）
PITEM_LIT_TIMEOUT = 4.0  # 点包后等按钮变亮
PITEM_ACQUIRE_TIMEOUT = 5.0  # 点「受け取る」后等按钮消失
PITEM_TRIES = 2


def _progress(why: str) -> None:
    from hiro.loop.repeat_guard import progress

    progress(why)


def locate_idol_by_swipe(skin_id: str) -> Rect | None:
    """固定 4 列、滑动行的网格裁剪逐格查偶像图像库（KAA 的 extract_idols 轮廓检测会漏掉部分卡位，2026-10-31 实测漏掉 Campus 広）。
    先回到列表顶部，再逐页上滑，到底（画面不再变化）为止。"""
    from kaa.game_ui.idols_overview import idols_db
    from kaa.tasks import R

    x0, y0, w, h = R.Produce.BoxIdolOverviewIdols.xywh
    cx = x0 + w // 2
    bottom = y0 + h
    db = idols_db()
    for _ in range(TOP_SWIPES):  # 回到顶部
        device.swipe(cx, y0 + 60, cx, bottom - 40, duration=0.4)
        time.sleep(0.5)
    time.sleep(0.8)
    last_sig = None
    for page in range(MAX_PAGES):
        img = device.screenshot()
        for x in COLS:
            for y in range(y0 - 8, bottom - MIN_H, ROW_STEP):
                crop = img[y : min(y + CARD_H, bottom), x : x + CARD_W]
                if crop.shape[0] < MIN_H:
                    continue
                res = db.query(crop, k=1, threshold=MATCH_THRESHOLD)
                if res and res[0].key.startswith(skin_id):
                    logger.info("Found idol %s on page %d at (%d, %d), distance %.1f", skin_id, page + 1, x, y, res[0].distance)
                    return Rect(x, y, CARD_W, crop.shape[0])
        sig = img[y0:bottom:8, x0 : x0 + w : 8].tobytes()
        if sig == last_sig:
            logger.info("偶像列表翻到底仍未找到 %s", skin_id)
            return None
        last_sig = sig
        _progress(f"找偶像：第 {page + 1} 页没有")
        device.swipe(cx, bottom - 40, cx, bottom - 40 - SWIPE_PX, duration=0.6)
        time.sleep(SETTLE)
    logger.info("翻了 %d 页仍未找到 %s", MAX_PAGES, skin_id)
    return None


def find_card_marks(region, threshold: float | None = None) -> list:
    """在 region 内找技能卡角标 A / M（读最近一次截图），按行（40px 内算同一行）再按 x 排序。threshold=None 用 KAA 默认阈值。"""
    from kaa.tasks import R

    kw = {"region": region} if threshold is None else {"region": region, "threshold": threshold}
    found = R.InProduce.A.q(**kw).find_all() + R.InProduce.M.q(**kw).find_all()
    return sorted(found, key=lambda m: (m.rect.y1 // 40, m.rect.x1))


def _poll(check, timeout: float):
    """截图后调用 check()，直到返回真值或超时；返回最后一次结果。"""
    deadline = time.monotonic() + timeout
    while True:
        device.screenshot()
        result = check()
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(CHANGE_POLL)


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
    from kaa.tasks import R

    if cancel:
        logger.info("换卡阶段 1：决策不换，但阶段 1 没有取消键——照常进入阶段 2，在阶段 2 取消")
    deadline = time.monotonic() + CHANGE_STAGE_TIMEOUT
    while time.monotonic() < deadline:
        device.screenshot()
        if R.InProduce.DialogCardChange2.Title.exists():
            return True
        if R.InProduce.DialogCardChange1.ButtonNext.q(enabled=True).try_click():
            logger.info("换卡阶段 1：已点「次へ」")
            time.sleep(CHANGE_POLL)
            continue
        marks = find_card_marks(CHANGE_LETTERS_BOX, CHANGE_LETTER_THRESHOLD)
        target = _stage1_mark(marks, index) if marks else None
        if target is not None:
            img = device.screenshot()
            _change_run().snap(img, "stage1")
            device.click(target)
            logger.info("换卡阶段 1：换入第 %d 张候选「%s」（找到 %d 个角标）", index, _mark_card_name(img, target), len(marks))
        elif marks:
            logger.info("换卡阶段 1：第 %d 列的候选角标没找到（找到 %d 个），等下一帧", index, len(marks))
        else:
            logger.info("换卡阶段 1：候选卡还没出现，等入场动画")
        time.sleep(CHANGE_POLL)
    logger.warning("换卡阶段 1：%.0fs 内没进入阶段 2，交回主循环", CHANGE_STAGE_TIMEOUT)
    return False


PROTECTED_PROVIDER: Callable[[], set[str]] | None = None  # 策略层注入的保护名单（set_protected_provider）；None = 用 knowledge 的


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
                     sleep=time.sleep, max_screens: int | None = None) -> dict:  # fmt: skip
    """选卡对话框（换卡阶段 2 / 删卡 / 强化）往下滑着读整副牌组，拼完滑回顶部。
    返回 {cards（整副，index = 牌组序号 deck_index）, complete, screens, problems}；一屏放得下时 screens=1、cards 就是这一屏。
    complete=False（拼接对不上 / 张数与记账差太多）时 cards 仍是拼出来的结果，调用方不要当整副用。"""
    from hiro.vision.deck import dialog_scroll, merge_dialog_screens, read_card_dialog

    shoot = shoot or device.screenshot
    swipe = swipe or (lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION))
    max_screens = max_screens or CARD_OP_MAX_SCREENS
    img = shoot()
    first = read_card_dialog(img, region)["cards"]
    if not first or not dialog_scroll(img, region)["scrollable"]:
        return {"cards": first, "complete": bool(first), "screens": 1, "problems": [] if first else ["卡格没读到"]}
    screens = [first]
    frames = [img]
    merged = merge_dialog_screens(screens, expected=expected)
    for _ in range(max_screens):
        _dialog_swipe(region, True, swipe)
        sleep(CARD_OP_SCROLL_SETTLE)
        frames.append(shoot())
        screens.append(read_card_dialog(frames[-1], region)["cards"])
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
        import cv2

        from hiro.produce.ops import CALIB_DIR

        d = CALIB_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-牌组拼接"
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            imgio.imwrite(str(d / f"screen{i:02d}.png"), f)  # PNG：JPEG 压缩后卡图识别结果会变（实机认出的卡离线成了「?」），复现不了
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
                              device.click, time.monotonic, time.sleep, label="换卡", calib=True)  # fmt: skip
    _change_run.t = time.monotonic()
    return _change_run.run


def _mark_card_name(img, mark) -> str:
    """阶段 1 候选：角标在卡格左下，往上取卡面认名字（只用于日志）。"""
    from kaa.game_ui.skill_card_select import match_card_region

    x, y = int(mark.rect.x1), int(mark.rect.y1)
    card = match_card_region(img[max(0, y - 95) : y + 25, max(0, x - 5) : x + 115])
    return card.name if card else "?"


STAGE2_MAX_CANCELS = 2
_STAGE2_CANCELS: dict = {}  # (card_id, remove_upgrade) → 本进程里阶段 2 因找不到那张而取消的次数


def change_stage2(self, index: int = 0, card_id: str | None = None, cancel: bool = False, remove_index: int | None = None,
                  remove_upgrade: int | None = None) -> bool:  # fmt: skip
    """换卡阶段 2：选被换掉的原卡 → チェンジ → 确认。认不出 / 只剩保护卡时点キャンセル，返回 False；画面不在阶段 2 也返回 False。

    阶段 2 点「キャンセル」之后去哪：没有实机帧，[推断] 回到阶段 1（换入候选的选择页）。理由：阶段 1 没有取消键，セレクトチェンジ
    是选定授業档位时就决定的，游戏不会让这次换卡作废；KAA 参考图 dialog_card_change_2 的「キャンセル」与相談删卡页同款，
    相談里它只退回上一层。所以 cancel=True 可能让主循环在「阶段 1 → 阶段 2 取消」之间打转：调用方需要限次，超过后改为按决策
    换一张非保护卡（或停下报警）。calib 帧 stage2-cancel 会记录取消后的第一帧，用来确认。
    """
    from kaa.tasks import R

    from hiro.vision.deck import read_card_dialog

    if not _poll(lambda: R.InProduce.DialogCardChange2.Title.exists(), CHANGE_STAGE_TIMEOUT):
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
        time.sleep(CHANGE_POLL)
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
                    time.sleep(CARD_OP_SCROLL_SETTLE)
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
    if target is None:
        if card_id and not cancel:
            _STAGE2_CANCELS[key] = _STAGE2_CANCELS.get(key, 0) + 1
        logger.warning("换卡阶段 2：%s，不换，点「キャンセル」（卡格 %s）", why, [c.get("name") for c in dialog["cards"]])
        device.click(*CHANGE_CANCEL_TAP)
        time.sleep(CHANGE_POLL)
        run.snap(device.screenshot(), "stage2-cancel")
        return False
    device.click(*target["center"])
    time.sleep(CHANGE_POLL)
    picked = read_card_dialog(device.screenshot(), CHANGE2_CARD_AREA)
    run.snap(device.screenshot(), "stage2-picked")
    sel = picked.get("selected_card") or {}
    chosen, chosen_up = sel.get("card_id"), sel.get("upgrade")
    if chosen and (chosen != target["card_id"] or (target.get("upgrade") is not None and chosen_up is not None and chosen_up != target["upgrade"])):
        logger.warning("换卡阶段 2：选中的是 %s（强化 %s），不是要换的 %s（强化 %s），点「キャンセル」", chosen, chosen_up, target["card_id"],
                       target.get("upgrade"))  # fmt: skip
        device.click(*CHANGE_CANCEL_TAP)
        return False
    logger.info("换卡阶段 2：换出「%s」（%s，强化 %s，%s）", target.get("name"), target["card_id"], target.get("upgrade"), why)
    deadline = time.monotonic() + CHANGE_STAGE_TIMEOUT
    gone = 0
    while time.monotonic() < deadline:
        time.sleep(CHANGE_POLL)
        img = device.screenshot()
        if confirm_change_dialog(img, run):
            gone = 0
        elif R.InProduce.DialogCardChange2.ButtonConfirm.try_click():
            logger.info("换卡阶段 2：已点「チェンジ」")
            gone = 0
        elif R.InProduce.DialogCardChange2.Title.exists():
            gone = 0  # 还在阶段 2、确认框还没弹出来：接着等（第二十局 03:21：这里曾被当成完成，确认框晚弹出没人点）
        else:
            gone += 1
            if gone >= 2:  # 连续两帧阶段 2 标题和确认框都不在：换完了
                run.snap(img, "stage2-done")
                return True
    logger.warning("换卡阶段 2：%.0fs 内确认框没有关闭", CHANGE_STAGE_TIMEOUT)
    return False


CHANGE_CONFIRM_TITLE = ("チェンジ確認",)  # 「スキルカードチェンジ確認」（第二十局 calib 20261103-032052/05）
CHANGE_CONFIRM_OK = ("決定", "はい", "OK")
CHANGE_CONFIRM_TAP = (520, 1160)  # 确认框右下「決定」


def confirm_change_dialog(img, run=None) -> bool:
    """画面上是换卡确认框（「スキルカードチェンジ確認」）就点「決定」，返回是否点了。先试 KAA 模板，认不出再用橙色标题条 + 局部 OCR。
    换出哪张在点「チェンジ」之前已按决策选好，确认框只是确认这一步。"""
    from kaa.tasks import R

    if R.InProduce.DialogCardChangeConfirm.TItle.exists():
        if run is not None:
            run.snap(img, "stage2-confirm")
        btn = R.InProduce.DialogCardChangeConfirm.ButtonConfirm.find()
        if btn:
            device.click(btn.rect)
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


def _card_op(kind: str, card_id: str | None, *, shoot=None, click=None, clock=time.monotonic, sleep=time.sleep, ocr=None,
             calib: bool = True, choose=None, swipe=None, expected_size: int | None = None) -> bool:
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
    run.snap(img, "deck")
    cards = got.get("d", {}).get("cards") or []
    can_cancel = shop.cancel_visible(img)

    def give_up(why: str) -> bool:
        if can_cancel:
            run.tap(find_text(run.ocr(img), ("キャンセル",), region=(0, 1100, 360, 1220)) or shop.CANCEL_TAP)
            run.snap(run.shoot(), "cancel")
            logger.warning("%s：%s，点「キャンセル」", label, why)
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
        im = run.shoot()
        return im, read_card_dialog(im, region)["cards"]

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
            return give_up(f"牌组拼接不可信（{len(deck_cards)} 张，{merged['problems']}），不交决策")  # 第十四局拼成 36 张
        logger.info("%s：牌组 %d 屏共 %d 张", label, len(screens), len(deck_cards))
    if not card_id and choose is not None:
        card_id = choose(deck_cards)
        run.action.args[f"card_{kind}"] = card_id
    if not card_id:
        return give_up("决策没有给 card_id")
    want = next((c.get("upgrade") for c in deck_cards if c.get("card_id") == card_id), None)

    def find(cs):
        return next((c for c in cs if c.get("card_id") == card_id and (want is None or c.get("upgrade") in (want, None))), None)

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

    def picked(im):
        d = read_card_dialog(im, region)
        got["sel"] = d
        sel = (d.get("selected_card") or {}).get("card_id")
        return sel == card_id or (sel is None and d.get("selected") is not None and d["cards"][d["selected"]].get("card_id") == card_id)

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
        if run.clock() - last[0] >= 1.0 and (b := find_text(boxes, CARD_OP_CONFIRM[kind], region=(360, 1080, 720, 1240))):
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


def pitem_commit(self, index: int, *, shoot=None, click=None, clock=time.monotonic, sleep=time.sleep) -> bool:
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
                logger.info("P 道具：点第 %d 个背包 (%d, %d) 后 %.1fs「受け取る」没变亮（第 %d 次）", index, x, y, w.waited_s, attempt + 1)
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


EXPECT_MEMORY: tuple[str, ...] = ()  # run_hif --expect-memory：本战开局前，开始确认页上必须出现的选拔回忆属性数字


class MemoryMismatch(RuntimeError):
    """本战开始确认页上选中的选拔回忆不是指定的那份：不开局。"""


MEMORY_CHANGE_BTN = (636, 577)  # 开始确认页「選択中の選抜試験メモリー」右侧「変更」
MEMORY_OK_BTN = (360, 1085)  # 回忆列表底部「OK」
MEMORY_SORT_BTN = (527, 860)  # 列表上的排序键按钮（「スター性」/「入手」…）→ ソート・フィルタ 弹窗
MEMORY_SORT_NEWEST = (413, 273)  # 弹窗里「入手」
MEMORY_SORT_OK = (500, 1160)  # 弹窗「決定」
MEMORY_CELLS = [(x, y) for y in (487, 627, 767) for x in (140, 360, 580)]  # 列表可见的 3×3 格
MEMORY_PAGES = 12  # 回忆 45 份以上（q10），每次只滑 2 行防跳行


MEMORY_HEADER_BAND = (100, 220)  # 列表上方所选回忆的属性行（110 < y < 210）
MEMORY_CELL_WAIT = 0.6  # 点一格后等上方属性更新
MEMORY_SORT_TEXT = "入手"  # ソート・フィルタ 弹窗里按入手排序的选项


def _memory_snap(img, tag: str) -> None:
    """回忆切换每一步存帧（第二十二局没找到第十八局回忆，又没有帧，分不清是排序错了还是回忆已不在列表）。"""
    if img is None:
        return
    try:
        import cv2

        from hiro.produce.ops import CALIB_DIR

        d = CALIB_DIR / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(_memory_snap.t0))}-回忆"
        d.mkdir(parents=True, exist_ok=True)
        _memory_snap.n += 1
        imgio.imwrite(str(d / f"{_memory_snap.n:02d}-{tag}.jpg"), img)
    except Exception:
        pass


_memory_snap.t0, _memory_snap.n = time.time(), 0


def _find_text_center(boxes, word: str, *, exact: bool = False):
    hits = [((x1 + x2) // 2, (y1 + y2) // 2) for t, _, (x1, y1, x2, y2) in boxes
            if (t.replace(" ", "") == word if exact else word in t.replace(" ", ""))]  # fmt: skip
    return hits[0] if hits else None


def select_memory(expect: tuple[str, ...], *, shoot=None, click=None, swipe=None, texts_of=None, sleep=time.sleep, boxes_of=None) -> bool:
    """本战开始确认页：点「変更」→ 按「入手」排序 → 逐格点开回忆、读上方属性，找到属性全部吻合的那份 → OK。找不到就关掉列表、返回 False
    （回首页再进开始确认页时游戏会重置为上次用过的回忆，q8 实机，所以只能在这一页当场选）。
    第二十二局：12 页 × 9 格每格「等 1.2s + 整帧 OCR（3 倍放大）」，翻完要 5 分半；排序选项按固定坐标点，总控看到列表按「タグ」排。
    现在排序选项按 OCR 找「入手」点（找不到才用固定坐标），每格只 OCR 上方属性条（1.5 倍），等 0.6s；每步存帧到 calib/*-回忆。"""
    from hiro.produce.ops import ocr_boxes

    shoot = shoot or device.screenshot
    click = click or device.click
    swipe = swipe or device.swipe
    y1, y2 = MEMORY_HEADER_BAND
    texts_of = texts_of or (lambda img: [t for t, _, _ in ocr_boxes(img[y1:y2])])
    boxes_of = boxes_of or (lambda img: ocr_boxes(img) if img is not None else [])
    _memory_snap.t0, _memory_snap.n = time.time(), 0
    click(*MEMORY_CHANGE_BTN)
    sleep(1.5)
    # 按「入手」排序：刚打完的选拔回忆排在最前（q11：按スター性 排时新回忆在 46 份里靠后，没找到）
    click(*MEMORY_SORT_BTN)
    sleep(1.0)
    img = shoot()
    _memory_snap(img, "sort-dialog")
    boxes = boxes_of(img)
    click(*(_find_text_center(boxes, MEMORY_SORT_TEXT, exact=True) or _find_text_center(boxes, MEMORY_SORT_TEXT) or MEMORY_SORT_NEWEST))
    sleep(0.5)
    img = shoot()
    _memory_snap(img, "sort-picked")
    click(*(_find_text_center(boxes_of(img), "決定") or MEMORY_SORT_OK))
    sleep(1.2)
    for page in range(MEMORY_PAGES):
        for i, cell in enumerate(MEMORY_CELLS):
            click(*cell)
            sleep(MEMORY_CELL_WAIT)
            img = shoot()
            if i == 0:
                _memory_snap(img, f"page{page:02d}")
            got = "|".join(texts_of(img)).replace(",", "").replace(" ", "")
            # 拼成一串再找：OCR 常给「+1434」「⑨1454」这种带前缀 / 读混的块，按块精确相等永远对不上（第十八局本战两次没找到）
            if all(memory_value_seen(v, got) for v in expect):
                logger.info("本战开局：在回忆列表里选中 %s（第 %d 页第 %d 格）", "/".join(expect), page + 1, i + 1)
                click(*MEMORY_OK_BTN)
                sleep(2.0)
                return True
        swipe(360, 900, 360, 620, 1.2)  # 约 2 行，慢滑无惯性
        sleep(1.0)
    logger.warning("回忆列表里没找到 %s（翻了 %d 页，每页首格帧存在 calib/*-回忆）", "/".join(expect), MEMORY_PAGES)
    click(*MEMORY_OK_BTN)  # 关掉列表；之后的核对仍会不通过，不开局
    sleep(2.0)
    return False


_DIGIT_CONFUSION = {"3": "35", "5": "53", "6": "68", "8": "86", "0": "08", "1": "17", "7": "71"}  # 开始确认页粗体数字 OCR 常混淆


def memory_value_seen(value: str, texts: str) -> bool:
    """页面文字里有没有这个回忆属性值；允许最多 1 位按 _DIGIT_CONFUSION 混淆（第十八局：1434 被读成 1454，本战核对拒绝开局）。"""
    if value in texts:
        return True
    import re

    for m in re.finditer(r"\d{%d}" % len(value), texts):
        cand = m.group(0)
        diffs = [(a, b) for a, b in zip(value, cand) if a != b]
        if len(diffs) == 1 and diffs[0][1] in _DIGIT_CONFUSION.get(diffs[0][0], ""):
            return True
    return False


MEMORY_PAGE_READS = 3


def _memory_missing_on_page(*, shoot=None, texts=None, sleep=time.sleep) -> list:
    """开始确认页读选中回忆的属性，最多读 MEMORY_PAGE_READS 次（页面刚出来时数字可能还没画完）；返回没读到的那几项。"""
    if shoot is None or texts is None:
        from kotonebot import device as _d, ocr as _o

        shoot = shoot or _d.screenshot
        texts = texts or (lambda: [t.text for t in _o.ocr()])
    missing = list(EXPECT_MEMORY)
    for i in range(MEMORY_PAGE_READS):
        shoot()
        got = "".join(texts()).replace(",", "")
        missing = [v for v in EXPECT_MEMORY if not memory_value_seen(v, got)]
        if not missing:
            return []
        if i + 1 < MEMORY_PAGE_READS:
            sleep(1.0)
    return missing


def _check_memory_then(step4):
    def wrapped(*a, **k):
        if EXPECT_MEMORY:
            missing = _memory_missing_on_page()
            if missing:
                select_memory(EXPECT_MEMORY)
                # 不管列表里找没找到都重读本页：已选中的那份不在「変更」列表里（第二十五局：上次中断时已选好 1554/2505/830，
                # 首读没认全 → 进列表翻 12 页找不到 → 不开局），关掉列表后页面上仍是它
                missing = _memory_missing_on_page()
            if missing:
                logger.error("本战开始确认页的选拔回忆不是指定的那份（缺 %s），不开局", missing)
                raise MemoryMismatch(f"选拔回忆不符：页面上找不到 {missing}")
            logger.info("本战开局：选拔回忆核对通过（%s）", "/".join(EXPECT_MEMORY))
        return step4(*a, **k)

    return wrapped


ALLOW_DISCARD = False  # 只有 run_hif --allow-discard（用户明确同意破弃）时为 True
_ORIG_DISCARD_CONFIRM = None


class NeverConfirmDiscard:
    """替换 R.Produce.BreakProduceDialog.ButtonConfirm：KAA do_produce 遇到「プロデュースデータの破棄」会直接点「開始する」破弃
    其他端末的培育。这里改成一律点「キャンセル」并抛 ResumeBlocked，由用户处理（用户规则：绝不按開始する）。"""

    @staticmethod
    def _block(*a, **k):
        if ALLOW_DISCARD and _ORIG_DISCARD_CONFIRM is not None:
            return _ORIG_DISCARD_CONFIRM.try_click(*a, **k)
        from hiro.loop.resume import CANCEL_BUTTON, DISCARD_TITLE, ResumeBlocked

        device.click(*CANCEL_BUTTON)
        logger.error("KAA 想确认「%s」：已点「キャンセル」，停止运行", DISCARD_TITLE)
        raise ResumeBlocked(f"游戏提示「{DISCARD_TITLE}」：其他端末有进行中的培育。已点「キャンセル」，不破弃；由用户决定。")

    try_click = click = wait = _block

    @staticmethod
    def exists(*_a, **_k) -> bool:
        return False


BLIND_CHOICE: dict = {"pending": False}  # KAA 想盲点的二选一被拦下后置 True，HiroController 下一轮停下报警


class _BlindDoubleClick(Exception):
    pass


def _guard_action_like(orig):
    """KAA ProducePage._check_action_like 末尾：画面上有 >1 个剧情按钮（培育初始饮料 / 卡二选一、带选项的剧情）时直接 double_click(buttons[0])。
    这是盲选第 0 项（用户规则 3c / 3f 禁止）：换掉 page 模块的 device，让 double_click 抛异常，判成 UNKNOWN 并置 BLIND_CHOICE。"""
    if getattr(orig, "_hiro_guard", False):
        return orig

    def wrapped(self):
        import importlib

        pg = importlib.import_module("kaa.tasks.produce.new.page")
        from kaa.tasks.produce.new.consts import Scene, SceneType

        real = pg.device

        class _Proxy:
            def __getattr__(self, name):
                return getattr(real, name)

            def double_click(self, *a, **k):
                raise _BlindDoubleClick(a)

        pg.device = _Proxy()
        try:
            return orig(self)
        except _BlindDoubleClick:
            BLIND_CHOICE["pending"] = True
            logger.error("KAA 想双击第 0 个剧情按钮（初始饮料 / 卡二选一或剧情选项），已拦下")
            return Scene(SceneType.UNKNOWN)
        finally:
            pg.device = real

    wrapped._hiro_guard = True
    return wrapped


class RentalPending(RuntimeError):
    """开局第 2 步租借格：支援卡选择系统还没交付，不让 KAA 直接确认列表第一张，停下交总控手动挑（用户 2026-11-02）。"""


RENTAL_ATTR: str | None = None  # 租借挑哪个属性（run_hif 按 --main-attr 设）；None = hiro.produce.rental.WANT_ATTR


def _default_rental_pick() -> bool:
    """有开局编成方案：按「加上这张后编成的价值」挑（support_select.rental_scorer，自己 5 张取方案的）；没有方案：主属性、等级最高。"""
    from hiro import kaa_env
    from hiro.produce import rental

    plan = kaa_env.ACTIVE_PLAN or {}
    own = [{"support_card_id": o["support_card_id"], "level": o.get("level")} for o in (plan.get("support") or {}).get("own") or []]
    scorer = None
    if own:
        from hiro.brain.support_select import rental_scorer

        scorer = rental_scorer(own, RENTAL_ATTR or kaa_env.PLAN_MAIN_ATTR, (plan.get("idol") or {}).get("plan"))
    return rental.pick_rental(attr=RENTAL_ATTR or rental.WANT_ATTR, scorer=scorer)


RENTAL_PICK: Callable | None = _default_rental_pick  # 默认：主属性、等级最高的一张；策略层可替换成自己的选择


from hiro.produce.formation_check import FormationMismatch  # noqa: E402  run_hif 按 kaa_patches.FormationMismatch 捕获


def _check_support(img) -> None:
    """有开局编成方案时核对第 2 步的 5 格支援卡；对不上抛 FormationMismatch（不开局）。核对本身出错只记日志。"""
    from hiro import kaa_env

    if not kaa_env.ACTIVE_PLAN:
        return
    from hiro.produce.formation_check import check_support_step2

    try:
        from hiro.vision.support_screens import read_support_formation

        problems, warnings = check_support_step2(read_support_formation(img), kaa_env.ACTIVE_PLAN)
    except Exception as e:  # noqa: BLE001
        logger.warning("支援卡编成核对失败（不拦开局）：%s", e)
        return
    for w in warnings:
        logger.warning("支援卡编成核对：%s", w)
    if problems:
        logger.error("支援卡编成和方案对不上，不开局：%s", "；".join(problems))
        raise FormationMismatch("支援卡编成和方案对不上：" + "；".join(problems))
    logger.info("支援卡编成核对通过（编成 %s）", kaa_env.SUPPORT_SET)


def _check_memory_cards(img, plan: dict) -> tuple[list[str], list[str]]:
    """第 3 步四格回忆的技能卡小图标 vs 方案的回忆卡。HIRO_MEMORY_CARD_CHECK：block（默认，有把握认错就拦）/ warn / off。"""
    import os

    mode = (os.environ.get("HIRO_MEMORY_CARD_CHECK") or "block").lower()
    if mode == "off":
        return [], []
    try:
        from hiro.produce.formation_check import check_memory_cards
        from hiro.vision.memory_icons import read_step3_icons

        reads = read_step3_icons(img, expect=list((plan.get("memory") or {}).get("cards") or []))
        logger.info("第 3 步回忆图标：%s", "；".join(f"{r['slot'] + 1}:{r['card']} {r['score']}" for r in reads))
        problems, warnings = check_memory_cards(reads, plan)
    except Exception as e:  # noqa: BLE001
        return [], [f"回忆卡面核对失败（不拦开局）：{e}"]
    if mode == "warn":
        return [], warnings + [p + "（HIRO_MEMORY_CARD_CHECK=warn，不拦）" for p in problems]
    return problems, warnings


def _check_memory_set() -> None:
    """有开局编成方案时，在 KAA 选完回忆编成后读「N/20」核对编成号；对不上抛 FormationMismatch。"""
    from hiro import kaa_env

    if not kaa_env.ACTIVE_PLAN:
        return
    from hiro.produce.formation_check import check_memory_step3, memory_set_number

    try:
        from hiro.produce.ops import ocr_boxes

        img = device.screenshot()
        problems, warnings = check_memory_step3(memory_set_number([(t, b) for t, _, b in ocr_boxes(img)]), kaa_env.ACTIVE_PLAN)
    except Exception as e:  # noqa: BLE001
        logger.warning("回忆编成核对失败（不拦开局）：%s", e)
        return
    if not problems:
        p2, w2 = _check_memory_cards(img, kaa_env.ACTIVE_PLAN)
        problems, warnings = problems + p2, warnings + w2
    for w in warnings:
        logger.warning("回忆编成核对：%s", w)
    if problems:
        logger.error("回忆编成和方案对不上，不开局：%s", "；".join(problems))
        raise FormationMismatch("回忆编成和方案对不上：" + "；".join(problems))
    logger.info("回忆编成核对通过（メモリー編成 %s）", kaa_env.MEMORY_SET)


def _rent_step2(orig):
    def wrapped(set_number=None, auto_set=None):
        if auto_set:
            from hiro import kaa_env

            if kaa_env.SUPPORT_SET == kaa_env.AUTO:  # 使用者明确写了 HIRO_SUPPORT_SET=auto：交给 KAA 的おまかせ
                logger.warning("支援卡：HIRO_SUPPORT_SET=auto，由 KAA「おまかせ」选支援卡和租借")
                return orig(set_number=set_number, auto_set=auto_set)
            raise RuntimeError("支援卡「おまかせ」没有被明确选择（HIRO_SUPPORT_SET=auto），auto_set_support_card 必须为 False")
        from kaa.tasks import R
        from kotonebot import device

        if not R.Produce.TextStepIndicator2.exists():
            return False
        if set_number is not None:
            importlib.import_module("kaa.tasks.produce.prepare")._select_set(set_number)
        _check_support(device.screenshot())
        if R.Produce.Step2.ButtonNext.q(enabled=True).exists():
            logger.info("支援卡：编成 %s 已满（含租借格），直接下一步", set_number)
            return True
        if RENTAL_PICK is not None and RENTAL_PICK():
            from kaa.tasks import R as _R

            for _ in range(10):
                device.screenshot()
                if _R.Produce.Step2.ButtonNext.q(enabled=True).exists():
                    return True
                time.sleep(0.5)
        logger.error("支援卡：编成 %s 的租借格还空着，选择系统未交付，停下交总控手动挑（不让 KAA 确认第一张）", set_number)
        raise RentalPending(f"支援卡编成 {set_number} 的租借格需要手动挑选")

    wrapped._hiro_rent = True
    return wrapped


def _snap_step3(orig):
    """第 3 步「メモリー選択」：存一帧并记下有没有租借入口（用户 2026-11-02 同意只看），然后照常走 KAA 的固定编成。"""

    def wrapped(*a, **k):
        try:
            from pathlib import Path

            import cv2
            from kotonebot import ocr

            img = device.screenshot()
            d = Path("reports/live/rental")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"step3-memory-{time.strftime('%Y%m%d-%H%M%S')}.png"
            imgio.imwrite(str(p), img)
            texts = [t.text for t in ocr.ocr()]
            hit = [t for t in texts if "レンタル" in t]
            logger.info("第 3 步回忆选择：截图 %s；租借字样 %s", p, hit or "无")
        except Exception as e:  # noqa: BLE001  只是观察，不能影响开局
            logger.warning("第 3 步截图失败：%s", e)
        import os

        if os.environ.get("HIRO_STEP3_PAUSE") == "1":
            # 回忆租借第一次实机探查（用户 2026-11-02 同意）：停在第 3 步交总控手动点开回忆格、看租借列表、必要时租
            raise RentalPending("第 3 步暂停：总控手动探查回忆租借（HIRO_STEP3_PAUSE=1）")
        r = orig(*a, **k)
        if r:
            _check_memory_set()  # KAA 选完编成、点「次へ」之前
        return r

    wrapped._hiro_snap = True
    return wrapped


_ORIG_ACTION_COMMIT = None  # KAA 原版（休息走它）


def action_commit(self, action) -> None:
    """选行动提交：同 KAA ActionSelectContext.commit，去掉点完后的固定 sleep(2)——之后的 wait_disappear 本来就轮询到按钮消失
    （第二十一局选拔：选行动每步执行 4–5s）。休息的确认框流程照旧。"""
    from kaa.tasks import R
    from kaa.tasks.produce.new.consts import ProduceAction

    actions = self.fetch_available_actions()
    if action not in actions[0]:
        raise ValueError(f"Action {action} is not available now.")
    button = actions[1][actions[0].index(action)]
    if action == ProduceAction.REST:
        return _ORIG_ACTION_COMMIT(self, action)
    for _ in range(3):
        button.click()
        time.sleep(0.3)
    logger.info("Entered action: %s", action)
    self.controller.wait_disappear(button.prefab)


PREP_STEPS = ("step1", "step2", "step3", "step4", "_select_set", "_select_idol")  # KAA 培育准备里每一步前后各报一次进展（D38）


def _prep_progress(fn, name: str):
    """KAA 培育准备的一步：开始前、结束后（含异常）各报一次进展，空转看门狗不会把整段准备算成没进展。
    单步自己卡住 STALL_S 秒照样停机。functools.wraps 带上原函数的 _hiro_* 标记，重复 apply 不会多包。"""
    import functools

    from hiro.loop.repeat_guard import progress

    @functools.wraps(fn)
    def wrapped(*a, **k):
        progress(f"培育准备 {name} 开始")
        try:
            return fn(*a, **k)
        finally:
            progress(f"培育准备 {name} 结束")

    wrapped._hiro_progress = True
    return wrapped


def apply() -> None:
    import importlib

    import kaa.game_ui.idols_overview as io
    from kaa.tasks.produce.new.page import ActionSelectContext, PItemSelectContext, SkillCardChangeContext

    global _ORIG_ACTION_COMMIT
    if _ORIG_ACTION_COMMIT is None and ActionSelectContext.commit is not action_commit:
        _ORIG_ACTION_COMMIT = ActionSelectContext.commit

    # kaa.tasks 导出了同名任务函数 produce，遮住了子包属性，只能按模块全名导入
    prep = importlib.import_module("kaa.tasks.produce.prepare")
    io.locate_idol = locate_idol_by_swipe
    prep.locate_idol = locate_idol_by_swipe
    SkillCardChangeContext.commit_stage1 = change_stage1
    SkillCardChangeContext.commit_stage2 = change_stage2
    PItemSelectContext.commit = pitem_commit
    ActionSelectContext.commit = action_commit
    from kaa.tasks import R
    from kaa.tasks.produce.new.page import ProducePage

    ProducePage._check_action_like = _guard_action_like(ProducePage._check_action_like)
    global _ORIG_DISCARD_CONFIRM
    if R.Produce.BreakProduceDialog.ButtonConfirm is not NeverConfirmDiscard:
        _ORIG_DISCARD_CONFIRM = R.Produce.BreakProduceDialog.ButtonConfirm
    R.Produce.BreakProduceDialog.ButtonConfirm = NeverConfirmDiscard  # do_produce 按属性名取，替换后它点不到「開始する」
    if not getattr(prep.step3, "_hiro_snap", False):
        prep.step3 = _snap_step3(prep.step3)
    if not getattr(prep.step2, "_hiro_rent", False):
        prep.step2 = _rent_step2(prep.step2)
    if not getattr(prep.step4, "_hiro_memory_check", False):
        prep.step4 = _check_memory_then(prep.step4)
        prep.step4._hiro_memory_check = True
    for name in PREP_STEPS:
        fn = getattr(prep, name, None)
        if fn is not None and not getattr(fn, "_hiro_progress", False):
            setattr(prep, name, _prep_progress(fn, name))
    from hiro.loop import win_desktop

    win_desktop.apply()  # DMM 后台点击：桌面暂时不可用（UAC / 锁屏）时等它恢复，不让主循环连崩
    logger.info("已启用 KAA 补丁：locate_idol 改为上滑翻页；授業换卡放宽卡片定位、阶段 2 不换保护卡；H.I.F P 道具按实测位置点击；破弃对话框只点キャンセル")
