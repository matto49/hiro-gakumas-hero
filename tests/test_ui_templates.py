"""界面模板（hiro.vision.ui_templates，去 KAA 第 3 步）：素材包完整、匹配语义同 KAA（fixed 只在原位找、阈值、同处去重）。

和 KAA image.find 的逐帧对照（36 个模板 × 512 张实机帧，命中 1283 次，位置 0 差异）做在迁移时，这里只留不依赖实机帧的用例。
"""

from hiro.vision.ui_templates import _catalog, box, checkbox_checked, primary_button_enabled, tpl

import numpy as np
import pytest


def _paste(name: str, x: int, y: int) -> np.ndarray:
    import cv2

    rng = np.random.default_rng(0)
    img = rng.integers(0, 60, (1280, 720, 3), dtype=np.uint8)  # 暗噪声底，避免常数图的相关系数无定义
    t = cv2.cvtColor(tpl(name).pixels, cv2.COLOR_GRAY2BGR)
    img[y : y + t.shape[0], x : x + t.shape[1]] = t
    return img


@pytest.mark.parametrize("name", sorted(_catalog()["templates"]))
def test_every_template_loads_and_fits_screen(name):
    t = tpl(name)
    h, w = t.pixels.shape
    assert h > 0 and w > 0
    if t.slice_rect is not None:
        x, y, sw, sh = t.slice_rect
        assert (sw, sh) == (w, h)
        if t.fixed:  # 只在原位找的模板，位置必须在画面内（非 fixed 的 slice_rect 只是记录，KAA 原数据有出界 1 像素的，如 Common.IconButtonCheck）
            assert x >= 0 and x + sw <= 720 and y >= 0 and y + sh <= 1280


def test_found_at_own_position():
    t = tpl("InProduce.ButtonUse")
    x, y, w, h = t.slice_rect
    m = t.find(_paste(t.name, x, y))
    assert m is not None and (m.rect.x1, m.rect.y1, m.rect.x2, m.rect.y2) == (x, y, x + w, y + h) and m.score > 0.99
    assert m.center == (x + w // 2, y + h // 2)


def test_fixed_template_only_searched_in_place():
    t = tpl("InProduce.ButtonUse")
    assert t.fixed and t.find(_paste(t.name, 100, 300)) is None
    assert tpl("Common.ButtonNext").find(_paste("Common.ButtonNext", 100, 300)) is not None  # 非 fixed：全屏找


def test_find_all_dedups_same_place_and_scales_screen():
    import cv2

    img = _paste("InProduce.M", 100, 400)
    img[400:414, 300:316] = img[400:414, 100:116]  # 第二个同样的字母
    hits = tpl("InProduce.M").find_all(img)
    assert sorted((m.rect.x1, m.rect.y1) for m in hits) == [(100, 400), (300, 400)]
    big = cv2.resize(img, (1080, 1920), interpolation=cv2.INTER_CUBIC)  # 高分辨率截图先缩到 720x1280
    assert {(m.rect.x1, m.rect.y1) for m in tpl("InProduce.M").find_all(big)} >= {(100, 400)}


def test_boxes():
    b = box("InProduce.BoxNoSkillCard")
    assert (b.x1, b.y1, b.x2, b.y2) == (180, 977, 529, 1026) and b.xywh == (180, 977, 349, 49)


def test_button_and_checkbox_state():
    from hiro.vision.ocr import Box
    from hiro.vision.ui_templates import Match

    m = Match(Box(0, 0, 40, 20), 1.0)
    on = np.zeros((1280, 720, 3), np.uint8)
    on[:20, :40] = (40, 120, 250)  # BGR：红通道 250 → 第 5 箱，亮
    off = on.copy()
    off[:20, :40] = (150, 160, 170)  # 红通道 170 → 第 4 箱，灰
    assert primary_button_enabled(on, m) is True and primary_button_enabled(off, m) is False
    orange = np.zeros((1280, 720, 3), np.uint8)
    orange[:20, :40] = (0, 140, 255)
    assert checkbox_checked(orange, m) is True and checkbox_checked(off, m) is False
