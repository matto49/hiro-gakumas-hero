"""新主循环的页面处理器（hiro.produce.handlers）：页面 → HifCore 方法；考试入口打完关门、换页开门、再挑戦直接重进。"""

from hiro.produce.handlers import make_handlers
from hiro.runtime.loop import DONE, STAY


class FakeCore:
    exam_restarted = False

    def __init__(self, restart_times: int = 0):
        self.calls, self.restart_times = [], restart_times

    def __getattr__(self, name):  # 其余 on_* 页面方法：记一笔名字（去掉 on_ 前缀）
        if not name.startswith("on_"):
            raise AttributeError(name)

        def hook(ctx):
            assert ctx is None  # 新主循环不给 KAA 上下文
            self.calls.append(name.removeprefix("on_"))

        return hook

    def on_exam_entered(self, ctx):
        self.calls.append("exam")
        if self.restart_times:
            self.restart_times -= 1
            self.exam_restarted = True  # 点了再挑戦


def _run(h, page):
    return h[page](None, None, None)


def test_pages_call_core_methods():
    core = FakeCore()
    h = make_handlers(core)
    assert (
        _run(h, "CONSULT") is DONE and _run(h, "SKILL_CARD_REMOVAL") is DONE and _run(h, "PDRINK_MAX_CONFIRM") is DONE
    )
    assert core.calls == ["consult", "skill_card_removal", "pdrink_max_confirm"]
    assert (
        _run(h, "HIF_FINALS_RANKING") is DONE and core.calls[-1] == "finals_ranking"
    )  # 本战名次页（Mac p13 11-17 没处理）
    assert (
        _run(h, "OUTING_STAMINA_MAX") is DONE and core.calls[-1] == "outing_stamina_max"
    )  # 原来没处理器（11-17 页面清单 P7b）
    assert _run(h, "INITIAL_CHOICE") is DONE and core.calls[-1] == "event_choice"  # 事件选项页（O12）
    assert (
        _run(h, "SPECIAL_GUIDANCE") is DONE and core.calls[-1] == "special_guidance"
    )  # 回到定制选卡页接着做（h11 11-15）
    assert "LOADING" not in h and "unknown" not in h  # 不需要处理的页没登记，交给 advance


def test_exam_entered_once_until_another_page():
    core = FakeCore()
    h = make_handlers(core)
    assert _run(h, "HIF_EXAM") is DONE
    assert _run(h, "HIF_EXAM") is STAY and _run(h, "EXAM") is STAY  # 打完的考试残帧：不重进
    assert core.calls == ["exam"]
    _run(h, "CONSULT")  # 换到别的已处理页面后，下一场考试照常进入
    assert _run(h, "HIF_EXAM") is DONE and core.calls == ["exam", "consult", "exam"]


def test_exam_retry_reenters_directly():
    core = FakeCore(restart_times=1)
    h = make_handlers(core)
    _run(h, "HIF_EXAM")  # 第一次：没过线，点了再挑戦
    assert _run(h, "HIF_EXAM") is DONE  # 再挑戦后同一场直接重进
    assert core.calls == ["exam", "exam"] and core.exam_restarted is False
    assert _run(h, "HIF_EXAM") is STAY  # 这次打完了：关门


