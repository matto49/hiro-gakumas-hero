"""日常兜底（hiro.daily_guard）：弹窗分类、连续确认才点、单项超时打断。"""

import threading
import time

import hiro  # noqa: F401
from hiro.daily_guard import PopupGuard, classify_popup, run_with_timeout, wait_interruptible


def box(t, x, y):
    return (t, 1.0, (x - 40, y - 15, x + 40, y + 15))


MISSION_PASS = [box("ミッションパスPt獲得", 360, 400), box("ミッションパスが切り替わるタイミングで", 360, 520),
                box("自動で獲得されます", 360, 560), box("閉じる", 360, 1160)]  # fmt: skip
JEWEL = [box("ジュエルを消費して回復しますか？", 360, 600), box("キャンセル", 212, 1160), box("回復する", 500, 1160)]


def test_classify_info_popup_close():
    assert classify_popup(MISSION_PASS) == ("close", (360, 1160))


def test_classify_jewel_confirm_only_cancel():
    assert classify_popup(JEWEL) == ("cancel", (212, 1160))
    assert classify_popup([box("ジュエル", 360, 600), box("OK", 500, 1160)]) is None  # 没有キャンセル：不碰


def test_classify_leaves_action_dialogs_alone():
    assert classify_popup([box("報酬獲得", 360, 400), box("閉じる", 212, 1160), box("受け取る", 500, 1160)]) is None
    assert classify_popup([box("ショップ", 360, 400), box("閉じる", 360, 1160)]) is None  # 不是说明框标题
    assert classify_popup([box("閉じる", 360, 600)]) is None  # 不在底部按钮行


def test_guard_needs_two_consecutive_hits():
    frames = iter([MISSION_PASS, [], MISSION_PASS, MISSION_PASS])
    clicks = []
    g = PopupGuard(lambda: None, lambda x, y: clicks.append((x, y)), lambda img: next(frames))
    assert g.step() is None and g.step() is None and g.step() is None  # 中间断了一帧：重新计数
    assert g.step() == ("close", (360, 1160)) and clicks == [(360, 1160)]


def test_guard_thread_clicks_stuck_popup():
    clicks = []
    g = PopupGuard(lambda: None, lambda x, y: clicks.append((x, y)), lambda img: JEWEL, interval=0.02)
    with g:
        time.sleep(0.2)
    assert clicks and set(clicks) == {(212, 1160)}  # 含ジュエル：只点キャンセル


class Flow:
    def __init__(self):
        self.ev = threading.Event()

    def request_interrupt(self):
        self.ev.set()

    def clear_interrupt(self):
        self.ev.clear()

    def check(self):
        if self.ev.is_set():
            raise KeyboardInterrupt("User requested interrupt.")


def test_timeout_interrupts_spinning_task_and_clears():
    flow = Flow()
    t0 = time.monotonic()
    timed_out, err = run_with_timeout(lambda: wait_interruptible(30, flow), 0.2, flow)
    assert timed_out and err is None and time.monotonic() - t0 < 3
    assert not flow.ev.is_set()  # 已清，下一项不受影响


def test_no_timeout_and_errors_pass_through():
    flow = Flow()
    assert run_with_timeout(lambda: None, 5, flow) == (False, None)
    timed_out, err = run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("x")), 5, flow)
    assert not timed_out and isinstance(err, ValueError)


def test_classify_club_request_received_close():
    """2026-11-03 club_reward：领完奖励弹「リクエスト受け取り」，只有「閉じる」，以前标题不认 → 挂 300s 超时。"""
    popup = [
        box("リクエスト受け取り", 360, 420),
        box("アノマリーノート（ビジュアル）×2", 360, 640),
        box("閉じる", 360, 1160),
    ]
    assert classify_popup(popup) == ("close", (360, 1160))
    # 带「受け取る」按钮的领取框仍不碰（ACTION_TEXTS）
    assert (
        classify_popup([box("プレゼント受け取り", 360, 420), box("閉じる", 212, 1160), box("受け取る", 500, 1160)])
        is None
    )
