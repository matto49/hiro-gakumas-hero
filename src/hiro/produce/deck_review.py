"""主动查看牌组（docs/state-events.md §6）：选行动页底栏的牌组按钮 → 「所持スキルカード」一览 → 读一屏（不全就滑到底再读、拼起来）→ 关闭。

只在三种情况调用（hif.HifCore._maybe_review_deck）：pending_review 含 produce.deck；每场考试之间第一次到选行动页；调试模式。
读不全不下结论（返回 deck=None + problems），由调用方记 pending。所有点击都可注入，便于离线测试。"""

from __future__ import annotations

import time
from collections.abc import Callable

from hiro import imgio
from hiro.devices.active import sleep as _sleep

CLOSE_TEXTS = ("閉じる",)
CLOSE_TAP = (360, 1160)  # 一览底部「閉じる」（识屏：KAA OwnedSkillCardsDialog.ButtonClose）
RETAP_AFTER = 5.0  # 点了牌组按钮这么久一览还没出来、画面还是原页面：再点一次
LATE_WATCH = 4.0  # 放弃读牌后再盯这么久，一览晚出来就关掉
OPEN_TIMEOUT = 12.0  # 第二十局开局：一览 6 秒后才弹出（转场后第一次点），读牌放弃后一览留在画面上
LIST_SWIPE = (
    360,
    950,
    360,
    650,
)  # 一览卡格视口（LIST_VIEW y 395–1010）里滑 300px ≈ 2 行（行距约 154，一屏整 3 行）：每两屏重叠 1 行，任何张数都能按行拼（识屏建议）
MAX_SCREENS = 6
SAME_SCREEN_DIFF = 2.0  # 视口灰度平均差小于这个 = 滑不动了（到底）
# 滑完等列表停稳再截：先等 SWIPE_SETTLE，之后每 STILL_EVERY 截一张，相邻两张差 < SAME_SCREEN_DIFF 算停稳，最多等 STILL_MAX。
# 第二十三局（录像、机器负载高）：滑到底后列表在回弹，每屏都截在动的时候，相邻屏一直不一样 → 拍满 6 屏，读整副 58.8s（平时 10–14s）
SWIPE_SETTLE, STILL_EVERY, STILL_MAX = 0.8, 0.3, 2.5
END_SWIPES = 8  # 回到顶 / 滑到底：一直滑到画面不动，最多这么多次
ROWS_PER_SCREEN = 3  # 一览一屏整 3 行（4 张一行）：首屏 + 末屏最多盖住 6 行 = 24 张
SAVE_DIR = None  # 每屏截图存这里（识屏要核对第一次实机的一览页帧）；None 不存


