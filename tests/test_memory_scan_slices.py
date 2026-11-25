"""多片回忆扫描（设计 5.4 / D6 / D12）：选择页里逐片设筛选 → 扫 → 最后恢复筛选、退出；结果按片入库。"""

import json

from hiro import memory_scan
from hiro.cli.store import Store

import pytest


class P:
    def __init__(self):
        self.t, self.cancels, self.log = 0.0, 0, []

    def clock(self):
        return self.t

    def cancel(self):
        self.cancels += 1
        self.log.append("cancel")
        return True


@pytest.fixture
def fakes(monkeypatch):
    from hiro.produce import memory_filter, memory_picker

    p = P()
    monkeypatch.setattr(memory_filter, "read_state", lambda pk: (pk.log.append("read"), {"sort": "アイドル"})[1])
    monkeypatch.setattr(memory_filter, "apply_slice", lambda pk, k: pk.log.append(f"apply {k}"))
    monkeypatch.setattr(
        memory_filter, "restore", lambda pk, st, **kw: (pk.log.append(f"restore {st['sort']}"), True)[1]
    )

    def fake_scan(pk, out, *, known=None, deadline=None, close=True, **kw):
        assert close is False
        key = pk.log[-1].split()[-1]
        pk.log.append(f"scan {key} known={known is not None}")
        pk.t += 50
        stopped = "budget" if deadline is not None and pk.t >= deadline else None
        return {"entries": [{"card": {"card_id": key}}] * 2, "complete": stopped is None, "new": 1, "stopped": stopped}

    monkeypatch.setattr(memory_picker, "scan", fake_scan)
    return p


def test_scans_each_slice_then_restores_and_cancels(fakes, tmp_path):
    out = memory_scan.scan_slices(fakes, ["sense:hif", "common:hif"], tmp_path, known_for=lambda k: None)
    assert fakes.log == ["read", "apply sense:hif", "scan sense:hif known=False", "apply common:hif",
                         "scan common:hif known=False", "restore アイドル", "cancel"]  # fmt: skip
    assert [s["key"] for s in out["slices"]] == ["sense:hif", "common:hif"] and out["complete"] and out["restored"]
    assert out["slices"][0]["mode"] == "full"


def test_incremental_and_budget_leave_rest_pending(fakes, tmp_path):
    out = memory_scan.scan_slices(fakes, ["sense:hif", "common:hif", "sense:nia"], tmp_path,
                                  known_for=lambda k: object(), budget_s=60)  # fmt: skip
    assert [s["key"] for s in out["slices"]] == ["sense:hif", "common:hif"]
    assert out["slices"][0]["mode"] == "incremental" and out["slices"][1]["complete"] is False
    # 预算到了没读完的 common:hif 也留在待补里（终审 I3）
    assert out["pending"] == ["common:hif", "sense:nia"] and not out["complete"]
    assert fakes.log[-2:] == ["restore アイドル", "cancel"]


def test_import_saves_each_slice(tmp_path, monkeypatch):
    from hiro.cli import cmd_info

    st = Store(tmp_path / "hiro.db")
    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    inv = {"slices": [{"key": "sense:hif", "mode": "full", "complete": True, "entries": [{"card": {"card_id": "a"}}]},
                      {"key": "common:hif", "mode": "incremental", "complete": True, "entries": []}],
           "context": {"empty": True}, "scanned_at": "2026-11-26 05:00:00"}  # fmt: skip
    r = cmd_info.import_inventory("memories", inv, source="scan:x")
    assert r["count"] == 1 and r["slices"] == 2
    cov = st.slice_coverage()
    assert set(cov) == {"sense:hif", "common:hif"} and cov["sense:hif"]["context"] == {"empty": True}


def test_incremental_scan_that_reads_to_the_end_counts_as_full(tmp_path, monkeypatch):
    """第一次扫一片时资料库是空的，增量补扫不会因为「连续已知」停下、一路读到底：算全读（D16 才认这一片）。"""
    from hiro.cli import cmd_info

    st = Store(tmp_path / "hiro.db")
    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    inv = {"slices": [{"key": "sense:hif", "mode": "incremental", "complete": True, "stopped": None, "entries": [{"card": {"card_id": "a"}}]},
                      {"key": "common:hif", "mode": "incremental", "complete": True, "stopped": "known", "entries": []}]}  # fmt: skip
    cmd_info.import_inventory("memories", inv, source="scan:x")
    cov = st.slice_coverage()
    assert cov["sense:hif"]["full_at"] and cov["sense:hif"]["mode"] == "full"
    assert cov["common:hif"]["full_at"] is None and cov["common:hif"]["mode"] == "incremental"


