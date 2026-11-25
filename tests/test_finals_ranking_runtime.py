"""新主循环的本战名次页（HifCore.on_finals_ranking，Mac p13 11-17：开考前名次页没处理，180 秒停机）。"""

from types import SimpleNamespace as NS

from hiro.produce import hif
from hiro.vision.ocr import Box, Text

import pytest


def T(text, x, y):
    return Text(text, Box(x - 40, y - 15, x + 40, y + 15))


def _core(monkeypatch, page, texts, retry=False):
    clicks, records = [], []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: "img", click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr("hiro.vision.pages.classify_page", lambda img: ("HIF_FINALS_RANKING", page))
    monkeypatch.setattr(hif, "screen_texts", lambda img: texts)
    core = hif.HifCore.__new__(hif.HifCore)
    core.s = NS(observe=lambda **k: records.append(k["fields"]), record=lambda *a, **k: None, note=lambda *a: None)
    core._finals_retry = lambda img, label: retry
    return core, clicks, records


def test_pre_exam_standings_taps_tap_to_next_and_records_once(monkeypatch):
    core, clicks, records = _core(
        monkeypatch, {"kind": "standings", "finals_round": 1, "next": True}, [T("タップして次へ", 364, 1187)]
    )
    core.on_finals_ranking()
    core.on_finals_ranking()  # 点了还在（动画）：再点，但不再记
    assert clicks == [(364, 1187), (364, 1187)] and len(records) == 1


def test_round_result_taps_next_with_arrow(monkeypatch):
    texts = [T("あと3回", 63, 1090), T("再挑戦", 129, 1133), T("→次へ", 326, 1134)]
    core, clicks, _ = _core(monkeypatch, {"kind": "round_result", "finals_round": 1}, texts)
    core.on_finals_ranking()
    assert clicks == [(326, 1134)]  # 「→次へ」也认（旧代码要求正好是「次へ」）


@pytest.mark.parametrize("retry, want", [(True, []), (False, [(334, 1134)])])
def test_final_retries_or_goes_next(monkeypatch, retry, want):
    texts = [T("あと3回", 63, 1090), T("再挑戦", 129, 1133), T("→次へ", 334, 1134)]
    core, clicks, _ = _core(
        monkeypatch, {"kind": "final", "finals_round": 2, "rank": 3, "outcome": "敗退"}, texts, retry=retry
    )
    core.on_finals_ranking()
    assert clicks == want


def test_repeat_cap_stops_tapping(monkeypatch):
    core, clicks, _ = _core(monkeypatch, {"kind": "standings", "finals_round": 1}, [T("タップして次へ", 364, 1187)])
    for _ in range(hif.HifCore.RANKING_REPEAT_MAX + 5):
        core.on_finals_ranking()
    assert len(clicks) == hif.HifCore.RANKING_REPEAT_MAX + 1  # 之后交给看门狗
