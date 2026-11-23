"""日常任务（tools/daily/run_daily.py）的兜底：弹窗看守 + 单项超时。

2026-11-02 mission_reward：ミッションパス页弹「ミッションパスPt獲得」说明框（只有「閉じる」），KAA 的循环不认，空转约 10 分钟。
- PopupGuard：KAA 任务执行期间后台线程每隔 interval 秒截图 OCR，同一个弹窗连续 2 次都在才动手（KAA 自己正在点的转场帧不碰）：
  · 含「ジュエル」的确认框：只点「キャンセル」（绝不点确认，花钻石由用户决定）；
  · 「〜獲得」「お知らせ」「について」一类说明框，底部只有「閉じる」：点「閉じる」。
- run_with_timeout：到时间调用 flow.request_interrupt()（kotonebot 的 FlowController，KAA 的 Loop / sleep / 设备操作里都会 check，
  抛 KeyboardInterrupt），任务结束后 clear_interrupt()，返回是否超时。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

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
    if any("ジュエル" in t for t, _ in texts):
        return ("cancel", cancel) if cancel else None  # 含ジュエル：只认キャンセル，没有就不碰
    close = next((c for t, c in buttons if any(k in t for k in CLOSE_TEXTS)), None)
    if close is None or any(any(k in t for k in ACTION_TEXTS) for t, _ in buttons):
        return None
    if any(any(k in t for k in INFO_TITLES) for t, c in texts if c[1] < BUTTON_BAND[0]):
        return "close", close
    return None


class PopupGuard:
    """后台看守。shoot / click / ocr 可注入；step() 单步供测试。同一个判定连续 confirm 次才点。"""

    def __init__(self, shoot: Callable, click: Callable[[int, int], None], ocr: Callable, *, interval: float = 4.0, confirm: int = 2,
                 on_action: Callable[[str, object], None] | None = None):  # fmt: skip
        self.shoot, self.click, self.ocr = shoot, click, ocr
        self.interval, self.confirm, self.on_action = interval, confirm, on_action
        self.actions: list[tuple[str, tuple[int, int]]] = []
        self._last, self._hits = None, 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> tuple[str, tuple[int, int]] | None:
        img = self.shoot()
        hit = classify_popup(self.ocr(img))
        if hit is None:
            self._last, self._hits = None, 0
            return None
        self._hits = self._hits + 1 if hit == self._last else 1
        self._last = hit
        if self._hits < self.confirm:
            return None
        kind, pos = hit
        logger.warning(
            "日常兜底：弹窗 %s，点 %s", "含ジュエル → キャンセル" if kind == "cancel" else "说明框 → 閉じる", pos
        )
        self.click(*pos)
        self.actions.append(hit)
        if self.on_action:
            self.on_action(kind, img)
        self._last, self._hits = None, 0
        return hit

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception as e:  # noqa: BLE001  看守不能把异常抛给主流程
                logger.warning("日常兜底看守出错：%r", e)

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="daily-popup-guard", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5)
        return False


def run_with_timeout(fn: Callable[[], object], timeout: float, flow) -> tuple[bool, BaseException | None]:
    """跑 fn；超过 timeout 秒就 flow.request_interrupt()（KAA 下一次 check 抛 KeyboardInterrupt）。
    返回 (是否超时, fn 抛出的异常或 None)。结束时总会 clear_interrupt()，不影响下一项。"""
    fired = threading.Event()

    def fire():
        fired.set()
        flow.request_interrupt()

    timer = threading.Timer(timeout, fire)
    timer.daemon = True
    timer.start()
    err = None
    try:
        fn()
    except BaseException as e:  # noqa: BLE001  KeyboardInterrupt 也要接住
        err = e
    finally:
        timer.cancel()
        flow.clear_interrupt()
    if fired.is_set() and isinstance(err, KeyboardInterrupt):
        err = None
    return fired.is_set(), err


def wait_interruptible(seconds: float, flow, step: float = 0.05) -> None:
    """测试 / 示例用：像 KAA 的 Loop 一样不停 check 的等待。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        flow.check()
        time.sleep(step)
