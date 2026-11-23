"""HiroHifStrategy.on_skill_card_change：按决策的 card_id 换出；决策取消时传 cancel；阶段 1 候选未识别时换入第 0 张。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, ExecutionResult, GameState
from hiro.produce.hif import HiroHifStrategy

import pytest

DAYDREAM, HIGH, BASIC = "p_card-02-act-1_037", "p_card-00-men-2_014", "p_card-02-act-0_037"


class StubSession:
    def __init__(self, fields_by_page):
        self.fields_by_page, self.state, self.records, self.notes = fields_by_page, GameState(), [], []

    def observe(self, scene, **_):
        self.state = GameState(page_fields=dict(self.fields_by_page.get(scene, {})))
        return scene

    def verify(self, action, *, page_before, **_):
        return ExecutionResult(action=action, ok=True, page_before=page_before)

    def record(self, obs, **kw):
        self.records.append((obs, kw))

    def note(self, text):
        self.notes.append(text)


_CTX: list = []  # 最近建的 StubCtx：换卡执行器（card_ops.change_stage1 / 2）的替身把调用记到它上面


@pytest.fixture(autouse=True)
def _route_card_ops(monkeypatch):
    from hiro.produce import card_ops

    monkeypatch.setattr(card_ops, "change_stage1", lambda _self, index: _CTX[-1].commit_stage1(index))
    monkeypatch.setattr(card_ops, "change_stage2", lambda _self, index, **kw: _CTX[-1].commit_stage2(index, **kw))
    yield
    _CTX.clear()


class StubCtx:
    def __init__(self, stage):
        self.stage, self.calls = stage, []
        _CTX.append(self)

    def commit_stage1(self, index):
        self.calls.append(("s1", index))
        return True

    def commit_stage2(self, index, card_id=None, cancel=False, remove_index=None, remove_upgrade=None):
        self.calls.append(("s2", card_id, cancel))
        self.stage2_args = (remove_index, remove_upgrade)
        return not cancel


def _strategy(fields):
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = StubSession(fields)
    st.deciders = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True)
    return st


def test_change_removes_decided_card_not_slot0():
    grid = [
        {"index": 0, "card_id": DAYDREAM, "upgrade": 1},
        {"index": 1, "card_id": HIGH, "upgrade": 0},
        {"index": 2, "card_id": BASIC, "upgrade": 0},
    ]
    st, ctx = _strategy({"SKILL_CARD_CHANGE_2": {"cards": grid}}), StubCtx(stage=1)
    st.CHANGE1_DECIDE_RETRIES = 0  # 本用例测「一直读不到候选」：不重读
    st.on_skill_card_change(ctx)
    assert ctx.calls[0] == ("s1", 0)  # 阶段 1 候选未识别 → 第 0 张
    assert ctx.calls[1] == (
        "s2",
        HIGH,
        False,
    )  # 冲突卡先换出（用户 2026-11-03：冲突牌 > 初始卡），デイドリーミング 不动
    assert ctx.stage2_args == (
        1,
        0,
    )  # 决策的格子序号 / 强化次数一起交给阶段 2（第十九局：同名卡多张时只按 card_id 点错了）
    assert len(st.s.records) == 2 and all(kw["execution"].ok for _, kw in st.s.records)


def test_change_cancels_when_deck_unread():
    st, ctx = _strategy({}), StubCtx(stage=2)
    st.CHANGE_OBSERVE_TRIES = 1
    st.on_skill_card_change(ctx)
    assert ctx.calls == [("s2", None, True)]
    assert st.s.records[0][1]["decision"].action.kind is ActionKind.CHANGE_CARD


def test_change_cancel_then_return_forces_lowest_value():
    """取消后又回到换卡页（阶段 1 无取消键，main8 死循环）：按 choose_card_to_change 换出价值最低的非保护卡；没有可换的才停下。"""
    from hiro.produce.hif import UnhandledPage

    import pytest

    grid = [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}, {"index": 1, "card_id": BASIC, "upgrade": 0}]
    st = _strategy({"SKILL_CARD_CHANGE_2": {"cards": grid}})
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        __import__("hiro.contracts", fromlist=["Action"]).Action(ActionKind.CHANGE_CARD, {"cancel": True}),
        "不换",
        "rule",
        0.7,
    )
    ctx = StubCtx(stage=2)
    st.on_skill_card_change(ctx)
    st.on_skill_card_change(ctx)
    assert ctx.calls == [("s2", None, True), ("s2", BASIC, False)]
    st2 = _strategy({"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}]}})
    st2.CHANGE_OBSERVE_TRIES = 1
    st2.s.recover = lambda *a: None
    st2.deciders.decide = st.deciders.decide
    st2._change_cancels = 1
    with pytest.raises(UnhandledPage):
        st2.on_skill_card_change(StubCtx(stage=2))


def test_stage1_pick_mapped_to_column_by_center():
    """识别多读一张时，决策的候选序号按卡面中心 x 换算成列（qualify6 r2 #29：#3 せのびの魔法 在第 2 列）。"""
    from hiro.contracts import Action, ActionKind, Decision

    cands = [{"index": 0, "center": [223, 899]}, {"index": 1, "center": [310, 860]}, {"index": 2, "center": [362, 899]},
             {"index": 3, "center": [500, 898]}]  # fmt: skip
    st = _strategy({"SKILL_CARD_CHANGE_1": {"candidates": cands}})
    st.s.observe("SKILL_CARD_CHANGE_1")
    d = Decision(Action(ActionKind.CHANGE_CARD, {"stage": 1, "pick_index": 3}), "", "rule", 1.0)
    assert st._stage1_column(3, d) == 2 and st._stage1_column(0, d) == 0


