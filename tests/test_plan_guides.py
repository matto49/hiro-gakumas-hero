"""开局编成方案用社区攻略（2026-11-26，用户：「选择偶像想要培育之后，会出现没有策略的问题」）：
- 攻略包里编成类条目几乎都是按单张偶像卡写的（排行榜前 100 的占比）；这张卡在这个模式没有条目时，
  按同流派、同模式其他卡的条目合并出一份「流派默认」（占比取平均，没出现的算 0）。
- 出方案时，攻略推荐的支援卡 / 回忆按占比加分（HIRO_PLAN_GUIDES=0 关掉）。"""

import json

from hiro.brain import guides

import pytest

pytestmark = pytest.mark.synthetic

A, B, C, OTHER = "i_card-aaaa-3-000", "i_card-bbbb-3-000", "i_card-cccc-3-000", "i_card-dddd-3-000"
TRAITS = {A: ("sense", "parameter_buff"), B: ("sense", "parameter_buff"), C: ("sense", "parameter_buff"),
          OTHER: ("logic", "review")}  # fmt: skip


def _stat(idol, decision, shares, mode="hajime"):
    return {"id": f"stat.{mode}.{idol}.{decision}", "kind": "stat", "scope": {"mode": mode, "idol_card": idol},
            "decision": decision, "content": {"prefer": [{"id": k, "stat": {"share": v}} for k, v in shares.items()], "avoid": []},
            "evidence": [{"kind": "ranking", "mode": mode, "n": 100}]}  # fmt: skip


ENTRIES = [
    _stat(A, "formation.support", {"s1": 0.8, "s2": 0.4}),
    _stat(B, "formation.support", {"s1": 0.6, "s3": 0.5}),
    _stat(OTHER, "formation.support", {"s9": 0.9}),
    _stat(A, "formation.main_attr", {"vi": 0.9, "da": 0.1}),
    _stat(B, "formation.main_attr", {"vi": 0.5, "vo": 0.5}),
    _stat(A, "formation.support", {"s1": 0.99}, mode="nia"),
]


@pytest.fixture
def pack(monkeypatch, tmp_path):
    for mode in ("hajime", "nia"):
        rows = [e for e in ENTRIES if e["scope"]["mode"] == mode]
        (tmp_path / f"{mode}-t.json").write_text(json.dumps({"schema": 1, "entries": rows}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 1, "packs": {m: {"name": f"{m}-t"} for m in ("hajime", "nia")}}), encoding="utf-8"
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    monkeypatch.delenv("HIRO_GUIDES", raising=False)
    monkeypatch.setattr(guides, "idol_traits", lambda idol: TRAITS.get(guides.norm_idol(idol), (None, None)))
    guides._load_pack.cache_clear()
    yield
    guides._load_pack.cache_clear()


def _shares(idol, decision, mode="hajime"):
    items = guides.merge(guides.lookup(mode=mode, difficulty=None, idol_card=idol, decision=decision))
    return {it.id: round(it.fields["stat"]["share"], 3) for it in items}, items


def test_card_without_entries_gets_style_average(pack):
    shares, items = _shares(C, "formation.support")
    assert shares == {"s1": 0.7, "s2": 0.2, "s3": 0.25}  # A、B 两张同流派卡的平均；别的流派（s9）不算
    assert list(shares) == ["s1", "s3", "s2"]  # 按占比排
    e = items[0].entry
    assert e.scope == {"mode": "hajime", "style": "parameter_buff"} and e.layer == 4
    assert "流派" in e.content["why"] and "2 张" in e.content["why"]
    assert e.raw["evidence"][0]["n"] == 200


def test_card_with_own_entries_is_not_mixed(pack):
    shares, items = _shares(A, "formation.support")
    assert shares == {"s1": 0.8, "s2": 0.4} and items[0].entry.layer == 5


def test_style_average_stays_within_mode(pack):
    assert _shares(C, "formation.support", mode="nia")[0] == {"s1": 0.99}  # N.I.A 只有 A 的那条


def test_main_attr_style_average(pack):
    assert _shares(C, "formation.main_attr")[0] == {"vi": 0.7, "vo": 0.25, "da": 0.05}


def test_style_average_off_for_non_formation_decisions(pack):
    assert guides.lookup(mode="hajime", difficulty=None, idol_card=C, decision="card_pick") == []


