# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""H.I.F 轮间（インターバル，R1 与 R2 之间）执行器：execute_interval(action, page_fields) -> ExecutionResult。

一步一个动作，由上层循环「decide → execute → 重新 observe」，直到 IntervalEnd。第四局本战轮间被 KAA ctx.end() 直接结束，
380P 没花、体力没回（第四局复盘问题 6）。

画面按 KAA 参考图 hif_interval.png / hif_interval_confirm.png（720x1280）：
- 货架 4 列 x 2 行，价格条中心 x 126/286/445/605、y 650/841，格子中心在价格条上方约 88 像素；「チェンジ」标签在第一行
  第 3、4 列上方。index（决策器 / docs/llm-context.md「HIF_ROUND_INTERVAL」）：卡 0–3、セレクトチェンジ 4–5、饮料 6–7。
  识别给了 center 就用识别的；没给时按 DEFAULT_CENTERS（卡按行优先占左两列，未在实机核对）。
- 「交換する」「終了」与相談页同一位置，相談的页面判据（shop.consult_idle / cancel_visible）在参考图上成立，
  所以买与结束复用 hiro.produce.shop：买 = 点格 → 交換する → 交換確認（确认框里的商品与要买的不一致就取消）→ 获得动画
  → 回到主画面且 交換済 / P 减少 / 格子变化；结束 = 終了 →「インターバル終了確認」→「本戦へ」(513,1160)。
- 回体力：中间右侧「回復」(622,930)。之后的画面没有样本：出弹窗就按文字确认（回復する / 決定 / はい / OK），
  以体力增加或 P 点减少为准。
- 强化：「強化 P100」(176,928) → 牌组选卡页（假定与相談「削除 / 強化」同一网格，special_guidance.grid_center）
  → 选第 card_index 张 → 右下确认键 →（确认框）→「〜を強化しました」或回到主画面且 P 减少。
