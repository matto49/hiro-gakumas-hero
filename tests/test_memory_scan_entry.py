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
