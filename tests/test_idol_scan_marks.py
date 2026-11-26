"""G3（11-26 Windows DMM，游戏窗口 540x960）：扫偶像卡进了偶像详情页，整屏 OCR 却读不出左下带图标的小字「一覧」，
报「没进到偶像详情页」停下。详情页认任意一个标志字（一覧 / 才能開花 / 特訓 / おすすめ効果），整屏读不出时把按钮那块放大再读。"""

from hiro.produce import idol_scan as isc

import numpy as np

IMG = np.zeros((1280, 720, 3), np.uint8)


def test_detail_page_seen_by_other_marks():
    ocr = lambda img: [("篠澤広", (0, 0, 1, 1)), ("才能開花", (530, 330, 650, 360))]  # noqa: E731  整屏没读出「一覧」
    assert isc.page_seen(IMG, ocr, isc.DETAIL_MARKS)


def test_detail_page_seen_by_crop_when_full_frame_misses():
    crops = []

    def crop_ocr(im):
        crops.append(im.shape)
        return [("一覧", 0.9)]

    assert isc.page_seen(IMG, lambda img: [("篠澤広", (0, 0, 1, 1))], isc.DETAIL_MARKS, isc.DETAIL_CROP, crop_ocr)
    x1, y1, x2, y2 = isc.DETAIL_CROP
    assert crops == [(y2 - y1, x2 - x1, 3)]


def test_other_pages_not_seen():
    ocr = lambda img: [("プロデュース", (0, 0, 1, 1)), ("ホーム", (0, 0, 1, 1))]  # noqa: E731
    assert not isc.page_seen(IMG, ocr, isc.DETAIL_MARKS, isc.DETAIL_CROP, lambda im: [("ホーム", 0.9)])
    assert not isc.page_seen(IMG, ocr, isc.LIST_MARKS)
