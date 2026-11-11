# 来源：digital-hiro tests/test_nonexam_speed.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""选拔里考试以外的提速（协调 2026-11-03，第二十一局选拔非考试 40 分钟）：
- 三选一快速提交：点选项 → 等「受け取る」亮 → 点 → 等消失（KAA commit 固定 sleep，选卡执行中位 8.5s）；
- 「受け取る」改底部条带局部 OCR（以前整帧 kotonebot OCR）；
- 执行后核对：KAA commit 已返回时一离开原页面就算生效（选行动后课程动画全是过渡帧，每步空等到 8s 超时）；
- 差し入れ光效 1s 就点角落（以前等 15s）。"""

from pathlib import Path

import cv2
import numpy as np

from hiro.produce import ops

FIX = Path(__file__).parent / "fixtures/nonexam"


def test_find_receive_on_real_select_card():
    # 文字框中心：Vision (355, 1096)，RapidOCR 框宽一点给 (359, 1096)；点按钮差几个像素没关系
    x, y = ops.find_receive(cv2.imread(str(FIX / "q21-select-card.jpg")))
    assert abs(x - 355) <= 8 and abs(y - 1096) <= 8


def _lit(img):
    out = img.copy()
    x, y, w, h = ops.RECEIVE_ROI
    out[y : y + h, x : x + w] = (40, 140, 250)  # 橙色（BGR）
    return out


def test_fast_acquire_taps_receive_when_lit():
    base = cv2.imread(str(FIX / "q21-select-card.jpg"))
    gone = np.zeros_like(base)
    state = {"img": base, "clicks": []}

    def click(x, y):
        state["clicks"].append((x, y))
        state["img"] = _lit(base) if (x, y) == (200, 700) else gone

    t = [0.0]
    ok = ops.fast_acquire((200, 700), shoot=lambda: state["img"], click=click, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert ok and state["clicks"] == [(200, 700), ops.RECEIVE_TAP]


def test_fast_acquire_falls_back_when_never_lit():
    base = cv2.imread(str(FIX / "q21-select-card.jpg"))
    clicks = []
    t = [0.0]
    ok = ops.fast_acquire((200, 700), shoot=lambda: base, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: t.__setitem__(0, t[0] + s),
                          clock=lambda: t[0])  # fmt: skip
    assert not ok and clicks == [(200, 700), (200, 700)] and ops.RECEIVE_TAP not in clicks  # 点两次选项都不亮：不点受け取る，交回 KAA


def test_verify_left_ok_returns_on_transient(tmp_path):
    from hiro.contracts import Action, ActionKind, TraceWriter
    from hiro.loop.session import Session

    seq = iter(["ACTION_SELECT", "unknown", "unknown", "unknown"])
    t = [0.0]
    s = Session(TraceWriter(tmp_path / "t"), shoot=lambda: np.zeros((1280, 720, 3), np.uint8), classify=lambda: (next(seq, "unknown"), "produce"),
                sleep=lambda x: t.__setitem__(0, t[0] + x), clock=lambda: t[0])  # fmt: skip
    r = s.verify(Action(ActionKind.CONTINUE, {}), page_before="ACTION_SELECT", left_ok=True)
    assert r.ok and r.page_after is None and r.waited_s < 1.0
    seq2 = iter(["ACTION_SELECT"] + ["unknown"] * 40)
    s2 = Session(TraceWriter(tmp_path / "t2"), shoot=lambda: np.zeros((1280, 720, 3), np.uint8), classify=lambda: (next(seq2, "unknown"), "produce"),
                 sleep=lambda x: t.__setitem__(0, t[0] + x), clock=lambda: t[0])  # fmt: skip
    t[0] = 0.0
    r2 = s2.verify(Action(ActionKind.CONTINUE, {}), page_before="ACTION_SELECT")
    assert r2.waited_s >= 8.0  # 不带 left_ok：过渡帧一直等到超时（旧行为）


def test_gift_effect_wait_short():
    from hiro.loop import controller

    assert controller.QUICK_WAIT_TAP_BY_KIND["gift_effect"] <= 1.0 and controller.QUICK_WAIT_TAP_S == 15.0


def test_action_commit_no_fixed_sleep(monkeypatch):
    """选行动提交：点 3 下后直接等按钮消失，不再固定 sleep(2)；休息走 KAA 原版。"""
    from types import SimpleNamespace as NS

    from kaa.tasks.produce.new.consts import ProduceAction

    import hiro.loop.kaa_patches as kp

    slept, clicks, waited = [], [], []
    monkeypatch.setattr(kp.time, "sleep", slept.append)
    btn = NS(click=lambda: clicks.append(1), prefab="P")
    ctx = NS(fetch_available_actions=lambda: ([ProduceAction.OUTING], [btn]), controller=NS(wait_disappear=waited.append))
    kp.action_commit(ctx, ProduceAction.OUTING)
    assert clicks == [1, 1, 1] and waited == ["P"] and max(slept) < 1.0
    orig = []
    monkeypatch.setattr(kp, "_ORIG_ACTION_COMMIT", lambda self, a: orig.append(a))
    rest = NS(fetch_available_actions=lambda: ([ProduceAction.REST], [btn]), controller=NS(wait_disappear=waited.append))
    kp.action_commit(rest, ProduceAction.REST)
    assert orig == [ProduceAction.REST]


def test_select_drink_uses_fast_acquire(monkeypatch):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    from hiro.contracts import Action, Decision, ExecutionResult, GameState

    got = []
    monkeypatch.setattr(ops, "fast_acquire", lambda c: got.append(c) or True)
    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st._drink_full_skip = lambda: False
    st._note_fallback = lambda *a: None
    st._confirm_acquire = lambda: 0
    st.s = NS(state=GameState(page_fields={"candidates": [{"drink_id": "a"}]}), observe=lambda **kw: NS(fields={"candidates": [{"x": 1}]}),
              record=lambda *a, **k: None, recover=lambda *a: None)  # fmt: skip
    st.deciders = NS(decide=lambda *a, **k: Decision(action=Action.activate_effect(1), reason="", source="rule"))
    st._act = lambda commit, act, **kw: (commit(), ExecutionResult(action=act, ok=True))[1]
    kaa_commit = []
    data = NS(drinks=[NS(index=0), NS(index=1), NS(index=2)], can_skip=False)
    ctx = NS(fetch_select_drink=lambda: data, commit=lambda d, dr: kaa_commit.append(dr))
    st.on_select_drink(ctx)
    assert got == [(360, 884)] and kaa_commit == []  # 第 2 瓶（POSTIONS[1] 中心），没走 KAA commit
