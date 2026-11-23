"""选偶像的编号比较：图库文件名是 i_card-skin-…，资料库 / 初星助手传的是 i_card-…，两种写法都要认（不依赖偶像卡图片）。
11-23 Mac 账号 2：Campus 広 在第 2 页匹配距离 6.1（阈值 12），但传进来的是 i_card-shro-3-007，startswith 不成立，翻了 20 页报「找不到」。"""

from hiro.produce import idol_select

import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


@pytest.mark.parametrize("wanted", ["i_card-shro-3-007", "i_card-skin-shro-3-007"])
def test_find_on_page_accepts_card_id_with_or_without_skin(monkeypatch, wanted):
    monkeypatch.setattr(idol_select.idols, "query", lambda crop, threshold: ("i_card-skin-shro-3-007_1.png", 6.1))
    hit = idol_select.find_on_page(np.zeros((1280, 720, 3), np.uint8), wanted)
    assert hit is not None and hit.x1 == idol_select.COLS[0]


def test_find_on_page_does_not_match_other_card(monkeypatch):
    monkeypatch.setattr(idol_select.idols, "query", lambda crop, threshold: ("i_card-skin-shro-3-001_1.png", 3.0))
    assert idol_select.find_on_page(np.zeros((1280, 720, 3), np.uint8), "i_card-shro-3-00") is None  # 不能只比前缀


@pytest.mark.parametrize("wanted", ["i_card-shro-3-007", "i_card-skin-shro-3-007"])
def test_opening_idol_selected_accepts_card_id_with_or_without_skin(monkeypatch, wanted):
    """第 1 步核对「已选偶像」框（opening.idol_selected）也要认不带 skin- 的编号：11-23 找到卡点上去后核对一直不过，选了 5 次报错。"""
    from types import SimpleNamespace

    from hiro.runtime import opening

    monkeypatch.setattr(idol_select.idols, "query", lambda crop, threshold: ("i_card-skin-shro-3-007_1.png", 5.0))
    f = SimpleNamespace(img=np.zeros((1280, 720, 3), np.uint8))
    assert opening.idol_selected(f, wanted)
    assert not opening.idol_selected(f, "i_card-shro-3-001")
