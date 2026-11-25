"""考试状态栏数值：背景很亮（天空、浅色石地）时数字的白色填充和背景连成一片，游戏字体比对判成「框里没字」，
やる気 98、好印象 6 等整格读成没有数值（识别测试集进阶集 9 格）。背景亮时「没字」不可信，退回 ddddocr / 整行 OCR；
真没有数值的常驻效果（エウレカ！）在亮背景上 ddddocr 也读不出数，仍是没有。帧在识别测试集仓库，没有就跳过。"""

import os
from pathlib import Path

from hiro.vision import status
from hiro.vision.offline import load

import cv2
import pytest

DS = Path(os.environ.get("HIRO_OCR_DATASET") or Path.home() / "github" / "hiro-ocr-dataset") / "hard"


def _slot(frame, k):
    p = next(DS.glob(f"*{frame}.jpg"), None) if DS.exists() else None
    if p is None:
        pytest.skip(f"需要识别测试集的帧 {frame}")
    return next(e for e in status.read_status_column(load(cv2.imread(str(p)))) if e["slot"] == k)


@pytest.mark.parametrize(
    "frame, k, value", [("00624", 0, 98), ("00624", 1, 6), ("00033", 0, 88), ("00211", 0, 42), ("00049", 1, 1)]
)
def test_value_on_bright_background(frame, k, value):
    assert _slot(frame, k)["value"] == value


@pytest.mark.parametrize("frame, k", [("00136", 7), ("00211", 7), ("00128", 6)])  # 亮背景上的エウレカ！：本来就没有数值
def test_no_value_stays_none_on_bright_background(frame, k):
    assert _slot(frame, k)["value"] is None


def test_turns_above_checks_brightness_of_the_upper_row(monkeypatch):
    """两行数字的格：上一行背景亮、下一行背景暗时，字体比对对上一行返回「没字」不可信，要退回整行 OCR 认「Nターン」；
    以前用的是下一行数值框的亮度，判成暗就直接返回 None，回合丢了（PR #104）。"""
    import numpy as np

    img = np.full((1280, 720, 3), 20, np.uint8)  # 深色背景
    k = 3
    cy = status._slot_y(k)
    y1, y2 = cy + status.TURNS_ABOVE[0], cy + status.TURNS_ABOVE[1]
    img[y1:y2, status.SLOT_X + 12 : status.SLOT_X + 60] = 255  # 只有上一行的框是亮的
    assert not status._bright_behind(img, k)  # 下一行（数值框）是暗的
    assert status._bright_behind(img, k, above=True)
    monkeypatch.setattr(status, "read_leading_int", lambda *a, **kw: (None, "empty"))
    monkeypatch.setattr(status, "ocr", lambda crop: [("5ターン", (0, 0, 1, 1))])
    assert status._turns_above(img, k) == 5
    dark = np.full((1280, 720, 3), 20, np.uint8)  # 上一行也是暗的、确实没字：还是提前返回
    assert status._turns_above(dark, k) is None
