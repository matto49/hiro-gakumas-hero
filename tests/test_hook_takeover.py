"""用户规则 3a / 3f：每个页面由 HifCore 自己处理；读不出 / 决策不合法时停下（UnhandledPage），不退回默认选第一个（原来是 KAA 的 commit(0)）。
「KAA 的每个策略钩子都被接管」那条测试随 KAA 策略类壳（去 KAA 第 3 批）删了。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, Decision, ExecutionResult, GameState
from hiro.produce.hif import HifCore, UnhandledPage
from hiro.vision.ocr import Box, Text

import pytest


class StubSession:
    def __init__(self, fields=None):
        self.fields, self.state, self.records, self.recovers = fields or {}, GameState(), [], []

    def observe(self, scene=None, **_):
        self.state = GameState(page_fields=dict(self.fields.get(scene, {})))
        return scene

    def verify(self, action, *, page_before, **_):
        return ExecutionResult(action=action, ok=True, page_before=page_before)

    def record(self, obs, **kw):
        self.records.append((obs, kw))

    def recover(self, kind, detail):
        self.recovers.append((kind, detail))

    def note(self, text):
        pass


def _strategy(fields=None):
    st = HifCore.__new__(HifCore)
    st.s = StubSession(fields)
    st.deciders = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True)
    st.CHANGE_OBSERVE_TRIES = 1
    return st


def test_event_page_invalid_choice_stops_instead_of_option0():
    st = _strategy()
    st.deciders.decide = lambda page, state, **kw: Decision(Action.event_select(7), "越界", "rule", 0.5)
    picked = []
    with pytest.raises(UnhandledPage):
        st._event_page("STUDY", picked.append, 3)
    assert picked == [] and st.s.recovers[0][0] == "unhandled"


def test_removal_unreadable_stops_without_tapping():
    st = _strategy()

    class Ctx:
        def commit(self, i):  # KAA 默认会走这里
            raise AssertionError("不应调用 KAA commit")

    with pytest.raises(UnhandledPage):
        st.on_skill_card_removal(Ctx())
    assert st.s.recovers and st.s.recovers[-1][0] == "unhandled"


def test_reward_skip_without_decision_cancels_then_stops(monkeypatch):
    """没有放弃新饮料的决策：第一次点キャンセル 回饮料页重新决策，第二次停下（qualify6 续跑实机）。"""
    import hiro.produce.hif as hif

    clicks = []
    monkeypatch.setattr(
        hif,
        "device",
        type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}),
    )
    st = _strategy()
    st.on_pdrink_max_confirm(object())
    assert clicks == [hif.REWARD_SKIP_CANCEL] and st.s.recovers[-1][0] == "dialog"
    with pytest.raises(UnhandledPage):
        st.on_pdrink_max_confirm(object())


def test_reward_skip_confirmed_after_own_full_skip(monkeypatch):
    """饮料满时我们勾了「受け取らない」（_skip_incoming_drink）→「報酬スキップ」确认放行（qualify6 00:08 实机：之前误停）。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    clicked = []
    monkeypatch.setattr(hif, "tpl", lambda name: NS(exists=lambda img: True))  # 确认框标题两帧都在
    monkeypatch.setattr(hif, "tap", lambda name, img, click: clicked.append(name) or True)
    monkeypatch.setattr(
        hif, "device", type("D", (), {"screenshot": staticmethod(lambda: None), "click": staticmethod(lambda *a: None)})
    )
    st = _strategy()
    st._skip_incoming_drink = True
    st.on_pdrink_max_confirm(object())
    assert clicked == ["Common.ButtonConfirm"] and st._skip_incoming_drink is False


def test_practice_page_stops():
    with pytest.raises(UnhandledPage):
        _strategy().on_practice_entered(object())


