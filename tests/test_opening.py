"""开局导航（hiro.runtime.opening / nav）：假设备 + 假帧（每帧标出能看到哪些模板、在哪），不跑真识别。"""

import os
import subprocess
import sys

import numpy as np
import pytest

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import Frame, Runtime, UNKNOWN, nav, opening
from hiro.vision.ocr import Box, Text
from hiro.vision.ui_templates import Match


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.taps = []

    def screenshot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def tap(self, x, y):
        self.taps.append((x, y))


def frame(seen: dict, texts=()):
    """seen：模板名 → 中心点。"""
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    f.__dict__.update(texts=list(texts), has_dialog=False, dialog=None)
    f.seen = seen
    return f


def rig(monkeypatch, frames, on_tap=None):
    """Runtime：截图依次给 frames（用完重复最后一帧）；on_tap(taps) 返回下一批帧时替换队列（模拟点完换页）。"""
    t = {"now": 0.0}
    rt = Runtime(FakeDevice(), lambda img: UNKNOWN, clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))
    queue = list(frames)

    def shot():
        f = queue.pop(0) if len(queue) > 1 else queue[0]
        f.t = t["now"]
        rt.screen.frame = f
        return f

    rt.screen.shot = shot
    raw_tap = rt.raw_device.tap

    def tap(x, y):
        raw_tap(x, y)
        if on_tap is not None and (nxt := on_tap(rt.raw_device.taps)) is not None:
            queue[:] = nxt

    rt.raw_device.tap = tap
    monkeypatch.setattr(nav, "find", lambda f, name, **kw: Match(Box(*(p := f.seen[name]), p[0] + 1, p[1] + 1), 0.9)
                        if name in getattr(f, "seen", {}) else None)  # fmt: skip
    monkeypatch.setattr("hiro.runtime.guard.word_at", lambda f, x, y: None)
    return rt, t


HOME = {"Daily.ButtonHomeCurrent": (359, 1155)}


def test_goto_home_taps_home_buttons_until_home(monkeypatch):
    rt, _ = rig(monkeypatch, [frame({"Common.ButtonIconClose": (650, 100)})], on_tap=lambda taps: [frame(HOME)])
    f = nav.goto_home(rt)
    assert f.seen == HOME and rt.raw_device.taps == [(650, 100)]


def test_goto_home_times_out(monkeypatch):
    rt, _ = rig(monkeypatch, [frame({})])
    with pytest.raises(ResumeBlocked, match="回首页"):
        nav.goto_home(rt, timeout=3)


def test_enter_produce_new_and_resume_guard(monkeypatch):
    ongoing = opening.box("Produce.BoxProduceOngoing").center
    rt, _ = rig(monkeypatch, [frame(HOME)], on_tap=lambda taps: [frame({"Produce.LogoHif": (360, 400)})])
    assert opening.enter_produce(rt, new=True) == "new" and rt.raw_device.taps == [tuple(ongoing)]
    rt, _ = rig(monkeypatch, [frame(HOME)], on_tap=lambda taps: [frame({"Produce.ButtonResume": (500, 1000)})])
    with pytest.raises(ResumeBlocked, match="resume"):
        opening.enter_produce(rt, new=True)
    rt, _ = rig(monkeypatch, [frame(HOME)], on_tap=lambda taps: [frame({"Produce.ButtonResume": (500, 1000)})])
    assert opening.enter_produce(rt, new=False) == "resume"


def test_enter_produce_discard_dialog_only_cancels(monkeypatch):
    dlg = frame({"Produce.BreakProduceDialog.Title": (360, 500)}, [Text("キャンセル", Box(180, 1150, 260, 1170)), Text("開始する", Box(460, 1150, 560, 1170))])
    rt, _ = rig(monkeypatch, [frame(HOME)], on_tap=lambda taps: [dlg] if len(taps) == 1 else None)
    with pytest.raises(ResumeBlocked, match="破棄"):
        opening.enter_produce(rt, new=True)
    assert rt.raw_device.taps[-1] == (220, 1160)  # 只点了キャンセル


