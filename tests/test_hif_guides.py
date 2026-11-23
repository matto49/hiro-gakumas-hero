"""H.I.F 接偶像卡攻略（HIRO_HIF_GUIDES=1，用户 11-21）：广以外的偶像选卡 / 删卡 / 强化按流派 + 攻略，不按广的必拿表；广照旧。
另有攻略包下载：H.I.F 包一起下、旧版删掉。"""

import json

from hiro.brain import guides

import pytest

FKTN = "i_card-fktn-3-000"
HIRO = "i_card-shro-3-007"
TEBYOSHI, IMETORE, KAIKA, BASIC = (
    "p_card-02-act-1_003",
    "p_card-02-men-2_008",
    "p_card-02-act-3_038",
    "p_card-00-act-0_001",
)
OTOME, MINNA = "p_card-02-men-2_060", "p_card-02-act-2_049"
ENTRIES = [
    {"id": "hif.idol.fktn.card_pick.test", "kind": "written", "scope": {"mode": "hif", "idol_card": FKTN}, "decision": "card_pick",
     "content": {"prefer": [{"id": IMETORE, "weight": 90}], "avoid": [{"id": TEBYOSHI, "role": "conflict"}], "why": "测试"},
     "evidence": [{"kind": "guide", "ref": "测试攻略"}]},
    {"id": "hif.idol.fktn.card_remove.test", "kind": "written", "scope": {"mode": "hif", "idol_card": FKTN}, "decision": "card_remove",
     "content": {"prefer": [BASIC], "avoid": [OTOME]}, "evidence": [{"kind": "guide", "ref": "测试攻略"}]},
    {"id": "stat.hif.fktn.card_upgrade", "kind": "stat", "scope": {"mode": "hif", "idol_card": FKTN}, "decision": "card_upgrade",
     "content": {"prefer": [{"id": MINNA, "stat": {"share": 0.9}}, {"id": KAIKA, "stat": {"share": 0.3}}]},
     "evidence": [{"kind": "ranking", "mode": "hif", "n": 100}]},
]  # fmt: skip


@pytest.fixture
def pack(monkeypatch, tmp_path):
    (tmp_path / "hif-t.json").write_text(json.dumps({"schema": 1, "mode": "hif", "version": "t", "entries": ENTRIES}, ensure_ascii=False),
                                         encoding="utf-8")  # fmt: skip
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 1, "packs": {"hif": {"name": "hif-t"}}}), encoding="utf-8"
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    monkeypatch.setenv("HIRO_HIF_GUIDES", "1")
    monkeypatch.delenv("HIRO_GUIDES", raising=False)
    monkeypatch.delenv("HIRO_GUIDES_HIRO", raising=False)
    guides._load_pack.cache_clear()
    yield
    guides._load_pack.cache_clear()


def _pd(idol=FKTN):
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import profile_for

    return ProduceDeciders(profile_for(idol), None)


def _prod(idol=FKTN, mode="produce-007", deck=(), p=160):
    return {"idol_card_id": idol, "mode": mode, "step": 3, "stamina": 20, "max_stamina": 30, "produce_point": p, "params": {"Vo": 1, "Da": 1, "Vi": 1},
            "growth_permil": {}, "rerolls_left": 2, "excludes_left": 2, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip


@pytest.mark.master  # 判流派（flows.flow）要查主数据 game.db；CI 没有主数据时跳过
def test_switch_and_who():
    from hiro.brain.idol_know import hif_other

    on = {"HIRO_HIF_GUIDES": "1"}
    assert hif_other("produce-007", FKTN, on) and hif_other("produce-008", "i_card-skin-amao-3-018", on)
    assert not hif_other("produce-007", FKTN, {})  # 缺省关
    assert not hif_other("produce-007", HIRO, on)  # 广照旧走手写表
    assert not hif_other("produce-002", FKTN, on)  # 初 / N.I.A 本来就走流派规则


@pytest.mark.master
def test_lookup_hif_only_when_on(pack, monkeypatch):
    from hiro.brain.idol_know import lookup

    assert [h.entry.id for h in lookup("produce-008", FKTN, "card_pick")] == ["hif.idol.fktn.card_pick.test"]
    monkeypatch.setenv("HIRO_HIF_GUIDES", "0")
    assert lookup("produce-008", FKTN, "card_pick") == []


@pytest.mark.master
def test_select_card_uses_guide_not_hiro_musts(pack):
    from hiro.brain.deciders import action_key
    from hiro.contracts import GameState

    d = _pd().decide(
        "SELECT_CARD",
        GameState(produce=_prod(), page_fields={"candidates": [{"card_id": TEBYOSHI}, {"card_id": IMETORE}]}),
    )
    assert action_key(d.action) == "pick:1" and "攻略" in d.reason and "必拿" not in d.reason


@pytest.mark.master
def test_remove_card_uses_guide(pack):
    from hiro.brain.deciders import action_key
    from hiro.contracts import GameState

    cards = [{"index": 0, "card_id": BASIC}, {"index": 1, "card_id": OTOME}, {"index": 2, "card_id": IMETORE}]
    d = _pd().decide(
        "SKILL_CARD_REMOVAL", GameState(produce=_prod(deck=[BASIC, OTOME, IMETORE]), page_fields={"cards": cards})
    )
    assert d.action.args.get("card_id") == OTOME and "攻略" in d.reason, (action_key(d.action), d.reason)


@pytest.mark.master
def test_enhance_by_upgrade_share(pack):
    from hiro.contracts import GameState

    cards = [{"index": 0, "card_id": KAIKA}, {"index": 1, "card_id": MINNA}, {"index": 2, "card_id": BASIC}]
    d = _pd().decide(
        "SKILL_CARD_ENHANCE", GameState(produce=_prod(deck=[KAIKA, MINNA, BASIC]), page_fields={"cards": cards})
    )
    assert d.action.args.get("card_id") == MINNA and "攻略" in d.reason, d.reason


def test_prune_old_packs(tmp_path):
    for n in ("hif-20261119-3.json", "hif-20261120-5.json", "nia-20261119-1.json", "nia-20261120-7.json", "manifest.json", "hif-notes.txt",
              "idolroad-20261119-1.json"):  # fmt: skip
        (tmp_path / n).write_text("{}", encoding="utf-8")
    removed = guides.prune_old(tmp_path, {"hif": "hif-20261120-5", "nia": "nia-20261120-7"})
    assert removed == ["hif-20261119-3.json", "nia-20261119-1.json"]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [
        "hif-20261120-5.json",
        "hif-notes.txt",
        "idolroad-20261119-1.json",
        "manifest.json",
        "nia-20261120-7.json",
    ]


def test_fetch_downloads_hif_and_prunes(tmp_path, monkeypatch):
    import hashlib
    import urllib.request

    body = json.dumps({"schema": 1, "entries": []}).encode()
    man = {
        "schema": 1,
        "packs": {
            m: {"name": f"{m}-20261121-1", "sha256": hashlib.sha256(body).hexdigest()} for m in ("hajime", "nia", "hif")
        },
    }
    (tmp_path / "hif-20261120-5.json").write_text("{}", encoding="utf-8")

    class R:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: R(json.dumps(man).encode() if url.endswith("manifest") else body),
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    out = guides.fetch()
    assert out["hif"] == "hif-20261121-1" and out["removed"] == ["hif-20261120-5.json"]
    assert (tmp_path / "hif-20261121-1.json").exists() and not (tmp_path / "hif-20261120-5.json").exists()
    guides._load_pack.cache_clear()
