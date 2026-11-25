"""偶像一览找卡：同一列里同一张卡有好几个窗口都过线时，取距离最小（对得最齐）的那个，不取第一个过线的。
11-25 账号 3：十王星南「Our Chant」在第 2 行第 4 列，y=704 的窗口（一半盖着第 1 行的花海咲季）距离 11.8 先过了线，
点它的中心落在花海咲季上，第 1 步选了 5 次都不对、退出。"""

import os
from pathlib import Path

from hiro.produce import idol_select
from hiro.vision import idols
from hiro.vision.ui_templates import box

import numpy as np
import pytest


@pytest.mark.synthetic
def test_find_on_page_takes_best_aligned_window(monkeypatch):
    _x0, y0, _w, _h = box("Produce.BoxIdolOverviewIdols").xywh
    x_col4 = idol_select.COLS[3]
    best_y = y0 + 232  # 第 2 行对齐的位置

    def query(crop, threshold=None):
        # 用裁剪图左上角像素编码出 (x, y)：只有第 4 列有这张卡，离对齐位置越远距离越大
        col, y = int(crop[0, 0, 0]), int(crop[0, 0, 1]) * 8
        if col != 4:
            return None
        d = 4.0 + abs(y - best_y) / 12
        return None if threshold is not None and d >= threshold else ("i_card-skin-jsna-3-006_1.png", d)

    img = np.zeros((1280, 720, 3), np.uint8)
    for i, x in enumerate(idol_select.COLS):
        for y in range(0, 1280, 1):
            img[y, x, 0], img[y, x, 1] = i + 1, min(255, y // 8)
    monkeypatch.setattr(idols, "query", query)
    hit = idol_select.find_on_page(img, "i_card-jsna-3-006")
    assert hit is not None and hit.x1 == x_col4
    assert abs(hit.y1 - best_y) <= idol_select.ROW_STEP  # 不是最上面那个刚过线的窗口


def test_find_on_page_real_frame_row2():
    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "idol-overview-jsna-3-006-row2.jpg"
    )
    if not path.exists() or not idols._db()[0]:
        pytest.skip("需要实机截图 pages/idol-overview-jsna-3-006-row2.jpg 和偶像卡图库")
    import cv2

    img = cv2.imread(str(path))
    hit = idol_select.find_on_page(img, "i_card-jsna-3-006")
    assert hit is not None and hit.x1 == idol_select.COLS[3]
    assert (
        abs(hit.y1 - 800) <= idol_select.ROW_STEP
    )  # 对齐第 2 行（离线实测最佳 y=800 距离 4.0；y=704 那个 11.8 盖着第 1 行）
