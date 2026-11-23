"""偶像卡攻略的查询接口（hiro.brain.guides），格式按 gakumas-bench docs/guides.md 定稿 v1（schema 1）。"""

import json

from hiro.brain import guides as g

import pytest

FKTN, HIRO = "i_card-fktn-3-000", "i_card-shro-3-007"


@pytest.fixture(autouse=True)
def _traits(monkeypatch):
    traits = {FKTN: ("logic", "review"), HIRO: ("logic", "aggressive")}
    monkeypatch.setattr(g, "idol_traits", lambda idol: traits.get(g.norm_idol(idol), (None, None)))
    monkeypatch.delenv("HIRO_GUIDES", raising=False)
    monkeypatch.delenv("HIRO_GUIDES_HIRO", raising=False)


def E(id, scope, decision="card_pick", prefer=(), avoid=(), ev=("guide",), unless=None, why=""):
    content = {"prefer": list(prefer), "avoid": list(avoid), "why": why}
    if unless is not None:
        content["unless"] = unless
    return g.Entry(
        {
            "id": id,
            "kind": "written",
            "scope": scope,
            "decision": decision,
            "content": content,
            "evidence": [{"kind": k, "ref": f"{k}-ref", "n": 100} for k in ev],
        }
    )


def _ids(hits):
    return [h.entry.id for h in hits]


def test_scope_filter_and_order():
    """scope 每个已写字段都要相符；先按依据级别（guide = decision > ranking > live），同级按层（偶像卡 > 流派 > プラン > 难度 > 模式 > 通用）。"""
    es = [E("common", {}), E("mode", {"mode": "hajime"}), E("diff", {"mode": "hajime", "difficulty": "pro"}),
          E("plan", {"mode": "hajime", "plan": "logic"}), E("style", {"mode": "hajime", "style": "review"}),
          E("idol", {"mode": "hajime", "idol_card": FKTN}), E("stat-idol", {"mode": "hajime", "idol_card": FKTN}, ev=("ranking",)),
          E("decision-mode", {"mode": "hajime"}, ev=("decision",)),
          E("other-style", {"mode": "hajime", "style": "aggressive"}), E("nia", {"mode": "nia"}), E("master", {"mode": "hajime", "difficulty": "master"}),
          E("other-decision", {"mode": "hajime"}, decision="card_remove")]  # fmt: skip
    hits = g.select(es, mode="hajime", difficulty="pro", idol_card="i_card-skin-fktn-3-000", decision="card_pick")
    assert _ids(hits) == ["idol", "style", "plan", "diff", "mode", "decision-mode", "common", "stat-idol"]


def test_unless_and_pool():
    """unless.when 成立跳过；判不了 / 只有文字照用、带提醒；pool.requires 不满足或卡池不知道时不用。"""
    es = [E("skip", {}, unless={"when": {"deck_count": {"ids": ["a"], "gte": 2}}, "text": "已有 2 张"}),
          E("keep", {}, unless={"when": {"deck_count": {"ids": ["a"], "gte": 3}}}),
          E("unknown", {}, unless={"when": {"weird_pred": 1}, "text": "判不了"}),
          E("text-only", {}, unless={"text": "只有文字"}),
          g.Entry({"id": "pool", "scope": {"pool": {"requires": [{"id": "s_card-1", "min_limit": 4}]}}, "decision": "card_pick",
                   "content": {}, "evidence": [{"kind": "guide"}]})]  # fmt: skip
    ctx = g.Ctx(deck=["a", "a", "b"])
    hits = g.select(es, mode="hajime", difficulty="pro", idol_card=FKTN, decision="card_pick", ctx=ctx)
    assert {h.entry.id: h.note for h in hits} == {"keep": None, "unknown": "判不了", "text-only": "只有文字"}
    full = g.Pool(support={"s_card-1": (4, 60)})
    assert "pool" in _ids(
        g.select(es, mode="hajime", difficulty="pro", idol_card=FKTN, decision="card_pick", ctx=ctx, pool=full)
    )
    low = g.Pool(support={"s_card-1": (2, 60)})
    assert "pool" not in _ids(
        g.select(es, mode="hajime", difficulty="pro", idol_card=FKTN, decision="card_pick", ctx=ctx, pool=low)
    )