def _view_diff(a, b) -> float:
    from hiro.vision.deck import LIST_VIEW

    import cv2
    import numpy as np

    x1, y1, x2, y2 = LIST_VIEW
    ga, gb = (cv2.cvtColor(i[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32) for i in (a, b))
    return float(np.abs(ga - gb).mean())


def _save(imgs) -> None:
    if SAVE_DIR is None:
        return
    try:
        import cv2  # noqa: F401

        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, im in enumerate(imgs):
            imgio.imwrite(str(SAVE_DIR / f"{stamp}-{i:02d}.jpg"), im)
    except Exception:
        pass


def merge_screens(imgs) -> dict:
    """一览页多屏截图（或已读好的 read_deck_list 结果）→ DECK_LIST 字段。优先用识屏的 merge_deck_list；还没有时两两用 merge_deck_pages 拼。"""
    from hiro.vision import deck as vd

    if hasattr(vd, "merge_deck_list"):
        return vd.merge_deck_list(imgs)
    reads = [im if isinstance(im, dict) else vd.read_deck_list(im) for im in imgs]
    page = reads[0]
    for r in reads[1:]:
        if page.get("deck") is not None:
            break
        page = vd.merge_deck_pages(page, r)
    return page


def _still(shoot, sleep, clock):
    """滑动后等列表停稳再返回这一帧（回弹 / 惯性还在动时相邻两帧差大）；最多等 STILL_MAX，超时返回最后一帧。"""
    sleep(SWIPE_SETTLE)
    prev = shoot()
    deadline = clock() + STILL_MAX
    while clock() < deadline:
        sleep(STILL_EVERY)
        cur = shoot()
        if prev is None or cur is None or _view_diff(prev, cur) < SAME_SCREEN_DIFF:
            return cur
        prev = cur
    return prev


def review_deck(*, shoot: Callable, click: Callable[[int, int], None], swipe: Callable[[int, int, int, int, float], None],
                ocr_texts: Callable, sleep: Callable[[float], None] = _sleep, clock: Callable[[], float] = time.monotonic) -> dict:  # fmt: skip
    """返回 {deck, deck_size, problems, opened, closed}。deck 为 ProduceState.deck 格式或 None。"""
    from hiro.vision.deck import find_deck_button, is_deck_list, merge_deck_pages, read_deck_list

    out = {"deck": None, "deck_size": None, "problems": [], "opened": False, "closed": False}
    img = shoot()
    btn = find_deck_button(img)
    if btn is None:
        out["problems"].append("选行动页底栏没找到牌组按钮")
        return out
    click(*btn)
    start = clock()
    retapped = False
    while clock() - start < OPEN_TIMEOUT:
        sleep(0.5)
        img = shoot()
        if is_deck_list(img):
            out["opened"] = True
            break
        # 第二十局开局：点了之后一览很晚才出（02:29:59 那帧上还没有），可能第一下被转场吃掉 / 晚生效。
        # RETAP_AFTER 秒后画面还是带牌组按钮的原页面（没在加载、没开别的）就再点一次
        if not retapped and clock() - start >= RETAP_AFTER and find_deck_button(img) == btn:
            click(*btn)
            retapped = True
            out["problems"].append("一览没出来，再点了一次牌组按钮")
    if not out["opened"]:
        out["problems"].append("点了牌组按钮，没看到「所持スキルカード」一览")
        # 放弃前再看一会儿：一览晚出来就关掉，不留在画面上（第二十局一览留着，被 KAA 判成 SELECT_PITEM 停机）
        late_deadline = clock() + LATE_WATCH
        while clock() < late_deadline:
            sleep(0.5)
            if is_deck_list(shoot()):
                out["opened"] = True
                out["problems"].append("一览晚出来了，读牌已放弃，直接关掉")
                out["closed"] = _close(shoot, click, ocr_texts, sleep, clock, is_deck_list)
                break
        return out
    sleep(0.6)  # 入场动画
    imgs = [shoot()]
    reads = [read_deck_list(imgs[0])]
    page = reads[0]
    if page.get("deck") is None:  # 一屏不全：往下滑着每屏截一张（停稳后），读完就拼，拼出整副或滑不动了就停
        for _ in range(MAX_SCREENS - 1):
            swipe(*LIST_SWIPE, 0.6)
            im = _still(shoot, sleep, clock)
            if _view_diff(imgs[-1], im) < SAME_SCREEN_DIFF:
                break
            imgs.append(im)
            reads.append(read_deck_list(im))
            page = merge_screens(reads)
            if page.get("deck") is not None:
                break
    size = page.get("deck_size")
    if page.get("deck") is None and size and -(-size // 4) <= 2 * ROWS_PER_SCREEN:
        # 逐屏拼出的张数和标签对不上（DMM qualifyverify4 11-17：第 2 屏滑过头，拼出 25 张、标签 16 张）：按标签重读一次——
        # 回到顶读首屏、滑到底读末屏，按行号拼（merge_deck_pages：中间有行没看到、重叠行读得不一样都给 None）。还不对就不写
        first = "；".join(page.get("problems") or [])
        top, bottom = _top_and_bottom(shoot, swipe, sleep, clock)
        imgs += [top, bottom]
        retry = merge_deck_pages(read_deck_list(top), read_deck_list(bottom))
        if retry.get("deck") is not None and len(retry["deck"]) == size:
            out["problems"].append(f"逐屏拼接不对（{first}），按标签 {size} 张回到顶 / 滑到底重读，按行号拼对了")
            page = retry
        else:
            out["problems"].append(
                f"逐屏拼接不对（{first}），按标签重读也没拼对（{'；'.join(retry.get('problems') or [])}），不写牌组"
            )
    _save(imgs)
    out["page"] = page
    out["screens"] = len(imgs)
    out["deck"], out["deck_size"] = page.get("deck"), page.get("deck_size")
    out["problems"].extend(page.get("problems") or [])
    out["closed"] = _close(shoot, click, ocr_texts, sleep, clock, is_deck_list)
    if not out["closed"]:
        out["problems"].append("牌组一览没关掉")
    return out


def _to_end(shoot, swipe, sleep, clock, *, up: bool):
    """一直滑到列表停住（相邻两屏一样），返回停稳的那一帧。up=True 回到顶，False 滑到底。"""
    x1, y1, x2, y2 = LIST_SWIPE
    seg = (x2, y2, x1, y1) if up else LIST_SWIPE
    prev = _still(shoot, sleep, clock)
    for _ in range(END_SWIPES):
        swipe(*seg, 0.6)
        cur = _still(shoot, sleep, clock)
        if _view_diff(prev, cur) < SAME_SCREEN_DIFF:
            return cur
        prev = cur
    return prev


def _top_and_bottom(shoot, swipe, sleep, clock):
    return _to_end(shoot, swipe, sleep, clock, up=True), _to_end(shoot, swipe, sleep, clock, up=False)


def _close(shoot, click, ocr_texts, sleep, clock, is_deck_list) -> bool:
    deadline = clock() + OPEN_TIMEOUT
    while clock() < deadline:
        img = shoot()
        if not is_deck_list(img):
            return True
        box = next((t for t in ocr_texts(img) if t[0].strip() in CLOSE_TEXTS), None)
        click(*(box[1] if box is not None else CLOSE_TAP))
        sleep(0.8)
    return not is_deck_list(shoot())
