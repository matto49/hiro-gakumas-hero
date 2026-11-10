# 来源：digital-hiro tests/test_change_index.py（c3fc27f）
"""第十九局 R1 授業换卡（23:59）：牌组里 えいえいおー +1 / 0 / 0，决策要换第三张没强化的（remove_index 10、remove_upgrade 0），
执行只按 card_id 点，换出了 えいえいおー+。现在同一 card_id 有多张时按格子序号 / 强化次数选，对不上就不换（点キャンセル）。"""

from pathlib import Path

import cv2

from hiro.loop import kaa_patches as kp
from hiro.vision.deck import read_card_dialog

FIX = Path(__file__).parent / "fixtures/change_index"
EIEIO = "p_card-02-men-1_031"


def _cards(name):
    return read_card_dialog(cv2.imread(str(FIX / name)), kp.CHANGE2_CARD_AREA)["cards"]


def test_main19_picks_unupgraded_copy_by_index():
    cards = _cards("main19-eieio-three.jpg")
    assert [(c["index"], c["upgrade"]) for c in cards if c.get("card_id") == EIEIO] == [(8, 1), (9, 0), (10, 0)]
    hit, why = kp.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=10, remove_upgrade=0)
    assert hit["index"] == 10 and hit["upgrade"] == 0 and tuple(hit["center"]) == (434, 992)


def test_upgrade_only_when_index_does_not_match():
    cards = _cards("main19-eieio-three.jpg")
    hit, _ = kp.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=99, remove_upgrade=0)
    assert hit["upgrade"] == 0  # 格子号对不上（画面变了）：按强化次数挑没强化的那张，绝不点 +1


def test_multiple_copies_without_match_stops():
    cards = _cards("main19-eieio-three.jpg")
    hit, why = kp.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=99, remove_upgrade=2)
    assert hit is None and "3 张" in why  # 不退回只按 card_id


def test_old_behaviour_without_index():
    cards = _cards("main19-eieio-three.jpg")
    hit, _ = kp.pick_change_target(cards, 0, EIEIO, protected=set())
    assert hit["card_id"] == EIEIO  # 决策没给序号 / 强化（旧数据）：仍按 card_id


def test_main19_second_change_kyorikan():
    """00:04 第二次换卡（距離感の基本，remove_index 7）当时是对的，新逻辑结果不变。"""
    cards = _cards("main19-kyorikan.jpg")
    hit, why = kp.pick_change_target(cards, 0, "p_card-02-men-0_039", protected=set(), remove_index=7, remove_upgrade=0)
    assert hit is not None and hit["index"] == 7


# ---------------------------------------------------------------- 整副扫描（决策 da6c8e5：对话框只读一屏 12 张，牌组 22 张）
def _deck(n=22):
    return [{"card_id": f"c{i:02d}", "upgrade": i % 2, "customized": False, "name": f"卡{i}"} for i in range(n)]


def _screens(deck, rows_visible=3, step_rows=1):
    """每屏露 rows_visible 行、一次滑 step_rows 行，生成 read_card_dialog 格式的每屏卡格。"""
    rows = [deck[i : i + 4] for i in range(0, len(deck), 4)]
    out = []
    for top in range(0, max(1, len(rows) - rows_visible + 1), step_rows):
        out.append([{**c, "row": r, "col": j, "index": r * 4 + j, "center": [140 + 147 * j, 700 + 147 * r]}
                    for r, row in enumerate(rows[top : top + rows_visible]) for j, c in enumerate(row)])  # fmt: skip
    return out


def test_scan_dialog_deck_merges_and_returns_to_top(monkeypatch):
    import hiro.vision.deck as vd

    screens = _screens(_deck())
    pos = {"i": 0, "swipes": []}
    monkeypatch.setattr(vd, "read_card_dialog", lambda img, region: {"cards": screens[pos["i"]]})
    monkeypatch.setattr(vd, "dialog_scroll", lambda img, region: {"scrollable": True, "at_top": pos["i"] == 0})

    def swipe(x1, y1, x2, y2):
        down = y2 < y1
        pos["swipes"].append("down" if down else "up")
        pos["i"] = min(len(screens) - 1, pos["i"] + 1) if down else max(0, pos["i"] - 1)

    out = kp.scan_dialog_deck(kp.CHANGE2_CARD_AREA, expected=22, shoot=lambda: None, swipe=swipe, sleep=lambda s: None)
    assert out["complete"] and len(out["cards"]) == 22 and out["screens"] > 1
    assert [c["card_id"] for c in out["cards"]] == [f"c{i:02d}" for i in range(22)] and out["cards"][21]["index"] == 21
    assert pos["i"] == 0 and pos["swipes"].count("up") >= 1  # 滑回顶部，点卡时从第一屏开始找


def test_scan_single_screen_does_not_swipe(monkeypatch):
    import hiro.vision.deck as vd

    one = _screens(_deck(12))[0]
    swipes = []
    monkeypatch.setattr(vd, "read_card_dialog", lambda img, region: {"cards": one})
    monkeypatch.setattr(vd, "dialog_scroll", lambda img, region: {"scrollable": False, "at_top": True})
    out = kp.scan_dialog_deck(kp.CHANGE2_CARD_AREA, shoot=lambda: None, swipe=lambda *a: swipes.append(a), sleep=lambda s: None)
    assert out["screens"] == 1 and len(out["cards"]) == 12 and swipes == []


def test_hif_hands_whole_deck_to_decision(monkeypatch):
    from types import SimpleNamespace as NS

    from hiro.contracts import GameState
    from hiro.produce.hif import HiroHifStrategy

    st = HiroHifStrategy.__new__(HiroHifStrategy)
    notes, recovers = [], []
    st.s = NS(state=GameState(page_fields={"cards": _screens(_deck())[0]}), note=notes.append, recover=lambda k, d: recovers.append(k))
    st._deck_size = lambda: 22
    whole = [{**c, "index": i, "deck_index": i} for i, c in enumerate(_deck())]
    monkeypatch.setattr(kp, "scan_dialog_deck", lambda region, expected=None: {"cards": whole, "complete": True, "screens": 4, "problems": []})
    assert st._scan_change_deck() is True
    pf = st.s.state.page_fields
    assert len(pf["cards"]) == 22 and pf["deck_complete"] and pf["deck_screens"] == 4
    # 拼接不可信：保留第一屏，标 deck_complete=False
    st.s.state = GameState(page_fields={"cards": _screens(_deck())[0]})
    monkeypatch.setattr(kp, "scan_dialog_deck", lambda region, expected=None: {"cards": whole[:30], "complete": False, "screens": 3, "problems": ["x"]})
    assert st._scan_change_deck() is False
    assert len(st.s.state.page_fields["cards"]) == 12 and st.s.state.page_fields["deck_complete"] is False and recovers == ["missing_fields"]


def test_stage2_cancel_limit_constants():
    """阶段 2 找不到要换的那张时先取消；同一张卡取消满 STAGE2_MAX_CANCELS 次后改换强化最低的（不能无限打转）。"""
    assert kp.STAGE2_MAX_CANCELS >= 1 and isinstance(kp._STAGE2_CANCELS, dict)
