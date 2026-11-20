"""hiro.sim.screen_source：不读存档，从画面局面 + 培育记账的牌组合成开局、按执行的动作跟踪、出预演。
「画面」用模拟器自己打的一局（SimGame.state()），牌组用同一份合成开局的牌组。"""

import copy

import pytest

from hiro.brain.state import CAMPUS_HIRO

pytestmark = pytest.mark.sim_master


class _Session:
    def __init__(self, deck):
        self.state = type("S", (), {"produce": {"deck": [{"card_id": c, "upgrade": u} for c, u in deck]}})()
        self.notes = []

    def note(self, t):
        self.notes.append(t)


def _truth():
    from hiro.brain.battle import BattleBrain
    from hiro.sim import interval as iv
    from hiro.sim import resource as rv
    from hiro.sim.env import SimGame

    deck = list(rv.REFERENCE_DECK)
    setup, _ = rv.setup_for("r1", deck=deck, drinks=[], hp=30, max_hp=33, permil=None)
    game = SimGame(iv._env(setup, seed=7), "r1", CAMPUS_HIRO)
    game.memory = BattleBrain(CAMPUS_HIRO, None).new_memory("r1")
    return deck, game


def test_tracks_and_previews_along_a_sim_game():
    from hiro.contracts import Action
    from hiro.sim.screen_source import SimScreenExamStateSource

    deck, truth = _truth()
    src = SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO, "session": _Session(deck)})
    mem = copy.deepcopy(truth.memory)
    with_preview = 0
    for _ in range(12):
        if truth.ended:
            break
        state = truth.state()  # 「画面」读数
        src.game = None
        src.screen_drinks = [d.drink_id for d in state.drinks]
        src._sync(state, mem)
        assert src.game is not None, src.lost
        pv = src.previews(state)
        if pv:
            with_preview += 1
            legal = {a for a in state.legal_actions()}
            assert all(p.action.legal_key() in legal for p in pv)
        legal = state.legal_actions()
        act = next((a for a in legal if a.startswith("play:")), "skip")
        slot = int(act.split(":")[1]) if act != "skip" else None
        truth.step(dict(kind="play", id=truth._hand[slot]) if slot is not None else dict(kind="end_turn"))
        src.executed(Action.from_legal(act), None)
    assert with_preview >= 8
    assert src.stats["lost"] == 0


def test_rounds_switch(monkeypatch):
    from hiro.sim import screen_source as ss

    monkeypatch.setattr(ss, "ROUNDS", ("r2",))
    src = ss.SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO})
    assert src.label is None  # R1 不给预演
    src.begin({"exam": "r2", "profile": CAMPUS_HIRO})
    assert src.label == "r2"


def test_basic_card_budget_resets_per_exam():
    """「一场最多加 6 张」基础类卡按场算：每次 begin（新一场考试、再挑戦）清零，不跨场累计（DMM 实机：R2 第 3 次再挑戦时
    画面上的 距離感の基本 加不进合成牌组，「重建失败…在模拟器里找不到」，这一场后面都给不出预演）。"""
    from hiro.sim.screen_source import SimScreenExamStateSource

    src = SimScreenExamStateSource()
    src.begin({"exam": "r2"})
    src._added = 6
    src.setup = object()
    src.begin({"exam": "r2"})
    assert src._added == 0 and src.setup is None


