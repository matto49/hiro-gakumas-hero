"""第十九局 R1 授業换卡（23:59）：牌组里 えいえいおー +1 / 0 / 0，决策要换第三张没强化的（remove_index 10、remove_upgrade 0），
执行只按 card_id 点，换出了 えいえいおー+。现在同一 card_id 有多张时按格子序号 / 强化次数选，对不上就不换（点キャンセル）。"""

from pathlib import Path

import cv2

from hiro.produce import card_ops
from hiro.loop import kaa_patches as kp
from hiro.vision.deck import read_card_dialog

FIX = Path(__file__).parent / "fixtures/change_index"
EIEIO = "p_card-02-men-1_031"


def _cards(name):
    return read_card_dialog(cv2.imread(str(FIX / name)), card_ops.CHANGE2_CARD_AREA)["cards"]


def test_main19_picks_unupgraded_copy_by_index():
    cards = _cards("main19-eieio-three.jpg")
    assert [(c["index"], c["upgrade"]) for c in cards if c.get("card_id") == EIEIO] == [(8, 1), (9, 0), (10, 0)]
    hit, why = card_ops.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=10, remove_upgrade=0)
    assert hit["index"] == 10 and hit["upgrade"] == 0 and tuple(hit["center"]) == (434, 992)


def test_upgrade_only_when_index_does_not_match():
    cards = _cards("main19-eieio-three.jpg")
    hit, _ = card_ops.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=99, remove_upgrade=0)
    assert hit["upgrade"] == 0  # 格子号对不上（画面变了）：按强化次数挑没强化的那张，绝不点 +1


def test_multiple_copies_without_match_stops():
    cards = _cards("main19-eieio-three.jpg")
    hit, why = card_ops.pick_change_target(cards, 0, EIEIO, protected=set(), remove_index=99, remove_upgrade=2)
    assert hit is None and "3 张" in why  # 不退回只按 card_id


def test_old_behaviour_without_index():
    cards = _cards("main19-eieio-three.jpg")
    hit, _ = card_ops.pick_change_target(cards, 0, EIEIO, protected=set())
    assert hit["card_id"] == EIEIO  # 决策没给序号 / 强化（旧数据）：仍按 card_id


def test_main19_second_change_kyorikan():
    """00:04 第二次换卡（距離感の基本，remove_index 7）当时是对的，新逻辑结果不变。"""
    cards = _cards("main19-kyorikan.jpg")
    hit, why = card_ops.pick_change_target(cards, 0, "p_card-02-men-0_039", protected=set(), remove_index=7, remove_upgrade=0)
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

    out = card_ops.scan_dialog_deck(card_ops.CHANGE2_CARD_AREA, expected=22, shoot=lambda: None, swipe=swipe, sleep=lambda s: None)
    assert out["complete"] and len(out["cards"]) == 22 and out["screens"] > 1
    assert [c["card_id"] for c in out["cards"]] == [f"c{i:02d}" for i in range(22)] and out["cards"][21]["index"] == 21
    assert pos["i"] == 0 and pos["swipes"].count("up") >= 1  # 滑回顶部，点卡时从第一屏开始找


def test_scan_single_screen_does_not_swipe(monkeypatch):
    import hiro.vision.deck as vd

    one = _screens(_deck(12))[0]
    swipes = []
    monkeypatch.setattr(vd, "read_card_dialog", lambda img, region: {"cards": one})
    monkeypatch.setattr(vd, "dialog_scroll", lambda img, region: {"scrollable": False, "at_top": True})
    out = card_ops.scan_dialog_deck(card_ops.CHANGE2_CARD_AREA, shoot=lambda: None, swipe=lambda *a: swipes.append(a), sleep=lambda s: None)
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
    monkeypatch.setattr(card_ops, "scan_dialog_deck", lambda region, expected=None: {"cards": whole, "complete": True, "screens": 4, "problems": []})
    assert st._scan_change_deck() is True
    pf = st.s.state.page_fields
    assert len(pf["cards"]) == 22 and pf["deck_complete"] and pf["deck_screens"] == 4
    # 拼接不可信：保留第一屏，标 deck_complete=False
    st.s.state = GameState(page_fields={"cards": _screens(_deck())[0]})
    monkeypatch.setattr(card_ops, "scan_dialog_deck", lambda region, expected=None: {"cards": whole[:30], "complete": False, "screens": 3, "problems": ["x"]})
    assert st._scan_change_deck() is False
    assert len(st.s.state.page_fields["cards"]) == 12 and st.s.state.page_fields["deck_complete"] is False and recovers == ["missing_fields"]


