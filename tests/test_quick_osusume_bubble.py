"""G1-F4（11-26 Mac 小白验收）：课程后「三选一拿技能卡」框，卡片下面那截白底带「おすすめ」角标，
被快速判页当成底部消息气泡（TRANSITION kind=message text=おすすめ），一直点 (12,12)，180 秒看门狗停机。"""

from pathlib import Path

from hiro.vision import quick

import cv2
import pytest

SHOT = Path(__file__).parent / "fixtures" / "pages" / "select-card-osusume-g1.png"  # 私有截图（游戏画面不随仓库发布）


@pytest.mark.skipif(not SHOT.exists(), reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）")
def test_card_select_with_osusume_badge_is_not_a_bubble():
    img = cv2.imread(str(SHOT))
    assert quick.bottom_bubble(img) is None
    q = quick.quick_page(img)
    assert q is None or q.get("page") != "TRANSITION", q


@pytest.mark.parametrize("text", ["おすすめ", "NEW", "おすすめNEW", ""])
def test_badge_only_text_is_not_a_message(monkeypatch, text):
    import hiro.vision.ocr

    import numpy as np

    monkeypatch.setattr(hiro.vision.ocr, "ocr", lambda crop, scale=2: [(text, 0.9)] if text else [])
    assert quick.read_bubble(np.zeros((1280, 720, 3), np.uint8), 980, 1040) is None


def test_real_message_still_a_bubble(monkeypatch):
    import hiro.vision.ocr

    import numpy as np

    monkeypatch.setattr(hiro.vision.ocr, "ocr", lambda crop, scale=2: [("Vo.が87上昇した!", 0.9)])
    assert quick.read_bubble(np.zeros((1280, 720, 3), np.uint8), 980, 1040) == (
        "TRANSITION",
        {"kind": "message", "text": "Vo.が87上昇した!"},
    )
