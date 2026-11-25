"""日常兜底（hiro.daily_guard）：弹窗分类（看守和超时在 hiro.daily.common，测试在 test_daily）。"""

import hiro  # noqa: F401
from hiro.daily_guard import classify_popup


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
