"""exec/exam.py：R1 结束记 finals_context 的牌组——已经记过（存档插件开考时 / 续跑读回）就不覆盖，没记过才用培育记账兜底。"""

from hiro.exec.exam import r1_deck_to_note


class _Session:
    def __init__(self, deck):
        self.state = type("S", (), {"produce": {"deck": deck}})()


LEDGER = [{"card_id": "p_card-02-act-3_039", "upgrade": 1}, {"card_id": "p_card-02-men-0_039"}]


def test_uses_ledger_when_context_has_no_deck():
    assert r1_deck_to_note("r1", _Session(LEDGER), {}) == [("p_card-02-act-3_039", 1), ("p_card-02-men-0_039", 0)]


def test_keeps_deck_already_noted():
    assert r1_deck_to_note("r1", _Session(LEDGER), {"deck": [["p_card-x", 2]]}) is None  # 存档插件记的更准，不覆盖


def test_only_r1_and_with_session():
    assert r1_deck_to_note("r2", _Session(LEDGER), {}) is None
    assert r1_deck_to_note("r1", None, {}) is None
    assert r1_deck_to_note("r1", _Session([]), {}) is None