def test_tracking_never_errors_on_random_effects(monkeypatch):
    """dmmt1 R1：跟踪 99 次 sim_error、核对通过 0 次。按画面重放随机效果时给 env.rng 赋新对象，
    新引擎（gakumas_exam，Python / Rust 都是）的 rng 是只读属性，只能改 rng.state。"""
    from hiro.contracts import Action
    from hiro.sim import track
    from hiro.sim.screen_source import SimScreenExamStateSource

    reasons = []
    real = track.SimTracker.sync

    def spy(self, screen):
        r = real(self, screen)
        reasons.append((r.reason, (r.notes or [""])[-1]))
        return r

    monkeypatch.setattr(track.SimTracker, "sync", spy)
    deck, truth = _truth()
    src = SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO, "session": _Session(deck)})
    mem = copy.deepcopy(truth.memory)
    for _ in range(30):
        if truth.ended:
            break
        state = truth.state()
        src.game = None
        src.screen_drinks = [d.drink_id for d in state.drinks]
        src._sync(state, mem)
        legal = state.legal_actions()
        act = next((a for a in legal if a.startswith("play:")), "skip")
        slot = int(act.split(":")[1]) if act != "skip" else None
        truth.step(dict(kind="play", id=truth._hand[slot]) if slot is not None else dict(kind="end_turn"))
        src.executed(Action.from_legal(act), None)
    assert not [r for r in reasons if r[0] == "sim_error"], reasons


SLEEPY = "p_card-00-acc-0_002"


def _needs_new_card():
    rs = pytest.importorskip("gakumas_exam_rs")
    if not hasattr(getattr(rs, "Exam", object), "new_card"):
        pytest.skip("Rust 后端没有 new_card")


def test_card_missing_from_ledger_is_caught_not_lost():
    """DMM mainverify2（11-17）：支援事件给的「頭上に注意！」没进牌组账本，R1 T1 起手就有它 → 「画面上的 … 在模拟器里找不到」，
    搜索整场退回 UINet。账本少的卡出现在手牌时按画面新建一张接住，跟踪不丢。"""
    _needs_new_card()
    from hiro.contracts import Action
    from hiro.sim.screen_source import SimScreenExamStateSource

    deck, truth = _truth()
    first = truth.state()
    ids = [c[0] for c in deck]
    gone = next(c.card_id for c in first.hand if ids.count(c.card_id) == 1)  # 起手里牌组只有一张的卡
    ledger = [c for c in deck if c[0] != gone]  # 账本漏了它
    src = SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO, "session": _Session(ledger)})
    mem = copy.deepcopy(truth.memory)
    for _ in range(6):
        if truth.ended:
            break
        state = truth.state()
        src.game = None
        src.screen_drinks = [d.drink_id for d in state.drinks]
        src._sync(state, mem)
        assert src.game is not None, src.lost
        legal = state.legal_actions()
        act = next((a for a in legal if a.startswith("play:")), "skip")
        slot = int(act.split(":")[1]) if act != "skip" else None
        truth.step(dict(kind="play", id=truth._hand[slot]) if slot is not None else dict(kind="end_turn"))
        src.executed(Action.from_legal(act), None)
    assert src.stats["lost"] == 0


def test_second_generated_sleepy_keeps_the_first():
    """DMM mainverify2 R1 T7（11-17）：引擎里已有一张考试中生成的 眠気（不在合成牌组），画面手牌两张 眠気。原来 _add_basic 把缺的一张
    加进合成牌组、从开局重新编译，引擎里生成的那张跟着旧局面丢了，重建还是缺一张。要在当前局面上新建，保留已有的那张。"""
    _needs_new_card()
    from hiro.sim.screen_source import SimScreenExamStateSource

    deck, truth = _truth()
    src = SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO, "session": _Session(deck)})
    mem = copy.deepcopy(truth.memory)
    state = truth.state()
    src.screen_drinks = [d.drink_id for d in state.drinks]
    src._sync(state, mem)
    assert src.game is not None
    rx = src.tracker.game.env.rx
    g = rx.new_card(SLEEPY, 0)  # 考试中效果塞进山札的那张（合成牌组里没有）
    rx.set_zone("deck", list(rx.zone("deck")) + [g])
    screen = copy.deepcopy(state)
    screen.hand[0].card_id, screen.hand[0].upgrade = SLEEPY, 0
    screen.hand[1].card_id, screen.hand[1].upgrade = SLEEPY, 0
    src.game = None
    src._sync(screen, mem)
    assert src.game is not None, src.lost
    hand = [src.game.env.rx.card_info(i)[0] for i in src.game.env.zones["hand"]]
    assert hand.count(SLEEPY) == 2, hand


