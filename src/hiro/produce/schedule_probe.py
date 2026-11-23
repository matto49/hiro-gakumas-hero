"""日程列表（ScheduleListView）：初 / N.I.A 第一次到选行动页时点开日程列表，读整局逐周可选行动，再关掉（协调 11-17 ① / 11-18）。

服务器开局就把整局日程下发了（UserProduceProgressSchedule，逆向 10.2），游戏里在选行动页左上「中間まで N週」框（ENTRY）点开。
读数在 hiro.vision.schedule_list；结果写进局面 produce.schedule（{weeks: [{week, kind, options, faded, done, chosen?, exam?}…],
current_week, total}），reduce 在选行动页放进 page_fields.schedule；续跑时跟着上一段 trace 的 produce 打底，不重读。

开关 HIRO_SCHEDULE_PROBE=1（默认关）。做法：
1. 点 ENTRY → 等画面停稳（至少 OPEN_MIN_S；面板弹出比点击晚，协调 11-18 初 MASTER：原来马上判「没变 = 入口不对」，面板随后盖上来，
   选行动读不到选项停机）→ 认出日程列表（底部「スケジュール」tab）才算打开；
2. 读当前一屏，往后面的周滑（手指往下拖），再往前面的周滑，每次停稳再读，周数不再增加或到头就停；
3. 点 ✕（360, 1190）关掉，最多两次，确认回到选行动页；关不回去停机，不在未知页面上乱点。
帧和读数写在 trace 目录的 schedule-probe/ 下。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ENTRY = (112, 92)  # 选行动页左上页头框（初「中間まで 4週」框中心约 (112, 95)；N.I.A 同位置）
OPEN_MIN_S = 1.0  # 点了以后至少等这么久再判有没有打开
# 停稳后还没认出：再盯这么久，每 LATE_EVERY 秒读一次（协调 11-18 N.I.A MASTER：开局后第一次选行动，点了 8 秒后列表才弹出，
# 原来 4 秒就判「没打开」走了，列表盖住选行动页，后面点行动点在列表上）
LATE_OPEN_S = 12.0
LATE_EVERY = 1.0
SETTLE_DIFF = 1.5
SETTLE_MAX = 3.0
SCROLL_MAX = 8  # 每个方向最多滑几次
TO_LATER = ((360, 450), (360, 950))  # 手指往下拖：看到更大的周（时间轴越往上周数越大）
TO_EARLIER = ((360, 950), (360, 450))
CLOSE_TRIES = 2


def enabled() -> bool:
    return os.environ.get("HIRO_SCHEDULE_PROBE") == "1"


def _thumb(img) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (90, 160)).astype(np.float32)


def _diff(a, b) -> float:
    return float(np.abs(_thumb(a) - _thumb(b)).mean())


def _settle(shoot, sleep, clock, min_s: float = 0.0):
    """等画面停稳（相邻两帧几乎不变），至少 min_s、最多 SETTLE_MAX 秒；返回最后一帧。"""
    t0 = clock()
    prev = shoot()
    while True:
        sleep(0.3)
        img = shoot()
        if (_diff(prev, img) < SETTLE_DIFF and clock() - t0 >= min_s) or clock() - t0 >= SETTLE_MAX + min_s:
            return img
        prev = img


def _at_action_select(img) -> bool:
    from hiro.vision.classify import classify

    try:
        return classify(img).name == "ACTION_SELECT"
    except Exception:  # noqa: BLE001
        return False


def probe(out_dir: Path, *, shoot, click, swipe, sleep=None, clock=time.monotonic, note=None, read=None) -> dict:
    """点开日程列表、读整局、关掉。返回 {opened, schedule, closed, frames}。关不回选行动页抛 ResumeBlocked。"""
    from hiro.loop.errors import ResumeBlocked

    if sleep is None:
        from hiro.devices.active import sleep  # 过运行时检查点，不用裸 time.sleep（test_exec_checkpoint）
    if read is None:
        from hiro.vision.schedule_list import read_schedule_list as read
    from hiro.vision.schedule_list import CLOSE, merge

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []

    def save(img, tag: str) -> None:
        name = f"{len(frames):02d}-{tag}.jpg"
        cv2.imwrite(str(out_dir / name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        frames.append(name)

    before = shoot()
    save(before, "before")
    click(*ENTRY)
    img = _settle(shoot, sleep, clock, OPEN_MIN_S)
    first = read(img)
    t_late = clock()
    while first is None and clock() - t_late < LATE_OPEN_S:
        sleep(LATE_EVERY)
        img = shoot()
        if (first := read(img)) is not None:
            logger.info("日程列表：点了 %.1f 秒后才弹出", clock() - t_late + OPEN_MIN_S)
            img = _settle(shoot, sleep, clock)  # 入场动画：停稳再读
            first = read(img) or first
    result = {"opened": first is not None, "schedule": None, "closed": None, "frames": frames}
    if first is None:
        save(img, "not-opened")
        logger.info(
            "日程列表：点 %s 停稳后没认出日程列表（%s）",
            ENTRY,
            "画面没变" if _diff(before, img) < SETTLE_DIFF * 4 else "画面变了但认不出",
        )
        if note:
            note(f"日程列表：点页头 {ENTRY} 停稳后没认出日程列表，没读到")
        if _at_action_select(img):
            _write(out_dir, result)
            return result
        # 画面变了又认不出：可能是面板版式不同，照关面板的办法关一次，关不回去停机
    else:
        save(img, "list-0")
        screens = [first]
        for direction, tag in ((TO_LATER, "later"), (TO_EARLIER, "earlier")):
            seen = set(first["weeks"])
            for k in range(SCROLL_MAX):
                swipe(*direction[0], *direction[1], 0.5)
                img = _settle(shoot, sleep, clock)
                s = read(img)
                if s is None:
                    break
                save(img, f"{tag}-{k + 1}")
                screens.append(s)
                new = set(s["weeks"]) - seen
                seen |= set(s["weeks"])
                if not new or (tag == "earlier" and 1 in seen):
                    break
        result["schedule"] = merge(screens)
    for _ in range(CLOSE_TRIES):
        click(*CLOSE)
        img = _settle(shoot, sleep, clock)
        if _at_action_select(img):
            result["closed"] = True
            break
    _write(out_dir, result)
    sch = result["schedule"]
    if note and sch:
        note(
            f"日程列表：读到 {len(sch['weeks'])} 周（当前第 {sch['current_week']} 周，共 {sch['total']} 周），帧在 {out_dir}"
        )
    if not result["closed"]:
        save(img, "stuck")
        raise ResumeBlocked(f"日程列表：点 ✕ {CLOSE_TRIES} 次没回到选行动页（帧在 {out_dir}），停机")
    return result


def _write(out_dir: Path, result: dict) -> None:
    (out_dir / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def apply(schedule: dict | None, session) -> None:
    """读到的整局日程写进局面 produce.schedule（来源 screen）；reduce 在选行动页放进 page_fields.schedule。"""
    if not schedule or session is None:
        return
    from hiro.contracts import Source

    st = session.state
    st.produce = {**(st.produce or {}), "schedule": schedule}
    st.sources["produce.schedule"] = Source("screen", getattr(st, "seq", 0))
