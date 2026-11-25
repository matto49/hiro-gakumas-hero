"""新主循环接线（hiro.runtime.play / scenes，HIRO_RUNTIME=own）：非弹窗场景、advance 的范围、run_hif 的分支。"""

from types import SimpleNamespace as NS

from hiro.runtime import play, scenes
from hiro.vision.ocr import Box, Text

import numpy as np
import pytest


def T(text, x, y):
    return Text(text, Box(x - 20, y - 10, x + 20, y + 10))


class FakeRt:
    def __init__(self, fresh=None):
        self.taps, self.recovers = [], []
        self.device = NS(tap=lambda x, y: self.taps.append((x, y)))
        self.screen = NS(shot=lambda: NS(img=np.zeros((1280, 720, 3), np.uint8)))
        self.session = NS(recover=lambda k, d: self.recovers.append(k))
        self.sleep = lambda s: None
        self.fresh = fresh or []


def frame(texts, landscape=False):
    shape = (720, 1280, 3) if landscape else (1280, 720, 3)
    return NS(img=np.zeros(shape, np.uint8), texts=texts)


def test_live_start_only_on_landscape_track_page():
    rt = FakeRt()
    assert scenes.live_start(rt, frame([T("TAP TO START", 640, 600)], landscape=True)) and rt.taps == [(640, 600)]
    assert not scenes.live_start(rt, frame([T("TAP TO START", 640, 600)]))  # 竖屏不管
    assert scenes.live_start(rt, frame([T("作詞 なんとか", 300, 300)], landscape=True)) and rt.taps[-1] == (640, 612)


def test_commu_skip_taps_recognized_skip_only(monkeypatch):
    rt = FakeRt()
    bar = [T("早送り", 600, 1200), T("ログ", 400, 1200), T("SKIP", 200, 1200)]
    monkeypatch.setattr(scenes, "screen_texts", lambda img: bar)
    assert scenes.commu_skip(rt, frame(bar)) and rt.taps == [(200, 1200)]
    monkeypatch.setattr(
        scenes, "screen_texts", lambda img: [T("早送り", 600, 1200), T("SKIP", 200, 900)]
    )  # SKIP 不在左下：不点
    assert not scenes.commu_skip(rt, frame(bar))
    assert not scenes.commu_skip(rt, frame([T("SKIP", 200, 1200)]))  # 没有「早送り」不是剧情


def test_first_run_settings_pick_then_confirm(monkeypatch):
    rt = FakeRt()
    boxes = [T("サポートイベント設定", 360, 400), T("強化済みで獲得する", 360, 700), T("決定", 520, 1160)]
    monkeypatch.setattr(scenes, "screen_texts", lambda img: boxes)
    assert (
        scenes.first_run_settings(rt, frame(boxes))
        and rt.taps == [(360, 700), (520, 1160)]
        and rt.recovers == ["dialog"]
    )


def test_advance_only_bursts_on_safe_pages(monkeypatch):
    import hiro.loop.advance as adv

    rt = FakeRt()
    calls = []
    monkeypatch.setattr(adv, "burst", lambda texts, **kw: calls.append(texts) or 2)
    monkeypatch.setattr(scenes, "commu_skip", lambda rt, f: False)
    monkeypatch.setattr(scenes, "first_run_settings", lambda rt, f: False)
    assert play.advance(rt, frame([]), NS(name="unknown")) is True
    assert play.advance(rt, frame([]), NS(name="HIF_FINALS_RANKING")) is False  # 认得出、但没处理器的页：不乱点
    assert play.advance(rt, frame([], landscape=True), NS(name="LIVE")) is False  # 横屏演出中：不点
    assert (
        play.advance(rt, frame([]), NS(name="ACQUIRE", fields={"kind": "card", "name": "眠気"})) is True
    )  # 判页认出的获得演出（不带快速判页）：连点
    assert play.advance(rt, frame([]), NS(name="DIALOG", fields={})) is False  # 弹窗交给中断层 / 处理器
    assert len(calls) == 2


