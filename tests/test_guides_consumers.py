"""偶像卡攻略接到决策器（广以外的偶像，默认生效）：选卡、相談删卡、特別指導定制、提示词偶像一节、开局编成主属性。"""

import json

from hiro.brain import guides

import pytest

FKTN = "i_card-fktn-3-000"
TEBYOSHI, IMETORE, KAIKA, BASIC = (
    "p_card-02-act-1_003",
    "p_card-02-men-2_008",
    "p_card-02-act-3_038",
    "p_card-00-act-0_001",
)
OTOME, MINNA = "p_card-02-men-2_060", "p_card-02-act-2_049"
ENTRIES = [
    {"id": "hajime.idol.i_card-fktn-3-000.card_pick.test", "kind": "written", "scope": {"idol_card": FKTN}, "decision": "card_pick",
     "content": {"prefer": [{"id": IMETORE, "weight": 90}, {"id": MINNA, "keep": 2}], "avoid": [{"id": TEBYOSHI, "role": "conflict"}],
                 "why": "测试：イメトレ 当强卡、手拍子 避开"},
     "evidence": [{"kind": "guide", "ref": "测试攻略"}]},
    {"id": "hajime.idol.i_card-fktn-3-000.card_remove.test", "kind": "written", "scope": {"idol_card": FKTN}, "decision": "card_remove",
     "content": {"prefer": [BASIC], "avoid": [OTOME], "why": "测试：基本卡保护、オトメゴコロ 先删"}, "evidence": [{"kind": "guide", "ref": "测试攻略"}]},
    {"id": "nia.idol.i_card-fktn-3-000.card_upgrade.test", "kind": "written", "scope": {"idol_card": FKTN}, "decision": "card_upgrade",
     "content": {"prefer": [{"id": OTOME, "custom": ["review_add"], "levels": 2, "full": True, "note": "オトメゴコロ：好印象定制"}],
                 "why": "测试：オトメゴコロ 定制好印象"}, "evidence": [{"kind": "guide", "ref": "いおん10"}]},
    {"id": "hajime.idol.i_card-fktn-3-000.formation.main_attr.test", "kind": "written", "scope": {"idol_card": FKTN},
     "decision": "formation.main_attr", "content": {"prefer": ["da"], "why": "测试"}, "evidence": [{"kind": "ranking", "mode": "hajime", "n": 100}]},
]  # fmt: skip


@pytest.fixture
def pack(monkeypatch, tmp_path):
    for mode in ("hajime", "nia"):
        (tmp_path / f"{mode}-t.json").write_text(
            json.dumps({"schema": 1, "mode": mode, "version": "t", "entries": ENTRIES}, ensure_ascii=False)
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 1, "packs": {m: {"name": f"{m}-t"} for m in ("hajime", "nia")}})
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    monkeypatch.delenv("HIRO_GUIDES", raising=False)
    guides._load_pack.cache_clear()
    yield
    guides._load_pack.cache_clear()


def _pd():
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for

    return ProduceDeciders(profile_for(FKTN), None)


