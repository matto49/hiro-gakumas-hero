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


def test_pick_empty_set_takes_largest_empty_memory_set_of_mode():
    assert memory_scan.pick_empty_set(ROWS, "hif") == 19
    assert memory_scan.pick_empty_set(ROWS, "hajime") == 3


def test_pick_empty_set_none_when_all_full_or_unknown():
    assert memory_scan.pick_empty_set([r for r in ROWS if r["filled"]], "hif") is None
    assert memory_scan.pick_empty_set(ROWS, "nia") is None
    assert memory_scan.pick_empty_set(ROWS, None) is None
