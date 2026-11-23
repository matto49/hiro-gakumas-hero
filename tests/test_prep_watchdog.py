"""D38 第三十三局：KAA 培育准备（选偶像、切编成、租借、选回忆）整段 186s，主循环还没接手，页面一直 unknown、局面没变，
空转看门狗从会话建立（17:52:48）算到 17:55:54 停机（停机帧是开始确认页「プロデュース開始」加载圈）。
现在准备的每一步前后、选偶像每翻一页、租借每扫一屏都报进展。按那局日志的时间线回放（秒，从 17:52:48.7 算）。"""

from types import SimpleNamespace as NS

from hiro.loop import kaa_patches, repeat_guard
from hiro.loop.repeat_guard import Guard, Stalled, watchdog
from hiro.produce import rental

import pytest


class Clock:
    def __init__(self):
        self.t = 0.0


def _session(clock):
    s = NS(guard=Guard(clock=lambda: clock.t), state=NS(page=NS(name="UNKNOWN")), notes=[])
    s.recover = lambda *a: None
    s.note = s.notes.append
    s.observe = lambda **k: None
    s.record = lambda *a, **k: None
    return s


def _replay(clock, cb, steps):
    """steps：KAA 培育准备的调用（按日志时间线推进时钟、中间每 5s 跑一次看门狗回调）。"""

    def run_to(t):
        while clock.t + 5 <= t:
            clock.t += 5
            cb(NS(screenshot=None))
        clock.t = t

    run_to(12)  # Going home（17:53:01）
    run_to(32)  # Enter produce page → 选偶像（17:53:20.9）
    steps["step1"](run_to)
    run_to(71)  # 17:53:59.8 切编成 #4
    steps["step2"](run_to)
    run_to(147)  # 17:55:15 第 3 步回忆选择
    steps["step3"](run_to)
    steps["step4"](run_to)
    run_to(200)  # 点开始 → 首次设置框；主循环接手前的几秒
    return clock.t


def _kaa_steps(prep):
    def step1(run_to):  # 17:53:20.9–17:53:52.7：选偶像，第 3 页找到（17:53:50）
        def body():
            for t in (40, 47):
                run_to(t)
                kaa_patches._progress(f"找偶像：第 {t} 页没有")
            run_to(64)

        prep["step1"](body)

    def step2(run_to):  # 17:53:59.8 切编成到 17:54:05.7；租借 17:54:08.6 勾プラン適正 → 17:55:03.8 选定
        def body():
            prep["_select_set"](lambda: run_to(77))
            for t in range(80, 136, 8):
                run_to(t)
                rental.progress(f"租借 {t}")
            run_to(141)

        prep["step2"](body)

    def step3(run_to):  # 17:55:15–17:55:47.5：截图、切编成 #5
        def body():
            run_to(170)
            prep["_select_set"](lambda: run_to(179))
            run_to(181)

        prep["step3"](body)

    def step4(run_to):
        prep["step4"](lambda: run_to(190))

    return {"step1": step1, "step2": step2, "step3": step3, "step4": step4}


def test_g33_prep_timeline_no_longer_stalls():
    clock = Clock()
    s = _session(clock)
    cb = watchdog(s, every=5, clock=lambda: clock.t)
    prep = {n: kaa_patches._prep_progress(lambda f: f(), n) for n in kaa_patches.PREP_STEPS}
    assert _replay(clock, cb, _kaa_steps(prep)) == 200
    assert s.guard.stalled() < 60  # 准备期间最长一段没进展：会话建立到选偶像第一页（40s）


def test_g33_prep_timeline_without_progress_stalls_like_before(monkeypatch):
    clock = Clock()
    s = _session(clock)
    cb = watchdog(s, every=5, clock=lambda: clock.t)
    monkeypatch.setattr(repeat_guard, "progress", lambda why="": None)  # 修之前：准备阶段没人报进展
    prep = {n: (lambda f: f()) for n in kaa_patches.PREP_STEPS}
    with pytest.raises(Stalled):
        _replay(clock, cb, _kaa_steps(prep))
    assert 180 <= clock.t <= 190  # 和实机一样在点开始前后停机


def test_single_prep_step_hanging_still_stalls():
    clock = Clock()
    s = _session(clock)
    cb = watchdog(s, every=5, clock=lambda: clock.t)
    step = kaa_patches._prep_progress(
        lambda: [cb(NS(screenshot=None)) for _ in range(40) if not setattr(clock, "t", clock.t + 5)], "step2"
    )
    with pytest.raises(Stalled):
        step()


def test_progress_wrapper_keeps_marks_and_is_not_rewrapped():
    def step2(set_number=None, auto_set=None):
        return set_number

    step2._hiro_rent = True
    w = kaa_patches._prep_progress(step2, "step2")
    assert w(set_number=4) == 4 and w._hiro_rent and w._hiro_progress and w.__wrapped__ is step2


def test_progress_without_session_is_noop(monkeypatch):
    monkeypatch.setitem(repeat_guard.ACTIVE, "session", None)
    repeat_guard.progress("没有会话")  # 不报错
