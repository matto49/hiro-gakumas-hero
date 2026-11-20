"""N.I.A 采样模式（hiro.runtime.nia_sample）：开局选难度、认不出的页不点且存帧停下、考试标签、存帧去重。假设备，不连游戏。"""

import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import nia_sample as ns
from hiro.vision.classify import UNKNOWN, PageHit
from test_opening import _texts, frame, rig  # 同目录（pytest 把测试目录放进 sys.path）


def test_choose_difficulty_taps_pro_then_waits_step1(monkeypatch):
    page = frame({}, _texts(("プロ", 180, 876), ("マスター", 540, 876)))
    step1 = frame({"Produce.TextStepIndicator1": (93, 49)})
    rt, _ = rig(monkeypatch, [page], on_tap=lambda taps: [step1])
    ns.choose_difficulty(rt, "pro")
    assert rt.raw_device.taps == [(180, 876)]


def test_choose_difficulty_master(monkeypatch):
    page = frame({}, _texts(("プロ", 180, 876), ("マスター", 540, 876)))
    rt, _ = rig(monkeypatch, [page], on_tap=lambda taps: [frame({"Produce.TextStepIndicator1": (93, 49)})])
    ns.choose_difficulty(rt, "master")
    assert rt.raw_device.taps == [(540, 876)]


def _rt(tmp_path, now):
    from hiro.runtime.frame import Frame

    taps = []
    rt = NS(clock=lambda: now[0], device=NS(tap=lambda x, y: taps.append((x, y))), session=None, sleep=lambda s: None,
            screen=NS(shot=lambda: Frame(np.zeros((1280, 720, 3), np.uint8), now[0])))  # fmt: skip
    return rt, taps


def test_unknown_page_with_buttons_not_tapped_then_stops_with_frame(tmp_path, monkeypatch):
    """认不出、画面上有按钮字样（キャンセル 等）：不点，不交给 play.advance 连点，stop_s 后存帧停下。"""
    from hiro.runtime import play
    from hiro.runtime.frame import Frame
    from hiro.vision.ocr import Box, Text

    monkeypatch.setattr(play, "advance", lambda *a: pytest.fail("认不出的画面不该交给 play.advance 连点"))
    now = [0.0]
    rt, taps = _rt(tmp_path, now)
    sampler = ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames")
    adv = ns.make_advance(sampler, stop_s=15)
    f = Frame(np.full((1280, 720, 3), 90, np.uint8), 0.0)
    f.__dict__["texts"] = [Text("キャンセル", Box(150, 1140, 280, 1180))]
    assert adv(rt, f, UNKNOWN) is False
    now[0] = 10.0
    assert adv(rt, f, UNKNOWN) is False and taps == []
    now[0] = 16.0
    with pytest.raises(ResumeBlocked, match="认不出"):
        adv(rt, f, UNKNOWN)
    assert list((tmp_path / "frames").glob("stop-*-unknown.jpg"))


