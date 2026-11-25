"""日常任务的弹窗判据（hiro.daily.common.Popup 用）：

2026-11-02 mission_reward：ミッションパス页弹「ミッションパスPt獲得」说明框（只有「閉じる」），KAA 的循环不认，空转约 10 分钟。
- 含「ジュエル」的确认框：只点「キャンセル」（绝不点确认，花钻石由用户决定）；
- 「〜獲得」「お知らせ」「について」一类说明框，底部只有「閉じる」：点「閉じる」。
原来的后台线程看守 PopupGuard 和单项超时 run_with_timeout 只给 KAA 那条路用，2026-11-25 随它删了
（新实现的看守和超时是 Runtime 的中断 hiro.daily.common.Popup / Deadline）。
"""

from __future__ import annotations

import logging

from hiro.vision.textnorm import contains  # OCR 容错匹配（用户 11-21）

logger = logging.getLogger(__name__)

INFO_TITLES = (
    "獲得",
    "お知らせ",
    "について",
    "ヘルプ",
    "説明",
    "受け取り",
)  # 受け取り：サークル「リクエスト受け取り」只有「閉じる」（2026-11-03 club_reward 挂 300s）
CLOSE_TEXTS = ("閉じる",)
CANCEL_TEXTS = ("キャンセル",)
ACTION_TEXTS = (
    "受け取る",
    "決定",
    "OK",
    "はい",
    "購入",
    "交換",
    "使用",
    "開始",
    "実行",
)  # 有这些按钮的框不是纯说明框，不碰
BUTTON_BAND = (1080, 1240)  # 弹窗底部按钮行（720x1280）


def _center(b) -> tuple[int, int]:
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2


def classify_popup(boxes) -> tuple[str, tuple[int, int]] | None:
    """OCR 结果 [(text, conf, (x1, y1, x2, y2))] → ("cancel", 点) / ("close", 点) / None。"""
    texts = [(t.replace(" ", ""), _center(b)) for t, _, b in boxes]
    buttons = [(t, c) for t, c in texts if BUTTON_BAND[0] <= c[1] <= BUTTON_BAND[1]]
    cancel = next((c for t, c in buttons if any(k in t for k in CANCEL_TEXTS)), None)
    if any(contains(t, "ジュエル") for t, _ in texts):
        return ("cancel", cancel) if cancel else None  # 含ジュエル：只认キャンセル，没有就不碰
    close = next((c for t, c in buttons if any(k in t for k in CLOSE_TEXTS)), None)
    if close is None or any(any(k in t for k in ACTION_TEXTS) for t, _ in buttons):
        return None
    if any(any(k in t for k in INFO_TITLES) for t, c in texts if c[1] < BUTTON_BAND[0]):
        return "close", close
    return None
