"""未读剧情：停下等人看，不替人跳过（用户 2026-11-20）。

开关 HIRO_UNREAD_STORY：skip（缺省，和以前一样跳过）/ stop。stop 时：
- 识别：剧情页点 SKIP（或快进）后，游戏对未读剧情会弹「スキップ確認：未読のコミュです」/ 快进确认框；读过的剧情直接跳过、不弹。
  所以这个确认框就是「未读」的信号（KAA 的 handle_unread_commu、主循环 FIRST_RUN_SETTINGS 都在这里点「決定」，stop 时改走这里）。
- 动作：点「キャンセル」（不跳），写等人文件（HIRO_WAIT_FILE，缺省 %LOCALAPPDATA%/hiro/waiting-for-user.txt）、发系统通知，然后等：
  - 删掉等人文件 → 继续；如果还停在剧情里，接下来 ALLOW_SKIP_S 秒内的未读确认框按「決定」（= 你同意跳过这段）；
  - 剧情页连续 LEFT_POLLS 次都不在了（你自己看完 / 手动跳过了）→ 删掉等人文件，继续；
  - 超过 HIRO_UNREAD_TIMEOUT_S（缺省 600 秒）没人管 → 关游戏 / 模拟器（hiro.loop.close_game，目标按 HIRO_CLOSE_GAME，没开时关模拟器），
    抛 UnreadStoryTimeout（ResumeBlocked 的子类：run_hif 退出码 3）。培育进度在服务器上，下次 `run_hif resume` 接着跑。
- 等的时间按暂停计（pause_flag.hold），空转看门狗、收尾超时不会把它算成没进展。
另外 stop 时开局的「早送り設定」不再打开「未読コミュでも早送り」，「演出スキップ設定」不动剧情类开关（skip_settings）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from hiro.loop.errors import ResumeBlocked

logger = logging.getLogger(__name__)

POLL_S = 3.0
LEFT_POLLS = 5  # 连续这么多次（约 15 秒）不在剧情页、也没有确认框，才算你已经看完 / 手动跳过
ALLOW_SKIP_S = 120.0  # 删等人文件后这么久内的未读确认框按「決定」


class UnreadStoryTimeout(ResumeBlocked):
    """未读剧情等人超时，已关游戏。"""


def mode(env: dict | None = None) -> str:
    v = ((env if env is not None else os.environ).get("HIRO_UNREAD_STORY") or "skip").strip().lower()
    if v not in ("skip", "stop"):
        logger.warning("HIRO_UNREAD_STORY=%r 不认识（skip / stop），按 skip", v)
        return "skip"
    return v


def stopping(env: dict | None = None) -> bool:
    return mode(env) == "stop"


def timeout_s(env: dict | None = None) -> float:
    try:
        return max(30.0, float((env if env is not None else os.environ).get("HIRO_UNREAD_TIMEOUT_S") or 600))
    except ValueError:
        return 600.0


def wait_file(env: dict | None = None) -> Path:
    env = env if env is not None else os.environ
    if env.get("HIRO_WAIT_FILE"):
        return Path(env["HIRO_WAIT_FILE"])
    base = env.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "hiro" / "waiting-for-user.txt"


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def is_unread_dialog(texts) -> bool:
    """「スキップ確認」/ 快进确认框（未読のコミュです …）。"""
    flat = [_norm(t) for t in texts]
    return any("未読" in t for t in flat) and any(("スキップ" in t or "早送り" in t) for t in flat)


def is_commu(texts) -> bool:
    """剧情页底栏：「早送り」+ ログ / AUTO / SKIP 任一（同 HiroController._skip_commu 的判据）。"""
    return any("早送" in t for t in texts) and any("ログ" in t or "AUT" in t.upper() or "SKI" in t.upper() for t in texts)


def notify(title: str, body: str) -> None:
    """系统通知，尽力而为（失败只记日志）：Windows 用 PowerShell 弹托盘气泡，macOS 用 osascript。"""
    try:
        if sys.platform == "win32":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing;"
                  "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information;"
                  "$n.Visible = $true; $n.ShowBalloonTip(15000, $env:HIRO_NOTIFY_TITLE, $env:HIRO_NOTIFY_BODY, 'Warning');"
                  "Start-Sleep -Seconds 16; $n.Dispose()")  # fmt: skip
            env = dict(os.environ, HIRO_NOTIFY_TITLE=title, HIRO_NOTIFY_BODY=body)
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f"display notification {body!r} with title {title!r}"])
    except Exception as e:  # noqa: BLE001
        logger.warning("发通知失败：%r", e)


_allow_until = [0.0]


def allow_skip(now: float | None = None) -> bool:
    """你删了等人文件后的一小段时间里，未读确认框按「決定」。"""
    return (time.monotonic() if now is None else now) < _allow_until[0]


def handle(*, cancel: Callable[[], object], confirm: Callable[[], object], texts: Callable[[], list[str]],
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           close: Callable[..., object] | None = None, notifier: Callable[[str, str], None] = notify,
           env: dict | None = None) -> str:  # fmt: skip
    """未读确认框在屏幕上时调。skip 模式 / 刚同意过跳过：点「決定」返回 "skipped"。
    stop 模式：点「キャンセル」，等人；返回 "user"（删了等人文件）/ "left"（剧情页不在了）；超时关游戏并抛 UnreadStoryTimeout。"""
    env = env if env is not None else os.environ
    if not stopping(env) or allow_skip(clock()):
        confirm()
        return "skipped"
    cancel()
    path = wait_file(env)
    limit = timeout_s(env)
    msg = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} 遇到未读剧情，已暂停。\n"
           f"看完剧情程序会自己继续；想跳过就删掉这个文件；{limit:.0f} 秒没人管会关闭游戏。\n")  # fmt: skip
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(msg, encoding="utf-8")
    except OSError as e:
        logger.warning("写等人文件 %s 失败：%r", path, e)
    logger.warning("未读剧情：点了キャンセル，暂停等人（%s；%.0f 秒后关游戏）", path, limit)
    notifier("数字小广：遇到未读剧情", f"已暂停。看完会自动继续；删掉 {path.name} 就跳过；{limit / 60:.0f} 分钟没人管会关闭游戏。")
    from hiro.loop.pause_flag import hold

    with hold():
        t0, away = clock(), 0
        while True:
            sleep(POLL_S)
            if not path.exists():
                _allow_until[0] = clock() + ALLOW_SKIP_S
                logger.warning("未读剧情：等人文件删了，继续（%.0f 秒内的未读确认框按「決定」）", ALLOW_SKIP_S)
                return "user"
            try:
                cur = texts()
            except Exception as e:  # noqa: BLE001
                logger.warning("未读剧情：等人时截图失败：%r", e)
                cur = None
            if cur is not None:
                away = 0 if (is_commu(cur) or is_unread_dialog(cur)) else away + 1
                if away >= LEFT_POLLS:
                    path.unlink(missing_ok=True)
                    logger.warning("未读剧情：剧情页已经不在了（看完 / 手动跳过），继续")
                    return "left"
            if clock() - t0 >= limit:
                break
    try:
        path.write_text(msg + f"{time.strftime('%H:%M:%S')} 超时没人管，已关闭游戏。\n", encoding="utf-8")
    except OSError:
        pass
    from hiro.loop import close_game

    what = close_game.target(env)
    (close or close_game.close)("emulator" if what == "off" else what, why="未读剧情等人超时")
    raise UnreadStoryTimeout(f"未读剧情等了 {limit:.0f} 秒没人管，已关闭游戏；看完剧情后 run_hif resume 续跑")
