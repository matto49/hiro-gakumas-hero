"""D41 第三十三局 18:18 seq 111：饮料溢出页「手持ちのドリンク」第 3 行被视口切掉（未知饮料），决策丢它，执行只看到 2 行报失败，
随后落到 KAA 默认「残す」= 丢掉新来的 特製ハツボシエキス。现在：视口外的已有饮料标成点不到，只在看得到的里重新决策一次；
还失败就停下，不交给 KAA。"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, Decision, GameState

import pytest

FIX = Path(__file__).parent / "data" / "g33-seq111-pdrink-max.json"  # 实机状态（page_fields 文本），随仓库发布


def _state():
    return GameState.from_dict(json.loads(FIX.read_text(encoding="utf-8"))["state"])


def _strategy(monkeypatch, state, decide, results):
    import hiro.produce.hif as hif
    from hiro.produce import drink_swap

    calls = {"resolve": [], "kaa": 0, "stops": []}

    def resolve(discard, **k):
        calls["resolve"].append(discard)
        drink_swap.LAST_ROWS.update(new=1, owned=2)
        ok = results.pop(0)
        return NS(ok=ok, error=None if ok else "溢出页上只看到 2 瓶已有饮料，没有第 [2] 瓶")

    monkeypatch.setattr(drink_swap, "resolve_overflow", resolve)
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(
        "hiro.produce.drink_swap.skip_incoming", lambda **k: calls.__setitem__("kaa", calls["kaa"] + 1) or True
    )  # 不要新饮料：点離れる
    sess = NS(
        state=state,
        observe=lambda **k: NS(fields={}),
        record=lambda *a, **k: None,
        recover=lambda *a, **k: None,
        note=lambda *a: None,
    )
    strat = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    strat.s, strat.deciders = sess, NS(decide=lambda page, st: decide(sess.state))

    def stop(page, why, **k):
        calls["stops"].append(why)
        raise RuntimeError(why)

    strat._stop = stop
    return strat, calls


@pytest.mark.master
def test_g33_offscreen_row_redecides_among_visible(monkeypatch):
    """钩子行为：第一次决策要丢视口外的第 3 行（当时的决策；决策侧 D41 之后已不会这样选）→ 执行失败 → 钩子把视口外的行标成点不到 →
    真实决策在看得到的里重选、执行成功，不落到 KAA 默认。"""
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    old = Decision(
        Action(ActionKind.DISCARD_DRINK, {"discard": [2], "receive": True}),
        "丢未知（第三十三局当时的决策）",
        "rule",
        0.8,
    )

    def decide(st):
        held = st.page_fields.get("held") or []
        marked = any(h.get("visible") is False for h in held)
        return dec.decide("PDRINK_MAX", st) if marked else old

    strat, calls = _strategy(monkeypatch, _state(), decide, [False, True])
    strat.on_pdrink_max(None)
    assert calls["resolve"][0] == [2] and len(calls["resolve"]) == 2 and 2 not in calls["resolve"][1]
    assert calls["kaa"] == 0 and not calls["stops"]  # 没有落到 KAA 的「残す」
    held = strat.s.state.page_fields["held"]
    assert held[2]["visible"] is False and held[0].get("visible") is not False


def test_swap_failing_twice_stops_instead_of_kaa_default(monkeypatch):
    d = Decision(Action(ActionKind.DISCARD_DRINK, {"discard": [0], "receive": True}), "换", "rule", 0.9)
    strat, calls = _strategy(monkeypatch, _state(), lambda st: d, [False, False])
    with pytest.raises(RuntimeError):
        strat.on_pdrink_max(None)
    assert calls["kaa"] == 0 and len(calls["resolve"]) == 2 and "不落到 KAA 默认" in calls["stops"][0]


def test_keep_decision_still_uses_kaa_skip(monkeypatch):
    d = Decision(Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False}), "不收", "rule", 0.9)
    strat, calls = _strategy(monkeypatch, _state(), lambda st: d, [])
    strat.on_pdrink_max(None)
    assert calls["kaa"] == 1 and calls["resolve"] == [] and strat._skip_incoming_drink


def test_take_new_without_discarding_goes_to_resolve_not_skip(monkeypatch):
    """栏里有空格、2 瓶新来只留 1 瓶：决策 discard=[]、receive=True、receive_indexes=[0]。要走 resolve_overflow（勾新饮料、点「残す」），
    不能当成「不要新饮料」去找「離れる」（这一页没有，Mac 账号 2 11-14 停机）。"""
    d = Decision(
        Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": True, "receive_indexes": [0]}),
        "收スムージー",
        "rule",
        0.8,
    )
    strat, calls = _strategy(monkeypatch, _state(), lambda st: d, [True])
    strat.on_pdrink_max()
    assert calls["resolve"] == [[]] and calls["kaa"] == 0 and calls["stops"] == []
