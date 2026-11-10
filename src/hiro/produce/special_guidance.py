# 来源：digital-hiro hiro/produce/special_guidance.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""「特別指導」（技能卡定制）执行。KAA 的 ProduceAction 里没有这一项，选行动页识别给的是 options 里 option="special_guidance"
的中心（第三局 r10 第 19 天：(442, 1009)，与「相談」并排）。

画面按 q10 第 19 天实机全分辨率帧（reports/live/special-guidance/q10-*.png；tests/fixtures/special_guidance/）：
- 选卡页：4 列卡格（未滚动时 x 120/280/440/600、首行 y≈693、行距约 160，可滚动；位置优先用识别 cards[].center），
  底部「カスタマイズ可能スキルカード あと N枚」、「カスタマイズ」(360,1083)、「終了」(653,1083)。
- 选卡 →「カスタマイズ」→ 定制菜单页：上半是卡详情（定制前 / 后），中间「カスタマイズメニュー」下 3 个横排色块
  （x 143 / 364 / 578，名字 y≈837，价格 y≈884，上方「あと N回」，做满显示「カスタマイズ上限」），底部「一覧に戻る」(213,1083)、
  「合計あと N回」+「実行する」(497,1083)、右侧「終了」(653,977)。
- 色块 →「実行する」→「カスタマイズ実行確認」点「実行する」(500,1160) →「〜をカスタマイズしました」点一下 → 回到菜单
  （同一项可以连续做多级，例如 紙吹雪 元気 110→130→170%）。
