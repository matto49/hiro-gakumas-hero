"""事件选项页（EVENT_CHOICE，判页 INITIAL_CHOICE）的纯台词按钮（初开场二选一，协调 11-17）：
原来 observation 没登记 EVENT_CHOICE，读出的选项被整个丢掉 → 决策器「少于 2 个」停；二选一时还多一个空的第 0 行。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision
from hiro.produce import hif

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures")


def _img(rel):
    p = FIX / rel
    if not p.exists():
        pytest.skip(f"需要实机截图 {rel}")
    from hiro.vision.offline import load

    return load(cv2.imread(str(p)))


def _fields(rel):
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("EVENT_CHOICE", _img(rel))
    return observation(1, 0.0, "EVENT_CHOICE", raw).fields


def test_dialogue_choice_read_and_kept_by_observation():
    f = _fields("pages/hajime-dialogue-choice.png")
    assert [(c["index"], c["text"]) for c in f["choices"]] == [
        (0, "お祝いをしないといけませんね"),
        (1, "次の試練が待っていますよ"),
    ]
    assert [c["center"][1] for c in f["choices"]] == pytest.approx([802, 903], abs=10)
    assert f["dialogue_only"] is True and f["complete"] and f["selected"] is None


@pytest.mark.parametrize("rel", ["event/study-00100.jpg", "event/outing-00125.jpg", "event/outing-00261.jpg"])
def test_effect_choices_not_dialogue(rel):
    """授業 / 外出的选项（彩色字 / 图标 / 代价角标）不算纯台词；选中态按钮认不出时有字的行照样留（outing-00261 中间那项）。"""
    f = _fields(rel)
    assert f["dialogue_only"] is False and len(f["choices"]) == 3 and all(c["text"] for c in f["choices"])


def _core(monkeypatch, decision, fields):
    core = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    state = NS(page_fields=fields)
    recorded = []
    core.s = NS(
        observe=lambda **k: "obs",
        state=state,
        record=lambda obs, **k: recorded.append(k["decision"]),
        note=lambda m: None,
    )
    core.deciders = NS(decide=lambda page, st, **k: decision)
    core._note_fallback = lambda page, d: None
    core._act = lambda fn, action, **k: fn()
    stops = []

    def stop(page, why, **k):
        stops.append(why)
        raise RuntimeError(why)

    core._stop = stop
    return core, recorded, stops


def _wait():
    return Decision(
        Action.ui(ActionKind.WAIT, why="x"), "有选项效果没解析出来", "fallback", 0.1, decider="event_choice"
    )


def test_dialogue_only_wait_picks_last(monkeypatch):
    chosen = []
    core, recorded, stops = _core(
        monkeypatch, _wait(), {"choices": [{"index": 0, "text": "a"}, {"index": 1, "text": "b"}], "dialogue_only": True}
    )
    core._event_page("EVENT_CHOICE", chosen.append, 2)
    assert chosen == [1] and not stops
    assert recorded[0].action.args["select"] == 1 and "纯台词" in recorded[0].reason


def test_not_dialogue_wait_still_stops(monkeypatch):
    chosen = []
    core, _, stops = _core(
        monkeypatch,
        _wait(),
        {"choices": [{"index": 0, "text": "a"}, {"index": 1, "text": "b"}], "dialogue_only": False},
    )
    with pytest.raises(RuntimeError):
        core._event_page("EVENT_CHOICE", chosen.append, 2)  # 决策是 Wait、又不是纯台词：照旧停机
    assert stops and not chosen


def test_decider_choice_respected_on_dialogue(monkeypatch):
    """决策器自己给了选择（3940a9 的全部无效果规则）：照决策器，不改成最后一项。"""
    chosen = []
    d = Decision(Action.event_select(0), "纯台词，选第 1 项", "rule", 0.8, decider="event_choice")
    core, recorded, _ = _core(
        monkeypatch, d, {"choices": [{"index": 0, "text": "a"}, {"index": 1, "text": "b"}], "dialogue_only": True}
    )
    core._event_page("EVENT_CHOICE", chosen.append, 2)
    assert chosen == [0] and recorded[0] is d
