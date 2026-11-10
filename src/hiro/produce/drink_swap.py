# 来源：digital-hiro hiro/produce/drink_swap.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""饮料满（所持上限）时丢一瓶已有的、收下新的。丢哪瓶由决策给已有饮料的序号（饮料栏从左到右，0 起）。

两种画面：
- 课程奖励 / 考试奖励等「直接获得」：游戏弹出溢出页（KAA SceneType.PDRINK_MAX）——上面「受け取ったドリンク」、下面
  「手持ちのドリンク」，每行右侧一个勾选框，底部「残す」+「あと N個選択」。勾上的保留、没勾的丢掉；新饮料没勾时点「残す」
  会再弹「報酬スキップ」（PDRINK_MAX_CONFIRM）。KAA 现在是保留旧的、确认跳过新的。resolve_overflow 改成：取消勾选第
  discard 瓶已有饮料、勾上新饮料、点「残す」。依据 KAA 参考图 screenshot_pdrink_max_confirm.png（行位置按 OCR 找，不写死）。
- 差し入れ（SELECT_DRINK 三选一）：满了时「受け取る」是灰的、下面标「Pドリンク所持上限」（实机帧实测按钮
  S 134 / V 186，与没选中时相同），这一页没有丢弃入口。swap_receive 先点饮料栏第 discard 格 →「Pドリンク詳細」→「捨てる」
  （考试里的同一个详情框右上有「捨てる」，fixtures/events/not-acquire-drink-detail-01824.jpg）→ 有确认框就确认 → 该格变空，
  再选第 pick 个候选 →「受け取る」。培育页（非考试）点饮料栏是否同样弹详情框还没有实机帧：每一步都存帧到 ops.CALIB_DIR。
