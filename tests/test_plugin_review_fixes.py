"""插件架构终审发现的问题（2026-11-25），每条一个复现用例。"""

import json
import threading
import time
from types import SimpleNamespace
from typing import ClassVar

from hiro import plugins, strategy
from hiro.brain import plugin_gate
from hiro.brain.deciders import ProduceDeciders
from hiro.contracts import Action, ActionKind, Decision, GameState, Observation, PageObs, TraceWriter
from hiro.loop import sources
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
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    yield
    for m in (plugins, strategy, sources, plugin_gate, RECORD):
        m.reset()


def _cfg(tmp_path, monkeypatch, d):
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps({"version": 1, **d}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    strategy.reset()


class Src:
    id = "hook"
    version = "1"
    contracts = "1"
    trust = "save"
    pages = None
    reply = None
    seen_t: ClassVar[list] = []

    def start(self, context): ...
    def read(self, page, frame_t, state):
        type(self).seen_t.append(frame_t)
        return type(self).reply(page, frame_t) if type(self).reply else None

    def stop(self): ...


# ---- 1（Critical）插件纠正获得弹窗时不能多发事件


def _acquire_events(tmp_path, with_plugin):
    s = Session(
        TraceWriter(root=tmp_path / ("p" if with_plugin else "n"), name="t"), shoot=lambda: IMG, sleep=lambda x: None
    )
    f = {"kind": "card", "card_id": "p_card-00-act-1_002", "upgrade": 0, "agree": True}
    s.observe(scene="ACTION_SELECT", img=IMG, fields={"stamina": 1}, ready=True)
    for _ in range(2):
        s.observe(scene="ACQUIRE", img=IMG, fields=f, ready=True)
    return sum(1 for e in s.state.ledger if e.kind == "CARD_ACQUIRE")


def test_plugin_correction_does_not_duplicate_acquire(tmp_path, monkeypatch):
    plain = _acquire_events(tmp_path, False)
    plugins.register("observation_source", Src, id="hook")
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Src.reply = lambda page, t: (
        SourceRead(t=t, fields={"kind": "card", "card_id": "p_card-00-act-1_001", "upgrade": 0, "agree": True})
        if page == "ACQUIRE"
        else None
    )
    assert _acquire_events(tmp_path, True) == plain


# ---- 2 trust 不高于 screen 的插件观测要过互验；save 档和存档一样不互验


def test_screen_trust_plugin_obs_is_checked(monkeypatch):
    from hiro.loop import events as ev

    calls = []
    monkeypatch.setattr(ev, "check", lambda state, obs, preds: calls.append(obs.trust) or [])

    def o(trust):
        return Observation(
            1, 1.0, PageObs("ACTION_SELECT", "produce"), fields={"stamina": 3}, source="plugin", trust=trust, by="x@1"
        )

    ev.reduce_checked(GameState(), o("screen"), [])
    ev.reduce_checked(GameState(), o("save"), [])
    assert calls == ["screen"]


# ---- 3 格式不对的插件决定交回内置，不抛异常


class SP:
    id = "sp"
    version = "1"
    contracts = "1"
    pages = frozenset({"STUDY"})
    reply = None

    def begin(self, context): ...
    def decide(self, page, state, *, n_options, kw):
        return type(self).reply(state) if callable(type(self).reply) else type(self).reply

    def end(self, result): ...


@pytest.mark.parametrize(
    "reply",
    [
        Decision(Action(ActionKind.EVENT_SELECT, {"select": "first"}), source="rule"),
        SimpleNamespace(action="not an action", source="rule"),
        Decision(Action(ActionKind.EVENT_SELECT, {}), source="rule"),
    ],
)
def test_malformed_decision_falls_back(tmp_path, monkeypatch, reply):
    plugins.register("produce_advisor", SP, id="sp")
    _cfg(tmp_path, monkeypatch, {"produce": {"STUDY": "sp"}})
    SP.reply = reply
    d = ProduceDeciders(None).decide("STUDY", GameState(), n_options=3)
    assert d.plugin is None


# ---- 4 strategy.json 写错类型只警告


@pytest.mark.parametrize(
    "bad",
    [
        {"produce": ["x"]},
        {"timeouts": []},
        {"sources": 5},
        {"exam": 3},
        {"produce": {"STUDY": 1}},
        ["not", "a", "dict"],
    ],
)
def test_bad_types_only_warn(tmp_path, bad):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(bad))
    s = strategy.load(p)
    assert s.warnings
    assert s.produce_for("STUDY") is None and s.timeout_for("x") == strategy.DEFAULT_TIMEOUT_S