def test_stage1_rereads_until_candidates_reach_state():
    """换卡页第一帧：状态还是上一页（候选没进状态），决策器回 Wait；再读一帧候选到了，就按价值选，不盲换第 0 张
    （Windows 第二局两次、Mac A5b 一次，2026-11-06）。"""
    cands = [{"index": 0, "card_id": BASIC, "upgrade": 0}, {"index": 1, "card_id": BASIC, "upgrade": 0},
             {"index": 2, "card_id": DAYDREAM, "upgrade": 1}]  # fmt: skip
    st, ctx = _strategy({}), StubCtx(stage=1)
    st.CHANGE1_DECIDE_WAIT = 0
    st.CHANGE_OBSERVE_TRIES = 1
    seen = []

    def observe(scene, **_):
        seen.append(scene)
        first_frame = scene == "SKILL_CARD_CHANGE_1" and seen.count(scene) == 1
        st.s.state = GameState(
            page_fields={} if first_frame else ({"candidates": cands} if scene == "SKILL_CARD_CHANGE_1" else {})
        )
        return scene

    st.s.observe = observe
    st._stage1_column = lambda pick, d: pick
    st.on_skill_card_change(ctx)
    assert seen.count("SKILL_CARD_CHANGE_1") == 3  # 第一帧拿不到，重读一帧；再读一帧核对候选两帧一致（mainz3 r3 04:21）
    assert ctx.calls[0] == ("s1", 2)  # 按价值选 デイドリーミング，不是第 0 张


def test_change_pick_by_cull():
    """换入按删卡顺序挑（最晚才会被删的），不挑冲突牌（Win2 seq19：止められない想い vs 気まぐれハート；用户 11-11 定为一直开）。"""
    from hiro.brain import deciders as D

    cands = [{"index": 0, "card_id": "p_card-02-men-2_056", "upgrade": 0, "name": "止められない想い"},
             {"index": 1, "card_id": "p_card-02-men-3_043", "upgrade": 0, "name": "トキメキ"},
             {"index": 2, "card_id": "p_card-02-men-2_100", "upgrade": 0, "name": "気まぐれハート"}]  # fmt: skip
    state = GameState(page_fields={"candidates": cands}, produce={"mode": "produce-008"})
    on = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True).change_pick(state).action.args["pick_index"]
    keys = {c["index"]: D.cull_key(c["card_id"], 0, finals=True) for c in cands}
    assert on == max(keys, key=keys.get) and on != 1  # 最晚才会被删的；不挑冲突牌 トキメキ


def test_forced_change_falls_back_to_ledger_when_screen_all_protected():
    """DMM mainz3（11-18 03:34，窗口 559x993）：牌组 22 张，换卡对话框整副拼接不可信只留第一屏 12 张，全是核心 / 保护卡；
    取消后绕回换卡页，「没有可换出的非保护卡」停机。一屏里挑不出时按牌组账本挑价值最低的非保护卡，执行按 card_id 翻屏找。"""

    class LedgerSession(StubSession):
        def observe(self, scene, **_):
            self.state = GameState(page_fields=dict(self.fields_by_page.get(scene, {})),
                                   produce={"deck": [{"card_id": DAYDREAM, "upgrade": 1}, {"card_id": BASIC, "upgrade": 0}]})  # fmt: skip
            return scene

    st = _strategy({})
    st.s = LedgerSession(
        {"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}], "deck_complete": False}}
    )
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7
    )
    st._change_cancels = 1
    ctx = StubCtx(stage=2)
    st.on_skill_card_change(ctx)
    assert ctx.calls == [("s2", BASIC, False)] and ctx.stage2_args[0] is None  # 按 card_id 翻屏找，不按格子号


