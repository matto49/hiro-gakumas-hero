"""设置检查只在首页做（DMM 实机：-MainOnly 从本战开始确认页起时，
check_settings 点 MENU 没反应后盲点 HOME (63,1213)，正好是开始确认页左下的「ホーム」，弹出 ホーム移動確認，
接着读选拔回忆数值被挡住，本战 33 秒就停了）。OCR 框取自 DMM 实机帧（Vision / RapidOCR 各一份，随仓库发布）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
BOXES = json.loads((Path(__file__).parent / "data" / "home-start-confirm-ocr-boxes.json").read_text(encoding="utf-8"))
MENU_BOXES = json.loads((Path(__file__).parent / "data" / "g33-ocr-boxes.json").read_text(encoding="utf-8"))


def _boxes(src, key):
    return [(t, c, tuple(b)) for t, c, b in src[key]]


@pytest.mark.synthetic
@pytest.mark.parametrize("engine", ["vision", "rapidocr"])
def test_is_home_only_on_home(engine):
    from check_settings import is_home

    assert is_home(_boxes(BOXES, f"home/dmm/{engine}"))
    assert not is_home(_boxes(BOXES, f"start_confirm/dmm-main/{engine}"))
    assert not is_home(_boxes(MENU_BOXES, "settings/menu-g33"))
    assert not is_home(_boxes(MENU_BOXES, "settings/03-produce-01"))


class _Dev:
    def __init__(self):
        self.clicks = []

    def screenshot(self):
        return object()  # 非 None 占位；OCR 由测试替身给

    def tap(self, x, y):
        self.clicks.append((x, y))

    def swipe(self, *a):
        pass


def _run_check(monkeypatch, boxes):
    import hiro.tool_device
    import hiro.vision.ocr

    import check_settings as cs

    dev = _Dev()
    monkeypatch.setattr(hiro.tool_device, "open_tool_device", lambda: dev)
    monkeypatch.setattr(hiro.vision.ocr, "ocr_boxes", lambda img, scale=1.0: boxes)
    monkeypatch.setattr(cs, "_wait", lambda d, ocr, pred, timeout, poll=1.0: pred(boxes))
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    return dev, cs.check(None)


@pytest.mark.synthetic
@pytest.mark.parametrize("engine", ["vision", "rapidocr"])
def test_check_on_start_confirm_clicks_nothing(monkeypatch, engine):
    from check_settings import HOME, verdict

    dev, found = _run_check(monkeypatch, _boxes(BOXES, f"start_confirm/dmm-main/{engine}"))
    assert dev.clicks == [] and HOME not in dev.clicks
    assert verdict(found).startswith("跳过")


@pytest.mark.synthetic
def test_check_menu_missing_does_not_tap_home(monkeypatch):
    """首页上点 MENU 但菜单没出来（画面还是首页）：存图返回，不再盲点 HOME。"""
    from check_settings import HOME, MENU, verdict

    dev, found = _run_check(monkeypatch, _boxes(BOXES, "home/dmm/vision"))
    assert dev.clicks == [MENU] and HOME not in dev.clicks
    assert verdict(found).startswith("读不到")
