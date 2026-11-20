"""任务 ② 第 4 部分：SearchAdvisor——对齐过画面的引擎局面 → 和画面核对 → Rust GNRPA → 画面槽位；核不上 / 没局面退回 bench 中转层。"""

from types import SimpleNamespace

import pytest

from hiro.contracts import Action

pytestmark = pytest.mark.sim_master

DECK = [("p_card-02-men-1_032", 1), ("p_card-02-act-2_062", 1), ("p_card-02-men-3_040", 0), ("p_card-02-ido-3_105", 1), ("p_card-02-men-2_004", 0),
        ("p_card-02-act-1_037", 1), ("p_card-00-sup-3_152", 1), ("p_card-02-men-2_100", 1), ("p_card-02-act-3_186", 0), ("p_card-02-men-1_031", 0),
        ("p_card-02-men-2_052", 1), ("p_card-02-sup-3_094", 1)] * 2  # fmt: skip


@pytest.fixture()
def adv(monkeypatch):
    rs = pytest.importorskip("gakumas_exam_rs")
    if not hasattr(rs, "search"):
        pytest.skip("Rust 后端没有 search")
    monkeypatch.setenv("HIRO_BENCH_ADVISOR", "0")  # 退回时用规则，不加载 UINet
    from hiro.bench_bridge.advisor import BenchAdvisor
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import Decision
    from hiro.search import advisor as sa
    from hiro.search.advisor import SearchAdvisor

    monkeypatch.setattr(sa, "BUDGET", {"seconds": 0.3, "worlds": 2, "threads": 0})

    # 退回的那一层（UINet → 规则）要完整的考试局面；这里只验搜索插件，退回换成桩
    monkeypatch.setattr(BenchAdvisor, "decide", lambda self, *a, **k: Decision(Action.skip(), "退回", "rule", None))

    a = SearchAdvisor(CAMPUS_HIRO, None)
    a.on_round_start(1, {"exam": "r2", "idol": "i_card-shro-3-007", "deck": [list(c) for c in DECK]})
    return a


def _game():
    from gakumas_exam.native_battle import maybe_native

    from hiro.sim import interval as iv

    env = maybe_native(iv._env(iv.R2Setup(deck=DECK, drinks=[], hp=20, max_hp=33, permil={"Vo": 37490, "Da": 36100, "Vi": 22860}), 7))
    env.rx.advance()
    return SimpleNamespace(env=env)


def _screen(game, *, wrong=False):
    rx = game.env.rx
    ids = [rx.card_info(g)[0] for g in rx.zone("hand")]
    if wrong:
        ids = ids[::-1] if len(set(ids)) > 1 else ["p_card-00-act-0_001"] + ids[1:]
    hand = [SimpleNamespace(slot=i, card_id=c, name=c, upgrade=0, available=True) for i, c in enumerate(ids)]
    c = rx.clone()
    c.advance()
    legal = [Action.play_card(int(a[1])) for a in c.legal_actions() if a[0] == "play"] + [Action.skip()]
    state = SimpleNamespace(hand=hand, drinks=[], sim_game=game, sim_exact_exam=True, hp=20, max_hp=33, genki=0, score=0,
                            remaining_turns=12, turn=1, motivation=0, impression=0)  # fmt: skip
    return state, legal


def test_search_picks_legal_action(adv):
    state, legal = _screen(_game())
    d = adv.decide(state, SimpleNamespace(turn=1), None, legal)
    assert d.source == "search" and d.action.legal_key() in {a.legal_key() for a in legal}
    assert "GNRPA" in d.reason and d.candidates


def test_mismatch_falls_back_then_turns_off(adv):
    from hiro.search import advisor as mod

    game = _game()
    state, legal = _screen(game, wrong=True)
    for _ in range(mod.MISS_LIMIT):
        d = adv.decide(state, SimpleNamespace(turn=1), None, legal)
        assert d.source != "search" and any("核不上" in w or "对不上" in w for w in d.warnings)
    assert adv.off_why and "连续" in adv.off_why
    good, legal2 = _screen(game)
    assert adv.decide(good, SimpleNamespace(turn=1), None, legal2).source != "search"  # 这场剩下的步不再搜


def test_no_engine_state_falls_back(adv):
    state, legal = _screen(_game())
    state.sim_game = None
    assert adv.decide(state, SimpleNamespace(turn=1), None, legal).source != "search"


