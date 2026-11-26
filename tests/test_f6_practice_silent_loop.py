"""G1-F6 复现（rc.2 e555b22）：初练习课里 run_exam 被 ended() 误判结束后，PRACTICE 处理器每拍空返回 DONE，主循环无日志空转。

这两个测试写的是「修好以后应有的行为」，在 e555b22 上都会失败（失败本身就是复现）。"""

from types import SimpleNamespace as NS

from hiro.loop.errors import ResumeBlocked
from hiro.produce.handlers import make_handlers
from hiro.vision.classify import PageHit

import pytest


def _core_with_lesson_done():
    """刚打完（其实是被误判打完）的练习课：_lesson_done=True，on_practice_entered 什么都不做就 return。"""
    from hiro.runtime import hajime

    Core = hajime.hajime_core("produce-001")
    core = Core.__new__(Core)
    core._lesson_done = True
    return core


def test_practice_gate_does_not_count_as_action():
    """PRACTICE 一直在（课程 HUD 还在、角色待机动画让缩略图一直在变），处理器每拍什么都没做：
    180s 内必须停机（或重进课程），不能当「有操作」无限空转（F6：55 分钟零日志）。"""
    from test_runtime import frame, runtime  # 同 test_hajime_end_flow：函数里导入，不在收集阶段把 test_runtime 加载两次

    core = _core_with_lesson_done()
    # 两种底色交替 = 画面一直在变（模拟 3D 待机动画：实测缩略图差中位 7.7、≥8 的占一半，看门狗基准 2000 帧里重置 895 次）
    rt, clock = runtime([frame(fill=0), frame(fill=200)] * 400, classify=lambda img: PageHit("PRACTICE", "template"),
                        handlers=make_handlers(core), idle_stop_s=180.0)  # fmt: skip
    rt.interrupts = ()
    with pytest.raises(ResumeBlocked):
        for _ in range(400):  # 400 拍 × 1s = 400s，远超 idle_stop_s
            rt.step()
            clock.now += 1.0


