"""新主循环补两处 H.I.F 也会撞上的缺口（协调 11-17，页面清单 P7b / O12）：
OUTING_STAMINA_MAX（体力已满确认）点「選択する」继续；INITIAL_CHOICE（事件选项页）走 EVENT_CHOICE 决策。"""

import os
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.produce import hif
from hiro.vision.ocr import Box, Text

import pytest


def T(text, x, y):
    return Text(text, Box(x - 40, y - 15, x + 40, y + 15))


def _core(monkeypatch, texts):
    clicks, notes = [], []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: "img", click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "screen_texts", lambda img: texts)
    core = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    core.s = NS(note=notes.append)

    def stop(page, why, **k):
        raise RuntimeError(why)

    core._stop = stop
    return core, clicks, notes


def test_outing_stamina_max_continues_with_select(monkeypatch):
    texts = [
        T("体力が最大なので回復効果は発動しません。", 295, 1021),
        T("✕キャンセル", 211, 1161),
        T("く", 420, 1159),
        T("選択する", 512, 1160),
    ]
    core, clicks, notes = _core(monkeypatch, texts)
    core.on_outing_stamina_max()
    assert clicks == [(512, 1160)] and "体力已满" in notes[0]


def test_outing_stamina_max_without_button_stops(monkeypatch):
    core, clicks, _ = _core(monkeypatch, [T("✕キャンセル", 211, 1161)])
    with pytest.raises(RuntimeError, match="選択する"):
        core.on_outing_stamina_max()
    assert clicks == []


def test_outing_stamina_max_real_frame_classified_and_button_read():
    """KAA 的样例帧（in_produce/screenshot_outing_2.png）：判页 OUTING_STAMINA_MAX，「選択する」在底部右侧。"""
    from hiro.vision.classify import classify
    from hiro.vision.ocr import screen_texts

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "outing-stamina-max-kaa.png"
    )
    if not path.exists():
        pytest.skip("需要样例帧 pages/outing-stamina-max-kaa.png")
    img = cv2.resize(cv2.imread(str(path)), (720, 1280))
    assert classify(img).name == "OUTING_STAMINA_MAX"
    btn = next(t for t in screen_texts(img) if hif.button_text(t.text, "選択する"))
    assert btn.rect.center.y > 1080 and btn.rect.center.x > 360


def test_event_choice_goes_through_event_choice_decider(monkeypatch):
    core = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    seen = {}
    core._event_options = lambda: ["a", "b"]
    core._event_page = lambda page, commit, n: seen.update(page=page, n=n)
    core.on_event_choice()
    assert seen == {"page": "EVENT_CHOICE", "n": 2}