def test_select_hif_goes_back_then_forward(monkeypatch):
    """认出当前剧本按顺序点：N.I.A → H.I.F 只点一次「下一页」。"""
    nxt = tuple(opening.point("Produce.PointNext"))
    pages = [frame({"Produce.LogoNia": (1, 1)}), frame({"Produce.LogoHif": (1, 1)})]
    rt, _ = rig(monkeypatch, [pages[0]], on_tap=lambda taps: [pages[len(taps)]])
    opening.select_hif(rt)
    assert rt.raw_device.taps == [nxt]


def test_select_scenario_waits_for_flip_animation(monkeypatch):
    """N.I.A 采样第一次实机 11-17：翻页动画中认不出 logo 还每拍都点，来回翻、30 秒停在 H.I.F。
    现在点一下后等画面换到别的剧本：动画帧（认不出）不点；H.I.F → N.I.A 只点一次「上一页」。"""
    prev = tuple(opening.point("Produce.PointPrev"))
    anim = frame({})
    rt, _ = rig(monkeypatch, [frame({"Produce.LogoHif": (1, 1)})],
                on_tap=lambda taps: [anim, anim, anim, frame({"Produce.LogoNia": (1, 1)})])  # fmt: skip
    opening.select_scenario(rt, "Produce.LogoNia")
    assert rt.raw_device.taps == [prev]


def test_select_scenario_retaps_when_page_did_not_change(monkeypatch):
    """点了画面一直没换（点击没生效）：FLIP_WAIT_S 后再点一次。"""
    nxt = tuple(opening.point("Produce.PointNext"))
    hajime = frame({"Produce.LogoHajime": (1, 1)})
    rt, _ = rig(monkeypatch, [hajime], on_tap=lambda taps: [hajime] if len(taps) == 1 else [frame({"Produce.LogoNia": (1, 1)})])
    opening.select_scenario(rt, "Produce.LogoNia")
    assert rt.raw_device.taps == [nxt, nxt]


def test_choose_mode_refills_ap_once(monkeypatch):
    calls = []

    def refill(shoot, click, ocr, **kw):
        calls.append(kw["extra_ap"])
        click(500, 800)  # 真的 refill 会点「回復する」「使用する」…，点完回到选模式页
        return {"ok": True, "drinks": 1, "shortfall": 5, "error": None}

    monkeypatch.setattr("hiro.loop.ap_refill.refill", refill)
    btn = {"Produce.ButtonHifQualify": (360, 900)}
    seq = [frame({"Produce.TextAPInsufficient": (360, 600)}), frame(btn), frame({"Produce.TextStepIndicator1": (100, 100)})]
    rt, _ = rig(monkeypatch, [frame(btn)], on_tap=lambda taps: [seq[len(taps) - 1]] if len(taps) <= len(seq) else None)
    opening.choose_mode(rt, "qualify")
    assert calls == [20]  # 只打选拔：本战还要 20 AP 一起补
    rt, _ = rig(monkeypatch, [frame({"Produce.TextAPInsufficient": (360, 600)})])
    monkeypatch.setattr("hiro.loop.ap_refill.refill", lambda *a, **k: {"ok": False, "error": "确认框有ジュエル"})
    with pytest.raises(ResumeBlocked, match="ジュエル"):
        opening.choose_mode(rt, "main")


def test_start_presses_start_then_skip_commu(monkeypatch):
    seq = [frame({"Produce.RadioTextSkipCommu": (200, 700)}), frame({"Common.ButtonConfirmNoIcon": (500, 1100)}), frame({})]
    rt, _ = rig(monkeypatch, [frame({"Produce.Step4.ButtonProduceStart": (500, 1150)})],
                on_tap=lambda taps: [seq[min(len(taps) - 1, 2)]])  # fmt: skip
    opening.start(rt)
    assert rt.raw_device.taps[:3] == [(500, 1150), (200, 700), (500, 1100)]


def test_opening_imports_no_kaa():
    code = "import sys, hiro.runtime.opening; print([m for m in sys.modules if m.split('.')[0] in ('kaa', 'kotonebot')])"
    env = {k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env).stdout.strip()
    assert out == "[]"


