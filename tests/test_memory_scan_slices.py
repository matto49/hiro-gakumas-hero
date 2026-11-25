"""多片回忆扫描（设计 5.4 / D6 / D12）：选择页里逐片设筛选 → 扫 → 最后恢复筛选、退出；结果按片入库。"""

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
    monkeypatch.setattr(memory_filter, "restore", lambda pk, st: (pk.log.append(f"restore {st['sort']}"), True)[1])

    def fake_scan(pk, out, *, known=None, deadline=None, close=True, **kw):
        assert close is False
        key = pk.log[-1].split()[-1]
        pk.log.append(f"scan {key} known={known is not None}")
        pk.t += 50
        stopped = "budget" if deadline is not None and pk.t >= deadline else None
        return {"entries": [{"card": {"card_id": key}}], "complete": stopped is None, "new": 1, "stopped": stopped}

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
    assert out["pending"] == ["sense:nia"] and not out["complete"]
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