def test_slice_rejected_when_card_plans_do_not_match(fakes, tmp_path):
    """终审 C2：筛选点错了（比如重置后其实是全不勾）读到的就不是这一片：按技能卡的流派核对，对不上的超过 1 格且超过一成就
    不入库、停下（照样恢复筛选、退出）。"""
    plans = {"sense:hif": "ProducePlanType_Plan2", "common:hif": "ProducePlanType_Common"}  # sense 片里全是 ロジック 卡
    out = memory_scan.scan_slices(fakes, ["sense:hif", "common:hif"], tmp_path, known_for=lambda k: None,
                                  plan_of=lambda cid: plans.get(cid))  # fmt: skip
    s = out["slices"][0]
    assert s["key"] == "sense:hif" and s["rejected"] and not out["complete"]
    assert out["pending"] == ["sense:hif", "common:hif"] and fakes.log[-2:] == ["restore アイドル", "cancel"]


def test_import_skips_rejected_slice(tmp_path, monkeypatch):
    from hiro.cli import cmd_info

    st = Store(tmp_path / "hiro.db")
    monkeypatch.setattr(cmd_info, "open_store", lambda: st)
    inv = {
        "slices": [
            {
                "key": "sense:hif",
                "mode": "full",
                "complete": True,
                "rejected": "流派对不上",
                "entries": [{"card": {"card_id": "a"}}],
            }
        ]
    }
    r = cmd_info.import_inventory("memories", inv, source="scan:x")
    assert r["slices"] == 0 and st.slice_coverage() == {}


def test_known_for_full_reads_unfinished_or_offset_slices(tmp_path):
    """终审 I3 / I4：这一片还没完整读过（之前被预算打断），或者上次是带偏移读的（没从空编成进），增量补扫读不全 / 签名对不上：
    这两种都全读；完整读过、属性是本身的才增量。"""
    st = Store(tmp_path / "hiro.db")
    st.save_slice("sense:hif", [{"card": {"card_id": "a"}}], mode="full", complete=True, context={"empty": True})
    st.save_slice(
        "logic:hif", [{"card": {"card_id": "b"}}], mode="incremental", complete=False, context={"empty": True}
    )
    st.save_slice("common:hif", [{"card": {"card_id": "c"}}], mode="full", complete=True, context={"empty": False})
    assert memory_scan.known_for_slice(st, "sense:hif") is not None
    assert memory_scan.known_for_slice(st, "logic:hif") is None
    assert memory_scan.known_for_slice(st, "common:hif") is None
    assert memory_scan.known_for_slice(st, "anomaly:hif") is None


def test_finished_slices_handed_out_before_a_later_slice_fails(fakes, tmp_path, monkeypatch):
    """终审 I2：第 2 片出错时，第 1 片已经交出去（main 每片落盘，任务失败也能入库），筛选照样恢复。"""
    from hiro.produce import memory_filter

    def apply(pk, k):
        pk.log.append(f"apply {k}")
        if k == "common:hif":
            raise memory_filter.MemoryFilterError("弹窗没出来")

    monkeypatch.setattr(memory_filter, "apply_slice", apply)
    got = []
    with pytest.raises(memory_filter.MemoryFilterError):
        memory_scan.scan_slices(fakes, ["sense:hif", "common:hif"], tmp_path, known_for=lambda k: None,
                                plan_of=lambda cid: None, on_slice=got.append)  # fmt: skip
    assert [r["key"] for r in got] == ["sense:hif"] and fakes.log[-2:] == ["restore アイドル", "cancel"]


def test_progress_writer_saves_after_each_slice(tmp_path):
    out = tmp_path / "out.json"
    w = memory_scan.slices_progress_writer(out, {"empty": True})
    w({"key": "sense:hif", "entries": [], "complete": True})
    first = json.loads(out.read_text(encoding="utf-8"))
    w({"key": "common:hif", "entries": [], "complete": True})
    second = json.loads(out.read_text(encoding="utf-8"))
    assert [s["key"] for s in first["slices"]] == ["sense:hif"] and len(second["slices"]) == 2
    assert second["context"] == {"empty": True} and second["partial"] is True


def test_restore_failure_closes_popup_before_leaving(fakes, tmp_path, monkeypatch):
    from hiro.produce import memory_filter

    def bad_restore(pk, st, **kw):
        pk.log.append("restore")
        raise memory_filter.MemoryFilterError("找不到入手")

    monkeypatch.setattr(memory_filter, "restore", bad_restore)
    monkeypatch.setattr(memory_filter, "close_popup", lambda pk: (pk.log.append("close_popup"), True)[1])
    out = memory_scan.scan_slices(fakes, ["sense:hif"], tmp_path, known_for=lambda k: None, plan_of=lambda c: None)
    assert fakes.log[-3:] == ["restore", "close_popup", "cancel"] and out["restored"] is False


