# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的 tasks/daily/assignment
"""工作（お仕事），移植 KAA tasks/daily/assignment：首页工作入口有红点就进去领奖励，再给ミニライブ、ライブ配信各派一位偶像。

- 先找好調图标的偶像，点上去看名字框变没变（没变 = 这位已经派出去了，换下一位）；看了 3 次都没有好調图标、
  或 4 次都选不上，就用默认选中的那位（同 KAA）。
- 时长按 KAA 配置的缺省 12 小时（run_daily 一直用缺省，没改过）。
- 「時短」按钮（可能花ジュエル）绝不点；含ジュエル的确认框由日常中断 Popup 只点キャンセル。
"""

from __future__ import annotations

import logging

from hiro.daily import common
from hiro.runtime import nav
from hiro.vision.ui_templates import box

import numpy as np

logger = logging.getLogger(__name__)

ENTRY = "Daily.ButtonAssignmentPartial"
HOME_BOX = "Daily.BoxHomeAssignment"
COMPLETION = "Common.ButtonCompletion"
TITLE = "Daily.IconTitleAssign"
SHORTEN = "Daily.ButtonAssignmentShortenTime"
ICONS = {"mini": "Daily.IconAssignMiniLive", "online": "Daily.IconAssignOnlineLive"}
NAMES = {"mini": "ミニライブ", "online": "ライブ配信"}
SELECT = "Common.ButtonSelect"
KOUCHOU = "Daily.IconAssignKouchou"
NAME_BOX = "Daily.Assignment.BoxCharName"
CONFIRM = "Common.ButtonConfirmNoIcon"
START = "Common.ButtonStart"
HOURS = {4: "Daily.Assignment.Button4Hours", 8: "Daily.Assignment.Button8Hours", 12: "Daily.Assignment.Button12Hours"}
COMPLETED, DOT = "#ff6085", "#ff134a"  # 入口上「完了」的粉色和通知红点（同 KAA）
SKIP_POINT = (12, 12)  # 空白处：点一下跳过动画（同 KAA skip()）
DURATION = 12
LOOKS, ATTEMPTS = 3, 4  # 找好調图标最多看 3 次、选人最多试 4 轮（同 KAA MAX_SWIPE_COUNT=2 / max_attempts=4）
TIMEOUT = 30.0


def at_assignment(f) -> bool:
    """在工作页（标题出来后还有一段动画，不能只看标题，同 KAA）。"""
    return any(nav.find(f, n) is not None for n in (SHORTEN, *ICONS.values()))


def _crop(f, b) -> np.ndarray:
    return f.img[b.y1 : b.y2, b.x1 : b.x2].copy()


def _pick_idol(rt) -> bool:
    """选好調的偶像；返回选上没有（没选上就用默认选中的那位）。"""
    looks = 0
    for _ in range(ATTEMPTS):
        f = common.frame(rt)
        icons = sorted(common.find_all(f, KOUCHOU), key=lambda m: m.rect.x1)
        if not icons:
            looks += 1
            if looks >= LOOKS:
                logger.info("没有好調的偶像，用默认的那位")
                return False
            rt.sleep(0.5)
            continue
        name_box = box(NAME_BOX)
        for m in icons:
            before = _crop(common.frame(rt), name_box)
            nav.tap_match(rt, m)
            rt.sleep(1)
            if common.same(before, _crop(common.frame(rt), name_box)):
                continue  # 名字框没变：这位已经派出去了
            return True
        rt.device.swipe(432, 896, 144, 896, duration=0.3)  # 可能在第二页（同 KAA swipe_scaled(0.6, 0.7, 0.2, 0.7)）
        rt.sleep(0.5)
    logger.info("好調的偶像都选不上，用默认的那位")
    return False


def _tap_when(rt, name: str, what: str, timeout: float = TIMEOUT) -> None:
    def step(f):
        if (m := nav.find(f, name)) is not None:
            nav.tap_match(rt, m)
            return True
        return None

    rt.drive(step, timeout, what, every=0.5)


def assign(rt, kind: str, duration: int = DURATION) -> bool:
    """在工作页给 kind（mini / online）派一位偶像；那一栏已经派出去了返回 False。"""
    f = rt.drive(lambda f: f if nav.find(f, TITLE) is not None else None, 10, "等工作页标题", every=0.5)
    icon = nav.find(f, ICONS[kind])
    if icon is None:
        logger.info("%s 已经派出去了", NAMES[kind])
        return False
    nav.tap_match(rt, icon)
    rt.drive(lambda f: True if nav.find(f, SELECT) is not None else None, 20, "等选偶像页", every=0.5)
    _pick_idol(rt)
    rt.sleep(0.5)
    _tap_when(rt, SELECT, "点「選択」")
    _tap_when(rt, HOURS[duration], f"选 {duration} 小时")
    rt.sleep(0.5)

    def finish(f):
        if at_assignment(f):
            return True
        for name in (CONFIRM, START):  # 決定する → 開始する
            if (m := nav.find(f, name)) is not None:
                nav.tap_match(rt, m)
                rt.sleep(0.5)
                return None
        return None

    rt.drive(finish, TIMEOUT, f"派出{NAMES[kind]}", every=0.5)
    return True


def run(rt, *, home: bool = True, duration: int = DURATION) -> str:
    if home:
        nav.goto_home(rt)
    f = common.frame(rt)
    area = box(HOME_BOX)
    completed = common.has_color(f.img, COMPLETED, area)
    if not completed and not common.has_color(f.img, DOT, area):
        return "工作不用处理"
    claimed = {"n": 0}

    def enter(f):
        if at_assignment(f):
            return True
        if completed and (m := nav.find(f, COMPLETION)) is not None:
            nav.tap_match(rt, m)
            claimed["n"] += 1
            rt.sleep(1)
            return None
        if (m := nav.find(f, ENTRY)) is not None:
            nav.tap_match(rt, m)
            rt.sleep(2)
        return None

    rt.drive(enter, 60, "进工作页", every=0.5)
    out = ["领了工作奖励"] if claimed["n"] else []
    for kind in ("mini", "online"):
        found = rt.wait_until(
            lambda f, k=kind: nav.find(f, ICONS[k]) is not None or nav.find(f, SHORTEN) is not None, 10
        )[1]
        if nav.find(found, ICONS[kind]) is None:
            out.append(f"{NAMES[kind]}在派")
            continue
        if assign(rt, kind, duration):
            out.append(f"派出{NAMES[kind]}")
        rt.wait_until(at_assignment, TIMEOUT)
    if home:
        nav.goto_home(rt)
    return "；".join(out)