# ---------------------------------------------------------------- 第 1 步选偶像
def _step1(monkeypatch, selected_frames):
    """idol_selected 按帧上的 selected 标记；idol_select.locate 直接给卡位。"""
    monkeypatch.setattr(opening, "idol_selected", lambda f, skin: getattr(f, "selected", False))
    monkeypatch.setattr("hiro.produce.idol_select.locate", lambda skin, **kw: Box(200, 600, 349, 797))


def test_step1_already_selected_just_next(monkeypatch):
    _step1(monkeypatch, None)
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.Step1.ButtonNext": (500, 1160)})
    s1.selected = True
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: [frame({"Produce.TextStepIndicator2": (1, 1)})])
    opening.step1(rt, "i_card-skin-hski-3-007")
    assert rt.raw_device.taps == [(500, 1160)]


def test_step1_selects_from_overview_then_next(monkeypatch):
    _step1(monkeypatch, None)
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.ButtonPIdolOverview": (600, 400)})
    another = frame({"Common.ButtonConfirmNoIcon": (500, 1160), "Produce.TextAnotherIdolAvailableDialog": (360, 500), "Common.ButtonIconClose": (650, 300)})
    overview = frame({"Produce.IdolOverview.ButtonConfirm": (500, 1160), "Common.ButtonConfirmNoIcon": (500, 1160)})
    back = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.Step1.ButtonNext": (500, 1160)})
    back.selected = True
    s2 = frame({"Produce.TextStepIndicator2": (1, 1)})
    script = {1: [another], 2: [overview], 3: [overview], 4: [back], 5: [s2]}  # 第几次点击之后的画面
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: script.get(len(taps)))
    opening.step1(rt, "i_card-skin-hski-3-007")
    assert rt.raw_device.taps == [(600, 400), (650, 300), (274, 698), (500, 1160), (500, 1160)]  # 一览、关提示、点卡、确认、次へ


def test_step1_idol_missing_stops(monkeypatch):
    monkeypatch.setattr(opening, "idol_selected", lambda f, skin: False)
    monkeypatch.setattr("hiro.produce.idol_select.locate", lambda skin, **kw: None)
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.IdolOverview.ButtonConfirm": (500, 1160)})
    rt, _ = rig(monkeypatch, [s1])
    with pytest.raises(ResumeBlocked, match="找不到"):
        opening.step1(rt, "i_card-skin-none")


# ---------------------------------------------------------------- 第 2 步支援卡 + 租借
def _step2(monkeypatch, *, enabled_after_pick=True, picked=True, mismatch=False):
    calls = {"set": [], "pick": []}
    from hiro.produce import formation_steps, prep_steps

    monkeypatch.setattr(prep_steps, "select_set", lambda n, **kw: calls["set"].append(n) or True)
    monkeypatch.setattr(formation_steps, "autofix_support", lambda dev, plan: None)

    def check(img, plan, set_no, **kw):  # shoot / sleep：等翻页动画停（账号 2 11-16）
        if mismatch:
            raise opening.ResumeBlocked("支援卡编成和方案对不上")  # FormationMismatch 也是停机类

    monkeypatch.setattr(formation_steps, "check_support", check)
    state = {"enabled": False}

    def pick(plan, attr, main):
        calls["pick"].append(attr)
        state["enabled"] = enabled_after_pick
        return picked

    monkeypatch.setattr(formation_steps, "rental_pick", pick)
    monkeypatch.setattr(opening, "_next_enabled", lambda f, name: Match(Box(500, 1150, 501, 1151), 0.9) if state["enabled"] or getattr(f, "lit", False) else None)
    return calls, state


def test_step2_full_formation_goes_next(monkeypatch):
    calls, state = _step2(monkeypatch)
    s2 = frame({"Produce.TextStepIndicator2": (1, 1)})
    s2.lit = True
    rt, _ = rig(monkeypatch, [s2], on_tap=lambda taps: [frame({"Produce.TextStepIndicator3": (1, 1)})])
    opening.step2(rt, set_no=4)
    assert calls["set"] == [4] and calls["pick"] == [] and rt.raw_device.taps == [(500, 1150)]


