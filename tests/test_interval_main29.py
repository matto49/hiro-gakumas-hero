"""第二十九局轮间（总控 2026-11-04 紧急）：R1 四瓶全喝完（finals_context drinks = []），轮间页 produce.drinks 停在 R1 开局的
4 瓶（seq 115、field_status pending）→ 判「饮料栏满」，方案里的 ブースト / ビネガー 交给 drink_step 后一瓶没买，去强化了 紙吹雪、剩 320P。
修：持有以本帧画面读到的饮料栏（observed / verified）或 R1 结束时的实际剩余为准，不用未在本帧读到的 produce.drinks；不一致报警。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main29_interval.json").read_text(encoding="utf-8"))
GAINS = {k: tuple(v) for k, v in FIX["gains"].items()}
SEQ = {s["seq"]: s for s in FIX["steps"]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: copy.deepcopy(FIX["plan"]))
    monkeypatch.setattr(iv, "drink_gains", lambda *a, **k: dict(GAINS))
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))
    monkeypatch.setattr(iv, "STATE_DIR", None)
    iv._GAINS_BG.clear()
    yield
    iv._GAINS_BG.clear()


def st(seq, p=None):
    s = copy.deepcopy(SEQ[seq]["state"])
    if p is not None:
        s["page_fields"]["produce_point"] = p
    return GameState.from_dict(s)


def test_main29_stale_holding_ignored(env):
    s = st(189)
    assert s.produce["drinks"] and s.field_status.get("produce.drinks") == "pending"  # 过时的 R1 开局持有
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    for seq in (187, 188):
        dec.decide("HIF_ROUND_INTERVAL", st(seq))
    d = dec.decide("HIF_ROUND_INTERVAL", st(189))
    assert d.action.kind is ActionKind.INTERVAL_BUY and d.action.args == {"index": 6}, (
        d.reason
    )  # ブースト（实机：强化）
    assert "持有 空" in d.reason, d.reason
    assert any("不一致" in w for w in d.warnings), d.warnings  # produce.drinks 和 R1 结束剩余不一致：报警
    s2 = st(189, p=345)
    s2.page_fields["interval"]["drinks"][0].update(purchased=True, drink_id=None, name=None)
    d = dec.decide("HIF_ROUND_INTERVAL", s2)  # 只剩 ビネガー（3 档）、P 够：刷新找目标，不强化
    assert d.action.kind is ActionKind.REROLL, d.reason


def test_fresh_screen_read_wins(env):
    s = st(189)
    s.field_status["produce.drinks"] = "observed"
    s.sources["produce.drinks"] = type(s.sources["produce.drinks"])(
        **{**vars(s.sources["produce.drinks"]), "seq": s.seq}
    )
    s.produce["drinks"] = ["pdrink_00-3-001"]
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    assert dec._interval_held(s, dec.produce_state(s)) == ["pdrink_00-3-001"]  # 本帧画面读到：按画面
    assert "按画面" in (dec._interval_held_warn or "")


def test_exam_end_sync_is_not_fresh_read(env, monkeypatch):
    """执行 cdce573：离开考试时 produce.drinks ← exam.drinks、记 observed（来源是 R1 结束那一帧）。轮间时它不是本帧读数：
    按 R1 结束剩余算，不拿它减本次轮间买的（R1 剩 ホエイ、轮间又买 ホエイ 时不会少算一瓶）。"""
    monkeypatch.setitem(iv.CONTEXT, "drinks", ["pdrink_00-3-001"])
    s = st(189)
    s.field_status["produce.drinks"] = "observed"
    s.produce["drinks"] = ["pdrink_00-3-001"]  # 来源 seq 仍是旧帧
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    dec._interval_ledger = {
        "key": ("produce-008", 6),
        "drinks": ["pdrink_00-3-001"],
        "cards": [],
        "upgraded": [],
        "rerolls": 0,
        "pending": None,
    }
    assert dec._interval_held(s, dec.produce_state(s)) == ["pdrink_00-3-001"]
