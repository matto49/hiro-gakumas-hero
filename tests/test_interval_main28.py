"""第二十八局轮间（总控 2026-11-04）：社区默认方案（plan_tiered）把 ミックススムージー（换手·2 档，增益 +1,506 ± 6,079）当抽牌类目标先买了。
drink_step 能用时，方案里的饮料一律交给它：这里不买 スムージー，先刷新找目标。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main28_interval.json").read_text(encoding="utf-8"))
GAINS = {k: tuple(v) for k, v in FIX["gains"].items()}
STEP = {s["line"]: s for s in FIX["steps"]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(iv, "plan_tiered", lambda *a, **k: copy.deepcopy(FIX["plan"]))
    monkeypatch.setattr(iv, "drink_gains", lambda *a, **k: dict(GAINS))
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))
    monkeypatch.setattr(iv, "STATE_DIR", None)
    iv._GAINS_BG.clear()
    yield
    iv._GAINS_BG.clear()


def st(line):
    s = copy.deepcopy(STEP[line])
    return GameState(page_fields={"stamina": s["stamina"], "max_stamina": s["max_stamina"], "produce_point": s["produce_point"], "interval": s["interval"]},
                     produce=s["produce"])  # fmt: skip


def test_main28_plan_drinks_go_through_drink_step(env):
    dec = ProduceDeciders(CAMPUS_HIRO, None)
    d = dec.decide("HIF_ROUND_INTERVAL", st(75))
    assert d.action.kind is ActionKind.REROLL, d.reason  # 实机：按方案买了 7 号格 ミックススムージー