def test_step2_rents_when_slot_empty(monkeypatch):
    calls, state = _step2(monkeypatch)
    rt, _ = rig(monkeypatch, [frame({"Produce.TextStepIndicator2": (1, 1)})], on_tap=lambda taps: [frame({"Produce.TextStepIndicator3": (1, 1)})])
    opening.step2(rt, set_no=4, rental_attr="Vi")
    assert calls["pick"] == ["Vi"] and rt.raw_device.taps == [(500, 1150)]


def test_step2_stops_without_rental_or_set_or_on_mismatch(monkeypatch):
    s2 = [frame({"Produce.TextStepIndicator2": (1, 1)})]
    _step2(monkeypatch, picked=False, enabled_after_pick=False)
    rt, _ = rig(monkeypatch, s2)
    with pytest.raises(ResumeBlocked, match="租借"):
        opening.step2(rt, set_no=4)
    rt, _ = rig(monkeypatch, s2)
    with pytest.raises(ResumeBlocked, match="编成号"):
        opening.step2(rt, set_no=None)
    _step2(monkeypatch, mismatch=True)
    rt, _ = rig(monkeypatch, s2)
    with pytest.raises(ResumeBlocked, match="对不上"):
        opening.step2(rt, set_no=4)


# ---------------------------------------------------------------- 第 3 / 4 步、本战选拔回忆
def test_step3_confirms_rent_tip_then_next(monkeypatch):
    from hiro.produce import formation_steps, prep_steps

    monkeypatch.setattr(prep_steps, "select_set", lambda n, **kw: True)
    checked = []
    monkeypatch.setattr(formation_steps, "check_memory_set", lambda img, plan, n, **kw: checked.append(n))
    s3 = frame({"Produce.TextStepIndicator3": (1, 1), "Produce.Step3.ButtonNext": (500, 1160)})
    tip = frame({"Produce.TextRentAvailable": (360, 600), "Produce.RentAvailableConfirmButton": (360, 900)})
    s4 = frame({"Produce.TextStepIndicator4Or2": (1, 1)})
    rt, _ = rig(monkeypatch, [s3], on_tap=lambda taps: [tip] if len(taps) == 1 else [s4])
    opening.step3(rt, set_no=5)
    assert checked == [5] and rt.raw_device.taps == [(500, 1160), (360, 900)]


def test_step4_sets_boost_checkboxes(monkeypatch):
    states = {"Produce.Step4.CheckboxNoteBoost": True, "Produce.Step4.CheckboxPtBoost": False}
    monkeypatch.setattr("hiro.vision.ui_templates.checkbox_checked", lambda img, m: states[m.name])
    s4 = frame({"Produce.TextStepIndicator4Or2": (1, 1), "Produce.Step4.CheckboxNoteBoost": (100, 800), "Produce.Step4.CheckboxPtBoost": (100, 900)})
    rt, _ = rig(monkeypatch, [s4])
    real_find = nav.find
    monkeypatch.setattr(nav, "find", lambda f, name, **kw: (m := real_find(f, name, **kw)) and type("M", (), {"rect": m.rect, "name": name})())
    opening.step4(rt, note_boost=False, pt_boost=False)
    assert rt.raw_device.taps == [(100, 800)]  # 只把勾着的 NoteBoost 取消，PtBoost 本来就没勾


def test_check_main_memory(monkeypatch):
    from hiro.produce import memory_select

    rt, _ = rig(monkeypatch, [frame({})])
    seq = iter([[], ["2066"], []])
    picked = []
    monkeypatch.setattr(memory_select, "memory_missing_on_page", lambda expect, **kw: next(seq))
    monkeypatch.setattr(memory_select, "select_memory", lambda expect, **kw: picked.append(expect) or True)
    opening.check_main_memory(rt, ("1498", "2066", "944"))  # 页面上已经是这份
    opening.check_main_memory(rt, ("1498", "2066", "944"))  # 缺 2066 → 去列表选 → 再核通过
    assert picked == [("1498", "2066", "944")]
    monkeypatch.setattr(memory_select, "memory_missing_on_page", lambda expect, **kw: ["944"])
    with pytest.raises(memory_select.MemoryMismatch):
        opening.check_main_memory(rt, ("1498", "2066", "944"))
    opening.check_main_memory(rt, ())  # 没指定就不查