# ---- 5 exam.<模式>=builtin 能盖掉全局 exam


def test_exam_mode_builtin_overrides_global():
    s = strategy.from_dict({"version": 1, "exam": "g", "exam.tower": "builtin"})
    assert s.exam_for("tower") is None and s.exam_for("hif") == "g"
    m = strategy.merge({"version": 1, "exam": "g"}, strategy.parse_overrides("exam.tower=builtin", None))
    assert strategy.from_dict(m).exam_for("tower") is None


# ---- 6 插件拿到的是状态副本；上一次没返回时不再起新线程


def test_plugin_gets_state_copy(tmp_path, monkeypatch):
    plugins.register("produce_advisor", SP, id="sp")
    _cfg(tmp_path, monkeypatch, {"produce": {"STUDY": "sp"}})

    def mutate(state):
        state.page_fields["poisoned"] = True
        return None

    SP.reply = mutate
    st = GameState()
    ProduceDeciders(None).decide("STUDY", st, n_options=3)
    assert "poisoned" not in st.page_fields


def test_no_new_thread_while_previous_call_running():
    started = []
    gate = threading.Event()

    def slow():
        started.append(1)
        gate.wait(2)

    with pytest.raises(plugin_gate.PluginTimeout):
        plugin_gate.call_with_timeout(slow, 0.05, key="k")
    with pytest.raises(plugin_gate.PluginTimeout, match="还没返回"):
        plugin_gate.call_with_timeout(slow, 0.05, key="k")
    assert len(started) == 1
    gate.set()


# ---- 7 数据来源的默认超时很短


def test_source_default_timeout_short():
    s = strategy.from_dict({"version": 1})
    assert s.source_timeout_for("hook") <= 1.0
    assert strategy.from_dict({"version": 1, "timeouts": {"hook": 0.2}}).source_timeout_for("hook") == 0.2


# ---- 8 frame_t 是截图时刻，不是识别做完的时刻


def test_frame_t_is_capture_time(tmp_path, monkeypatch):
    plugins.register("observation_source", Src, id="hook")
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Src.seen_t, Src.reply = [], None
    marks = {}

    def slow_extract(scene, img):
        marks["extract_start"] = time.time()
        time.sleep(0.3)
        return {"stamina": 1}, 1.0

    s = Session(
        TraceWriter(root=tmp_path / "tr", name="t"),
        shoot=lambda: IMG,
        classify=lambda: ("ACTION_SELECT", "produce"),
        extract=slow_extract,
        sleep=lambda x: None,
    )
    s.observe()
    assert Src.seen_t and Src.seen_t[0] <= marks["extract_start"]


# ---- 9 同一个 trace 目录续跑：plugins.json 接着累计，uses_hidden 不丢


