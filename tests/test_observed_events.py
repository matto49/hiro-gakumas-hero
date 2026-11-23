"""H1 §3.1：事件页面 → Event（docs/state-events.md §2.1 字段名）。夹具同 test_deck_events / test_pages，真值按截图目视。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.contracts import Event
from hiro.vision import deck
from hiro.vision.events import observed_events, selection_args

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures")


def _obs(rel: str, seq: int = 1):
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX / rel)))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    return observation(seq, 0.0, scene, raw, screenshot=rel)


def _one(rel: str):
    obs = _obs(rel)
    assert obs.problems(strict=True) == []
    evs = observed_events(obs)
    assert len(evs) == 1 and isinstance(evs[0], Event) and evs[0].problems() == []
    ev = evs[0].to_dict()
    assert (
        ev["seq"] == 1
        and ev["basis"] == "observe_only"
        and ev["ref"] == {"observation_seq": 1, "screenshot": rel, "page": obs.page.name}
    )
    return ev


@pytest.mark.parametrize(
    ("rel", "kind", "args"),
    [
        (
            "events/acquire-memory-00028.jpg",
            "CARD_ACQUIRE",
            {"card_id": "p_card-02-men-1_035", "upgrade": 1, "memory_effect": True},
        ),
        # 相談买卡的演出背景压暗（没有培育 HUD）也带 memory_effect；它总和 ShopBuy 动作配对，不单独改牌组
        (
            "events/acquire-shop-00128.jpg",
            "CARD_ACQUIRE",
            {"card_id": "p_card-02-act-1_037", "upgrade": 1, "memory_effect": True},
        ),
        ("events/acquire-study-00224.jpg", "CARD_ACQUIRE", {"card_id": "p_card-02-men-2_101", "upgrade": 0}),
        ("events/acquire-upgraded-00303.jpg", "CARD_ACQUIRE", {"card_id": "p_card-02-men-2_111", "upgrade": 1}),
        ("events/acquire-sleepy-00263.jpg", "CARD_ACQUIRE", {"card_id": "p_card-00-acc-0_002", "upgrade": 0}),
        ("events/acquire-drink-00270.jpg", "DRINK_ACQUIRE", {"drink_id": "pdrink_02-3-010"}),
        ("events/acquire-pitem-00166.jpg", "PITEM_ACQUIRE", {"pitem_id": "pitem_00-3-010-0"}),
        ("events/acquire-wappen-00034.jpg", "PITEM_ACQUIRE", {"pitem_id": "pitem_00-3-265-0"}),
    ],
)
def test_acquire_events(rel, kind, args):
    ev = _one(rel)
    assert (ev["kind"], ev["args"], ev["origin"], ev["confirmed"], ev["confidence"]) == (
        kind,
        args,
        "observed",
        True,
        1.0,
    )


@pytest.mark.parametrize(
    ("rel", "kind", "args"),
    [
        ("events/delete-confirm-00148.jpg", "CARD_DELETE",
         {"card_id": "p_card-02-men-0_039", "upgrade": 0, "deck_index": None, "price": 100, "p_before": 116, "p_after": 16, "sale": False}),
        ("events/exchange-confirm-00135.jpg", "SHOP_BUY",
         {"card_id": "p_card-02-men-3_040", "upgrade": 1, "price": 105, "p_before": 221, "p_after": 116, "sale": False}),
        ("shop/flow/00127-confirm.jpg", "SHOP_BUY",
         {"card_id": "p_card-02-act-1_037", "upgrade": 1, "price": 49, "p_before": 270, "p_after": 221, "sale": True}),
    ],
)  # fmt: skip
def test_confirm_events(rel, kind, args):
    """确认弹窗只是意图：confirmed=None，点了确认之后的获得弹窗 / P 点变化才算发生。"""
    ev = _one(rel)
    assert (ev["kind"], ev["args"], ev["confirmed"], ev["confidence"]) == (kind, args, None, 1.0)


def test_upgrade_result_event():
    ev = _one("pages/upgrade_result.jpg")
    assert (ev["kind"], ev["args"], ev["confirmed"]) == (
        "CARD_UPGRADE",
        {"card_id": "p_card-02-men-1_031", "upgrade": 1},
        True,
    )


@pytest.mark.parametrize(("rel", "gain"), [("pages/star_gain-00178.jpg", 45), ("pages/star_gain-00320.jpg", 15)])
def test_wappen_event(rel, gain):
    ev = _one(rel)
    assert (ev["kind"], ev["args"], ev["confirmed"]) == ("WAPPEN_TRIGGER", {"star_gain": gain}, True)


def test_pdrink_max_event():
    ev = _one("pages/pdrink_max-q4-00069.jpg")
    assert ev["kind"] == "DRINK_ACQUIRE" and ev["confirmed"] is None and ev["args"]["overflow"] is True
    assert ev["args"]["drink_id"] is not None


@pytest.mark.parametrize(
    "rel",
    ["events/removal-sel6-00146.jpg", "events/not-acquire-select-drink.jpg", "events/not-acquire-drink-detail-01824.jpg",
     "pages/dialog-turn_end.jpg", "pages/transition-loading.jpg", "select_card/00283.jpg", "deck/list-kaa.png"],
)  # fmt: skip
def test_no_event(rel):
    """选中某张卡、选饮料页、考试饮料详情、转场、三选一、看不全的牌组页：都不是识别能确认的事件。"""
    assert observed_events(_obs(rel)) == []


def test_popup_held_over_frames_emits_once():
    first, again = _obs("events/acquire-memory-00028.jpg", 1), _obs("events/acquire-memory-00028.jpg", 2)
    assert len(observed_events(first)) == 1 and observed_events(again, prev=first) == []
    other = _obs("events/acquire-study-00224.jpg", 3)
    assert [e.args["card_id"] for e in observed_events(other, prev=again)] == ["p_card-02-men-2_101"]


def test_selection_args():
    assert selection_args(_obs("events/removal-sel6-00146.jpg")) == {
        "card_id": "p_card-02-men-0_039",
        "upgrade": 0,
        "deck_index": 6,
    }
    assert selection_args(_obs("events/removal-none-00141.jpg")) is None
    assert selection_args(_obs("events/acquire-memory-00028.jpg")) is None


def test_deck_review_event():
    """牌组页看全才发 DECK_REVIEW（origin=review）；deck 与推算比较用 deck.deck_diff。"""
    from hiro.vision.observe import observation

    _raw, merged = _merged()
    obs = observation(5, 0.0, "DECK_LIST", merged)
    ev = observed_events(obs)[0].to_dict()
    assert (ev["kind"], ev["origin"], ev["confirmed"], ev["args"]["deck_size"]) == ("DECK_REVIEW", "review", True, 16)
    assert ev["args"]["deck"] == obs.fields["deck"] and len(ev["args"]["deck"]) == 16


def test_deck_diff():
    _raw, merged = _merged()
    expected = [{"card_id": c["card_id"], "upgrade": c["upgrade"]} for c in merged["deck"]]
    assert deck.deck_diff(expected, merged["cards"]) == {
        "same": True,
        "missing": [],
        "extra": [],
        "upgraded": [],
        "unknown": 0,
    }

    # 推算漏了一次强化（第 1 张开花实际 +1）、多算了一张眠気
    seen = [dict(c) for c in merged["cards"]]
    seen[0]["upgrade"] = 1
    d = deck.deck_diff(expected + [{"card_id": "p_card-00-acc-0_002", "upgrade": 0}], seen)
    assert d["same"] is False and d["upgraded"] == [{"card_id": seen[0]["card_id"], "expected": 0, "observed": 1}]
    assert d["missing"] == [{"card_id": "p_card-00-acc-0_002", "upgrade": 0}] and d["extra"] == []

    # 认不出的一张可能就是差的那张 → same 为 None（不下结论，牌组记 pending）
    seen = [dict(c) for c in merged["cards"]]
    seen[3]["card_id"] = None
    d = deck.deck_diff(expected, seen)
    assert d["same"] is None and d["unknown"] == 1 and len(d["missing"]) == 1


def _merged():
    """list-kaa 只看到 12 / 16 张：用它的前 12 张 + 前 4 张拼一副 16 张的完整牌组（同 test_deck.test_deck_list_observation）。"""
    from hiro.vision.extract import extract
    from hiro.vision.offline import load

    raw, _ = extract("DECK_LIST", load(cv2.imread(str(FIX / "deck/list-kaa.png"))))
    full = raw["cards"] + raw["cards"][:4]

    def page(cards):
        return {"deck_size": 16, "cards": [dict(c, index=i, row=i // 4, col=i % 4) for i, c in enumerate(cards)]}

    return raw, deck.merge_deck_pages(page(full[:12]), page(full[4:]))


def test_acquire_without_id_emits_nothing():
    """数据确认：获得弹窗没有 card_id 时若照发，会和动作事件配不上而多算一张 → 不发。"""
    from hiro.vision.observe import observation

    obs = observation(1, 0.0, "ACQUIRE", {"kind": "card", "card_id": None, "name": "?", "upgrade": None, "agree": None})
    assert observed_events(obs) == []
