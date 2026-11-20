"""初 REGULAR 实机（协调 11-17）两处停机：① 「休み確認」单独留在画面上（DIALOG 没处理器，181 秒停）；② 外出页 P 不够的档是灰的，
按钮识别只数到 1 个判成 IDLE（180 秒停），而且按钮序号和选项序号对不上。实机帧在私有 fixtures pages/hajime-*。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

from hiro.contracts import Action, Decision

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _real(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


def _rest_rt(last):
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    rt, _ = runtime([fake_frame()])
    rt.session = NS(last_decision=last)
    return rt


def _check(rt):
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    return interrupts.check(Frame(_real("hajime-rest-confirm.jpg"), 0.0), rt)


def test_rest_confirm_after_rest_decision_taps_rest():
    rt = _rest_rt(("ACTION_SELECT", Decision(Action.produce_next("rest"), "休息", "rule", 0.8)))
    assert "休む" in _check(rt)
    (x, y), = rt.raw_device.taps
    assert abs(x - 497) < 30 and abs(y - 1159) < 20


@pytest.mark.parametrize("last", [None, ("ACTION_SELECT", Decision(Action.produce_next("training", attr="Da"), "课程", "rule", 0.8))])
def test_rest_confirm_without_rest_decision_cancels(last):
    """续跑后不知道刚才选了什么、或者选的不是休息：点キャンセル回选行动页重新决策。"""
    rt = _rest_rt(last)
    assert "キャンセル" in _check(rt)
    (x, y), = rt.raw_device.taps
    assert x < 300 and abs(y - 1160) < 20


def test_session_remembers_last_decision(tmp_path):
    from hiro.contracts import TraceWriter
    from hiro.loop.session import Session

    import numpy as np

    s = Session(TraceWriter(root=tmp_path, name="t"), shoot=lambda: np.zeros((1280, 720, 3), np.uint8))
    obs = s.observe(scene="ACTION_SELECT", img=np.zeros((1280, 720, 3), np.uint8), fields=False)
    d = Decision(Action.produce_next("rest"), "休息", "rule", 0.8)
    s.record(obs, decision=d)
    assert s.last_decision == ("ACTION_SELECT", d)


def _outing_fields():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load

    raw, _ = extract("OUTING", load(_real("hajime-outing-grey.png")))
    return observation(1, 0.0, "OUTING", raw).fields


def test_outing_with_grey_choices_classified_outing():
    from hiro.vision.classify import classify
    from hiro.vision.offline import load

    assert classify(load(_real("hajime-outing-grey.png"))).name == "OUTING"


def test_outing_choices_cost_and_disabled():
    f = _outing_fields()
    assert [(c["text"], c["cost_p"], c["disabled"]) for c in f["choices"]] == [
        ("1パーセント", 100, True), ("50パーセント", 50, True), ("100パーセント", None, False)]  # fmt: skip


def test_outing_decider_picks_affordable():
    """3940a9 的外出决策（P 30）：只有 100パーセント 买得起。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    prod = {"mode": "produce-001", "stamina": 20, "max_stamina": 30, "produce_point": 30, "params": {"Vo": 195, "Da": 250, "Vi": 159}}
    d = ProduceDeciders(profile_for("i_card-shro-3-007"), None).outing(GameState(produce=prod, page_fields={"choices": _outing_fields()["choices"]}))
    assert d.action.args["select"] == 2


def test_event_commit_uses_choice_centers_when_buttons_missing(monkeypatch):
    """按钮识别只认出 1 个（灰的两档不算）、识别层 3 项：选第 3 项要点第 3 项的位置，不能点「按钮 0」。"""
    from hiro.produce import hif

    clicks = []
    monkeypatch.setattr(hif, "device", NS(click=lambda x, y: clicks.append((x, y)), screenshot=lambda: None))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    core = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    choices = [{"index": i, "text": t, "center": [360, y], "disabled": i < 2}
               for i, (t, y) in enumerate((("1パーセント", 698), ("50パーセント", 799), ("100パーセント", 901)))]  # fmt: skip
    core.s = NS(observe=lambda **k: "obs", state=NS(page_fields={"choices": choices}), record=lambda obs, **k: None, note=lambda m: None)
    core.deciders = NS(decide=lambda page, st, **k: Decision(Action.event_select(2), "买得起的", "rule", 0.8))
    core._note_fallback = lambda page, d: None
    core._act = lambda fn, action, **k: fn()
    core._outing_ap_check = lambda: None
    wrong = []
    core._event_page("OUTING", lambda i: wrong.append(i), 1)
    assert not wrong and clicks == [(360, 901), (360, 901)]
    # 有灰档时选第 1 项（灰的，决策不该选，但万一）也按位置点，不点「按钮 0」（那是第 3 项）
    core.deciders = NS(decide=lambda page, st, **k: Decision(Action.event_select(0), "x", "rule", 0.8))
    clicks.clear()
    core._event_page("OUTING", lambda i: wrong.append(i), 1)
    assert not wrong and clicks == [(360, 698), (360, 698)]