def test_prepare_main_skips_formation_steps(monkeypatch):
    called = []
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: called.append(("memory", expect)))
    monkeypatch.setattr(opening, "step4", lambda rt, **kw: called.append(("step4", kw)))
    monkeypatch.setattr(opening, "step1", lambda *a, **k: called.append("step1"))
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.Step1.ButtonNext": (500, 1160)})
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: [frame({"Produce.TextStepIndicator4Or2": (1, 1)})])
    opening.prepare(rt, "main", idol="i_card-skin-hski-3-007", expect_memory=("1", "2", "3"))
    assert called == [("memory", ("1", "2", "3")), ("step4", {"note_boost": False, "pt_boost": False})]


def _memory_unset_rig(monkeypatch, chosen):
    from hiro.produce import formation_steps, prep_steps
    from hiro.vision.ocr import Box as OBox, Text

    monkeypatch.setattr(prep_steps, "select_set", lambda n, **kw: True)
    monkeypatch.setattr(formation_steps, "check_memory_set", lambda img, plan, n, **kw: None)
    s3 = frame({"Produce.TextStepIndicator3": (1, 1), "Produce.Step3.ButtonNext": (500, 1160)})
    dlg = frame({}, [Text("キャンセル", OBox(162, 1144, 311, 1180)), Text("く完了する", OBox(394, 1144, 577, 1176))])
    dlg.__dict__.update(has_dialog=True, dialog={"kind": "other", "title": "メモリー編成確認", "danger": False, "y": 937, "body": ""})
    s4 = frame({"Produce.TextStepIndicator4Or2": (1, 1)})
    rt, _ = rig(monkeypatch, [s3], on_tap=lambda taps: [dlg] if len(taps) == 1 else [s4])
    plan = {"memory": {"chosen": [{"card_id": f"c{i}"} for i in range(chosen)]}}
    return rt, plan


def test_step3_memory_unset_confirm_when_plan_has_fewer(monkeypatch):
    """「メモリー編成確認」（有空的回忆格，账号 2 11-16 原来没处理）：方案本来就不到 4 份 → 点「完了する」进第 4 步。"""
    rt, plan = _memory_unset_rig(monkeypatch, 1)
    opening.step3(rt, set_no=20, plan=plan)
    assert rt.raw_device.taps == [(500, 1160), (485, 1160)]


def test_step3_memory_unset_cancel_and_stop_when_plan_wants_four(monkeypatch):
    rt, plan = _memory_unset_rig(monkeypatch, 4)
    with pytest.raises(ResumeBlocked, match="キャンセル"):
        opening.step3(rt, set_no=20, plan=plan)
    assert rt.raw_device.taps[-1] == (236, 1162)  # 点的是キャンセル，不是完了する


def _texts(*items):
    from hiro.vision.ocr import Box as OBox, Text

    return [Text(t, OBox(x - 40, y - 15, x + 40, y + 15)) for t, x, y in items]


def test_prepare_main_on_finals_start_checks_memory_then_presses_honsen(monkeypatch):
    """选拔后保留的本战：点「本戦」直接到「開始確認」（没有编成页）。核对选拔回忆 → 点「H.I.F本戦へ」→ 不再点 step4 / プロデュース開始
    （Mac 账号 2 11-17：原来等「本战第 1 步」20 秒超时；Windows 11-14 同一处）。"""
    called = []
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: called.append(("memory", expect)))
    monkeypatch.setattr(opening, "step4", lambda rt, **kw: called.append("step4"))
    confirm = frame({"Produce.TextStepIndicator4Or2": (65, 49)}, _texts(("開始確認", 120, 40), ("H.I.F本戦へ", 470, 1160)))
    after = frame({})
    rt, _ = rig(monkeypatch, [confirm], on_tap=lambda taps: [after])
    assert opening.prepare(rt, "main", idol="i", expect_memory=("1", "2", "3")) is True
    assert called == [("memory", ("1", "2", "3"))] and rt.raw_device.taps == [(470, 1160)]


