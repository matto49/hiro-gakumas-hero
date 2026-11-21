"""未读剧情：停下等人看，不替人跳过（用户 2026-11-20）。

开关 HIRO_UNREAD_STORY：skip（缺省，和以前一样跳过）/ stop。stop 时：
- 识别：剧情页点 SKIP（或快进）后，游戏对未读剧情会弹「スキップ確認：未読のコミュです」/ 快进确认框；读过的剧情直接跳过、不弹。
  所以这个确认框就是「未读」的信号（KAA 的 handle_unread_commu、主循环 FIRST_RUN_SETTINGS 都在这里点「決定」，stop 时改走这里）。
- 动作：点「キャンセル」（不跳），写等人文件（HIRO_WAIT_FILE，缺省 %LOCALAPPDATA%/hiro/waiting-for-user.txt）、发系统通知，然后等：
  - 删掉等人文件 → 继续；如果还停在剧情里，接下来 ALLOW_SKIP_S 秒内的未读确认框按「決定」（= 你同意跳过这段）；
  - 连续 LEFT_POLLS 次认出是日文原版的游戏页面、不在剧情里（你自己看完 / 手动跳过了）→ 删掉等人文件，继续；
    汉化插件开着（界面是中文、认不出页面）时不算，接着等：关掉汉化后程序才继续；
  - 剧情框的文字 HIRO_UNREAD_TIMEOUT_S 秒（缺省 60）没变（没人在推进剧情）→ 关游戏 / 模拟器（hiro.loop.close_game，目标按 HIRO_CLOSE_GAME，没开时关模拟器），
    抛 UnreadStoryTimeout（ResumeBlocked 的子类：run_hif 退出码 3）。培育进度在服务器上，下次 `run_hif resume` 接着跑。
  「有没有人在看」只看剧情框文字（OCR）：剧情里人物待机、风吹等画面一直在动，按像素变化判不了（用户 11-20）。
  对话文字逐字打出来也算变化，那只在有人点了之后才发生。
- 等的时间按暂停计（pause_flag.hold），空转看门狗、收尾超时不会把它算成没进展。
- 进入等待时存一帧到 reports/live/unread/（剧情框位置的校准样本）。
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
DIALOG_Y = (760, 1240)  # 剧情框（含名字条）的 y 范围（720×1280）；底栏按钮的字另外去掉
BAR_WORDS = ("ログ", "AUTO", "AUT0", "SKIP", "早送", "SKI")
SAME_TEXT = 0.8  # 剧情框文字相似度达到这个算没变（OCR 同一帧读两次会差一两个字）
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
        return max(15.0, float((env if env is not None else os.environ).get("HIRO_UNREAD_TIMEOUT_S") or 60))
    except ValueError:
        return 60.0


def wait_file(env: dict | None = None) -> Path:
    env = env if env is not None else os.environ
    if env.get("HIRO_WAIT_FILE"):
        return Path(env["HIRO_WAIT_FILE"])
    base = env.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "hiro" / "waiting-for-user.txt"


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def _texts_only(items) -> list[str]:
    return [t if isinstance(t, str) else t[0] for t in items]


def dialog_text(items) -> str:
    """剧情框里的文字（去掉底栏按钮）。items：字符串，或 (文字, 中心 y)；有 y 时只取 DIALOG_Y 里的。"""
    out = []
    for it in items:
        t, y = (it, None) if isinstance(it, str) else (it[0], it[1])
        n = _norm(t)
        if not n or any(w in n.upper() for w in BAR_WORDS) or (y is not None and not DIALOG_Y[0] <= y <= DIALOG_Y[1]):
            continue
        out.append(n)
    return "".join(out)


def _changed(a: str | None, b: str) -> bool:
    if a is None:
        return True
    from difflib import SequenceMatcher

    return a != b and SequenceMatcher(None, a, b).ratio() < SAME_TEXT


def is_unread_dialog(texts) -> bool:
    """「スキップ確認」/ 快进确认框（未読のコミュです …）。"""
    flat = [_norm(t) for t in _texts_only(texts)]
    return any("未読" in t for t in flat) and any(("スキップ" in t or "早送り" in t) for t in flat)


FF_WORDS = ("早送", "快进", "快進", "快转", "快轉")  # 底栏「早送り」；后几个是汉化插件开着时可能的译名
BAR_KEYS = ("ログ", "AUT", "SKI", "LOG", "跳过", "跳過", "自动", "自動", "日志", "日誌", "记录", "記録")
# 认得出的游戏页面（日文原版界面）：看到这些字才算「剧情已经结束、回到了游戏」。汉化插件开着时界面是中文，
# 程序认不出页面，宁可接着等（超时就关游戏），也不在看不懂的界面上继续操作（AGENTS.md：游戏界面必须是日文原版）
# 只用带假名的词：体力 / 決定 / 相談 这类汉字词中文界面里也有（测试 test_chinese_ui_after_story_does_not_resume）
GAME_PAGE_WORDS = ("レッスン", "ドリンク", "残りターン", "スキルカード", "次へ", "プロデュース", "アイテム", "おでかけ",
                   "お休み", "ホーム", "キャンセル", "パラメータ",
                   # 剧情结束直接进考试（11-20 DMM：第 2 回 選抜試験 的开场剧情 → Now loading → 考试开始页，没认出来，干等 60 秒关了游戏）
                   "スター性", "ターン", "オーディション", "ステージ", "NOWLOADING", "LOADING")  # fmt: skip
KANA_MIN = 8  # 没命中上面的词时：假名至少这么多字、又没有简体中文专用字，也算日文原版页面
SIMPLIFIED_ONLY = set("的了们这个说吗么没时对还会为过让给现样着里么么吧呢该么点关开发东车门间问")  # 汉化插件译文常见、日文里不用的简体字


def _kana(text: str) -> int:
    return sum(1 for ch in text if "ぁ" <= ch <= "ゟ" or "ァ" <= ch <= "ヺ")


def is_commu(texts) -> bool:
    """剧情页底栏：「早送り」+ ログ / AUTO / SKIP 任一（同 HiroController._skip_commu 的判据）；汉化插件的译名也认。"""
    texts = _texts_only(texts)
    up = [t.upper() for t in texts]
    return any(w in t for t in texts for w in FF_WORDS) and any(k in t for t in up for k in BAR_KEYS)


def is_game_page(texts) -> bool:
    """日文原版的游戏页面（不在剧情里）。"""
    texts = _texts_only(texts)
    if is_commu(texts):
        return False
    up = [_norm(t).upper() for t in texts]
    if any(w in t for t in up for w in GAME_PAGE_WORDS):
        return True
    joined = "".join(up)
    return _kana(joined) >= KANA_MIN and not (set(joined) & SIMPLIFIED_ONLY)


# Windows 通知（Toast）：借 PowerShell 的 AppId 发，scenario=reminder 会一直留在屏幕上直到点掉，带提示音。
# 托盘气泡（NotifyIcon.ShowBalloonTip）在 Windows 11 + 管理员进程（DMM 版）下 11-20 实测没弹出来，所以换成这个。
# 标题 / 正文经环境变量传进去（不拼进命令行，免得引号和中文出问题）
TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
    "$t = [Security.SecurityElement]::Escape($env:HIRO_NOTIFY_TITLE); $b = [Security.SecurityElement]::Escape($env:HIRO_NOTIFY_BODY);"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml(\"<toast scenario='reminder'><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual>"
    "<actions><action content='OK' arguments='dismiss' activationType='system'/></actions>"
    "<audio src='ms-winsoundevent:Notification.Reminder'/></toast>\");"
    r"$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe';"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def notify(title: str, body: str) -> None:
    """系统通知，尽力而为（失败只记日志）：Windows 发 Toast（TOAST_PS），macOS 用 osascript（没实测）。HIRO_NOTIFY=0 关掉。"""
    if os.environ.get("HIRO_NOTIFY") == "0":
        return
    try:
        if sys.platform == "win32":
            env = dict(os.environ, HIRO_NOTIFY_TITLE=title, HIRO_NOTIFY_BODY=body)
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", TOAST_PS], env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # fmt: skip
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f"display notification {body!r} with title {title!r}"])
    except Exception as e:  # noqa: BLE001
        logger.warning("发通知失败：%r", e)


_allow_until = [0.0]


def allow_skip(now: float | None = None) -> bool:
    """你删了等人文件后的一小段时间里，未读确认框按「決定」。"""
    return (time.monotonic() if now is None else now) < _allow_until[0]


def _save_frame(snap) -> None:
    if snap is None:
        return
    try:
        import cv2

        img = snap()
        d = Path("reports/live/unread")
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / f"{time.strftime('%Y%m%d-%H%M%S')}.png"), img)
    except Exception as e:  # noqa: BLE001
        logger.warning("未读剧情：存帧失败：%r", e)


def _show_game(env: dict) -> None:
    """DMM 版：后台点击把游戏窗口挪到了点击点落在光标下的位置，常常半个窗口在屏幕外，人没法看剧情。挪到屏幕正中（用户 11-20）。"""
    if (env.get("HIRO_DEVICE") or "").strip().lower() != "dmm" or sys.platform != "win32":
        return
    try:
        from hiro.devices import win_window

        pos = win_window.center()
        logger.info("未读剧情：游戏窗口挪到屏幕正中 %s", pos)
    except Exception as e:  # noqa: BLE001
        logger.warning("未读剧情：挪游戏窗口失败：%r", e)


def handle(*, cancel: Callable[[], object], confirm: Callable[[], object], texts: Callable[[], list],
           snap: Callable[[], object] | None = None,
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
    _show_game(env)
    path = wait_file(env)
    limit = timeout_s(env)
    msg = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} 遇到未读剧情，已暂停。\n"
           f"看完剧情程序会自己继续；想跳过就删掉这个文件；剧情 {limit:.0f} 秒没推进会关闭游戏。\n")  # fmt: skip
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(msg, encoding="utf-8")
    except OSError as e:
        logger.warning("写等人文件 %s 失败：%r", path, e)
    logger.warning("未读剧情：点了キャンセル，暂停等人（%s；剧情 %.0f 秒没推进就关游戏）", path, limit)
    _save_frame(snap)
    notifier("数字小广：遇到未读剧情", f"已暂停。看完会自动继续；删掉 {path.name} 就跳过；剧情 {limit:.0f} 秒没推进会关闭游戏。")
    from hiro.loop.pause_flag import hold

    with hold():
        last_change, away, last_text = clock(), 0, None
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
                away = away + 1 if (is_game_page(cur) and not is_unread_dialog(cur)) else 0
                if away >= LEFT_POLLS:
                    path.unlink(missing_ok=True)
                    logger.warning("未读剧情：剧情页已经不在了（看完 / 手动跳过），继续")
                    return "left"
                text = dialog_text(cur)
                if _changed(last_text, text):
                    if last_text is not None:
                        logger.info("未读剧情：剧情在推进（%s…）", text[:20])
                    last_change, last_text = clock(), text
            if clock() - last_change >= limit:
                break
    try:
        path.write_text(msg + f"{time.strftime('%H:%M:%S')} 超时没人管，已关闭游戏。\n", encoding="utf-8")
    except OSError:
        pass
    from hiro.loop import close_game

    what = close_game.target(env)
    (close or close_game.close)("emulator" if what == "off" else what, why="未读剧情等人超时")
    raise UnreadStoryTimeout(f"未读剧情 {limit:.0f} 秒没推进，已关闭游戏；看完剧情后 run_hif resume 续跑")