@pytest.mark.parametrize("on", [False, True])
def test_r2_ouenbou_pool_fill_swaps_placeholder(monkeypatch, on):
    """mainz4 R2 复盘③（协调 11-18）：HIRO_SIM_OUENBOU_POOL=1 时 R2 応援棒按広的真实卡池（やる気流派）补；画面露出的基础卡在引擎里不够时
    换掉一张还没露面的占位卡，牌组总数保持 22。缺省开；0 关掉是旧轮换：补的卡对不上，露出的卡只能新建，引擎牌组越补越多。"""
    _needs_new_card()
    from hiro.sim import interval as iv
    from hiro.sim import resource as rv
    from hiro.sim.screen_source import SimScreenExamStateSource

    if on:
        monkeypatch.delenv("HIRO_SIM_OUENBOU_POOL", raising=False)  # 缺省开（协调 11-18）
    else:
        monkeypatch.setenv("HIRO_SIM_OUENBOU_POOL", "0")
    deck = (list(rv.REFERENCE_DECK) * 2)[:17]
    SHIGUSA = "p_card-02-act-0_038"  # 仕草の基本（卡池里有、旧轮换前 5 张没有；新补法只补 1 张）
    from hiro.brain.battle import BattleBrain
    from hiro.sim.env import SimGame

    setup, _ = rv.setup_for("r2", deck=deck, drinks=[], hp=20, max_hp=33, permil=None)
    truth = SimGame(iv._env(setup, seed=7), "r2", CAMPUS_HIRO)
    truth.memory = BattleBrain(CAMPUS_HIRO, None).new_memory("r2")
    src = SimScreenExamStateSource()
    src.begin({"exam": "r2", "profile": CAMPUS_HIRO, "session": _Session(deck)})
    mem = copy.deepcopy(truth.memory)
    state = truth.state()
    src.screen_drinks = []
    src._sync(state, mem)
    assert src.game is not None, src.lost
    screen = copy.deepcopy(state)
    for c in screen.hand[:2]:  # 画面上露出两张 仕草の基本
        c.card_id, c.upgrade = SHIGUSA, 0
    src.game = None
    src._sync(screen, mem)
    assert src.game is not None, src.lost
    env = src.game.env
    total = sum(len(env.zones[z]) for z in ("hand", "deck", "grave", "lost"))
    hand = [env.rx.card_info(i)[0] for i in env.zones["hand"]]
    assert hand.count(SHIGUSA) == 2
    assert SHIGUSA not in [c for c, _ in iv.ouenbou_fill(deck)[17:]]  # 旧轮换前 5 张没有 仕草
    assert total == (22 if on else 24)  # 关掉时两张都只能新建，22 + 2


def test_unique_item_fallback_when_ledger_empty(monkeypatch):
    """mainz4（11-18）：P 道具账本为空时合成开局把模板道具全清掉，引擎里没有固有道具。HIRO_SIM_UNIQUE_ITEM=1 时只放偶像卡的固有道具
    （IdolCard.beforeProduceItemId），也可以直接写道具 id；缺省开（协调 11-18），0 关。"""
    from hiro.sim import screen_source as ss

    src = ss.SimScreenExamStateSource()
    src.begin({"exam": "r2", "profile": CAMPUS_HIRO, "session": _Session([("p_card-02-men-1_031", 0)])})
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    monkeypatch.setenv("HIRO_SIM_UNIQUE_ITEM", "0")
    assert src._run_loadout()[0]["items"] == ()
    monkeypatch.delenv("HIRO_SIM_UNIQUE_ITEM", raising=False)  # 缺省开
    run, notes = src._run_loadout()
    assert run["items"] == ("pitem_02-3-076-0",) and any("固有道具" in n for n in notes)
    monkeypatch.setenv("HIRO_SIM_UNIQUE_ITEM", "pitem_02-3-076-1")
    assert src._run_loadout()[0]["items"] == ("pitem_02-3-076-1",)