def test_new_produce_main_already_in_produce_hands_over(monkeypatch):
    """点了「本戦」后画面已经是选行动页（本战已经开始了）：开局到此为止，交主循环，不等第 1 步、不点プロデュース開始。"""
    monkeypatch.setattr(opening, "enter_produce", lambda rt, new: "new")
    monkeypatch.setattr(opening, "select_hif", lambda rt: None)
    called = []
    monkeypatch.setattr(opening, "prepare", lambda *a, **k: called.append("prepare"))
    monkeypatch.setattr(opening, "start", lambda rt: called.append("start"))
    rt, _ = rig(monkeypatch, [frame({})])
    from hiro.vision.classify import PageHit

    rt.classify = lambda img: PageHit("ACTION_SELECT", "pixel")
    opening.new_produce(rt, "main")
    assert called == [] and rt.raw_device.taps == []


def test_prepare_main_does_not_tap_bottom_button_without_step1_indicator(monkeypatch):
    """Windows 9580c0c 11-17：開始確認页没认出，Step1.ButtonNext 模板命中了「H.I.F本戦へ」就点了，选拔回忆核对没跑。
    现在没有第 1 步指示器就不点底部大按钮：认不出的页一直等到超时停机。"""
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: pytest.fail("不该走到核对"))
    rt, _ = rig(monkeypatch, [frame({"Produce.Step1.ButtonNext": (360, 1078)})])
    with pytest.raises(ResumeBlocked, match="本战第 1 步"):
        opening.prepare(rt, "main", idol="i", expect_memory=("1",))
    assert rt.raw_device.taps == []
    s1 = frame({"Produce.TextStepIndicator1": (60, 50), "Produce.Step1.ButtonNext": (360, 1150)})
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: [frame({"Produce.TextStepIndicator4Or2": (65, 49)})])
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: None)
    monkeypatch.setattr(opening, "step4", lambda rt, **kw: None)
    assert opening.prepare(rt, "main", idol="i") is False and rt.raw_device.taps == [(360, 1150)]  # 认出第 1 步才点


def test_prepare_main_finals_start_by_button_template(monkeypatch):
    """OCR 一个字都没读到「本戦」（我们的 OCR 在三张实机帧上都读不出橙底白字）：按钮模板命中就认成開始確認页，核对后点模板中心。"""
    called = []
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: called.append("memory"))
    confirm = frame({"Produce.ButtonHifFinalsStart": (360, 1078), "Produce.Step1.ButtonNext": (360, 1078),
                     "Produce.TextStepIndicator4Or2": (65, 49)})  # fmt: skip
    rt, _ = rig(monkeypatch, [confirm], on_tap=lambda taps: [frame({})])
    assert opening.prepare(rt, "main", idol="i", expect_memory=("1",)) is True
    assert called == ["memory"] and rt.raw_device.taps == [(360, 1078)]


def test_finals_start_ocr_variants():
    """RapidOCR 的读法：「H.I.F本戦ヘ」（片假名ヘ）、空格。要同时有页头「開始確認」。"""
    f = frame({}, _texts(("開始確認", 65, 48), ("H.I.F 本戦ヘ", 360, 1078)))
    assert opening._finals_start(f)
    assert not opening._finals_start(frame({}, _texts(("H.I.F本戦へ", 360, 1078))))  # 只有按钮字不算
    assert not opening._finals_start(frame({}, _texts(("開始確認", 65, 48), ("本戦へ進む", 360, 400))))  # 正文里的不算


@pytest.mark.parametrize("name, want", [("dmm/start-confirm-dmm1.png", True), ("start-confirm-q16.png", True),
                                        ("finals/start-confirm-540.png", True), ("settings/start-confirm-m23.png", False)])
def test_finals_start_on_real_frames(name, want):
    """实机开始确认页：本战（H.I.F本戦へ）认得出；选拔第 2 步的「プロデュース開始」不算。只用模板（不 OCR）。"""
    import cv2

    from hiro.vision.offline import load

    path = os.path.join(os.environ.get("HIRO_FIXTURES", os.path.join(os.path.dirname(__file__), "fixtures")), name)
    if not os.path.exists(path):
        pytest.skip(f"需要实机截图 {name}")
    f = Frame(load(cv2.imread(path)), 0.0)
    f.__dict__["texts"] = []
    assert opening._finals_start(f) is want


