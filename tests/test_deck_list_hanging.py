# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""第二十局开局：读整副时一览晚于 6 秒才弹出，读牌放弃后一览留在画面上，KAA 把「Pアイテム」标签判成
SELECT_PITEM → 决策「P 道具候选未识别」→ 停机。主循环快速判页先认牌组一览并点「閉じる」。"""

from pathlib import Path
from types import SimpleNamespace as NS

import hiro  # noqa: F401
from hiro.loop.controller import DECK_LIST_CLOSE, HiroController
from hiro.vision.deck import is_deck_list

import cv2
import pytest

FRAME = Path(__file__).parent / "fixtures/deck_list/m20-deck-list-late.png"


def _img():
    return cv2.resize(cv2.imread(str(FRAME)), (720, 1280))


@pytest.mark.fixtures
def test_frame_is_deck_list():
    assert is_deck_list(_img())


@pytest.mark.fixtures
def test_quick_page_closes_hanging_deck_list(monkeypatch):
    import kotonebot

    clicks = []
    monkeypatch.setattr(kotonebot, "device", NS(screenshot=_img, click=lambda *a: clicks.append(a)))
    c = HiroController.__new__(HiroController)
    assert c._quick_page() == "handled"
    assert clicks == [DECK_LIST_CLOSE]