def test_lesson_end_not_triggered_by_two_hidden_samples_far_apart(monkeypatch):
    """ended() 只在 run_exam 每轮循环顶部调一次。卡停在选中态时正中 HUD 换成分数预览（「40 ∨ 26」，CLEARまで 模板匹配不到）：
    第一次看到 HUD 消失在 t=16.5，中间出牌、决策的 3 秒里没再调 ended()，t=19.9 第二次又是选中态 → 当成 HUD 连续消失 3.4s，误判课程结束。"""
    import hiro.devices.active as act
    import hiro.exec.exam as ex
    from hiro.runtime import hajime
    from hiro.vision import hajime as hv

    got = {}
    now = {"t": 0.0}

    def fake_run_exam(brain, label, rec, ended, **kw):
        now["t"] = 11.9
        got["a"] = ended("clear")  # 开课：HUD 在
        now["t"] = 16.5
        got["b"] = ended(None)  # 第 1 张牌停在选中态
        now["t"] = 19.9  # 中间点空白取消选中、重新决策、点第 2 张：这 3.4s 里 ended() 没被调用
        got["c"] = ended(None)  # 第 2 张牌又停在选中态

    monkeypatch.setattr(ex, "run_exam", fake_run_exam)  # hajime_core 建类时才 import run_exam：先替换
    core = _core_with_lesson_done()
    core._lesson_done = False
    core.lesson_count = 0
    core.last_action_choice, core.last_action_options = NS(attr="Da", sp=True), []
    core.s = NS(state=NS(produce={"mode": "produce-001", "idol_card_id": "i_card-fktn-3-000"},
                         exam_session=NS(label=None, lesson=None)), note=lambda *a: None)  # fmt: skip
    core.rec = NS(log=lambda *a, **k: None)
    core.brain = core.advisor = None
    core._prior_trace_dirs = list
    monkeypatch.setattr(act, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(hv, "lesson_attr", lambda img: "Da")
    monkeypatch.setattr(hv, "lesson_goal", lambda img: img)  # 用「图」直接表示这一帧的 goal
    monkeypatch.setattr(hv, "lesson_turns", lambda img: 6)
    monkeypatch.setattr(hv, "read_lesson_hud", lambda img: {"goal": img, "left": None, "param": None})
    # 选中态帧上左上「残りターン数 6」还读得出（实机帧 00317 / 00320 / 00321 都是 6）；e555b22 没有这个函数，raising=False
    monkeypatch.setattr(hv, "lesson_turns_shown", lambda img: True, raising=False)
    monkeypatch.setattr(hajime.time, "monotonic", lambda: now["t"])
    core.on_practice_entered(None)
    assert got["a"] is False and got["b"] is False
    assert got["c"] is False  # e555b22：True（误判课程结束 → _lesson_done=True → F6 空转）


def test_real_frames_selected_card_keeps_turns_hud():
    """实机帧（G1-F6，私有）：卡停在选中态时 lesson_goal 认不出，但剩余回合模板读得出；课程真结束后两样都没了。"""
    import os

    from hiro.vision import hajime as hv

    import cv2

    d = os.environ.get("F6_FRAMES")
    if not d:
        pytest.skip("要 F6_FRAMES 指向 G1-F6 的 frames 目录")
    for name, goal, shown in [("00317-080416-tap-360-330.jpg", None, True), ("00320-080420-unknown.jpg", None, True),
                              ("00324-080422-PRACTICE.jpg", "clear", True), ("00178-080252-tap-360-330.jpg", None, False),
                              ("00188-080258-SELECT_CARD.jpg", None, False)]:  # fmt: skip
        img = cv2.imread(os.path.join(d, name))
        assert hv.lesson_goal(img) == goal, name
        assert hv.lesson_turns_shown(img) is shown, name


def test_turns_box_zero_or_bright_is_not_lesson(monkeypatch):
    """N37（G1 第 3 轮 11-26）：PERFECT 提前结束后，选卡页左上同一位置是白底课程标签，数字模板读出「0」，
    被当成「剩余回合还在」，ended 一直不成立，空等 181 秒。读成 0、或者这块是亮底，都不算剩余回合。"""
    from hiro.exec import exam
    from hiro.vision import hajime as hv
    from hiro.vision import hif

    import numpy as np

    x1, y1, x2, y2 = hif.BOX_TURNS
    dark = np.full((1280, 720, 3), 40, np.uint8)
    bright = dark.copy()
    bright[y1:y2, x1:x2] = 230
    monkeypatch.setattr(exam, "_hud_int", lambda img, b, t: 3)
    assert hv.lesson_turns_shown(dark) is True
    assert hv.lesson_turns_shown(bright) is False  # 选卡页 / 选行动页的亮底
    monkeypatch.setattr(exam, "_hud_int", lambda img, b, t: 0)
    assert hv.lesson_turns_shown(dark) is False  # 课程里剩余回合最少是 1


def test_real_frames_perfect_end_is_seen(monkeypatch):
    """实机帧（N37，私有）：课程里读得出剩余回合；PERFECT 结束后的选卡 / 拿卡 / 选行动页都不算。"""
    import os

    from hiro.vision import hajime as hv

    import cv2

    d = os.environ.get("N37_FRAMES")
    if not d:
        pytest.skip("要 N37_FRAMES 指向 G1 第 3 轮的 n37-frames 目录")
    for name, shown in [("00565-173436-tap-360-330.jpg", True), ("00572-173447-tap-332-1010.jpg", True),
                        ("00581-173501-tap-114-1009.jpg", True), ("00586-173505-tap-360-330.jpg", False),
                        ("00597-173803-SELECT_CARD.jpg", False), ("00600-173816-ACQUIRE.jpg", False),
                        ("00140-172555-ACTION_SELECT.jpg", False), ("00338-172908-ACTION_SELECT.jpg", False)]:  # fmt: skip
        img = cv2.imread(os.path.join(d, name))
        assert img is not None, name  # 帧缺了要报出来，不能当成「读不出回合」蒙混过去
        assert hv.lesson_turns_shown(img) is shown, name