def test_rebind_same_dir_keeps_previous_segment(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    RECORD.bind(tmp_path)
    RECORD.hidden({"seed"}, "hook@1")
    RECORD.decided("source.hook", "hook@1")
    RECORD.flush()
    RECORD.reset()
    RECORD.trace_dir = None  # 新进程
    RECORD.bind(tmp_path)
    RECORD.decided("source.hook", "hook@1")
    RECORD.flush()
    d = json.loads((tmp_path / "plugins.json").read_text())
    assert d["uses_hidden"] is True and d["points"]["source.hook"]["decided"] == 2


# ---- 10 考试出招插件出错 / 不合法时交给原来的出招，计入出错


def test_exam_plugin_errors_fall_back():
    from hiro.exec.exam import GuardedAdvisor

    class Bad:
        def decide(self, *a):
            raise RuntimeError("出招插件炸了")

    class Given:
        def decide(self, state, memory, previews, legal):
            return Decision(legal[0], source="rule")

    legal = [Action(ActionKind.PLAY_CARD, {"slot": 0}), Action(ActionKind.SKIP, {})]
    g = GuardedAdvisor(Bad(), Given(), "bad@1", "exam.hif")
    d = g.decide(None, None, None, legal)
    assert d.plugin is None and d.action.kind is ActionKind.PLAY_CARD
    assert RECORD.points["exam.hif"]["errors"] == 1

    class Illegal:
        def decide(self, *a):
            return Decision(Action(ActionKind.PLAY_CARD, {"slot": 9}), source="rule")

    d = GuardedAdvisor(Illegal(), Given(), "ill@1", "exam.hif").decide(None, None, None, legal)
    assert d.plugin is None and RECORD.points["exam.hif"]["errors"] == 2

    class Good:
        def decide(self, state, memory, previews, legal):
            return Decision(legal[1], source="sim")

    d = GuardedAdvisor(Good(), Given(), "good@1", "exam.hif").decide(None, None, None, legal)
    assert d.plugin == "good@1" and RECORD.points["exam.hif"]["decided"] == 1


# ---- 11 转接层不把观测形状的列表字段塞进 BattleState；冲突说明进 trace


def test_bridge_skips_list_fields_and_notes_to_session(tmp_path, monkeypatch):
    from hiro.defaults.exam_bridge import ObservationExamStateSource

    plugins.register("observation_source", Src, id="hook")
    _cfg(tmp_path, monkeypatch, {"sources": ["hook"]})
    Src.reply = lambda page, t: SourceRead(t=t, fields={"hand": [{"name": "x"}], "hp": 9})
    notes = []
    st = SimpleNamespace(hp=3, hand=["CardView"], hidden={})

    class Inner:
        def begin(self, c): ...
        def read(self, f, m):
            return st, ""

        def confidence(self):
            return "screen"

    b = ObservationExamStateSource(Inner())
    b.begin({"session": SimpleNamespace(note=notes.append)})
    got, _ = b.read(SimpleNamespace(wall_t=time.time()), None)
    assert got.hand == ["CardView"] and got.hp == 9
    assert any("hand" in n for n in notes) and any("hp" in n for n in notes)


# ---- 12 开局把配置警告写进任务事件；任务的成绩摘要标出用了隐藏信息；复盘页带标记


def test_job_start_emits_config_warnings(tmp_path, monkeypatch):
    from hiro.cli import jobs

    home = tmp_path / "home"
    home.mkdir()
    (home / "strategy.json").write_text(json.dumps({"version": 1, "produce": {"NOPE": "x", "STUDY": "ghost"}}))
    j = jobs.Job(home, jobs.KINDS["selftest"], {"plugins": "garbage"})
    j.strategy_warnings()
    texts = [json.loads(x)["text"] for x in (j.dir / "events.jsonl").read_text().splitlines()]
    assert any("NOPE" in t for t in texts) and any("garbage" in t for t in texts) and any("ghost" in t for t in texts)


def test_hidden_marker_helpers(tmp_path):
    from hiro.cli import report as cli_report

    seg = tmp_path / "job1"
    seg.mkdir()
    (seg / "steps.jsonl").write_text("")
    assert cli_report.uses_hidden(tmp_path, "job1") is False
    (seg / "plugins.json").write_text(json.dumps({"uses_hidden": True}))
    assert cli_report.uses_hidden(tmp_path, "job1") is True
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "tools" / "viewer"))
    import build_exam_view

    assert build_exam_view.build("job1", tmp_path)["uses_hidden"] is True