- 退出：「終了」；还有可定制次数时弹「特別指導終了確認」→「終了する」(500,1160)。
所有等待按时间封顶，每步存 calib 帧。
"""

import re
import time
from collections.abc import Callable

import numpy as np
from kotonebot import device, logging

from hiro.contracts import Action, ActionKind, ExecutionResult
from hiro.produce.ops import Run, button_sv, find_text, orange_lit

logger = logging.getLogger(__name__)

PAGE = "SPECIAL_GUIDANCE"
OPTION_CENTER = (442, 1009)  # r10 第 19 天识别值；有 page_fields.options 时以识别为准
TITLE = ("特別指導",)
SELECTED_HINT = ("カスタマイズできます", "スキルカードをカスタマイズ")  # 选中「特別指導」后浮出的说明（等第二下确认）
GRID_X0, GRID_Y0, GRID_STEP, GRID_COLS, GRID_ROWS = 140, 458, 147, 4, 3
SG_COLS, SG_ROW0, SG_PITCH = (119, 279, 438, 599), 707, 160  # 选卡页卡格（刚进来未滚动；识别 352f383 实测中心）
CUSTOMIZE_TAP, BACK_TAP, EXEC_TAP = (360, 1083), (213, 1083), (497, 1083)
CUSTOMIZE_BTN_ROI, EXEC_BTN_ROI = (260, 1058, 200, 50), (420, 1058, 170, 50)  # 橙色按钮亮 = 可点
EXEC_CONFIRM_TAP, END_CONFIRM_TAP = (500, 1160), (500, 1160)
MENU_COLS, MENU_BLOCK_Y = (143, 364, 578), 845
TOAST_TAP = (360, 1000)  # 「〜をカスタマイズしました」提示
BOTTOM_RIGHT = (360, 1110, 720, 1220)  # 弹窗底部右侧按钮

ENTER_TIMEOUT = 5.0
STEP_TIMEOUT = 3.0
DONE_TIMEOUT = 8.0


def grid_center(index: int) -> tuple[int, int] | None:
    row, col = divmod(index, GRID_COLS)
    if index < 0 or row >= GRID_ROWS:
        return None
    return GRID_X0 + GRID_STEP * col, GRID_Y0 + GRID_STEP * row


def option_center(page_fields: dict | None) -> tuple[int, int]:
    for o in (page_fields or {}).get("options") or []:
        if o.get("option") == "special_guidance" and o.get("center"):
            return tuple(o["center"])
    return OPTION_CENTER


def _runner(action, shoot, click, clock, sleep, ocr, calib) -> Run:
    return Run(action, PAGE, shoot or device.screenshot, click or device.click, clock, sleep, label="特別指導", ocr=ocr, calib=calib)


def enter(
    page_fields: dict | None = None,
    *,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """选行动页进入特別指導：点一次选中、再点一次确认（与 KAA 选行动相同），直到按钮下方的「特別指導」标签消失。
    （「行動を選択してください」字太淡，OCR 常丢字，不用它判断。）"""
    act = Action(ActionKind.PRODUCE_NEXT, {"step_type": "SpecialGuidance", "option": "special_guidance"})
    r = _runner(act, shoot, click, clock, sleep, ocr, calib)
    r.page = "ACTION_SELECT"
    pos = option_center(page_fields)
    label = (pos[0] - 100, pos[1] + 20, pos[0] + 100, pos[1] + 110)  # 按钮下方的「特別指導」标签（r10：(440, 1080)）
    img = r.shoot()
    r.snap(img, "action-select")
    for attempt in range(2):
        r.tap(pos)
        ok, img = r.until(lambda im: find_text(r.ocr(im), TITLE, region=label) is None, ENTER_TIMEOUT / 2)
        if ok:
            break
    if not ok:
        # DMM dmm2 选拔第 19 天：两下里丢了一下，画面停在「特別指導」已选中、等确认（上方浮出「…カスタマイズできます」说明），
        # 判「没进去」后改休息，之后被别的点击确认进了定制页、空等 183s 停机。看得到选中说明就再点一下确认；看不到（第二下多半
        # 已经确认、只是切换慢）就不点，多等一个 ENTER_TIMEOUT
        if find_text(r.ocr(img), SELECTED_HINT) is not None:
            r.snap(img, "selected-not-confirmed")
            r.tap(pos)
        ok, img = r.until(lambda im: find_text(r.ocr(im), TITLE, region=label) is None, ENTER_TIMEOUT)
    r.snap(img, "entered")
    if not ok:
        return r.result(False, f"点「特別指導」{len(r.taps)} 次后仍在选行动页")
    logger.info("已进入特別指導（%.1fs）", r.clock() - r.t0)
    return r.result(True, page_after=PAGE)


def sg_grid_center(index: int) -> tuple[int, int]:
    """选卡页第 index 张（未滚动时的位置；识别 cards 带 center 时优先用它）。"""
    row, col = divmod(index, len(SG_COLS))
    return SG_COLS[col], SG_ROW0 + SG_PITCH * row


def on_grid(boxes) -> bool:
    return find_text(boxes, ("カスタマイズ可能スキルカード",)) is not None or find_text(boxes, ("カスタマイズするスキルカード",)) is not None


def on_menu(boxes) -> bool:
    return find_text(boxes, ("カスタマイズメニュー",)) is not None and find_text(boxes, ("一覧に戻る",)) is not None


TOAST = ("カスタマイズしまし", "をカスタマイズ", "しました")  # OCR 断行不定：q12「…カスタマイズしまし / た」，q13「…をカスタマイズ / しました」
TOAST_REGION = (0, 920, 720, 1130)  # 提示框所在的屏幕下部（菜单色块在 y≈845，「カスタマイズ上限」不含这些词）


def has_toast(boxes) -> bool:
    return find_text(boxes, TOAST, region=TOAST_REGION) is not None


def on_page(boxes) -> bool:
    """还在特別指導的定制页（选卡页 / 菜单页 / 菜单页叠着「〜をカスタマイズしました」提示，提示会挡住「一覧に戻る」）。"""
    return on_grid(boxes) or on_menu(boxes) or has_toast(boxes) or find_text(boxes, ("カスタマイズメニュー",)) is not None


def _to_grid(r: Run, tries: int = 6) -> bool:
    """回到选卡页：有提示先点掉，在菜单页点「一覧に戻る」，每次点完等画面变化。q12 第 19 天：提示比「回到菜单」判定晚出现，
    「一覧に戻る」被吞，提示一直挂着，之后每次定制都报「不在选卡页」空转。"""
    for _ in range(tries):
        img = r.shoot()
        boxes = r.ocr(img)
        if on_grid(boxes) and not has_toast(boxes):
            return True
        if has_toast(boxes):
            r.snap(img, "toast")
            r.tap(TOAST_TAP)
        elif on_page(boxes):
            r.tap(BACK_TAP)
        else:
            r.snap(img, "to-grid-lost")
            return False
        r.sleep(1.2)
    return False


def menu_items(boxes) -> list[dict]:
    """定制菜单页的色块（从左到右）：[{item_index, name, price, left, maxed, center}]。读不到的项为 None。"""
    out = []
    for i, x in enumerate(MENU_COLS):
        col = [(t.replace(" ", ""), (b[1] + b[3]) // 2) for t, _, b in boxes if abs((b[0] + b[2]) // 2 - x) <= 95]
        name = next((t for t, y in col if 815 <= y <= 860 and not t.isdigit() and t not in ("+", "P")), None)
        price = next((int(t) for t, y in col if 870 <= y <= 900 and t.isdigit()), None)
        left = next((int(m.group(1)) for t, y in col if 785 <= y <= 812 and (m := re.search(r"あと(\d+)回", t))), None)
        maxed = any("上限" in t for t, y in col if 895 <= y <= 920)
        out.append({"item_index": i, "name": name, "price": price, "left": left, "maxed": maxed, "center": [x, MENU_BLOCK_Y]})
    return out


def _wait(r: Run, check, timeout=STEP_TIMEOUT):
    """轮询 OCR 直到 check(boxes, img) 为真。返回 (结果, 最后一帧, boxes)。"""
    got = {}

    def ok(im):
        got["b"] = r.ocr(im)
        return check(got["b"], im)

    res, img = r.until(ok, timeout)
    return res, img, got.get("b", [])


_DASHES = str.maketrans({"一": "ー", "－": "ー", "-": "ー", "―": "ー", "ｰ": "ー"})


def _name_in(boxes, name: str, region) -> bool:
    """卡名在不在区域里：长音符号统一后再比（RapidOCR 常把「ー」读成「一」，DMM dmm2 第 19 天「えいえいおー+」对不上，退回一览）。"""
    want = name.replace(" ", "").translate(_DASHES)
    for text, _, (x1, y1, x2, y2) in boxes:
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if region[0] <= cx <= region[2] and region[1] <= cy <= region[3] and want in text.replace(" ", "").translate(_DASHES):
            return True
    return False


LEAVE_RETAP_S = 1.5  # 点了「終了」这么久还在定制页、按钮还在：重新找位置再点（最多 LEAVE_RETAPS 次）
LEAVE_RETAPS = 3


def _leave(r: Run) -> bool:
    """点「終了」（菜单页 (653,977) / 选卡页 (653,1083)，按 OCR 找），有「特別指導終了確認」就点「終了する」，直到离开定制页。"""
    from hiro.produce.ops import find_text as ft

    img = r.shoot()
    boxes = r.ocr(img)
    if not on_page(boxes):
        return True
    for _ in range(4):  # 提示挡住菜单页的「終了」，先点掉
        if not has_toast(boxes):
            break
        r.tap(TOAST_TAP)
        r.sleep(1.2)
        img = r.shoot()
        boxes = r.ocr(img)
    end = ft(boxes, ("終了",), region=(560, 940, 720, 1120))
    if end is None:
        r.snap(img, "leave-no-button")
        return False
    r.tap(end)
    last = [r.clock()]
    retaps = [0]

    def gone(bx, im):
        if (c := ft(bx, ("終了する",), region=BOTTOM_RIGHT)) is not None and r.clock() - last[0] >= 0.8:
            r.snap(im, "leave-confirm")
            r.tap(c)
            last[0] = r.clock()
            return False
        if has_toast(bx) and r.clock() - last[0] >= 1.0:
            r.tap(TOAST_TAP)
            last[0] = r.clock()
            return False
        if on_page(bx) and ft(bx, ("終了確認",)) is None and r.clock() - last[0] >= LEAVE_RETAP_S and retaps[0] < LEAVE_RETAPS:
            # 点的时候画面还在切（菜单页「一覧に戻る」后立刻找「終了」，拿到的是菜单页位置 (653,977)，切回一览后那里是卡格，
            # DMM dmm2 点成了选中一张卡）：按当前画面重新找「終了」再点
            if (e := ft(bx, ("終了",), region=(560, 940, 720, 1120))) is not None:
                r.tap(e)
                last[0] = r.clock()
                retaps[0] += 1
                return False
        return not on_page(bx) and ft(bx, ("終了確認",)) is None

    ok, img, _ = _wait(r, gone, DONE_TIMEOUT)
    r.snap(img, "leave")
    return ok


def leave(
    *,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """离开特別指導（不定制或定制完）。"""
    r = _runner(Action(ActionKind.SHOP_END), shoot, click, clock, sleep, ocr, calib)
    ok = _leave(r)
    return r.result(ok, None if ok else "没能离开特別指導（没找到「終了」或点了没反应）", page_after=None if ok else PAGE)


def customize(
    card_index: int,
    item_index: int,
    *,
    levels: int = 1,
    cards: list[dict] | None = None,
    card_name: str | None = None,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    ocr=None,
    calib: bool = True,
) -> ExecutionResult:
    """特別指導：选第 card_index 张 →「カスタマイズ」→ 第 item_index 个色块（从左到右）做 levels 级 →「一覧に戻る」。

    cards：识别 SPECIAL_GUIDANCE 的 cards[{index, card_id, name, center, ...}]：带 center 就点它，否则按未滚动的网格位置。
    card_name：给了（或 cards 里有 name）就核对菜单页上的卡名。某级之前色块已「カスタマイズ上限」/ 没次数就停在已做的级数。
    ok=True 表示 levels 级全做完；部分完成时 ok=False，error 写做了几级。结束时回到选卡页（可继续下一张或 leave）。
    """
    act = Action(ActionKind.CONTINUE, {"customize": {"card_index": card_index, "item_index": item_index, "levels": levels}})
    r = _runner(act, shoot, click, clock, sleep, ocr, calib)
    img = r.shoot()
    boxes = r.ocr(img)
    r.snap(img, "grid")
    if not on_grid(boxes) or has_toast(boxes):
        if not on_page(boxes):
            return r.result(False, "不在特別指導选卡页", page_after=None)
        if not _to_grid(r):
            return r.result(False, "不在特別指導选卡页（菜单页 / 提示退不回一览）", page_after=PAGE)
        img = r.shoot()
        boxes = r.ocr(img)
    entry = next((c for c in cards or [] if c.get("index") == card_index), None)
    pos = tuple(entry["center"]) if entry and entry.get("center") else sg_grid_center(card_index)
    name = card_name or (entry or {}).get("name")
    base_val = button_sv(img, CUSTOMIZE_BTN_ROI)[1]
    r.tap(pos)
    ok, img = r.until(lambda im: orange_lit(im, CUSTOMIZE_BTN_ROI, base_val), STEP_TIMEOUT)
    r.snap(img, "card-picked")
    if not ok:
        return r.result(False, f"点第 {card_index} 张卡 {pos} 后「カスタマイズ」没亮（卡已定制满 / 没点中）")
    r.tap(CUSTOMIZE_TAP)
    ok, img, boxes = _wait(r, lambda b, im: on_menu(b))
    r.snap(img, "menu")
    if not ok:
        return r.result(False, "点「カスタマイズ」后没进定制菜单")
    if name and not _name_in(boxes, name.rstrip("+"), (150, 150, 720, 520)):
        r.tap(BACK_TAP)
        return r.result(False, f"菜单页的卡不是「{name}」，已回到一览")
    done = 0
    for level in range(levels):
        items = menu_items(boxes)
        if not 0 <= item_index < len(items):
            break
        it = items[item_index]
        if it["maxed"] or it["left"] == 0:
            logger.info("特別指導：第 %d 项「%s」已做满（做了 %d 级）", item_index, it["name"], done)
            break
        r.tap(it["center"])
        ok, img = r.until(lambda im: orange_lit(im, EXEC_BTN_ROI), STEP_TIMEOUT)
        r.snap(img, f"item-{level}")
        if not ok:
            break
        r.tap(EXEC_TAP)
        ok, img, b = _wait(r, lambda b, im: find_text(b, ("実行確認",)) is not None)
        r.snap(img, f"exec-confirm-{level}")
        if not ok:
            break
        r.tap(find_text(b, ("実行する",), region=BOTTOM_RIGHT) or EXEC_CONFIRM_TAP)
        # 先等提示真的出现（q12：确认框关掉后有一段菜单帧，提示还没出来就判「回到菜单」，之后的「一覧に戻る」被吞）
        ok, img, _ = _wait(r, lambda b, im: has_toast(b), DONE_TIMEOUT)
        r.snap(img, f"after-{level}")
        if not ok:
            logger.warning("特別指導：实行后 %.0fs 没看到「カスタマイズしました」", DONE_TIMEOUT)
        last = [r.clock() - 1.0]
        got = {}

        def dismiss(im):  # 「〜をカスタマイズしました」还在就每秒点一下
            if r.clock() - last[0] >= 1.0 and has_toast(r.ocr(im)):
                r.tap(TOAST_TAP)
                last[0] = r.clock()

        def menu_back(im):
            got["b"] = r.ocr(im)
            return on_menu(got["b"]) and not has_toast(got["b"])

        ok, img = r.until(menu_back, DONE_TIMEOUT, tick=dismiss)
        boxes = got.get("b", [])
        if not ok:
            break
        done += 1
        logger.info("特別指導：「%s」第 %d 项「%s」第 %d 级", name or card_index, item_index, it["name"], done)
    back = _to_grid(r)
    r.snap(r.shoot(), "back-to-grid" if back else "not-back")
    if done < levels:
        return r.result(False, f"定制只做了 {done}/{levels} 级" + ("" if back else "；没回到一览"), page_after=PAGE)
    return r.result(True, page_after=PAGE)
