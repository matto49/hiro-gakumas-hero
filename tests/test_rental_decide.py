"""租借：点「決定」后列表还开着就再点（11-22 DMM 新主循环：点了没反应，第 2 步「次へ」等 20 秒超时）。"""

from types import SimpleNamespace as NS

from hiro.produce import rental


def _t(text, y):
    return NS(text=text, rect=NS(center=NS(x=360, y=y)))


def test_decide_retapped_until_list_closes(monkeypatch):
    clicks, screens = [], [[_t("キャンセル", 1160), _t("決定", 1160)], [_t("次へ", 1090)]]
    monkeypatch.setattr(rental, "device", NS(click=lambda x, y: clicks.append((x, y)), screenshot=lambda: None))
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr("hiro.vision.ocr.screen_texts", lambda img: screens.pop(0) if len(screens) > 1 else screens[0])
    row = {"card": {"name": "x"}, "center": (360, 870), "player": "p"}
    assert rental._tap_and_decide(row, {"decide": (497, 1157)})
    assert clicks == [(360, 870), (497, 1157), (497, 1157)]  # 行 + 決定 ×2（第一次没反应）


def test_decide_not_repeated_when_list_closed(monkeypatch):
    clicks = []
    monkeypatch.setattr(rental, "device", NS(click=lambda x, y: clicks.append((x, y)), screenshot=lambda: None))
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr("hiro.vision.ocr.screen_texts", lambda img: [_t("次へ", 1090)])
    rental._tap_and_decide({"card": {"name": "x"}, "center": (360, 870)}, {})
    assert clicks == [(360, 870), (497, 1157)]
