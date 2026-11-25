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
    r = st.save_slice("sense:hif", [_e("c"), _e("a")], mode="incremental", complete=True)
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
