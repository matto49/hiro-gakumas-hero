"""ExamAdvisor / ExamStateSource 默认实现：不需要模拟器、KAA、识别层也能跑（合成局面）。"""

from hiro import plugins
from hiro.brain.cards import CardView, DrinkView  # noqa: F401
from hiro.brain.state import CAMPUS_HIRO, BattleState, ExamMemory
from hiro.contracts import Action, Preview
from hiro.defaults.advisor import RuleLLMAdvisor, preview_rows
from hiro.defaults.exam_state import ScreenExamStateSource, screen_state

import pytest


def _card(slot, name, cid, available=True):
    return CardView(slot, name, cid, 0, available, "active", 3, 0, None, "パラメータ+10")


def _state(hand, drinks=(), remaining=8):
    mem = ExamMemory(exam="r1")
    st = BattleState.build(
        mem,
        CAMPUS_HIRO,
        source="screen",
        remaining_turns=remaining,
        hp=20,
        genki=10,
        motivation=5,
        impression=0,
        score=1000,
        buffs={},
        attr="Da",
        multiplier_permil=None,
        npc_scores=None,
        my_rank_index=None,
        deck_count=None,
        pitem_charges=None,
        statuses=None,
        attr_permil=None,
        hand=list(hand),
        drinks=list(drinks),
    )
    return st, mem


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.delenv("HIRO_LLM", raising=False)
    plugins._cache.clear()


def test_plugins_resolve_defaults():
    from hiro.bench_bridge.advisor import BenchAdvisor

    adv = plugins.get("exam_advisor")
    src = plugins.get("exam_state_source")
    assert isinstance(adv, BenchAdvisor) and isinstance(
        src, ScreenExamStateSource
    )  # 缺省出招：bench 算法（测试里关着，走规则）
    assert isinstance(adv, plugins.ExamAdvisor) and isinstance(src, plugins.ExamStateSource)


def test_advisor_without_llm_picks_legal_action():
    st, mem = _state([_card(0, "テスト", "c-1"), _card(1, "テスト2", "c-2")])
    legal = [Action.from_legal(a) for a in st.legal_actions()]
    d = RuleLLMAdvisor(CAMPUS_HIRO).decide(st, mem, None, legal)
    assert d.action.legal_key() in {a.legal_key() for a in legal}


def test_advisor_with_previews_follows_best_expected(monkeypatch):
    monkeypatch.setenv("HIRO_EXAM_POLICY", "expert")  # expert：按预演期望选；默认 llm 时预演只进提示词
    st, mem = _state([_card(0, "A", "c-a"), _card(1, "B", "c-b")])
    legal = [Action.from_legal(a) for a in st.legal_actions()]
    previews = [
        Preview(action=Action.from_legal("play:0"), expected=1000.0, vs_best_se=0.0),
        Preview(action=Action.from_legal("play:1"), expected=5000.0, vs_best_se=0.0),
        Preview(action=Action.from_legal("skip"), expected=10.0, vs_best_se=0.0),
    ]
    rows = preview_rows(previews, legal)
    assert rows[0]["legal_action"] == "play:1"
    d = RuleLLMAdvisor(CAMPUS_HIRO).decide(st, mem, previews, legal)
    assert d.action.legal_key() == "play:1"


class _Frame:
    def __init__(self, raw):
        self.raw, self.img, self.cards, self.drinks = raw, None, [], []
        self.remaining, self.hp, self.genki, self.hand = 7, 18, 5, None


def test_state_source_reads_screen_fields(monkeypatch):
    import hiro.defaults.exam_state as es

    monkeypatch.setattr(
        es, "screen_fields", lambda raw, session=None, img=None: {"remaining_turns": 6, "hp": 15, "score": 2000}
    )
    src = ScreenExamStateSource()
    src.begin({"profile": CAMPUS_HIRO})
    st, why = src.read(_Frame({"hand_layout": "ok"}), ExamMemory(exam="r1"))
    assert why == "screen" and st.remaining_turns == 6 and st.hp == 15 and src.confidence() == "screen"
    assert src.previews(st) is None


def test_state_source_overlay_frame_is_skipped():
    src = ScreenExamStateSource()
    src.begin({"profile": CAMPUS_HIRO})
    st, why = src.read(_Frame({"hand_layout": "overlay"}), ExamMemory(exam="r1"))
    assert st is None and why == "hand_overlay"


def test_screen_state_fills_from_frame_when_unreadable():
    st = screen_state(ExamMemory(exam="r1"), CAMPUS_HIRO, {}, _Frame({}))
    assert st.remaining_turns == 7 and st.hp == 18 and st.genki == 5


def test_prompt_explains_missing_status_icons(monkeypatch):
    st, _ = _state([_card(0, "A", "c-a")])
    st.motivation = st.impression = None
    g = type(st).to_prompt_dict.__globals__  # 别的测试可能重新导入过模块：改实例所属模块的全局名
    monkeypatch.setitem(g, "status_icons_missing", lambda: True)
    d = st.to_prompt_dict()
    assert "effect_names_unavailable" in d and "capture_icons" in d["effect_names_unavailable"]
    monkeypatch.setitem(g, "status_icons_missing", lambda: False)
    assert "effect_names_unavailable" not in st.to_prompt_dict()


def test_out_of_legal_rechooses_by_rule_not_first(monkeypatch):
    st, mem = _state([_card(0, "A", "c-a"), _card(1, "B", "c-b")])
    adv = RuleLLMAdvisor(CAMPUS_HIRO)
    from hiro.brain.rules import Decision as BD

    monkeypatch.setattr(adv.brain, "decide", lambda s, m: BD("play:5", "越界", "llm"))
    import hiro.defaults.advisor as a

    monkeypatch.setattr(a, "guard_llm", lambda s, m, b, d: d)
    legal = [Action.from_legal(x) for x in ("play:1", "skip")]  # 只有第 1 张和结束回合合法
    d = adv.decide(st, mem, None, legal)
    assert d.action.legal_key() in {"play:1", "skip"} and any("不在合法动作里" in w for w in d.warnings)
