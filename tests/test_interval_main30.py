"""第三十局轮间（协调 2026-11-04 饮料反事实）：增益表校准后仍低估目标饮料（特製 实测 +8.5–11.2 万 vs 表 2.4–4.2 万，ブースト +5.4–6.9 万 vs 1.9–2.3 万），
seq 198 只比继续刷新多 +3,448 就买了 ブレンド。TARGET_SCALE_V2（复制 ×4.5、爆发 ×2.9、抽牌 ×1.62、加行动 ×1.3，默认关）开时不买、继续刷新。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main30_interval.json").read_text(encoding="utf-8"))
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


def run(upto):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = None
    for seq in (196, 197, 198, 199, 200, 201, 202):
        if seq > upto:
            break
        d = dec.decide("HIF_ROUND_INTERVAL", GameState.from_dict(copy.deepcopy(SEQ[seq]["state"])))
    return d


def test_v1_buys_blend(env, monkeypatch):
    monkeypatch.setattr(iv, "TARGET_SCALE_V2", False)  # 第一版比例：同实机
    d = run(198)
    assert d.action.args == {"index": 7}, d.reason  # 同实机：买 ブレンド


def test_v2_keeps_refreshing(env, monkeypatch):
    monkeypatch.setattr(iv, "VALUE_TABLE_SCALE", False)  # V2 口径；价值表开时见 test_value_table_buys_blend
    assert iv.TARGET_SCALE_V2 is True  # 协调 2026-11-04 验收后默认开
    d = run(198)
    assert d.action.kind is ActionKind.REROLL, d.reason  # 第一次见到 ブレンド 不买，继续刷新找目标
    assert iv.calibrated_gains(GAINS)["pdrink_02-3-010"][0] == round(GAINS["pdrink_02-3-010"][0] * 4.5)


def test_value_table_buys_blend(env, monkeypatch):
    """VALUE_TABLE_SCALE（价值表，协调 2026-11-04 开）：目标饮料不再 ×4.5 / ×2.9，继续刷新的价值变低，
    第一次见到 ブレンド（やる気·2 档，专家层 +2.1 万）就买，不再刷新找目标（fresh 验收：R2 分不变、结束空位 31% → 13%）。"""
    monkeypatch.setattr(iv, "VALUE_TABLE_SCALE", True)
    iv.value_table_scale.cache_clear()
    d = run(198)
    assert d.action.kind is ActionKind.INTERVAL_BUY and d.action.args == {"index": 7} and "ブレンド" in d.reason, (
        d.reason
    )
