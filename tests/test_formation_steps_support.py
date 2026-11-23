"""开局第 2 步支援卡核对（hiro.produce.formation_steps.check_support）：等翻页动画停、全认不出不算通过（账号 2 11-16）。"""

from hiro.produce import formation_steps
from hiro.produce.formation_check import FormationMismatch

import pytest

PLAN = {"support": {"set": 20, "own": [{"support_card_id": f"s{i}"} for i in range(5)]}}


def _reads(seq, monkeypatch):
    it = iter(seq)
    monkeypatch.setattr("hiro.vision.support_screens.read_support_formation", lambda img: next(it))


def _page(ids):
    return {"view": "step2", "number": 20, "slots": [{"support_card_id": i} for i in ids]}


def test_waits_for_settled_then_passes(monkeypatch, caplog):
    _reads([_page([None] * 5), _page([None, "s1", "s2", "s3", "s4"]), _page([f"s{i}" for i in range(5)])], monkeypatch)
    caplog.set_level("INFO")
    formation_steps.check_support("img", PLAN, 20, shoot=lambda: "img", sleep=lambda s: None)
    assert "核对通过" in caplog.text


def test_all_unknown_after_waiting_is_not_a_pass(monkeypatch):
    _reads([_page([None] * 5)] * 20, monkeypatch)
    with pytest.raises(FormationMismatch, match="都认不出"):
        formation_steps.check_support("img", PLAN, 20, shoot=lambda: "img", sleep=lambda s: None)


def test_partial_unknown_logs_incomplete(monkeypatch, caplog):
    _reads([_page([None, "s1", "s2", "s3", "s4"])] * 20, monkeypatch)
    caplog.set_level("INFO")
    formation_steps.check_support("img", PLAN, 20, shoot=lambda: "img", sleep=lambda s: None)
    assert "核对不完整" in caplog.text and "核对通过" not in caplog.text
