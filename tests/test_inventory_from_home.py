"""从首页开扫库存（初星助手「扫描支援卡 / 回忆」按钮）：进编成菜单 → 支援卡 / 回忆页 → 点第 1 格进选择列表。只点格子，不点決定。"""

import pytest

from hiro.produce import formation_menu as fm
from hiro.produce.formation_writer import FormationWriteError


class Dev:
    def __init__(self, opens_after: int | None):
        self.clicks, self.shots, self.opens_after = [], 0, opens_after

    def click(self, x, y):
        self.clicks.append((x, y))

    def shoot(self):
        self.shots += 1
        return self.shots

    def sleep(self, s):
        pass


@pytest.fixture(autouse=True)
def _menu(monkeypatch):
    seen = []
    monkeypatch.setattr(fm.Menu, "open", lambda self: seen.append("open") or {})
    monkeypatch.setattr(fm.Menu, "ensure_tab", lambda self, tab: seen.append(tab) or {})
    monkeypatch.setattr(fm.Menu, "__init__", lambda self, dev: None)
    return seen


def test_memory_picker_opens_from_first_tile(_menu):
    dev = Dev(opens_after=1)
    fm.open_picker(dev, "memory", ready=lambda img: img >= 1)
    assert _menu == ["open", "memory"] and dev.clicks == [fm.MEMORY_TILES[0]]


def test_support_list_uses_first_slot_center(monkeypatch, _menu):
    page = {"view": "formation", "slots": [{"center": (120, 640)}, {"center": (300, 640)}]}
    monkeypatch.setattr("hiro.produce.formation_writer._settled_step2", lambda dev, read, view: page)
    dev = Dev(opens_after=1)
    fm.open_picker(dev, "support", ready=lambda img: True, read_formation=lambda img: page)
    assert _menu == ["open", "support"] and dev.clicks == [(120, 640)]


def test_picker_never_opens_stops_without_more_taps(_menu):
    dev = Dev(opens_after=None)
    with pytest.raises(FormationWriteError, match="没打开"):
        fm.open_picker(dev, "memory", ready=lambda img: False)
    assert dev.clicks == [fm.MEMORY_TILES[0], fm.MEMORY_TILES[0]]  # 第一次 + 中途补点一次，不点别处
