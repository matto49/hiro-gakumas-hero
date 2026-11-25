"""HIRO_OCR=ppocr5：小图要像 RapidOCR 那样居中补白再识别。不补白时检测模型在 HUD 小框（体力「22/33」、P 点数）上什么也找不到，
识别测试集核心集上选行动页的体力 / 上限 / P 点、轮间商店的价格全成「没读」（核心集 65.8%，其他后端 92% 上下）。"""

from types import SimpleNamespace

from hiro.vision import ocr

import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def test_small_image_is_padded_and_boxes_map_back(monkeypatch):
    seen = {}

    def fake_engine(languages):
        def run(img):
            seen["shape"] = img.shape
            h, w = img.shape[:2]
            # 假设检测到正中间一段字（原图 3 倍放大后 30×12 的框）
            cx, cy = w // 2, h // 2
            box = [[cx - 15, cy - 6], [cx + 15, cy - 6], [cx + 15, cy + 6], [cx - 15, cy + 6]]
            return SimpleNamespace(boxes=[box], txts=["22/33"], scores=[0.9])

        return run

    monkeypatch.setattr(ocr, "_pp_engine", fake_engine)
    img = np.zeros((30, 90, 3), np.uint8)
    got = ocr._pp_raw(img, 3.0, ("ja-JP",))
    assert min(seen["shape"][:2]) >= ocr.RAPID_PAD  # 补白了
    ((text, _conf, (x1, y1, x2, y2)),) = got
    assert text == "22/33"
    assert (x1, y1, x2, y2) == (10, 3, 20, 7)  # 去掉补白偏移、除以放大倍数：落回原图正中
