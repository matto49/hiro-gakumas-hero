"""新主循环的 LLM 看图兜底（hiro.loop.rescue 接到 hiro.runtime）：11-23 Mac 初 REGULAR，「サポートイベント効果」只有 OK 的通知框
没有处理器，等到 180 秒停机；旧主循环有 LLM 兜底，换成 runtime 后没接上。全用假设备 / 假兜底，不调模型。"""

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import DONE, STAY, PageHit, Runtime
from hiro.runtime.frame import Frame

import numpy as np
import pytest


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class Dev:
    name = "fake"

    def __init__(self):
        self.taps = []

    def screenshot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def tap(self, x, y):
        self.taps.append((x, y))


class FakeRescuer:
    """due 到点就 begin（后台），下一拍 poll 回一条记录；action 由测试给。"""

    def __init__(self, action="tap_box"):
        self.action, self.pending, self.begun, self.progress = action, False, [], 0

    def due(self, stuck_s, *, dialog=False, img=None):
        return stuck_s >= (3 if dialog else 12)

    def begin(self, img, context, boxes=None):
        self.begun.append(context)
        self.pending = True

    def poll(self):
        self.pending = False
        return {"action": {"action": self.action}, "blocked": None}

    def progressed(self):
        self.progress += 1


def make(rescuer, handlers=None, page="DIALOG"):
    clock = Clock()
    rt = Runtime(Dev(), lambda img: PageHit(page, "template"), handlers=handlers or {}, clock=clock, sleep=clock.sleep,
                 idle_stop_s=180, rescuer=rescuer)  # fmt: skip
    n = {"i": 0}

    def shot():
        n["i"] += 1
        f = Frame(np.full((1280, 720, 3), n["i"] % 2 * 100, np.uint8), clock.now)  # 画面一直在变：只有「没有操作」会停
        f.__dict__.update(texts=[], has_dialog=False, dialog=None)
        return f

    rt.screen.shot = shot
    return rt, clock


def run(rt, clock, seconds, every=5):
    for _ in range(int(seconds / every)):
        rt.step()
        clock.now += every


def test_unhandled_dialog_goes_to_llm_and_counts_as_acted():
    r = FakeRescuer("tap_box")
    rt, clock = make(r)
    run(rt, clock, 400)  # 远超 180 秒：兜底一直有动作，不停机
    assert r.begun and r.begun[0]["page"] == "DIALOG"


def test_rescue_giving_up_still_stops_on_idle():
    rt, clock = make(FakeRescuer("stop"))
    with pytest.raises(ResumeBlocked, match="没有任何操作"):
        run(rt, clock, 400)


def test_progress_resets_rescuer():
    r = FakeRescuer("tap_box")
    rt, clock = make(r, handlers={"home": lambda rt, f, hit: DONE}, page="home")
    run(rt, clock, 30)
    assert r.progress >= 1 and not r.begun  # 有处理器在干活：不问 LLM


def test_off_by_default_without_rescue_flag():
    clock = Clock()
    rt = Runtime(Dev(), lambda img: PageHit("DIALOG", "template"), clock=clock, sleep=clock.sleep)
    assert rt.rescuer is None
    _ = STAY


def test_rescue_flag_builds_real_rescuer_unless_env_off(monkeypatch):
    from hiro.loop import rescue

    clock = Clock()
    rt = Runtime(Dev(), lambda img: PageHit("DIALOG", "template"), clock=clock, sleep=clock.sleep, rescue=True)
    assert isinstance(rt.rescuer, rescue.Rescuer)
    monkeypatch.setattr(rescue, "LLM_UNKNOWN", False)  # HIRO_RESCUE_LLM=0
    rt2 = Runtime(Dev(), lambda img: PageHit("DIALOG", "template"), clock=clock, sleep=clock.sleep, rescue=True)
    assert rt2.rescuer is None


def test_frozen_unknown_screen_goes_to_llm_even_if_advance_keeps_tapping():
    """G0b 后半（11-26 03:44 偶像之路从 H.I.F 回忆编成页起跑）：认不出的画面上 advance 一直点空白（跳过场用），每点一下都算「有操作」，
    兜底的空闲计时永远到不了 12s，只能等 180s 停机。画面冻住（点了也不变）够久就交给 LLM 看。"""
    from hiro.runtime import UNKNOWN

    r = FakeRescuer("tap_box")
    clock = Clock()
    rt = Runtime(Dev(), lambda img: UNKNOWN, handlers={}, advance=lambda rt, f, hit: True, clock=clock, sleep=clock.sleep,
                 idle_stop_s=180, rescuer=r)  # fmt: skip

    def shot():
        f = Frame(np.full((1280, 720, 3), 50, np.uint8), clock.now)  # 点了也不变：冻住的画面
        f.__dict__.update(texts=[], has_dialog=False, dialog=None)
        return f

    rt.screen.shot = shot
    run(rt, clock, 30)
    assert r.begun, "冻住的认不出画面要交给 LLM 兜底"
    assert r.progress <= 1, "第一帧建立基准算一次进展；之后点空白、画面没变都不算"


def test_changing_unknown_screen_with_advance_does_not_bother_llm():
    """过场动画（画面一直在变）照旧由 advance 点过去，不找 LLM。"""
    from hiro.runtime import UNKNOWN

    r = FakeRescuer("tap_box")
    clock = Clock()
    rt = Runtime(Dev(), lambda img: UNKNOWN, handlers={}, advance=lambda rt, f, hit: True, clock=clock, sleep=clock.sleep,
                 idle_stop_s=180, rescuer=r)  # fmt: skip
    n = {"i": 0}

    def shot():
        n["i"] += 1
        f = Frame(np.full((1280, 720, 3), n["i"] % 2 * 100, np.uint8), clock.now)
        f.__dict__.update(texts=[], has_dialog=False, dialog=None)
        return f

    rt.screen.shot = shot
    run(rt, clock, 30)
    assert not r.begun
