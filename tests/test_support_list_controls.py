"""N40（G2 11-26 Mac rc.7）：扫支援卡翻页时那一帧读空（Vision 刚坏、换 RapidOCR），controls 是 None，
直接 r["controls"]["next"] 崩了。现在缺按钮先重截重读，还读不到就好好停下、已认出的照样返回。"""

import importlib.util
import sys
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parents[1] / "tools" / "support_inventory.py"
    spec = importlib.util.spec_from_file_location("support_inventory_t", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Dev:
    def __init__(self):
        self.taps = []

    def screenshot(self):
        return None

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *a, **k):
        pass


def _run(monkeypatch, reads):
    m = _load()
    dev = Dev()
    it = iter(reads)
    monkeypatch.setitem(sys.modules, "hiro.tool_device", type(sys)("td"))
    sys.modules["hiro.tool_device"].LIST_HOLD_S = 0
    sys.modules["hiro.tool_device"].open_tool_device = lambda: dev
    import hiro.vision.support_screens as ss

    monkeypatch.setattr(ss, "read_support_list", lambda img: next(it))
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    return m.list_scan(), dev


CTRL = {"next": (600, 1100), "cancel": (200, 1200)}


def page(cards, controls=CTRL, pages=2):
    return {"pages": pages, "cards": [{"support_card_id": c, "name": c, "level": 1, "breaks": 0, "partial": False} for c in cards],
            "controls": controls}  # fmt: skip


def test_missing_controls_are_reread(monkeypatch):
    reads = [page(["a"]), page(["a"]), page([]), page([], None), page([]),  # 第 1 页：两屏没新卡；翻页前那帧读空，重读后有按钮
             page(["b"]), page(["b"]), page(["b"])]  # fmt: skip
    _screens, dev = _run(monkeypatch, reads)
    assert (600, 1100) in dev.taps and dev.taps[-1] == (200, 1200)


def test_controls_never_read_stops_cleanly(monkeypatch, capsys):
    reads = [page(["a"]), page(["a"]), page([], None)] + [page([], None)] * 10
    screens, dev = _run(monkeypatch, reads)
    assert dev.taps == [] and screens
    assert "读不到翻页按钮" in capsys.readouterr().out
