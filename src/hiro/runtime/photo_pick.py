"""LIVE 之后选回忆照片：停下等人自己挑（用户 2026-11-21）。

LIVE 演出时游戏会自动拍照，演出完是「メモリーにするフォト」页：从这些照片里选一张存成回忆的封面，
点「次へ」后弹「選択したフォトをメモリーとして保存しますか? ※保存していないフォトは削除されます」，点「決定」。
程序原来直接用默认的第一张（hiro.loop.resume.finish_produce_end / hiro.runtime.finish）。

开关 HIRO_PHOTO_PICK：auto（缺省，和以前一样用第一张）/ stop。stop 时第一次看到选照片页：
- 不点，写等人文件（同 hiro.runtime.unread_story.wait_file）、发系统通知；DMM 版把游戏窗口挪到屏幕正中；
- 等你自己选、点「次へ」和「決定」。选照片页和保存确认框都连续 LEFT_POLLS 次不在 → 删掉等人文件，接着点结算画面；
- 删掉等人文件 = 用默认的那张，程序接着点；
- HIRO_PHOTO_TIMEOUT_S 秒（缺省 600）没人管 → 用默认的那张接着点（不关游戏：照片不影响进度，选拔之后还要接着打本战）。
之后 AUTO_AFTER_S 秒内再看到选照片页 / 确认框就照旧自动点，不再等第二次。
等的时间按暂停计（pause_flag.hold），收尾超时、空转看门狗不算。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

PAGE_WORD = "メモリーにするフォト"
CONFIRM_WORD = "メモリーとして保存しますか"
POLL_S = 3.0
LEFT_POLLS = 3  # 连续这么多次（约 9 秒）两种画面都不在，才算你选完了
AUTO_AFTER_S = 300.0  # 等完之后这么久内照旧自动点

_auto_until = [0.0]


def mode(env: Mapping[str, str] | None = None) -> str:
    """HIRO_LIVE 是 watch / pick / auto 时由它定（hiro.runtime.live_mode.photo_override），否则看 HIRO_PHOTO_PICK。"""
    from hiro.runtime import live_mode

    if (forced := live_mode.photo_override(env)) is not None:
        return forced
    v = ((env if env is not None else os.environ).get("HIRO_PHOTO_PICK") or "auto").strip().lower()
    if v not in ("auto", "stop"):
        logger.warning("HIRO_PHOTO_PICK=%r 不认识（auto / stop），按 auto", v)
        return "auto"
    return v


def timeout_s(env: Mapping[str, str] | None = None) -> float:
    try:
        return max(30.0, float((env if env is not None else os.environ).get("HIRO_PHOTO_TIMEOUT_S") or 600))
    except ValueError:
        return 600.0


def is_photo_page(texts) -> bool:
    return any(PAGE_WORD in t for t in texts)


def is_photo_confirm(texts) -> bool:
    return any(CONFIRM_WORD in t for t in texts)


def should_wait(texts, env: Mapping[str, str] | None = None, now: float | None = None) -> bool:
    """这一帧要不要停下等人：stop 模式、画面是选照片页、刚才没等过。"""
    now = time.monotonic() if now is None else now
    return mode(env) == "stop" and is_photo_page(texts) and now >= _auto_until[0]


def wait(*, texts: Callable[[], list], sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
         notifier: Callable[[str, str], None] | None = None, env: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """选照片页在屏幕上、should_wait 为真时调。返回 "user"（你选完了）/ "file"（删了等人文件）/ "timeout"（没人管，用默认）。
    返回后 AUTO_AFTER_S 秒内 should_wait 为假，调用方照旧自动点。"""
    from hiro.loop.pause_flag import hold
    from hiro.runtime import unread_story

    env = env if env is not None else os.environ
    notifier = notifier or unread_story.notify
    path = unread_story.wait_file(env)
    limit = timeout_s(env)
    unread_story._show_game(env, "选回忆照片")
    msg = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} 到了选回忆照片页，已暂停。\n"
        f"在游戏里选好照片、点「次へ」和「決定」，程序会自己继续；删掉这个文件 = 用默认的第一张；{limit:.0f} 秒没人管也用默认的。\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(msg, encoding="utf-8")
    except OSError as e:
        logger.warning("写等人文件 %s 失败：%r", path, e)
    logger.warning("选回忆照片：暂停等人自己选（%s；%.0f 秒没人管就用默认的第一张）", path, limit)
    notifier(
        "数字小广：选回忆照片",
        f"已暂停，请在游戏里选照片并点「次へ」「決定」。删掉 {path.name} 或 {limit:.0f} 秒没人管就用默认的第一张。",
    )
    result = "timeout"
    with hold():
        start, away = clock(), 0
        while clock() - start < limit:
            sleep(POLL_S)
            if not path.exists():
                result = "file"
                logger.warning("选回忆照片：等人文件删了，用默认的照片继续")
                break
            try:
                cur = texts()
            except Exception as e:  # noqa: BLE001
                logger.warning("选回忆照片：等人时截图失败：%r", e)
                continue
            away = away + 1 if not (is_photo_page(cur) or is_photo_confirm(cur)) else 0
            if away >= LEFT_POLLS:
                result = "user"
                logger.warning("选回忆照片：选完了，继续点结算画面")
                break
        else:
            logger.warning("选回忆照片：%.0f 秒没人管，用默认的照片继续", limit)
    path.unlink(missing_ok=True)
    _auto_until[0] = clock() + AUTO_AFTER_S
    return result
