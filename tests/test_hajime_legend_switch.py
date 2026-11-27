"""初的剧本页有普通「初」和「初 LEGEND」两种，左下「初 ⇄」切换（11-22 DMM：上次停在 LEGEND，找不到「レギュラー」40 秒超时）。"""

from types import SimpleNamespace as NS

from hiro.runtime import nia_sample as ns


def _t(text, y):
    return NS(text=text, rect=NS(center=NS(y=y)))


REGULAR = [_t("初", 200), _t("レギュラー", 876), _t("プロ", 876), _t("マスター", 876)]
LEGEND = [_t("初", 200), _t("レジェンド", 876)]


def test_wants_regular_but_page_is_legend():
    words = ns.DIFFICULTY_WORDS["pro"]
    assert ns._other_hajime_page(LEGEND, words)
    assert not ns._other_hajime_page(REGULAR, words)


def test_wants_legend_but_page_is_regular():
    assert ns._other_hajime_page(REGULAR, ("レジェンド",))
    assert not ns._other_hajime_page(LEGEND, ("レジェンド",))


def test_not_hajime_page():
    """N.I.A 的剧本页（没有「初」）不切。"""
    assert not ns._other_hajime_page([_t("レジェンド", 876)], ns.DIFFICULTY_WORDS["pro"])


def test_real_ocr_misread_legend():
    """实机 OCR：「レジェンド」读成「レジエンド」，下面还有一行「LEGEND」。"""
    legend = [_t("定期公演『初」", 631), _t("レジエンド", 880), _t("LEGEND", 909)]
    assert ns._other_hajime_page(legend, ("レギュラー", "REGULAR"))
    assert not ns._other_hajime_page(legend, ("レジェンド", "LEGEND"))
