"""第二十七局轮间（协调 2026-11-04）：
1. 05:35:38 买 ブレンド 的理由只写了「不买时继续刷新的价值 50,260」，像是拿 +12,176 和它比；实际比较是「买：净值 + 剩 3 格继续刷新」对「不买：剩 4 格继续刷新」。理由两侧都写。
2. 刷满 3 次（硬上限）后剩 170P、空 2 格，100P 强化了 紙吹雪。REROLL_FOR_TARGETS（默认关）开时：还缺目标、P 够刷新 + 一瓶目标，就继续刷新找目标，不强化。
3. TARGET_CALIBRATION（默认关）：复制 / 抽牌 / 爆发 的增益按专家口径放大（增益表复核）。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main27_interval.json").read_text(encoding="utf-8"))
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


def run(dec, upto):
    d = None
    for line in range(71, upto + 1):
        d = dec.decide("HIF_ROUND_INTERVAL", st(line))
    return d


def test_main27_reason_shows_both_sides(env):
    d = run(ProduceDeciders(CAMPUS_HIRO, None), 74)
    assert d.action.args == {"index": 7}, d.reason  # ブレンド（买一瓶只占一格，买后剩 3 格照样刷新）
    assert "买：净值" in d.reason and "不买：剩 4 格继续刷新" in d.reason, d.reason


def test_main27_after_cap_switch_off_upgrades(env, monkeypatch):
    monkeypatch.setattr(iv, "REROLL_FOR_TARGETS", False)
    d = run(ProduceDeciders(CAMPUS_HIRO, None), 78)
    assert d.action.kind is ActionKind.INTERVAL_UPGRADE_CARD, d.reason  # 开关关：同实机


def test_main27_after_cap_reroll_for_targets(env):
    assert iv.REROLL_FOR_TARGETS is True and iv.TARGET_CALIBRATION is True  # 协调 2026-11-04 定默认开
    d = run(ProduceDeciders(CAMPUS_HIRO, None), 78)
    assert d.action.kind is ActionKind.REROLL, d.reason  # 剩 170P、空 2 格、还缺目标：再刷找目标，不拿 100P 去强化


def test_target_calibration_scales_only_targets(monkeypatch):
    monkeypatch.setattr(
        iv, "VALUE_TABLE_SCALE", False
    )  # 测手调倍率本身；价值表开时被 drink_scale.json 取代（test_value_table_finals）
    monkeypatch.setattr(iv, "TARGET_SCALE_V2", False)  # 第一版比例（V2 默认开后单独测）
    g = iv.calibrated_gains(GAINS)
    assert g["pdrink_00-2-008"][0] == round(GAINS["pdrink_00-2-008"][0] * 1.43)  # ブースト
    assert g["pdrink_02-3-007"] == GAINS["pdrink_02-3-007"]  # ブレンド 不变
    monkeypatch.setattr(iv, "TARGET_CALIBRATION", False)
    assert iv.calibrated_gains(GAINS) is GAINS


def test_target_scale_v2_default_on(monkeypatch):
    monkeypatch.setattr(iv, "VALUE_TABLE_SCALE", False)  # 测 V2 本身；价值表开时被取代
    assert iv.TARGET_SCALE_V2 is True  # 协调 2026-11-04 验收后开
    g = iv.calibrated_gains(GAINS)
    assert g["pdrink_00-2-008"][0] == round(GAINS["pdrink_00-2-008"][0] * 2.9)  # ブースト：爆发 ×2.9
    assert g["pdrink_02-3-007"] == GAINS["pdrink_02-3-007"]  # ブレンド 不变