def test_quiet_unknown_tapped_up_to_cap_then_stops(tmp_path, monkeypatch):
    """认不出、没有任何字的过场：每 3 秒点一下 (12,12)，最多 QUIET_TAP_MAX 下；还认不出就照旧 stop_s 后停下。"""
    from hiro.runtime import play
    from hiro.runtime.frame import Frame

    monkeypatch.setattr(play, "advance", lambda *a: pytest.fail("认不出的画面不该交给 play.advance 连点"))
    now = [0.0]
    rt, taps = _rt(tmp_path, now)
    adv = ns.make_advance(ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames"), stop_s=15)
    f = Frame(np.full((1280, 720, 3), 90, np.uint8), 0.0)
    f.__dict__["texts"] = []
    with pytest.raises(ResumeBlocked, match="认不出"):
        for _ in range(200):
            adv(rt, f, UNKNOWN)
            now[0] += 1.0
    assert taps == [ns.BUBBLE_TAP] * ns.QUIET_TAP_MAX


def test_known_advance_page_delegates(tmp_path, monkeypatch):
    from hiro.runtime import play
    from hiro.runtime.frame import Frame

    called = []
    monkeypatch.setattr(play, "advance", lambda rt, f, hit: called.append(hit.name) or True)
    now = [0.0]
    rt, _ = _rt(tmp_path, now)
    adv = ns.make_advance(ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames"))
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    assert adv(rt, f, PageHit("ACQUIRE", "quick")) is True and called == ["ACQUIRE"]


def test_sampler_saves_on_change_and_maps_tower_exam(tmp_path):
    hits = iter([PageHit("ACTION_SELECT", "x"), PageHit("ACTION_SELECT", "x"), PageHit("TOWER_EXAM", "tower_ocr"), PageHit("TOWER_LIST", "ocr")])
    s = ns.Sampler(lambda img: next(hits), tmp_path / "frames")
    a, b = np.full((1280, 720, 3), 10, np.uint8), np.full((1280, 720, 3), 200, np.uint8)
    assert s(a).name == "ACTION_SELECT"
    assert s(a).name == "ACTION_SELECT"  # 没变：不存
    assert s(b).name == "EXAM"  # N.I.A 试镜被偶像之路的考试判据认中：当考试
    assert s(b).name == "unknown"  # 其余 TOWER_* 当认不出
    rows = [json.loads(line) for line in (tmp_path / "frames" / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["page"] for r in rows] == ["ACTION_SELECT", "EXAM", "unknown"]


def test_exam_labels_by_count():
    Core = ns.nia_core("produce-004")
    c = Core.__new__(Core)
    c.exam_count = 0
    assert [c._battle_label(None) for _ in range(4)] == ["nia-mid1", "nia-mid2", "nia-final", "nia-final"]


def test_set_mode():
    s = NS(state=NS(produce={"step": 3}))
    ns.set_mode(s, "produce-004")
    assert s.state.produce == {"step": 3, "mode": "produce-004"}


def test_sampler_save_tap_always_saves(tmp_path):
    """开局阶段（rt.drive 不调 classify）每次点击都存一帧，同一画面也存。"""
    from hiro.runtime.frame import Frame

    s = ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames")
    f = Frame(np.full((1280, 720, 3), 50, np.uint8), 0.0)
    s.save_tap(f, 34, 596)
    s.save_tap(f, 34, 596)
    rows = [json.loads(line) for line in (tmp_path / "frames" / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["page"] for r in rows] == ["tap-34-596", "tap-34-596"]


FIXP = __import__("pathlib").Path(__file__).with_name("fixtures") / "pages"


def _real(name):
    import cv2

    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


def test_self_lesson_options_on_real_frame():
    """N.I.A 第 1 周「Vo. / Da. / Vi. 自主レッスン」（实机帧）：training + 属性 + SP（原来 attr=None，决策找不到按钮）。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("ACTION_SELECT", _real("nia-action-week1.png"))
    opts = observation(1, 0.0, "ACTION_SELECT", raw).fields["options"]
    assert [(o["option"], o["attr"], o.get("sp"), o["hp_cost"]) for o in opts] == [("training", a, True, 8) for a in ("Vo", "Da", "Vi")]


def test_param_up_bubble_tapped_on_unknown(tmp_path, monkeypatch):
    """自主课结算「Da.が147上昇した!」（实机帧）判 unknown：底部气泡是结算类的话就点一下，不按认不出停。"""
    from hiro.runtime import play
    from hiro.runtime.frame import Frame

    monkeypatch.setattr(play, "advance", lambda *a: pytest.fail("认不出的画面不交给 play.advance"))
    now = [0.0]
    rt, taps = _rt(tmp_path, now)
    adv = ns.make_advance(ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames"))
    assert adv(rt, Frame(_real("nia-param-up-bubble.jpg"), 0.0), UNKNOWN) is True and taps == [ns.BUBBLE_TAP]


def test_upgrade_result_is_a_show_page(tmp_path):
    """支援卡事件强化后的「〜を強化しました」（实机帧，page_ocr 判 UPGRADE_RESULT）：advance 点一下就过
    （原来交 burst，被禁点词「強化」拦住）。"""
    from hiro.runtime import play
    from hiro.runtime.frame import Frame

    now = [0.0]
    rt, taps = _rt(tmp_path, now)
    f = Frame(_real("nia-upgrade-result.jpg"), 0.0)
    assert play.advance(rt, f, PageHit("UPGRADE_RESULT", "page_ocr")) is True and taps


def test_consult_recognized_without_review_criteria():
    """N.I.A 右上没有「審査基準」：相談页（被支援能力横幅盖住页头的实机帧）也要认成 CONSULT。"""
    from hiro.vision.classify import classify

    assert classify(_real("nia-consult-banner.jpg")).name == "CONSULT"


def test_consult_end_confirm_interrupt():
    """「相談終了確認」单独留在画面上（实机帧）：中断层点「決定」。"""
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    rt, _ = runtime([fake_frame()])
    assert "相談終了" in interrupts.check(Frame(_real("nia-consult-end-confirm.jpg"), 0.0), rt)
    (x, y), = rt.raw_device.taps
    assert x > 400 and abs(y - 1160) < 20


def test_support_event_art_tapped_on_unknown(tmp_path, monkeypatch):
    """支援卡事件插画过场（实机帧：蓝绿几何底 + 横幅插画，没有字）判 unknown：点一下；纯蓝绿闪光不点。"""
    from hiro.runtime import play
    from hiro.runtime.frame import Frame

    monkeypatch.setattr(play, "advance", lambda *a: pytest.fail("认不出的画面不交给 play.advance"))
    now = [0.0]
    rt, taps = _rt(tmp_path, now)
    adv = ns.make_advance(ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames"))
    assert adv(rt, Frame(_real("nia-support-event-art.jpg"), 0.0), UNKNOWN) is True and taps == [ns.BUBBLE_TAP]
    flash = np.zeros((1280, 720, 3), np.uint8)
    flash[:] = (215, 190, 0)  # BGR 纯蓝绿
    taps.clear()
    assert adv(rt, Frame(flash, 0.0), UNKNOWN) is False and taps == []


# ---------------------------------------------------------------- 没有界面的过场：认不出 3 秒后点空白（协调 11-18 N.I.A 第二局停 4 次）
def _quiet(img, rt_t=(0.0, 4.0)):
    from hiro.runtime import nia_sample as ns
    from hiro.runtime.frame import Frame

    taps, t = [], [rt_t[0]]
    rt = NS(clock=lambda: t[0], device=NS(tap=lambda x, y: taps.append((x, y))))
    st = {"since": None, "page": None, "quiet": 0, "unknown_since": None}
    f = Frame(img, 0.0)
    first = ns._quiet_tap(rt, f, st)
    t[0] = rt_t[1]
    return first, ns._quiet_tap(rt, f, st), taps, st


@pytest.mark.parametrize("name", ["nia-quiet-whiteboard.jpg", "nia-quiet-idol.jpg"])
def test_quiet_scene_tapped_after_wait(name):
    first, second, taps, st = _quiet(_real(name))
    assert first is False and second is True and taps == [(12, 12)] and st["quiet"] == 1


def test_quiet_not_before_wait():
    _, second, taps, _ = _quiet(_real("nia-quiet-whiteboard.jpg"), (0.0, 1.0))
    assert second is False and taps == []


def test_quiet_not_on_button_words(monkeypatch):
    from hiro.vision.ocr import Box, Text

    monkeypatch.setattr("hiro.runtime.frame.screen_texts", lambda img: [Text("キャンセル", Box(150, 1140, 280, 1180))])
    _, second, taps, _ = _quiet(np.full((1280, 720, 3), 90, np.uint8))
    assert second is False and taps == []


def test_quiet_capped():
    from hiro.runtime import nia_sample as ns
    from hiro.runtime.frame import Frame

    img = _real("nia-quiet-idol.jpg")
    taps, t = [], [0.0]
    rt = NS(clock=lambda: t[0], device=NS(tap=lambda x, y: taps.append((x, y))))
    st = {"since": None, "page": None, "quiet": 0, "unknown_since": None}
    for _ in range(30):
        ns._quiet_tap(rt, Frame(img, 0.0), st)
        t[0] += 4.0
    assert len(taps) == ns.QUIET_TAP_MAX