没有实机帧的步骤都存帧到 ops.CALIB_DIR（open / pick / confirm / back / end）。所有等待按时间封顶。
"""

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from hiro.contracts import Action, ActionKind, ExecutionResult
from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep
from hiro.produce import shop
from hiro.produce.ops import Run, find_text, orange_lit
from hiro.produce.special_guidance import grid_center

import numpy as np

if TYPE_CHECKING:
    from hiro.contracts.decision import Decision

logger = logging.getLogger(__name__)

PAGE = "HIF_ROUND_INTERVAL"
COLS_X = (126, 286, 445, 605)
ROWS_Y = (562, 753)
DEFAULT_CENTERS = {0: (COLS_X[0], ROWS_Y[0]), 1: (COLS_X[1], ROWS_Y[0]), 2: (COLS_X[0], ROWS_Y[1]), 3: (COLS_X[1], ROWS_Y[1]),
                   4: (COLS_X[2], ROWS_Y[0]), 5: (COLS_X[3], ROWS_Y[0]), 6: (COLS_X[2], ROWS_Y[1]), 7: (COLS_X[3], ROWS_Y[1])}  # fmt: skip
UPGRADE_TAP = (176, 928)
RECOVER_TAP = (622, 930)
HP_BAND = (320, 50, 440, 95)  # 顶部「18/33」
RECOVER_CONFIRM = ("回復する", "回復", "決定", "はい", "OK")
# 「体力回復確認」框（第十七局 calib 20261102-172120-轮间/02-confirm）：滑条两端 −/＋，每格 2 体力 / 10P；上方「+2」是本次回复量
RECOVER_PLUS = (585, 945)
RECOVER_MINUS = (135, 945)
RECOVER_AMOUNT_BAND = (300, 750, 420, 805)
RECOVER_STEP_WAIT = 0.25
RECOVER_MAX_TIMES = 20
UPGRADE_CONFIRM = ("強化する", "強化", "決定", "はい", "OK")
UPGRADE_DONE = ("強化しました",)
DIALOG_BUTTONS = (360, 1080, 720, 1240)

TITLE_TEXT, TITLE_REGION = (
    ("インターバル",),
    (30, 25, 200, 70),
)  # 左上角页面标题：进入动画、退出淡出时都在，ラウンド2 转场里没有
ENTER_TIMEOUT = 8.0  # 进入轮间时笔记本翻开的动画（第四局 frames/00037）
LEAVE_TIMEOUT = 10.0  # 「本戦へ」后轮间画面淡出
DIALOG_TIMEOUT = 3.0
SETTLE_TIMEOUT = 10.0
DECK_TIMEOUT = 3.0
SKIP_EVERY = 1.0


DIGIT_FIX = str.maketrans({"ろ": "3", "る": "3", "O": "0", "o": "0", "〇": "0", "l": "1", "I": "1", "|": "1", "S": "5", "s": "5",
                           "B": "8", "／": "/"})  # fmt: skip  # 顶部数字常见 OCR 混淆（q10 轮间：「21/3ろ」）


def read_hp(img: np.ndarray, ocr) -> tuple[int, int] | None:
    """顶部「体力/上限」。数字按 DIGIT_FIX 纠正常见 OCR 混淆，前面的心形图标等杂字忽略（「♥16/33」）。"""
    misread = False
    for t, _, (x1, y1, x2, y2) in ocr(img):
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if HP_BAND[0] <= cx <= HP_BAND[2] and HP_BAND[1] <= cy <= HP_BAND[3]:  # noqa: SIM102
            if m := re.search(r"(\d+)/(\d+)", t.replace(" ", "").translate(DIGIT_FIX)):
                hp, mx = int(m.group(1)), int(m.group(2))
                if 0 < mx <= 99 and hp <= mx:
                    return hp, mx
                misread = misread or mx >= 10  # 「33/21」：两边都读全了却矛盾，读错了就不信
    if misread:
        return None
    # 没读到或上限被截成一位（RapidOCR「21/3」）：头部体力和选行动页同一位置，用它那套逐块放大读（含斜杠误读规则）兜底
    from hiro.vision.action import read_stamina

    return read_stamina(img)


def find_item(page_fields: dict, index: int) -> dict | None:
    """page_fields["interval"] 的 cards / changes / drinks 里找 index；没有 center 时补默认位置。"""
    interval = (page_fields or {}).get("interval") or {}
    for kind in ("cards", "changes", "drinks"):
        for it in interval.get(kind) or []:
            if it.get("index") == index:
                return it if it.get("center") else {**it, "center": list(DEFAULT_CENTERS[index])}
    if index in DEFAULT_CENTERS:  # 识别没给这一格：按参考图位置，不知道商品是什么就不核对确认框
        return {"index": index, "center": list(DEFAULT_CENTERS[index])}
    return None


def on_interval(img: np.ndarray, ocr) -> bool:
    return find_text(ocr(img), TITLE_TEXT, region=TITLE_REGION) is not None


def _end(r: Run) -> ExecutionResult:
    """終了 →「本戦へ」，再等左上「インターバル」标题消失才算离开。
    第五局 21:26：「本戦へ」后画面淡出期间（calib 212647/04-end）标题还在，原来这时就返回成功，KAA 仍认得轮间标题，
    主循环又进来发了一次 IntervalEnd。"""
    res = shop.tap_end(r)
    if not res.ok:
        return res
    left, img = r.until(lambda im: not on_interval(im, r.ocr), LEAVE_TIMEOUT)
    r.snap(img, "left" if left else "still-interval")
    if not left:
        return r.result(False, f"点了「本戦へ」，但 {LEAVE_TIMEOUT:.0f}s 后左上仍是「インターバル」")
    return r.result(True, page_after=None)


def _wait_ready(r: Run) -> bool:
    """等进入动画结束、主画面出现（相談同一判据）。"""
    ok, img = r.until(shop.consult_idle, ENTER_TIMEOUT)
    r.snap(img, "open")
    return ok


RECOVER_DIALOG = ("体力回復確認",)


def _recover_dialog(boxes) -> bool:
    return find_text(boxes, RECOVER_DIALOG) is not None


def _clear_recover_dialog(r: Run, *, accept: bool) -> bool | None:
    """残留的「体力回復確認」弹窗（main12：P 点读错，回体力在确认框还开着时就判成功，弹窗挡住之后每一步）。
    accept=True 点「回復する」，否则点「キャンセル」。返回 None = 没有弹窗；True/False = 弹窗是否已关掉。"""
    img = r.shoot()
    boxes = r.ocr(img)
    if not _recover_dialog(boxes):
        return None
    r.snap(img, "stale-recover-dialog")
    btn = find_text(boxes, ("回復する",) if accept else ("キャンセル",), region=DIALOG_BUTTONS)
    if btn is None:
        btn = (500, 1160) if accept else (220, 1160)
    r.tap(btn)
    logger.warning("轮间：画面上残留「体力回復確認」，点「%s」", "回復する" if accept else "キャンセル")
    ok, _ = r.until(lambda im: not _recover_dialog(r.ocr(im)), SETTLE_TIMEOUT)
    return ok


def _dialog_amount(boxes) -> int | None:
    """确认框上方的「+N」（本次回复量）。"""
    x1, y1, x2, y2 = RECOVER_AMOUNT_BAND
    for t, _, (a, b, c, d) in boxes:
        if (
            x1 <= (a + c) // 2 <= x2
            and y1 <= (b + d) // 2 <= y2
            and (m := re.fullmatch(r"[+＋](\d+)", t.replace(" ", "").translate(DIGIT_FIX)))
        ):
            return int(m.group(1))
    return None


def _recover_times(hp0, p0, target_hp) -> int:
    """回到 target_hp 要按几格（每格 RECOVER_HP），受 P 限制。读不到体力 / 没给目标时 1 格（旧行为）。"""
    if target_hp is None or hp0 is None:
        return 1
    times = -(-(min(target_hp, hp0[1]) - hp0[0]) // RECOVER_HP)
    if p0 is not None:
        times = min(times, p0 // RECOVER_PRICE)
    return max(1, min(times, RECOVER_MAX_TIMES))


def _set_amount(r: Run, times: int) -> int | None:
    """确认框里按「＋」把回复量加到 times 格，读「+N」核对，差了再补按一次 ±。返回最后读到的回复量（读不到为 None）。"""
    for _ in range(times - 1):
        r.tap(RECOVER_PLUS)
        r.sleep(RECOVER_STEP_WAIT)
    amount = None
    for _ in range(3):
        amount = _dialog_amount(r.ocr(r.shoot()))
        if amount is None or amount == times * RECOVER_HP:
            break
        steps = (times * RECOVER_HP - amount) // RECOVER_HP
        for _ in range(abs(steps)):
            r.tap(RECOVER_PLUS if steps > 0 else RECOVER_MINUS)
            r.sleep(RECOVER_STEP_WAIT)
        if steps == 0:  # 差不到一格（到上限被截断）：以画面为准
            break
    return amount


def _recover(r: Run, info: dict | None, target_hp: int | None = None) -> ExecutionResult:
    """「回復」：target_hp 给出时在确认框里一次按够格数（第十七局轮间 19→33 分 7 次各 12–14 秒，共 1.5 分钟），否则回 1 格。"""
    if (info or {}).get("available") is False:
        return r.result(False, "「回復」不可用（识别为灰色 / 体力已满）")
    before = r.shoot()
    hp0, p0 = read_hp(before, r.ocr), shop.read_p(before, r.ocr)
    if target_hp is not None and hp0 is not None and hp0[0] >= min(target_hp, hp0[1]):
        return r.result(False, f"体力 {hp0[0]}/{hp0[1]} 已到目标 {target_hp}")
    times = _recover_times(hp0, p0, target_hp)
    r.tap(RECOVER_TAP)
    amount = None
    if times > 1:
        opened, _ = r.until(lambda im: _recover_dialog(r.ocr(im)), DIALOG_TIMEOUT)
        if opened:
            amount = _set_amount(r, times)

    def recovered(im) -> bool:
        if not shop.consult_idle(im) or _recover_dialog(r.ocr(im)):  # 确认框还开着不算（P 点读错曾让它提前判成功）
            return False
        hp, p = read_hp(im, r.ocr), shop.read_p(im, r.ocr)
        return (hp0 is not None and hp is not None and hp[0] > hp0[0]) or (p0 is not None and p is not None and p < p0)

    last = [r.clock() - SKIP_EVERY]

    def confirm(im):
        if r.clock() - last[0] < SKIP_EVERY:
            return
        if btn := find_text(r.ocr(im), RECOVER_CONFIRM, region=DIALOG_BUTTONS):
            r.snap(im, "confirm")
            r.tap(btn)
            last[0] = r.clock()

    ok, img = r.until(recovered, SETTLE_TIMEOUT, tick=confirm)
    r.snap(img, "back")
    if not ok:
        return r.result(False, f"点「回復」后 {SETTLE_TIMEOUT:.0f}s 内体力没增加、P 点也没减少（体力 {hp0}，P {p0}）",
                        page_after=PAGE if shop.consult_idle(img) else None)  # fmt: skip
    hp, p = read_hp(img, r.ocr), shop.read_p(img, r.ocr)
    logger.info(
        "轮间：回复体力 %s → %s（%d 格，确认框读到 +%s），P %s → %s（%.1fs）",
        hp0,
        hp,
        times,
        amount,
        p0,
        p,
        r.clock() - r.t0,
    )
    return r.result(True)


def _upgrade(
    r: Run,
    card_index: int,
    info: dict | None,
    card_id: str | None = None,
    choose_card=None,
    deck_size: int | None = None,
) -> ExecutionResult:
    """轮间「強化」：打开牌组选卡页 → 有 card_id / choose_card 时走 card_ops._card_op（按 card_id 定位、核对、确认、
    认不出就取消）；都没有时按 card_index 点网格（旧路径）。"""
    if (info or {}).get("available") is False:
        return r.result(False, "「強化」不可用（识别为灰色）")
    if card_id or choose_card is not None:
        from hiro.produce.card_ops import _card_op

        r.tap(UPGRADE_TAP)
        ok, img = r.until(shop.cancel_visible, DECK_TIMEOUT)
        r.snap(img, "deck")
        if not ok:
            return r.result(False, f"点「強化」后 {DECK_TIMEOUT:.0f}s 内没打开牌组选卡页（P 点不足？）")
        choose = (lambda cards: choose_card("enhance", cards)) if choose_card is not None else None
        done = _card_op("enhance", card_id, shoot=r.shoot, click=r.click_fn, clock=r.clock, sleep=r.sleep, ocr=r.ocr,
                        calib=r.calib is not None, choose=choose, expected_size=deck_size)  # fmt: skip
        back, img = r.until(shop.consult_idle, SETTLE_TIMEOUT)
        r.snap(img, "back")
        return r.result(
            done,
            None if done else "「強化」没做成（没给 / 没找到 card_id 或选中不符，已取消）",
            page_after=PAGE if back else None,
        )
    return _upgrade_by_index(r, card_index)


def _upgrade_by_index(r: Run, card_index: int) -> ExecutionResult:
    pos = grid_center(card_index)
    if pos is None:
        return r.result(False, f"第 {card_index} 张卡不在牌组页可见的 3 行里（滚动未实现）")
    before = r.shoot()
    p0 = shop.read_p(before, r.ocr)
    r.tap(UPGRADE_TAP)
    ok, img = r.until(shop.cancel_visible, DECK_TIMEOUT)
    r.snap(img, "deck")
    if not ok:
        return r.result(False, f"点「強化」后 {DECK_TIMEOUT:.0f}s 内没打开牌组选卡页（P 点不足？）")
    r.tap(pos)
    ok, img = r.until(lambda im: shop.selected(im, pos) and orange_lit(im, shop.CONFIRM_BTN), DIALOG_TIMEOUT)
    r.snap(img, "pick")
    if not ok:
        r.tap(shop.CANCEL_TAP)
        return r.result(False, f"牌组页点第 {card_index} 张后 {DIALOG_TIMEOUT:.0f}s 内没选中 / 确认键没亮，已取消")
    r.tap(shop.CONFIRM_TAP)
    last = [r.clock()]
    state = {}

    def done(im) -> bool:
        boxes = r.ocr(im)
        if find_text(boxes, UPGRADE_DONE) is not None:
            state["toast"] = True
            return True
        if shop.consult_idle(im) and p0 is not None and (p := shop.read_p(im, r.ocr)) is not None and p < p0:
            return True
        if r.clock() - last[0] >= SKIP_EVERY and (btn := find_text(boxes, UPGRADE_CONFIRM, region=DIALOG_BUTTONS)):
            r.snap(im, "confirm")
            r.tap(btn)
            last[0] = r.clock()
        return False

    ok, img = r.until(done, SETTLE_TIMEOUT)
    r.snap(img, "back")
    if not ok:
        return r.result(
            False, f"确认强化后 {SETTLE_TIMEOUT:.0f}s 内没看到「強化しました」，P 点也没减少", page_after=None
        )
    if state.get("toast"):  # 结果页：等它自己回主画面，不回来就交主循环
        back, img = r.until(shop.consult_idle, SETTLE_TIMEOUT)
        r.snap(img, "returned" if back else "not-returned")
        logger.info("轮间：强化第 %d 张（%.1fs）", card_index, r.clock() - r.t0)
        return r.result(True, page_after=PAGE if back else None)
    logger.info("轮间：强化第 %d 张（%.1fs）", card_index, r.clock() - r.t0)
    return r.result(True)


def execute_interval(
    action: Action,
    page_fields: dict,
    *,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
    purchased: Callable[[np.ndarray, list], bool] = shop.ocr_purchased,
    confirm_reader=shop._read_dialog,
    ocr=None,
    calib: bool = True,
    choose_card: Callable[[str, list], str | None] | None = None,
    deck_size: int | None = None,
) -> ExecutionResult:
    """执行一个轮间动作。page_fields 是识别给的字段（page_fields["interval"]）。ok=True 表示观察到生效。
    deck_size：状态机记账的牌组张数（强化选卡对话框多屏拼接核对用；None = 不核对）。"""
    r = Run(
        action,
        PAGE,
        shoot or device.screenshot,
        click or device.click,
        clock,
        sleep,
        label="轮间",
        ocr=ocr,
        calib=calib,
    )
    args = action.args or {}
    interval = (page_fields or {}).get("interval") or {}
    is_recover = action.kind is ActionKind.INTERVAL_BUY and args.get("service") == "recover"
    if (
        action.kind is not ActionKind.REROLL
    ):  # 刷新确认框挂着（晚弹出来）：取消，别让它挡住这一步、也别被当成「已离开轮间」
        shop.cancel_reroll_dialog(r)
    elif shop.reroll_dialog(r.ocr(r.shoot())):  # 要刷新、确认框正好挂着：直接按确认框处理（主画面被它挡着，等不到）
        return shop._reroll(r, "interval", interval.get("refresh"))
    if (comm := shop.clear_comm_error(r)) is not None:
        return comm
    stale = _clear_recover_dialog(r, accept=is_recover)
    if stale is not None and is_recover:  # 想回体力、正好停在它的确认框上：点了「回復する」就是这一步
        return r.result(bool(stale), None if stale else "「体力回復確認」点了「回復する」没关掉", page_after=PAGE)
    if action.kind is ActionKind.INTERVAL_END and not on_interval(r.shoot(), r.ocr):
        logger.info("轮间：IntervalEnd 时已不在轮间（转场中），视为已结束")
        return r.result(True, page_after=None)
    if not _wait_ready(r):
        img = r.shoot()
        if action.kind is ActionKind.INTERVAL_END and not on_interval(img, r.ocr):
            return r.result(True, page_after=None)
        return r.result(False, f"{ENTER_TIMEOUT:.0f}s 内轮间主画面没出现（还在进入动画？）", page_after=None)
    if action.kind is ActionKind.INTERVAL_END:
        return _end(r)
    if action.kind is ActionKind.REROLL:  # args {"scope": "interval"}：左下「リフレッシュ」（10P）
        return shop._reroll(r, "interval", interval.get("refresh"))
    if action.kind is ActionKind.INTERVAL_UPGRADE_CARD:
        return _upgrade(
            r, int(args.get("card_index", -1)), interval.get("upgrade"), args.get("card_id"), choose_card, deck_size
        )
    if action.kind is ActionKind.INTERVAL_BUY and args.get("service") == "recover":
        return _recover(r, interval.get("recover"), args.get("target_hp"))
    if action.kind is ActionKind.INTERVAL_BUY and "index" in args:
        item = find_item(page_fields, int(args["index"]))
        if item is None:
            return r.result(False, f"轮间没有第 {args['index']} 格")
        return shop._buy(r, item, purchased, confirm_reader)
    return r.result(False, f"轮间执行器不支持 {action}")


# ---------------------------------------------------------------- 整个轮间的循环（第八局复盘 B1）

RECOVER_PRICE, RECOVER_HP = 10, 2  # 每次「回復」10P 回 2 体力（第五局实机：P 360→350、体力 21→23）
MAX_BUY_FAILS = 2  # 同一格失败这么多次就不再买
MAX_FAIL_STREAK = 3  # 连续失败（任意动作）这么多次就去回体力、结束


def mark_known(page_fields: dict, bought: set[int], blocked: set[int]) -> dict:
    """把本轮间已买到 / 买不了的格子标成 purchased（识别刷新慢或缺帧时，决策器不会再选它们）。返回新的 page_fields。"""
    pf = dict(page_fields or {})
    iv = pf.get("interval")
    if not iv:
        return pf
    iv = dict(iv)
    for kind in ("cards", "changes", "drinks"):
        iv[kind] = [
            dict(it, purchased=True) if it.get("index") in bought | blocked else it for it in iv.get(kind) or []
        ]
    pf["interval"] = iv
    return pf


def screen_status(
    shoot=None, ocr=None, fallback: Callable[[], tuple] | None = None, tries: int = 3
) -> tuple[int | None, int | None, int | None]:
    """画面顶部读 (体力, 体力上限, P)。读不出就再截几帧；仍读不出的项用 fallback()（例如观测状态里的体力 / 上限 / P）补。"""
    from hiro.produce.ops import ocr_boxes

    o = ocr or ocr_boxes
    hp = p = None
    for _ in range(tries):
        img = (shoot or device.screenshot)()
        hp = hp or read_hp(img, o)
        p = p if p is not None else shop.read_p(img, o)
        if hp and p is not None:
            break
    out = [hp[0] if hp else None, hp[1] if hp else None, p]
    if fallback is not None and None in out:
        fb = fallback() or (None, None, None)
        out = [v if v is not None else f for v, f in zip(out, fb)]
        logger.info("轮间：画面读不全体力 / P，用观测状态补：%s", out)
    return tuple(out)


def interval_loop(
    observe: Callable[[], dict],
    decide: Callable[[dict], "Decision"],
    record: Callable[[object, ExecutionResult], None] | None = None,
    *,
    execute: Callable[[Action, dict], ExecutionResult] = execute_interval,
    status: Callable[[], tuple] = screen_status,
    max_steps: int = 12,
    hp_target: Callable[[int], int] | None = None,
) -> bool:
    """轮间：observe → decide → execute，一步一步，直到决策给 IntervalEnd；离开前一定先回体力。返回是否已离开轮间。

    第八局 main8 -r2 #95–101：买过的第 0 格识别没刷新（连续 4 帧缺 interval 字段，沿用的旧字段里它还是 purchased=false），
    决策又去买它，执行失败后整个轮间直接结束——体力 18/33 没回、395P 没花。现在：
    - 买成功的格子、同一格失败 MAX_BUY_FAILS 次的格子，在交给决策前标成 purchased（mark_known）；
    - 某一步失败不退出：page_after 还在轮间就重新观测、继续；连续失败 MAX_FAIL_STREAK 次才转去收尾；
    - 观测缺 interval 字段时沿用上一次的字段（再叠加已买 / 买不了的标记）；
    - 收尾（决策给 IntervalEnd、步数用完、或连续失败）：按画面读体力和 P，没到目标且 P ≥ 10 就「回復」到目标或 P 不够，再结束。
      目标 hp_target(max_hp) 由调用方按角色给（用户 2026-11-01：不用回满。広 有回血，エウレカ 在 HP ≤ 50% 才触发，
      兜底目标是刚过半血）；不给时回满。
    """
    bought: set[int] = set()
    fails: dict[int, int] = {}
    streak = 0
    last_iv = None
    for step in range(max_steps):
        pf = dict(observe() or {})
        if pf.get("interval"):
            last_iv = pf["interval"]
        elif last_iv is not None:
            pf["interval"] = last_iv
            logger.info("轮间 第 %d 步：本帧缺 interval 字段，沿用上一次的", step + 1)
        blocked = {i for i, n in fails.items() if n >= MAX_BUY_FAILS}
        pf = mark_known(pf, bought, blocked)
        d = decide(pf)
        action = d.action
        if action.kind is ActionKind.INTERVAL_END:
            break
        if action.kind is ActionKind.WAIT:  # 决策器暂时给不出动作（字段缺 / 异常）：重新观测，不执行
            streak += 1
            logger.warning("轮间 第 %d 步：决策给 Wait（%s），重新观测", step + 1, (action.args or {}).get("why"))
            if streak >= MAX_FAIL_STREAK:
                break
            continue
        idx = (action.args or {}).get("index")
        if action.kind is ActionKind.INTERVAL_BUY and idx is not None and (idx in bought or idx in blocked):
            logger.warning("轮间 第 %d 步：决策又选了已买 / 买不了的第 %d 格，跳过", step + 1, idx)
            streak += 1
            if streak >= MAX_FAIL_STREAK:
                break
            continue
        if (
            action.kind is ActionKind.INTERVAL_BUY
            and (action.args or {}).get("service") == "recover"
            and "target_hp" not in action.args
        ):
            # 一次回到目标：目标与收尾同一口径（hp_target(上限)，调用方已含决策的模拟目标），不再每 2 点走一遍决策
            _, max_hp, _ = status()
            if max_hp is not None:
                action = Action(
                    action.kind, {**action.args, "target_hp": min(max_hp, hp_target(max_hp) if hp_target else max_hp)}
                )
        res = execute(action, pf)
        if record:
            record(d, res)
        if res.ok:
            streak = 0
            if action.kind is ActionKind.INTERVAL_BUY and idx is not None:
                bought.add(idx)
            if action.kind is ActionKind.REROLL:  # 新货架：已买 / 买不了的标记和沿用的旧货架都作废
                bought.clear()
                fails.clear()
                last_iv = None
            continue
        streak += 1
        if idx is not None:
            fails[idx] = fails.get(idx, 0) + 1
        logger.warning("轮间 第 %d 步 %s 失败（%s），重新观测后继续", step + 1, action, res.error)
        if streak >= MAX_FAIL_STREAK:
            break
    return finish_interval(execute, status, record, hp_target)


def finish_interval(execute, status=screen_status, record=None, hp_target: Callable[[int], int] | None = None) -> bool:
    """离开轮间前把体力回到目标（hp_target(max_hp)，不给则回满；P 够的话），然后 IntervalEnd。返回是否已离开。"""
    hp, max_hp, p = status()
    target = None if max_hp is None else min(max_hp, hp_target(max_hp) if hp_target else max_hp)
    tries = 0
    recover_fails = 0
    limit = (
        -(-(target - hp) // RECOVER_HP) + 2 if hp is not None and target is not None else 0
    )  # 到目标要的次数 + 2 次余量
    while hp is not None and target is not None and hp < target and (p is None or p >= RECOVER_PRICE):
        if tries >= limit or recover_fails >= 2:
            break
        tries += 1
        res = execute(Action(ActionKind.INTERVAL_BUY, {"service": "recover", "target_hp": target}), {})
        if record:
            record(None, res)
        recover_fails = 0 if res.ok else recover_fails + 1
        hp, max_hp, p = status()
    if hp is not None and target is not None and hp < target:
        logger.warning("轮间收尾：体力 %s/%s 没到目标 %s（P %s）", hp, max_hp, target, p)
    else:
        logger.info("轮间收尾：体力 %s/%s（目标 %s），P %s，结束轮间", hp, max_hp, target, p)
    end = execute(Action(ActionKind.INTERVAL_END), {})
    if record:
        record(None, end)
    return end.ok
