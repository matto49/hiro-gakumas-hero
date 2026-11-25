"""卡名 OCR 全角半角：回忆详情把「200%スマイル+」读成「200％スマイル＋」（账号 2 回忆库 11-24 两份 card_id 为空）。"""

from hiro.vision import deck

import pytest

pytestmark = pytest.mark.synthetic


@pytest.fixture
def names(monkeypatch):
    table = {
        "200%スマイル": [("p_card-02-act-3_001", 0)],
        "200%スマイル+": [("p_card-02-act-3_001", 1)],
        "200%スマイル++": [("p_card-02-act-3_001", 2)],
        "届いて！": [("p_card-x", 0)],
    }
    monkeypatch.setattr(deck, "_cards_by_full_name", lambda: table)
    deck._cards_by_dash_name.cache_clear()
    yield
    deck._cards_by_dash_name.cache_clear()


def test_fullwidth_percent_and_plus(names):
    assert deck.card_from_name("200％スマイル＋") == {
        "card_id": "p_card-02-act-3_001",
        "name": "200%スマイル",
        "upgrade": 1,
    }


def test_halfwidth_exclamation_still_matches(names):
    assert deck.card_from_name("届いて!")["card_id"] == "p_card-x"
