"""「通信エラー」弹窗（第二十二局本战轮间 12:26：游戏弹「通信エラー / 通信中にエラーが発生しました」，按钮 タイトルへ / リトライ；
轮间每步「8s 内轮间主画面没出现」失败重试，主循环把它判成 unknown，195s 后停机。总控点了リトライ后又弹
「通信エラー / 時間が経っているのでタイトルに戻します」，只有「閉じる」，只能回标题）。

两种：
- retry：有「リトライ」→ 点リトライ（用户规则：绝不点タイトルへ）；
- to_title：没有リトライ、只有「閉じる」（强制回标题）→ 点閉じる，抛 CommErrorToTitle，
  由 run_hif 按日期切换同一条路回首页、从「プロデュース中」续跑。
输入是 OCR 结果：[(text, conf, (x1, y1, x2, y2))]（hiro.produce.ops.ocr_boxes / hiro.vision.ocr.ocr_boxes 格式），坐标是整帧坐标。
"""

from __future__ import annotations

from hiro.vision.textnorm import contains

import time
from collections.abc import Callable

import logging

from hiro.loop.date_change import DateChanged

logger = logging.getLogger(__name__)

TITLE_HINTS = ("通信エラー", "通信工ラー")  # OCR 偶尔把「エ」读成「工」
RETRY = "リトライ"
CLOSE = "閉じる"
TO_TITLE_BUTTON = "タイトルへ"  # 绝不点
RETRY_SETTLE = 3.0


class CommErrorToTitle(DateChanged):
    """通信エラー 只能回标题：已点「閉じる」，run_hif 应从标题回首页续跑同一局（与日期切换同路）。"""


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def _center(b) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def comm_error(boxes) -> dict | None:
    """认出通信エラー框 → {kind: retry | to_title | unknown, retry, close}（按钮中心，没有为 None）；不是返回 None。"""
    texts = [(_norm(t), b) for t, _, b in boxes]
    if not any(any(h in t for h in TITLE_HINTS) or ("通信" in t and ("エラー" in t or "工ラー" in t)) for t, _ in texts):
        return None
    # 按钮是「リトライ」短字（OCR 会多读按钮图标成「くリトライ」）；E209「アセット取得に失敗」的说明句「通信環境の良い場所でリトライしてください」
    # 也含リトライ，排在按钮上面，原来先命中它、点了 10 次说明文字都没重试（11-19 账号 2）。先找以リトライ结尾的短字，说明句不点
    cands = [(t, b) for t, b in texts if RETRY in t and not contains(t, TO_TITLE_BUTTON) and "ください" not in t]
    button = [(t, b) for t, b in cands if t.endswith(RETRY) and len(t) <= len(RETRY) + 2]
    retry = next((_center(b) for t, b in (button or cands)), None)
    close = next((_center(b) for t, b in texts if t.endswith(CLOSE)), None)
    kind = "retry" if retry else "to_title" if close else "unknown"
    return {"kind": kind, "retry": retry, "close": close}


def handle(boxes, click: Callable[[int, int], None], *, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """按 OCR 结果处理通信エラー：retry 点リトライ并等一下，返回 "retry"；to_title 点閉じる并抛 CommErrorToTitle；
    认出但两种按钮都没读到返回 "unknown"（不点任何东西）；不是通信エラー返回 None。"""
    e = comm_error(boxes)
    if e is None:
        return None
    if e["kind"] == "retry":
        logger.warning("通信エラー：点「リトライ」%s", e["retry"])
        click(*e["retry"])
        sleep(RETRY_SETTLE)
        return "retry"
    if e["kind"] == "to_title":
        logger.warning("通信エラー（只能回标题）：点「閉じる」%s，回首页续跑", e["close"])
        click(*e["close"])
        sleep(RETRY_SETTLE)
        raise CommErrorToTitle("通信エラー：タイトルに戻ります")
    logger.warning("通信エラー框认出了，但没读到「リトライ」/「閉じる」，不点")
    return "unknown"
