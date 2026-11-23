"""技能卡移动对话框（exec.exam._skill_card_move，照 KAA handle_skill_card_move 移植）：双击找到的最后一张、点 ✔，对话框消失 3 秒后结束。"""

from types import SimpleNamespace as NS

import hiro.exec.exam as ex
from hiro.vision.ocr import Box
from hiro.vision.ui_templates import Match


def _m(x):
    return Match(Box(x, 1080, x + 16, 1095), 0.9)


def _setup(monkeypatch, dialog_frames: int, letters: dict):
    t = [0.0]
    shots = [0]
    clicks, taps = [], []

    def screenshot():
        shots[0] += 1
        return shots[0]

    monkeypatch.setattr(
        ex, "device", NS(screenshot=screenshot, double_click=lambda x, y: clicks.append((x, y)), click=lambda *a: None)
    )
    monkeypatch.setattr(ex, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(ex.time, "monotonic", lambda: t[0])

    def loop():
        for _ in range(50):
            t[0] += 0.5
            yield None

    monkeypatch.setattr(ex, "Loop", loop)

    def tpl(name):
        if name == "InProduce.IconTitleSkillCardMove":
            return NS(exists=lambda img: img <= dialog_frames)
        return NS(find_all=lambda img: letters.get(name, []))

    monkeypatch.setattr(ex, "tpl", tpl)
    monkeypatch.setattr(ex, "tap", lambda name, img, click: taps.append(name) or True)
    return clicks, taps


def test_moves_last_found_card_and_confirms(monkeypatch):
    clicks, taps = _setup(monkeypatch, dialog_frames=2, letters={"InProduce.A": [_m(100)], "InProduce.M": [_m(300)]})
    assert ex._skill_card_move() is None
    assert clicks[0] == _m(300).center and taps[0] == "Common.IconButtonCheck"  # A、M 依次找，取最后一个（M）


def test_no_cards_returns_false(monkeypatch):
    clicks, taps = _setup(monkeypatch, dialog_frames=5, letters={})
    assert ex._skill_card_move() is False and clicks == [] and taps == []
