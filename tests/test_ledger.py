"""续跑记账：拿卡 / 买卡 / 换入加，换出 / 删除减（用户规则 3e）。"""

from hiro.loop.ledger import owned_cards

A, B, C, D = "p_card-a", "p_card-b", "p_card-c", "p_card-d"


def _st(page, kind, args, pf=None, ok=True, produce=None):
    return {
        "observation": {"page": {"name": page}},
        "state": {"page": {"name": page}, "page_fields": pf or {}, "produce": produce or {}},
        "decision": {"action": {"kind": kind, "args": args}},
        "execution": {"ok": ok},
    }


def test_ledger_adds_and_removes():
    steps = [
        _st("SELECT_CARD", "ActivateEffect", {"pick_indexes": [1]}, {"candidates": [{"card_id": C}, {"card_id": A}]}),
        _st(
            "SELECT_CARD", "ActivateEffect", {"pick_indexes": [0]}, {"candidates": [{"card_id": B}]}, ok=False
        ),  # 没生效不算
        _st("CONSULT", "ShopBuy", {"index": 0}, {"shelf": [{"card_id": B}]}),
        _st("SKILL_CARD_CHANGE_1", "ChangeCard", {"stage": 1, "pick_index": 0}, {"candidates": [{"card_id": C}]}),
        _st("SKILL_CARD_CHANGE_2", "ChangeCard", {"stage": 2, "remove_card_id": A}),
        _st("SKILL_CARD_REMOVAL", "RemoveCard", {"card_id": B}),
        _st("CONSULT", "ShopBuy", {"service": "delete", "card_index": 0}, produce={"deck": [{"card_id": D}]}),
    ]
    assert set(owned_cards(steps, memory_cards=[D])) == {C}


def test_change_cancel_keeps_cards():
    steps = [
        _st("SKILL_CARD_CHANGE_1", "ChangeCard", {"stage": 1, "pick_index": 0}, {"candidates": [{"card_id": C}]}),
        _st("SKILL_CARD_CHANGE_2", "ChangeCard", {"cancel": True}),
    ]
    assert set(owned_cards(steps, memory_cards=[A])) == {A}