def _prod(mode="produce-002", deck=(), p=160):
    return {"idol_card_id": FKTN, "mode": mode, "stamina": 20, "max_stamina": 30, "produce_point": p, "params": {"Vo": 1, "Da": 1, "Vi": 1},
            "growth_permil": {}, "rerolls_left": 0, "excludes_left": 0, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip


@pytest.mark.master
def test_card_pick_uses_guide(pack, monkeypatch):
    from hiro.brain.deciders import action_key
    from hiro.contracts import GameState

    def pick(cands, deck=()):
        d = _pd().decide(
            "SELECT_CARD",
            GameState(produce=_prod(deck=deck), page_fields={"candidates": [{"card_id": c} for c in cands]}),
        )
        return action_key(d.action), d.reason

    key, reason = pick([TEBYOSHI, IMETORE])
    assert (
        key == "pick:1" and "攻略" in reason
    )  # 没有攻略时拿 手拍子（主打好印象）；攻略把它列为避开、イメトレ 列为强卡
    key, _ = pick([MINNA, KAIKA], deck=[MINNA])
    assert key == "pick:0"  # みんな大好き：攻略 keep 2，牌组里 1 张还缺
    monkeypatch.setenv("HIRO_GUIDES", "0")
    key, _ = pick([TEBYOSHI, IMETORE])
    assert key == "pick:0"  # 总开关关掉：回到流派规则


@pytest.mark.master
def test_consult_uses_guide(pack):
    from hiro.brain.deciders import action_key
    from hiro.contracts import GameState

    pf = {"shelf": [], "services": {"delete": {"price": 100, "available": True}}}
    d = _pd().decide("CONSULT", GameState(produce=_prod(deck=[BASIC, OTOME, TEBYOSHI]), page_fields=pf))
    assert action_key(d.action) == "buy:delete:1"  # 基本卡受保护；オトメゴコロ 攻略里先删


@pytest.mark.master
def test_customize_and_prompt_and_main_attr(pack):
    from hiro.brain.deciders import _idol_section
    from hiro.brain.formation_plan import idol_info
    from hiro.brain.idol_know import customize_plan
    from hiro.brain.state import profile_for

    pri, lv = customize_plan("produce-004", FKTN)
    assert pri[0][0] == OTOME and pri[0][2] == ["review_add"] and lv == {OTOME: (2, True)}
    text = _idol_section(FKTN, profile_for(FKTN), "produce-002")
    assert "这张卡的攻略要点" in text and "测试攻略" in text
    info = idol_info(FKTN, mode="produce-002")
    assert info["default_main"] == "Da" and info["main_why"].startswith("攻略")
    assert idol_info(FKTN)["main_why"] == "成长率最高"  # 不给 mode：不查攻略


def test_stat_share_as_weight_and_no_custom():
    """统计条目：占比换成 weight（≥ 75% 算强卡）；card_upgrade 统计没有定制项，不当定制顺序。"""
    from hiro.brain.idol_know import CardKnow

    k = CardKnow(
        prefer={
            "a": {"id": "a", "stat": {"share": 0.96}},
            "b": {"id": "b", "stat": {"share": 0.5}},
            "c": {"id": "c", "weight": 60},
        }
    )
    assert k.weight("a") == 96 and k.strong("a") and not k.strong("b") and k.weight("c") == 60


@pytest.mark.master
def test_stat_upgrade_not_customize(monkeypatch, tmp_path):
    from hiro.brain.idol_know import customize_plan

    e = {
        "id": "stat.nia.x.card_upgrade",
        "kind": "stat",
        "scope": {"mode": "nia", "idol_card": FKTN},
        "decision": "card_upgrade",
        "content": {"prefer": [{"id": OTOME, "stat": {"share": 0.7}}]},
        "evidence": [{"kind": "ranking", "mode": "nia", "n": 100}],
    }
    (tmp_path / "nia-s.json").write_text(json.dumps({"schema": 1, "entries": [e]}))
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": 1, "packs": {"nia": {"name": "nia-s"}}}))
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    guides._load_pack.cache_clear()
    assert customize_plan("produce-004", FKTN) is None
    guides._load_pack.cache_clear()


@pytest.mark.master
def test_review_pack_protect_and_genki_cash(monkeypatch, tmp_path):
    """条目级别高于 flows：prefer 保护的卡不删；class:genki_cash（元気のN%分）条目点名才删。"""
    from hiro.brain.deciders import _flow_delete_pool

    genki = "p_card-02-act-1_004"  # 元気な挨拶：元気の110%分
    e = {
        "id": "hajime.style-review.card_remove.t",
        "kind": "written",
        "scope": {"mode": "hajime", "style": "review"},
        "decision": "card_remove",
        "content": {"avoid": ["class:genki_cash"], "prefer": [KAIKA]},
        "evidence": [{"kind": "guide", "ref": "t"}],
    }
    (tmp_path / "hajime-r.json").write_text(json.dumps({"schema": 1, "entries": [e]}))
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": 1, "packs": {"hajime": {"name": "hajime-r"}}}))
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    guides._load_pack.cache_clear()
    cards = [{"index": 0, "card_id": KAIKA}, {"index": 1, "card_id": genki}]
    ids = [c["card_id"] for c in _flow_delete_pool(cards, FKTN, "produce-002")]
    assert ids == [genki]  # 開花（主打やる気，flows 判冲突）被条目保护；元気な挨拶 按 class:genki_cash 删
    guides._load_pack.cache_clear()


@pytest.mark.master
def test_signature_buff_per_style():
    """拿手 buff：麻央 Fluorite 好調、莉波 Howling 集中、リーリヤ 極光 強気、ことね 好印象、广 やる気。"""
    from hiro.brain.deciders import _signature_buff

    assert [
        _signature_buff(i)
        for i in ("i_card-amao-3-000", "i_card-hrnm-3-012", "i_card-kllj-3-008", FKTN, "i_card-shro-3-007")
    ] == ["好調", "集中", "強気", "好印象", "やる気"]
