# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""三选一「カード除去」（KAA 没有这个操作）：决策给 ExcludeCard{pick_index}。

三选一弹窗（实机帧 tests/fixtures/select_card）：候选卡中心 (220/360/500, 880)，底部中间「受け取る」，
左下「カード除去」(133,1096) 上方角标「あと N回」，右下「再抽選」。第四局实机标定帧（tests/fixtures/exec04/exclude-*.jpg）：
没选中候选时「カード除去」是灰色药丸（S 7 / V 177，「再抽選」白色 V 232），直接点没反应。所以流程是：
1. 点第 pick_index 张候选 → 等「カード除去」变白（V ≥ EXCLUDE_ON_VAL）；「受け取る」变亮但它仍灰 → 失败；
2. 点「カード除去」→ 确认框「スキルカード除去確認」点「除去する」(≈(520,1161))；
3. 出现提示「〜を除去しました」即成功（第四局 calib 20261031-170601 / 170616：被除去的卡带「−」飞走、三选一弹窗先收起，
   6–14 秒后才带着补位的新卡回来，角标 あと3回 → あと2回）。也接受直接回到三选一且候选行有变化；
4. 成功后再等三选一回来（上限 RETURN_TIMEOUT），回来了 page_after=SELECT_CARD，没回来给 None 交主循环重新观测。
每一步存帧到 ops.CALIB_DIR。任何一步超时返回 ok=False 并写明卡在哪一步。所有等待按时间封顶。
"""

import logging
import time
from collections.abc import Callable

from hiro.contracts import Action, ExecutionResult
from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep
from hiro.produce.ops import Run, button_sv, find_text, orange_lit, roi_diff

import numpy as np

logger = logging.getLogger(__name__)

PAGE = "SELECT_CARD"
EXCLUDE_TAP = (133, 1096)
CANDIDATES = ((220, 880), (360, 880), (500, 880))
CARDS_ROI = (140, 810, 440, 140)  # 候选卡行
MAIN_BTN = (260, 1062, 200, 50)  # 「受け取る」：选中候选后变亮
EXCLUDE_PILL = (40, 1080, 150, 34)  # 「カード除去」药丸：灰 V 177，可点时应与「再抽選」同为白色（V 232）
EXCLUDE_ON_VAL = 210
SELECT_PROMPT = ("受け取るスキルカード",)
CONFIRM_TEXTS = ("除去する", "決定", "はい", "OK")
DONE_TEXTS = ("除去しました",)
DIALOG_BUTTONS = (360, 1080, 720, 1240)

LIT_TIMEOUT = 3.0
DONE_TIMEOUT = 8.0  # 点「カード除去」→ 确认框 → 提示「除去しました」
RETURN_TIMEOUT = 15.0  # 提示后三选一带着补位卡回来（实机 6–14 秒）


def exclude_enabled(img: np.ndarray) -> bool:
    return button_sv(img, EXCLUDE_PILL)[1] >= EXCLUDE_ON_VAL


def exclude_card(
    pick_index: int,
    *,
    candidates=None,
    action: Action | None = None,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """除去第 pick_index 张候选。candidates：候选中心坐标（KAA fetch_cards 的 rect 中心），默认三张卡的固定位置。"""
    centers = list(candidates or CANDIDATES)
    act = action or Action.exclude_card(pick_index)
    r = Run(
        act,
        PAGE,
        shoot or device.screenshot,
        click or device.click,
        clock,
        sleep,
        label="カード除去",
        ocr=ocr,
        calib=calib,
    )
    if not 0 <= pick_index < len(centers):
        return r.result(False, f"没有第 {pick_index} 张候选（共 {len(centers)} 张）")
    before = r.shoot()
    r.snap(before, "open")
    base_val = button_sv(before, MAIN_BTN)[1]
    r.tap(centers[pick_index])

    def picked(im):
        return exclude_enabled(im) or orange_lit(im, MAIN_BTN, base_val)

    ok, img = r.until(picked, LIT_TIMEOUT)
    r.snap(img, "picked")
    if not ok:
        return r.result(False, f"点第 {pick_index} 张候选后 {LIT_TIMEOUT:.0f}s 内没选中（「受け取る」没亮）")
    if not exclude_enabled(img):
        return r.result(False, f"已选中第 {pick_index} 张，但「カード除去」仍是灰的（次数用完？）")
    selected = img  # 和选中后的画面比：选中本身就会给卡行加橙色角框，拿点卡前的画面比会误判
    r.tap(EXCLUDE_TAP)

    state = {}

    def done(im):
        boxes = r.ocr(im)
        if find_text(boxes, DONE_TEXTS) is not None:
            state["toast"] = True
            return True
        if find_text(boxes, SELECT_PROMPT) is not None and roi_diff(selected, im, CARDS_ROI) > 12:
            state["back"] = True
            return True
        if btn := find_text(boxes, CONFIRM_TEXTS, region=DIALOG_BUTTONS):
            r.snap(im, "confirm")
            r.tap(btn)
        return False

    ok, img = r.until(done, DONE_TIMEOUT)
    r.snap(img, "after")
    if not ok:
        return r.result(
            False, f"点「カード除去」后 {DONE_TIMEOUT:.0f}s 没看到「除去しました」也没回到三选一", page_after=None
        )
    back = state.get("back", False)
    if not back:
        back, img = r.until(lambda im: find_text(r.ocr(im), SELECT_PROMPT) is not None, RETURN_TIMEOUT)
        r.snap(img, "returned" if back else "not-returned")
    logger.info(
        "カード除去：已除去第 %d 张候选（%.1fs，%s）",
        pick_index,
        r.clock() - r.t0,
        "已回到三选一" if back else "三选一还没回来",
    )
    return r.result(True, page_after=PAGE if back else None)
