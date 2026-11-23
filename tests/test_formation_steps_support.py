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


def test_two_blank_reads_do_not_count_as_settled(monkeypatch, caplog):
    """11-21 DMM：翻号后动画慢，连续几帧 5 格全空。两次全空不算「稳了」，接着等到认出来。"""
    _reads([_page([None] * 5)] * 4 + [_page([f"s{i}" for i in range(5)])], monkeypatch)
    caplog.set_level("INFO")
    formation_steps.check_support("img", PLAN, 20, shoot=lambda: "img", sleep=lambda s: None)
    assert "核对通过" in caplog.text


def test_kaa_check_support_waits_for_animation(monkeypatch):
    """KAA 主循环的第 2 步核对要传 shoot / sleep（原来没传：只读翻号后的第一帧）。"""
    import sys
    import types

    from hiro.loop import kaa_patches

    seen = {}
    monkeypatch.setattr(formation_steps, "check_support", lambda img, plan, set_no, **kw: seen.update(kw))
    monkeypatch.setitem(
        sys.modules, "kotonebot", types.SimpleNamespace(device=types.SimpleNamespace(screenshot=lambda: "img"))
    )
    kaa_patches._check_support("img")
    assert callable(seen.get("shoot")) and callable(seen.get("sleep"))


def test_step3_blank_reads_keep_waiting(monkeypatch):
    """第 3 步回忆图标：两次一格认不出不算停稳，等到认出来的那一帧。"""
    frames = iter(["blank", "blank", "blank", "full"])
    reads = {"blank": [{"known": False}] * 4, "full": [{"known": True, "card": c} for c in ("a", "b", "c", "d")]}
    monkeypatch.setattr("hiro.vision.memory_icons.read_step3_icons", lambda img: reads[img])
    monkeypatch.setattr("hiro.vision.memory_icons.card_key", lambda c: c)
    img = formation_steps.settled_step3(
        lambda: next(frames), {"memory": {"cards": ["a", "b", "c", "d"]}}, sleep=lambda s: None
    )
    assert img == "full"