def test_empty_slice_recorded_as_full_zero_and_scan_continues(fakes, tmp_path, monkeypatch):
    from hiro.produce import memory_filter

    def apply(pk, k):
        pk.log.append(f"apply {k}")
        if k == "common:hif":
            raise memory_filter.EmptySlice("条件に当てはまるメモリーがありません")

    monkeypatch.setattr(memory_filter, "apply_slice", apply)
    out = memory_scan.scan_slices(
        fakes, ["common:hif", "sense:hif"], tmp_path, known_for=lambda k: None, plan_of=lambda c: None
    )
    first, second = out["slices"]
    assert first == {
        "key": "common:hif",
        "mode": "full",
        "complete": True,
        "entries": [],
        "new": 0,
        "stopped": None,
        "empty": True,
    }
    assert second["key"] == "sense:hif" and out["pending"] == [] and out["complete"]


def test_picker_closed_mid_slice_stops_and_touches_nothing(fakes, tmp_path, monkeypatch):
    """#116 审查严重 1：某一片扫描中选择页关了（scan 打上 formation_may_have_changed）：立即停，不设下一片、不恢复、不关弹窗、
    不点キャンセル（保留现场给人核对），标记交给调用方。"""
    from hiro.produce import memory_filter, memory_picker

    monkeypatch.setattr(memory_filter, "close_popup", lambda pk: (pk.log.append("close_popup"), True)[1])

    def closing_scan(pk, out, **kw):
        pk.log.append("scan")
        pk.formation_may_have_changed = True
        return {"entries": [{"card": {"card_id": "x"}}], "complete": False, "new": 1, "stopped": None,
                "formation_may_have_changed": True}  # fmt: skip

    monkeypatch.setattr(memory_picker, "scan", closing_scan)
    out = memory_scan.scan_slices(
        fakes, ["sense:hif", "common:hif"], tmp_path, known_for=lambda k: None, plan_of=lambda c: None
    )
    assert fakes.log == ["read", "apply sense:hif", "scan"]
    assert out["formation_may_have_changed"] is True and not out["complete"] and out["restored"] is False


def test_incremental_after_unfinished_scan_reads_full(tmp_path):
    """#116 第二轮审查重要 2：完整读过，但最近一次增量被预算打断（complete=False）：下一次要全读，不然顶部 3 格已知就停、
    中断处以下的新回忆永远补不进来。当前编成上下文和库里的不同（一个空编成、一个不是）也要全读。"""
    st = Store(tmp_path / "hiro.db")
    st.save_slice("sense:hif", [{"card": {"card_id": "a"}}], mode="full", complete=True, context={"empty": True})
    assert memory_scan.known_for_slice(st, "sense:hif") is not None
    st.save_slice(
        "sense:hif", [{"card": {"card_id": "b"}}], mode="incremental", complete=False, context={"empty": True}
    )
    assert memory_scan.known_for_slice(st, "sense:hif") is None
    st.save_slice("logic:hif", [{"card": {"card_id": "c"}}], mode="full", complete=True, context={"empty": True})
    assert memory_scan.known_for_slice(st, "logic:hif", empty=False) is None


def test_filter_reset_reported_only_when_it_happened(fakes, tmp_path, monkeypatch):
    """#116 第二轮审查（第一轮 4 的另一半）：restore 出错、没点到重置时，不能说「筛选已重置」。"""
    from hiro.produce import memory_filter

    def bad_restore(pk, st, **kw):
        raise memory_filter.MemoryFilterError("找不到入手")

    monkeypatch.setattr(memory_filter, "restore", bad_restore)
    monkeypatch.setattr(memory_filter, "close_popup", lambda pk: True)
    out = memory_scan.scan_slices(fakes, ["sense:hif"], tmp_path, known_for=lambda k: None, plan_of=lambda c: None)
    assert out["filter_reset"] is False and out["restored"] is False

    def good_restore(pk, st, report=None):
        if report is not None:
            report["filter_reset"] = True
        return True

    monkeypatch.setattr(memory_filter, "restore", good_restore)
    out = memory_scan.scan_slices(fakes, ["sense:hif"], tmp_path, known_for=lambda k: None, plan_of=lambda c: None)
    assert out["filter_reset"] is True and out["restored"] is True
