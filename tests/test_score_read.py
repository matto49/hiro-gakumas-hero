# 来源：digital-hiro tests/test_score_read.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""考试排名栏玩家分数（read_ranking / digits.read_score）：复盘 2026-10-31 的掉位帧回归。

夹具 tests/fixtures/score/*.jpg 是 traces/<run>-qualify* 帧的顶部 180 行（720 宽）。
旧实现：字体比对不确定时按玩家卡白框裁图交给 ddddocr，5 位分数超出白框的首 / 尾一位被切掉
（37811→3781、14031→1403、22311→2231、14949→1494）；白框顶边与背景白色连成一片时找不到玩家卡（None），
或 JPEG 下断成恰好一段时定到别处（3418→34）。
"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision import digits
from hiro.vision.hif import read_ranking

FIX = Path(__file__).with_name("fixtures") / "score"

# 夹具 → (玩家分数, 名次, NPC 分数)；真值按截图目视
TRUTH = {
    "q2r3-00048": (37719, 2, [44992, 13055, 11899, 10761]),
    "q2r3-00049": (37811, 2, [44992, 13055, 11899, 10761]),  # 记录值 37811，旧实现重读 3781
    "q2r3-00047": (28551, 2, [40816, 11256, 10342, 9933]),
    "q2r3-00025": (789, 5, [12159, 4259, 2678, 2446]),
    "q2r3-00033": (3418, 5, [28397, 8455, 7105, 6103]),  # 白框找不到 → 青色底定位
    "q2r2-00024": (7790, 1, [5287, 2162, 2141, 1993]),
    "q3r2-00013": (22311, 2, [40816, 11845, 10906, 8096]),  # 实机记录 2231
    "q3-00200": (14031, 1, [5287, 2180, 2150, 2113]),  # 实机记录 1403
    "q1r10-00009": (14949, 2, [36626, 10351, 9913, 6938]),
    "q1r10-00012": (46209, 1, [44992, 12540, 12129, 11120]),
    "q1r8-00004": (5531, 4, [24331, 7460, 6141, 3784]),  # 白框找不到 → 青色底定位
}


def _img(name: str):
    strip = cv2.imread(str(FIX / f"{name}.jpg"))
    return cv2.copyMakeBorder(strip, 0, 1280 - strip.shape[0], 0, 0, cv2.BORDER_CONSTANT)


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_ranking_scores(name):
    r = read_ranking(_img(name))
    assert (r["my_score"], r["my_rank_index"], r["npc_scores"]) == TRUTH[name]
    # 没有数字字体时：ddddocr 与 Apple Vision 互相核对，对不上而 ddddocr 位数与字形数相同时置信度 0.6（见 digits._score_without_font）
    assert r["my_score_read"]["method"] in ("digits", "dddd") and r["my_score_read"]["confidence"] >= (
        0.8 if digits._templates() else 0.6
    )


@pytest.mark.user_assets("font")
def test_ocr_fallback_with_fewer_digits_is_rejected(monkeypatch):
    """字体比对不确定、OCR 少读一位时给 None，不交出掉位的值。"""
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 3781)
    r = read_ranking(_img("q2r3-00049"))
    assert r["my_score"] is None and r["my_score_read"]["method"] is None
    assert "对不上" in r["my_score_read"]["note"]


@pytest.mark.user_assets("font")
def test_ocr_fills_digit_font_missed(monkeypatch):
    """字体只认出一部分、OCR 多出的位数在可疑块数以内且包含字体那串 → 用 OCR，置信度 0.6。"""
    real = digits._read_glyphs

    def drop_middle(img, box, **kw):
        text, _, info = real(img, box, **kw)
        info = dict(info, parts=info["parts"][:2] + [("?", 0.3, 50, 60)] + info["parts"][2:])
        return text[:2] + text[3:], True, info

    monkeypatch.setattr(digits, "_read_glyphs", drop_middle)
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 37719)
    value, meta = digits.read_score(_img("q2r3-00048"), (367, 125, 482, 163))
    assert value == 37719 and meta["method"] == "dddd" and meta["confidence"] == 0.6


def test_score_field_meta():
    from hiro.vision.hif import read_hif_exam
    from hiro.vision.observe import observation

    raw = {**read_ranking(_img("q3-00200")), "remaining_turns": 1}
    obs = observation(1, 0.0, "HIF_EXAM", raw)
    assert obs.fields["score"] == 14031
    if digits._templates():
        assert obs.field_meta["score"].confidence == 0.9 and obs.field_meta["score"].note == "字体比对与 OCR 一致"
    else:
        assert (
            obs.field_meta["score"].confidence == 0.8
            and obs.field_meta["score"].note == "无数字字体：ddddocr 与 Vision 一致"
        )
    assert read_hif_exam  # 完整读取另有 test_observe 覆盖


# ---- 没有数字字体（开源默认素材）：ddddocr 与 Apple Vision 互相核对。有字体时也跑（把字体模板换成空）
NO_FONT_VISION_ONLY = {"q2r2-00024"}  # 这一帧没有字体时 ddddocr 两读不一致，要靠 OCR 判；RapidOCR 判不出（实机有字体，不走这条）


@pytest.fixture
def no_font(monkeypatch):
    from hiro.vision import hif

    monkeypatch.setattr(digits, "_templates", dict)
    monkeypatch.setattr(hif, "_templates", dict)


@pytest.mark.parametrize("name", [pytest.param(n, marks=pytest.mark.vision_only("模拟「没有数字字体」时 ddddocr 两读不一致、用 OCR 判的路径；Windows / DMM 用随仓库的素材包（assets/pack/font 带数字字体），实机走字形比对，不走这条")) if n in NO_FONT_VISION_ONLY else n for n in sorted(TRUTH)])
def test_no_font_ranking_scores(no_font, name):
    r = read_ranking(_img(name))
    assert (r["my_score"], r["my_rank_index"], r["npc_scores"]) == TRUTH[name]
    assert r["my_score_read"]["confidence"] >= 0.6


def test_no_font_dropped_digit_is_rejected(no_font, monkeypatch):
    """ddddocr 掉一位、Vision 读不出：位数（4）与整高字形数（5）对不上 → None，不交出掉位的值。"""
    import hiro.vision.ocr as ocr_mod

    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 3781)
    monkeypatch.setattr(ocr_mod, "ocr", lambda img, **kw: [])
    r = read_ranking(_img("q2r3-00049"))
    assert r["my_score"] is None and "对不上" in r["my_score_read"]["note"]


def test_no_font_truncation_takes_longer(no_font, monkeypatch):
    """ddddocr 掉末位、Vision 读全：取长的那个，置信度 0.6。"""
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 1403)
    r = read_ranking(_img("q3-00200"))
    assert r["my_score"] == 14031 and r["my_score_read"]["confidence"] == 0.6
