"""回忆扫描入口挑空编成（设计 5.3）：资料库最新的编成快照里，当前模式下全空的回忆编成，取号最大的。"""

from hiro import memory_scan

ROWS = [
    {"mode": "hif", "kind": "memory", "set": 7, "filled": 4},
    {"mode": "hif", "kind": "memory", "set": 8, "filled": 0},
    {"mode": "hif", "kind": "memory", "set": 19, "filled": 0},
    {"mode": "hif", "kind": "memory", "set": 20, "filled": 4},
    {"mode": "hif", "kind": "support", "set": 18, "filled": 0},
    {"mode": "hajime", "kind": "memory", "set": 3, "filled": 0},
]


def test_pick_empty_set_prefers_current_mode():
    assert memory_scan.pick_empty_set(ROWS, "hif") == ("hif", 19)
    assert memory_scan.pick_empty_set(ROWS, "hajime") == ("hajime", 3)


def test_pick_empty_set_falls_back_to_another_mode():
    """11-26 实机：游戏停在「初」，「初」没有全空的回忆编成；回忆列表各模式一样，切到有空编成的模式去扫（扫完切回）。"""
    rows = [r for r in ROWS if r["mode"] != "hajime"]
    assert memory_scan.pick_empty_set(rows, "hajime") == ("hif", 19)
    assert memory_scan.pick_empty_set(rows, None) == ("hif", 19)


def test_pick_empty_set_none_when_all_full():
    assert memory_scan.pick_empty_set([r for r in ROWS if r["filled"]], "hif") is None


def test_main_exits_formation_changed_even_if_scan_raises(monkeypatch, tmp_path):
    """#116 第二轮审查次要：选择页关了的标记打上后又抛异常（比如存盘出错），退出码也要是 FORMATION_CHANGED_EXIT，
    不然开局前补扫当成普通失败、照样开局。"""
    import sys

    from hiro.produce import memory_picker

    class Dev:
        def screenshot(self):
            return None

        def tap(self, x, y):
            pass

        def swipe(self, *a, **k):
            pass

    monkeypatch.setattr(memory_scan, "open_tool_device", lambda: Dev())

    def boom(picker, out, **kw):
        picker.formation_may_have_changed = True
        raise OSError("存盘失败")

    monkeypatch.setattr(memory_scan, "scan", boom)
    monkeypatch.setattr(sys, "argv", ["memory_scan", "scan", "--no-calib", "--out", str(tmp_path / "o.json")])
    assert memory_scan.main() == memory_scan.FORMATION_CHANGED_EXIT
    del memory_picker


def test_scan_context_records_formation_cards_when_not_empty():
    """带偏移读时（没从空编成进），上下文要记模式、编成号和那套编成里的卡，入库时才比得出偏移是不是同一个（#116 第三轮）。"""
    rows = [{"mode": "hajime", "kind": "memory", "set": 10, "filled": 4, "cards": ["a", "b", "c", "d"]}]
    where = {"mode": "hajime", "set": 10, "used": None, "used_mode": None, "empty": False}
    assert memory_scan.scan_context(where, rows) == {
        "mode": "hajime",
        "formation_set": 10,
        "empty": False,
        "cards": ["a", "b", "c", "d"],
    }
    where = {"mode": "hajime", "set": 10, "used": 19, "used_mode": "hif", "empty": True}
    assert memory_scan.scan_context(where, rows) == {"mode": "hif", "formation_set": 19, "empty": True}