def test_event_commit_double_clicks_and_confirms_outing_ap_dialog(monkeypatch):
    """授業 / 外出提交（HifCore._event_commit，替换 KAA Study/OutingContext.commit）：双击第 i 个按钮；外出时 AP 已满框点确定。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.classify as cl
    from hiro.vision.ocr import Box

    import pytest

    t = [0.0]
    clicks, taps = [], []
    monkeypatch.setattr(hif.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(hif, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(
        hif,
        "device",
        NS(screenshot=lambda: None, click=lambda x, y: None, double_click=lambda x, y: clicks.append((x, y))),
    )
    monkeypatch.setattr(cl, "option_regions", lambda img: [Box(80, 700, 640, 780), Box(80, 820, 640, 900)])
    shown = iter([True, False, False])
    monkeypatch.setattr(hif, "tpl", lambda name: NS(exists=lambda img: next(shown, False)))
    monkeypatch.setattr(hif, "tap", lambda name, img, click: taps.append(name))
    core = hif.HifCore.__new__(hif.HifCore)
    core._event_commit(1, outing=True)
    assert clicks == [(360, 860)] and taps == ["InProduce.OutingApMaxOutDialog.ButtonConfirm"]
    with pytest.raises(ValueError):
        core._event_commit(5, outing=False)


def test_produce_end_and_allowance_record_then_delegate(monkeypatch):
    from types import SimpleNamespace as NS

    from hiro.runtime import finish

    seen = []
    core = FakeCore()
    core.s = NS(
        observe=lambda **kw: seen.append(("observe", kw["scene"])) or kw["scene"],
        record=lambda obs, **kw: seen.append(("record", obs)),
    )
    monkeypatch.setattr(finish, "on_produce_end", lambda rt, f, hit: seen.append("finish_end") or finish.END)
    monkeypatch.setattr(finish, "on_allowance", lambda rt, f, hit: seen.append("claim") or DONE)
    h = make_handlers(core)
    assert h["ALLOWANCE"](None, None, None) is DONE
    assert h["PRODUCE_END"](None, None, None) is finish.END
    assert seen == [
        ("observe", "ALLOWANCE"),
        "claim",
        ("record", "ALLOWANCE"),
        ("observe", "PRODUCE_END"),
        ("record", "PRODUCE_END"),
        "finish_end",
    ]


def test_action_commit_taps_three_times_then_waits_button_gone(monkeypatch):
    """选行动提交（hiro.produce.action_select，替换 KAA ActionSelectContext.commit）：点 3 下、等按钮消失；休息走确认框。"""
    from types import SimpleNamespace as NS

    import hiro.produce.action_select as ac
    from hiro.vision.action_buttons import ActionButton
    from hiro.vision.ocr import Box

    t = [0.0]
    clicks = []
    monkeypatch.setattr(ac, "device", NS(screenshot=lambda: None, click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(ac, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    present = {"InProduce.ButtonIconOuting": [True, False]}
    monkeypatch.setattr(ac, "tpl", lambda name: NS(exists=lambda img: (present.get(name) or [False]).pop(0)))
    ac.action_commit(
        ActionButton("outing", None, None, Box(140, 960, 230, 1040), "InProduce.ButtonIconOuting"), clock=lambda: t[0]
    )
    assert clicks == [(185, 1000)] * 3

    clicks.clear()
    frames = iter(["menu", "confirm", "confirm", "after"])  # 休む → 确认框出来 → 点了一次还在 → 关掉
    monkeypatch.setattr(
        ac, "device", NS(screenshot=lambda: next(frames, "after"), click=lambda x, y: clicks.append((x, y)))
    )
    where = {"InProduce.Rest": ("menu", (640, 840)), "InProduce.RestConfirmBtn": ("confirm", (500, 1160))}

    def tpl(name):
        page, xy = where[name]
        return NS(exists=lambda img: img == page, find=lambda img: NS(center=xy) if img == page else None)

    monkeypatch.setattr(ac, "tpl", tpl)
    ac.action_commit(ActionButton("rest", None, None, Box(620, 820, 670, 860), "InProduce.Rest"), clock=lambda: t[0])
    assert clicks == [(640, 840), (500, 1160)]


def test_handlers_and_core_import_no_kaa():
    """新主循环用的处理器和 HifCore 不 import KAA / kotonebot（HiroHifStrategy 壳懒加载，用到才 import KAA）。"""
    import os
    import subprocess
    import sys

    code = ("import sys, hiro.produce.handlers, hiro.produce.hif, hiro.produce.card_ops, hiro.produce.action_select, "
            "hiro.vision.scene, hiro.loop.session; "
            "print([m for m in sys.modules if m.split('.')[0] in ('kaa', 'kotonebot')])")  # fmt: skip
    env = {k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()
    assert out == "[]"


def test_special_guidance_reentry_continues_customizing(monkeypatch):
    """特別指導 定制中途弹 ACQUIRE：定制循环先退出（不离开特別指導）；点过演出回到定制选卡页，处理器重进、按决策接着定制，最后 終了。"""
    from types import SimpleNamespace as NS

    from hiro.contracts import Action, ActionKind, Decision
    from hiro.produce import hif
    from hiro.produce import special_guidance as sg

    calls = []
    monkeypatch.setattr(sg, "enter", lambda pf: calls.append("enter") or NS(ok=True))
    monkeypatch.setattr(
        sg,
        "customize",
        lambda ci, ii, levels=1, cards=None: (
            calls.append(("customize", ci)) or NS(ok=True, page_after="SPECIAL_GUIDANCE")
        ),
    )
    monkeypatch.setattr(sg, "leave", lambda: calls.append("leave") or NS(ok=True))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    state = NS(page_fields={"cards": [{"index": 0}, {"index": 1}]})
    decisions = [Decision(Action(ActionKind.CUSTOMIZE_CARD, {"card_index": 1, "item_index": 0}), "定制", "rule", 0.8),
                 Decision(Action(ActionKind.CUSTOMIZE_END, {}), "结束", "rule", 0.8)]  # fmt: skip
    core = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    core.s = NS(state=state, observe=lambda **k: NS(fields={}), left_page=lambda *a: None, record=lambda *a, **k: None,
                recover=lambda *a: None, note=lambda *a: None)  # fmt: skip
    core.deciders = NS(decide=lambda page, st: decisions.pop(0))
    core.on_special_guidance(None)
    assert calls == [("customize", 1), "leave"]  # 已经在里面：不再 enter；接着定制一张，决策结束就离开


def test_page_read_buttons_fill_in_for_missing_templates():
    """N.I.A「自主レッスン」没有模板按钮：用选行动页读数里的位置补上，决策 training/Da/SP 能对上按钮。"""
    from types import SimpleNamespace as NS

    from hiro.contracts import Action
    from hiro.produce.hif import HifCore

    core = HifCore.__new__(HifCore)
    core.s = NS(state=NS(page_fields={"options": [{"option": "training", "attr": a, "sp": True, "center": [x, 1011]}
                                                 for a, x in (("Vo", 197), ("Da", 360), ("Vi", 521))]}))  # fmt: skip
    buttons = core._with_page_buttons([])
    b = HifCore._match_button(Action.produce_next("training", attr="Da", sp=True), buttons)
    assert b is not None and tuple(b.rect.center) == (360, 1011) and b.template == ""
