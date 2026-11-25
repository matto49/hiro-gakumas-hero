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


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("32164人", (32, 64)),
        ("16132人", (16, 32)),
        ("64/128人", (64, 128)),
        ("64128人", (64, 128)),
        ("/32人9T", (None, None)),
    ],
)
def test_breakthrough_slash_read_as_one(raw, want):
    """A16：macOS 27.2.1 的 Vision 把突破线「32/64人」的斜杠读成「1」（「32」「164人」，识别测试集核心集 4 帧）。"""
    from hiro.vision.hif import _parse_breakthrough

    r = _parse_breakthrough(raw)
    assert (r["rank_line"], r["participants"]) == want


def test_extra_turns_falls_back_to_dddd(monkeypatch):
    """A16：Vision 把额外回合胶囊「+1」读成「十」时，用 ddddocr 只读数字再试；读出 1–9 才收。"""
    import hiro.vision.digits
    import hiro.vision.ocr
    from hiro.vision import hif

    img = np.full((1280, 720, 3), 255, np.uint8)  # 全白：胶囊判成「有」
    monkeypatch.setattr(hiro.vision.ocr, "ocr_boxes", lambda crop, scale=3: [("十", 0.3, (0, 0, 10, 10))])
    monkeypatch.setattr(hiro.vision.digits, "read_dddd", lambda im, box: 1)
    assert hif.read_extra_turns(img) == 1
    monkeypatch.setattr(hiro.vision.digits, "read_dddd", lambda im, box: 41)
    assert hif.read_extra_turns(img) is None
    monkeypatch.setattr(hiro.vision.ocr, "ocr_boxes", lambda crop, scale=3: [("+2", 0.9, (0, 0, 10, 10))])
    assert hif.read_extra_turns(img) == 2
