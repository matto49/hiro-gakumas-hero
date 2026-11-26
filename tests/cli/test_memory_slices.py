"""回忆分片的存储和合并（设计 5.1 / D12 / D16 / D18）。"""

from hiro.cli.store import Store
from hiro.state import memory_slices as msl

import pytest


def _e(card, vi=64, nums=("15",)):
    return {"card": {"card_id": card, "upgrade": 0}, "stats": {"Vo": 0, "Da": 52, "Vi": vi, "hp": 0},
            "abilities": [{"text": f"初期ビジュアル上昇+{n}", "disabled": False} for n in nums]}  # fmt: skip


@pytest.fixture
def st(tmp_path):
    return Store(tmp_path / "hiro.db")


def test_slice_keys():
    assert len(msl.SLICES) == 12 and msl.slice_key("sense", "hif") == "sense:hif"
    assert msl.slices_for_plan("sense") == [
        "sense:hajime",
        "sense:nia",
        "sense:hif",
        "common:hajime",
        "common:nia",
        "common:hif",
    ]
    with pytest.raises(ValueError):
        msl.slice_key("vocal", "hif")


def test_full_read_replaces_slice_incremental_appends_new_only(st):
    r = st.save_slice("sense:hif", [_e("a"), _e("b")], mode="full", complete=True, context={"empty": True})
    assert r == {"read": 2, "added": 2}
    r = st.save_slice("sense:hif", [_e("c"), _e("a")], mode="incremental", complete=True, context={"empty": True})
    assert r == {"read": 2, "added": 1}
    assert sorted(e["card"]["card_id"] for e in st.slice_entries("sense:hif")) == ["a", "b", "c"]
    st.save_slice("sense:hif", [_e("a")], mode="full", complete=True)  # 全读：卖掉的 b、c 不在了
    assert [e["card"]["card_id"] for e in st.slice_entries("sense:hif")] == ["a"]
    cov = st.slice_coverage()["sense:hif"]
    assert cov["mode"] == "full" and cov["complete"] and cov["read"] == 1 and cov["full_at"]


def test_incomplete_full_read_does_not_drop_old_entries(st):
    """全读中途停了（complete=False）：没读到的不能当成卖掉了，只追加。"""
    st.save_slice("sense:hif", [_e("a"), _e("b")], mode="full", complete=True)
    st.save_slice("sense:hif", [_e("c")], mode="full", complete=False)
    assert sorted(e["card"]["card_id"] for e in st.slice_entries("sense:hif")) == ["a", "b", "c"]
    assert st.slice_coverage()["sense:hif"]["complete"] is False


def test_memories_for_plan_uses_slices_only_when_all_six_fully_read(st):
    st.save_snapshot("memories", [{"x": 1}], "scan:old", complete=True, raw={"entries": [_e("old")], "complete": True})
    for k in msl.slices_for_plan("sense")[:5]:
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    inv = st.memories_for_plan("sense")
    assert [e["card"]["card_id"] for e in inv["entries"]] == ["old"]  # 6 片没齐：整套用旧整表
    st.save_slice("common:hif", [_e("common:hif")], mode="full", complete=True)
    inv = st.memories_for_plan("sense")
    assert inv["source"] == "slices" and len(inv["entries"]) == 6
    assert {e["slice"] for e in inv["entries"]} == set(msl.slices_for_plan("sense"))


def test_slices_are_per_account(st):
    st.switch_account("A")
    st.save_slice("sense:hif", [_e("a")], mode="full", complete=True)
    st.switch_account("B")
    assert st.slice_entries("sense:hif") == [] and st.slice_coverage() == {}


def test_info_memories_reports_slice_coverage(st, monkeypatch):
    """设计 5.8：回忆信息里带片覆盖：12 片里全读过几片、最旧的扫描时间、有没有没扫完的（待补）。"""
    from hiro.cli import cmd_info

    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    st.save_snapshot("memories", [{"card": "old"}], "scan:old", complete=True, raw={"entries": [_e("old")]})
    st.save_slice("sense:hif", [_e("a")], mode="full", complete=True, scanned_at="2026-11-26 05:00:00")
    st.save_slice("logic:hif", [_e("b")], mode="full", complete=False, scanned_at="2026-11-26 04:00:00")
    out = cmd_info._memories({"timing": None, "card": None})
    assert out["slices"] == {"done": 1, "total": 12, "oldest": "2026-11-26 04:00:00", "pending": ["logic:hif"]}
    assert [r["card"] for r in out["items"]] == ["old"]  # 12 片没齐：列表照旧是整表快照


def test_info_memories_lists_slice_data_when_all_twelve_read(st, monkeypatch):
    from hiro.cli import cmd_info

    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    for k in msl.SLICES:
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    out = cmd_info._memories({"timing": None, "card": None})
    assert out["total"] == 12 and out["source"] == "slices" and out["slices"]["done"] == 12


