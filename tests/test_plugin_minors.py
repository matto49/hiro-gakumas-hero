"""插件框架 11-25 合入时暂缓的小问题（2026-11-26），每条一组复现用例。编号对应当时列的 9 条。"""

import json
import time
from types import SimpleNamespace
from typing import Any, ClassVar, cast

from hiro import plugins, strategy
from hiro.brain import plugin_gate
from hiro.brain.deciders import ProduceDeciders
from hiro.contracts import GameState, Observation, PageObs, TraceWriter
from hiro.loop import sources
from hiro.loop.reduce import reduce
from hiro.loop.session import Session
from hiro.plugin_record import RECORD
from hiro.plugins import SourceRead

import numpy as np
import pytest

IMG = np.zeros((1280, 720, 3), np.uint8)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    for m in (plugins, strategy, sources, plugin_gate, RECORD):
        m.reset()
    RECORD.reset(closers=True)
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    yield
    for m in (plugins, strategy, sources, plugin_gate, RECORD):
        m.reset()
    RECORD.reset(closers=True)


def _cfg(tmp_path, monkeypatch, d):
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps({"version": 1, **d}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    strategy.reset()


def _session(tmp_path, name="t"):
    return Session(TraceWriter(root=tmp_path / "traces", name=name), shoot=lambda: IMG, sleep=lambda x: None)


def _obs(seq, page, fields=None, *, hidden=None, source="screen"):
    return Observation(
        seq, float(seq), PageObs(page, "produce"), fields=fields or {}, source=source, hidden=hidden or {}
    )


# ---- 1  GameState.hidden 换页清空，隐藏信息标记仍然准确


def test_hidden_cleared_on_page_change():
    s = reduce(GameState(), _obs(1, "HIF_EXAM", {"hp": 30}))
    s = reduce(s, _obs(1, "HIF_EXAM", hidden={"deck_order": ["a", "b"]}, source="plugin"))
    assert s.hidden == {"deck_order": ["a", "b"]}
    s = reduce(s, _obs(2, "HIF_EXAM", {"hp": 29}))  # 同一页：还在
    assert s.hidden == {"deck_order": ["a", "b"]}
    s = reduce(s, _obs(3, "HIF_EXAM_RESULT", {"score": 1}))  # 考试打完：上一场的牌库顺序不能带进结算页的记录
    assert s.hidden == {}
    s = reduce(s, _obs(4, "HIF_EXAM_BRIEFING", {}))
    s = reduce(s, _obs(5, "HIF_EXAM", {"hp": 30}))  # 下一场开考：干净
    assert s.hidden == {}


def test_hidden_merges_within_page_and_new_value_wins():
    s = reduce(GameState(), _obs(1, "ACTION_SELECT", {"stamina": 1}))
    s = reduce(s, _obs(1, "ACTION_SELECT", hidden={"a": 1, "b": 2}, source="plugin"))
    s = reduce(s, _obs(2, "ACTION_SELECT", hidden={"b": 3}, source="plugin"))
    assert s.hidden == {"a": 1, "b": 3}


class HiddenSrc:
    id = "hook"
    version = "1"
    contracts = "1"
    trust = "screen"
    pages = None
    hidden_on: ClassVar[frozenset] = frozenset({"ACTION_SELECT"})

    def start(self, context): ...
    def read(self, page, frame_t, state):
        h = {"deck_order": ["x"]} if page in type(self).hidden_on else {}
        return SourceRead(t=frame_t, fields={}, hidden=h)

    def stop(self): ...


def test_step_record_does_not_carry_previous_pages_hidden(tmp_path, monkeypatch):
    """记录里每一步的 state.hidden 只有当前页拿到的；marker 在收到的那一刻就记了，不因为之后清空而丢。"""
    plugins.register("observation_source", HiddenSrc, id="hook")
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    s = _session(tmp_path)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    o1 = s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    st1 = s.record(o1)
    assert st1.state.hidden == {"deck_order": ["x"]}  # 当前页拿到的，决策和记录都看得到
    assert s.state.hidden == {"deck_order": ["x"]}
    o2 = s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    assert s.state.hidden == {}  # 换页就没了
    st2 = s.record(o2)
    assert st2.state.hidden == {} and st2.observation.hidden == {}
    assert RECORD.uses_hidden and RECORD.hidden_fields == {"hook@1": ["deck_order"]}


def test_uses_hidden_stays_false_when_source_gives_none(tmp_path, monkeypatch):
    HiddenSrc.hidden_on = frozenset()
    try:
        plugins.register("observation_source", HiddenSrc, id="hook")
        _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
        s = _session(tmp_path)
        s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
        s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
        assert s.state.hidden == {} and not RECORD.uses_hidden
    finally:
        HiddenSrc.hidden_on = frozenset({"ACTION_SELECT"})


# ---- 2  启动失败的插件后面不再调用


class BadStartSrc:
    id = "bad"
    version = "1"
    contracts = "1"
    trust = "screen"
    pages = None
    reads = 0
    starts = 0
    stops = 0

    def start(self, context):
        type(self).starts += 1
        raise RuntimeError("起不来")

    def read(self, page, frame_t, state):
        type(self).reads += 1
        return SourceRead(t=frame_t, fields={}, hidden={"leak": 1})

    def stop(self):
        type(self).stops += 1


def test_failed_source_start_is_not_read_again(tmp_path, monkeypatch):
    BadStartSrc.reads = BadStartSrc.starts = BadStartSrc.stops = 0
    plugins.register("observation_source", BadStartSrc, id="bad")
    _cfg(tmp_path, monkeypatch, {"sources": ["bad"]})
    s = _session(tmp_path)
    for _ in range(4):
        s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    assert BadStartSrc.starts == 1 and BadStartSrc.reads == 0
    assert s.state.hidden == {} and not RECORD.uses_hidden
    assert RECORD.points["source.bad"]["errors"] == 1  # 只记一次启动失败，不是每帧一次
    RECORD.at_exit()
    assert BadStartSrc.stops == 0  # 没起来的不用收尾


class BadBeginProduce:
    id = "bp"
    version = "1"
    contracts = "1"
    pages = frozenset({"STUDY"})
    begins = 0
    decides = 0
    ends: ClassVar[list] = []

    def begin(self, context):
        type(self).begins += 1
        raise RuntimeError("begin 炸了")

    def decide(self, page, state, *, n_options, kw):
        type(self).decides += 1
        return None

    def end(self, result):
        type(self).ends.append(result)


def test_failed_produce_begin_is_not_decided_again(tmp_path, monkeypatch):
    BadBeginProduce.begins = BadBeginProduce.decides = 0
    BadBeginProduce.ends = []
    plugins.register("produce_advisor", BadBeginProduce, id="bp")
    _cfg(tmp_path, monkeypatch, {"produce": {"STUDY": "bp"}})
    s = _session(tmp_path)
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    dec = ProduceDeciders(None)
    ds = [dec.decide("STUDY", s.state, n_options=3) for _ in range(3)]
    assert BadBeginProduce.begins == 1 and BadBeginProduce.decides == 0
    assert all(d.plugin is None for d in ds)  # 都由内置决策器定
    assert any("begin 出错" in w for w in ds[0].warnings)
    assert RECORD.points["produce.STUDY"]["errors"] == 1
    RECORD.at_exit()
    assert BadBeginProduce.ends == []  # 没 begin 成功的不调 end


# ---- 2（续）出招插件开局回调出错、考试转接里数据来源 start 出错，之后同样不再调


def _legal():
    from hiro.contracts import Action, ActionKind

    return [Action(ActionKind.PLAY_CARD, {"slot": 0}), Action(ActionKind.SKIP, {})]


class _Given:
    def decide(self, state, memory, previews, legal):
        from hiro.contracts import Decision

        return Decision(legal[0], source="rule")

    def on_round_start(self, round_no, context): ...
    def on_round_end(self, result): ...


def test_failed_round_start_exam_plugin_is_not_decided_again():
    from hiro.contracts import Decision
    from hiro.exec.exam import GuardedAdvisor

    class Adv:
        decides = 0

        def on_round_start(self, round_no, context):
            raise RuntimeError("开局炸了")

        def decide(self, state, memory, previews, legal):
            type(self).decides += 1
            return Decision(legal[1], source="sim")

        def on_round_end(self, result): ...

    g = GuardedAdvisor(Adv(), _Given(), "adv@1", "exam.hif")
    g.on_round_start(1, {})
    ds = [g.decide(None, None, None, _legal()) for _ in range(3)]
    assert Adv.decides == 0 and all(d.plugin is None and d.source == "rule" for d in ds)
    assert any("on_round_start" in w for w in ds[0].warnings)
    assert RECORD.points["exam.hif"]["errors"] == 1
    g.on_round_start(2, {})  # 下一场重新开局：再试一次
    assert RECORD.points["exam.hif"]["errors"] == 2


def test_failed_round_start_recovers_next_round():
    from hiro.contracts import Decision
    from hiro.exec.exam import GuardedAdvisor

    class Adv:
        fail = True

        def on_round_start(self, round_no, context):
            if type(self).fail:
                raise RuntimeError("第一场炸了")

        def decide(self, state, memory, previews, legal):
            return Decision(legal[1], source="sim")

        def on_round_end(self, result): ...

    g = GuardedAdvisor(Adv(), _Given(), "adv@1", "exam.hif")
    g.on_round_start(1, {})
    assert g.decide(None, None, None, _legal()).plugin is None
    Adv.fail = False
    g.on_round_start(2, {})
    assert g.decide(None, None, None, _legal()).plugin == "adv@1"


def test_bridge_failed_start_not_read_again(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hiro.defaults.exam_bridge import ObservationExamStateSource

    BadStartSrc.reads = BadStartSrc.starts = 0
    plugins.register("observation_source", BadStartSrc, id="bad")
    _cfg(tmp_path, monkeypatch, {"sources": ["bad"]})

    class Inner:
        def begin(self, c): ...
        def read(self, f, m):
            return SimpleNamespace(hidden={}), "画面"

        def confidence(self):
            return "screen"

    b = ObservationExamStateSource(Inner())
    for _ in range(3):
        b.read(SimpleNamespace(wall_t=1.0), None)
    b2 = ObservationExamStateSource(Inner())  # 下一场考试换了新的转接实例：进程里已经知道它起不来
    b2.read(SimpleNamespace(wall_t=1.0), None)
    assert BadStartSrc.starts == 1 and BadStartSrc.reads == 0
    assert not RECORD.uses_hidden


# ---- 9  考试里被作废的步骤不计进「插件已定」


def test_voided_exam_step_not_counted_as_decided():
    from hiro.contracts import Decision
    from hiro.exec.exam import GuardedAdvisor, _executed, _not_executed

    class Adv:
        def decide(self, state, memory, previews, legal):
            return Decision(legal[1], source="sim")

    g = GuardedAdvisor(Adv(), _Given(), "adv@1", "exam.hif")
    legal = _legal()
    d = g.decide(None, None, None, legal)
    assert d.plugin == "adv@1"
    assert RECORD.points["exam.hif"]["decided"] == 0  # 只是定了，还没执行
    _not_executed(g, "skip", "决策帧已过时")  # 帧过时 / 画面对不上 / 牌没打出去 / 点了没变化：作废
    assert RECORD.points["exam.hif"]["decided"] == 0
    d = g.decide(None, None, None, legal)
    _executed(g, "skip")
    assert RECORD.points["exam.hif"]["decided"] == 1
    _executed(g, "skip")  # 同一个决定不重复计
    assert RECORD.points["exam.hif"]["decided"] == 1


def test_executed_hook_ignores_fallback_decisions():
    from hiro.exec.exam import GuardedAdvisor, _executed

    class Bad:
        def decide(self, *a):
            raise RuntimeError("炸了")

    g = GuardedAdvisor(Bad(), _Given(), "bad@1", "exam.hif")
    g.decide(None, None, None, _legal())
    _executed(g, "play:0")  # 这一步是原来的出招定的：不算插件
    assert RECORD.points["exam.hif"]["decided"] == 0 and RECORD.points["exam.hif"]["errors"] == 1


def test_executed_hook_on_plain_advisor_is_noop():
    from hiro.exec.exam import _executed

    _executed(object(), "play:0")  # 没有插件包装：什么也不做


# ---- 9（续）run_exam 里只在动作真的生效时才计「已定」（骨架 tests/test_exec_exam_skeleton.py 在没有 kaa 的环境整个跳过，这里自带一份替身）


class _Rec:
    def log(self, kind, **kw): ...


class _Memory:
    def __init__(self):
        self.turn, self.last_remaining, self.history, self.plan, self.blocked = 1, None, [], [], []

    def block(self, a):
        self.blocked.append(a)


class _Brain:
    profile = SimpleNamespace(idol_id="i_test")

    def new_memory(self, exam):
        return _Memory()

    def commit(self, state, memory, d): ...


class _State:
    drinks, hand = [], []

    def legal_actions(self):
        return ["play:0", "skip"]


class _Source:
    def begin(self, ctx): ...
    def read(self, frame, memory):
        return _State(), None

    def confidence(self) -> str:
        return "screen"

    def executed(self, action, result): ...
    def previews(self, state):
        return None

    def end(self, result): ...


class _PluginAdv:
    id = "adv"
    version = "1"
    contracts = "1"

    def on_round_start(self, n, ctx): ...
    def decide(self, state, memory, previews, legal):
        from hiro.contracts import Action
        from hiro.contracts import Decision as DC

        return DC(Action.play_card(0), "测试", "rule")

    def on_round_end(self, result): ...


@pytest.fixture
def exam_env(monkeypatch):
    import hiro.exec.exam as ex

    t = [0.0]
    loops = {"n": 0}

    class FakeLoop:
        def __init__(self, interval): ...
        def __iter__(self):
            return self

        def __next__(self):
            loops["n"] += 1
            if loops["n"] > 50:
                raise StopIteration

    frame = SimpleNamespace(img="img", t=0.0, remaining=5, hp=20, genki=10, cards=[], drinks=[], signature=("sig",))
    monkeypatch.setattr(ex, "Loop", FakeLoop)
    monkeypatch.setattr(ex, "device", SimpleNamespace(screenshot=lambda: "img", click=lambda *a: None))
    monkeypatch.setattr(ex, "_handle_dialogs", lambda img, slow=False: False)
    monkeypatch.setattr(ex, "_settle_fast", lambda img, **kw: frame)
    monkeypatch.setattr(ex, "_stable_frame", lambda **kw: frame)
    monkeypatch.setattr(ex, "_stale_before_tap", lambda f: None)
    monkeypatch.setattr(ex, "_execute", lambda d, f: [[100, 1010]])
    monkeypatch.setattr(ex, "_wait_change", lambda sig: True)
    monkeypatch.setattr(ex, "_play_missed", lambda f, c, observable=True: False)
    monkeypatch.setattr(ex.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(ex, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    return ex


def _run_one_step(ex, tmp_path, monkeypatch):
    plugins.register("exam_advisor", _PluginAdv, id="adv")
    _cfg(tmp_path, monkeypatch, {"exam": "adv"})
    done = iter([False, True])
    ex.run_exam(_Brain(), "r1", _Rec(), lambda img: next(done), source=_Source(), advisor=_Given())


def test_run_exam_counts_decided_when_action_took_effect(exam_env, tmp_path, monkeypatch):
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 1


def test_run_exam_does_not_count_step_that_had_no_effect(exam_env, tmp_path, monkeypatch):
    monkeypatch.setattr(exam_env, "_wait_change", lambda sig: False)  # 点了画面没变：作废
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 0


def test_run_exam_does_not_count_stale_frame_step(exam_env, tmp_path, monkeypatch):
    monkeypatch.setattr(exam_env, "_stale_before_tap", lambda f: "画面变了")  # 决策帧过时没点：作废
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 0


# ---- 5  只有数值来自存档级插件、手牌还是画面的：不能把点击改成「按卡 id 对位置」


class _SaveishSource(_Source):
    """可信度报 save，但手牌顺序是画面的（数据来源只给了体力 / 分数）。"""

    hand_from_plugin = False

    def read(self, frame, memory):
        st = _State()
        st.hand = [SimpleNamespace(card_id="p_card-X", upgrade=0, name="X")]  # 画面识别出来的牌
        return st, None

    def confidence(self) -> str:
        return "save"


def test_screen_hand_is_not_remapped_by_card_id_just_because_confidence_is_save(exam_env, monkeypatch, tmp_path):
    monkeypatch.setattr(exam_env, "_play_observable", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(exam_env, "_screen_slot", lambda frame, state, d: seen.append(d.slot) or None)
    done = iter([False, True])
    plugins.register("exam_advisor", _PluginAdv, id="adv")
    _cfg(tmp_path, monkeypatch, {"exam": "adv"})
    exam_env.run_exam(_Brain(), "r1", _Rec(), lambda img: next(done), source=_SaveishSource(), advisor=_Given())
    assert seen == []  # 没去按卡 id 找位置
    assert RECORD.points["exam.hif"]["decided"] == 1


def test_plugin_hand_is_still_remapped_by_card_id(exam_env, monkeypatch, tmp_path):
    class PluginHand(_SaveishSource):
        hand_from_plugin = True

    monkeypatch.setattr(exam_env, "_play_observable", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(exam_env, "_screen_slot", lambda frame, state, d: seen.append(d.slot) or 0)
    done = iter([False, True])
    plugins.register("exam_advisor", _PluginAdv, id="adv")
    _cfg(tmp_path, monkeypatch, {"exam": "adv"})
    exam_env.run_exam(_Brain(), "r1", _Rec(), lambda img: next(done), source=PluginHand(), advisor=_Given())
    assert seen == [0]


def test_source_without_flag_keeps_old_rule(exam_env, monkeypatch, tmp_path):
    """私有存档插件没有 hand_from_plugin：和以前一样，可信度 save 就按卡 id 对位置。"""

    class Private(_Source):  # 没有 hand_from_plugin 这个属性
        def confidence(self):
            return "save"

    src = Private()
    monkeypatch.setattr(exam_env, "_play_observable", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(exam_env, "_screen_slot", lambda frame, state, d: seen.append(d.slot) or 0)
    done = iter([False, True])
    plugins.register("exam_advisor", _PluginAdv, id="adv")
    _cfg(tmp_path, monkeypatch, {"exam": "adv"})
    exam_env.run_exam(_Brain(), "r1", _Rec(), lambda img: next(done), source=src, advisor=_Given())
    assert seen == [0]


# ---- 6  育成插件 begin 拿到难度 / 编成方案 / 攻略包，end 拿到真实结果


class CtxProduce:
    id = "ctx"
    version = "1"
    contracts = "1"
    pages = frozenset({"STUDY"})
    ctx: ClassVar[dict] = {}
    ended: ClassVar[list] = []

    def begin(self, context):
        type(self).ctx = context

    def decide(self, page, state, *, n_options, kw):
        return None

    def end(self, result):
        type(self).ended.append(result)


def _begin_context(tmp_path, monkeypatch, mode="produce-007"):
    plugins.register("produce_advisor", CtxProduce, id="ctx")
    _cfg(tmp_path, monkeypatch, {"produce": {"STUDY": "ctx"}})
    CtxProduce.ctx, CtxProduce.ended = {}, []
    s = _session(tmp_path)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"mode": mode}, ready=True)  # 选行动页读到培育 id
    s.state.produce = {**(s.state.produce or {}), "mode": mode}
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    s.state.produce = {**(s.state.produce or {}), "mode": mode}
    ProduceDeciders(None, main_attr="Da").decide("STUDY", s.state, n_options=3)
    return s, CtxProduce.ctx


def test_begin_context_has_difficulty_plan_and_guides(tmp_path, monkeypatch):
    from hiro import formation_env

    monkeypatch.setattr(
        formation_env, "ACTIVE_PLAN", {"idol": {"idol_card_id": "i_card-x"}, "main_attr": "Da", "memory": {"set": 3}}
    )
    gdir = tmp_path / "guides"
    gdir.mkdir()
    (gdir / "manifest.json").write_text(
        json.dumps({"schema": 1, "packs": {"hif": {"name": "hif-20261126-1"}, "nia": {"name": "nia-9"}}})
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(gdir))
    _, ctx = _begin_context(tmp_path, monkeypatch)
    assert ctx["mode"] == "produce-007" and ctx["difficulty"] == "selection" and ctx["family"] == "hif"
    assert ctx["formation_plan"] == {"idol": {"idol_card_id": "i_card-x"}, "main_attr": "Da", "memory": {"set": 3}}
    assert ctx["guides"]["enabled"] is True and ctx["guides"]["dir"] == str(gdir)
    assert (
        ctx["guides"]["packs"] == {"hif": "hif-20261126-1", "nia": "nia-9"}
        and ctx["guides"]["pack"] == "hif-20261126-1"
    )
    ctx["formation_plan"]["main_attr"] = "Vo"  # 插件改它不能影响全局的方案
    assert (formation_env.ACTIVE_PLAN or {})["main_attr"] == "Da"


def test_begin_context_without_plan_or_guides(tmp_path, monkeypatch):
    from hiro import formation_env

    monkeypatch.setattr(formation_env, "ACTIVE_PLAN", None)
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    _, ctx = _begin_context(tmp_path, monkeypatch, mode="produce-002")
    assert ctx["difficulty"] == "pro" and ctx["family"] == "hajime" and ctx["formation_plan"] is None
    assert ctx["guides"]["packs"] == {} and ctx["guides"]["pack"] is None


def test_begin_context_unknown_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    plugins.register("produce_advisor", CtxProduce, id="ctx")
    _cfg(tmp_path, monkeypatch, {"produce": {"STUDY": "ctx"}})
    s = _session(tmp_path)
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    ProduceDeciders(None).decide("STUDY", s.state, n_options=3)
    assert (
        CtxProduce.ctx["difficulty"] is None
        and CtxProduce.ctx["family"] is None
        and CtxProduce.ctx["guides"]["pack"] is None
    )


def test_end_gets_real_results(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    s, _ = _begin_context(tmp_path, monkeypatch)
    # 一场选拔：结算页读到名次和分数（多帧，字段陆续读全）；再进下一页
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(scene="HIF_EXAM_RESULT", img=IMG, fields={"score": 123456}, ready=True)
    s.observe(scene="HIF_EXAM_RESULT", img=IMG, fields={"rank": 2, "passed": True, "exam_no": 1}, ready=True)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    RECORD.at_exit()
    assert len(CtxProduce.ended) == 1
    r = CtxProduce.ended[0]
    assert r is not None and r["trace_dir"] == str(s.trace.dir) and r["final_page"] == "ACTION_SELECT"
    assert [(x["page"], x.get("exam_no"), x.get("rank"), x.get("score"), x.get("passed")) for x in r["results"]] == [
        ("HIF_EXAM_RESULT", 1, 2, 123456, True)
    ]


def test_end_result_has_all_exams_and_finals(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    s, _ = _begin_context(tmp_path, monkeypatch)
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(
        scene="HIF_EXAM_RESULT", img=IMG, fields={"score": 10, "rank": 3, "passed": False, "exam_no": 1}, ready=True
    )
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(
        scene="HIF_EXAM_RESULT", img=IMG, fields={"score": 20, "rank": 1, "passed": True, "exam_no": 2}, ready=True
    )
    RECORD.at_exit()
    r = CtxProduce.ended[0]
    assert [(x["exam_no"], x["rank"], x["passed"]) for x in r["results"]] == [(1, 3, False), (2, 1, True)]
    assert r["last_result"]["exam_no"] == 2


def test_end_result_empty_when_nothing_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    _begin_context(tmp_path, monkeypatch)
    RECORD.at_exit()
    r = CtxProduce.ended[0]
    assert r["results"] == [] and r["last_result"] is None


def test_result_bookkeeping_restarts_with_new_trace_dir(tmp_path):
    RECORD.bind(tmp_path / "a")
    RECORD.note_page("HIF_EXAM_RESULT", {"score": 1, "exam_no": 1})
    RECORD.bind(tmp_path / "b")
    assert RECORD.end_result()["results"] == []


# ---- 4  没装插件时，记录和报告里不多出插件才有的字段


def test_records_without_plugins_have_no_plugin_fields(tmp_path):
    from hiro.contracts import Action, ActionKind, Decision

    o = _obs(1, "ACTION_SELECT", {"stamina": 1}).to_dict()
    assert not {"by", "trust", "hidden"} & set(o)
    g = reduce(GameState(), _obs(1, "ACTION_SELECT", {"stamina": 1})).to_dict()
    assert "hidden" not in g and all("by" not in v for v in g["sources"].values())
    d = Decision(Action(ActionKind.WAIT, {}), reason="x", source="rule").to_dict()
    assert "plugin" not in d
    for obj in (_obs(1, "ACTION_SELECT"), GameState(), Decision(Action(ActionKind.WAIT, {}))):
        assert not obj.problems()  # 少了这些字段照样符合契约


def test_trace_steps_without_plugins_have_no_by_null(tmp_path):
    s = _session(tmp_path)
    o = s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.record(o)
    text = (s.trace.dir / "steps.jsonl").read_text(encoding="utf-8")
    assert '"by"' not in text and '"hidden"' not in text and '"trust"' not in text and '"plugin"' not in text


def test_plugin_fields_still_written_when_present():
    from hiro.contracts import Action, ActionKind, Decision

    o = Observation(
        1, 1.0, PageObs("HIF_EXAM", "produce"), source="plugin", trust="save", by="hook@1", hidden={"seed": 1}
    )
    d = o.to_dict()
    assert (d["trust"], d["by"], d["hidden"]) == ("save", "hook@1", {"seed": 1})
    assert Observation.from_dict(d) == o
    dd = Decision(Action(ActionKind.WAIT, {}), source="plugin", plugin="u@1").to_dict()
    assert dd["plugin"] == "u@1"
    from hiro.contracts.game_state import Source

    g = GameState(hidden={"seed": 1}, sources={"produce.x": Source("save", 1, by="hook@1")}).to_dict()
    assert g["hidden"] == {"seed": 1} and g["sources"]["produce.x"]["by"] == "hook@1"


def test_report_without_plugins_has_no_plugin_keys(tmp_path):
    from hiro.loop.report import markdown, report

    s = _session(tmp_path)
    o = s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.record(o)
    r = report(s.trace.dir)
    assert "plugins" not in r and "uses_hidden" not in r
    assert "插件" not in markdown(r) and "隐藏" not in markdown(r)


def test_report_with_plugins_json_has_plugin_keys(tmp_path):
    from hiro.loop.report import report

    s = _session(tmp_path)
    o = s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.record(o)
    (s.trace.dir / "plugins.json").write_text(json.dumps({"points": {}, "uses_hidden": True, "warnings": []}))
    r = report(s.trace.dir)
    assert r["uses_hidden"] is True and r["plugins"]["uses_hidden"] is True


# ---- 7  只能按 id 取用的两种插件：没有环境变量、没有不带 id 的入口点 / register


def test_by_id_kinds_have_no_env_var_and_no_plain_registration(monkeypatch):
    for kind in ("observation_source", "produce_advisor"):
        _proto, env, default = plugins.KINDS[kind]
        assert env is None and default is None
        with pytest.raises(ValueError, match="id"):
            plugins.register(kind, CtxProduce)  # 不带 id 的注册用不上：直接报错，别静默没效果
        assert plugins.get(kind) is None
        assert plugins.source(kind) == "strategy.json"
    # 以前写的环境变量不再有任何作用
    monkeypatch.setenv("HIRO_PRODUCE_ADVISOR", "tests.test_plugin_minors:CtxProduce")
    assert plugins.get("produce_advisor") is None


def test_plain_entry_point_name_ignored_for_by_id_kinds(monkeypatch):
    from importlib.metadata import EntryPoint

    eps = [EntryPoint("produce_advisor", "tests.test_plugin_minors:CtxProduce", plugins.GROUP)]
    monkeypatch.setattr(plugins, "_eps", lambda: eps)
    assert plugins.get("produce_advisor") is None
    assert plugins.available("produce_advisor") == []  # 不带 id 的入口点也不会被列成已装


def test_old_kinds_keep_env_and_entry_point():
    assert plugins.KINDS["exam_advisor"][1] == "HIRO_EXAM_ADVISOR"
    assert plugins.KINDS["asset_source"][1] == "HIRO_ASSET_SOURCE"
    assert plugins.KINDS["exam_state_source"][1] == "HIRO_EXAM_STATE_SOURCE"


# ---- 8  示例数据来源读的是 JSON 文件，不叫 replay


def test_example_source_named_after_what_it_does():
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parents[1] / "examples" / "plugins" / "hiro_example_plugins"
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "observation_source.example_json" in text and "example_replay" not in text
    sys.path.insert(0, str(root / "src"))
    try:
        sys.modules.pop("hiro_example_plugins.source", None)
        import importlib

        JsonFileSource = importlib.import_module("hiro_example_plugins.source").JsonFileSource  # noqa: N806
        assert JsonFileSource.id == "example_json"
        assert "HIRO_EXAMPLE_JSON" in (root / "src" / "hiro_example_plugins" / "source.py").read_text(encoding="utf-8")
    finally:
        sys.path.remove(str(root / "src"))
        for m in [m for m in sys.modules if m.startswith("hiro_example_plugins")]:
            del sys.modules[m]


# ---- PR #102 CodeRabbit 三条


class ResultSrc:
    """数据来源插件：结算页上补画面漏读的字段（也可以覆盖，高档位时）。"""

    id = "res"
    version = "1"
    contracts = "1"
    trust = "screen"
    pages = frozenset({"HIF_EXAM_RESULT"})
    reply: ClassVar[dict] = {}

    def start(self, context): ...
    def read(self, page, frame_t, state):
        return SourceRead(t=frame_t, fields=dict(type(self).reply))

    def stop(self): ...


class SaveResultSrc(ResultSrc):
    id = "resave"
    trust = "save"


def test_plugin_filled_result_fields_reach_end_result(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    plugins.register("observation_source", ResultSrc, id="res")
    ResultSrc.reply = {"rank": 2, "passed": True}
    plugins.register("produce_advisor", CtxProduce, id="ctx")
    _cfg(tmp_path, monkeypatch, {"sources": ["res"], "produce": {"STUDY": "ctx"}})
    CtxProduce.ended = []
    s = _session(tmp_path)
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    ProduceDeciders(None).decide("STUDY", s.state, n_options=3)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(scene="HIF_EXAM_RESULT", img=IMG, fields={"score": 99, "exam_no": 1}, ready=True)  # 画面漏读了名次
    assert s.state.page.name == "HIF_EXAM_RESULT" and s.state.page_fields["rank"] == 2  # 插件的读数进了状态
    RECORD.at_exit()
    r = CtxProduce.ended[0]
    assert [(x["exam_no"], x["score"], x["rank"], x["passed"]) for x in r["results"]] == [(1, 99, 2, True)]


def test_plugin_result_when_screen_read_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    plugins.register("observation_source", ResultSrc, id="res")
    ResultSrc.reply = {"score": 5, "rank": 1}
    plugins.register("produce_advisor", CtxProduce, id="ctx")
    _cfg(tmp_path, monkeypatch, {"sources": ["res"], "produce": {"STUDY": "ctx"}})
    CtxProduce.ended = []
    s = _session(tmp_path)
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    ProduceDeciders(None).decide("STUDY", s.state, n_options=3)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(scene="HIF_EXAM_RESULT", img=IMG, fields={}, ready=True)
    RECORD.at_exit()
    assert [(x["score"], x["rank"]) for x in CtxProduce.ended[0]["results"]] == [(5, 1)]


def test_higher_trust_plugin_result_overrides_screen_in_end_result(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "nothing"))
    plugins.register("observation_source", SaveResultSrc, id="resave")
    SaveResultSrc.reply = {"score": 100}
    plugins.register("produce_advisor", CtxProduce, id="ctx")
    _cfg(tmp_path, monkeypatch, {"sources": ["resave"], "produce": {"STUDY": "ctx"}})
    CtxProduce.ended = []
    s = _session(tmp_path)
    s.observe(scene="STUDY", img=IMG, fields={"complete": True}, ready=True)
    ProduceDeciders(None).decide("STUDY", s.state, n_options=3)
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 30}, ready=True)
    s.observe(scene="HIF_EXAM", img=IMG, fields={"hp": 30}, ready=True)
    s.observe(scene="HIF_EXAM_RESULT", img=IMG, fields={"score": 90, "exam_no": 1}, ready=True)
    RECORD.at_exit()
    assert [(x["exam_no"], x["score"]) for x in CtxProduce.ended[0]["results"]] == [(1, 100)]  # 和状态里采用的一致


def test_select_retap_is_not_counted_as_decided(exam_env, tmp_path, monkeypatch):
    def wait(sig):
        exam_env.WAIT_INFO["select"] = True  # 只是处理了停在选中态的 SELECT（补点），局面还没变
        return True

    monkeypatch.setattr(exam_env, "_wait_change", wait)
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 0


def test_unplayable_step_is_not_counted_as_decided(exam_env, tmp_path, monkeypatch):
    def wait(sig):
        exam_env.UNPLAYABLE["hit"] = True  # 再点超限、取消选中：本回合禁用这个动作
        exam_env.UNPLAYABLE["why"] = "使用条件不满足"
        return True

    monkeypatch.setattr(exam_env, "_wait_change", wait)
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 0


def test_real_change_after_wait_still_counts(exam_env, tmp_path, monkeypatch):
    def wait(sig):
        exam_env.WAIT_INFO["select"] = False
        return True

    monkeypatch.setattr(exam_env, "_wait_change", wait)
    _run_one_step(exam_env, tmp_path, monkeypatch)
    assert RECORD.points["exam.hif"]["decided"] == 1


def test_select_retap_sets_flag_and_wait_change_resets_it(monkeypatch):
    import hiro.exec.exam as ex

    ex.WAIT_INFO["select"] = False
    img = object()
    sel = SimpleNamespace(rect=SimpleNamespace(center=SimpleNamespace(x=100, y=300)))
    monkeypatch.setattr(ex, "sleep", lambda s: None)
    monkeypatch.setattr(ex, "device", SimpleNamespace(screenshot=lambda: img, click=lambda *a: None))
    monkeypatch.setattr(ex, "_selects", lambda im: [sel])
    assert ex._select_retap(cast(Any, sel)) is True
    assert ex.WAIT_INFO["select"] is True
    ex._RETAP.update(pos=None, n=0, t0=0.0)

    class F:
        remaining = 5
        signature = ("same",)

    monkeypatch.setattr(ex, "Frame", lambda im: F())
    monkeypatch.setattr(ex, "_handle_dialogs", lambda im, **kw: False)
    t = [0.0]
    monkeypatch.setattr(ex.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(ex, "sleep", lambda s: t.__setitem__(0, t[0] + 10))
    assert ex._wait_change(("same",)) is False
    assert ex.WAIT_INFO["select"] is False  # 每次等之前清零


def test_bridge_inherits_inner_hand_order_flag(tmp_path, monkeypatch):
    from hiro.defaults.exam_bridge import ObservationExamStateSource

    class Hp(HiddenSrc):
        trust = "save"

        def read(self, page, frame_t, state):
            return SourceRead(t=frame_t, fields={"hp": 7})

    plugins.register("observation_source", Hp, id="hook")
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})

    class InnerSave:  # 私有存档插件：手牌按存档顺序，没有 hand_from_plugin 属性
        def begin(self, c): ...
        def read(self, f, m):
            return SimpleNamespace(hp=1, hand=[], hidden={}), "存档"

        def confidence(self) -> str:
            return "save"

    class InnerFlagged(InnerSave):
        hand_from_plugin = False

    class InnerScreen(InnerSave):
        def confidence(self) -> str:
            return "screen"

    for inner, want in ((InnerSave(), True), (InnerFlagged(), False), (InnerScreen(), False)):
        b = ObservationExamStateSource(inner)
        b.read(SimpleNamespace(wall_t=time.time()), None)
        assert b.hand_from_plugin is want, type(inner).__name__
    b = ObservationExamStateSource(InnerSave())
    assert b.hand_from_plugin is False  # 还没 read 过
