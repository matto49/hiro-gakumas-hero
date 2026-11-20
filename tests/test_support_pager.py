"""支援卡「選択」/ 租借列表的分页栏读数：RapidOCR 把「/」读成「1」等时也要认出（11-18 Windows MuMu「1/2」→「112」）。"""

from hiro.vision.support_screens import _pager

BOX = (326, 516, 394, 550)  # 分页栏居中


def _pg(text, box=BOX):
    m = _pager([(text, box)])
    return (int(m.group(1)), int(m.group(2))) if m else None


def test_slash_read_correctly():
    assert _pg("1/2") == (1, 2)
    assert _pg("2/2") == (2, 2)


def test_slash_misread_as_one_or_bar():
    assert _pg("112") == (1, 2)
    assert _pg("212") == (2, 2)
    assert _pg("1|2") == (1, 2)
    assert _pg("1l2") == (1, 2)


def test_slash_dropped():
    assert _pg("12") == (1, 2)
    assert _pg("22") == (2, 2)
    assert _pg("21") is None


def test_misread_rejected_when_not_a_page():
    assert _pg("211") is None  # 2 > 1：不是页码
    assert _pg("112", (40, 516, 90, 550)) is None  # 不在居中位置
    assert _pg("1123") is None
    assert _pg("112", (326, 600, 394, 630)) is None  # 不在分页栏高度
