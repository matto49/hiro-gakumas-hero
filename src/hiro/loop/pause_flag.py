"""外部暂停旗标：旗标文件存在就暂停对局，消失就继续（最初用于「录屏一停就暂停对局」，录屏守护进程维护旗标）。

旗标文件存在 = 外部条件不满足（例如录屏没在正常录），内容一行「<epoch> <原因>」；不存在 = 正常。

- Watcher：对局进程里的后台线程，每 POLL 秒看一次旗标：出现 → kotonebot FlowController.request_pause()，消失 → request_resume()。
  暂停在主线程的下一个检查点生效：kotonebot 的 device.screenshot()、Loop 每轮、sleep 都会 check（我们自己的 while 循环每轮都截图）。
  暂停 / 恢复各写一条 WARNING，并在 trace 里记 note（rec_pause / rec_resume），复盘能列出来。
- active_clock()：扣掉暂停时长的单调时钟。空转看门狗、收尾超时、日切换恢复、AP 等待用它计时，暂停期间不会误停。
- wait_until_recording()：开局前旗标在就等它消失。
开关：设了旗标路径（HIRO_PAUSE_FLAG）才启用。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
import time
from collections.abc import Callable
from pathlib import Path

import logging

logger = logging.getLogger(__name__)

_FLAG_ENV = os.environ.get("HIRO_PAUSE_FLAG")
FLAG = Path(_FLAG_ENV) if _FLAG_ENV else Path("pause.flag")
ENABLED = bool(_FLAG_ENV)
POLL = 1.0

_lock = threading.Lock()
_state = {"since": None, "total": 0.0}  # 当前这次暂停开始的 monotonic 时刻；之前累计暂停秒数


def paused() -> bool:
    return _state["since"] is not None


def paused_total(now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    with _lock:
        return _state["total"] + (now - _state["since"] if _state["since"] is not None else 0.0)


def active_clock() -> float:
    """扣掉录屏暂停时长的单调时钟（暂停期间不走）。"""
    now = time.monotonic()
    return now - paused_total(now)


@contextmanager
def hold():
    """主线程自己在等人（例如未读剧情，hiro.loop.unread_story）：这段时间按暂停计，active_clock 不走。
    已经在暂停里（录屏旗标）就不重复记，也不替它结束。"""
    mine = not paused()
    if mine:
        _mark(True)
        _state["held"] = True
    try:
        yield
    finally:
        if mine:
            _state["held"] = False
            _mark(False)


def _mark(pause: bool, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        if pause and _state["since"] is None:
            _state["since"] = now
        elif not pause and _state["since"] is not None:
            _state["total"] += now - _state["since"]
            _state["since"] = None


def reason(flag: Path | None = None) -> str | None:
    """旗标内容（「<epoch> <原因>」去掉 epoch）；旗标不存在返回 None。"""
    flag = flag or FLAG
    try:
        text = flag.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as e:  # 读的瞬间被删 / 正在写：当作存在，原因未知
        return f"（读不到：{e}）"
    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) == 2 and parts[0].replace(".", "").isdigit() else (text or "（没写原因）")


def wait_until_recording(*, flag: Path | None = None, sleep: Callable[[float], None] = time.sleep, log_every: float = 10.0,
                         clock: Callable[[], float] = time.monotonic) -> float:  # fmt: skip
    """开局前：旗标在就等它消失（录屏开始正常录）再返回，返回等了多少秒。开关关着直接返回 0。"""
    flag = flag or FLAG
    if not ENABLED:
        return 0.0
    t0 = last = clock()
    first = True
    while (why := reason(flag)) is not None:
        if first or clock() - last >= log_every:
            logger.warning("等录屏：%s 存在（%s），录屏正常后再开局（已等 %.0fs）", flag, why, clock() - t0)
            first, last = False, clock()
        sleep(POLL)
    waited = clock() - t0
    if waited > 0:
        logger.warning("录屏已正常（等了 %.0fs），开局", waited)
    return waited


class Watcher:
    """后台线程：旗标出现 → flow.request_pause()，消失 → flow.request_resume()。flow 要在主线程取（kotonebot vars.flow）。"""

    def __init__(self, flow, *, session=None, flag: Path | None = None, poll: float = POLL):
        self.flow, self.session, self.flag, self.poll = flow, session, flag or FLAG, poll
        self.events: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        """看一次旗标，按需暂停 / 恢复（线程每 poll 秒调一次；测试直接调）。"""
        why = reason(self.flag)
        if why is not None and not paused():
            _mark(True)
            self._since = time.monotonic()
            self.flow.request_pause()
            self._event("rec_pause", why)
        elif why is None and paused() and not _state.get("held"):  # 主线程自己在等人（hold）的暂停不归旗标管
            dur = time.monotonic() - getattr(self, "_since", time.monotonic())
            _mark(False)
            self.flow.request_resume()
            self._event("rec_resume", f"录屏恢复，暂停了 {dur:.0f}s")

    def _event(self, kind: str, why: str) -> None:
        msg = f"{kind}: {time.strftime('%H:%M:%S')} {why}"
        logger.warning("录屏%s：%s", "中断，暂停对局" if kind == "rec_pause" else "恢复，继续对局", why)
        self.events.append({"kind": kind, "t": time.time(), "why": why})
        if self.session is not None:
            try:
                self.session.note(msg)  # 跟下一条 Step 写进 trace（复盘按 note 前缀 rec_pause / rec_resume 列出来）
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001  看守出错不能打断对局
                logger.warning("录屏看守出错：%r", e)
            self._stop.wait(self.poll)

    def start(self) -> "Watcher":
        if ENABLED and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="hiro-rec-pause", daemon=True)
            self._thread.start()
            logger.info("录屏看守已启动：%s（每 %.0fs 看一次）", self.flag, self.poll)
        return self

    def stop(self) -> None:
        self._stop.set()
        if paused():  # 退出时别把主线程留在暂停里
            _mark(False)
            try:
                self.flow.request_resume()
            except Exception:  # noqa: BLE001
                pass
