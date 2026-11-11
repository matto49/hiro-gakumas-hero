# 来源：digital-hiro tests/test_hook_takeover.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""用户规则 3a / 3f：KAA 的每个策略钩子都由 HiroHifStrategy 自己接管；没接管 / 读不出 / 决策不合法时停下（UnhandledPage），不落到 KAA 默认的 commit(0)。"""

import inspect

import pytest

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, Decision, ExecutionResult, GameState
from hiro.produce.hif import HiroHifStrategy, UnhandledPage


def _kaa_hooks() -> set[str]:
    from kaa.tasks.produce.new.strategies.base import ProduceStrategy
    from kaa.tasks.produce.new.strategies.hif_grind import HifGrindStrategy

    return {n for cls in (ProduceStrategy, HifGrindStrategy) for n, _ in inspect.getmembers(cls, inspect.isfunction) if n.startswith("on_")}


def test_every_kaa_hook_is_overridden():
    missing = sorted(h for h in _kaa_hooks() if h not in HiroHifStrategy.__dict__)
    assert not missing, f"这些 KAA 钩子还会走 KAA 默认：{missing}"


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
    st = HiroHifStrategy.__new__(HiroHifStrategy)
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
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}))
    st = _strategy()
    st.on_pdrink_max_confirm(object())
    assert clicks == [hif.REWARD_SKIP_CANCEL] and st.s.recovers[-1][0] == "dialog"
    with pytest.raises(UnhandledPage):
        st.on_pdrink_max_confirm(object())


def test_reward_skip_confirmed_after_own_full_skip(monkeypatch):
    """饮料满时我们勾了「受け取らない」（_skip_incoming_drink）→「報酬スキップ」确认放行（qualify6 00:08 实机：之前误停）。"""
    import importlib

    import hiro.produce.hif as hif

    common = importlib.import_module("kaa.tasks.produce.shared.common")
    clicked = []
    monkeypatch.setattr(common.ProduceInterrupt, "_check_pdrink_max_confirm", staticmethod(lambda img: clicked.append(1)))
    monkeypatch.setattr(hif, "device", type("D", (), {"screenshot": staticmethod(lambda: None)}))
    st = _strategy()
    st._skip_incoming_drink = True
    st.on_pdrink_max_confirm(object())
    assert clicked == [1] and st._skip_incoming_drink is False


def test_practice_page_stops():
    with pytest.raises(UnhandledPage):
        _strategy().on_practice_entered(object())


def test_unknown_screen_stops_after_limit(monkeypatch):
    """画面持续认不出超过 UNKNOWN_STOP_AFTER：调 strategy._stop（停下报警），不无限空转。"""
    import time as _time

    from kaa.tasks.produce.new.consts import Scene, SceneType

    from hiro.loop.controller import HiroController

    c = HiroController.__new__(HiroController)
    c._texts = ["?"]
    stopped = []
    c.strategy = type("S", (), {"_stop": lambda self, page, why: stopped.append(page)})()
    monkeypatch.setattr(HiroController, "session", None)
    import kotonebot

    # _watch_unknown 先截一帧查游戏是否还活着：单独跑时 kotonebot 上下文没初始化，给 device 打桩
    monkeypatch.setattr(kotonebot, "device", type("D", (), {"screenshot": lambda self: None})())
    monkeypatch.setattr(HiroController, "_check_game_alive", lambda self, shot: None)
    t = [1000.0]
    monkeypatch.setattr(_time, "monotonic", lambda: t[0])
    c._watch_unknown(Scene(SceneType.UNKNOWN))
    t[0] += 60
    c._watch_unknown(Scene(SceneType.UNKNOWN))
    assert not stopped
    t[0] += 200
    c._watch_unknown(Scene(SceneType.UNKNOWN))
    assert stopped == ["unknown"]


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

    class T:
        def __init__(self, text, y=1100):
            self.text = text
            self.rect = type("R", (), {"center": type("C", (), {"y": y, "x": 300})()})()

    clicks = []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st = _strategy()
    st._retries_so_far = lambda label: 0
    monkeypatch.setattr(hif, "ocr", type("O", (), {"ocr": staticmethod(lambda *a, **k: [T("ジュエルを消費して再挑戦"), T("キャンセル")])}))
    assert st._click_retry("r2", T("再挑戦"), []) is None
    n0 = len(clicks)  # 确认框在第一次点确认后关掉（点过确认、确认框没了才算完成）
    monkeypatch.setattr(hif, "ocr", type("O", (), {"ocr": staticmethod(lambda *a, **k: [] if len(clicks) > n0 + 1 else
                                                                         [T("再挑戦券を1枚使用"), T(hif.HiroHifStrategy.RETRY_CONFIRM[0])])}))  # fmt: skip
    assert st._click_retry("r2", T("再挑戦"), []) == "ticket"


