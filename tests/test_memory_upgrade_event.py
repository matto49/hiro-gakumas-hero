"""回忆效果展示的卡：牌组里已有低一级的同一张 → 记成强化，不是获得（DMM q4 11-17：选拔试验后 私がスター / 届いて！ 被强化成 +，
原来各记一次 CARD_ACQUIRE，账本 16 → 18，14:21 复核读到 18 才纠正回来）。"""

from pathlib import Path

from hiro.contracts import Event, GameState
from hiro.loop.events import apply_event

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "events"
DECK = [{"card_id": "p_card-02-men-3_002", "upgrade": 0}, {"card_id": "p_card-02-act-1_037", "upgrade": 1}]


def _acq(card_id, upgrade, memory=True, origin="observed"):
    args = {"card_id": card_id, "upgrade": upgrade}
    if memory:
        args["memory_effect"] = True
    return Event(seq=522, kind="CARD_ACQUIRE", origin=origin, args=args, confirmed=True)


def _deck(s):
    return sorted((c["card_id"], c["upgrade"]) for c in s.produce["deck"])


def test_memory_effect_on_owned_card_is_upgrade():
    s, _ = apply_event(GameState(produce={"deck": [dict(c) for c in DECK]}), _acq("p_card-02-men-3_002", 1))
    assert _deck(s) == [("p_card-02-act-1_037", 1), ("p_card-02-men-3_002", 1)]  # 张数不变，强化了
    assert s.ledger[-1].kind == "CARD_UPGRADE" and s.ledger[-1].args["source"] == "memory"


def test_memory_effect_on_new_card_is_acquire():
    """开局回忆卡加入牌组（events/acquire-memory-00028）：牌组里没有这张 → 照旧是获得。"""
    s, _ = apply_event(GameState(produce={"deck": [dict(c) for c in DECK]}), _acq("p_card-02-men-1_035", 1))
    assert len(s.produce["deck"]) == 3 and s.ledger[-1].kind == "CARD_ACQUIRE"


def test_plain_acquire_of_upgraded_duplicate_stays_acquire():
    """没有回忆效果标记（选卡 / 事件给的同名强化版）：还是获得。"""
    s, _ = apply_event(
        GameState(produce={"deck": [dict(c) for c in DECK]}), _acq("p_card-02-men-3_002", 1, memory=False)
    )
    assert len(s.produce["deck"]) == 3


@pytest.mark.parametrize(("name", "card_id"), [("memory-upgrade-q4-522.jpg", "p_card-02-men-3_002"),
                                               ("memory-upgrade-q4-523.jpg", "p_card-02-act-3_039")])  # fmt: skip
def test_memory_effect_flag_on_q4_frames(name, card_id):
    """522：标签还没淡入，靠没有培育 HUD 认；523：有「メモリー効果」标签。"""
    from hiro.vision.deck import read_acquire_popup

    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要实机截图 events/{name}")
    r = read_acquire_popup(cv2.resize(cv2.imread(str(p)), (720, 1280)))
    assert r["card_id"] == card_id and r["upgrade"] == 1 and r["memory_effect"] is True


@pytest.mark.parametrize("name", ["acquire-study-00224.jpg", "acquire-upgraded-00303.jpg", "acquire-sleepy-00263.jpg"])
def test_ordinary_acquire_not_memory(name):
    from hiro.vision.deck import read_acquire_popup

    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要实机截图 events/{name}")
    assert read_acquire_popup(cv2.imread(str(p)))["memory_effect"] is False