def test_stage2_cancel_limit_constants():
    """阶段 2 找不到要换的那张时先取消；同一张卡取消满 STAGE2_MAX_CANCELS 次后改换强化最低的（不能无限打转）。"""
    assert card_ops.STAGE2_MAX_CANCELS >= 1 and isinstance(card_ops._STAGE2_CANCELS, dict)


def test_dmm_unsure_upgrade_copy_is_the_target():
    """DMM dmm1m：4 张 ゆるふわおしゃべり 实为 +1 / +1 / +1 / 0，没强化那张原图读成 None（橙色描边落在「+」区域），
    整副扫描过（不按格子号）时只认强化恰好 0 的 → 取消绕回阶段 1 十几分钟。认不准的那张就是要换的。"""
    yur = "p_card-02-act-1_028"
    cards = [{"index": i, "card_id": yur, "upgrade": u, "name": "ゆるふわおしゃべり"} for i, u in enumerate((1, 1, 1, None))]
    cards.append({"index": 4, "card_id": "p_card-02-act-1_037", "upgrade": 1, "name": "デイドリーミング"})
    hit, why = card_ops.pick_change_target(cards, 0, yur, protected=set(), remove_index=None, remove_upgrade=0)
    assert hit["index"] == 3 and "认不准" in why
    cards[3]["upgrade"] = 1  # 四张都明确认成 +1：还是不换（不能盲换一张强化过的）
    hit, _ = card_ops.pick_change_target(cards, 0, yur, protected=set(), remove_index=None, remove_upgrade=0)
    assert hit is None


def test_cancel_fallback_prefers_unsure_over_upgraded():
    """取消满次数后的兜底（换强化最低的）：认不准的排在明确 +1 之前（以前 None 排最后，会换掉一张 +1）。"""
    rank = lambda c: 0.5 if c.get("upgrade") is None else c["upgrade"]  # noqa: E731  与 change_stage2 里的排序一致
    same = [{"index": i, "upgrade": u} for i, u in enumerate((1, 1, 1, None))]
    assert min(same, key=rank)["index"] == 3
    same[1]["upgrade"] = 0
    assert min(same, key=rank)["index"] == 1


# ---------------------------------------------------------------- 认不出的格子按目标卡核对（mainz3 11-18 03:47，DMM 559x993）
HINATA = "p_card-02-men-2_052"  # ひなたぼっこ：左侧两个角标盖住卡面，票数 2:2 并列、卡图 0.494 只领先 0.086，通用识别认不出
MZ3 = FIX / "mainz3-hinata-unread.jpg"


def _mz3():
    import pytest

    if not MZ3.exists():
        pytest.skip("没有 mainz3 换卡阶段 2 截图")
    img = cv2.imread(str(MZ3))
    return img, read_card_dialog(img, card_ops.CHANGE2_CARD_AREA)["cards"]


def test_guess_unread_cell_by_target_card():
    img, cards = _mz3()
    assert cards[8]["card_id"] is None  # 通用识别仍不认（不放宽全局门槛）
    g = card_ops.guess_unread_target(img, cards, HINATA)
    assert g is not None and g["index"] == 8 and g["card_id"] == HINATA and g["guessed"] is True and g["upgrade"] == 0


def test_guess_unread_cell_rejects_other_cards():
    img, cards = _mz3()
    assert card_ops.guess_unread_target(img, cards, "p_card-02-men-0_039") is None  # 距離感の基本：卡图不像，不猜
    assert card_ops.guess_unread_target(img, cards, HINATA, remove_upgrade=1) is None  # 强化次数对不上


