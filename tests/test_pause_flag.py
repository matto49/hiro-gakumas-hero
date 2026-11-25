"""录屏一停就暂停对局（hiro.loop.pause_flag）：旗标出现 → 暂停、消失 → 恢复；开局前等录屏；暂停期间看门狗 / 收尾超时不计时。"""

from hiro.loop import pause_flag as rp

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(rp, "ENABLED", True)
    rp._state.update(since=None, total=0.0)
    yield
    rp._state.update(since=None, total=0.0)


class Flow:
    def __init__(self):
        self.calls = []

    def request_pause(self):
        self.calls.append("pause")

    def request_resume(self):
        self.calls.append("resume")


class Notes:
    def __init__(self):
        self.notes = []

    def note(self, t):
        self.notes.append(t)


def test_flag_appears_pauses_and_disappears_resumes(tmp_path):
    flag = tmp_path / "REC_DOWN"
    flow, sess = Flow(), Notes()
    w = rp.Watcher(flow, session=sess, flag=flag)
    w.tick()
    assert flow.calls == [] and not rp.paused()
    flag.write_text("1793782800 片段 45s 不增长\n", encoding="utf-8")
    w.tick()
    w.tick()  # 还在：不重复暂停
    assert (
        flow.calls == ["pause"]
        and rp.paused()
        and sess.notes[0].startswith("rec_pause:")
        and "片段 45s 不增长" in sess.notes[0]
    )
    flag.unlink()
    w.tick()
    assert flow.calls == ["pause", "resume"] and not rp.paused() and sess.notes[1].startswith("rec_resume:")


def test_reason_strips_epoch(tmp_path):
    flag = tmp_path / "REC_DOWN"
    flag.write_text("1793782800 录屏没启动", encoding="utf-8")
    assert rp.reason(flag) == "录屏没启动"
    flag.write_text("", encoding="utf-8")
    assert rp.reason(flag) == "（没写原因）"
    assert rp.reason(tmp_path / "none") is None


def test_wait_until_recording_before_start(tmp_path):
    flag = tmp_path / "REC_DOWN"
    flag.write_text("1 已停", encoding="utf-8")
    t = [0.0]

    def sleep(s):
        t[0] += s
        if t[0] >= 5:
            flag.unlink(missing_ok=True)

    waited = rp.wait_until_recording(flag=flag, sleep=sleep, clock=lambda: t[0])
    assert waited == 5 and not flag.exists()
    assert rp.wait_until_recording(flag=flag, sleep=sleep, clock=lambda: t[0]) == 0


def test_disabled_does_not_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "ENABLED", False)
    flag = tmp_path / "REC_DOWN"
    flag.write_text("1 已停", encoding="utf-8")
    assert rp.wait_until_recording(flag=flag, sleep=lambda s: (_ for _ in ()).throw(AssertionError())) == 0


def test_active_clock_excludes_pause(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(rp.time, "monotonic", lambda: t[0])
    a0 = rp.active_clock()
    rp._mark(True)
    t[0] += 150  # 暂停 150s
    assert rp.active_clock() == a0  # 暂停期间不走
    rp._mark(False)
    t[0] += 10
    assert rp.active_clock() == a0 + 10


def test_watchdog_does_not_fire_across_pause(monkeypatch):
    """暂停 150s（录屏卡住强杀重启）：恢复后看门狗不能把暂停时间算成没进展。"""
    from types import SimpleNamespace as NS

    from hiro.loop import repeat_guard as rg

    t = [0.0]
    monkeypatch.setattr(rp.time, "monotonic", lambda: t[0])
    g = rg.Guard()  # 默认时钟 = rec_pause.active_clock
    sess = NS(
        guard=g,
        state=NS(page=NS(name="HIF_EXAM")),
        recover=lambda *a: None,
        note=lambda *a: None,
        observe=lambda **k: None,
        record=lambda: None,
    )
    cb = rg.watchdog(sess, clock=lambda: t[0])
    loop = NS(screenshot=np.full((1280, 720, 3), 40, np.uint8))
    t[0] = 100
    cb(loop)
    rp._mark(True)
    t[0] = 250
    rp._mark(False)
    t[0] = 260
    assert cb(loop) is False and g.stalled() == pytest.approx(110)  # 墙钟过了 260s，扣掉暂停 150s


def test_watcher_stop_resumes(tmp_path):
    """进程退出时还在暂停：stop() 先恢复，别把主线程留在暂停里。"""
    flow, flag = Flow(), tmp_path / "REC_DOWN"
    flag.write_text("1 已停", encoding="utf-8")
    w = rp.Watcher(flow, flag=flag)
    w.tick()
    w.stop()
    assert flow.calls == ["pause", "resume"] and not rp.paused()
