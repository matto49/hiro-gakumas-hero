"""考试没过线时把再挑戦交给人（用户 2026-11-21：方便排查是打牌的问题还是养成的问题）。

开关 HIRO_RETRY_HANDOFF=1（缺省关）。结算页出现「再挑戦」、程序本来要决定重不重打时，先停下：发系统通知、
写等人文件（只是说明，不用删）、DMM 版把窗口挪到屏幕正中。然后看画面判断你在做什么（用户 11-21：不让人删文件）：
- 结算页一直停着 HIRO_RETRY_HANDOFF_TIMEOUT_S 秒（缺省 60）没人动 → 当作你不打，交回程序按规则决定（"auto"）；
- 进了考试画面 → 你在亲手打，一直等（不计时，最多 PLAY_MAX_S）；
  - 打完又回到带「再挑戦」的结算页（又没过）→ 再给你 60 秒决定要不要再打；
  - 打完离开了结算页（过了）→ 交回程序从当前画面接着走（"user"）；
- 离开结算页、也不在考试里，持续 AWAY_S 秒（你点了次へ / 结束，不打了）→ 交回程序从当前画面接着走（"user"）。
删掉等人文件也算「继续」：画面还在结算页就交回程序决定，不在就从当前画面接着走。
等的时间按暂停计（pause_flag.hold），看门狗不算。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

POLL_S = 2.0
AWAY_S = 15.0  # 不在结算页也不在考试里持续这么久，才算你已经走开了结算页（点再挑戦后的确认框 / 读取画面不算）
# 你已经在亲手打（看到过考试画面）之后：考试里也有认不成「考试中」的画面（选中卡牌的放大图、技能卡移动框、回合切换动画、
# 你点开的审查日程面板），11-24 mao3 打到一半 15 秒没认出考试就判「你打完了」、程序抢过去自己打。打过之后要离开这么久才算完
AWAY_PLAYED_S = 90.0
PLAY_MAX_S = 45 * 60  # 亲手打一场考试最多等这么久


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (env if env is not None else os.environ).get("HIRO_RETRY_HANDOFF", "0") == "1"


def timeout_s(env: Mapping[str, str] | None = None) -> float:
    try:
        return max(15.0, float((env if env is not None else os.environ).get("HIRO_RETRY_HANDOFF_TIMEOUT_S") or 60))
    except ValueError:
        return 60.0


def wait(label: str, *, look: Callable[[], str], sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic, notifier: Callable[[str, str], None] | None = None,
         env: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """结算页上、要不要再挑戦之前调。look() 返回当前画面："result"（带再挑戦的结算页）/ "exam"（考试中）/ "other"。
    返回 "user"（你处理过了，程序从当前画面接着走）/ "auto"（交回程序按规则决定再挑戦）。"""
    from hiro.loop.pause_flag import hold
    from hiro.runtime import unread_story

    env = env if env is not None else os.environ
    notifier = notifier or unread_story.notify  # 通知 / 等人文件 / DMM 窗口居中 复用未读剧情那套
    path = unread_story.wait_file(env)
    unread_story._show_game(env)
    limit = timeout_s(env)
    msg = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} {label} 没过线，再挑戦交给你：{limit:.0f} 秒内点「再挑戦」就亲手打这一场，"
        f"程序等你打完再接着走；{limit:.0f} 秒没人动就交回程序。\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(msg, encoding="utf-8")
    except OSError as e:
        logger.warning("写等人文件 %s 失败：%r", path, e)
    logger.warning("%s 没过线：再挑戦交给人（%.0f 秒内没人动就交回程序）", label, limit)
    notifier(
        "数字小广：考试没过线", f"{label}：{limit:.0f} 秒内点「再挑戦」就亲手打，打完程序接着走；没人动就交回程序。"
    )
    result = "auto"
    with hold():
        idle_since, away_since, play_since, played = clock(), None, None, False
        while True:
            sleep(POLL_S)
            now = clock()
            if not path.exists():  # 手动「继续」
                try:
                    result = "auto" if look() == "result" else "user"
                except Exception:  # noqa: BLE001
                    result = "auto"
                break
            try:
                s = look()
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 再挑戦交接：读画面失败：%r", label, e)
                continue
            if s == "exam":
                if play_since is None:
                    play_since = now
                    logger.warning("%s 你在亲手打这一场，等你打完", label)
                played, away_since = True, None
                if now - play_since >= PLAY_MAX_S:
                    logger.warning("%s 亲手打了 %.0f 分钟还没打完，交回程序", label, PLAY_MAX_S / 60)
                    result = "user"
                    break
                continue
            if s == "result":
                if played:  # 打完又没过：再给 limit 秒决定要不要再打
                    logger.warning("%s 你打的这一次也没过，%.0f 秒内可以再点再挑戦", label, limit)
                    played, play_since, idle_since = False, None, now
                away_since = None
                if now - idle_since >= limit:
                    logger.warning("%s 结算页 %.0f 秒没人动，交回程序按规则决定再挑戦", label, limit)
                    result = "auto"
                    break
                continue
            away_since = now if away_since is None else away_since  # other：确认框 / 读取画面 / 你点了次へ
            if now - away_since >= (AWAY_PLAYED_S if played else AWAY_S):
                logger.warning(
                    "%s 画面已经离开结算页（%s），程序从当前画面接着走", label, "你打完了" if played else "你没再挑戦"
                )
                result = "user"
                break
    path.unlink(missing_ok=True)
    return result
