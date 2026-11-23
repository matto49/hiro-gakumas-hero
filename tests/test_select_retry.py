"""第十八局选拔第 1 天差し入れ：SELECT_DRINK 读到的是弹窗淡入帧，候选为空 → 决策 Wait → 停机（同一画面稍后三瓶都认得出）。
候选为空时等 0.5s 重读，最多 SELECT_RETRIES 次，仍空才走原来的停机。"""

from types import SimpleNamespace as NS

import hiro.produce.hif as hif
from hiro.contracts import Action, ActionKind, Decision, ExecutionResult, GameState
from hiro.produce import ops
from hiro.produce.hif import HiroHifStrategy, UnhandledPage

import pytest

CANDS = [
    {"index": i, "drink_id": f"d{i}", "name": n}
    for i, n in enumerate(("ブーストエキス", "ミックススムージー", "センブリソーダ"))
]


class Session:
    def __init__(self, frames):
        self.frames, self.state, self.records, self.recovers, self.n = list(frames), GameState(), [], [], 0

    def observe(self, scene=None, **_):
        fields = self.frames[min(self.n, len(self.frames) - 1)]
        self.n += 1
        self.state = GameState(page_fields=dict(fields))
        return NS(fields=fields)

    def record(self, obs, **kw):
        self.records.append((obs, kw))

    def recover(self, kind, detail):
        self.recovers.append((kind, detail))

    def note(self, text):
        pass


def _run(monkeypatch, frames):
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = Session(frames)
    seen = []

    def decide(page, state, **kw):
        cands = state.page_fields.get("candidates")
        seen.append(len(cands or []))
        if not cands:
            return Decision(Action.ui(ActionKind.WAIT, why="饮料候选未识别"), "", "fallback", 0.1)
        return Decision(Action.activate_effect(0), "拿第 1 瓶", "rule", 0.8)

    st.deciders = NS(decide=decide)
    st._drink_full_skip = lambda: False
    st._note_fallback = lambda *a: None
    st._confirm_acquire = lambda: None
    committed = []
    st._act = lambda commit, act, **kw: (commit(), ExecutionResult(action=act, ok=True))[1]
    from hiro.produce import card_ops

    monkeypatch.setattr(card_ops, "drink_select_can_skip", lambda img: False)
    monkeypatch.setattr(card_ops, "drink_select_commit", lambda drink: committed.append("abc"[drink]))
    monkeypatch.setattr(ops, "fast_acquire", lambda c: False)  # 走慢提交，记下选了哪瓶
    return st, None, seen, committed


def test_empty_first_frame_then_candidates(monkeypatch):
    st, ctx, seen, committed = _run(monkeypatch, [{"candidates": []}, {"candidates": CANDS}])
    st.on_select_drink(ctx)
    assert seen == [3] and committed == ["a"] and st.s.n == 2


def test_still_empty_after_retries_stops(monkeypatch):
    st, ctx, seen, committed = _run(monkeypatch, [{"candidates": []}])
    with pytest.raises(UnhandledPage):
        st.on_select_drink(ctx)
    assert seen == [0] and st.s.n >= 1 + HiroHifStrategy.SELECT_RETRIES and committed == []
    assert any(k == "missing_fields" for k, _ in st.s.recovers)


class DebounceSession(Session):
    """obs 带候选，但前 `lag` 次观测页面还在防抖，state 里没有字段（第十八局本战 22:02 GIFT → SELECT_DRINK）。"""

    def __init__(self, frames, lag):
        super().__init__(frames)
        self.lag = lag

    def observe(self, scene=None, **_):
        obs = super().observe(scene)
        if self.n <= self.lag:
            self.state = GameState()
        return obs


def test_obs_has_candidates_but_state_debouncing_retries(monkeypatch):
    st, ctx, seen, committed = _run(monkeypatch, [{"candidates": CANDS}])
    st.s = DebounceSession([{"candidates": CANDS}], lag=2)
    st.on_select_drink(ctx)
    assert seen == [3] and committed == ["a"] and st.s.n == 3


# ---------------------------------------------------------------- 饮料溢出页：决策异常不交给 KAA 默认（第十八局 20:23:29）
def _pdrink(monkeypatch, decisions):
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = Session([{"held": [], "incoming": None}])
    it = iter(decisions)
    st.deciders = NS(decide=lambda page, state, **kw: next(it))
    kaa = []
    monkeypatch.setattr(
        "hiro.produce.drink_swap.skip_incoming", lambda **k: kaa.append("leave") or True
    )  # 不要新饮料：点離れる
    return st, kaa


WAIT = Decision(
    Action.ui(ActionKind.WAIT, why="决策器异常 TypeError"), "PDRINK_MAX 没有可用的兜底模板", "fallback", 0.1
)
KEEP = Decision(
    Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False}), "新饮料不比持有的强：不收", "rule", 0.8
)


def test_pdrink_decider_error_retried_then_explicit_keep(monkeypatch):
    st, kaa = _pdrink(monkeypatch, [WAIT, KEEP])
    st.on_pdrink_max(None)
    assert kaa == ["leave"] and st.s.n == 2  # 重新观测一次，决策明确「不收」才交 KAA 点跳过


def test_pdrink_decider_error_persistent_stops(monkeypatch):
    st, kaa = _pdrink(monkeypatch, [WAIT] * 5)
    with pytest.raises(UnhandledPage):
        st.on_pdrink_max(None)
    assert kaa == []  # 不落到 KAA 默认（它会点 Leave 跳过新饮料）
    assert any(k == "unhandled" for k, _ in st.s.recovers)


def test_gift_to_select_drink_is_legal_transition():
    """第十八局本战：GIFT → SELECT_DRINK 不在转移图里，要防抖 3 帧，第 2 帧的候选没进 state → 决策看到空候选停机。"""
    from hiro.loop.transitions import allowed

    assert allowed("GIFT", "SELECT_DRINK") and allowed("GIFT", "SELECT_CARD") and allowed("ACTION_SELECT", "GIFT")


def test_reduce_applies_candidates_right_after_gift():
    """实机序列：GIFT 稳定 → 若干 TRANSITION → SELECT_DRINK（第 1 帧就带三瓶候选）。"""
    from hiro.contracts import GameState, Observation, PageObs
    from hiro.loop.reduce import reduce

    def ob(seq, page, fields=None):
        return Observation(seq, float(seq), PageObs(page, "produce"), fields=fields or {}, source="screen")

    s = GameState()
    s = reduce(s, ob(1, "ACTION_SELECT"))
    s = reduce(s, ob(2, "ACTION_SELECT"))
    s = reduce(s, ob(3, "GIFT"))
    s = reduce(s, ob(4, "GIFT"))
    s = reduce(s, ob(5, "TRANSITION"))
    cands = [{"slot": 0, "drink_id": "pdrink_02-3-010"}]
    s = reduce(s, ob(6, "SELECT_DRINK", {"candidates": cands}))
    assert s.page.name == "SELECT_DRINK" and s.page_fields.get("candidates")