坐标优先用识别给的 center（饮料栏：hiro.vision.items.read_drink_slots；溢出页：read_pdrink_max 的 held / incoming），
没给时按页面取默认值：培育页饮料栏比考试页低 13 像素（识别-01 54a368b 两行比图库得分确认）。
所有等待按时间封顶。
"""

import time
from collections.abc import Callable

import cv2
import numpy as np
from kotonebot import device, logging

from hiro.contracts import Action, ActionKind, ExecutionResult
from hiro.produce.ops import Run, button_sv, find_text, orange_lit

logger = logging.getLogger(__name__)

# 饮料栏：x 两种页面相同；y 考试页 1200（hiro.vision.items.DRINK_CENTERS），培育页（差し入れ / 相談 / 选行动等）低 13 像素
BAR_X = (62, 147, 233, 318)
BAR_Y = {"exam": 1200, "produce": 1213}
BAR_SIZE = 56
SLOT_EMPTY_SAT = 50  # 饮料图标平均饱和度 68–93，空槽 ≤ 45（hiro.vision.items.GREY_SAT_MAX）
# 差し入れ三选一
OFFER_CENTERS = ((222, 880), (360, 880), (498, 880))  # hiro.vision.drinks.DIALOG_SLOTS_X / Y
RECEIVE_BTN, RECEIVE_TAP = (260, 1062, 200, 50), (360, 1092)
BUTTON_TEXT_REGION = (200, 1060, 520, 1125)  # 底部主按钮文字：勾了「受け取らない」时按钮也变成「受け取らない」
SKIP_CHECKBOX = (253, 1006)  # 「受け取らない」勾选框（KAA CheckboxUnchecked 242–265, 997–1016；qualify6 frames/00409 核对）
# Pドリンク詳細
DETAIL_TITLE = ("ドリンク詳細",)
DISCARD_TEXT = ("捨てる",)
DISCARD_TAP = (606, 890)  # 考试详情框里「捨てる」的位置（OCR 找不到时的兜底）
CONFIRM_TEXTS = ("はい", "決定", "OK")  # 丢弃确认框「廃棄確認」：いいえ / はい（q8-r1 calib 03 帧），按文字点底部右侧
CONFIRM_TITLE = ("廃棄確認",)
DIALOG_BUTTONS = (360, 1080, 720, 1240)  # 弹窗底部右侧按钮区（キャンセル 在左，确认在右）
# 溢出页
OVERFLOW_HEADER = ("手持ち",)
KEEP_TEXT = ("残す",)
REMAINING = "あと"
SKIP_TITLE = ("報酬スキップ",)
CANCEL_TEXT = ("キャンセル",)
ROW_TEXT_X = (160, 600)  # 行内效果文字的中心 x 范围（左边是图标，右边是勾选框）
CHECK_X = (625, 660)  # 勾选框
CHECK_ORANGE_MIN = 200  # 勾上：橙色像素 ≈ 580（压暗后仍有）；没勾：0
ROW_MERGE = 45

DETAIL_TIMEOUT = 8.0  # 第二十三局（录像、负载高）：详情框 3 秒后才出
STEP_TIMEOUT = 3.0  # 「捨てる / はい / 受け取る」点了等画面反应；没反应改用真实鼠标（Run.tap_confirmed）
DISCARD_TIMEOUT = 4.0
TOGGLE_TIMEOUT = 2.0
LIT_TIMEOUT = 3.0
LEAVE_TIMEOUT = 5.0


def bar_center(slot: int, slots: list[dict] | None = None, page: str = "produce") -> tuple[int, int] | None:
    """饮料栏第 slot 格的中心：识别给了（read_drink_slots 的 center）就用识别的，否则按页面取默认行。"""
    if not 0 <= slot < len(BAR_X):
        return None
    if slots and slot < len(slots) and slots[slot].get("center"):
        return tuple(int(v) for v in slots[slot]["center"])
    return BAR_X[slot], BAR_Y[page]


def slot_empty(img: np.ndarray, center) -> bool:
    cx, cy = center
    h = BAR_SIZE // 2
    return float(cv2.cvtColor(img[cy - h : cy + h, cx - h : cx + h], cv2.COLOR_BGR2HSV)[..., 1].mean()) < SLOT_EMPTY_SAT


def checked(img: np.ndarray, y: int) -> bool:
    patch = cv2.cvtColor(img[max(y - 17, 0) : y + 17, CHECK_X[0] : CHECK_X[1]], cv2.COLOR_BGR2HSV)
    return int(cv2.inRange(patch, (0, 90, 120), (20, 255, 255)).sum() // 255) >= CHECK_ORANGE_MIN


CHECKBOX_X = (612, 648)  # 勾选框方块（勾上：橙底白勾；没勾：浅灰底白勾），实测 x≈616–642、高 25–27
CHECKBOX_H = (20, 32)
CHECKBOX_Y = (250, 1100)


def checkboxes(img: np.ndarray) -> list[tuple[int, bool]]:
    """溢出页右侧一列勾选框 → [(中心 y, 是否勾上)]，从上到下。按像素找，不靠行文字：
    第十七局 #401（calib 161341）第三瓶已有饮料的行在视口底部被截掉，按效果文字算的行中心 y=1006，勾选框实际在 1043，
    「取消勾选」看成已经没勾而跳过，随后新饮料勾不上（已选满）。"""
    hsv = cv2.cvtColor(img[:, CHECKBOX_X[0] : CHECKBOX_X[1]], cv2.COLOR_BGR2HSV)
    orange = (cv2.inRange(hsv, (0, 90, 120), (20, 255, 255)) > 0).sum(axis=1)
    grey = ((hsv[:, :, 1] < 30) & (hsv[:, :, 2] > 150) & (hsv[:, :, 2] < 225)).sum(axis=1)
    out = []
    for on, arr in ((True, orange), (False, grey)):
        runs: list[list[int]] = []
        for y in range(*CHECKBOX_Y):
            if arr[y] < 12:
                continue
            if runs and y - runs[-1][1] <= 2:
                runs[-1][1] = y
            else:
                runs.append([y, y])
        out += [((a + b) // 2, on) for a, b in runs if CHECKBOX_H[0] <= b - a <= CHECKBOX_H[1]]
    return sorted(out)


def select_left(boxes: list) -> int | None:
    """底部「あと N個選択」的 N（还要再勾几瓶）；读不到 None。"""
    import re

    t = next((t for t, _, _ in boxes if REMAINING in t and "選択" in t), None)
    m = re.search(r"あと\s*(\d+)", (t or "").replace(" ", ""))
    return int(m.group(1)) if m else None


def overflow_rows(boxes: list) -> tuple[list[int], list[int]] | None:
    """溢出页的行：(新饮料各行 y, 已有饮料各行 y)，都从上到下。认不出页面（没有「手持ち」标题）返回 None。
    行按效果文字的 OCR 框定位：中心 x 在 ROW_TEXT_X 内、在「残す」之上，y 相近的框并成一行。"""
    header = find_text(boxes, OVERFLOW_HEADER)
    keep = find_text(boxes, KEEP_TEXT)
    if header is None:
        return None
    bottom = keep[1] - 30 if keep else 1280
    ys = sorted((y1 + y2) // 2 for t, _, (x1, y1, x2, y2) in boxes
                if ROW_TEXT_X[0] <= (x1 + x2) // 2 <= ROW_TEXT_X[1] and abs((y1 + y2) // 2 - header[1]) > 20
                and (y1 + y2) // 2 < bottom and "受け取った" not in t and "手持ち" not in t)  # fmt: skip
    rows: list[int] = []
    for y in ys:
        if rows and y - rows[-1] < ROW_MERGE:
            continue
        rows.append(y)
    return [y for y in rows if y < header[1]], [y for y in rows if y > header[1]]


def _toggle(r: Run, y: int, want: bool, what: str) -> str | None:
    """把 y 行的勾选框点成 want。返回错误说明，成功 None。"""
    img = r.shoot()
    if checked(img, y) == want:
        return None
    r.tap(((CHECK_X[0] + CHECK_X[1]) // 2, y))
    ok, img = r.until(lambda im: checked(im, y) == want, TOGGLE_TIMEOUT)
    r.snap(img, f"toggle-{what}")
    return None if ok else f"{what}的勾选框点了 {TOGGLE_TIMEOUT:.0f}s 没变成{'勾上' if want else '取消'}"


LAST_ROWS: dict = {}  # 最近一次 resolve_overflow 在溢出页上看到的行数 {"new": n, "owned": n}（钩子据此把视口外的已有饮料标成点不到）


def resolve_overflow(
    discard: int | list[int],
    *,
    receive_indexes: list[int] | None = None,
    held: list[dict] | None = None,
    incoming: dict | None = None,
    action: Action | None = None,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """溢出页（PDRINK_MAX）：丢第 discard 瓶（可多瓶）已有饮料，留下新饮料，点「残す」，等离开溢出页。
    receive_indexes：要勾的新饮料（「受け取ったドリンク」区从上往下 0 起，决策 99fa922 febd374）；没给时勾最下面那瓶，还差再补勾。
    held / incoming：识别 read_pdrink_max 的结果，给了就按其 center 的 y 找行（与识别的序号一致），否则自己按 OCR 找行。"""
    r = Run(action or Action(ActionKind.CONTINUE, {"pdrink_discard": discard}), "PDRINK_MAX",
            shoot or device.screenshot, click or device.click, clock, sleep, label="饮料溢出", ocr=ocr, calib=calib)  # fmt: skip
    img = r.shoot()
    r.snap(img, "open")
    boxes = r.ocr(img)
    header = find_text(boxes, OVERFLOW_HEADER)
    if header is None:
        return r.result(False, "认不出饮料溢出页（没读到「手持ちのドリンク」）")
    cbs = checkboxes(img)
    new = [y for y, _ in cbs if y < header[1]]
    owned = [y for y, _ in cbs if y > header[1]]
    if not new or not owned:  # 勾选框没找全（动画中？）：退回按行文字 / 识别给的行
        if held and incoming and incoming.get("center") and all(h.get("center") for h in held):
            new, owned = [int(incoming["center"][1])], [int(h["center"][1]) for h in held]
        else:
            rows = overflow_rows(boxes)
            if rows is None:
                return r.result(False, "认不出饮料溢出页（没读到「手持ちのドリンク」）")
            new, owned = rows
    LAST_ROWS.clear()
    LAST_ROWS.update(new=len(new), owned=len(owned))
    if not new:
        return r.result(False, "溢出页上没找到新饮料那一行（可能要往上滚）")
    discards = [int(discard)] if isinstance(discard, int) else [int(i) for i in discard]
    if bad := [i for i in discards if not 0 <= i < len(owned)]:
        return r.result(False, f"溢出页上只看到 {len(owned)} 瓶已有饮料，没有第 {bad} 瓶")
    receives = [new[-1]] if receive_indexes is None else []
    for j in receive_indexes or []:
        if not 0 <= int(j) < len(new):
            return r.result(False, f"溢出页上只看到 {len(new)} 瓶新饮料，没有第 {j} 瓶")
        receives.append(new[int(j)])
    # 先腾出名额再勾新的：已选满（あと0個選択）时游戏不让多勾（第十七局 #401）。没给 receive_indexes 时勾识别的 incoming = 最下面那瓶
    steps = [(owned[i], False, f"已有第 {i} 瓶") for i in discards] + [(y, True, f"新饮料第 {new.index(y)} 瓶") for y in receives]
    for y, want, what in steps:
        if err := _toggle(r, y, want, what):
            return r.result(False, err)
    discard = discards[0] if len(discards) == 1 else discards
    # 还没选满（第十七局 #63 / #72：「受け取ったドリンク」有两瓶，决策只看了一瓶）：先补勾其余新饮料（从上到下），再补没让丢的已有饮料
    img = r.shoot()
    boxes = r.ocr(img)
    left = select_left(boxes)
    # 补勾顺序：其余新饮料 → 没让丢的已有饮料 → 让丢的已有饮料（从后往前；决策给的数量对不上时宁可少丢一瓶，也要把新饮料留下）
    fill = ([(y, "其余新饮料") for y in new if y not in receives] + [(y, f"已有第 {i} 瓶") for i, y in enumerate(owned) if i not in discards]
            + [(owned[i], f"已有第 {i} 瓶（原定丢弃）") for i in reversed(discards)])  # fmt: skip
    while left and fill:
        y, what = fill.pop(0)
        if checked(img, y):
            continue
        if err := _toggle(r, y, True, what):
            return r.result(False, err)
        logger.info("饮料溢出：还差 %d 瓶，补勾%s", left, what)
        img = r.shoot()
        boxes = r.ocr(img)
        left = select_left(boxes)
    if left:
        return r.result(False, f"勾完后仍是「あと{left}個選択」，没有选满")
    keep = find_text(boxes, KEEP_TEXT)
    if keep is None:
        return r.result(False, "没找到「残す」按钮")
    r.tap(keep)
    state = {}

    def gone(im):
        b = r.ocr(im)
        state["skip"] = find_text(b, SKIP_TITLE) is not None
        return state["skip"] or find_text(b, OVERFLOW_HEADER) is None

    ok, img = r.until(gone, LEAVE_TIMEOUT)
    r.snap(img, "after-keep")
    if state.get("skip"):  # 新饮料没留下：不能确认跳过，退回溢出页
        if cancel := find_text(r.ocr(img), CANCEL_TEXT):
            r.tap(cancel)
        return r.result(False, "点「残す」后弹出「報酬スキップ」：新饮料没被勾上，已取消")
    if not ok:
        return r.result(False, f"点「残す」后 {LEAVE_TIMEOUT:.0f}s 仍在溢出页")
    logger.info("饮料溢出：丢第 %s 瓶已有饮料、留下新饮料（%.1fs）", discard, r.clock() - r.t0)
    return r.result(True, page_after=None)


def _roi_changed(a: np.ndarray, b: np.ndarray, roi, thresh: float = 12.0) -> bool:
    """roi=(x, y, w, h) 区域灰度平均差 ≥ thresh：按钮那块画面变了（离开 / 关闭）。"""
    x, y, w, h = roi
    ga = cv2.cvtColor(a[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean()) >= thresh


def occupied(img: np.ndarray, slots: list[dict] | None = None) -> int:
    """饮料栏非空格数（4 格逐格看饱和度；坐标优先用识别 center）。"""
    return sum(not slot_empty(img, bar_center(i, slots)) for i in range(len(BAR_X)))


def discard_owned(r: Run, slot: int, slots: list[dict] | None = None) -> str | None:
    """点饮料栏第 slot 格 →「Pドリンク詳細」→「捨てる」→「廃棄確認」点「はい」→ 非空格数减 1。返回错误说明，成功 None。

    游戏丢掉一瓶后会把剩下的往左挤，空出来的是最右边那格，不是被丢的那格（q8-r1 calib 20261101-050220：丢第 1 格，
    04 帧空的是第 3 格）。所以按「非空格数比丢之前少 1」判成功。确认框是「廃棄確認 / 本当に〜を捨てますか？」，
    按钮「いいえ / はい」，「はい」在 (521,1161)。
    """
    center = bar_center(slot, slots)
    if center is None:
        return f"饮料栏没有第 {slot} 格"
    if slots and slot < len(slots) and slots[slot].get("state") == "empty":
        return f"饮料栏第 {slot} 格是空的（识别）"
    img = r.shoot()
    if slot_empty(img, center):
        return f"饮料栏第 {slot} 格是空的"
    before = occupied(img, slots)
    r.tap(center)
    pos, img = r.until_text(DISCARD_TEXT + DETAIL_TITLE, DETAIL_TIMEOUT)
    if pos is None and r.real_click is not None and not slot_empty(img, center):
        logger.warning("%s：点饮料栏第 %d 格后 %.0fs 没出详情框，改用真实鼠标再点", r.label, slot, DETAIL_TIMEOUT)
        try:
            r.real_click(*center)
            r.real_taps += 1
            pos, img = r.until_text(DISCARD_TEXT + DETAIL_TITLE, DETAIL_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning("真实鼠标点击失败：%r", e)
    r.snap(img, "drink-detail")
    if pos is None:
        return f"点饮料栏第 {slot} 格后 {DETAIL_TIMEOUT:.0f}s 内没出现「Pドリンク詳細」"
    discard_at = find_text(r.ocr(img), DISCARD_TEXT) or DISCARD_TAP

    def confirm_up(im):
        b = r.ocr(im)
        return find_text(b, CONFIRM_TITLE) is not None or find_text(b, CONFIRM_TEXTS, region=DIALOG_BUTTONS) is not None \
            or find_text(b, DETAIL_TITLE) is None

    ok, img = r.tap_confirmed(discard_at, confirm_up, STEP_TIMEOUT, what="捨てる")
    if not ok:
        return f"点「捨てる」后 {STEP_TIMEOUT:.0f}s 没出「廃棄確認」（真实鼠标也试过：{r.real_taps > 0}）"
    b = r.ocr(img)
    if (yes := find_text(b, CONFIRM_TEXTS, region=DIALOG_BUTTONS)) is not None:
        r.snap(img, "discard-confirm")

        def closed(im):
            return find_text(r.ocr(im), DETAIL_TITLE + CONFIRM_TITLE) is None

        ok, img = r.tap_confirmed(yes, closed, STEP_TIMEOUT, what="はい")
        if not ok:
            return f"点「廃棄確認」的「はい」后 {STEP_TIMEOUT:.0f}s 弹窗没关"

    confirmed = [yes is not None]

    def settled(im):
        b = r.ocr(im)
        dialogs_gone = find_text(b, DETAIL_TITLE + CONFIRM_TITLE) is None
        # 点过「廃棄確認」的「はい」且弹窗都关了就算丢掉：空格的杯子轮廓叠在彩色背景上时饱和度判成「有饮料」
        # （main13 第 7 天：已剩 3 瓶，occupied 仍读 4，误判失败停机；calib 20261102-032839-饮料替换/04）
        if dialogs_gone and (confirmed[0] or occupied(im, slots) == before - 1):
            return True
        if btn := find_text(b, CONFIRM_TEXTS, region=DIALOG_BUTTONS):
            r.snap(im, "discard-confirm")
            r.tap(btn)
            confirmed[0] = True
        return False

    ok, img = r.until(settled, DISCARD_TIMEOUT)
    r.snap(img, "after-discard")
    return None if ok else f"点「捨てる」后 {DISCARD_TIMEOUT:.0f}s 内饮料没少一瓶（之前 {before} 瓶，现在 {occupied(img, slots)} 瓶）"


def swap_receive(
    pick: int,
    discard: int,
    *,
    slots: list[dict] | None = None,
    action: Action | None = None,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """差し入れ三选一（SELECT_DRINK）饮料满时：丢饮料栏第 discard 瓶，再拿第 pick 个候选。slots：read_drink_slots 的结果（用其 center）。"""
    r = Run(action or Action.activate_effect(pick), "SELECT_DRINK", shoot or device.screenshot, click or device.click, clock, sleep,
            label="饮料替换", ocr=ocr, calib=calib)  # fmt: skip
    if click is None:  # 实机：点了没反应时用设备层的兜底点击（PlayCover 是真实鼠标；测试注入了 click 就不会碰）
        from hiro.device import fallback_click

        r.real_click = fallback_click()
    r.snap(r.shoot(), "open")
    if not 0 <= pick < len(OFFER_CENTERS):
        return r.result(False, f"没有第 {pick} 个候选饮料")
    if err := discard_owned(r, discard, slots):
        return r.result(False, err)
    img = r.shoot()
    base_val = button_sv(img, RECEIVE_BTN)[1]
    r.tap(OFFER_CENTERS[pick])
    ok, img = r.until(lambda im: orange_lit(im, RECEIVE_BTN, base_val), LIT_TIMEOUT)
    r.snap(img, "picked")
    if not ok:
        # q15：丢之前这个候选已是选中态，丢完对话框重画后再点一下反而取消了选中（按钮灰着）。再点一次切回选中
        r.tap(OFFER_CENTERS[pick])
        ok, img = r.until(lambda im: orange_lit(im, RECEIVE_BTN, base_val), LIT_TIMEOUT)
        r.snap(img, "picked-retry")
    if not ok:
        return r.result(False, f"已丢掉第 {discard} 瓶，但选第 {pick} 个候选后「受け取る」{LIT_TIMEOUT:.0f}s 内没亮（点了两次）")
    if find_text(r.ocr(img), ("受け取らない",), region=BUTTON_TEXT_REGION) is not None:  # 按下去就是跳过：先取消勾选
        r.tap(SKIP_CHECKBOX)
        ok, img = r.until(lambda im: find_text(r.ocr(im), ("受け取らない",), region=BUTTON_TEXT_REGION) is None, LIT_TIMEOUT)
        r.snap(img, "unchecked-skip")
        if not ok:
            return r.result(False, "底部按钮是「受け取らない」，取消勾选后没变回「受け取る」，不按")
    before_receive = img

    def left(im):
        return _roi_changed(before_receive, im, RECEIVE_BTN)

    ok, img = r.tap_confirmed(RECEIVE_TAP, left, LEAVE_TIMEOUT, what="受け取る")
    r.snap(img, "after-receive")
    if not ok:
        return r.result(False, f"点「受け取る」后 {LEAVE_TIMEOUT:.0f}s 画面没变")
    logger.info("饮料替换：丢第 %d 瓶，收第 %d 个候选（%.1fs）", discard, pick, r.clock() - r.t0)
    return r.result(True, page_after=None)


def discard_then_receive(
    held_index: int,
    pick_slot: int,
    *,
    held: list[dict] | None,
    action: Action | None = None,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """差し入れ / 活動支給 的饮料三选一，饮料满（「Pドリンク所持上限」、「受け取る」灰）时：丢底栏第 held_index 瓶，收第 pick_slot 个候选。

    held：识别读出的底栏已有饮料 [{index, drink_id, name, center}]（格式同 PDRINK_MAX.held）。读不出（None / 空 /
    没有这一瓶 / 没有 center）就一下都不点，返回 ok=False。流程见 swap_receive：点底栏那瓶 →「Pドリンク詳細」→「捨てる」
    →（确认框）→ 该格变空 → 选候选 →（底部按钮若是「受け取らない」先取消勾选）→「受け取る」。每步存 calib 帧。
    证据帧（第六局选拔实机帧）：候选中心 (222/360/498, 880)、底栏 y 1213 与实拍一致。
    """
    act = action or Action.activate_effect(pick_slot)
    entry = next((h for h in held or [] if h.get("index") == held_index), None)
    if entry is None or not entry.get("center") or not 0 <= pick_slot < len(OFFER_CENTERS):
        why = ("识别没给底栏饮料" if not held else f"底栏第 {held_index} 瓶没读出位置" if entry is None or not entry.get("center")
               else f"没有第 {pick_slot} 个候选")  # fmt: skip
        r = Run(act, "SELECT_DRINK", shoot or device.screenshot, click or device.click, clock, sleep, label="饮料替换", ocr=ocr, calib=calib)
        return r.result(False, f"{why}，不点")
    slots = [{"slot": i, "state": "drink", "center": None} for i in range(len(BAR_X))]
    for h in held:
        if h.get("center") and 0 <= h.get("index", -1) < len(slots):
            slots[h["index"]] = {"slot": h["index"], "state": "drink", "center": list(h["center"]), "drink_id": h.get("drink_id")}
    logger.info("饮料替换：丢底栏第 %d 瓶「%s」，收第 %d 个候选", held_index, entry.get("name"), pick_slot)
    return swap_receive(pick_slot, held_index, slots=slots, action=act, shoot=shoot, click=click, clock=clock, sleep=sleep,
                        ocr=ocr, calib=calib)  # fmt: skip
