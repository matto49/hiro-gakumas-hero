"""第二十局开局：读整副时一览晚于 6 秒才弹出，读牌放弃后一览留在画面上，KAA 把「Pアイテム」标签判成
SELECT_PITEM → 决策「P 道具候选未识别」→ 停机。快速判页先认牌组一览并点「閉じる」。"""

from pathlib import Path

from hiro.vision.deck import is_deck_list
from hiro.vision.quick import DECK_LIST_CLOSE, quick_page

import cv2
import pytest

FRAME = Path(__file__).parent / "fixtures/deck_list/m20-deck-list-late.png"


def _img():
    return cv2.resize(cv2.imread(str(FRAME)), (720, 1280))


@pytest.mark.fixtures
def test_frame_is_deck_list():
    assert is_deck_list(_img())


@pytest.mark.fixtures
def test_quick_page_closes_hanging_deck_list():
    """快速判页先认牌组一览、点「閉じる」（原来测旧主循环 HiroController._quick_page，2026-11-24 改测 hiro.vision.quick）。"""
    hit = quick_page(_img())
    assert hit is not None and hit["page"] == "DECK_LIST" and hit["tap"] == DECK_LIST_CLOSE
