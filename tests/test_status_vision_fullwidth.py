"""A16（11-26）：macOS 27.2.1 的 Apple Vision 把状态栏的「%」读成全角「％」、「3ターン」读成「ろターン」，
两行数字的格（上「3ターン」、下「50%」）丢了单位和回合（识别测试集进阶集 00154 第 5、7 格）。
这里用假 OCR 输出测文字归一化，不依赖识别测试集。"""

from hiro.vision import status

import numpy as np
import pytest


@pytest.fixture
def fake_ocr(monkeypatch):
    out = {"text": ""}
    monkeypatch.setattr(status, "ocr", lambda img, **kw: [(out["text"], 0.3)])
    return out


IMG = np.zeros((1280, 720, 3), np.uint8)


@pytest.mark.parametrize(
    ("raw", "want"), [("50％", "50%"), ("30 ％", "30%"), ("12ターン", "12ターン"), ("３回", "3回")]
)
def test_label_text_folds_fullwidth(fake_ocr, raw, want):
    fake_ocr["text"] = raw
    assert status._label_text(IMG, 5) == want


def test_value_unit_matches_folded_percent(fake_ocr):
    fake_ocr["text"] = "50％"
    m = status.VALUE_UNIT.match(status._label_text(IMG, 5))
    assert m and (int(m.group(1)), m.group(2)) == (50, "%")
    assert status._unit_label(IMG, 5) == "%"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("3ターン", 3),
        ("ろターン", 3),
        ("Oターン", 0),
        ("1Oターン", 10),
        ("lターン", 1),
        ("Sターン", 5),
        ("ターン", None),
        ("50%", None),
    ],
)
def test_turns_text_reads_lookalike_digits(raw, want):
    assert status._turns_from_text(raw) == want
