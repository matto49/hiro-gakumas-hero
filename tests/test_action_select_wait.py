"""第十九局本战第 1 天：Da 2296 读成 3296 被不变量拦下 → 决策器缺属性抛异常 → Wait → choice None → 交给 KAA，
KAA 按 v0 规则自己进了 Vi 授業。Wait 时要重读再决策；仍是 Wait 就停下，不交给 KAA。"""

from types import SimpleNamespace as NS

import hiro.produce.hif as hif
from hiro.contracts import Action, ActionKind, Decision, GameState
from hiro.produce.hif import HiroHifStrategy, UnhandledPage

import pytest

WAIT = Decision(Action.ui(ActionKind.WAIT, why="决策器异常"), "ACTION_SELECT 没有可用的兜底模板", "fallback", 0.1)


def _st(monkeypatch, decisions):
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = NS(state=GameState(), img=None, observe_complete=lambda *a, **k: NS(fields={}), observe=lambda **k: NS(fields={}),
              record=lambda *a, **k: None, recover=lambda *a: None, note=lambda *a: None)  # fmt: skip
    it = iter(decisions)
    calls = []
    st.deciders = NS(decide=lambda page, state, **kw: (calls.append(page), next(it))[1])
    st._maybe_review_deck = lambda: None
    st.rec = NS(log=lambda *a, **k: None)
    committed = []
    from hiro.vision.action_buttons import ActionButton
    from hiro.vision.ocr import Box

    btn = ActionButton("class", "Da", None, Box(0, 0, 2, 4), "InProduce.ButtonIconStudyDanceHif")
    monkeypatch.setattr("hiro.vision.action_buttons.available_actions", lambda img: [btn])
    monkeypatch.setattr("hiro.produce.action_select.action_commit", lambda b: committed.append(b.label))
    st._act = lambda commit, act, **kw: commit()
    return st, None, calls, committed


def test_wait_then_real_decision_after_reread(monkeypatch):
    real = Decision(Action.produce_next("class", attr="Da"), "Da 授業", "rule", 0.9)
    st, ctx, calls, committed = _st(monkeypatch, [WAIT, real])
    st.on_action_select(ctx)
    assert len(calls) == 2 and committed == ["class/Da"]


def test_wait_persists_stops_not_kaa(monkeypatch):
    st, ctx, calls, committed = _st(monkeypatch, [WAIT] * 10)
    handed = []
    monkeypatch.setattr(hif.HifGrindStrategy, "on_action_select", lambda self, ctx: handed.append(1))
    with pytest.raises(UnhandledPage):
        st.on_action_select(ctx)
    assert handed == [] and committed == [] and len(calls) == 1 + HiroHifStrategy.ACTION_WAIT_RETRIES
