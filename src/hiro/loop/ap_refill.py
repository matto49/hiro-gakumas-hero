"""AP 不够时喝 APドリンク 补（用户规则：AP 不够喝 APドリンク，绝不用ジュエル）。

第二十一局开局：AP 10/100（选拔 / 本战各 20），KAA 打印「AP insufficient. Exiting produce.」退出（use_ap_drink=false）。
不打开 KAA 的 use_ap_drink：它连点 ButtonUse / ButtonRefillAP，不核对ジュエル。

流程（KAA 参考图 screenshot_no_enough_ap_1..3，720x1280）：
1. 「AP不足 / APがN不足しています」→ 点「回復する」（读出缺口 N）；
2. 「AP回復」列表 → 只点与「APドリンク」同一行的「使う」（行里有ジュエル字样就不点）；
3. 确认框「APドリンクを消費して、APを回復しますか？」→ 按「＋」把使用数加到需要的瓶数，核对「使用数 → n」，
   确认框文字必须含「APドリンクを消費」、整屏不含「ジュエル」才点「回復する」，否则点「キャンセル」、返回失败；
4. 回到列表点「閉じる」。
弹窗会叠在下层弹窗上面（下层压暗但文字还读得出），每一步都取最下面那层（y 最大）的按钮。
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable

AP_PER_DRINK = 20
MAX_DRINKS = 3  # 一次最多喝几瓶（整局 40 AP = 2 瓶；多给 1 瓶余量）
PLUS_TAP = (584, 1033)  # 确认框滑条右端「＋」（参考图 3）
STEP_TIMEOUT = 6.0
POLL = 0.5
JEWEL = "ジュエル"


LATIN_FIX = str.maketrans(
    {"Ä": "A", "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Å": "A", "Ｐ": "P", "Ａ": "A"}
)  # RapidOCR 把「AP」读成「ÄP」（Windows 第十二局）


def _texts(boxes) -> list[tuple[str, int, int]]:
    return [(t.replace(" ", "").translate(LATIN_FIX), (b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for t, _, b in boxes]


def _lowest(texts, word, *, exact=False, min_y=0):
    hits = [(x, y) for t, x, y in texts if (t == word if exact else word in t) and y >= min_y]
    return max(hits, key=lambda p: p[1]) if hits else None


def shortfall(texts) -> int | None:
    """「APがN不足しています」的 N（取最下面那条）。"""
    hits = [(y, int(m.group(1))) for t, _, y in texts if (m := re.search(r"が(\d+)不足", t))]
    return max(hits)[1] if hits else None


def drink_use_button(texts) -> tuple[int, int] | None:
    """「AP回復」列表里与「APドリンク」同一行的「使う」。同一行（名字上下 70px 内）有ジュエル字样就不给。"""
    names = [(x, y) for t, x, y in texts if t.startswith("APドリンク") and "消費" not in t]
    for nx, ny in sorted(names, key=lambda p: -p[1]):
        if any(JEWEL in t and abs(y - ny) <= 70 for t, _, y in texts):
            continue
        use = [(x, y) for t, x, y in texts if t == "使う" and x > nx and abs(y - ny) <= 70]
        if use:
            return use[0]
    return None


def use_count(texts) -> int | None:
    """确认框「使用数 0 → n」的 n：「使用数」那一行最右边的数字。"""
    row = _lowest(texts, "使用数")
    if row is None:
        return None
    nums = [(x, int(t)) for t, x, y in texts if t.isdigit() and abs(y - row[1]) <= 15 and x > row[0]]
    return max(nums)[1] if nums else None


HIF_BUTTON_TEXT = {"main": "本戦", "qualify": "選抜試験"}
HIF_BUTTON_Y = (800, 1000)  # H.I.F 页两个大按钮的文字行（720×1280：選抜試験 / 本戦 y≈890）
OPEN_TIMEOUT = 8.0


def open_hif(shoot: Callable, click: Callable[[int, int], None], ocr: Callable, *, mode: str, try_template: Callable[[], bool] | None = None,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] | None = None) -> bool:  # fmt: skip
    """在培育的 H.I.F 页点「本戦 / 選抜試験」按钮（AP 不够时游戏会弹「AP不足」）。先试 KAA 模板（try_template），没中就按 OCR 文字定位；
    OPEN_TIMEOUT 内每帧都试（页面在过场 / 刚日切换时第一帧可能还没到）。第二十七局：只截一次图、模板没中就报「没找到 H.I.F 按钮」，
    实际按钮在（日切换后多了「あさり先生のプロデュースゼミ」横幅、「イベントモード」开关，本戦 文字在 (570,890)）。"""
    if clock is None:
        from hiro.loop.pause_flag import active_clock

        clock = active_clock  # 录屏暂停期间不计时
    from hiro.produce.ops import button_text

    want = HIF_BUTTON_TEXT[mode]
    deadline = clock() + OPEN_TIMEOUT
    while True:
        img = shoot()
        if try_template is not None and try_template():
            return True
        hit = next(
            (
                b
                for t, _, b in ocr(img)
                if button_text(t, want) and HIF_BUTTON_Y[0] <= (b[1] + b[3]) / 2 <= HIF_BUTTON_Y[1]
            ),
            None,
        )
        if hit is not None:
            click((hit[0] + hit[2]) // 2, (hit[1] + hit[3]) // 2)
            return True
        if clock() >= deadline:
            return False
        sleep(0.8)


def refill(shoot: Callable, click: Callable[[int, int], None], ocr: Callable, *, extra_ap: int = 0, sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] | None = None, max_drinks: int = MAX_DRINKS, log: Callable[[str], None] = print) -> dict:  # fmt: skip
    """从「AP不足」弹窗开始喝 APドリンク。extra_ap：这一局之后还要用的 AP（只打选拔时本战还要 20）。
    返回 {ok, drinks, shortfall, error}。任何一步没认出来 / 出现ジュエル：点キャンセル / 閉じる，ok=False。"""
    if clock is None:
        from hiro.loop.pause_flag import active_clock

        clock = active_clock  # 录屏暂停期间不计时
    out = {"ok": False, "drinks": 0, "shortfall": None, "error": None}

    def look():
        return _texts(ocr(shoot()))

    def wait_for(pred):
        deadline = clock() + STEP_TIMEOUT
        while True:
            t = look()
            if pred(t):
                return t
            if clock() >= deadline:
                return None
            sleep(POLL)

    def fail(why, texts=None):
        out["error"] = why
        texts = texts or look()
        btn = _lowest(texts, "キャンセル") or _lowest(texts, "閉じる")
        if btn:
            click(*btn)
        log(f"AP 补充失败：{why}，已取消")
        return out

    # 1. AP不足
    t = wait_for(lambda t: shortfall(t) is not None)
    if t is None:
        return fail("没看到「AP不足」弹窗")
    if any(JEWEL in s for s, _, _ in t):
        return fail("AP不足弹窗里有ジュエル字样", t)
    out["shortfall"] = gap = shortfall(t)
    need = max(1, min(max_drinks, math.ceil((gap + max(0, extra_ap)) / AP_PER_DRINK)))
    btn = _lowest(t, "回復する")
    if btn is None:
        return fail("AP不足弹窗没有「回復する」", t)
    click(*btn)

    # 2. AP回復 列表 → APドリンク 的「使う」
    t = wait_for(lambda t: drink_use_button(t) is not None)
    if t is None:
        return fail("AP回復列表里没找到 APドリンク 的「使う」")
    click(*drink_use_button(t))

    # 3. 确认框：加到 need 瓶，核对文字，无ジュエル才回復
    t = wait_for(lambda t: any("APドリンクを消費" in s for s, _, _ in t))
    if t is None:
        return fail("没看到「APドリンクを消費して…」确认框")
    for _ in range(need - 1):
        click(*PLUS_TAP)
        sleep(0.3)
    t = look()
    n = use_count(t)
    if n is not None and n != need:
        for _ in range(max(0, need - n)):
            click(*PLUS_TAP)
            sleep(0.3)
        t = look()
        n = use_count(t)
    if not any("APドリンクを消費" in s for s, _, _ in t) or any(JEWEL in s for s, _, _ in t):
        return fail("确认框不是「APドリンクを消費」或出现ジュエル", t)
    if n is not None and n > max_drinks:
        return fail(f"使用数读到 {n}，超过上限 {max_drinks}", t)
    btn = _lowest(t, "回復する", min_y=1000)
    if btn is None:
        return fail("确认框没有「回復する」", t)
    click(*btn)
    out["drinks"] = n if n is not None else need

    # 4. 关掉 AP回復 列表
    t = wait_for(lambda t: not any("APドリンクを消費" in s for s, _, _ in t))
    if t is not None and (close := _lowest(t, "閉じる")):
        click(*close)
    out["ok"] = True
    log(f"AP 补充：缺 {gap}，喝 APドリンク {out['drinks']} 瓶")
    return out
