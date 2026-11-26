"""GKMS 插件各页面（spec 4.5）：用假 client，不需要本机装 GKMS。输出都要能过插件闸门的白名单和越界检查。"""

import copy
import json
from pathlib import Path

import pytest

from hiro.brain.deciders import _invalid_decision
from hiro.brain.plugin_gate import ALLOWED_KINDS
from hiro.contracts import ActionKind, GameState
from hiro.gkms.advisor import GkmsProduceAdvisor
from hiro.gkms.client import GkmsError

RAW = json.loads((Path(__file__).parent / "data" / "gkms-hajime-pro-w11.json").read_text())
HELLO = {"format": "gkms.pickv.v1", "algo": 2, "tau": 0.05, "reroll_gain": 0.1}


class FakeClient:
    def __init__(self, **replies):
        self.replies = {"hello": HELLO, **replies}
        self.calls = []

    def call(self, op, *, timeout=None, **args):
        self.calls.append((op, args))
        r = self.replies.get(op)
        if isinstance(r, Exception):
            raise r
        return r(args) if callable(r) else copy.deepcopy(r)

    def close(self):
        pass


def make(page, mode="produce-002", page_fields=None, deck=None, **produce):
    d = copy.deepcopy(RAW)
    d["page"]["name"] = page
    d["produce"].update(mode=mode, idol_card_id="i_card-shro-3-007", deck=deck or [], **produce)
    if page_fields is not None:
        d["page_fields"] = page_fields
    return GameState.from_dict(d)


def ok(page, d, n=None, kw=None):
    assert d is not None
    assert d.action.kind in ALLOWED_KINDS[page]
    assert _invalid_decision(page, d, n, kw or {}) is None
    return d


def run(adv, page, state, n=None, kw=None):
    adv.begin({"mode": (state.produce or {}).get("mode"), "idol": "i_card-shro-3-007", "trace_dir": None})
    return adv.decide(page, state, n_options=n, kw=kw or {})


ROUTE = {"plan": {"route": [[11, "event_school"], [12, "lesson_da"]], "rating": 8271.1, "end": {"vocal": 570, "dance": 1253, "visual": 1034}},
         "decisions": [{"step": 11, "plan": "event_school", "options": [{"choice": "refresh", "vs_best": -289.8}, {"choice": "event_school", "vs_best": 0.0}]}],
         "from_step": 11}  # fmt: skip


def test_action_select_follows_route():
    adv = GkmsProduceAdvisor(client=FakeClient(route=ROUTE))
    d = ok("ACTION_SELECT", run(adv, "ACTION_SELECT", make("ACTION_SELECT")))
    assert d.action.args["option"] == "class"
    assert "GKMS 路线" in d.reason and "-289.8" in d.reason


def test_action_select_hif_defers():
    adv = GkmsProduceAdvisor(client=FakeClient(route=ROUTE))
    assert run(adv, "ACTION_SELECT", make("ACTION_SELECT", mode="produce-008")) is None
    assert not [c for c in adv.client.calls if c[0] == "route"]


def test_action_select_route_error_defers():
    adv = GkmsProduceAdvisor(client=FakeClient(route=GkmsError("boom")))
    assert run(adv, "ACTION_SELECT", make("ACTION_SELECT")) is None


def test_action_select_plan_not_on_screen_defers():
    route = copy.deepcopy(ROUTE)
    route["plan"]["route"][0][1] = "lesson_vo"
    adv = GkmsProduceAdvisor(client=FakeClient(route=route))
    assert run(adv, "ACTION_SELECT", make("ACTION_SELECT")) is None


CANDS = [{"index": 0, "card_id": "p_card-a"}, {"index": 1, "card_id": "p_card-b"}, {"index": 2, "card_id": "p_card-c"}]


def values(take, reroll_gain=None, held=None, up=None, state="ok"):
    return {"state": state, "take": take, "held": held or {}, "up": up or {}, "reroll_gain": reroll_gain}


