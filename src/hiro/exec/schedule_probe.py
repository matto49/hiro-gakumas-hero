"""考试审查日程探测（HIRO_EXAM_SCHEDULE_PROBE=1 打开，默认关）。

任务 ①（实战局面读准）：UINet 要后面回合的属性顺序，画面路径现在是按配额猜的（3.4% 的选择因此不同）。
考试左上角「残りターン / ビジュアル 900%」那块在客户端布局里是 ExamAuditionBonusScheduleView，带可点的 BonusButtonArea
（x 9–222，y 40–140）；点开后的面板没有布局、也没有截图。这个探测只做一件事：每场考试第 1 回合出牌前点开一次，
存下打开后的帧和关闭过程，记耗时，回到考试画面再继续。读面板内容等拿到帧以后再写。

关闭：先找面板上的「閉じる」/ ✕，找不到再点同一块（可能是开关式）。两次都回不到考试画面就停机（ResumeBlocked），
不在考试里乱点。点的位置在左上角，离手牌（y≈860 以下）和出牌区很远。
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable

import numpy as np

import logging

from hiro.devices.active import sleep as _sleep

logger = logging.getLogger(__name__)

ENABLED_ENV = "HIRO_EXAM_SCHEDULE_PROBE"
BONUS_TAP = (115, 95)  # BonusButtonArea 中部（720x1280）
CLOSE_X = (360, 1160)  # 面板底部圆形 ✕
CLOSE_TEXTS = ("閉じる", "とじる", "✕", "×")
OPEN_WAIT = 5.0  # 点了以后等面板出来（白色大面板盖上来才算打开）
CLOSE_WAIT = 3.0  # 每次关闭后等回到考试画面
CHANGED_MIN = 8.0  # 整屏灰度平均差：面板盖上来远大于这个；同一考试画面两帧（角色在动）实测 < 4


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV) == "1"


# 只在有审查日程面板的考试 / 试镜里点（协调 11-18 初 PRO 实机：课程第 1 回合前点开了「残りターン数」框的回合详情，
# 关闭判定只认 H.I.F 考试画面、认不出课程画面，停机）。课程（hajime-lesson-*）、偶像之路（tower*）不点。
EXAM_LABEL = re.compile(r"^(qualify-\d|r[12]|hajime-(mid1|final)|nia-(mid1|mid2|final))(#.*)?$")


def should_probe(label: str | None) -> bool:
    return bool(label) and EXAM_LABEL.match(str(label)) is not None


def back_in_exam(img) -> bool:
    """回到考试画面了：H.I.F 考试、初 / N.I.A 考试（判页用偶像之路那套判据认中，TOWER_EXAM）、课程 HUD 任一认得出就算。"""
    from hiro.vision.hajime import in_lesson
    from hiro.vision.hif import is_hif_exam
    from hiro.vision.tower import is_tower_exam

    try:
        return bool(is_hif_exam(img) or is_tower_exam(img) or in_lesson(img))
    except Exception:  # noqa: BLE001  识别异常当没回来，交给超时
        return False


def screen_diff(a: np.ndarray, b: np.ndarray) -> float:
    import cv2

    ga = cv2.cvtColor(cv2.resize(a, (180, 320)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(cv2.resize(b, (180, 320)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean())


def _wait(pred: Callable[[np.ndarray], bool], timeout: float, shoot, clock, sleep) -> tuple[bool, np.ndarray]:
    end = clock() + timeout
    img = shoot()
    while not pred(img):
        if clock() >= end:
            return False, img
        sleep(0.25)
        img = shoot()
    return True, img


def probe(
    before: np.ndarray,
    *,
    exam: str,
    shoot: Callable[[], np.ndarray],
    click: Callable[[int, int], None],
    ocr_boxes: Callable[[np.ndarray], list],
    is_exam: Callable[[np.ndarray], bool],
    log: Callable[..., object] | None = None,
    note: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
    read_panel: Callable[[np.ndarray], dict | None] | None = None,
    swipe: Callable[..., None] | None = None,
    total_turns: int | None = None,
) -> dict:
    """点开审查日程、存帧、读面板（read_panel，hiro.vision.schedule.read_schedule_panel）、关掉，返回
    {opened, closed, close_by, open_ms, close_ms, total_ms, texts, panel}；panel = {turn_types, permil, pass_rank, pass_score, current_turn}
    或 None。行数比总回合少时往下滑接着读（最多 SCROLLS 次）。关不掉抛 ResumeBlocked。"""
    from hiro.loop.errors import ResumeBlocked
    from hiro.produce.ops import find_text

    t0 = clock()
    click(*BONUS_TAP)
    from hiro.vision.schedule import panel_open

    # 按白色大面板认打开，不按整屏差（协调 11-18 N.I.A MASTER：试镜里偶像在跳舞，整屏差过阈值判成「打开了」，面板其实没出来，
    # 读数全 None，还在出牌区下面点了 ✕ 的位置）
    opened, img = _wait(panel_open, OPEN_WAIT, shoot, clock, sleep)
    t_open = clock()
    img = _settle(shoot, sleep, clock)  # 面板入场动画：等停稳再读（原来固定 0.5 秒，最終那次第一屏「現在のターン」没读到）
    boxes = ocr_boxes(img)
    texts = [t for t, _, _ in boxes]
    if log:
        log("exam_schedule_probe_open", image=img, exam=exam, opened=opened, texts=texts)
    out = {"opened": opened, "closed": None, "close_by": None, "open_ms": round((t_open - t0) * 1000), "texts": texts, "panel": None}
    panels = []
    if opened and read_panel is not None:
        panels = _read_panels(img, read_panel, swipe, total_turns, shoot, sleep, log, exam, clock)
        if panels:
            from hiro.vision.schedule import turn_effects, turn_types

            first = panels[0]
            out["panel"] = {"turn_types": turn_types(panels, total_turns), "turn_effects": turn_effects(panels, total_turns),
                            "permil": _merge_permil(panels), "pass_rank": first.get("pass_rank"), "pass_score": first.get("pass_score"),
                            "pass_text": next((q["pass_text"] for q in panels if q.get("pass_text")), None), "current_turn": first.get("current_turn")}  # fmt: skip
    if not opened:
        # 画面没变：点了没反应（或者不是可点的区域）。没有面板要关，照常继续
        out.update(closed=True, close_by="not_opened", close_ms=0, total_ms=round((clock() - t0) * 1000))
        _report(out, exam, note)
        return out
    _settle(shoot, sleep, clock)  # 滑过以后面板还在惯性滚动：这时点 ✕ 只会让滚动停下（初 PRO 最終 11-18：2 种关法都没关上）
    # 面板底部中间的圆形 ✕（实拍 (360,1160)；OCR 读成「X」）。点两次：第一下万一只是停住了滚动
    attempts = [("close_x", CLOSE_X), ("close_x", CLOSE_X)]
    hit = find_text(boxes, CLOSE_TEXTS)
    if hit is not None:
        attempts.append(("close_text", hit))
    attempts.append(("retap", BONUS_TAP))
    t_close = clock()
    back = False
    for how, xy in attempts:
        click(*xy)
        back, img = _wait(lambda im: _closed(im, before, is_exam), CLOSE_WAIT, shoot, clock, sleep)
        if back:
            out["close_by"] = how
            break
        boxes = ocr_boxes(img)
        if (h2 := find_text(boxes, CLOSE_TEXTS)) is not None and ("close_text", h2) not in attempts:
            attempts.append(("close_text", h2))
    out.update(closed=back, close_ms=round((clock() - t_close) * 1000), total_ms=round((clock() - t0) * 1000))
    if log:
        log("exam_schedule_probe_closed", image=img, exam=exam, closed=back, close_by=out["close_by"])
    _report(out, exam, note)
    if not back:
        raise ResumeBlocked(f"[{exam}] 审查日程探测：点开后 {len(attempts)} 种关法都没回到考试画面，停机（帧见 trace）")
    return out


SETTLE_DIFF = 1.5  # 相邻两帧灰度缩略图平均差小于这个算停稳
SETTLE_MAX = 3.0


def _settle(shoot, sleep, clock, timeout: float = SETTLE_MAX):
    """等画面停稳（相邻两帧几乎不变），最多 timeout 秒；返回最后一帧。"""
    end = clock() + timeout
    prev = shoot()
    while True:
        sleep(0.3)
        img = shoot()
        if screen_diff(prev, img) < SETTLE_DIFF or clock() >= end:
            return img
        prev = img


def _closed(img, before, is_exam) -> bool:
    """面板关上了：白色大面板不在了，并且认得出考试 / 课程 HUD 或画面和打开前差不多。"""
    from hiro.vision.schedule import panel_open

    return not panel_open(img) and (is_exam(img) or screen_diff(before, img) < CHANGED_MIN)


SCROLLS = 4
SCROLL_FROM, SCROLL_TO = (360, 950), (360, 450)  # 在面板的行区里往上滑
# 面板打开时行区往往已经滚过「現在のターン」那一行（N.I.A 最終 12 回合、初 最終：第一行是「1ターン後」，当前回合读不到）：
# 先往下拖一下回到顶，读当前回合那一行，再往后滑（协调 11-19）
TOP_FROM, TOP_TO = (360, 500), (360, 900)


def _merge_permil(panels: list[dict]) -> dict | None:
    """几屏的倍率读数合在一起：每项取第一个过了合理性校验的值。"""
    out: dict = {}
    for p in panels:
        for a, v in (p.get("permil") or {}).items():
            out.setdefault(a, v)
    return out or None


def _read_panels(img, read_panel, swipe, total_turns, shoot, sleep, log, exam, clock=time.monotonic) -> list[dict]:
    """读一屏；行没覆盖到最后一回合时往下滑再读（12 回合的 H.I.F R2 一屏放不下）。"""
    panels = []
    p = read_panel(img)
    if p is None:
        return panels
    panels.append(p)
    if swipe is not None and p.get("current_turn") is not None and 0 not in (p.get("rows") or {}):
        swipe(*TOP_FROM, *TOP_TO)  # 当前回合那一行在上面滚出去了：拖回顶读一次
        top_img = _settle(shoot, sleep, clock)
        top = read_panel(top_img)
        if top is not None and top.get("rows"):
            if log:
                log("exam_schedule_probe_top", image=top_img, exam=exam, rows=top.get("rows"))
            panels.append(top)
            p = top  # 之后从顶往下滑
    for _ in range(SCROLLS):
        cur, rows = p.get("current_turn"), p.get("rows") or {}
        if swipe is None or not total_turns or cur is None or not rows or cur + max(rows) >= total_turns:
            break
        swipe(*SCROLL_FROM, *SCROLL_TO)
        img = _settle(shoot, sleep, clock)  # 滑动有惯性：停稳再读
        p = read_panel(img)
        if p is None or not p.get("rows"):
            break
        if log:
            log("exam_schedule_probe_scroll", image=img, exam=exam, rows=p.get("rows"))
        panels.append(p)
    return panels


def apply_panel(panel: dict | None, session, memory) -> None:
    """面板读数写进考试局面（来源 screen）：exam.turn_types（下标 0 = 第 1 回合，读不到的回合 None）、exam.attr_permil（三属性倍率，
    画面真值，8a3a1e7 的搜索用）、exam.pass_line {rank, score, stage（过线条件原文）}；倍率也记进本场考试记忆（ExamMemory.attr_permil）。"""
    if not panel:
        return
    from hiro.contracts import Source

    if memory is not None and panel.get("permil"):
        memory.attr_permil.update(panel["permil"])
    if session is None:
        return
    st = session.state
    ex = dict(st.exam or {})
    seq = getattr(st, "seq", 0)
    if panel.get("turn_types"):
        ex["turn_types"] = list(panel["turn_types"])
        st.sources["exam.turn_types"] = Source("screen", seq)
    if panel.get("permil"):
        ex["attr_permil"] = {**(ex.get("attr_permil") or {}), **panel["permil"]}
        st.sources["exam.attr_permil"] = Source("screen", seq)
    if panel.get("turn_effects"):
        ex["turn_effects"] = list(panel["turn_effects"])  # 每回合的回合效果文字（gimmick），下标同 turn_types
        st.sources["exam.turn_effects"] = Source("screen", seq)
    if panel.get("pass_rank") is not None or panel.get("pass_score") is not None or panel.get("pass_text"):
        # stage = 过线条件原文（「3位以上で中間試験突破」/ N.I.A「…オーディション突破」），8a3a1e7 按关键词认场次（11-18）
        ex["pass_line"] = {"rank": panel.get("pass_rank"), "score": panel.get("pass_score"), "stage": panel.get("pass_text")}
        st.sources["exam.pass_line"] = Source("screen", seq)
    st.exam = ex


def _report(out: dict, exam: str, note) -> None:
    msg = (f"[{exam}] 审查日程探测：{'打开了' if out['opened'] else '点了没打开'}，"
           f"{'关闭方式 ' + str(out['close_by']) if out['closed'] else '没关掉'}；打开 {out['open_ms']}ms，"
           f"关闭 {out.get('close_ms')}ms，共 {out.get('total_ms')}ms；读数 {out.get('panel')}；面板文字 {out['texts'][:12]}")  # fmt: skip
    logger.info(msg)
    if note:
        note(msg)