def test_retry_confirm_reclicks_until_closed(monkeypatch):
    """第二十五局 R2：确认框淡入时点了「決定」没响应，以前点一下就当完成、确认框挂 46s。现在确认框还在就隔 1.5s 再点，
    关了才返回；按钮读成「く決定」也认。"""
    import hiro.produce.hif as hif

    class T:
        def __init__(self, text, y=1160):
            self.text = text
            self.rect = type("R", (), {"center": type("C", (), {"y": y, "x": 500})()})()

    t = {"now": 0.0}
    clicks = []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}))
    monkeypatch.setattr(hif, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    monkeypatch.setattr(hif.time, "monotonic", lambda: t["now"])
    dialog = [T("再挑戦確認", 400), T("再挑戦チケットを消費して、試験に再挑戦しますか？", 650), T("メキャンセル"), T("く決定")]
    # 第 1 次点「決定」没响应（淡入中），第 2 次才关
    monkeypatch.setattr(hif, "ocr", type("O", (), {"ocr": staticmethod(lambda *a, **k: dialog if len(clicks) < 3 else [])}))
    st = _strategy()
    st._retries_so_far = lambda label: 0
    assert st._click_retry("r2", T("再挑戦", 1000), []) in ("free", "ticket")
    assert len(clicks) == 3 and t["now"] < hif.HiroHifStrategy.RETRY_CONFIRM_WAIT  # 再挑戦 + 決定 ×2，没等满 15s



def test_retry_capped():
    st = _strategy()
    st._retries_so_far = lambda label: 3
    assert st._click_retry("r2", object(), []) is None


def test_retry_cost_from_owned_tickets():
    """按确认框「所持 a → b」判断：减 1 = 用了券，不变 = 免费；读不到 None（第二十五局用了券 260→259 却记成 free）。"""
    from hiro.produce.hif import retry_cost

    def T(text, x, y):
        return type("T", (), {"text": text, "rect": type("R", (), {"center": type("C", (), {"x": x, "y": y})()})()})()

    used = [T("所持", 134, 995), T("260", 323, 992), T("→", 421, 990), T("259", 509, 992), T("残り再挑戦回数", 166, 1048), T("3/3", 465, 1048)]
    assert retry_cost(used) == "ticket"
    free = [T("所持", 134, 995), T("267", 323, 992), T("→", 421, 990), T("267", 509, 992), T("本日あと1回無料", 430, 950)]
    assert retry_cost(free) == "free"
    assert retry_cost([T("所持", 134, 995), T("267→267", 420, 992)]) == "free"  # 读成一个框
    assert retry_cost([T("再挑戦確認", 200, 400)]) is None
    from hiro.produce.hif import retry_tickets

    assert retry_tickets(used) == (260, 259)


def test_retry_records_ticket_use_and_left(monkeypatch):
    """记账：确认框「所持 260 → 259」→ 返回 ticket，note 写明用了券、还剩 259 张。"""
    import hiro.produce.hif as hif

    class T:
        def __init__(self, text, x=300, y=1160):
            self.text = text
            self.rect = type("R", (), {"center": type("C", (), {"y": y, "x": x})()})()

    t = {"now": 0.0}
    clicks = []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}))
    monkeypatch.setattr(hif, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    monkeypatch.setattr(hif.time, "monotonic", lambda: t["now"])
    dialog = [T("再挑戦確認", 200, 400), T("所持", 134, 995), T("260", 323, 992), T("→", 421, 990), T("259", 509, 992), T("決定", 500, 1160)]
    monkeypatch.setattr(hif, "ocr", type("O", (), {"ocr": staticmethod(lambda *a, **k: dialog if len(clicks) < 2 else [])}))
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


def test_live_landscape_skips_page_checks(monkeypatch):
    """第三十一局：横屏 LIVE 期间主循环不跑快速判页 / KAA 判页（KAA 拿竖屏 rect 裁 1280x720，每 2s 报错），记一次进展后等。"""
    import time as _time

    import kotonebot
    import numpy as np

    from hiro.loop.controller import HiroController
    from hiro.loop.repeat_guard import Guard

    live = np.zeros((720, 1280, 3), np.uint8)
    live[200:500, 300:900] = 120
    monkeypatch.setattr(kotonebot, "device", type("D", (), {"screenshot": staticmethod(lambda: live)}))
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    touched = []
    g = Guard()
    g.touch = lambda: touched.append(1)
    monkeypatch.setattr(HiroController, "session", type("S", (), {"guard": g})())
    lg = type("L", (), {"tick": lambda self, img: False, "in_live": lambda self, img: True})()
    monkeypatch.setattr(HiroController, "_landscape", lg)
    c = HiroController.__new__(HiroController)
    c._quick_page = lambda: (_ for _ in ()).throw(AssertionError("LIVE 中不该判页"))
    c._update()
    assert touched == [1]


def test_live_tick_taps_start_and_warns_when_still(monkeypatch):
    """横屏帧：每 2s OCR 一次，认到 TAP TO START 就点；同一画面 60s 不变报一次警。"""
    import time as _time

    import kotonebot
    import numpy as np

    import hiro.produce.ops as ops
    from hiro.loop.controller import HiroController

    t = [0.0]
    clicks, notes = [], []
    monkeypatch.setattr(_time, "monotonic", lambda: t[0])
    monkeypatch.setattr(kotonebot, "device", type("D", (), {"click": staticmethod(lambda x, y: clicks.append((x, y)))}))
    monkeypatch.setattr(ops, "ocr_boxes", lambda img: [("TAP TO START", 1.0, (551, 597, 731, 621))])
    monkeypatch.setattr(HiroController, "session", type("S", (), {"note": lambda self, n: notes.append(n)})())
    c = HiroController.__new__(HiroController)
    img = np.full((720, 1280, 3), 80, np.uint8)
    c._live_tick(img)
    assert clicks == [(641, 609)]
    t[0] = 1.0
    c._live_tick(img)  # 不到 2s：不再 OCR
    assert len(clicks) == 1
    t[0] = 61.0
    c._live_tick(img)
    assert notes and "没变" in notes[0]


def test_finals_hopeless_and_stop_loss_cancels_ticket(monkeypatch):
    """RETRY_STOP_LOSS：本战差距翻不了盘时，再挑戦确认框要用券就点キャンセル，返回 None（Win2：R2 19 万、夺冠要 83.5 万）。"""
    import hiro.produce.hif as hif

    f = {"score": 432466, "round_score": 190569,
         "entries": [{"score": 1076914}, {"score": 816254}, {"score": 432466}]}  # fmt: skip
    assert hif.finals_hopeless(f) and "R2" in hif.finals_hopeless(f)
    assert hif.finals_hopeless({**f, "round_score": None}) is None  # 读数不全不判
    close = {"score": 1000000, "round_score": 500000, "entries": [{"score": 1080000}, {"score": 1000000}]}
    assert hif.finals_hopeless(close) is None  # 只差 8 万：值得再打

    class T:
        def __init__(self, text, y=1100):
            self.text = text
            self.rect = type("R", (), {"center": type("C", (), {"y": y, "x": 300})()})()

    clicks = []
    monkeypatch.setattr(hif, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a)), "screenshot": staticmethod(lambda: None)}))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st = _strategy()
    st._retries_so_far = lambda label: 0
    cancel = T("キャンセル")
    monkeypatch.setattr(hif, "ocr", type("O", (), {"ocr": staticmethod(lambda *a, **k: [T("再挑戦券を1枚使用"), cancel,
                                                                                          T(hif.HiroHifStrategy.RETRY_CONFIRM[0])])}))  # fmt: skip
    assert st._click_retry("r2", T("再挑戦"), [], allow_ticket=False) is None
    assert clicks[-1] == (cancel.rect,)  # 最后点的是キャンセル，不是確定
