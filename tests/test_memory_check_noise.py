"""本战开始确认页的选拔回忆核对：DMM 540x960 窗口 + RapidOCR（9580c0c 11-17）。Vi「1191」被读成「391121」，原来判「回忆不符」不开局。"""

import json
from pathlib import Path

from hiro.produce.memory_select import memory_missing_on_page

import pytest

FIX = Path(__file__).with_name("fixtures") / "pages"
TRUE = ("1472", "2236", "1191")  # 画面上的 Vo / Da / Vi


def _rapid():
    p = FIX / "finals-start-dmm-540.json"
    if not p.exists():
        pytest.skip("需要样例 pages/finals-start-dmm-540.json")
    return [t["text"] for t in json.loads(p.read_text(encoding="utf-8"))["texts"]]


def _missing(expect, tokens):
    return memory_missing_on_page(expect, shoot=lambda: None, texts=lambda img: tokens, sleep=lambda s: None)


def test_rapidocr_noise_one_value_one_digit_off_passes():
    assert _missing(TRUE, _rapid()) == []


@pytest.mark.parametrize(
    "expect",
    [
        ("1472", "2236", "2181"),  # 这一项差 2 位
        ("1500", "2236", "1191"),  # 两项对不上
        ("1472", "2136", "1191"),
    ],
)  # 对上的那项其实差 1 位、另一项也差：不止一项缺
def test_other_memory_still_rejected(expect):
    assert _missing(expect, _rapid()) != []


def test_one_digit_off_does_not_join_tokens():
    """「差 1 位」只在同一段文字里找，不跨段拼（「11」「21」拼不出和 1191 只差 1 位的 1121）。"""
    from hiro.produce.memory_select import _one_digit_off

    assert _one_digit_off("1191", ["391121"]) and not _one_digit_off("1191", ["11", "21"])


def test_finals_start_page_recognized_on_dmm_frame():
    from hiro.runtime import opening
    from hiro.runtime.frame import Frame

    import cv2

    p = FIX / "finals-start-dmm-540.png"
    if not p.exists():
        pytest.skip("需要样例 pages/finals-start-dmm-540.png")
    f = Frame(cv2.imread(str(p)), 0.0)
    f.__dict__["texts"] = []  # 只靠按钮模板
    assert opening._finals_start(f) and opening._finals_button(f) == (360, 1078)
