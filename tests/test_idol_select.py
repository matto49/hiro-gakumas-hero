"""编成第 1 步选偶像：hiro.vision.idols（偶像卡图库，和 KAA idols 库同一套描述子）+ hiro.produce.idol_select（翻页找卡）。
和 KAA idols_db 的对照（120 张，第一名和距离全一致）在 （作者本机，未发布）。"""

import cv2
import numpy as np
import pytest

from hiro.produce import idol_select
from hiro.vision import idols
from hiro.vision.ui_templates import box

pytestmark = pytest.mark.skipif(not idols._db()[0], reason="没有偶像卡图（KAA 公开数据 idol_cards/，HIRO_KAA_GAME_DATA）")


def _card(key):
    return cv2.imread(str(idols._idol_dir() / key))


def test_query_finds_itself():
    key = idols._db()[0][7]
    hit = idols.query(_card(key))
    assert hit is not None and hit[0] == key and hit[1] < 1e-3


def _overview(key, col=1):
    """合成一屏偶像一览：key 那张卡贴在第 col 列第一行，其余空白。"""
    img = np.full((1280, 720, 3), 235, np.uint8)
    x0, y0, w, h = box("Produce.BoxIdolOverviewIdols").xywh
    x = idol_select.COLS[col]
    img[y0 : y0 + idol_select.CARD_H, x : x + idol_select.CARD_W] = cv2.resize(_card(key), (idol_select.CARD_W, idol_select.CARD_H))
    return img


def test_locate_scrolls_to_top_then_finds_card():
    key = idols._db()[0][20]
    skin = key.rsplit("_", 1)[0]
    swipes = []
    hit = idol_select.locate(skin, shoot=lambda: _overview(key), swipe=lambda *a: swipes.append(a), sleep=lambda s: None)
    assert hit is not None and hit.x1 == idol_select.COLS[1]
    assert len(swipes) == idol_select.TOP_SWIPES  # 只回了顶部，第一屏就找到


def test_locate_gives_up_at_list_end():
    blank = np.full((1280, 720, 3), 235, np.uint8)
    swipes = []
    assert idol_select.locate("i_card-skin-none-0-000", shoot=lambda: blank, swipe=lambda *a: swipes.append(a), sleep=lambda s: None) is None
    assert len(swipes) == idol_select.TOP_SWIPES + 1  # 第二屏和第一屏一样 = 到底了
