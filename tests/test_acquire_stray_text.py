"""DMM qualifyz1（11-17 17:35）：Da SP 课结束，3 条支援能力横幅 + P 道具浮条盖着「ホットコーヒー」获得演出，名称栏这一行右端露出
后面横幅的「H.I.F」logo 一角，OCR 读成「ıF」（x≈659）。read_acquire_popup 见到偏离中心的文字就不算 → 整页 unknown、没处理器，
180 秒停机。名称栏里很短、不含假名 / 汉字的碎片不算文字（选饮料页、Pドリンク詳細 旁边的字都是日文，照旧排除）。"""

from pathlib import Path

from hiro.vision import deck

import pytest

FIX = Path(__file__).with_name("fixtures") / "acquire_dmm"


def _fake_boxes(monkeypatch, boxes):
    import hiro.vision.ocr as ocr

    x1 = deck.ACQUIRE_BAR[0]
    monkeypatch.setattr(
        ocr, "ocr_boxes", lambda img, **k: [(t, 0.9, (cx - x1 - 20, 5, cx - x1 + 20, 30)) for t, cx in boxes]
    )


@pytest.mark.master
def test_stray_latin_fragment_does_not_hide_acquire(monkeypatch):
    import numpy as np

    _fake_boxes(monkeypatch, [("ホットコーヒー", 379), ("ıF", 659)])
    item = deck.read_acquire_popup(np.zeros((1280, 720, 3), np.uint8), art=False)
    assert item is not None and item["kind"] == "drink" and item["drink_id"] == "pdrink_02-1-008"


@pytest.mark.master
def test_offcenter_japanese_still_rejected(monkeypatch):
    """Pドリンク詳細：名字偏左、右边「捨てる」——照旧不算获得页。"""
    import numpy as np

    _fake_boxes(monkeypatch, [("ホットコーヒー", 321), ("捨てる", 600)])
    assert deck.read_acquire_popup(np.zeros((1280, 720, 3), np.uint8), art=False) is None


@pytest.mark.master
def test_dmm_frame_classified_acquire():
    import cv2

    p = FIX / "qualifyz1-hotcoffee-banners.png"
    if not p.exists():
        pytest.skip("没有 DMM 获得页样例（截图不进仓库）")
    from hiro.vision.classify import classify

    assert classify(cv2.imread(str(p))).name == "ACQUIRE"


def test_acquire_bar_retries_smaller_scale_when_empty(monkeypatch):
    """mainz7（11-19 15:27 DMM 1080x1920 RapidOCR）：P 道具效果「ランダムなPドリンクを2つ獲得」的获得演出，名称栏放大 3 倍
    RapidOCR 一个字都没读出（1.5 / 2 倍都读得出「ホットコーヒー」），整页判 unknown，180 秒停机。默认倍数读空时再用 1.5 倍读一次。"""
    from pathlib import Path

    import hiro.vision.ocr as ocr_mod
    from hiro.vision import deck

    import cv2

    p = Path(__file__).parent / "fixtures" / "pages" / "acquire-drink-pitem-banner-z7.png"
    if not p.exists():
        pytest.skip("没有 mainz7 获得演出帧")
    real = ocr_mod.ocr_boxes
    monkeypatch.setattr(
        ocr_mod, "ocr_boxes", lambda img, *, scale=3.0, **kw: [] if scale == 3.0 else real(img, scale=scale, **kw)
    )
    got = deck.read_acquire_popup(cv2.imread(str(p)), art=False)
    assert got is not None and got["name"] == "ホットコーヒー" and got["kind"] == "drink"
