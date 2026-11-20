"""主动查看牌组：打开 → 读 → 关闭；找不到按钮 / 没打开不点后续。"""

import hiro.produce.deck_review as dr


def test_review_deck_happy_path(monkeypatch):
    import hiro.vision.deck as vd

    state = {"open": False}
    monkeypatch.setattr(vd, "find_deck_button", lambda img: (600, 1210))
    monkeypatch.setattr(vd, "is_deck_list", lambda img: state["open"])
    monkeypatch.setattr(vd, "read_deck_list", lambda img: {"deck": [{"card_id": "a", "upgrade": 0}], "deck_size": 1, "problems": []})
    clicks = []

    def click(x, y):
        clicks.append((x, y))
        state["open"] = (x, y) == (600, 1210)

    t = [0.0]
    r = dr.review_deck(shoot=lambda: None, click=click, swipe=lambda *a: None, ocr_texts=lambda img: [("閉じる", (360, 1180))],
                       sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])  # fmt: skip
    assert r["deck"] == [{"card_id": "a", "upgrade": 0}] and r["opened"] and r["closed"] and clicks == [(600, 1210), (360, 1180)]


def test_review_deck_no_button(monkeypatch):
    import hiro.vision.deck as vd

    monkeypatch.setattr(vd, "find_deck_button", lambda img: None)
    r = dr.review_deck(shoot=lambda: None, click=lambda *a: (_ for _ in ()).throw(AssertionError()), swipe=None, ocr_texts=None)
    assert r["deck"] is None and not r["opened"]


def test_review_deck_multi_screen_uses_merge(monkeypatch):
    """一屏不全：每屏截一张往下滑，滑不动了停；整组交给识屏的 merge_deck_list（没有时两两拼）。读完点「閉じる」。"""
    import numpy as np

    import hiro.vision.deck as vd

    frames = [np.full((1280, 720, 3), v, np.uint8) for v in (10, 60, 110, 110)]  # 第 3、4 屏一样 = 到底
    state = {"open": False, "i": 0}
    monkeypatch.setattr(vd, "find_deck_button", lambda img: (558, 1200))
    monkeypatch.setattr(vd, "is_deck_list", lambda img: state["open"])
    monkeypatch.setattr(vd, "read_deck_list", lambda img: {"deck": None, "deck_size": 22, "problems": ["只看到 12 / 22 张"]})
    got = {}
    whole = [{"card_id": f"c{i}", "upgrade": 0} for i in range(22)]
    # 拼到第 3 屏才完整（读完一屏拼一次，完整就停）
    monkeypatch.setattr(vd, "merge_deck_list", lambda reads: got.__setitem__("n", len(reads)) or (
        {"deck": whole, "deck_size": 22, "complete": True, "problems": []} if len(reads) >= 3 else {"deck": None, "deck_size": 22, "problems": ["不全"]}),
        raising=False)  # fmt: skip
    clicks = []

    def click(x, y):
        clicks.append((x, y))
        state["open"] = (x, y) == (558, 1200)

    def swipe(*a):
        state["i"] = min(state["i"] + 1, len(frames) - 1)

    t = [0.0]
    r = dr.review_deck(shoot=lambda: frames[state["i"]], click=click, swipe=swipe, ocr_texts=lambda img: [],
                       sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])  # fmt: skip
    assert got["n"] == 3 and r["deck"] == whole and r["screens"] == 3
    assert r["closed"] and clicks[-1] == dr.CLOSE_TAP  # OCR 没读到「閉じる」也按固定位置关


