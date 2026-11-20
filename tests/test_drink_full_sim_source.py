"""第二十一局 06:01 差し入れ：饮料满，决策按模拟偏离社区默认（> 2 SE，source=sim）要丢 パワフル漢方、收 厳選初星ブレンド；
_drink_full_skip 只认 source=rule，把有效决策当成「认不出」停机。规则 / 模拟决策都应执行先丢再领。"""

from types import SimpleNamespace as NS

import pytest

import hiro.produce.drink_swap as ds
import hiro.produce.hif as hif
from hiro.contracts import Action, ActionKind, Decision, GameState
from hiro.produce.hif import HiroHifStrategy, UnhandledPage
from hiro.vision.ocr import Box, Text

CANDS = [{"slot": 0, "drink_id": "pdrink_kampo", "name": "パワフル漢方ドリンク"}, {"slot": 1, "drink_id": "pdrink_blend", "name": "厳選初星ブレンド"},
         {"slot": 2, "drink_id": "pdrink_aojiru", "name": "初星スペシャル青汁"}]  # fmt: skip
HELD = [{"index": i, "drink_id": d, "name": n} for i, (d, n) in enumerate([("pdrink_kampo", "パワフル漢方ドリンク"), ("pdrink_blend", "厳選初星ブレンド"),
                                                                            ("pdrink_senburi", "センブリソーダ"), ("pdrink_aojiru", "初星スペシャル青汁")])]  # fmt: skip


def _st(monkeypatch, source):
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [Text("Pドリンク所持上限", Box(350, 1170, 370, 1190))])
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda *a: None))
    monkeypatch.setattr(hif.dk if hasattr(hif, "dk") else __import__("hiro.brain.drinks", fromlist=["x"]), "best_incoming", lambda cands, held, stage, cap: CANDS[1])
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = NS(state=GameState(page_fields={"candidates": CANDS, "held": HELD}), observe=lambda **k: NS(fields={}), record=lambda *a, **k: None,
              recover=lambda *a: None)  # fmt: skip
    d = Decision(Action(ActionKind.ACTIVATE_EFFECT, {"receive": [1], "discard": [0]}), "丢 パワフル漢方、收 厳選初星ブレンド +8,020", source, 0.8)
    st.deciders = NS(decide=lambda page, state, **kw: d)
    swapped = []
    monkeypatch.setattr(ds, "discard_then_receive", lambda discard, slot, held=None: (swapped.append((discard, slot)), NS(ok=True, error=None))[1])
    return st, swapped


@pytest.mark.parametrize("source", ["rule", "sim"])
def test_rule_and_sim_decisions_swap(monkeypatch, source):
    st, swapped = _st(monkeypatch, source)
    assert st._drink_full_skip() is True
    assert swapped == [(0, 1)]


def test_fallback_still_stops(monkeypatch):
    monkeypatch.delenv("HIRO_DRINK_FULL_AUTO", raising=False)
    st, swapped = _st(monkeypatch, "fallback")
    st._stop = lambda page, why, **kw: (_ for _ in ()).throw(UnhandledPage(why))
    with pytest.raises(UnhandledPage):
        st._drink_full_skip()
    assert swapped == []


# ---------------------------------------------------------------- 开关 HIRO_DRINK_FULL_AUTO（协调 11-17，初 REGULAR 实机：LLM 结论同规则仍停机）
def _stopping(st):
    st._stop = lambda page, why, **kw: (_ for _ in ()).throw(UnhandledPage(why))


def test_llm_stops_when_switch_off(monkeypatch):
    monkeypatch.delenv("HIRO_DRINK_FULL_AUTO", raising=False)
    st, swapped = _st(monkeypatch, "llm")
    _stopping(st)
    with pytest.raises(UnhandledPage):
        st._drink_full_skip()
    assert swapped == []


def test_llm_swaps_when_switch_on(monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "1")
    st, swapped = _st(monkeypatch, "llm")
    _stopping(st)
    assert st._drink_full_skip() is True and swapped == [(0, 1)]


def test_unrecognized_skips_when_switch_on(monkeypatch):
    """开关开：决策认不出（fallback）就勾「受け取らない」→ 点按钮，不停机；随后的「報酬スキップ」由 on_pdrink_max_confirm 放行。"""
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "1")
    st, swapped = _st(monkeypatch, "fallback")
    _stopping(st)
    clicks, recorded, notes = [], [], []
    texts = [Text("Pドリンク所持上限", Box(350, 1170, 370, 1190)), Text("受け取らない", Box(290, 990, 430, 1022)),
             Text("受け取らない", Box(300, 1080, 420, 1114))]  # fmt: skip
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: texts)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st.s.record = lambda obs, **k: recorded.append(k.get("decision"))
    st.s.note = notes.append
    assert st._drink_full_skip() is True
    assert swapped == [] and clicks == [(360, 1006), (360, 1097)] and st._skip_incoming_drink
    assert recorded[0].action.args == {"discard": [], "receive": False} and "默认跳过" in recorded[0].reason and notes