# ---------------------------------------------------------------- 攻略占比进入选择（要主数据）
def _support_pool(attr, n, plan="ProducePlanType_Plan1"):
    from hiro.brain import support_select as ss
    from hiro.state.context import catalog

    c, out = catalog(), []
    for r in c.rows("SupportCard"):
        if (
            r["rarity"] == "SupportCardRarity_Ssr"
            and ss.ATTR_OF.get(r["type"]) == attr
            and r["planType"]
            in (
                "ProducePlanType_Common",
                plan,
            )
        ):
            lims = sorted(c.by_id("SupportCardLevelLimit", r["supportCardLevelLimitId"]), key=lambda x: x["levelLimit"])
            out.append({"support_card_id": r["id"], "level": lims[-1]["levelLimit"], "limit_rank": lims[-1]["rank"]})
        if len(out) >= n:
            break
    return out


@pytest.mark.master
def test_support_guide_share_pulls_recommended_card_in():
    from hiro.brain import support_select as ss

    inv = {"cards": _support_pool("Vi", 9) + _support_pool("Da", 4), "rentals": []}
    with ss.using("Vi", "ProducePlanType_Plan1", 4):
        base = ss.choose(inv, size=6)
        picked = {o["support_card_id"] for o in base["own"]}
        left = next(c["support_card_id"] for c in inv["cards"][:9] if c["support_card_id"] not in picked)
    with ss.using("Vi", "ProducePlanType_Plan1", 4, guide={left: 0.9}):
        r = ss.choose(inv, size=6)
    chosen = {o["support_card_id"]: o for o in r["own"]}
    assert left in chosen and any("攻略推荐 90%" in w for w in chosen[left]["why"])
    assert ss.GUIDE == {}  # using 退出后还原


def _memories(card_ids):
    base = {"stats": {"Vo": 10, "Da": 10, "Vi": 10, "hp": 0}, "timing": "mid", "abilities": [], "effect": []}
    return {
        "entries": [
            {**base, "id": f"m{i}", "card": {"card_id": c, "name": c, "upgrade": 1}} for i, c in enumerate(card_ids)
        ]
    }


SENSE_MEMS = ["p_card-01-act-2_001", "p_card-01-men-3_006", "p_card-01-men-2_037", "p_card-01-act-3_049",
              "p_card-01-men-3_036", "p_card-01-men-2_011"]  # fmt: skip


@pytest.mark.master
def test_memory_guide_share_pulls_recommended_card_in():
    from hiro.brain.formation import select_memories

    kw = {"plan": "ProducePlanType_Plan1", "profile": "sense_spotlight"}
    base = {c["card_id"] for c in select_memories(_memories(SENSE_MEMS), **kw)["chosen"]}
    left = next(c for c in SENSE_MEMS if c not in base)
    r = select_memories(_memories(SENSE_MEMS), guide={left: 0.9}, **kw)
    chosen = {c["card_id"]: c for c in r["chosen"]}
    assert left in chosen and any("攻略推荐 90%" in w for w in chosen[left]["why"])


def test_plan_guide_shares_from_pack(pack, monkeypatch):
    from hiro.brain import formation_plan as fp

    assert fp.guide_shares("produce-001", C, "formation.support") == {"s1": 0.7, "s3": 0.25, "s2": 0.2}
    monkeypatch.setenv("HIRO_PLAN_GUIDES", "0")
    assert fp.guide_shares("produce-001", C, "formation.support") == {}


def test_support_guide_share_scales_with_level(monkeypatch):
    """N44（G2 11-26）：攻略推荐的占比是练满的卡统计出来的，Lv1 的推荐卡不该拿满额加分，被方案选进编成。"""
    from hiro.brain import support_select as ss

    monkeypatch.setattr(ss, "GUIDE", {"s_card-3-0034": 0.5, "s_card-2-0001": 0.5, "s_card-1-0001": 0.5})

    def bonus(cid, level):
        total, why = ss.card_value(ss.Slot(cid, level), {})
        return total, why

    full, why_full = bonus("s_card-3-0034", 60)
    assert full == pytest.approx(0.5 * ss.GUIDE_WEIGHT)
    assert any("攻略推荐 50%" in w for w in why_full)
    low, why_low = bonus("s_card-3-0034", 1)
    assert low == pytest.approx(0.5 * ss.GUIDE_WEIGHT / 60)
    assert any("Lv1/60" in w for w in why_low)
    assert bonus("s_card-3-0034", 30)[0] == pytest.approx(full / 2)
    assert bonus("s_card-2-0001", 50)[0] == pytest.approx(full)  # SR 满级 50
    assert bonus("s_card-1-0001", 40)[0] == pytest.approx(full)  # R 满级 40
    assert bonus("s_card-9-0001", 1)[0] == pytest.approx(0)  # 不在 GUIDE 里