def test_empty_deck_triggers_review_without_flag(monkeypatch):
    """本战开局 produce.deck 为空（第十九局根因）：不开 HIRO_DECK_REVIEW 也读一次，读到后用 DECK_LIST 观测写回牌组。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    from hiro.contracts import GameState

    monkeypatch.delenv("HIRO_DECK_REVIEW", raising=False)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda *a: None, swipe=lambda *a: None))
    calls, observed = [], []
    deck = [{"card_id": "c0", "upgrade": 0}]
    monkeypatch.setattr(dr, "review_deck", lambda **kw: calls.append(1) or {"deck": deck, "deck_size": 1, "problems": [], "opened": True,
                                                                           "closed": True, "page": {"deck": deck, "deck_size": 1, "complete": True}})  # fmt: skip
    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.exam_count, st._reviewed_at_exam = 0, None
    st.s = NS(state=GameState(produce={"deck": []}), trace=NS(next_seq=lambda: 1), emit=lambda ev: None, note=lambda t: None,
              observe=lambda **kw: observed.append(kw))  # fmt: skip
    st._maybe_review_deck()
    assert calls == [1] and observed and observed[0]["scene"] == "DECK_LIST" and observed[0]["fields"]["deck"] == deck
    st._maybe_review_deck()  # 同一考试段只读一次
    assert calls == [1]
    st2 = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st2.exam_count, st2._reviewed_at_exam = 0, None
    st2.s = NS(state=GameState(produce={"deck": deck}))
    st2._maybe_review_deck()  # 牌组已知、没开开关：不读
    assert calls == [1]


def _open_env(monkeypatch, opens_at):
    """opens_at：第几次点牌组按钮之后（或第几秒）一览出现。"""
    import hiro.vision.deck as vd

    state = {"open": False, "taps": 0, "t": 0.0}
    monkeypatch.setattr(vd, "find_deck_button", lambda img: None if state["open"] else (558, 1200))
    monkeypatch.setattr(vd, "is_deck_list", lambda img: state["open"] or (opens_at[0] == "time" and state["t"] >= opens_at[1]))
    monkeypatch.setattr(vd, "read_deck_list", lambda img: {"deck": [{"card_id": "a", "upgrade": 0}], "deck_size": 1, "problems": []})
    clicks = []

    def click(x, y):
        clicks.append((x, y))
        if (x, y) == (558, 1200):
            state["taps"] += 1
            if opens_at[0] == "tap" and state["taps"] >= opens_at[1]:
                state["open"] = True
        elif (x, y) == dr.CLOSE_TAP:
            state["open"] = False
            opens_at[:] = ["never", 0]

    def sleep(s):
        state["t"] += s

    return state, clicks, dict(shoot=lambda: None, click=click, swipe=lambda *a: None, ocr_texts=lambda img: [], sleep=sleep,
                               clock=lambda: state["t"])  # fmt: skip


def test_first_tap_swallowed_then_retap_opens(monkeypatch):
    """第二十局开局：第一下没生效（转场 / 断线重连后），5 秒后画面还是原页面就再点一次。"""
    state, clicks, kw = _open_env(monkeypatch, ["tap", 2])
    r = dr.review_deck(**kw)
    assert r["opened"] and r["deck"] and clicks.count((558, 1200)) == 2 and state["t"] >= dr.RETAP_AFTER


def test_late_list_is_closed_after_giving_up(monkeypatch):
    """一览在放弃之后才出来：关掉，不留在画面上（第二十局被 KAA 判成 SELECT_PITEM 停机）。"""
    state, clicks, kw = _open_env(monkeypatch, ["time", dr.OPEN_TIMEOUT + 1.0])
    monkeypatch.setattr(dr, "RETAP_AFTER", 999)  # 只看「晚出来」这条
    r = dr.review_deck(**kw)
    assert r["deck"] is None and r["opened"] and r["closed"] and clicks[-1] == dr.CLOSE_TAP


def test_review_deck_waits_for_bounce_and_stops_when_complete(monkeypatch):
    """第二十三局：滑到底后列表回弹，每屏都截在动的时候，相邻屏一直不一样 → 拍满 6 屏、58.8s。
    现在滑完等两帧一样（停稳）再截，读一屏拼一次，拼出整副就不再滑。"""
    import numpy as np

    import hiro.vision.deck as vd

    # 每次滑动后：先两帧在动（回弹），然后停在这一屏
    screens = [10, 60, 110]
    state = {"open": False, "screen": 0, "settle": 0}
    monkeypatch.setattr(vd, "find_deck_button", lambda img: (558, 1200))
    monkeypatch.setattr(vd, "is_deck_list", lambda img: state["open"])
    reads = []
    monkeypatch.setattr(vd, "read_deck_list", lambda img: reads.append(int(img[600, 300, 0])) or {"deck": None, "deck_size": 21, "problems": []})
    whole = [{"card_id": f"c{i}", "upgrade": 0} for i in range(21)]
    monkeypatch.setattr(vd, "merge_deck_list", lambda rs: {"deck": whole if len(rs) >= 2 else None, "deck_size": 21, "complete": len(rs) >= 2,
                                                            "problems": []}, raising=False)  # fmt: skip

    def shoot():
        if state["settle"] > 0:
            state["settle"] -= 1
            return np.full((1280, 720, 3), 200 + state["settle"] * 20, np.uint8)  # 还在动：每帧不一样
        return np.full((1280, 720, 3), screens[state["screen"]], np.uint8)

    def swipe(*a):
        state["screen"] += 1
        state["settle"] = 2

    def click(x, y):
        state["open"] = (x, y) == (558, 1200)

    t = [0.0]
    r = dr.review_deck(shoot=shoot, click=click, swipe=swipe, ocr_texts=lambda img: [], sleep=lambda s: t.__setitem__(0, t[0] + s),
                       clock=lambda: t[0])  # fmt: skip
    assert r["deck"] == whole and r["screens"] == 2 and state["screen"] == 1  # 第 2 屏拼完整就停，没再滑
    assert reads == [10, 60]  # 读的是停稳后的帧，每屏只读一次


def _strategy(monkeypatch, deck, pending, calls, ok=True):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    from hiro.contracts import GameState

    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda *a: None, swipe=lambda *a: None))
    page = {"deck": deck, "deck_size": len(deck), "complete": True}
    monkeypatch.setattr(dr, "review_deck", lambda **kw: calls.append(1) or {"deck": deck if ok else None, "deck_size": len(deck), "opened": True,
                                                                           "closed": True, "problems": [] if ok else ["读不全"], "page": page})  # fmt: skip
    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.exam_count, st._reviewed_at_exam = 0, 0  # 这一考试段已经读过一次（开局那次）
    gs = GameState(produce={"deck": deck, "step": 3})
    gs.pending_review = ["produce.deck"] if pending else []
    st.s = NS(state=gs, trace=NS(next_seq=lambda: 1), emit=lambda ev: None, note=lambda t: None, observe=lambda **kw: None)
    return st


def test_pending_deck_review_runs_without_flag(monkeypatch):
    """DMM mainverify2（11-17）：开局读过牌组（19 张）之后，支援事件给的「頭上に注意！」跳过剧情时没有获得页，账本漏记，
    R1 搜索整场跟丢。剧情跳过后牌组标成待复核（pending_review）：下次到选行动页不开 HIRO_DECK_REVIEW 也读一次。"""
    monkeypatch.delenv("HIRO_DECK_REVIEW", raising=False)
    calls = []
    st = _strategy(monkeypatch, [{"card_id": "c0", "upgrade": 0}], True, calls)
    st._maybe_review_deck()
    assert calls == [1]
    calls2 = []
    st2 = _strategy(monkeypatch, [{"card_id": "c0", "upgrade": 0}], False, calls2)
    st2._maybe_review_deck()  # 没有待复核、开局已读过：不读
    assert calls2 == []


def test_pending_deck_review_failed_read_not_retried_same_step(monkeypatch):
    """读不全时待复核还在：同一天（同一 step）不再重读，免得每帧开一次一览；到下一天再试。"""
    monkeypatch.delenv("HIRO_DECK_REVIEW", raising=False)
    calls = []
    st = _strategy(monkeypatch, [{"card_id": "c0", "upgrade": 0}], True, calls, ok=False)
    st._maybe_review_deck()
    st._maybe_review_deck()
    assert calls == [1]
    st.s.state.produce["step"] = 4
    st._maybe_review_deck()
    assert calls == [1, 1]


def test_pending_deck_review_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HIRO_DECK_REVIEW", "0")
    calls = []
    st = _strategy(monkeypatch, [{"card_id": "c0", "upgrade": 0}], True, calls)
    st._maybe_review_deck()
    assert calls == []


def _rows_list(monkeypatch, retry_ok: bool):
    """一览 4 行 16 张：屏位置 0（顶，第 1–3 行）/ 1（底，第 2–4 行）。逐屏拼接出错（DMM qualifyverify4：拼出 25 张、标签 16 张）。"""
    import numpy as np

    import hiro.vision.deck as vd

    state = {"open": False, "pos": 0, "swipes": []}
    monkeypatch.setattr(vd, "find_deck_button", lambda img: (558, 1200))
    monkeypatch.setattr(vd, "is_deck_list", lambda img: state["open"])
    monkeypatch.setattr(vd, "read_deck_list", lambda img: {"deck": None, "deck_size": 16, "pos": int(img[600, 300, 0]) // 100, "problems": ["只看到 12 / 16 张"]})
    monkeypatch.setattr(vd, "merge_deck_list", lambda rs: {"deck": None, "deck_size": 16, "complete": False, "problems": ["拼出 25 张，标签 16 张"]},
                        raising=False)  # fmt: skip
    whole = [{"card_id": f"c{i}", "upgrade": 0} for i in range(16)]

    def pages(top, bottom):
        state["merged"] = (top["pos"], bottom["pos"])
        ok = retry_ok and (top["pos"], bottom["pos"]) == (0, 1)
        return {"deck": whole if ok else None, "deck_size": 16, "complete": ok, "problems": [] if ok else ["第 4 行两屏读得不一样"]}

    monkeypatch.setattr(vd, "merge_deck_pages", pages)

    def swipe(x1, y1, x2, y2, d):
        state["swipes"].append("down" if y1 > y2 else "up")
        state["pos"] = 1 if y1 > y2 else 0

    def click(x, y):
        state["open"] = (x, y) == (558, 1200)

    t = [0.0]
    r = dr.review_deck(shoot=lambda: np.full((1280, 720, 3), 10 + 100 * state["pos"], np.uint8), click=click, swipe=swipe,
                       ocr_texts=lambda img: [], sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])  # fmt: skip
    return r, state, whole


def test_count_mismatch_rereads_top_and_bottom(monkeypatch):
    r, state, whole = _rows_list(monkeypatch, retry_ok=True)
    assert r["deck"] == whole and state["merged"] == (0, 1)  # 首屏（顶）+ 末屏（底）按行号拼
    assert "up" in state["swipes"] and state["swipes"][-1] == "down"
    assert any("按标签 16 张" in p for p in r["problems"])


def test_count_mismatch_reread_still_wrong_writes_nothing(monkeypatch):
    r, _, _ = _rows_list(monkeypatch, retry_ok=False)
    assert r["deck"] is None and any("不写牌组" in p for p in r["problems"]) and r["closed"]


def test_empty_deck_failed_read_retried_next_visit(monkeypatch):
    """协调 11-18 初 MASTER：续跑后牌组按钮点了两次没反应、一览 20 秒后才弹出，读牌放弃。原来失败也算「这一考试段读过」，
    之后几次选行动都没再读，牌组 0 张进课程（GNRPA「拼不了编成」）。牌组为空时失败了下次选行动再读，同一考试段最多 EMPTY_REVIEW_TRIES 次。"""
    import hiro.produce.hif as hif

    monkeypatch.delenv("HIRO_DECK_REVIEW", raising=False)
    calls = []
    st = _strategy(monkeypatch, [], False, calls, ok=False)
    st._reviewed_at_exam = None  # 开局 / 续跑：这一考试段还没读过
    for _ in range(hif.HiroHifStrategy.EMPTY_REVIEW_TRIES + 2):
        st._maybe_review_deck()
    assert len(calls) == hif.HiroHifStrategy.EMPTY_REVIEW_TRIES  # 每次选行动都再试，到上限停
    st.exam_count = 1  # 下一考试段重新计数
    st._maybe_review_deck()
    assert len(calls) == hif.HiroHifStrategy.EMPTY_REVIEW_TRIES + 1


def test_empty_deck_retry_succeeds_then_stops(monkeypatch):
    """第二次读到了：写回牌组，之后不再读。"""
    monkeypatch.delenv("HIRO_DECK_REVIEW", raising=False)
    calls, results = [], [None, [{"card_id": "c0", "upgrade": 0}]]
    st = _strategy(monkeypatch, [], False, calls, ok=False)
    st._reviewed_at_exam = None

    def review(**kw):
        calls.append(1)
        deck = results[min(len(calls), len(results)) - 1]
        if deck:
            st.s.state.produce["deck"] = deck  # 观测 DECK_LIST 写回（reduce）在替身里直接写
        return {"deck": deck, "deck_size": 1 if deck else None, "opened": bool(deck), "closed": True, "problems": [] if deck else ["没打开"],
                "page": {"deck": deck, "deck_size": 1, "complete": True}}  # fmt: skip

    monkeypatch.setattr(dr, "review_deck", review)
    for _ in range(4):
        st._maybe_review_deck()
    assert len(calls) == 2
