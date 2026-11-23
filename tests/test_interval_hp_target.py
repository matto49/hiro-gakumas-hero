"""回体力超过角色目标那段单独过 2 SE（HP_ABOVE_TARGET_TEST，默认开；用户「控制在半血以上就好」，协调 2026-11-04）：
模拟方案回到 24、社区默认回到 17 时，同种子配对「方案 − 方案只回到 17」，不到 2 SE 就只回到 17（省下的 P 给刷新找目标饮料）。"""

import copy
import json
from pathlib import Path

import hiro.sim  # noqa: F401
from hiro.sim import interval as iv

import pytest

FIX = json.loads((Path(__file__).parent / "fixtures" / "interval" / "main25_interval.json").read_text(encoding="utf-8"))
STEP = {s["line"]: s for s in FIX["steps"]}


@pytest.mark.parametrize("extra,kept", [(0, False), (20_000, True)])
def test_hp_above_target_tested_alone(monkeypatch, extra, kept):
    shelf = copy.deepcopy(STEP[75]["interval"])
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))

    def fake_search(setup, opts, p, **k):
        rows = [{"options": [o.key], "labels": [o.label], "gain": {"hp24": 45_000, "hp17": 20_000}.get(o.key, -1), "gain_se": 3000, "cost": o.cost, "mean": 1}
                for o in opts if o.key in ("hp24", "hp17")]  # fmt: skip
        return {"combos": rows, "seconds": 0, "policy": "community", "seeds": [0]}

    def score(setup, s):
        noise = 6000 * ((s * 7919) % 3 - 1)
        # 社区默认方案买的 特製 在这里记 −30,000：模拟方案（不买它）整体显著好过社区默认；回体力超过 17 只多 extra（加噪声）
        return (
            2000 * min(setup.hp, 17)
            + (extra + noise if setup.hp > 17 else 0)
            - 30_000 * ("pdrink_02-3-010" in setup.drinks)
        )

    def fake_eval(setups, seeds, policy, workers=4, **k):
        return [{"mean": 0, "se": 1, "scores": [score(st_, s) for s in seeds]} for st_ in setups]

    monkeypatch.setattr(iv, "search", fake_search)
    monkeypatch.setattr(iv, "evaluate", fake_eval)
    # 先让「回到 24 vs 社区默认」显著（整体偏离成立），再看超过 17 那段单独的检验
    r = iv.plan_tiered(shelf, hp=10, max_hp=33, p=480, star=None, target_hp=17)
    assert any("比只回到 17 多" in n for n in r["rule_notes"]), r["rule_notes"]
    assert (r["target_hp"] == 24) is kept, (r["target_hp"], r["rule_notes"])


@pytest.mark.parametrize("best,unsure", [("hp17", True), ("hp24", False)])
def test_plan_tiered_marks_noise_level_sim_plan_unsure(monkeypatch, best, unsure):
    """决策点补全：模拟方案比社区默认好但在噪声内 → unsure + alt（交 LLM 二选一）；模拟方案回体力超过角色目标时不算（多回要单独显著）。"""
    shelf = copy.deepcopy(STEP[75]["interval"])
    monkeypatch.setattr(iv, "CONTEXT", copy.deepcopy(FIX["context"]))

    def fake_search(setup, opts, p, **k):
        rows = [{"options": [o.key], "labels": [o.label], "gain": 45_000 if o.key == best else -1, "gain_se": 3000, "cost": o.cost, "mean": 1}
                for o in opts if o.key in ("hp24", "hp17")]  # fmt: skip
        return {"combos": rows, "seconds": 0, "policy": "community", "seeds": [0]}

    def score(setup, s):  # 社区默认买的 特製 只 −500（噪声 ±6,000）：模拟方案好一点，但不显著
        has = "pdrink_02-3-010" in setup.drinks
        return 2000 * min(setup.hp, 17) + has * (6000 * ((s * 7919) % 3 - 1) - 500)

    monkeypatch.setattr(iv, "search", fake_search)
    monkeypatch.setattr(
        iv,
        "evaluate",
        lambda setups, seeds, policy, workers=4, **k: [
            {"mean": 0, "se": 1, "scores": [score(x, s) for s in seeds]} for x in setups
        ],
    )
    r = iv.plan_tiered(shelf, hp=10, max_hp=33, p=480, star=None, target_hp=17)
    assert r["deviation"] is None, r["rule_notes"]
    assert bool(r["unsure"]) is unsure, r["rule_notes"]
    if unsure:
        assert r["alt"]["target_hp"] == 17 and "在噪声内" in r["unsure"] and r["alt"]["labels"] != r["rule_labels"]
