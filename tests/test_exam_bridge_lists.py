"""考试转接层全覆盖：插件给的手牌 / 饮料（卡 id 形式或完整 CardView）转成 BattleState，点击按卡 id 对位（2026-11-25 插件架构补充）。"""

import json
import time
from types import SimpleNamespace

from hiro import plugins, strategy
from hiro.brain.cards import CardView
from hiro.brain.state import BattleState, ExamMemory
from hiro.defaults.exam_bridge import ObservationExamStateSource
from hiro.loop import sources
from hiro.plugin_record import RECORD
from hiro.plugins import SourceRead

import pytest


def _master_ok():
    try:
        from hiro import master

        return bool(master.card("p_card-00-act-0_001"))
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _master_ok(), reason="没有主数据（HIRO_KAA_GAME_DATA）")

A, B, C = "p_card-00-act-0_001", "p_card-00-act-0_002", "p_card-00-acc-0_002"  # アピールの基本 / ポーズの基本 / 眠気
DRINK = "pdrink_00-1-001"  # 初星水


class Hook:
    id = "hook"
    version = "1"
    contracts = "1"
    trust = "save"
    pages = frozenset({"HIF_EXAM"})
    reply = None

    def start(self, context): ...
    def read(self, page, frame_t, state):
        return SourceRead(t=frame_t, fields=type(self).reply) if type(self).reply is not None else None

    def stop(self): ...


class ScreenHook(Hook):
    id = "shook"
    trust = "screen"


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    for m in (plugins, strategy, sources, RECORD):
        m.reset()
    Hook.reply = ScreenHook.reply = None
    plugins.register("observation_source", Hook, id="hook")
    plugins.register("observation_source", ScreenHook, id="shook")
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "sources": ["hook"]}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    yield
    for m in (plugins, strategy, sources, RECORD):
        m.reset()


def _use(tmp_path, ids):
    (tmp_path / "s.json").write_text(json.dumps({"version": 1, "sources": ids}))
    strategy.reset()


class Inner:
    def __init__(self, st):
        self.st = st

    def begin(self, c): ...
    def read(self, f, m):
        return self.st, "画面"

    def confidence(self):
        return "screen"


def _screen_state(hand=None):
    mem = ExamMemory("r1")
    return mem, BattleState.build(
        mem, None, remaining_turns=5, hp=20, genki=3, score=100, hand=hand or [], drinks=[], buffs={}
    )


def _read(bridge, mem):
    return bridge.read(SimpleNamespace(wall_t=time.time()), mem)


def test_hand_from_card_ids_becomes_cardviews():
    mem, st = _screen_state()
    Hook.reply = {
        "hand": [{"card_id": A, "upgrade": 0, "available": True}, {"card_id": B, "upgrade": 1, "available": False}]
    }
    b = ObservationExamStateSource(Inner(st))
    got, _ = _read(b, mem)
    assert [c.card_id for c in got.hand] == [A, B]
    assert [c.slot for c in got.hand] == [0, 1]
    from hiro.brain.cards import CardView as CV  # 用当前模块里的类：别的测试可能重新加载过 hiro.brain

    assert all(isinstance(c, CV) and c.name and c.name != "（未识别）" for c in got.hand)
    assert got.hand[1].upgrade == 1 and got.hand[1].available is False
    assert b.confidence() == "save"  # 手牌来自插件：点击按卡 id 对到屏幕位置


def test_full_cardview_dicts_accepted():
    mem, st = _screen_state()
    from hiro import master
    from hiro.brain.cards import card_view

    full = card_view(0, master.card(A), available=True).to_dict()
    Hook.reply = {"hand": [full]}
    got, _ = _read(ObservationExamStateSource(Inner(st)), mem)
    assert got.hand[0].card_id == A and got.hand[0].name == full["name"]


def test_drinks_from_drink_ids():
    mem, st = _screen_state()
    Hook.reply = {"drinks": [{"drink_id": DRINK}]}
    got, _ = _read(ObservationExamStateSource(Inner(st)), mem)
    assert [d.drink_id for d in got.drinks] == [DRINK] and got.drinks[0].name


def test_unknown_entries_dropped_with_note():
    mem, st = _screen_state()
    notes = []
    Hook.reply = {"hand": [{"card_id": A}, {"name": "没有 id 的卡"}, {"card_id": "p_card-not-exist"}]}
    b = ObservationExamStateSource(Inner(st))
    b.begin({"session": SimpleNamespace(note=notes.append)})
    got, _ = _read(b, mem)
    assert [c.card_id for c in got.hand] == [A, None, None]  # 认不出的占位，不丢格子（位置要和屏幕对得上）
    assert any("hand" in n for n in notes)


def test_screen_tier_keeps_screen_hand_fills_empty(tmp_path):
    _use(tmp_path, ["shook"])
    from hiro import master
    from hiro.brain.cards import card_view

    mem, st = _screen_state(hand=[card_view(0, master.card(C), available=True)])
    ScreenHook.reply = {"hand": [{"card_id": A}]}
    b = ObservationExamStateSource(Inner(st))
    got, _ = _read(b, mem)
    assert [c.card_id for c in got.hand] == [C] and b.confidence() == "screen"  # 同档：画面读到了就用画面
    mem2, st2 = _screen_state(hand=[])
    b2 = ObservationExamStateSource(Inner(st2))
    got2, _ = _read(b2, mem2)
    assert [c.card_id for c in got2.hand] == [A] and b2.confidence() == "save"  # 画面没读到：用插件的，按卡 id 对位