def test_plan_formation_uses_slices_of_idol_plan(st, monkeypatch, run_cli, hiro_home):
    """D16：偶像卡流派的 3 片 + 共通 3 片都全读过，出方案就用片数据。"""
    from hiro.brain import formation_plan as fp
    from hiro.cli import cmd_plan

    monkeypatch.setattr(cmd_plan, "open_store", lambda: st)
    monkeypatch.setattr(msl, "enabled", lambda: True)  # 开了「回忆按片扫描」
    for k in msl.slices_for_plan("sense"):
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    st.save_snapshot("supports", [], "scan:x", complete=True, raw={"items": []})
    got = {}
    monkeypatch.setattr(fp, "idol_info", lambda idol, *a, **k: {"plan": "ProducePlanType_Plan1"})
    monkeypatch.setattr(fp, "build", lambda idol, **kw: got.update(kw) or {"idol": {"idol_card_id": idol}})
    monkeypatch.setattr(cmd_plan, "summarize", lambda plan: {})
    monkeypatch.setattr(fp, "format_report", lambda plan: "")
    cmd_plan._formation({"idol": "i_card-x", "support_set": None, "memory_set": None, "main_attr": None,
                         "fixed_main": False, "mode": None, "out": None})  # fmt: skip
    assert got["memory_inventory"]["source"] == "slices" and len(got["memory_inventory"]["entries"]) == 6


