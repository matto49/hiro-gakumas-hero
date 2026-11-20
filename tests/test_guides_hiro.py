"""广的手写知识迁成攻略条目（hiro.brain.guides_hiro）后行为不变：HIRO_GUIDES_HIRO=1 从条目读，和手写表逐条一致（设计文档 4.6 / 4.9-4）。"""

import json
import random

import pytest

from hiro.brain import guides

HIRO = "i_card-shro-3-007"


@pytest.fixture
def hiro_pack(monkeypatch, tmp_path):
    from hiro.brain import guides_hiro

    for mode in ("hajime", "nia"):
        pack = {"format": "gkbench.guides.v1", "schema": 1, "mode": mode, "version": "test", "entries": guides_hiro.entries()}
        (tmp_path / f"{mode}-test.json").write_text(json.dumps(pack, ensure_ascii=False))
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": 1, "packs": {m: {"name": f"{m}-test"} for m in ("hajime", "nia")}}))
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    guides._load_pack.cache_clear()
    yield
    guides._load_pack.cache_clear()


def _pool():
    from hiro.state.context import catalog
    from hiro.state.knowledge import CONFLICT_CARDS, CULL_AS_IMPRESSION, OFF_PLAN_CARDS, SMALL_DECK_CORE, YARUKI_CARDS

    ids = set(YARUKI_CARDS) | set(SMALL_DECK_CORE) | set(CONFLICT_CARDS) | set(OFF_PLAN_CARDS) | set(CULL_AS_IMPRESSION)
    others = sorted(r["id"] for r in catalog().rows("ProduceCard")
                    if r.get("upgradeCount", 0) == 0 and r["id"].startswith(("p_card-02-", "p_card-00-")) and "-ido-" not in r["id"] and "-0_" not in r["id"])  # fmt: skip
    rnd = random.Random(7)
    return sorted(ids) + rnd.sample(others, 40)


def _decide(mode, cands, deck, rerolls):
    from hiro.brain.deciders import ProduceDeciders, action_key
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    prod = {"idol_card_id": HIRO, "mode": mode, "stamina": 20, "max_stamina": 30, "produce_point": 50, "params": {"Vo": 1, "Da": 1, "Vi": 1},
            "growth_permil": {}, "rerolls_left": rerolls, "excludes_left": 1, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("SELECT_CARD", GameState(produce=prod, page_fields={"candidates": [{"card_id": c} for c in cands]}))
    return action_key(d.action)


@pytest.mark.master
def test_hiro_card_pick_same_with_guides(monkeypatch, hiro_pack):
    """300 页三选一 × 3 种牌组 × 初 / N.I.A：开 HIRO_GUIDES_HIRO 前后选法一样，而且开了以后确实读到了条目。"""
    from hiro.brain.idol_know import card_know

    pool = _pool()
    rnd = random.Random(11)
    decks = [[], ["p_card-02-act-2_062", "p_card-02-men-1_031"], ["p_card-02-men-1_031", "p_card-02-men-1_031", "p_card-02-men-2_057", "p_card-02-men-2_111"]]
    pages = [(rnd.sample(pool, 3), decks[i % 3], rnd.choice([0, 2]), rnd.choice(["produce-002", "produce-004"])) for i in range(300)]
    monkeypatch.delenv("HIRO_GUIDES_HIRO", raising=False)
    assert card_know("produce-002", HIRO) is None
    before = [_decide(m, c, d, r) for c, d, r, m in pages]
    monkeypatch.setenv("HIRO_GUIDES_HIRO", "1")
    assert card_know("produce-002", HIRO) is not None
    after = [_decide(m, c, d, r) for c, d, r, m in pages]
    diff = [(p, a, b) for p, a, b in zip(pages, before, after) if a != b]
    assert not diff, diff[:3]
    assert len(set(before)) > 3  # 样本里各种选法都有（拿第 0 / 1 / 2 张、刷新、禁用）


@pytest.mark.master
def test_hiro_customize_same_with_guides(monkeypatch, hiro_pack):
    from hiro.brain.idol_know import customize_plan
    from hiro.state.knowledge import CUSTOMIZE_LEVELS, CUSTOMIZE_PRIORITY

    monkeypatch.setenv("HIRO_GUIDES_HIRO", "1")
    pri, lv = customize_plan("produce-004", HIRO)
    assert [(c, k) for c, _, k, _ in pri] == [(c, k) for c, _, k, _ in CUSTOMIZE_PRIORITY]
    assert lv == CUSTOMIZE_LEVELS
    assert customize_plan("produce-007", HIRO) is None  # H.I.F 不接攻略


@pytest.mark.master
def test_hiro_entries_evidence():
    """迁移的条目：偶像卡层、每条有证据，社区攻略标 guide，用户 / 总控定的规则标 decision。"""
    from hiro.brain import guides_hiro

    es = guides_hiro.entries()
    assert {e["decision"] for e in es} == {"card_pick", "card_upgrade", "formation.main_attr"}
    for e in es:
        assert e["scope"].get("idol_card") == HIRO and e["evidence"] and guides.Entry(e).layer == 5
    kinds = {(x["kind"], x["ref"]) for x in es[0]["evidence"]}
    assert ("guide", "game8") in kinds and ("decision", "决策-07") in kinds
