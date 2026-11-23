"""第十四局相談删卡（协调 4a / 总控 2026-11-02）：删除对话框滚动没动，两屏拼成 36 张（真实 18 张），
えいえいおー（protect、keep=2）被当成多出来的副本删掉了。现在受保护卡所有副本都不删、定制过的也不删。帧：tests/fixtures/q14_delete。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.brain.deciders import ProduceDeciders, deletable
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import GameState
from hiro.vision.offline import load

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "q14_delete"
EIEI = "p_card-02-men-1_031"


def _read(name):
    from hiro.produce.card_ops import CARD_OP_REGION
    from hiro.vision.deck import read_card_dialog

    return read_card_dialog(load(cv2.imread(str(FIX / f"{name}.jpg"))), CARD_OP_REGION["remove"])["cards"]


@pytest.fixture(scope="module")
def decks():
    from hiro.vision.deck import merge_dialog_screens

    first, bottom, full = _read("01-deck"), _read("02-deck-bottom"), _read("03-picked")
    merged = merge_dialog_screens([first, bottom])  # 实机同样的两屏：滚动没动，拼成重复的整副
    return full, merged


def _choose(cards):
    grid = [dict(c, index=i) for i, c in enumerate(cards)]
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "SKILL_CARD_REMOVAL", GameState(page_fields={"cards": grid}, produce={"mode": "produce-007"})
    )
    return d.action.args.get("card_id"), d


@pytest.mark.fixtures
def test_stitch_fixed(decks):
    """实机两屏拼成 36 张（识别 merge_dialog_screens 旧版）；现在没认出的格当通配、对上后补全 → 18 张，えいえいおー 2 张。"""
    full, merged = decks
    assert len(full) == 18 and sum(c.get("card_id") == EIEI for c in full) == 2
    assert merged["complete"] is True and len(merged["cards"]) == 18 and merged["new_rows"][-1] == 0
    assert sum(c.get("card_id") == EIEI for c in merged["cards"]) == 2


@pytest.mark.fixtures
@pytest.mark.parametrize("which", ["full", "merged"])
def test_never_deletes_protected_or_customized(decks, which):
    full, merged = decks
    cards = full if which == "full" else merged["cards"]
    cid, d = _choose(cards)
    assert cid != EIEI, d.reason
    target = next(c for c in cards if c.get("card_id") == cid)
    assert deletable(target) and not target.get("customized"), d.reason


def test_deletable_rule():
    assert not deletable({"card_id": EIEI})  # 受保护：不论张数
    assert not deletable({"card_id": "p_card-02-men-0_039", "customized": True})  # 定制过的基础卡也不删
    assert deletable({"card_id": "p_card-02-men-0_039"})  # 距離感の基本（基础卡）
    assert not deletable({"card_id": None})
