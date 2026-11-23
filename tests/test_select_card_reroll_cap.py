"""选卡：决策要刷新、执行侧没刷成（每页上限 / 失败）时，按「刷新已用完」重新决策，不退到候选里第一个「拿」的动作。
DMM 选拔第 6 天：决策要用最后 1 次刷新，执行侧每页 3 次上限先到，拿了不要的 本番前夜+，禁用还剩 3 次没用。"""

from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Candidate, Decision, GameState

import pytest


def test_reroll_blocked_redecides(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif

    cands = [
        {"index": i, "card_id": c}
        for i, c in enumerate(("p_card-02-men-2_054", "p_card-02-men-1_006", "p_card-02-act-1_071"))
    ]
    state = GameState(page_fields={"candidates": cands, "rerolls_left": 1, "excludes_left": 3})
    seen, acted = [], []

    def decide(page, st, n_options=None):
        seen.append(st.page_fields.get("rerolls_left"))
        if st.page_fields.get("rerolls_left"):  # 第一次：要刷新；候选里第一个「拿」是 本番前夜（第 0 张）
            return Decision(Action(ActionKind.REROLL), "刷新", "rule", 0.9, [Candidate(Action.activate_effect(0))])
        return Decision(Action.activate_effect(2), "刷新用完：拿第 2 张", "rule", 0.9)

    opts = [NS(rect=NS(center=NS(x=150 + 200 * i, y=700)), card=None) for i in range(3)]
    monkeypatch.setattr("hiro.vision.card_select.select_card_options", lambda img: opts)
    monkeypatch.setattr("hiro.vision.card_select.recommended_index", lambda img, options: None)
    h = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    h.s = NS(state=state, observe=lambda **k: NS(fields={}), record=lambda *a, **k: None)
    h.deciders = NS(decide=decide)
    h.rec = NS(log=lambda *a, **k: None)
    h._note_fallback = lambda *a: None
    h._rerolls_this_page = hif.HiroHifStrategy.MAX_REROLLS_PER_PAGE  # 本页已到上限
    h._act = lambda commit, action, **k: acted.append(action) or NS(ok=True)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr("hiro.vision.extract.extract", lambda page, img: ({"options": cands}, 0.0))
    h.on_select_card()
    assert seen == [1, 0] and acted == [Action.activate_effect(2)]


def test_select_card_rereads_until_recognized(monkeypatch):
    """extract 返回的是 options：全认出就不再等；有没认出的就重读，取认出最多的那次。"""
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif

    reads = iter([[None, None, None], ["a", None, "c"], ["a", "b", "c"], ["a", "b", "c"]])
    calls, slept, observed = [], [], []

    def extract(page, img):
        calls.append(page)
        return {"options": [{"slot": i, "card_id": c} for i, c in enumerate(next(reads))]}, 0.0

    opts = [NS(rect=NS(center=NS(x=150 + 200 * i, y=700)), card=None) for i in range(3)]
    h = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    h.s = NS(state=GameState(page_fields={"candidates": [{"card_id": "a"}]}), observe=lambda **k: observed.append(k["fields"]) or NS(fields={}),
             record=lambda *a, **k: None)  # fmt: skip
    h.deciders = NS(decide=lambda *a, **k: Decision(Action.activate_effect(0), "拿", "rule", 0.9))
    h.rec, h._note_fallback, h._rerolls_this_page = NS(log=lambda *a, **k: None), lambda *a: None, 0
    h._act = lambda commit, action, **k: NS(ok=True)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(hif, "sleep", slept.append)
    monkeypatch.setattr("hiro.vision.extract.extract", extract)
    monkeypatch.setattr("hiro.vision.card_select.select_card_options", lambda img: opts)
    monkeypatch.setattr("hiro.vision.card_select.recommended_index", lambda img, options: None)
    h.on_select_card()
    assert len(calls) == 3 and len(slept) == 2  # 第 3 次全认出就停
    assert [o["card_id"] for o in observed[0]["options"]] == ["a", "b", "c"]


def _confirm_env(monkeypatch, texts):
    from types import SimpleNamespace as NS

    from hiro.contracts import Action, ActionKind, Decision
    from hiro.produce import hif

    clicks, notes = [], []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: "img", click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(hif, "screen_texts", lambda img: texts)
    fake = NS(s=NS(note=notes.append))
    d = Decision(Action(ActionKind.REROLL), "刷新", "rule", 0.8)
    return hif, fake, d, clicks


def test_reroll_confirm_taps_reroll_button_with_arrow(monkeypatch):
    """第一次刷新弹「スキルカード再抽選確認」：按钮读成「→ 再抽選する」也要认，点它；「次回以降表示しない」不动（协调 11-14）。"""
    import os
    from pathlib import Path

    from hiro.vision.ocr import screen_texts

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "settings"
        / "reroll-confirm-acct2.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 settings/reroll-confirm-acct2.png")
    texts = screen_texts(cv2.imread(str(path)))
    hif, fake, d, clicks = _confirm_env(monkeypatch, texts)
    assert hif.HifCore._reroll_confirm(fake, d) is True
    assert (
        len(clicks) == 1 and clicks[0][0] > 380 and abs(clicks[0][1] - 1160) < 15
    )  # 右边的「再抽選する」，不是キャンセル


def test_reroll_confirm_with_jewel_cancels(monkeypatch):
    from hiro.vision.ocr import Box, Text

    texts = [Text("スキルカード再抽選確認", Box(60, 647, 409, 681)), Text("ジュエルを30個消費して再抽選しますか?", Box(63, 733, 612, 759)),
             Text("キャンセル", Box(158, 1142, 311, 1180)), Text("→ 再抽選する", Box(396, 1142, 590, 1178))]  # fmt: skip
    hif, fake, d, clicks = _confirm_env(monkeypatch, texts)
    assert hif.HifCore._reroll_confirm(fake, d) is False
    assert clicks == [(234, 1161)] and "ジュエル" in d.warnings[-1]