def test_forced_change_gives_up_after_repeated_failures():
    """mainz3 r1（11-18 03:47）：强制换出的卡执行侧没点成，每次取消都绕回换卡页，转了 2 分钟。强制换出 CHANGE_FORCED_MAX 次都没换成就停下。"""
    from hiro.produce.hif import UnhandledPage

    import pytest

    st = _strategy({})
    st.s = StubSession(
        {"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": BASIC, "upgrade": 0}], "deck_complete": True}}
    )
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7
    )
    st._change_cancels = 1 + st.CHANGE_FORCED_MAX
    with pytest.raises(UnhandledPage, match="强制换出"):
        st.on_skill_card_change(StubCtx(stage=2))


def test_forced_change_is_recorded_as_change_for_ledger(monkeypatch):
    """mainz3 r3（11-18 04:16）：强制换出 ひなたぼっこ → イメトレ 在游戏里换成了，账本却没记：记录的决策还是「取消」，
    events_from_step 不出 CARD_CHANGE。强制换出做成后，记录的动作改成实际执行的换卡（换出卡的强化次数取执行侧实际点到的那张）。"""
    from hiro.produce import card_ops

    st = _strategy({})
    st.s = StubSession(
        {"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": BASIC, "upgrade": 0}], "deck_complete": True}}
    )
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7
    )
    st._change_cancels = 1
    ctx = StubCtx(stage=2)
    real = ctx.commit_stage2

    def commit(index, **kw):
        card_ops.LAST_CHANGED = {"card_id": kw.get("card_id"), "upgrade": 1}
        return real(index, **kw)

    ctx.commit_stage2 = commit
    st.on_skill_card_change(ctx)
    d = st.s.records[-1][1]["decision"]
    assert d.action.kind is ActionKind.CHANGE_CARD and not d.action.args.get("cancel")
    assert d.action.args["remove_card_id"] == BASIC and d.action.args["remove_upgrade"] == 1


def test_cancel_stays_cancel_in_record():
    st = _strategy({})
    st.s = StubSession(
        {"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": BASIC, "upgrade": 0}], "deck_complete": True}}
    )
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7
    )
    st.on_skill_card_change(StubCtx(stage=2))
    assert st.s.records[-1][1]["decision"].action.args.get("cancel") is True  # 第一次取消：照记取消，不出 CARD_CHANGE


def test_stage1_waits_until_candidates_read_twice_the_same(monkeypatch):
    """mainz3 r3（11-18 04:21）：换卡阶段 1 第一帧把 気まぐれハート 读成 全身全霊（存下的帧重读是对的，读的那一帧在入场动画里），
    决策按 全身全霊 选了第 0 张。候选要连续两帧读得一样才决策；变了就按新读数重新决策。"""
    from hiro.contracts import Decision
    from hiro.produce import hif

    monkeypatch.setattr(hif, "sleep", lambda s: None)
    ZENSHIN, KIMAGURE, LETTER = "p_card-03-act-3_065", "p_card-02-men-2_100", "p_card-02-act-2_047"
    reads = [[ZENSHIN, LETTER], [KIMAGURE, LETTER], [KIMAGURE, LETTER]]

    class SeqSession(StubSession):
        def observe(self, scene, **_):
            if scene == "SKILL_CARD_CHANGE_1":
                ids = reads.pop(0) if len(reads) > 1 else reads[0]
                self.state = GameState(
                    page_fields={
                        "candidates": [{"slot": i, "index": i, "card_id": c, "upgrade": 0} for i, c in enumerate(ids)]
                    }
                )
                return scene
            return super().observe(scene)

    st = _strategy({})
    st.s = SeqSession(
        {"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": BASIC, "upgrade": 0}], "deck_complete": True}}
    )
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st._stage1_column = lambda pick, d: pick
    seen = []

    def decide(page, state, **kw):
        if page == "SKILL_CARD_CHANGE_1":
            ids = [c["card_id"] for c in state.page_fields["candidates"]]
            seen.append(ids)
            return Decision(
                Action(ActionKind.CHANGE_CARD, {"stage": 1, "pick_index": 0 if ids[0] == ZENSHIN else 1}),
                "x",
                "rule",
                0.7,
            )
        return Decision(Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7)

    st.deciders.decide = decide
    ctx = StubCtx(stage=1)
    st.on_skill_card_change(ctx)
    assert ctx.calls[0] == ("s1", 1)  # 按第二、三帧（一致）的读数决策
    assert seen[-1][0] == KIMAGURE