def test_predicates():
    ctx = g.Ctx(deck=["a", "b", "b"], p_point=80, stamina=10, week=5, pitems=["pitem_x"], next_exam="mid2")
    w = g.eval_when
    assert w({"all": [{"deck_has_all": ["a", "b"]}, {"deck_size_lte": 3}, {"p_point_lt": 100}]}, ctx) is True
    assert w({"any": [{"deck_has_any": ["z"]}, {"stamina_lt": 5}]}, ctx) is False
    assert w({"not": {"has_pitem": ["pitem_x"]}}, ctx) is False
    assert (
        w({"week_gte": 5}, ctx) is True
        and w({"next_exam": "final"}, ctx) is False
        and w({"deck_size_gte": 4}, ctx) is False
    )
    assert w({"p_point_lt": 1}, g.Ctx()) is None and w({"all": [{"p_point_lt": 100}, {"nope": 1}]}, ctx) is None


def test_merge_first_entry_wins():
    """同一张卡一条推荐、一条避开：排序在前的那条说了算。"""
    hits = [
        g.Hit(E("idol", {}, prefer=[{"id": "x", "role": "loop"}, "y"])),
        g.Hit(E("style", {}, prefer=["z"], avoid=["x", "w"])),
    ]
    m = g.merge(hits)
    assert [(i.id, i.side, i.entry.id) for i in m] == [
        ("x", "prefer", "idol"),
        ("y", "prefer", "idol"),
        ("z", "prefer", "style"),
        ("w", "avoid", "style"),
    ]
    assert m[0].fields["role"] == "loop"


def test_switches_and_pack(monkeypatch, tmp_path):
    """攻略包从缓存目录读；HIRO_GUIDES=0 全关；广要 HIRO_GUIDES_HIRO=1；schema 比自己新的包不用。"""
    pack = {
        "format": "gkbench.guides.v1",
        "schema": 1,
        "mode": "hajime",
        "version": "20261120.1",
        "entries": [E("x", {"mode": "hajime"}).raw],
    }
    (tmp_path / "hajime-20261120-1.json").write_text(json.dumps(pack), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 1, "packs": {"hajime": {"name": "hajime-20261120-1"}}}), encoding="utf-8"
    )
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    g._load_pack.cache_clear()
    q = {"mode": "hajime", "difficulty": "pro", "decision": "card_pick"}
    assert _ids(g.lookup(idol_card=FKTN, **q)) == ["x"]
    assert g.lookup(idol_card=HIRO, **q) == []
    monkeypatch.setenv("HIRO_GUIDES_HIRO", "1")
    assert _ids(g.lookup(idol_card=HIRO, **q)) == ["x"]
    monkeypatch.setenv("HIRO_GUIDES", "0")
    assert g.lookup(idol_card=FKTN, **q) == []
    monkeypatch.delenv("HIRO_GUIDES")
    pack["schema"] = 2
    (tmp_path / "hajime-20261120-1.json").write_text(json.dumps(pack), encoding="utf-8")
    g._load_pack.cache_clear()
    assert g.lookup(idol_card=FKTN, **q) == []
    g._load_pack.cache_clear()


def test_no_pack_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path / "none"))
    g._load_pack.cache_clear()
    assert g.lookup(mode="hajime", difficulty="pro", idol_card=FKTN, decision="card_pick") == []
    g._load_pack.cache_clear()


def test_refresh_if_stale(monkeypatch, tmp_path):
    """开局时：缓存没有 / 超过 24 小时才下载；下载失败只写日志、不抛；HIRO_GUIDES_FETCH=0 不联网。"""
    import os
    import time

    monkeypatch.setenv("HIRO_GUIDES_DIR", str(tmp_path))
    monkeypatch.delenv(
        "HIRO_GUIDES_FETCH", raising=False
    )  # conftest 默认关掉联网，这条要测下载这条路（fetcher 是假的）
    calls = []
    ok = lambda: calls.append(1) or {"hajime": "x"}  # noqa: E731

    def boom():
        raise OSError("network down")

    assert g.refresh_if_stale(fetcher=ok) == "fetched" and calls == [1]  # 没有缓存
    man = tmp_path / "manifest.json"
    man.write_text("{}", encoding="utf-8")
    assert g.refresh_if_stale(fetcher=ok) == "fresh" and calls == [1]  # 刚下过
    old = time.time() - 25 * 3600
    os.utime(man, (old, old))
    assert g.refresh_if_stale(fetcher=boom) == "failed"  # 过期、下载失败：不抛
    monkeypatch.setenv("HIRO_GUIDES_FETCH", "0")
    assert g.refresh_if_stale(fetcher=ok) == "off" and calls == [1]
    monkeypatch.setenv("HIRO_GUIDES", "0")
    monkeypatch.delenv("HIRO_GUIDES_FETCH")
    assert g.refresh_if_stale(fetcher=ok) == "off"
