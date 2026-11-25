# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""开局第 2 步租借格（用户 2026-11-02：不用おまかせ，租借格由我们挑）。

挑法：
- 有开局编成方案时（kaa_patches 传 scorer = support_select.rental_scorer）：两页从顶滑到底，每一行按「加上这张后整个编成的价值」打分，租价值最高的。
- 没有方案时：挑「主属性、等级最高」的一张，碰到满级立刻选。
流程：点「レンタルのみ」空格 → 读列表（读两屏：顶部 + 往下滑一次）→ 挑 → 点该行 → 「決定」→ 等第 2 步「次へ」亮。
只点租借列表里的行和「決定」；读不到合格行就点「キャンセル」返回 False（交 RentalPending 停下）。
"""

from __future__ import annotations

import logging
import os

from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep

logger = logging.getLogger(__name__)

RENTAL_SLOT = (588, 906)
WANT_ATTR = "Da"


def _rows():
    from hiro.vision.support_screens import read_rental_list

    out = read_rental_list(device.screenshot())
    return out or {"rows": [], "controls": {}}


def best_row(rows: list[dict], attr: str = WANT_ATTR, exclude: set | None = None) -> dict | None:
    """主属性、等级最高（同级突破多的优先）；没有主属性的就返回 None（不乱租别的属性）。exclude：已知プラン不一致的卡名。"""
    exclude = exclude or set()
    cand = [r for r in rows if not r.get("covered") and (r.get("card") or {}).get("attr") == attr and (r.get("card") or {}).get("level")
            and (r.get("card") or {}).get("name") not in exclude]  # fmt: skip
    if not cand:
        return None
    return max(cand, key=lambda r: ((r["card"].get("level") or 0), (r["card"].get("breaks") or 0)))


SWIPE = (360, 1000, 360, 640, 0.8)  # 约一行半
MAX_SCREENS = 10  # 每页最多滑几次
TOP_LEVEL = 60


SWIPE_BACK = (360, 640, 360, 1000, 0.8)  # 往上翻（看更靠前的行），同 SWIPE 的幅度和速度（不带惯性，不漏行）
TOP_MAX_SWIPES = 30


def _players() -> list:
    return [r.get("player") for r in _rows()["rows"]]


def _to_top():
    """滑到列表最上面：一直往上翻到连续两次画面不动（原来固定滑 10 次，长列表从底部回不到顶，11-17 Mac p15 没找到最好的那行）。"""
    last, still = _players(), 0
    for _ in range(TOP_MAX_SWIPES):
        device.swipe(360, 640, 360, 1000, 0.5)
        _sleep(0.8)
        cur = _players()
        still = still + 1 if cur == last else 0
        if still >= 2:
            break
        last = cur
    _sleep(0.5)


def _goto_page(page: int, pager: dict):
    for _ in range(3):
        cur = (_rows().get("pager") or {}).get("page")
        if cur == page or cur is None:
            return
        device.click(*(pager["next"] if cur < page else pager["prev"]))
        _sleep(1.5)


def _card_sig(row: dict) -> tuple:
    """一行租借卡的身份：卡名 + 等级 + 突破数（玩家名是小字 OCR，两遍读得可能差一个字）。"""
    c = row.get("card") or {}
    return c.get("name"), c.get("level"), c.get("breaks")


def _find_row(rows: list[dict], player, sig: tuple) -> dict | None:
    """回去找记下的那一行：先按玩家名完全一致；再按卡的身份（卡名 + 等级 + 突破）一致（玩家名 OCR 差一个字时也能找到；
    别人的同一张同级同突破的卡价值一样，租哪张都行）。11-17 Mac：按玩家名没找到 chamiria（价值 1336），退成了 1228。"""
    cand = [r for r in rows if r.get("card") and not r.get("covered")]
    return (next((r for r in cand if player and r.get("player") == player), None)
            or next((r for r in cand if sig[0] and _card_sig(r) == sig), None))  # fmt: skip


def _tap_and_decide(row: dict, ctrl: dict) -> bool:
    c = row["card"]
    logger.info(
        "租借：选 %s（%s Lv%s 突破%s，玩家 %s）",
        c.get("name"),
        c.get("attr"),
        c.get("level"),
        c.get("breaks"),
        row.get("player"),
    )
    device.click(*row["center"])
    _sleep(1.0)
    decide = ctrl.get("decide") or (497, 1157)
    for attempt in range(DECIDE_TRIES):
        device.click(*decide)
        _sleep(2.5)
        if not _list_still_open():
            return True
        # 11-22 DMM 新主循环：选中了行、点「決定」没反应（列表还开着），第 2 步「次へ」等 20 秒超时。列表还在才再点
        logger.info("租借：点了「決定」列表还开着，再点一次（第 %d 次）", attempt + 2)
    return True


DECIDE_TRIES = 3  # 「決定」最多点几次（列表关了就停）


def _list_still_open() -> bool:
    """租借列表还开着：底部同时有「キャンセル」和「決定」（第 2 步页面底部是「次へ」）。读不出时当成已经关了（不多点）。"""
    try:
        from hiro.vision.ocr import screen_texts

        low = [t.text.replace(" ", "") for t in screen_texts(device.screenshot()) if t.rect.center.y > 1080]
    except Exception:  # noqa: BLE001
        return False
    return any("キャンセル" in t for t in low) and any(t == "決定" for t in low)


def _ensure_plan_fit():
    """勾上租借列表的「プラン適正」（KAA 原流程也是这么做的），只显示本方案能用的卡。"""
    try:
        from hiro.vision.ui_templates import checkbox_checked, tpl

        img = device.screenshot()
        if (chk := tpl("Produce.Step2.Rent.CheckboxPlan").find(img)) is not None and checkbox_checked(
            img, chk
        ) is False:
            device.click(*chk.center)
            logger.info("租借：勾上「プラン適正」")
            _sleep(1.5)
    except Exception as e:  # noqa: BLE001
        logger.warning("租借：勾「プラン適正」失败（%s），继续，靠决定后的不一致检查兜底", e)


def rental_mismatch() -> bool:
    """第 2 步页面：租借格（右下 tile）上有没有「プラン不一致」。"""
    from hiro.produce.ops import ocr_boxes

    return any(
        "不一致" in t and (b[0] + b[2]) // 2 > 480 and 600 <= (b[1] + b[3]) // 2 <= 900
        for t, _, b in ocr_boxes(device.screenshot())
    )


def scored_best(rows: list[dict], scorer, exclude: set | None = None) -> tuple[dict, float] | None:
    """按 scorer 挑一行（价值最高；同价值等级、突破高的优先）。没有可打分的行返回 None。"""
    exclude = exclude or set()
    best = None
    for r in rows:
        c = r.get("card") or {}
        if r.get("covered") or c.get("name") in exclude:
            continue
        v = scorer(r)
        if v is None:
            continue
        key = (v, c.get("level") or 0, c.get("breaks") or 0)
        if best is None or key > best[0]:
            best = (key, r)
    return (best[1], best[0][0]) if best else None


def fallback_scorer(ok):
    """退路打分：类型对（ok(attr)）的卡按等级、突破排，其他不要。"""

    def score(row: dict) -> float | None:
        c = row.get("card") or {}
        if not c.get("level") or not ok(c.get("attr")):
            return None
        return float((c.get("level") or 0) * 10 + (c.get("breaks") or 0))

    return score


# 社区推荐一张都没有时退回「对应类型等级最高」（用户 11-23 定，默认开）；HIRO_RENTAL_FALLBACK=0 关掉，照旧停下
FALLBACK_ON = os.environ.get("HIRO_RENTAL_FALLBACK", "1") != "0"

SEEK_MAX_SWIPES = 40  # 回去找那一行：往上 + 往下一共最多滑这么多次
RENTAL_REFRESHES = 2  # 两页都没有合适的卡时最多刷新租借列表几次（刷新免费）


def pick_rental(attr: str = WANT_ATTR, tries: int = 3, scorer=None, fallback=None) -> bool:
    """挑一张；决定后租借格标「プラン不一致」就记下卡名、重开列表换下一张（第十七局：勾选没生效，租到了别的方案的卡）。
    scorer：按编成价值打分（有方案时）；None = 主属性、等级最高。"""
    exclude: set = set()
    for _ in range(tries):
        name = _pick_once(attr, exclude, scorer, fallback=fallback)
        if name is None:
            return False
        if not rental_mismatch():
            return True
        logger.warning("租借：「%s」是プラン不一致，换一张", name)
        exclude.add(name)
    return False


def progress(why: str) -> None:
    from hiro.loop.repeat_guard import progress as _p

    _p(why)


def _seek(match, scorer=None, exclude=None, max_swipes: int = SEEK_MAX_SWIPES):
    """从当前位置先往上逐屏找、到顶后往下逐屏找，match(rows) 命中就停（不再往后滑，调用方直接点）。
    返回 (命中的行或 None, 最后一屏读数, 途中看到的价值最高的 (行, 价值) 或 None, 途中读到的玩家)。
    到头：连续两次滑了画面不动（只一次不动可能是滑动没走满）。"""
    seen_best, seen_players, lst = None, [], {"rows": []}
    swipes = 0
    for swipe in (SWIPE_BACK, SWIPE):
        last, still = None, 0
        while swipes <= max_swipes:
            lst = _rows()
            row = match(lst["rows"])
            if row is not None:
                return row, lst, seen_best, seen_players
            players = [r.get("player") for r in lst["rows"]]
            seen_players += [p for p in players if p not in seen_players]
            if (
                scorer is not None
                and (hit := scored_best(lst["rows"], scorer, exclude)) is not None
                and (seen_best is None or hit[1] > seen_best[1])
            ):
                seen_best = (hit[0], hit[1])
            still = still + 1 if players == last else 0
            if still >= 2:
                break
            last = players
            device.swipe(*swipe)
            _sleep(1.2)
            swipes += 1
    return None, lst, seen_best, seen_players


def _pick_once(attr: str, exclude: set, scorer=None, refreshes: int = RENTAL_REFRESHES, fallback=None) -> str | None:
    """两页都从顶滑到底，记下最好的一张（页、滑动次数、玩家名），回去点它。返回选中的卡名。
    没有 scorer 时遇到满级（Lv60）主属性卡立刻选；有 scorer 时要看完全部再挑价值最高的。
    fallback(attr) → bool：社区推荐打分一张都打不上时（刷新也用完），退回租这种类型里等级最高的一张（用户 11-23 定）；None = 不退，取消。"""
    device.click(*RENTAL_SLOT)
    _sleep(2.5)
    _ensure_plan_fit()
    first = _rows()
    ctrl, pager = first.get("controls") or {}, first.get("pager") or {}
    pages = pager.get("pages") or 1
    best = None  # (level, breaks, page, screen, player)
    page1_top = None
    for page in range(1, pages + 1):
        if page > 1:
            _goto_page(page, pager)
            _to_top()
            if _players() == page1_top:
                # 「1/2」是上面卡片详情区的翻页，不是列表的（11-17 Mac p15：翻过去列表行一模一样，又从头扫了一遍）
                logger.info("租借：翻到第 %d 页后列表没变（翻页是详情区的），不再扫", page)
                break
        elif page1_top is None:
            page1_top = _players()
        last_players = None
        for screen in range(MAX_SCREENS):
            progress(f"租借：第 {page} 页第 {screen + 1} 屏")  # 两页翻完约 55s，每屏报一次进展（D38）
            lst = _rows()
            rows = lst["rows"]
            players = [r.get("player") for r in rows]
            if players == last_players:
                break  # 到底了
            last_players = players
            if scorer is not None:
                hit = scored_best(rows, scorer, exclude)
                if hit is not None:
                    row, value = hit
                    c = row["card"]
                    key = (value, c.get("level") or 0, c.get("breaks") or 0)
                    if best is None or key > best[:3]:
                        best = (*key, page, screen, row.get("player"), _card_sig(row))
                        logger.info(
                            "租借：目前最好 %s（%s Lv%s，编成价值 %.0f，玩家 %s）",
                            c.get("name"),
                            c.get("attr"),
                            c.get("level"),
                            value,
                            row.get("player"),
                        )
            else:
                row = best_row(rows, attr, exclude)
                if row is not None:
                    c = row["card"]
                    if (c.get("level") or 0) >= TOP_LEVEL:
                        _tap_and_decide(row, ctrl)
                        return c.get("name")
                    key = (0.0, c.get("level") or 0, c.get("breaks") or 0)
                    if best is None or key > best[:3]:
                        best = (*key, page, screen, row.get("player"), _card_sig(row))
            device.swipe(*SWIPE)
            _sleep(1.2)
    if best is None:
        refresh = ctrl.get("refresh")
        if refreshes > 0 and refresh:
            # 租借列表会换一批（11-16 Mac 实机：上一轮有 Da Lv60，这一轮两页都没有主属性卡就停了）：点刷新（免费）换一批再找
            logger.warning(
                "租借：两页都没读到%s，刷新租借列表再找（还剩 %d 次）",
                f" {attr} 卡" if scorer is None else "能补成 4-2 的卡",
                refreshes,
            )
            if pages > 1:
                _goto_page(1, pager)
            _to_top()
            before = [r.get("player") for r in _rows()["rows"]]
            device.click(*refresh)
            _sleep(2.5)
            if [r.get("player") for r in _rows()["rows"]] != before:
                device.click(*(ctrl.get("cancel") or (220, 1157)))
                _sleep(1.5)
                return _pick_once(attr, exclude, scorer, refreshes - 1, fallback)
            logger.warning("租借：点了刷新列表没变（可能在冷却），不再刷新")
        device.click(*(ctrl.get("cancel") or (220, 1157)))
        if scorer is not None and fallback is not None and FALLBACK_ON:
            logger.warning("租借：两页都没读到能补成 4-2 的卡（社区推荐打分全打不上），退回租对应类型里等级最高的一张")
            _sleep(1.5)
            return _pick_once(attr, exclude, fallback_scorer(fallback), 0, None)
        logger.warning("租借：两页都没读到%s，取消", f" {attr} 卡" if scorer is None else "能补成 4-2 的卡")
        return None
    _, _, _, page, screen, player, sig = best
    if pages > 1:
        _goto_page(page, pager)
    # 回到那一行：滑动有惯性，滑同样次数不一定回到同一屏（11-16 Mac 实机）；从顶滑同样屏数也可能回不到（p15：从底部回不到顶）。
    # 从当前位置先往上、再往下逐屏找，看到就点（_seek）；整条列表都没找到，才退回这一趟看到的最好一行，按它自己的玩家 / 卡去找
    row, lst, seen_best, seen_players = _seek(lambda rows: _find_row(rows, player, sig), scorer, exclude)
    if row is None and seen_best is not None:
        sb_row, sb_value = seen_best
        sb_player, sb_sig = sb_row.get("player"), _card_sig(sb_row)
        logger.warning("租借：回到最佳行时没找到（玩家 %s，卡 %s），改租往回找时看到的最好一张 %s（玩家 %s，价值 %.0f，最好的是 %.0f）；往回找时读到的玩家 %s",
                       player, sig, sb_sig[0], sb_player, sb_value, best[0], seen_players)  # fmt: skip
        row, lst, _, _ = _seek(lambda rows: _find_row(rows, sb_player, sb_sig), None, exclude)
        if row is None:
            logger.warning("租借：往回找时看到的那张（玩家 %s，卡 %s）也没再找到", sb_player, sb_sig)
    if row is None:
        hit = scored_best(lst["rows"], scorer, exclude) if scorer is not None else None
        row = hit[0] if hit else (best_row(lst["rows"], attr, exclude) if scorer is None else None)
    if row is None:
        logger.warning("租借：回到最佳行时没找到（玩家 %s，卡 %s），取消", player, sig)
        device.click(*(ctrl.get("cancel") or (220, 1157)))
        return None
    _tap_and_decide(row, ctrl)
    return row["card"].get("name")
