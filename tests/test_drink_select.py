"""选饮料三选一的提交（card_ops.drink_select_commit，替换 KAA DrinkSelectContext.commit）。"""

from types import SimpleNamespace as NS

from hiro.produce import card_ops
from hiro.vision.ocr import Box
from hiro.vision.ui_templates import Match

import pytest


def _setup(monkeypatch, found: dict):
    clicks = []
    monkeypatch.setattr(card_ops, "device", NS(screenshot=lambda: None, click=lambda x, y: clicks.append((x, y))))

    def tpl(name):
        seq = found.get(name, [])
        return NS(find=lambda img: seq.pop(0) if seq else None, exists=lambda img: bool(seq))

    monkeypatch.setattr("hiro.vision.ui_templates.tpl", tpl)
    return clicks


def _m(x, y):
    return Match(Box(x - 10, y - 10, x + 10, y + 10), 0.9)


def test_pick_taps_drink_then_acquire_until_button_gone(monkeypatch):
    clicks = _setup(monkeypatch, {"InProduce.AcquireBtnDisabled": [_m(360, 1080)]})  # 第一次点完按钮还灰，第二次没了
    assert card_ops.drink_select_commit(1, sleep=lambda s: None) is True
    assert clicks == [(360, 884), (360, 1080), (360, 884)]


def test_skip_taps_dont_claim_then_button(monkeypatch):
    clicks = _setup(
        monkeypatch, {"InProduce.TextDontClaim": [_m(359, 1006)], "InProduce.ButtonDontClaim": [_m(357, 1096)]}
    )
    assert card_ops.drink_select_commit(None, sleep=lambda s: None) is True
    assert clicks == [(359, 1006), (357, 1096)]


def test_pick_gives_up_after_max_tries(monkeypatch):
    clicks = _setup(monkeypatch, {"InProduce.AcquireBtnDisabled": [_m(360, 1080)] * 20})
    assert card_ops.drink_select_commit(0, sleep=lambda s: None) is False
    assert len(clicks) == 2 * card_ops.DRINK_SELECT_MAX_TRIES
    with pytest.raises(ValueError):
        card_ops.drink_select_commit(3, sleep=lambda s: None)


def test_card_select_commit_taps_letter_then_acquire(monkeypatch):
    clicks = _setup(monkeypatch, {"InProduce.AcquireBtnDisabled": [_m(360, 1080)]})
    assert card_ops.card_select_commit(Box(200, 940, 216, 955), sleep=lambda s: None) is True
    assert clicks == [(208, 947), (360, 1080), (208, 947)]


def test_badge_match_below_and_within():
    from hiro.vision.card_select import badge_match

    cards = [Box(100, 900, 120, 920), Box(300, 900, 320, 920)]
    badges = [Box(290, 960, 330, 980), Box(95, 960, 125, 980), Box(500, 960, 520, 980)]  # 第 3 个不在任何卡下面
    assert badge_match(cards, badges) == [Box(95, 960, 125, 980), Box(290, 960, 330, 980)]
    assert badge_match(cards, [Box(100, 880, 120, 890)]) == [None, None]  # 在上面不算
