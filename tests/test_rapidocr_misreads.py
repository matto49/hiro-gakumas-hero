"""RapidOCR（Windows 默认后端）在 DMM 帧上的读错修正：不依赖画面样例的纯解析部分（W3-Windows 回归集 A / B / C 项）。"""

import hiro  # noqa: F401
from hiro.vision.action import normalize_hif
from hiro.vision.hif import _parse_breakthrough
from hiro.vision.pages import _growth_pct


def test_normalize_hif_exam_name():
    for raw in ("H.I.F本戦", "HI.F本戦", "H.「.F本戦", "H.、F本戦", "HIF本戦"):
        assert normalize_hif(raw) == "H.I.F本戦"
    assert normalize_hif("選抜試験2") == "選抜試験2"


def test_breakthrough_without_slash():
    assert _parse_breakthrough("64/128人") == {"rank_line": 64, "participants": 128}
    assert _parse_breakthrough("64128人") == {"rank_line": 64, "participants": 128}
    assert _parse_breakthrough("1632人") == {"rank_line": 16, "participants": 32}
    # 切不出「后半 = 前半 × 2」的不猜
    assert _parse_breakthrough("64129人") == {"rank_line": None, "participants": None}
    assert _parse_breakthrough("12人") == {"rank_line": None, "participants": None}


def test_growth_pct_lost_decimal_point():
    assert [_growth_pct(t) for t in ("ヘ455%", "〜44:4%", "〜41.3%", "45.5")] == [45.5, 44.4, 41.3, 45.5]
    assert _growth_pct("5%") is None