def _stage2_stub(monkeypatch, img, detail):
    from types import SimpleNamespace as NS

    import hiro.vision.deck as vd
    import hiro.vision.ui_templates as ui
    from hiro.produce import ops

    dev = NS(clicks=[], screenshot=lambda: img, swipe=lambda *a, **k: None)
    dev.click = lambda *a: dev.clicks.append(a)
    monkeypatch.setattr(card_ops, "device", dev)
    monkeypatch.setattr(card_ops, "CHANGE_STAGE_TIMEOUT", 0.6)
    monkeypatch.setattr(card_ops, "CHANGE_POLL", 0.05)
    monkeypatch.setattr(card_ops._change_run, "run", None, raising=False)
    monkeypatch.setattr(card_ops, "_STAGE2_CANCELS", {})
    monkeypatch.setattr(ops, "CALIB_DIR", Path(__file__).parent / ".calib-test")
    real_tpl = ui.tpl
    title = NS(exists=lambda im: not dev.clicks)  # 点卡前在阶段 2，点后标题消失
    monkeypatch.setattr(ui, "tpl", lambda name: title if name == "InProduce.DialogCardChange2.Title" else real_tpl(name))
    monkeypatch.setattr(ui, "tap", lambda *a, **k: False)
    monkeypatch.setattr(card_ops, "confirm_change_dialog", lambda im, run=None: False)
    real = vd.read_card_dialog
    monkeypatch.setattr(vd, "read_card_dialog", lambda im, region, **kw: {**real(im, region, **kw), **({"selected_card": detail} if dev.clicks else {})})
    return dev


def test_stage2_taps_guessed_cell_when_detail_confirms(monkeypatch):
    img, _ = _mz3()
    dev = _stage2_stub(monkeypatch, img, {"card_id": HINATA, "upgrade": 0})
    assert card_ops.change_stage2(None, 0, card_id=HINATA) is True
    assert dev.clicks[0] == (82 + 59, 933 + 59)


def test_stage2_guessed_cell_needs_detail_confirmation(monkeypatch):
    img, _ = _mz3()
    dev = _stage2_stub(monkeypatch, img, None)  # 详情栏卡名没读到：猜出来的格子不点「チェンジ」
    assert card_ops.change_stage2(None, 0, card_id=HINATA) is False
    assert dev.clicks[-1] == card_ops.CHANGE_CANCEL_TAP


def test_detail_reads_expected_card_on_unread_cell():
    """mainz3 r2（04:02）：点了猜的第 9 格，详情栏写着 ひなたぼっこ，却读成 None 取消：换卡页详情栏在缩略图下面，旧框框的是顶部；
    选中格认不出时又被当成和详情栏冲突。换卡用 CHANGE2_DETAIL_BOX，选中格认不出不算冲突；expect 把目标卡加进同名消歧候选。"""
    import pytest

    p = FIX / "mainz3-hinata-picked.jpg"
    if not p.exists():
        pytest.skip("没有 mainz3 点卡后截图")
    img = cv2.imread(str(p))
    from hiro.vision.deck import CHANGE2_DETAIL_BOX

    d = read_card_dialog(img, card_ops.CHANGE2_CARD_AREA, expect=HINATA, detail_box=CHANGE2_DETAIL_BOX)
    assert d["selected"] == 8 and d["selected_card"]["card_id"] == HINATA and d["agree"] is None
    assert read_card_dialog(img, card_ops.CHANGE2_CARD_AREA)["detail"] is None  # 旧框（顶部 DETAIL_BOX）读不到换卡页的详情栏


def test_stage2_guessed_cell_end_to_end_with_real_frames(monkeypatch):
    """点卡前用 mainz3 卡格帧、点卡后用详情栏帧，读卡全走真实识别（不塞 selected_card）：应点第 9 格并完成换卡。"""
    import pytest

    import hiro.vision.deck as vd

    img, _ = _mz3()
    p = FIX / "mainz3-hinata-picked.jpg"
    if not p.exists():
        pytest.skip("没有 mainz3 点卡后截图")
    picked = cv2.imread(str(p))
    real = vd.read_card_dialog
    dev = _stage2_stub(monkeypatch, img, None)
    monkeypatch.setattr(vd, "read_card_dialog", real)
    dev.screenshot = lambda: picked if dev.clicks else img
    assert card_ops.change_stage2(None, 0, card_id=HINATA) is True
    assert dev.clicks[0] == (82 + 59, 933 + 59) and card_ops.CHANGE_CANCEL_TAP not in dev.clicks
