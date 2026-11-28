"""初的剧本页有普通「初」和「初 LEGEND」两种，左下「初 ⇄」切换（11-22 DMM：上次停在 LEGEND，找不到「レギュラー」40 秒超时）。"""

from types import SimpleNamespace as NS

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import hajime
from hiro.runtime import nia_sample as ns

import pytest
from test_opening import _texts, frame, rig  # 同目录（pytest 把测试目录放进 sys.path）


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


# ---------------------------------------------------------------- choose_difficulty 端到端（假设备，11-28 适配初 LEGEND）
LEGEND_WORDS = hajime.DIFFICULTY_WORDS["legend"]
STEP1 = {"Produce.TextStepIndicator1": (93, 49)}


def _regular_page(*, regular="レギュラー"):
    return frame(
        {}, _texts(("定期公演『初」", 360, 631), (regular, 228, 881), ("プロ", 494, 881), ("マスター", 600, 881))
    )


def _legend_page():
    return frame({}, _texts(("定期公演『初」", 360, 631), ("レジエンド", 360, 880), ("LEGEND", 360, 909)))


def test_legend_mode_table():
    assert hajime.MODES["legend"] == "produce-006"
    assert "LEGEND" in LEGEND_WORDS and "レジエンド" in LEGEND_WORDS  # 实机 OCR 读法


def test_choose_legend_on_legend_page_taps_button(monkeypatch):
    rt, _ = rig(monkeypatch, [_legend_page()], on_tap=lambda taps: [frame(STEP1)])
    ns.choose_difficulty(rt, "legend", words=LEGEND_WORDS)
    assert rt.raw_device.taps == [(360, 880)]


def test_choose_legend_on_regular_page_switches_then_taps(monkeypatch):
    """画面是普通初：先点左下「初 ⇄」，切到 LEGEND 页再点「レジェンド」。"""

    def on_tap(taps):
        return [_legend_page()] if taps[-1] == ns.HAJIME_SWITCH else [frame(STEP1)]

    rt, _ = rig(monkeypatch, [_regular_page()], on_tap=on_tap)
    ns.choose_difficulty(rt, "legend", words=LEGEND_WORDS)
    assert rt.raw_device.taps == [ns.HAJIME_SWITCH, (360, 880)]


def test_choose_legend_switches_even_if_regular_misread(monkeypatch):
    """普通初页 OCR 把「レギュラー」读成「レギユラー」：其他几个难度照样认得出，照切。"""

    def on_tap(taps):
        return [_legend_page()] if taps[-1] == ns.HAJIME_SWITCH else [frame(STEP1)]

    rt, _ = rig(monkeypatch, [_regular_page(regular="レギユラー")], on_tap=on_tap)
    ns.choose_difficulty(rt, "legend", words=LEGEND_WORDS)
    assert rt.raw_device.taps == [ns.HAJIME_SWITCH, (360, 880)]


def test_choose_legend_switch_never_lands_stops(monkeypatch):
    """点「初 ⇄」一直没切过去（坐标点偏 / 没解放）：点满 SWITCH_MAX 次停下交人，不再每 3 秒点到超时。"""
    rt, _ = rig(monkeypatch, [_regular_page()])
    with pytest.raises(ResumeBlocked, match="初 ⇄"):
        ns.choose_difficulty(rt, "legend", words=LEGEND_WORDS)
    assert rt.raw_device.taps == [ns.HAJIME_SWITCH] * ns.SWITCH_MAX


def test_choose_regular_on_legend_page_switches_back(monkeypatch):
    """要普通难度、画面停在 LEGEND（#196 的原场景）：切回再点「プロ」。"""

    def on_tap(taps):
        return [_regular_page()] if taps[-1] == ns.HAJIME_SWITCH else [frame(STEP1)]

    rt, _ = rig(monkeypatch, [_legend_page()], on_tap=on_tap)
    ns.choose_difficulty(rt, "pro", words=hajime.DIFFICULTY_WORDS["pro"])
    assert rt.raw_device.taps == [ns.HAJIME_SWITCH, (494, 881)]
