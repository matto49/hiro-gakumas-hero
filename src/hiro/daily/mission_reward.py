"""领任务奖励，移植 KAA tasks/daily/mission_reward：
1. 任务页（ミッション）：デイリー / ウィークリー 两个页签各点一次「一括受取」，等按钮变灰，中间的奖励框点 ✕ 关；
2. 通行证页（パス）：逐个点「受取」、关奖励框，按滚动条每次往下拖 10% 轨道，直到到底；领到过以后碰到一整屏没有可领的就停。
KAA 的「活动奖励」是空实现（TODO），这里也不做。说明框（「ミッションパスPt獲得」等，只有閉じる）由日常中断 Popup 关。
"""

from __future__ import annotations

import logging

from hiro.daily import common
from hiro.runtime import nav
from hiro.vision.ui_templates import point

logger = logging.getLogger(__name__)

MISSION_ENTRY = "Daily.ButtonMission"
MISSION_TITLE = "Daily.MissonRewards.TitleIcon"
MISSION_CLAIM = "Daily.MissonRewards.ButtonClaim"
TABS = (("daily", "Daily.MissonRewards.PointDaily"), ("weekly", "Daily.MissonRewards.PointWeekly"))
PASS_ENTRY = "Daily.ButtonPass"
PASS_TITLE = "Daily.PassRewards.IconTitle"
PASS_CLAIM = "Daily.PassRewards.ButtonClaim"
ICON_CLOSE = "Common.ButtonIconClose"
PASS_SCROLLBAR = (699, 328, 8, 608)  # KAA R.Daily.PassRewards.Scrollbar.rect（x, y, w, h）
SCROLL_STEP = 0.1
MAX_SCROLLS = 30  # 每次 10% 轨道，十几次就到底；防止把手认错时一直拖
MAX_CLAIMS_PER_SCREEN = 20
TIMEOUT = 30.0


def _enter(rt, entry: str, title: str, what: str) -> None:
    nav.goto_home(rt)

    def step(f):
        if nav.find(f, title) is not None:
            return True
        if (m := nav.find(f, entry)) is not None:
            nav.tap_match(rt, m)
        return None

    rt.drive(step, TIMEOUT, what, every=0.5)


def claim_tab(rt, name: str) -> bool:
    """当前页签有亮的「一括受取」就点，然后关奖励框直到按钮变灰。返回领没领。"""
    f = common.frame(rt)
    if (m := nav.find(f, MISSION_CLAIM)) is None or common.enabled(f, m) is not True:
        logger.info("%s 任务没有可领的奖励", name)
        return False
    nav.tap_match(rt, m)
    rt.sleep(0.5)

    def step(f):
        if (b := nav.find(f, MISSION_CLAIM)) is not None and common.enabled(f, b) is False:
            return True
        if (c := nav.find(f, ICON_CLOSE)) is not None:
            nav.tap_match(rt, c)
            rt.sleep(1)
        return None

    rt.drive(step, TIMEOUT, f"领 {name} 任务奖励", every=0.5)
    logger.info("领了 %s 任务奖励", name)
    return True


def claim_missions(rt) -> list[str]:
    _enter(rt, MISSION_ENTRY, MISSION_TITLE, "进任务页")
    rt.sleep(1)
    got = []
    for name, pt in TABS:
        rt.device.tap(*point(pt))
        rt.sleep(1.5)
        if claim_tab(rt, name):
            got.append(name)
    return got


def _claim_one_pass(rt) -> bool:
    """这一屏有「受取」就点一个：点到奖励框出来，再关到回通行证页。没有返回 False。"""
    if nav.find(common.frame(rt), PASS_CLAIM) is None:
        return False

    def press(f):
        if nav.find(f, ICON_CLOSE) is not None:
            return True
        if (m := nav.find(f, PASS_CLAIM)) is not None:
            nav.tap_match(rt, m)
            rt.sleep(1)
        return None

    def close(f):
        if (m := nav.find(f, ICON_CLOSE)) is not None:
            nav.tap_match(rt, m)
            rt.sleep(1)
            return None
        return True if nav.find(f, PASS_TITLE) is not None else None

    rt.drive(press, TIMEOUT, "点通行证「受取」")
    rt.drive(close, TIMEOUT, "关通行证奖励框")
    return True


def claim_pass(rt) -> int:
    _enter(rt, PASS_ENTRY, PASS_TITLE, "进通行证页")
    n, last = 0, None
    for _ in range(MAX_SCROLLS):
        got = 0
        for _ in range(MAX_CLAIMS_PER_SCREEN):
            if not _claim_one_pass(rt):
                break
            got += 1
        n += got
        if n > 0 and got == 0:
            rt.sleep(1.0)  # 一帧没认出「受取」不算：画面稳了再看一次（CodeRabbit #138）
            while got < MAX_CLAIMS_PER_SCREEN and _claim_one_pass(rt):
                got += 1
            n += got
        if n > 0 and got == 0:
            # 能领的都连在当前等级附近：领到过以后又一整屏没有可领的，下面也不会有，不再往下拖（11-27 用户：每天划到底浪费时间）
            break
        rt.sleep(0.5)
        bar = common.scrollbar(common.frame(rt).img, PASS_SCROLLBAR)
        if bar is None or bar.position >= 0.99 or bar == last:  # 认不出 / 到底 / 拖了没动
            break
        last = bar
        common.scroll_by(rt, PASS_SCROLLBAR, bar, SCROLL_STEP)
        rt.sleep(0.8)
    logger.info("通行证领了 %d 个", n)
    return n


def run(rt) -> str:
    got = claim_missions(rt)
    rt.sleep(1)
    n = claim_pass(rt)
    nav.goto_home(rt)
    tabs = "、".join(got) if got else "无"
    return f"任务奖励：{tabs}；通行证：{n} 个"
