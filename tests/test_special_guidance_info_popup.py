"""特別指導里弹出的支援事件效果说明框（只有「OK」）要点掉继续（11-19 Windows MuMu：盖住页面，进不了菜单、找不到「終了」，停机）。"""

from hiro.produce.special_guidance import dismiss_info_popup


class _R:
    def __init__(self):
        self.taps = []

    def tap(self, xy):
        self.taps.append(xy)


def _box(text, x1, y1, x2, y2):
    return (text, 1.0, (x1, y1, x2, y2))


POPUP = [
    _box("サポートイベント効果", 60, 921, 377, 954),
    _box("効果による影響がないため、イべント効果は発揮されま", 60, 1007, 657, 1031),
    _box("せんでした。", 60, 1042, 191, 1069),
    _box("×", 251, 1141, 292, 1179),
    _box("0K", 360, 1145, 406, 1176),  # RapidOCR 把 O 读成 0
]


def test_taps_ok_on_support_event_info_popup():
    r = _R()
    assert dismiss_info_popup(r, POPUP) is True
    assert r.taps == [(383, 1160)]


def test_ignores_other_pages():
    r = _R()
    grid = [_box("カスタマイズ", 300, 1070, 420, 1096), _box("終了", 630, 1070, 676, 1096), _box("OK", 360, 1145, 406, 1176)]
    assert dismiss_info_popup(r, grid) is False  # 没有说明框的文字：不点
    assert dismiss_info_popup(r, POPUP[:3]) is False  # 有文字但没找到 OK：不乱点
    assert r.taps == []
