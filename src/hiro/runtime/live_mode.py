"""培育结束后的 LIVE：看不看、跳不跳、照片谁来选（用户 2026-11-21）。

开关 HIRO_LIVE：
- play（缺省，和以前一样）：点「TAP TO START」开始演出，演出不点，等它播完；照片按 HIRO_PHOTO_PICK。
- watch（我亲自看）：开始前的曲目页不点，像未读剧情一样停下等人——发通知、写等人文件、DMM 版把窗口挪到屏幕正中。
  你自己点开始、看完，程序接着走；照片你自己选（hiro.runtime.photo_pick）。
  曲目页 HIRO_LIVE_TIMEOUT_S 秒（缺省 60）还在 = 没人在，关游戏 / 模拟器（同未读剧情），抛 LiveWatchTimeout（run_hif 退出码 3，之后 resume）。
  删掉等人文件 = 这场不看了：程序点开始再跳过，照片仍然等你选。
- pick（跳过 LIVE，我自己选照片）：演出里点右上角 SKIP；照片你自己选。
- auto（全自动）：跳过 LIVE，照片用默认的第一张（不管 HIRO_PHOTO_PICK）。
LIVE 里游戏照样自动拍照，跳过不影响选照片（用户 11-21）。

SKIP 按钮：横屏帧右上区域 OCR 认到「SKIP」才点；认不到时每 REVEAL_S 秒点一下屏幕中间（演出中点一下会显示菜单），最多 REVEAL_MAX 次。
点了 SKIP 弹确认框时点「スキップ」/「はい」/「OK」/「決定」，不点キャンセル。
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable, Mapping

from hiro.loop.errors import ResumeBlocked

logger = logging.getLogger(__name__)

MODES = ("play", "watch", "pick", "auto")
LIVE_START_TEXT = "TAPTOSTART"
LIVE_INFO_TEXTS = ("作詞", "作曲", "編曲")
CONFIRM_BUTTONS = ("スキップする", "スキップ", "はい", "OK", "決定")
POLL_S = 3.0
STARTED_POLLS = 2  # 连续这么多次不在曲目页 = 你点了开始
REVEAL_S = 8.0
REVEAL_MAX = 6
NEW_LIVE_GAP_S = 30.0  # 这么久没见横屏帧，下一次横屏算新的一场 LIVE

Items = list  # [(文字, (cx, cy))]


class LiveWatchTimeout(ResumeBlocked):
    """LIVE 开始前等人超时，已关游戏。"""


def mode(env: Mapping[str, str] | None = None) -> str:
    v = ((env if env is not None else os.environ).get("HIRO_LIVE") or "play").strip().lower()
    if v not in MODES:
        logger.warning("HIRO_LIVE=%r 不认识（%s），按 play", v, " / ".join(MODES))
        return "play"
    return v


def photo_override(env: Mapping[str, str] | None = None) -> str | None:
    """LIVE 模式定下的照片选法：watch / pick → stop（自己选），auto → auto（默认那张）；play → None（按 HIRO_PHOTO_PICK）。"""
    return {"watch": "stop", "pick": "stop", "auto": "auto"}.get(mode(env))


def timeout_s(env: Mapping[str, str] | None = None) -> float:
    try:
        return max(15.0, float((env if env is not None else os.environ).get("HIRO_LIVE_TIMEOUT_S") or 60))
    except ValueError:
        return 60.0


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def start_point(w: int, h: int, items: Items) -> tuple[int, int] | None:
    """曲目页（开始前）：「TAP TO START」的位置；只认到曲目信息（作詞 / 作曲 / 編曲）时点下方中间；不是曲目页 None。"""
    for t, c in items:
        if LIVE_START_TEXT in _norm(t).upper():
            return int(c[0]), int(c[1])
    if any(k in t for t, _ in items for k in LIVE_INFO_TEXTS):
        return w // 2, int(h * 0.85)
    return None


def find_skip(w: int, h: int, items: Items) -> tuple[int, int] | None:
    """右上角的 SKIP（中心在右 40%、上 30%）。"""
    for t, c in items:
        if "SKIP" in _norm(t).upper() and c[0] > w * 0.6 and c[1] < h * 0.3:
            return int(c[0]), int(c[1])
    return None


def find_confirm(items: Items) -> tuple[int, int] | None:
    """跳过确认框：画面上有「スキップ」字样的问句时，点确认按钮（不点キャンセル）。"""
    texts = [_norm(t) for t, _ in items]
    if not any("スキップ" in t and ("?" in t or "？" in t or "しますか" in t) for t in texts):
        return None
    for want in CONFIRM_BUTTONS:
        for t, c in items:
            if _norm(t) == want:
                return int(c[0]), int(c[1])
    return None


def watch_wait(*, items: Callable[[], tuple[int, int, Items]], sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, close: Callable[..., object] | None = None,
               notifier: Callable[[str, str], None] | None = None, env: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """曲目页上、watch 模式时调。items() → (宽, 高, [(文字, 中心)])。返回 "user"（你点了开始）/ "file"（删了等人文件：不看了）；
    曲目页一直在到超时：关游戏并抛 LiveWatchTimeout。"""
    from hiro.loop.pause_flag import hold
    from hiro.runtime import unread_story

    env = env if env is not None else os.environ
    notifier = notifier or unread_story.notify
    path = unread_story.wait_file(env)
    limit = timeout_s(env)
    unread_story._show_game(env, "LIVE")
    msg = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} LIVE 要开始了，已暂停。\n"
        f"在游戏里点开始看 LIVE，看完程序会自己继续；删掉这个文件 = 这场不看（程序跳过）；{limit:.0f} 秒没人点开始会关闭游戏。\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(msg, encoding="utf-8")
    except OSError as e:
        logger.warning("写等人文件 %s 失败：%r", path, e)
    logger.warning("LIVE：开始前暂停等人（%s；%.0f 秒没人点开始就关游戏）", path, limit)
    notifier(
        "数字小广：LIVE 要开始了",
        f"已暂停，请在游戏里点开始。删掉 {path.name} 就跳过；{limit:.0f} 秒没人点会关闭游戏。",
    )
    with hold():
        start, away = clock(), 0
        while clock() - start < limit:
            sleep(POLL_S)
            if not path.exists():
                logger.warning("LIVE：等人文件删了，这场不看，跳过")
                return "file"
            try:
                w, h, cur = items()
            except Exception as e:  # noqa: BLE001
                logger.warning("LIVE：等人时截图失败：%r", e)
                continue
            away = away + 1 if start_point(w, h, cur) is None else 0
            if away >= STARTED_POLLS:
                path.unlink(missing_ok=True)
                logger.warning("LIVE：开始了，你在看；演出完程序接着走")
                return "user"
    with contextlib.suppress(OSError):
        path.write_text(msg + f"{time.strftime('%H:%M:%S')} 超时没人点开始，已关闭游戏。\n", encoding="utf-8")
    from hiro.runtime import close_game

    what = close_game.target(env)
    (close or close_game.close)("emulator" if what == "off" else what, why="LIVE 开始前等人超时")
    raise LiveWatchTimeout(f"LIVE 开始前 {limit:.0f} 秒没人点开始，已关闭游戏；之后 run_hif resume 续跑")


class LiveDriver:
    """横屏 LIVE 的一拍（hiro.runtime 用；原来也给 2026-11-24 删掉的旧主循环 hiro.loop.controller 用）。"""

    def __init__(self, env: Mapping[str, str] | None = None, clock: Callable[[], float] = time.monotonic, **wait_kw):
        self.env, self.clock, self.wait_kw = env, clock, wait_kw
        self._reset(None)

    def _reset(self, now: float | None) -> None:
        self.first, self.last, self.watched, self.skip_this = now, now, False, False
        self.reveal_at, self.reveals, self.skip_taps = now, 0, 0

    def step(self, w: int, h: int, items: Items, tap: Callable[[int, int], object],
             shoot: Callable[[], tuple[int, int, Items]]) -> str | None:  # fmt: skip
        """一张横屏帧（已 OCR）。返回做了什么（start / watched / skip / confirm / reveal）或 None。"""
        now = self.clock()
        if self.last is None or now - self.last > NEW_LIVE_GAP_S:
            self._reset(now)
        self.last = now
        m = mode(self.env)
        skipping = m in ("pick", "auto") or self.skip_this
        if skipping:
            if (c := find_confirm(items)) is not None:
                tap(*c)
                logger.info("LIVE：跳过确认框，点 %s", c)
                return "confirm"
            if (s := find_skip(w, h, items)) is not None and self.skip_taps < REVEAL_MAX:
                tap(*s)
                self.skip_taps += 1
                logger.info("LIVE：点右上角 SKIP %s", s)
                return "skip"
        p = start_point(w, h, items)
        if p is not None:
            if m == "watch" and not self.watched:
                self.watched = True
                if watch_wait(items=shoot, env=self.env, **self.wait_kw) == "user":
                    self.last = self.clock()
                    return "watched"
                self.skip_this = True
            tap(*p)
            logger.info("LIVE 曲目页：点 %s 开始演出", p)
            return "start"
        if (
            skipping
            and self.reveals < REVEAL_MAX
            and now - (now if self.reveal_at is None else self.reveal_at) >= REVEAL_S
        ):
            self.reveal_at, self.reveals = now, self.reveals + 1
            tap(w // 2, h // 2)
            logger.info("LIVE：没认到 SKIP，点一下画面中间调出菜单（第 %d 次）", self.reveals)
            return "reveal"
        return None


_driver: list = [None]


def driver() -> LiveDriver:
    if _driver[0] is None:
        _driver[0] = LiveDriver()
    return _driver[0]
