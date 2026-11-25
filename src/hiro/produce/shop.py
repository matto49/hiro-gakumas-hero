# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""相談商店执行器：execute_shop(action, page_fields) -> ExecutionResult。

一步一个动作，由上层循环「decide → execute → 重新 observe」，直到 ShopEnd。画面与流程按选拔赛数据集
20261029-224103-hif-qualify 第 115–151 帧（手动走过一遍相談）标定，坐标为 720x1280 逻辑坐标：
- 买：点货架格（格子出现橙色选中角框、「交換する」变亮）→ 点「交換する」→「交換確認」弹窗点「交換」
  → 获得动画（可点画面跳过）→ 回到相談，格子盖上「交換済」。
- 删卡 / 强化：点服务按钮 → 牌组选卡页（左下白色「キャンセル」）。按 card_index 选卡要等牌组识别，
  现在点「キャンセル」退回相談并返回 ok=False——不能把选卡页留给主循环：KAA 的 on_skill_card_removal 会 commit(0) 删第一张。
- 离开：点「終了」，无确认弹窗。KAA ConsultContext.commit 会先买第一格，不能沿用。
所有等待按时间封顶；截图、点击、时钟、「交換済」判定都可注入，便于离线测试。
"""

import logging
import time
from collections.abc import Callable

from hiro.contracts import Action, ActionKind, ExecutionResult
from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep
from hiro.produce.ops import Run, button_sv, find_text, orange_lit, roi_diff

import cv2
import numpy as np

logger = logging.getLogger(__name__)

PAGE = "CONSULT"
CELL = 125  # 货架格边长（hiro.vision.shop.CELL）
EXCHANGE_BTN, EXCHANGE_TAP = (250, 1050, 220, 70), (360, 1085)
CONFIRM_BTN, CONFIRM_TAP = (410, 1128, 180, 60), (500, 1158)  # 交換確認 / スキルカード削除 弹窗的确认键
CANCEL_PILL, CANCEL_TAP = (110, 1138, 150, 44), (180, 1160)  # 选卡页 / 弹窗左下的白色「キャンセル」
END_TAP = (640, 1085)
SERVICE_TAP = {"upgrade": (220, 968), "delete": (535, 968)}
SERVICE_USED_REGION = {
    "upgrade": (40, 940, 345, 1012),
    "delete": (360, 940, 690, 1012),
}  # 用过的服务键上盖「交換済」（q12 第 12 天：(202,993)）
SERVICE_USED = "本次相談已用过"
SKIP_TAP = (360, 700)  # 跳过获得动画；x=360 落在货架第 2、3 列之间的空隙，回到相談后误点也不会选中商品
IDLE_SAT, IDLE_VAL = (100, 145), (155, 200)  # 相談页没选中商品时「交換する」的灰色（实测 S 119–136 / V 171–186）
END_PILL = (
    600,
    1062,
    90,
    46,
)  # 「終了」白色按钮：相談主画面 S 13 / V 224；获得动画的星星层压暗到 V 181，弹窗提亮到 254
END_SAT_MAX, END_VAL = 25, (218, 235)
PILL_SAT_MAX, PILL_VAL_MIN = 30, 215  # 白色「キャンセル」：S 0 / V 233；相談页同位置 S 113 / V 185
SELECT_ORANGE_MIN = 400  # 选中角框四角的橙色像素：选中 1359，未选中 ≤ 60

LIT_TIMEOUT = 3.0  # 点商品后等「交換する」变亮
DIALOG_TIMEOUT = 3.0  # 点「交換する」后等确认弹窗
SETTLE_TIMEOUT = 12.0  # 确认后等获得动画结束、回到相談
SKIP_EVERY = 1.0
P_BAND = (300, 90, 460, 140)  # 顶部 P 点数字（x1, y1, x2, y2），实测 270 / 116 / 370 / 420 都读得出
CELL_CHANGED_MIN = 20.0  # 买到的格子盖上「交換済」：与购买前帧差 43；没动的格子 0
END_CONFIRM_TEXTS = ("決定", "本戦へ", "はい", "OK")  # 本戦へ：轮间「インターバル終了確認」
SERVICE_TIMEOUT = 3.0
END_TIMEOUT = 5.0


def consult_idle(img: np.ndarray) -> bool:
    """相談主画面、没有选中商品、没有弹窗 / 动画盖着。"""
    sat, val = button_sv(img, EXCHANGE_BTN)
    end_sat, end_val = button_sv(img, END_PILL)
    return (IDLE_SAT[0] <= sat <= IDLE_SAT[1] and IDLE_VAL[0] <= val <= IDLE_VAL[1]
            and end_sat <= END_SAT_MAX and END_VAL[0] <= end_val <= END_VAL[1] and not cancel_visible(img))  # fmt: skip


def cancel_visible(img: np.ndarray) -> bool:
    sat, val = button_sv(img, CANCEL_PILL)
    return sat <= PILL_SAT_MAX and val >= PILL_VAL_MIN


def _cell(center) -> tuple[int, int, int, int]:
    cx, cy = center
    return int(cx - CELL // 2), int(cy - CELL // 2), CELL, CELL


def selected(img: np.ndarray, center) -> bool:
    """货架格外缘四角有橙色选中角框。"""
    x, y, c, _ = _cell(center)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    total = 0
    for cx, cy in ((x - 14, y - 14), (x + c - 16, y - 14), (x - 14, y + c - 16), (x + c - 16, y + c - 16)):
        patch = hsv[max(cy, 0) : cy + 30, max(cx, 0) : cx + 30]
        total += int(cv2.inRange(patch, (8, 150, 180), (25, 255, 255)).sum() // 255)
    return total >= SELECT_ORANGE_MIN


def ocr_purchased(img: np.ndarray, center) -> bool:
    """格子里有「交換済」（与 hiro.vision.shop.read_consult 同一判据，只读这一格）：白字模板或 OCR 任一认出就算。
    A16：macOS 27.2.1 的 Apple Vision 把「交換済」读成「卒換済」「燃戰K」，只靠 OCR 会把买过的当没买。"""
    from hiro.vision.ocr import ocr_boxes
    from hiro.vision.shop import _purchased_mark

    x, y, c, _ = _cell(center)
    if _purchased_mark(img, x, y):
        return True
    return any("交換済" in t.replace(" ", "") for t, _, _ in ocr_boxes(img[y : y + c, x - 10 : x + c + 10], scale=2))


def _Run(action: Action, shoot, click, clock, sleep, ocr=None, calib: bool = True) -> Run:
    return Run(action, PAGE, shoot, click, clock, sleep, label="相談", ocr=ocr, calib=calib)


P_DIGIT_FIX = str.maketrans(
    {"ろ": "3", "る": "3", "O": "0", "o": "0", "〇": "0", "l": "1", "I": "1", "S": "5", "B": "8"}
)  # 数字 OCR 混淆


def read_p(img: np.ndarray, ocr) -> int | None:
    """顶部 P 点（相談页 x 300–460、y 90–140 的数字）。"""
    for t, _, (x1, y1, x2, y2) in ocr(img):
        v = t.strip().translate(P_DIGIT_FIX)
        if P_BAND[0] <= (x1 + x2) // 2 <= P_BAND[2] and P_BAND[1] <= (y1 + y2) // 2 <= P_BAND[3] and v.isdigit():
            return int(v)
    return None


def bought(before: np.ndarray, img: np.ndarray, center, p_before: int | None, purchased, ocr) -> str | None:
    """这一格是否已换到：「交換済」/ P 点减少 / 格子画面大变（被「交換済」盖住，识别那边表现为置信度骤降）。返回依据，没有 None。
    第四局 17:12 买饮料格：实际买到（离开后 P 420→345、饮料栏多一瓶），但饮料格的「交換済」OCR 没读到，判成了失败。"""
    if purchased(img, center):
        return "交換済"
    p = read_p(img, ocr)
    if p_before is not None and p is not None and p < p_before:
        return f"P {p_before}→{p}"
    diff = roi_diff(before, img, _cell(center))
    if diff >= CELL_CHANGED_MIN:
        return f"格子变化 {diff:.0f}"
    return None


def dialog_mismatch(item: dict, dialog: dict | None) -> str | None:
    """确认框里的商品与要买的不一致时返回说明（读不出 / 没有 id 时不判）。"""
    got = (dialog or {}).get("item") or {}
    for key in ("card_id", "drink_id"):
        want, seen = item.get(key), got.get(key)
        if want and seen and want != seen:
            return f"确认框里是 {got.get('name') or seen}，要买的是 {item.get('name') or want}"
    return None


def _read_dialog(img):
    from hiro.vision.deck import read_confirm_dialog

    return read_confirm_dialog(img)


def _buy(r: Run, item: dict, purchased, confirm_reader=_read_dialog) -> ExecutionResult:
    index, center = item["index"], item.get("center")
    if not center:
        return r.result(False, f"货架第 {index} 格没有坐标")
    if item.get("purchased"):
        return r.result(False, f"货架第 {index} 格已经换过")
    before = r.shoot()
    r.snap(before, "open")
    p_before = read_p(before, r.ocr)
    base_val = button_sv(before, EXCHANGE_BTN)[1]
    r.tap(center)
    w = r.wait(
        before, done=lambda im: selected(im, center) and orange_lit(im, EXCHANGE_BTN, base_val), timeout=LIT_TIMEOUT
    )
    r.snap(w.img, "picked")
    if not w.changed:
        why = "格子已选中但「交換する」没亮（P 点不足？）" if selected(w.img, center) else "格子没有被选中"
        return r.result(False, f"点货架第 {index} 格后 {LIT_TIMEOUT:.0f}s 内{why}")
    r.tap(EXCHANGE_TAP)
    ok, img = r.until(lambda im: cancel_visible(im) and orange_lit(im, CONFIRM_BTN), DIALOG_TIMEOUT)
    r.snap(img, "confirm")
    if not ok:
        return r.result(False, f"点「交換する」后 {DIALOG_TIMEOUT:.0f}s 内没出现「交換確認」弹窗")
    if confirm_reader is not None and (item.get("card_id") or item.get("drink_id")):  # noqa: SIM102
        if why := dialog_mismatch(item, confirm_reader(img)):
            r.tap(CANCEL_TAP)
            return r.result(False, f"货架第 {index} 格：{why}，已取消")
    r.tap(CONFIRM_TAP)
    last_skip = [r.clock()]
    evidence: list[str] = []

    def skip(im):
        if r.clock() - last_skip[0] >= SKIP_EVERY and not cancel_visible(im) and not consult_idle(im):
            if not evidence and last_skip[0] == skip_start:  # 第一次跳过获得动画前存一帧
                r.snap(im, "acquire")
            r.tap(SKIP_TAP)
            last_skip[0] = r.clock()

    skip_start = last_skip[0]

    def done(im):
        if not consult_idle(im):
            return False
        if ev := bought(before, im, center, p_before, purchased, r.ocr):
            evidence.append(ev)
            return True
        return False

    # 弹窗关掉到获得动画开始之间可能有一两帧像相談主画面，所以「回到相談」和「已换到」放在同一个条件里等
    ok, img = r.until(done, SETTLE_TIMEOUT, tick=skip)
    r.snap(img, "back")
    if not ok:
        if consult_idle(img):
            return r.result(False, f"回到相談后货架第 {index} 格没有「交換済」，P 点没减少，格子也没变化")
        return r.result(False, f"确认交換后 {SETTLE_TIMEOUT:.0f}s 内没回到相談主画面", page_after=None)
    logger.info("相談：已换货架第 %d 格（%s，%.1fs）", index, evidence[0], r.clock() - r.t0)
    return r.result(True)


DONE_BUBBLE = ("しました",)  # 「ゆめみごこちを強化しました」「〜を削除しました」消息框
DONE_BUBBLE_REGION = (0, 930, 720, 1120)
DONE_BUBBLE_TAPS = 3


def dismiss_done_bubble(r: Run) -> bool:
    """强化 / 删除后底部的「〜しました」消息框（DMM 上一直挂着、卡牌弹窗也不退，要点一下）：看到文字才点它本身，返回是否点了。
    DMM 第 12 天相談：强化 ゆめみごこち 后它盖了 18s，相談循环连续 3 帧读不出货架，只好结束相談；「終了」第一下也被它吃掉。"""
    b = find_text(r.ocr(r.shoot()), DONE_BUBBLE, region=DONE_BUBBLE_REGION)
    if b is None:
        return False
    r.tap(b)
    return True


def settle_after_op(r: Run) -> tuple[bool, np.ndarray]:
    """强化 / 删除之后等回到空闲相談页；被「〜しました」消息框挡着就点掉它（最多 DONE_BUBBLE_TAPS 次）。"""
    back, img = r.until(consult_idle, SETTLE_TIMEOUT / 3)
    for _ in range(DONE_BUBBLE_TAPS):
        if back or not dismiss_done_bubble(r):
            break
        back, img = r.until(consult_idle, SETTLE_TIMEOUT / 3)
    if not back:
        back, img = r.until(consult_idle, SETTLE_TIMEOUT / 3)
    return back, img


def _service(
    r: Run, name: str, services: dict, card_id: str | None = None, choose_card=None, deck_size: int | None = None
) -> ExecutionResult:
    """相談「強化 / 削除」：点服务键打开选卡页 → 读卡格 → card_id（动作给的，或 choose_card(kind, cards) 让决策选）
    → 点卡、核对选中 →「強化 / 削除」→ 确认框 →「〜しました」→ 回到相談。走 card_ops._card_op；
    选不出 / 找不到 / 选中不符时点「キャンセル」退回相談，返回 ok=False（error 写原因）。"""
    from hiro.produce.card_ops import _card_op

    label = {"delete": "削除", "upgrade": "強化"}[name]
    kind = {"delete": "remove", "upgrade": "enhance"}[name]
    info = (services or {}).get(name) or {}
    if info.get("available") is False:
        return r.result(False, f"「{label}」不可用（识别为灰色）")
    if find_text(r.ocr(r.shoot()), ("交換済",), region=SERVICE_USED_REGION[name]) is not None:
        return r.result(False, f"「{label}」{SERVICE_USED}（按钮上有「交換済」），不点")
    r.tap(SERVICE_TAP[name])
    ok, img = r.until(cancel_visible, SERVICE_TIMEOUT)
    r.snap(img, f"{kind}-dialog")
    if not ok:
        return r.result(False, f"点「{label}」后 {SERVICE_TIMEOUT:.0f}s 内没打开选卡页（P 点不足？）")
    choose = (lambda cards: choose_card(kind, cards)) if choose_card is not None else None
    done = _card_op(kind, card_id, shoot=r.shoot, click=r.click_fn, clock=r.clock, sleep=r.sleep, ocr=r.ocr,
                    calib=r.calib is not None, choose=choose, expected_size=deck_size)  # fmt: skip
    back, img = settle_after_op(r)
    r.snap(img, f"{kind}-back")
    if not done:
        return r.result(
            False,
            f"「{label}」没做成（见日志：没给 / 没找到 card_id 或选中不符，已取消）",
            page_after=PAGE if back else None,
        )
    return r.result(True, page_after=PAGE if back else None)


SHOP_MARKS = (
    "選んでください",
    "交換するもの",
    "強化や削除",
    "交換する",
)  # 相談 / 轮间商店页自己的文字：提示条、说明行、「交換する」按钮
END_IDLE_WAIT = 6.0  # 点「終了」前等提示条（「〜を削除しました」）/ 强化演出 / 支援卡横幅退掉的上限
END_LEFT_FRAMES = 2  # 连续这么多帧看不到商店页文字（也没有确认框）才算离开
END_RETAPS = 2


def shop_shown(boxes) -> bool:
    """画面上还有商店页（相談 / 轮间）自己的文字。提示条、横幅、强化演出都盖不全这几处；相談结束后的角色场景、loading、考试画面都没有。"""
    return any(k in t.replace(" ", "") for t, _, _ in boxes for k in SHOP_MARKS)


def tap_end(r: Run) -> ExecutionResult:
    """点「終了」、过确认框，画面不再是空闲商店页就返回成功。轮间用这个（离开与否由它自己等左上「インターバル」标题消失来判）：
    轮间画面有「〜選んでください」，相談的严格判定（_end）在轮间永远不成立，会在「本戦へ」淡出时重点「終了」。"""
    r.snap(r.shoot(), "end-open")
    r.tap(END_TAP)
    last = [r.clock() - SKIP_EVERY]

    def confirm(im):
        if cancel_visible(im) and r.clock() - last[0] >= SKIP_EVERY:
            r.snap(im, "end-confirm")
            r.tap(find_text(r.ocr(im), END_CONFIRM_TEXTS, region=(360, 1080, 720, 1240)) or CONFIRM_TAP)
            last[0] = r.clock()

    ok, img = r.until(lambda im: not consult_idle(im) and not cancel_visible(im), END_TIMEOUT, tick=confirm)
    r.snap(img, "end")
    if not ok:
        return r.result(False, f"点「終了」后 {END_TIMEOUT:.0f}s 内画面没变")
    return r.result(True, page_after=None)


def _end(r: Run) -> ExecutionResult:
    """点「終了」。还有没换的东西时会弹「相談終了確認」（第四局 17:12），点「決定」。
    成功要正面确认：连续 END_LEFT_FRAMES 帧既没有商店页文字、也没有确认框。以前「不是空闲相談页、没有取消框」就算离开：
    删卡 / 强化后底部弹「〜を削除しました」提示条时第一帧就满足（Mac 第 33 局选拔 0.062s / 0.035s，DMM 0.047s），
    实际还在相談，决策器从「第 1 步」重开相談；画面真的走了以后 state 又停在 CONSULT，在 loading / 考试画面上接着点（DMM 实机）。"""
    ok, _ = r.until(consult_idle, END_IDLE_WAIT)  # 先等提示条 / 演出退掉，盖着的时候点「終了」可能不生效
    if not ok and dismiss_done_bubble(r):  # 「〜しました」消息框不会自己退
        r.until(consult_idle, END_IDLE_WAIT)
    im0 = r.shoot()
    r.snap(im0, "end-open")
    if not cancel_visible(im0) and not shop_shown(r.ocr(im0)):
        # 已经不在商店页（离开后的相談人物画面仍被分类成 CONSULT，DMM 第 12 天）：不在人物画面上盲点「終了」的位置
        im1 = r.shoot()
        if not cancel_visible(im1) and not shop_shown(r.ocr(im1)):
            return r.result(True, page_after=None)
    for attempt in range(1 + END_RETAPS):
        r.tap(END_TAP)
        last = [r.clock() - SKIP_EVERY]
        streak = [0]

        def confirm(im):
            if cancel_visible(im) and r.clock() - last[0] >= SKIP_EVERY:  # noqa: B023
                r.snap(im, "end-confirm")
                r.tap(find_text(r.ocr(im), END_CONFIRM_TEXTS, region=(360, 1080, 720, 1240)) or CONFIRM_TAP)
                last[0] = r.clock()  # noqa: B023

        def left(im):
            gone = not cancel_visible(im) and not shop_shown(r.ocr(im))
            streak[0] = streak[0] + 1 if gone else 0  # noqa: B023
            return streak[0] >= END_LEFT_FRAMES  # noqa: B023

        ok, img = r.until(left, END_TIMEOUT, tick=confirm)
        r.snap(img, "end")
        if ok:
            return r.result(True, page_after=None)
        if not consult_idle(img):
            break  # 不在空闲相談页（演出 / 弹窗没退）：不盲点第二下
        logger.info("点「終了」后 %.0fs 画面还在相談（第 %d 次），再点一次", END_TIMEOUT, attempt + 1)
    return r.result(False, f"点「終了」后没离开相談（{1 + END_RETAPS} 次内，商店页文字一直在）")


# ---------------------------------------------------------------- 刷新货架（リフレッシュ）
# 左下「リフレッシュ」：相談（选拔 / 本战）上方有「あと N回」角标（免费、有次数，q12 / 第五局帧 y≈1085）；
# 轮间下方标价「10」（P，第十七局 calib 20261102-172120-轮间/01-open，y≈1066）。点了之后有没有确认框、
# 刷新后货架怎么动画、多次刷新会不会涨价，离线帧里都没有：确认框出现就按 REROLL_CONFIRM 点；出现ジュエル一律点キャンセル。
REROLL_REGION = (0, 1020, 260, 1140)
REROLL_TAP = {"consult": (108, 1085), "interval": (108, 1066)}
REROLL_CONFIRM = ("リフレッシュする", "リフレッシュ", "更新する", "決定", "はい", "OK")
REROLL_DIALOG_BUTTONS = (300, 1080, 720, 1240)  # 确认框右下按钮（避开左下的「リフレッシュ」本身）
REROLL_CANCEL_TAP = (220, 1160)
REROLL_OK_TAP = (520, 1160)  # 确认框右下（左キャンセル、右确认，与其他确认框同版式）
# 确认框标题（总控 2026-11-02 设置页确认，两个确认都开着）：轮间「リフレッシュ実行確認」、相談「相談リフレッシュ確認」
REROLL_TITLES = ("リフレッシュ実行確認", "相談リフレッシュ確認", "リフレッシュ確認")
JEWEL_WORDS = ("ジュエル",)
SHELF_REGION = (30, 470, 690, 870)  # 货架两行（相談 / 轮间都在这一片）
SHELF_CHANGED_MIN = 12.0  # 货架区灰度平均差：换了一批商品远大于这个；同一货架两帧 < 3
SHELF_CHANGED_WEAK = 6.0  # 刷新按钮那块也变了时，货架差过这个就算换了（同一货架两帧 < 3）


def refresh_badge(boxes) -> str:
    """左下「あと N回」+「リフレッシュ」（轮间是价格）那一块的文字，用来看刷新后它变没变。"""
    x1, y1, x2, y2 = REROLL_REGION
    return "".join(
        t.replace(" ", "")
        for t, _, (bx1, by1, bx2, by2) in boxes
        if x1 <= (bx1 + bx2) / 2 <= x2 and y1 <= (by1 + by2) / 2 <= y2
    )


REROLL_TIMEOUT = 10.0
REROLL_GRACE = 10.0  # 点过确认框后还没换：服务器慢时再多等这么久


def shelf_diff(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1, x2, y2 = SHELF_REGION
    ga = cv2.cvtColor(a[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean())


REROLL_CHANGED_FRAMES = 2  # 连续这么多帧（主画面、无确认框）都与刷新前明显不同才算换了货架


def reroll_dialog(boxes) -> bool:
    """画面上挂着刷新确认框（「リフレッシュ確認」/「リフレッシュ実行確認」/「相談リフレッシュ確認」）。"""
    return find_text(boxes, REROLL_TITLES) is not None


def cancel_reroll_dialog(r: Run) -> bool | None:
    """残留的刷新确认框（第十八局本战轮间：刷新的确认框晚弹出来没人处理，挡住之后每一步，IntervalEnd 还把它当成「已不在轮间」）：
    点「キャンセル」。不点「更新する」：没有刷新决策时不花 P、不换掉决策已规划好的货架（要刷新决策会再给 Reroll）。
    返回 None = 没有确认框；True / False = 是否已关掉。"""
    img = r.shoot()
    boxes = r.ocr(img)
    if not reroll_dialog(boxes):
        return None
    r.snap(img, "stale-reroll-dialog")
    r.tap(find_text(boxes, ("キャンセル",), region=(0, 1080, 400, 1240)) or REROLL_CANCEL_TAP)
    logger.warning("画面上残留刷新确认框：点「キャンセル」")
    ok, _ = r.until(lambda im: not reroll_dialog(r.ocr(im)), DIALOG_TIMEOUT)
    return ok


def _reroll(r: Run, scope: str, info: dict | None) -> ExecutionResult:
    """点「リフレッシュ」刷新货架；确认框（「リフレッシュ確認」，按钮 キャンセル / 更新する）按「更新する」，出现ジュエル点キャンセル。
    成功 = 回到主画面、没有确认框、货架停稳（相邻两帧几乎不变）且与刷新前明显不同。P 只记日志、不当证据：
    第十八局本战轮间第 1 次在翻页动画帧上就判了成功（整屏在变，货架差 58.7，P 还没变），第 2 次把 335 读成 355、
    确认框还没弹出就以「P 减少」判了成功，随后弹出的确认框没人处理。"""
    if (info or {}).get("available") is False:
        return r.result(False, "「リフレッシュ」不可用（次数用完 / P 不够）")
    before = r.shoot()
    boxes0 = r.ocr(before)
    p0 = read_p(before, r.ocr)
    r.snap(before, "reroll-open")
    if not reroll_dialog(boxes0):  # 确认框已经挂着（上一次点的晚弹出来）：不再点按钮，直接按确认框处理
        btn = find_text(boxes0, ("リフレッ",), region=REROLL_REGION)  # RapidOCR 常把ュ读成ユ
        if scope == "consult" and btn is None and find_text(boxes0, ("あと",), region=REROLL_REGION) is None:
            # 相談次数用完时按钮整块消失：盲点 REROLL_TAP 会点到空地（DMM 第 12 天相談把 rerolls_left 当成还有）
            return r.result(False, "画面上没有「リフレッシュ」按钮（相談刷新次数已用完）")
        r.tap(btn or REROLL_TAP[scope])
    state = {"jewel": False, "changed": 0, "confirmed": False}
    last = [r.clock() - SKIP_EVERY]
    badge0 = refresh_badge(boxes0)

    def tick(im):
        if r.clock() - last[0] < SKIP_EVERY:
            return
        boxes = r.ocr(im)
        if find_text(boxes, JEWEL_WORDS) is not None:
            r.snap(im, "reroll-jewel")
            state["jewel"] = True
            r.tap(find_text(boxes, ("キャンセル",), region=(0, 1080, 400, 1240)) or REROLL_CANCEL_TAP)
            logger.warning("刷新货架：确认框要花ジュエル，点「キャンセル」")
        elif b := find_text(boxes, REROLL_CONFIRM, region=REROLL_DIALOG_BUTTONS):
            r.snap(im, "reroll-confirm")
            r.tap(b)
            state["confirmed"] = True
        elif reroll_dialog(boxes):  # 认出确认框标题、按钮文字没读到：点右下确认位置
            r.snap(im, "reroll-confirm-title")
            r.tap(REROLL_OK_TAP)
            state["confirmed"] = True
        last[0] = r.clock()

    def done(im):
        if not consult_idle(im) or reroll_dialog(r.ocr(im)):
            state["changed"] = 0
            return False
        if state["jewel"]:
            return True  # 已取消、回到主画面
        # 连续两帧都与刷新前明显不同才算换了（单帧可能是动画）。不要求相邻两帧一样：
        # 第十九局本战轮间第 2 次刷新成功（P 229→219、货架差 19.1），新货架上「NEW」标记一直在动，按「停稳」判了 10s 没换
        diff = shelf_diff(before, im)
        # 新货架和旧货架长得像时差值不大（DMM 第 12 天相談真的换了货架，差 11.2 < 12，判了「没换」）：
        # 左下「あと N回 / リフレッシュ」变了（次数减少、按钮消失、价格变化）时，差值过 SHELF_CHANGED_WEAK 就算换了
        weak = diff >= SHELF_CHANGED_WEAK and refresh_badge(r.ocr(im)) != badge0
        state["changed"] = state["changed"] + 1 if diff >= SHELF_CHANGED_MIN or weak else 0
        return state["changed"] >= REROLL_CHANGED_FRAMES

    ok, img = r.until(done, REROLL_TIMEOUT, tick=tick)
    if not ok and state["confirmed"] and not state["jewel"]:
        # 点过确认、服务器慢：货架晚换（z5 11-19 02:42：刚出过通信エラー，P 544→534、刷新价 10→20 都在 10 秒后才出来，判了三次「没换」）
        logger.info("刷新货架（%s）：确认后 %.0fs 货架还没换，再等 %.0fs", scope, REROLL_TIMEOUT, REROLL_GRACE)
        ok, img = r.until(done, REROLL_GRACE, tick=tick)
    r.snap(img, "reroll-done" if ok else "reroll-not-done")
    if state["jewel"]:
        return r.result(False, "刷新要花ジュエル，已取消", page_after=PAGE if consult_idle(img) else None)
    if not ok:
        if reroll_dialog(r.ocr(img)):  # 超时还挂着确认框：取消，不留给后面的步骤
            cancel_reroll_dialog(r)
        waited = REROLL_TIMEOUT + (REROLL_GRACE if state["confirmed"] else 0)
        return r.result(False, f"点「リフレッシュ」后 {waited:.0f}s 内货架没换（确认框{'已' if state['confirmed'] else '没'}点，P {p0}）",
                        page_after=PAGE if consult_idle(r.shoot()) else None)  # fmt: skip
    logger.info(
        "刷新货架（%s）：P %s → %s，货架差 %.1f（%.1fs）",
        scope,
        p0,
        read_p(img, r.ocr),
        shelf_diff(before, img),
        r.clock() - r.t0,
    )
    return r.result(True)


def clear_comm_error(r: Run) -> ExecutionResult | None:
    """动作前先看有没有「通信エラー」框（第二十二局轮间：挂着它每步都「8s 内主画面没出现」，最后被当成「已不在轮间」）。
    有标题条才 OCR（约 20ms）。リトライ：点了返回失败结果（调用方重新观测再来）；只能回标题：点閉じる、抛 CommErrorToTitle。"""
    from hiro.loop import comm_error
    from hiro.vision.pages import dialog_header

    img = r.shoot()
    try:
        if dialog_header(img) is None:
            return None
    except Exception:
        return None
    boxes = r.ocr(img)
    if comm_error.comm_error(boxes) is None:
        return None
    r.snap(img, "comm-error")
    done = comm_error.handle(boxes, lambda x, y: r.tap((x, y)), sleep=r.sleep)
    return r.result(False, "通信エラー：已点リトライ，重新观测" if done == "retry" else "通信エラー：没读到按钮，没点")


def execute_shop(
    action: Action,
    page_fields: dict,
    *,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
    purchased: Callable[[np.ndarray, list], bool] = ocr_purchased,
    ocr=None,
    calib: bool = True,
    choose_card: Callable[[str, list], str | None] | None = None,
    deck_size: int | None = None,
) -> ExecutionResult:
    """执行一个相談动作。deck_size：状态机记账的牌组张数（选卡对话框多屏拼接核对用，拼出张数差太多就取消、不交决策；None = 不核对）。page_fields 是识别给的 CONSULT 字段（shelf / services）。ok=True 表示观察到生效。
    强化 / 删卡：动作 args 带 card_id 就按它点；否则打开选卡页后把卡格交给 choose_card("enhance"|"remove", cards) 选
    （cards 格式同 read_card_dialog / SKILL_CARD_REMOVAL.cards）。两者都没有就取消。"""
    r = _Run(action, shoot or device.screenshot, click or device.click, clock, sleep, ocr=ocr, calib=calib)
    args = action.args or {}
    if (comm := clear_comm_error(r)) is not None:
        return comm
    if action.kind is not ActionKind.REROLL:
        cancel_reroll_dialog(r)  # 「相談リフレッシュ確認」挂着：取消，同轮间
    if action.kind is ActionKind.SHOP_END:
        return _end(r)
    if action.kind is ActionKind.REROLL:  # args {"scope": "consult"}：相談左下「リフレッシュ」（あと N回）
        left = page_fields.get("rerolls_left")
        return _reroll(r, "consult", {"available": left > 0 if isinstance(left, int) else None})
    if action.kind is ActionKind.SHOP_UPGRADE_CARD or (
        action.kind is ActionKind.SHOP_BUY and args.get("service") == "upgrade"
    ):
        return _service(r, "upgrade", page_fields.get("services"), args.get("card_id"), choose_card, deck_size)
    if action.kind is ActionKind.SHOP_BUY and args.get("service") == "delete":
        return _service(r, "delete", page_fields.get("services"), args.get("card_id"), choose_card, deck_size)
    if action.kind is ActionKind.SHOP_BUY and "index" in args:
        item = next((s for s in page_fields.get("shelf") or [] if s.get("index") == args["index"]), None)
        if item is None:
            return r.result(False, f"货架里没有第 {args['index']} 格")
        return _buy(r, item, purchased)
    return r.result(False, f"相談执行器不支持 {action}")