def test_consult_opens_dialog_when_deck_unknown():
    """相談：决策因牌组未识别给 ShopEnd 时，先打开強化，再打开削除，都做过就照常离开（q10 第 12 天 440P 没花）。"""
    from hiro.contracts import Decision

    st = _strategy()
    st.s.state = GameState(produce={"produce_point": 440},
                           page_fields={"services": {"upgrade": {"price": 100, "available": True}, "delete": {"price": 100, "available": True}}})  # fmt: skip
    end = Decision(Action(ActionKind.SHOP_END), "没有值得买的", "rule", 0.5, warnings=["牌组未识别：没有做删卡 / 强化"])
    d1 = st._consult_open_dialog(end, set())
    assert d1.action.kind is ActionKind.SHOP_UPGRADE_CARD
    d2 = st._consult_open_dialog(end, {"upgrade"})
    assert d2.action.args == {"service": "delete"}
    assert st._consult_open_dialog(end, {"upgrade", "delete"}).action.kind is ActionKind.SHOP_END


def test_retry_cancels_when_jewel(monkeypatch):
    """再挑戦确认框出现ジュエル：点キャンセル，返回 None；出现券：确认，记 ticket。"""
    import hiro.produce.hif as hif

    def T(text, y=1100):
        return Text(text, Box(300 - 10, y - 10, 300 + 10, y + 10))

    clicks = []
    monkeypatch.setattr(
        hif,
        "device",
        type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st = _strategy()
    st._retries_so_far = lambda label: 0
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [T("ジュエルを消費して再挑戦"), T("キャンセル")])
    assert st._click_retry("r2", T("再挑戦"), []) is None
    n0 = len(clicks)  # 确认框在第一次点确认后关掉（点过确认、确认框没了才算完成）
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [] if len(clicks) > n0 + 1 else
                                                                         [T("再挑戦券を1枚使用"), T(hif.HifCore.RETRY_CONFIRM[0])])  # fmt: skip
    assert st._click_retry("r2", T("再挑戦"), []) == "ticket"