def test_new_produce_snaps_a_frame_per_tap(monkeypatch):
    """开局阶段每次点击都存一帧到 trace、记一条备注；开局结束后摘掉钩子。"""
    saved, notes = [], []
    rt, _ = rig(monkeypatch, [frame({})])
    rt.session = type("S", (), {})()
    rt.session.trace = type("T", (), {"next_seq": lambda self: len(saved) + 1,
                                      "save_frame": lambda self, seq, img: saved.append(seq) or f"frames/{seq:05d}.jpg"})()  # fmt: skip
    rt.session.note = notes.append
    monkeypatch.setattr(opening, "_new_produce", lambda rt, mode, **kw: (rt.screen.shot(), rt.device.tap(10, 20), rt.device.tap(30, 40)))
    opening.new_produce(rt, "main")
    assert saved == [1, 2] and notes == ["开局点击 (10, 20)（帧 frames/00001.jpg）", "开局点击 (30, 40)（帧 frames/00002.jpg）"]
    rt.device.tap(50, 60)
    assert len(saved) == 2  # 开局之后不再存


def test_step1_next_is_not_tapped_twice_while_page_changes(monkeypatch):
    """Mac p15-main 11-17：第 1 步点了「次へ」，页面切换慢、下一拍还是第 1 步，又点一下，落到「2.開始確認」同一位置的
    「プロデュース開始」，本战直接开始、选拔回忆没核对。点过以后 STEP1_RETAP_S 秒内不再点。"""
    called = []
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: called.append("memory"))
    monkeypatch.setattr(opening, "step4", lambda rt, **kw: called.append("step4"))
    s1 = frame({"Produce.TextStepIndicator1": (93, 49), "Produce.Step1.ButtonNext": (353, 1093)})
    confirm = frame({"Produce.TextStepIndicator4Or2": (123, 49), "Produce.Step4.ButtonProduceStart": (361, 1078)},
                    _texts(("2.開始確認", 111, 49)))  # fmt: skip
    rt, _ = rig(monkeypatch, [s1, s1, s1, s1, confirm])
    assert opening.prepare(rt, "main", idol="i", expect_memory=("1",)) is False
    assert rt.raw_device.taps == [(353, 1093)] and called == ["memory", "step4"]


@pytest.mark.parametrize("name, want", [("finals-start-confirm-2-p15main.jpg", True), ("finals-step1-p15main.jpg", False),
                                        ("dmm/start-confirm-dmm1.png", True)])
def test_on_start_confirm_real_frames(name, want):
    import cv2

    path = os.path.join(os.environ.get("HIRO_FIXTURES", os.path.join(os.path.dirname(__file__), "fixtures")),
                        name if "/" in name else os.path.join("pages", name))  # fmt: skip
    if not os.path.exists(path):
        pytest.skip(f"需要实机截图 {name}")
    assert opening.on_start_confirm(Frame(cv2.imread(path), 0.0)) is want


def test_on_start_confirm_not_qualify_step4(monkeypatch):
    rt, _ = rig(monkeypatch, [frame({})])
    q = frame({"Produce.TextStepIndicator4Or2": (123, 49)}, _texts(("4.開始確認", 111, 49)))
    assert opening.on_start_confirm(q) is False


def test_new_produce_main_from_start_confirm_page_skips_entry(monkeypatch):
    """已经停在本战「2.開始確認」：不回首页、不重走剧本选择，直接核对选拔回忆 → 加成 → プロデュース開始。"""
    called = []
    monkeypatch.setattr(opening, "enter_produce", lambda rt, new: pytest.fail("不该回去进培育入口"))
    monkeypatch.setattr(opening, "check_main_memory", lambda rt, expect: called.append("memory"))
    monkeypatch.setattr(opening, "step4", lambda rt, **kw: called.append("step4"))
    monkeypatch.setattr(opening, "start", lambda rt: called.append("start"))
    confirm = frame({"Produce.TextStepIndicator4Or2": (123, 49)}, _texts(("2.開始確認", 111, 49)))
    rt, _ = rig(monkeypatch, [confirm])
    opening.new_produce(rt, "main", idol="i", expect_memory=("1",))
    assert called == ["memory", "step4", "start"] and rt.raw_device.taps == []