def test_run_hif_always_runs_new_runtime_without_kaa(monkeypatch, tmp_path):
    """run_hif 只剩新主循环（KAA 主循环 2026-11-24 删了）：HIRO_RUNTIME=kaa 也照样走 hiro.runtime.play，且不 import KAA。"""
    import sys

    import hiro.run_hif as rh

    monkeypatch.setenv("HIRO_RUNTIME", "kaa")
    monkeypatch.chdir(tmp_path)
    called = {}
    monkeypatch.setattr("hiro.runtime.play.run", lambda args, session: called.setdefault("play", args.command) and 0)
    monkeypatch.setattr(
        rh.manifest_mod, "collect", lambda: NS(problems=[], to_dict=dict, exam_state_source="x", exam_advisor="y")
    )
    monkeypatch.setattr(rh, "_write_report", lambda trace, name: called.setdefault("report", name))
    monkeypatch.setattr(sys, "argv", ["run_hif", "resume", "--mode", "main", "--trace", "t-own"])
    assert rh.main() == 0
    assert called == {"play": "resume", "report": "t-own"}
    assert not hasattr(rh, "init_kaa") and not hasattr(rh, "HiroController")


def _run_with(monkeypatch, new_effects, run_effects, command="new"):
    """play.run 的外壳：设备 / Runtime / 开局 / 续跑 / 主循环都换成替身，按给定顺序抛 DateChanged 或正常返回。"""
    from hiro import formation_env
    from hiro.devices import active
    from hiro.loop.date_change import DateChanged
    from hiro.runtime import finish, opening

    calls = []

    def effect(seq, name):
        def f(*a, **k):
            calls.append(name)
            if seq and seq.pop(0) == "date":
                raise DateChanged("日付変更")

        return f

    monkeypatch.setattr(formation_env, "apply_formation_plan", lambda idol: None)
    monkeypatch.setattr(formation_env, "SUPPORT_SET", 4)
    monkeypatch.setattr(formation_env, "MEMORY_SET", 5)
    monkeypatch.setattr(
        "hiro.devices.open_device", lambda: NS(connect=lambda: None, close=lambda: calls.append("close"))
    )
    rt = NS(run=effect(list(run_effects), "run"))
    monkeypatch.setattr(play, "build", lambda args, session, device: (None, rt))
    monkeypatch.setattr(active, "use", lambda r: None)
    monkeypatch.setattr(opening, "new_produce", effect(list(new_effects), "new"))
    monkeypatch.setattr(finish, "finish_produce_end", lambda rt, go_home=False: None)
    monkeypatch.setattr(finish, "resume_produce", lambda rt: calls.append("resume"))
    monkeypatch.setattr(play, "recover_date_change", lambda rt, session: calls.append("recover"))
    args = NS(command=command, idol="i", mode="qualify", main_attr="Da", expect_memory="")
    return play.run(args, NS()), calls


def test_date_change_during_opening_reopens(monkeypatch):
    code, calls = _run_with(monkeypatch, ["date", "ok"], ["ok"])
    assert code == 0 and calls == ["new", "recover", "new", "run", "close"]


def test_date_change_during_play_resumes(monkeypatch):
    code, calls = _run_with(monkeypatch, ["ok"], ["date", "ok"])
    assert code == 0 and calls == ["new", "run", "recover", "resume", "run", "close"]


def test_too_many_date_changes_stop(monkeypatch):
    code, calls = _run_with(monkeypatch, ["ok"], ["date", "date", "date"])
    assert code == 3 and calls.count("recover") == play.MAX_DATE_CHANGES and calls[-1] == "close"


def test_quick_pages_tap_through_or_wait(monkeypatch):
    """快速判页认出的页：带 tap 直接点（ACQUIRE 获得演出：实机 own 第一局卡了 181 秒停机）；不带 tap 的等够了点兜底。"""
    rt = FakeRt()
    hit = NS(name="ACQUIRE", fields={"_quick": {"page": "ACQUIRE", "tap": (360, 1100), "dispatch": None, "fields": {}}})
    assert play.advance(rt, NS(img=np.zeros((1280, 720, 3), np.uint8), texts=[], t=0.0), hit) and rt.taps == [
        (360, 1100)
    ]
    load = NS(
        name="LOADING", fields={"_quick": {"page": "LOADING", "tap": None, "dispatch": None, "fields": {"kind": None}}}
    )
    f = lambda t: NS(img=np.zeros((1280, 720, 3), np.uint8), texts=[], t=t)  # noqa: E731
    assert not play.advance(rt, f(1.0), load) and not play.advance(rt, f(10.0), load)  # 读取页：等
    assert play.advance(rt, f(17.0), load) and rt.taps[-1] == (12, 12)  # 等了 16 秒：点兜底
    pitem = NS(
        name="SELECT_PITEM",
        fields={"_quick": {"page": "SELECT_PITEM", "tap": None, "dispatch": "SELECT_PITEM", "fields": {}}},
    )
    monkeypatch.setattr(scenes, "commu_skip", lambda rt, fr: False)
    monkeypatch.setattr(scenes, "first_run_settings", lambda rt, fr: False)
    assert not play.advance(rt, f(20.0), pitem)  # 要决策的页（dispatch）不在这里点


