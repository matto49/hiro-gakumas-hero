"""考试左侧状态栏的红底槽（低下状态 / デバフ）：偶像之路 g5c（11-17）的红心是「消費体力増加」（游戏内说明里的名字）。
以前红底不算状态，交给卡面比对，读成「ダークヒーローの誕生」N ターン 或「消費体力減少」0，引擎因此少算一倍体力消耗；
图库里没有「増加」的图标，字形和「消費体力減少」一样，按底色翻过来。#19 菱形外面背景发亮，曾和「体力回復無効」（×）排反。
实机帧在私有 fixtures。"""

from pathlib import Path

from hiro.vision import status
from hiro.vision.offline import load

import cv2
import numpy as np
import pytest

FIX = Path(__file__).parent / "fixtures" / "tower"


def _read(name):
    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要样例 {p}")
    return status.read_status_column(load(cv2.imread(str(p))))


@pytest.mark.parametrize("n, slot, value", [(14, 3, 2), (18, 3, 1), (19, 3, 3), (23, 2, 2)])
def test_red_heart_is_consumption_add(n, slot, value):
    es = _read(f"status-red-heart-g5c-{n}.jpg")
    e = next(e for e in es if e["slot"] == slot)
    assert e["key"] == "examstaminaconsumptionadd" and e["name"] == "消費体力増加" and e.get("debuff")
    assert e["type"] == "ProduceExamEffectType_ExamStaminaConsumptionAdd"
    assert e["value"] == value
    assert "alternatives" not in e  # 「体力回復無効」已按角标重比分开
    assert not any(x.get("key") == "examstaminaconsumptiondown" for x in es)


def _icon_patch(key: str, bg: tuple[int, int, int]) -> np.ndarray:
    """合成一个槽：底色菱形 + 图库里的白色字形（38px），放在 56×56 槽里。"""
    _meta, templates = status._icon_index()
    if key not in templates:
        pytest.skip("没有考试效果图标")
    patch = np.full((2 * status.SLOT_HALF, 2 * status.SLOT_HALF, 3), 40, np.uint8)
    c = status.SLOT_HALF
    diamond = np.array([[c, 2], [2 * c - 2, c], [c, 2 * c - 2], [2, c]], np.int32)
    cv2.fillConvexPoly(patch, diamond, bg)
    t = templates[key][2]
    n = t.shape[0]
    y0 = x0 = c - n // 2
    a = (t / 255.0)[..., None]
    patch[y0 : y0 + n, x0 : x0 + n] = (patch[y0 : y0 + n, x0 : x0 + n] * (1 - a) + 255 * a).astype(np.uint8)
    return patch


@pytest.mark.parametrize("key", ["examstaminaconsumptiondown", "examstaminarecoverrestriction"])
def test_split_pair_keeps_synthetic_icon(key):
    patch = _icon_patch(key, (80, 80, 230))  # 红底（BGR）
    top = status._split_pair(patch, status.match_status_icon(patch))
    assert top[0][1] == key


def test_split_pair_ignores_other_pairs():
    top = [(0.9, "examreview"), (0.89, "examcardplayaggressive")]
    assert status._split_pair(np.zeros((56, 56, 3), np.uint8), top) is top