def test_without_rust_search_falls_back_and_says_why(monkeypatch):
    """用户 11-14：H.I.F 缺省用搜索；没有带 search 的 Rust 包时每步退回 bench 中转层，理由里写明（不要求装新 Rust 包）。"""
    monkeypatch.setenv("HIRO_BENCH_ADVISOR", "0")
    from hiro.bench_bridge.advisor import BenchAdvisor
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import Decision
    from hiro.search import advisor as sa

    monkeypatch.setattr(BenchAdvisor, "decide", lambda self, *a, **k: Decision(Action.skip(), "退回", "rule", None))
    monkeypatch.setattr(sa, "_rs", lambda: None)
    a = sa.SearchAdvisor(CAMPUS_HIRO, None)
    a.on_round_start(1, {"exam": "r2", "idol": "i_card-shro-3-007", "deck": [list(c) for c in DECK]})
    d = a.decide(SimpleNamespace(), SimpleNamespace(turn=1), None, [Action.skip()])
    assert d.source == "rule" and any(sa.NO_RS in w for w in d.warnings)


class _FakeRx:
    """check() 用到的引擎接口：手牌卡 id、推进后的合法动作、饮料。"""

    def __init__(self, hand, playable):
        self._hand, self._playable, self.drinks, self.ended = hand, playable, [], False

    def zone(self, z):
        return list(range(len(self._hand))) if z == "hand" else []

    def card_info(self, g):
        return (self._hand[g], 0, None)

    def clone(self):
        return self

    def advance(self):
        pass

    def legal_actions(self):
        return [("play", i) for i in self._playable] + [("end",)]


def _check(eng_play, scr_play):
    from hiro.search.advisor import check

    hand = ["c0", "c1", "c2"]
    state = SimpleNamespace(hand=[SimpleNamespace(slot=i, card_id=c) for i, c in enumerate(hand)], drinks=[])
    return check(_FakeRx(hand, eng_play), state, [f"play:{i}" for i in scr_play] + ["skip"])


def test_engine_stricter_than_screen_still_searches():
    """DMM mainverify2 R2 T7（11-17）：ゆめみごこち 要消耗好印象 2，不够时游戏不让打、卡面却不变灰；画面判可打、引擎判不可打。
    原来整步退回 UINet，UINet 选了它，连点 3 次停在选中态才禁用。引擎更严时照常搜：搜索只在引擎可打的牌里选，画面上都能打。"""
    assert _check({0, 1}, {0, 1, 2}) is None


def test_engine_looser_than_screen_is_a_mismatch():
    """引擎认为能打、画面灰着（mainverify2 R1：元気 0 读成 10，引擎以为「届いて！」（元気 ≥7）能打）：搜索可能选到打不出的牌，照旧退回。"""
    assert "能打出的牌对不上" in (_check({0, 1, 2}, {0, 1}) or "")


def test_apply_schedule_known_turns():
    """审查日程已知（平台 exam.turn_types）：回合属性按日程改在副本上，已知的回合不再随机；认不出的项、比日程多的回合照旧随机。"""
    from hiro.search.advisor import apply_schedule, free_turns

    rx = _game().env.rx
    sched = ["Vo", "Da", "Vo", "Vo", "Vo", "Da", "Vi", "Vi", "Da", "Vi", "Da", "Vo"]
    c, free = apply_schedule(rx, sched)
    assert list(c.turn_types) == [{"Vo": 1, "Da": 2, "Vi": 3}[a] for a in sched] and free == []
    _, free2 = apply_schedule(rx, sched[:6] + [None] * 6)
    assert free2 == [i for i in free_turns(rx) if i >= 6]


def test_known_schedule_switch_reads_session_exam(adv, monkeypatch):
    """开关 HIRO_SEARCH_KNOWN_TURNS 缺省关；打开后从 session.state.exam.turn_types 读，交给搜索的回合属性按日程、free 为空。"""
    from hiro.search import advisor as sa

    sched = ["Da"] * 12
    adv._session = SimpleNamespace(state=SimpleNamespace(exam={"turn_types": sched}))
    seen = {}
    real = adv.rs.search

    def spy(rx, prefix, free, **kw):
        seen.update(types=list(rx.turn_types), free=list(free))
        return real(rx, prefix, free, **kw)

    monkeypatch.setattr(adv, "rs", SimpleNamespace(search=spy))
    game = _game()
    state, legal = _screen(game)
    monkeypatch.delenv("HIRO_SEARCH_KNOWN_TURNS", raising=False)
    adv.decide(state, SimpleNamespace(turn=1), None, legal)
    assert seen["free"] == sa.free_turns(game.env.rx)  # 缺省关：照旧
    monkeypatch.setenv("HIRO_SEARCH_KNOWN_TURNS", "1")
    adv.decide(state, SimpleNamespace(turn=1), None, legal)
    assert seen["types"] == [2] * 12 and seen["free"] == []