def test_retry_confirm_reclicks_until_closed(monkeypatch):
    """第二十五局 R2：确认框淡入时点了「決定」没响应，以前点一下就当完成、确认框挂 46s。现在确认框还在就隔 1.5s 再点，
    关了才返回；按钮读成「く決定」也认。"""
    import hiro.produce.hif as hif

    def T(text, y=1160):
        return Text(text, Box(500 - 10, y - 10, 500 + 10, y + 10))

    t = {"now": 0.0}
    clicks = []
    monkeypatch.setattr(
        hif,
        "device",
        type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(hif, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    monkeypatch.setattr(hif.time, "monotonic", lambda: t["now"])
    dialog = [
        T("再挑戦確認", 400),
        T("再挑戦チケットを消費して、試験に再挑戦しますか？", 650),
        T("メキャンセル"),
        T("く決定"),
    ]
    # 第 1 次点「決定」没响应（淡入中），第 2 次才关
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: dialog if len(clicks) < 3 else [])
    st = _strategy()
    st._retries_so_far = lambda label: 0
    assert st._click_retry("r2", T("再挑戦", 1000), []) in ("free", "ticket")
    assert len(clicks) == 3 and t["now"] < hif.HifCore.RETRY_CONFIRM_WAIT  # 再挑戦 + 決定 ×2，没等满 15s


def test_retry_capped():
    st = _strategy()
    st._retries_so_far = lambda label: 3
    assert st._click_retry("r2", object(), []) is None


def test_retry_cost_from_owned_tickets():
    """按确认框「所持 a → b」判断：减 1 = 用了券，不变 = 免费；读不到 None（第二十五局用了券 260→259 却记成 free）。"""
    from hiro.produce.hif import retry_cost

    def T(text, x, y):
        return type("T", (), {"text": text, "rect": type("R", (), {"center": type("C", (), {"x": x, "y": y})()})()})()

    used = [
        T("所持", 134, 995),
        T("260", 323, 992),
        T("→", 421, 990),
        T("259", 509, 992),
        T("残り再挑戦回数", 166, 1048),
        T("3/3", 465, 1048),
    ]
    assert retry_cost(used) == "ticket"
    free = [
        T("所持", 134, 995),
        T("267", 323, 992),
        T("→", 421, 990),
        T("267", 509, 992),
        T("本日あと1回無料", 430, 950),
    ]
    assert retry_cost(free) == "free"
    assert retry_cost([T("所持", 134, 995), T("267→267", 420, 992)]) == "free"  # 读成一个框
    assert retry_cost([T("再挑戦確認", 200, 400)]) is None
    from hiro.produce.hif import retry_tickets

    assert retry_tickets(used) == (260, 259)


def test_retry_records_ticket_use_and_left(monkeypatch):
    """记账：确认框「所持 260 → 259」→ 返回 ticket，note 写明用了券、还剩 259 张。"""
    import hiro.produce.hif as hif

    def T(text, x=300, y=1160):
        return Text(text, Box(x - 10, y - 10, x + 10, y + 10))

    t = {"now": 0.0}
    clicks = []
    monkeypatch.setattr(
        hif,
        "device",
        type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(hif, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    monkeypatch.setattr(hif.time, "monotonic", lambda: t["now"])
    dialog = [
        T("再挑戦確認", 200, 400),
        T("所持", 134, 995),
        T("260", 323, 992),
        T("→", 421, 990),
        T("259", 509, 992),
        T("決定", 500, 1160),
    ]
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: dialog if len(clicks) < 2 else [])
    st = _strategy()
    st._retries_so_far = lambda label: 0
    notes = []
    st.s.note = notes.append
    assert st._click_retry("r2", T("再挑戦", 300, 1000), []) == "ticket"
    assert any("再挑戦チケット" in n and "260 → 259" in n for n in notes)


def _study_fields():
    """第二十五局本战授業 frames/00007：识别层 3 项（第 3 项被下方对话框挡住一半），KAA 只认出 2 项。"""
    return {"STUDY": {"choices": [{"index": 0, "text": "飲食物のチェック", "center": [360, 698]},
                                  {"index": 1, "text": "基礎の確認", "center": [360, 799]},
                                  {"index": 2, "text": "声の細かいチェック", "trouble": True, "center": [360, 901]}]}}  # fmt: skip


def test_study_hidden_third_option_uses_recognized_center(monkeypatch):
    import hiro.produce.hif as hif

    clicks, kaa = [], []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda x, y: clicks.append((x, y)))}))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st = _strategy(_study_fields())
    seen = {}

    def decide(page, state, **kw):
        seen["n"] = kw.get("n_options")
        return Decision(Action.event_select(2), "第 3 项", "rule", 0.5)

    st.deciders.decide = decide
    st._act = lambda fn, action, **kw: (fn(), ExecutionResult(action=action, ok=True))[1]
    st._event_page("STUDY", kaa.append, 2)  # KAA fetch_options 只有 2 项
    assert seen["n"] == 3 and kaa == [] and clicks == [(360, 901), (360, 901)]


def test_study_visible_option_still_uses_kaa_commit(monkeypatch):
    import hiro.produce.hif as hif

    clicks, kaa = [], []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda x, y: clicks.append((x, y)))}))
    st = _strategy(_study_fields())
    st.deciders.decide = lambda page, state, **kw: Decision(Action.event_select(1), "第 2 项", "rule", 0.5)
    st._act = lambda fn, action, **kw: (fn(), ExecutionResult(action=action, ok=True))[1]
    st._event_page("STUDY", kaa.append, 2)
    assert kaa == [1] and clicks == []


def test_finals_retry_restarts_exam(monkeypatch):
    """本战最终结果页敗退、有再挑戦按钮：点了（免费 / 券）就标记考试重开。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = _strategy()
    st.rec = NS(log=lambda *a, **k: None)
    monkeypatch.setattr(
        pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", {"kind": "final", "rank": 2, "outcome": "敗退"})
    )
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [btn])
    st._click_retry = lambda label, b, texts: "free"
    st._retry_took_effect = lambda label, **kw: True  # 考试重开的核对另有测试
    assert st._finals_retry(None, "r2") is True and st.exam_restarted is True