def _at(e, slot):
    return {**e, "slot": slot, "cell": [slot // 3, slot % 3]}


def test_incremental_puts_new_first_and_recomputes_positions(st):
    """终审 C1：入手排序下新回忆排在最上面，旧回忆整体往后挪；增量入库要把新的放前面、整片重算格子位置，
    否则写编成按旧的行 / 列找不到（新增数不是 3 的倍数时列也变了）。"""
    st.save_slice(
        "sense:hif", [_at(_e("a"), 0), _at(_e("b"), 1), _at(_e("c"), 2), _at(_e("d"), 3)], mode="full", complete=True
    )
    st.save_slice(
        "sense:hif",
        [_at(_e("n"), 0), _at(_e("a"), 1), _at(_e("b"), 2), _at(_e("c"), 3)],
        mode="incremental",
        complete=True,
    )
    got = [(e["card"]["card_id"], e["slot"], e["cell"]) for e in st.slice_entries("sense:hif")]
    assert got == [("n", 0, [0, 0]), ("a", 1, [0, 1]), ("b", 2, [0, 2]), ("c", 3, [1, 0]), ("d", 4, [1, 1])]


def test_slice_only_user_is_not_told_memories_never_scanned(st, monkeypatch):
    """终审 I7：新用户只按片扫过（没有整表快照）：概况里回忆要算扫过（时间取最近一次片扫描、数量是片里的份数），
    回忆列表列出已有的片数据（没齐 12 片时 complete=False）。"""
    from hiro.cli import cmd_info

    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    st.save_slice("sense:hif", [_e("a"), _e("b")], mode="full", complete=True, scanned_at="2026-11-26 05:00:00")
    st.save_slice("common:hif", [_e("c")], mode="full", complete=True, scanned_at="2026-11-26 06:00:00")
    mem = cmd_info._summary({})["kinds"]["memories"]
    assert mem["scanned_at"] == "2026-11-26 06:00:00" and mem["count"] == 3 and mem["complete"] is False
    out = cmd_info._memories({"timing": None, "card": None})
    assert out["source"] == "slices" and out["total"] == 3 and out["complete"] is False and len(out["items"]) == 3


def test_slices_switch_from_env_or_settings(monkeypatch, hiro_home):
    import json as _json

    monkeypatch.delenv("HIRO_MEMORY_SLICES", raising=False)
    assert msl.enabled()  # D10（用户 11-26 18:14 定）：缺省开，没有设置文件也开
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(_json.dumps({"memory_slices_off": True}), encoding="utf-8")
    assert not msl.enabled()  # 玩家在设置里关了
    (hiro_home / "settings.json").write_text(_json.dumps({"memory_slices": True}), encoding="utf-8")
    assert msl.enabled()  # 旧设置文件（缺省关时勾上写的 memory_slices）照样是开
    monkeypatch.setenv("HIRO_MEMORY_SLICES", "0")
    assert not msl.enabled()


def test_plan_uses_whole_list_when_slices_switch_off(st, monkeypatch):
    """开关关着时出方案照旧用整表快照，即使资料库里有片数据。"""
    from hiro.cli import cmd_plan

    for k in msl.slices_for_plan("sense"):
        st.save_slice(k, [_e(k)], mode="full", complete=True)
    st.save_snapshot("memories", [{"x": 1}], "scan:old", complete=True, raw={"entries": [_e("old")], "complete": True})
    monkeypatch.delenv("HIRO_MEMORY_SLICES", raising=False)
    monkeypatch.setattr(msl, "enabled", lambda: False)
    assert [e["card"]["card_id"] for e in cmd_plan.memory_inventory(st, "sense")["entries"]] == ["old"]
    monkeypatch.setattr(msl, "enabled", lambda: True)
    assert cmd_plan.memory_inventory(st, "sense")["source"] == "slices"


def test_merge_with_different_context_does_not_duplicate(st):
    """#116 第二轮审查重要 3：库里是从空编成读的（属性是本身的），这次增量 / 没读完的读数带偏移（不是空编成）：签名对不上，
    合并会把同一批回忆重复入库、还改写上下文。上下文不同又不是完整全读时不合并，旧数据和上下文不动，标成下次要全读。"""
    st.save_slice("sense:hif", [_e("a", vi=64)], mode="full", complete=True, context={"empty": True})
    r = st.save_slice("sense:hif", [_e("a", vi=79)], mode="incremental", complete=True, context={"empty": False})
    assert r["added"] == 0
    assert [e["stats"]["Vi"] for e in st.slice_entries("sense:hif")] == [64]
    cov = st.slice_coverage()["sense:hif"]
    # 不合并：旧数据、上下文、完整全读时间都保留（#116 第三轮：清掉 full_at 会让好好的片用不了），只标最近一次没读完
    assert cov["context"] == {"empty": True} and cov["full_at"] and cov["complete"] is False
    # 完整全读照旧整片替换（换成这次的上下文）
    st.save_slice("sense:hif", [_e("a", vi=79)], mode="full", complete=True, context={"empty": False})
    assert st.slice_coverage()["sense:hif"]["context"] == {"empty": False}


def test_offset_contexts_compared_by_formation_and_cards(st):
    """#116 第三轮：两次都带偏移读（empty 都是 False）时，编成号或其余几格不同，偏移就不同：同样不合并、上下文不改。"""
    a = {"empty": False, "mode": "hajime", "formation_set": 10, "cards": ["x", "y", "z"]}
    st.save_slice("sense:hif", [_e("a", vi=79)], mode="full", complete=True, context=a)
    r = st.save_slice(
        "sense:hif", [_e("a", vi=90)], mode="incremental", complete=True, context={**a, "formation_set": 11}
    )
    assert r["added"] == 0 and st.slice_coverage()["sense:hif"]["context"] == a
    r = st.save_slice(
        "sense:hif", [_e("a", vi=90)], mode="incremental", complete=True, context={**a, "cards": ["x", "y", "w"]}
    )
    assert r["added"] == 0
    r = st.save_slice("sense:hif", [_e("b", vi=79)], mode="incremental", complete=True, context=dict(a))
    assert r["added"] == 1  # 同一个上下文照常合并


def test_newer_whole_snapshot_beats_older_slices(st, monkeypatch):
    """#173 review：按片扫描失败后改用整表扫描（N43），新整表不能被以前全读过的旧片遮住——出方案和「查回忆」都用更新的那份。"""
    from hiro.cli import cmd_info

    for k in msl.SLICES:
        st.save_slice(k, [_e(k)], mode="full", complete=True, scanned_at="2026-11-25 10:00:00")
    st.save_snapshot("memories", [{"card": "new"}], "collect:whole", scanned_at="2026-11-26 19:30:00", complete=True,
                     raw={"entries": [_e("new")], "complete": True, "scanned_at": "2026-11-26 19:30:00"})  # fmt: skip
    inv = st.memories_for_plan("sense")
    assert inv.get("source") != "slices" and [e["card"]["card_id"] for e in inv["entries"]] == ["new"]
    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    out = cmd_info._memories({"timing": None, "card": None})
    assert out.get("source") != "slices"
    # 之后又把这几片重新全读了：片比整表新，用片
    for k in msl.SLICES:
        st.save_slice(k, [_e(k)], mode="full", complete=True, scanned_at="2026-11-27 09:00:00")
    assert st.memories_for_plan("sense")["source"] == "slices"
    assert cmd_info._memories({"timing": None, "card": None})["source"] == "slices"


def test_incomplete_slice_rescan_does_not_bring_back_deleted_memory(st, monkeypatch):
    """#173 review：整表里已经没有 A；之后某片重扫没读完（scanned_at 更新、full_at 不变），不能改用片数据把 A 带回来。"""
    from hiro.cli import cmd_info

    for k in msl.SLICES:
        st.save_slice(
            k, [_e("A" if k == "sense:hif" else k)], mode="full", complete=True, scanned_at="2026-11-25 10:00:00"
        )
    st.save_snapshot("memories", [{"card": "B"}], "collect:whole", scanned_at="2026-11-26 19:30:00", complete=True,
                     raw={"entries": [_e("B")], "complete": True, "scanned_at": "2026-11-26 19:30:00"})  # fmt: skip
    for k in msl.SLICES:  # 每片都重扫了一遍但都没读完
        st.save_slice(k, [], mode="full", complete=False, scanned_at="2026-11-27 09:00:00")
    ids = [e["card"]["card_id"] for e in st.memories_for_plan("sense")["entries"]]
    assert "A" not in ids and ids == ["B"]
    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    assert cmd_info._memories({"timing": None, "card": None}).get("source") != "slices"