def test_step1_confirm_taps_card_only_once_while_list_fades(monkeypatch):
    """z6（11-19 10:37 DMM 3.6.0）：一览里点了 篠澤広、点「決定」，淡出几帧里「決定」还认得出，确认循环又点了一次卡位中心 (104,898)，
    那时已回到第 1 步，正好点在底部轮播的 月村手毬 上，选成了她。卡位只点一次；之后只在一览还在时补点「決定」。"""
    _step1(monkeypatch, None)
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.ButtonPIdolOverview": (600, 400)})
    overview = frame({"Produce.IdolOverview.ButtonConfirm": (500, 1160), "Common.ButtonConfirmNoIcon": (500, 1160)})
    fading = frame({"Common.ButtonConfirmNoIcon": (500, 1160)})  # 一览淡出：「決定」还认得出，一览本体已不在
    back = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.Step1.ButtonNext": (500, 1160)})
    back.selected = True
    s2 = frame({"Produce.TextStepIndicator2": (1, 1)})
    script = {1: [overview], 3: [fading, fading, fading, back], 4: [s2]}
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: script.get(len(taps)))
    opening.step1(rt, "i_card-skin-shro-3-007")
    assert rt.raw_device.taps.count((274, 698)) == 1
    assert rt.raw_device.taps == [(600, 400), (274, 698), (500, 1160), (500, 1160)]  # 一览、点卡、确认、次へ


def test_idol_selected_rechecks_frames(monkeypatch):
    """第 1 步刚出现、轮播还在动时读不准（z4 / z5 / z6 都先判成「选中的不是」去开一览）：多看几帧，有一帧对上就算选好了。"""
    seq = iter([False, False, True])
    monkeypatch.setattr(opening, "idol_selected", lambda f, skin: next(seq, True))
    s1 = frame({"Produce.TextStepIndicator1": (1, 1), "Produce.Step1.ButtonNext": (500, 1160)})
    rt, _ = rig(monkeypatch, [s1], on_tap=lambda taps: [frame({"Produce.TextStepIndicator2": (1, 1)})])
    monkeypatch.setattr("hiro.produce.idol_select.locate", lambda skin, **kw: pytest.fail("不该去开偶像一览"))
    opening.step1(rt, "i_card-skin-shro-3-007")
    assert rt.raw_device.taps == [(500, 1160)]


def test_start_retaps_when_still_on_step4(monkeypatch):
    """z6（11-19 10:51 DMM）：第 3 步刚切到第 4 步就点了「プロデュース開始」，被动画吞了；原来点了就当成功，
    交给主循环，PRODUCE_START_CONFIRM 没处理器 180s 停机。点了还在第 4 步就隔一会儿再点，直到离开第 4 步。"""
    step4 = frame({"Produce.Step4.ButtonProduceStart": (500, 1150)})
    seq = {2: [frame({"Produce.RadioTextSkipCommu": (200, 700)})], 3: [frame({})]}  # 第 1 下没反应，第 2 下才弹出
    rt, _ = rig(monkeypatch, [step4], on_tap=lambda taps: seq.get(len(taps)))
    opening.start(rt)
    assert rt.raw_device.taps[:3] == [(500, 1150), (500, 1150), (200, 700)]


def test_start_skip_loop_does_not_tap_confirm_on_step4(monkeypatch):
    """开始按钮还在（第 4 步页）时，跳过交流那段不去点「Common.ButtonConfirmNoIcon」（第 4 步页上会误命中）。"""
    step4 = frame({"Produce.Step4.ButtonProduceStart": (500, 1150), "Common.ButtonConfirmNoIcon": (520, 1150)})
    rt, _ = rig(monkeypatch, [step4])
    with pytest.raises(ResumeBlocked):
        opening.start(rt)
    assert (520, 1150) not in rt.raw_device.taps and rt.raw_device.taps.count((500, 1150)) >= 2