def test_select_card_takes_highest_v():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({"p_card-a": 0.2, "p_card-b": 0.7, "p_card-c": None})))
    d = ok("SELECT_CARD", run(adv, "SELECT_CARD", make("SELECT_CARD", page_fields={"candidates": CANDS, "rerolls_left": 0}), n=3), n=3)
    assert d.action.kind is ActionKind.ACTIVATE_EFFECT and d.action.args["pick_indexes"] == [1]
    assert "H.I.F 的包" in d.reason  # 初用 H.I.F 的选卡包近似


def test_select_card_rerolls_when_worth():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({"p_card-a": 0.1, "p_card-b": 0.1, "p_card-c": 0.1}, reroll_gain=0.3)))
    d = ok("SELECT_CARD", run(adv, "SELECT_CARD", make("SELECT_CARD", page_fields={"candidates": CANDS, "rerolls_left": 1}), n=3), n=3)
    assert d.action.kind is ActionKind.REROLL


def test_select_card_no_reroll_left_takes_best():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({"p_card-a": 0.1, "p_card-b": 0.3, "p_card-c": 0.1}, reroll_gain=0.3)))
    d = run(adv, "SELECT_CARD", make("SELECT_CARD", page_fields={"candidates": CANDS, "rerolls_left": 0}), n=3)
    assert d.action.kind is ActionKind.ACTIVATE_EFFECT


def test_select_card_no_pack_defers():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({}, state="none")))
    assert run(adv, "SELECT_CARD", make("SELECT_CARD", page_fields={"candidates": CANDS}), n=3) is None


def test_change_pick_and_remove():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({"p_card-a": 0.2, "p_card-b": 0.7, "p_card-c": 0.1}),
                                               removal={"state": "ok", "order": [{"card_id": "p_card-x", "upgrade": 0}, {"card_id": "p_card-y", "upgrade": 1}]}))  # fmt: skip
    d1 = ok("SKILL_CARD_CHANGE_1", run(adv, "SKILL_CARD_CHANGE_1", make("SKILL_CARD_CHANGE_1", page_fields={"candidates": CANDS})))
    assert d1.action.args == {"stage": 1, "pick_index": 1}
    cards = [{"index": 0, "card_id": "p_card-y", "upgrade": 1}, {"index": 1, "card_id": "p_card-x", "upgrade": 0, "customized": True},
             {"index": 2, "card_id": None}]  # fmt: skip
    d2 = ok("SKILL_CARD_CHANGE_2", adv.decide("SKILL_CARD_CHANGE_2", make("SKILL_CARD_CHANGE_2", page_fields={"cards": cards}), n_options=None, kw={}))
    assert d2.action.args["remove_card_id"] == "p_card-y" and d2.action.args["remove_index"] == 0  # 定制过的 x 跳过


def test_removal_cancels_when_nothing_removable():
    adv = GkmsProduceAdvisor(client=FakeClient(removal={"state": "ok", "order": [{"card_id": "p_card-x", "upgrade": 0}]}))
    cards = [{"index": 0, "card_id": "p_card-x", "upgrade": 0, "customized": True}]
    d = ok("SKILL_CARD_REMOVAL", run(adv, "SKILL_CARD_REMOVAL", make("SKILL_CARD_REMOVAL", page_fields={"cards": cards})))
    assert d.action.args == {"cancel": True}


def test_enhance_picks_highest_upgrade_value():
    adv = GkmsProduceAdvisor(client=FakeClient(values=values({}, up={"p_card-a": 0.1, "p_card-b": 0.4})))
    cards = [{"index": 0, "card_id": "p_card-a", "upgrade": 0}, {"index": 1, "card_id": "p_card-b", "upgrade": 0},
             {"index": 2, "card_id": "p_card-c", "upgrade": 1}]  # fmt: skip
    d = ok("SKILL_CARD_ENHANCE", run(adv, "SKILL_CARD_ENHANCE", make("SKILL_CARD_ENHANCE", page_fields={"cards": cards})))
    assert d.action.args == {"card_id": "p_card-b", "index": 1, "upgrade": 0}
    assert adv.client.calls[-1][1]["upgrade"] == ["p_card-a", "p_card-b"]  # 强化过的不问


def test_not_installed_raises_on_create(monkeypatch):
    from hiro.gkms import locate

    monkeypatch.setattr(locate, "find", lambda: None)
    with pytest.raises(RuntimeError):
        GkmsProduceAdvisor()