def test_session_classify_uses_runtime_device_under_own(monkeypatch):
    """新主循环下 Session.verify / HifCore 的判页（scene.classify_current）从 Runtime 截图，不碰 kotonebot（实机：选完行动核对时崩）。"""
    from hiro.devices import active
    from hiro.vision import scene

    rt, seen = FakeRt(), []
    monkeypatch.setattr(scene, "classify_scene", lambda img: seen.append(img.shape) or NS(name="LIVE", area="produce"))
    active.use(rt)
    try:
        r = scene.classify_current()
    finally:
        active.use(None)
    assert seen == [(1280, 720, 3)] and r.scene == "unknown" and r.area == "unknown"


def test_produce_end_pages_hand_off_to_finish_and_stop(monkeypatch):
    """选拔打完后的回忆照片页（「メモリーにするフォトを選んでください」，DMM 11-14 判页认不出、advance 不点，180 秒停机）：
    交给 finish.finish_produce_end 点到开始确认页停，这一段结束。"""
    from hiro.runtime import finish

    rt, called = FakeRt(), []
    monkeypatch.setattr(finish, "finish_produce_end", lambda r, **kw: called.append(kw) or True)
    photo = frame([T("メモリーにするフォトを選んでください", 360, 440), T("次へ", 360, 1160)])
    assert play.advance(rt, photo, NS(name="unknown")) is True and called == [{}] and rt.finished is True
    rt2 = FakeRt()
    monkeypatch.setattr(scenes, "commu_skip", lambda rt, f: False)
    monkeypatch.setattr(scenes, "first_run_settings", lambda rt, f: False)
    import hiro.loop.advance as adv

    monkeypatch.setattr(adv, "burst", lambda texts, **kw: 0)
    assert play.advance(rt2, frame([T("次へ", 360, 1160)]), NS(name="unknown")) is False and not getattr(
        rt2, "finished", False
    )


def _segment(monkeypatch, new_effects, run_effects, *, resume_only=False):
    """play.segment_with_date_changes（初 / N.I.A 的开局 + 主循环外壳）：按顺序抛 DateChanged 或正常返回。"""
    from hiro.loop.date_change import DateChanged

    calls = []

    def effect(seq, name):
        def f():
            calls.append(name)
            if seq and seq.pop(0) == "date":
                raise DateChanged("日付変更")

        return f

    monkeypatch.setattr(play, "recover_date_change", lambda rt, session: calls.append("recover"))
    play.segment_with_date_changes(
        NS(), NS(), new=None if resume_only else effect(list(new_effects), "new"),
        resume=lambda: calls.append("resume"), play_until=effect(list(run_effects), "run"),
    )  # fmt: skip
    return calls


def test_segment_date_change_before_opening_reopens(monkeypatch):
    """11-25 账号 3：开「初」时正赶上日本时间 5 点日切换，开局前的结算收尾就抛 DateChanged，hajime.run 没接、整个 traceback 退出。"""
    assert _segment(monkeypatch, ["date", "ok"], ["ok"]) == ["new", "recover", "new", "run"]


def test_segment_date_change_during_play_resumes(monkeypatch):
    assert _segment(monkeypatch, ["ok"], ["date", "ok"]) == ["new", "run", "recover", "resume", "run"]


def test_segment_resume_command(monkeypatch):
    assert _segment(monkeypatch, [], ["ok"], resume_only=True) == ["resume", "run"]


def test_segment_too_many_date_changes_stops(monkeypatch):
    from hiro.loop.errors import ResumeBlocked

    with pytest.raises(ResumeBlocked, match="日切换"):
        _segment(monkeypatch, ["date"] * 5, ["ok"])