def test_builds_when_screen_gives_nothing():
    mem = ExamMemory("r1")
    Hook.reply = {
        "remaining_turns": 4,
        "hp": 18,
        "genki": 2,
        "score": 300,
        "hand": [{"card_id": A}],
        "pitem_charges": 1,
    }

    class Overlay(Inner):
        def read(self, f, m):
            return None, "hand_overlay"

    b = ObservationExamStateSource(Overlay(None))
    b.begin({"profile": None})
    got, _ = _read(b, mem)
    from hiro.brain.state import BattleState as BS  # 同上：取当前模块里的类

    assert isinstance(got, BS)
    assert got.remaining_turns == 4 and got.hp == 18 and got.score == 300 and got.hand[0].card_id == A
    assert mem.item_charges_left == 1  # 和 build 一样更新记忆


def test_scalar_memory_hooks():
    mem, st = _screen_state()
    Hook.reply = {"pitem_charges": 2, "hp": 15}
    got, _ = _read(ObservationExamStateSource(Inner(st)), mem)
    assert got.hp == 15 and mem.item_charges_left == 2


def test_save_numbers_alone_do_not_claim_the_hand(monkeypatch):
    """只有体力 / 分数来自存档级插件：可信度报 save，但手牌还是画面的（run_exam 不按卡 id 对位置）。"""
    monkeypatch.setattr(Hook, "reply", {"hp": 15})
    mem, st = _screen_state(hand=[])
    b = ObservationExamStateSource(Inner(st))
    got, _ = _read(b, mem)
    assert got.hp == 15 and b.confidence() == "save"
    assert b.hand_from_plugin is False and b.lists_from_plugin is False


def test_plugin_hand_marks_hand_from_plugin(monkeypatch):
    monkeypatch.setattr(Hook, "reply", {"hand": [{"card_id": A, "upgrade": 0, "available": True, "slot": 0}]})
    mem, st = _screen_state(hand=[])
    b = ObservationExamStateSource(Inner(st))
    _read(b, mem)
    assert b.hand_from_plugin is True and b.confidence() == "save"
    monkeypatch.setattr(Hook, "reply", {"hp": 3})
    _read(b, mem)  # 下一帧插件没给手牌：标记跟着这一帧走
    assert b.hand_from_plugin is False


def test_plugin_drinks_only_do_not_claim_the_hand(monkeypatch):
    monkeypatch.setattr(Hook, "reply", {"drinks": [{"drink_id": DRINK, "slot": 0}]})
    mem, st = _screen_state(hand=[])
    b = ObservationExamStateSource(Inner(st))
    _read(b, mem)
    assert b.lists_from_plugin is True and b.hand_from_plugin is False


def test_screen_slot_maps_plugin_hand_by_card_id():
    """插件手牌顺序和屏幕不同时，按卡 id 找到屏幕位置（run_exam 在 confidence=save 时走这条）。"""
    from hiro import master
    from hiro.brain.cards import card_view
    from hiro.exec.exam import _screen_slot

    state = SimpleNamespace(
        hand=[card_view(0, master.card(A), available=True), card_view(1, master.card(B), available=True)]
    )
    frame = SimpleNamespace(hand=[{"card_id": B, "upgrade": 0}, {"card_id": A, "upgrade": 0}])
    assert _screen_slot(frame, state, SimpleNamespace(kind="play", slot=0)) == 1
    assert _screen_slot(frame, state, SimpleNamespace(kind="play", slot=1)) == 0


# ---- 真实对局数据回放：插件只给「卡 id + 强化」，框架补全的 CardView 要和当时画面识别出来的一致


REPLAY = json.loads(
    (__import__("pathlib").Path(__file__).parent / "data" / "exam-plugin-replay.json").read_text(encoding="utf-8")
)
KEYS = ("card_id", "name", "upgrade", "available", "category", "cost", "hp_cost", "slot", "badge_score")


@pytest.mark.parametrize("step", REPLAY["steps"], ids=lambda s: f"seq{s['seq']}")
def test_replay_ids_only_hand_matches_screen(step):
    recorded = step["exam"]["hand"]
    Hook.reply = {
        "hand": [{k: c[k] for k in ("slot", "card_id", "upgrade", "available", "badge_score")} for c in recorded]
    }
    mem, st = _screen_state()
    got, _ = _read(ObservationExamStateSource(Inner(st)), mem)
    assert [{k: getattr(c, k) for k in KEYS} for c in got.hand] == [{k: c[k] for k in KEYS} for c in recorded]


@pytest.mark.parametrize("step", REPLAY["steps"], ids=lambda s: f"seq{s['seq']}")
def test_replay_full_dicts_build_when_screen_gives_nothing(step):
    mem = ExamMemory("r1")
    Hook.reply = {"remaining_turns": 3, "hp": 10, "genki": 1, "score": 50, **step["exam"]}

    class Overlay(Inner):
        def read(self, f, m):
            return None, "hand_overlay"

    b = ObservationExamStateSource(Overlay(None))
    b.begin({"profile": None})
    got, _ = _read(b, mem)
    assert [c.to_dict() for c in got.hand] == [CardView.from_dict(c).to_dict() for c in step["exam"]["hand"]]
    assert b.confidence() == "save"
