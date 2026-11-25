"""`hiro doctor`：新手向导和出问题时的自检（设计稿 §11）。每项给 ok / 白话说明 / 下一步。

ok：True 没问题；False 要处理；None 程序判断不了，要用户自己确认（例如游戏内声音）。
默认不连游戏；加 --probe 才真的连一次设备、截一张图（只读）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.registry import Arg, Risk, command
from hiro.cli.store import open_store

PROVIDERS = {"deepseek": "DeepSeek", "codex": "Codex", "claude": "Claude"}
SCAN_NAMES = {"account": "首页数值", "idols": "偶像卡", "supports": "支援卡", "memories": "回忆", "affection": "好感度"}


def _item(id_: str, title: str, ok: bool | None, detail: str, next_: str = "", optional: bool = False) -> dict:
    return {"id": id_, "title": title, "ok": ok, "detail": detail, "next": next_, "optional": optional}


def _python() -> dict:
    v = sys.version_info
    if v < (3, 10):
        return _item(
            "python",
            "Python 环境",
            False,
            f"Python 版本 {v.major}.{v.minor} 太旧，要 3.10 以上",
            "重新安装（设置 → 环境 → 重新安装）",
        )
    missing = []
    for mod in ("numpy", "cv2"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return _item(
            "python", "Python 环境", False, f"缺依赖：{'、'.join(missing)}", "重新安装（设置 → 环境 → 重新安装）"
        )
    return _item("python", "Python 环境", True, f"Python {v.major}.{v.minor}.{v.micro}，依赖齐全")


def _platform(probe: bool) -> dict:
    dev = (os.environ.get("HIRO_DEVICE") or "auto").lower()
    if dev in ("mumu", "adb", "leidian"):
        label, testing = {"mumu": "MuMu 模拟器", "adb": "安卓设备（adb）", "leidian": "雷电模拟器"}[dev], True
    elif dev == "dmm" or (dev == "auto" and sys.platform == "win32"):
        label, testing = "Windows · DMM 版", False
    elif sys.platform == "darwin":
        label, testing = "Mac · PlayCover", False
    else:
        return _item(
            "platform", "游戏平台", False, "这个系统还不支持", "目前支持 Mac（PlayCover）和 Windows（DMM 版 / MuMu）"
        )
    suffix = "（测试中）" if testing else ""
    if sys.platform == "darwin" and dev in ("auto", "maatools") and not Path("/Applications/PlayCover.app").exists():
        return _item(
            "platform",
            "游戏平台",
            False,
            "没找到 PlayCover",
            "安装 hguandl 分支的 PlayCover，并在游戏设置里打开 maaTools",
        )
    if not probe:
        return _item("platform", "游戏平台", None, f"{label}{suffix}：还没试连游戏", "打开游戏停在首页，再点「试连」")
    try:
        from hiro.devices import open_device

        d = open_device("dmm" if dev == "auto" and sys.platform == "win32" else None)
        d.connect()
        img = d.screenshot()
        d.close()
    except Exception as e:  # noqa: BLE001 连不上的原因都转成白话
        hint = {
            "darwin": "确认游戏开着、用的是 hguandl 分支 PlayCover、游戏设置里打开了 maaTools",
            "win32": "确认 DMM 版游戏开着、窗口没最小化；程序要以管理员身份运行",
        }.get(sys.platform, "确认游戏开着")
        return _item("platform", "游戏平台", False, f"{label}{suffix}：连不上游戏（{e}）", hint)
    h, w = img.shape[:2]
    return _item("platform", "游戏平台", True, f"{label}{suffix}：已连上，画面 {w}×{h}")


def _settings() -> dict:
    try:
        return json.loads((ensure_home() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _model() -> dict:
    st = _settings()
    p = st.get("provider") or "deepseek"  # 缺省 DeepSeek
    if p == "none":
        return _item(
            "model",
            "AI 模型",
            False,
            "选了不用模型：只能用快捷指令，对局决策只用规则和算法",
            "到设置里选 DeepSeek 并填 API Key（可以跳过）",
            optional=True,
        )
    if p == "deepseek" and not st.get("has_key"):
        return _item(
            "model",
            "AI 模型",
            False,
            "默认用 DeepSeek，但还没填 API Key：现在只能用快捷指令，对局决策只用规则和算法",
            "到设置里填 DeepSeek 的 API Key（可以跳过）",
            optional=True,
        )
    return _item("model", "AI 模型", True, f"已选 {PROVIDERS.get(p, p)}", optional=True)


def _secrets() -> dict:
    try:
        names = json.loads((ensure_home() / "secrets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        names = []
    if {"game-account", "game-password"} <= set(names):
        return _item("secrets", "游戏账号密码", True, "已保存（只用于掉登录时自动粘贴）", optional=True)
    return _item(
        "secrets",
        "游戏账号密码",
        False,
        "没保存：掉登录时要你自己登",
        "可以跳过；要用就到设置 → 账号密码里填",
        optional=True,
    )


def _game_settings() -> dict:
    meta, rows = open_store().latest("game_settings")
    if meta is None:
        return _item(
            "game_settings",
            "游戏快进设置",
            None,
            "还没检查过快进 / 跳过演出那几项",
            "游戏停在首页时让初星助手检查（hiro run start check-settings，只读）",
        )
    off = [r["item"] for r in rows if r["state"] == "OFF"]
    unknown = [r["item"] for r in rows if r["state"] is None]
    if off:
        return _item(
            "game_settings",
            "游戏快进设置",
            False,
            f"这几项没开：{'、'.join(off)}（{meta['scanned_at'][5:16]} 检查）",
            "在游戏里打开 メニュー → 設定 → プロデュース，把它们打开；不开也能打，开局会多处理几个弹框",
        )
    if unknown:
        return _item(
            "game_settings", "游戏快进设置", None, f"有几项没读到：{'、'.join(unknown)}", "游戏停在首页时再检查一次"
        )
    return _item("game_settings", "游戏快进设置", True, f"5 项都开着（{meta['scanned_at'][5:16]} 检查）")


def _catalog() -> dict:
    p = ensure_home() / "catalog" / "catalog.ja.json"
    if not p.is_file():
        return _item("catalog", "游戏资料库", False, "还没下载", "运行 hiro catalog sync（向导里点「下载资料库」）")
    try:
        n = len(json.loads(p.read_text(encoding="utf-8")).get("cards") or {})
    except (OSError, ValueError):
        return _item("catalog", "游戏资料库", False, "文件坏了", "重新运行 hiro catalog sync")
    return _item("catalog", "游戏资料库", True, f"已下载（{n} 张技能卡）")


def _scan() -> dict:
    st = open_store()
    done = [SCAN_NAMES[k] for k in SCAN_NAMES if st.latest(k)[0] is not None]
    if not done:
        return _item("scan", "账号信息", False, "还没扫描过账号", "让初星助手扫描支援卡、回忆（向导最后一步）")
    return _item("scan", "账号信息", True, f"已扫描：{'、'.join(done)}")


def _plugins() -> dict | None:
    """2026-11-25 插件架构：配了 strategy.json 时核对里面的插件都装了、能用（契约版本对得上）。没配时不出这一项。"""
    from hiro import plugins
    from hiro.cli.cmd_plugins import config_path
    from hiro.strategy import load

    path = config_path()
    if not path.is_file():
        return None
    cfg = load(path)
    want = [("observation_source", i) for i in cfg.sources]
    want += [("exam_advisor", i) for i in set(cfg.exam.values())]
    want += [("produce_advisor", i) for i in set(cfg.produce.values())]
    bad = []
    for kind, pid in want:
        if plugins.get_by_id(kind, pid) is None:
            why = next((x.error for x in plugins.available(kind) if x.id == pid), None) or "没装"
            bad.append(f"{pid}（{why}）")
    problems = bad + list(cfg.warnings)
    if problems:
        return _item(
            "plugins",
            "插件",
            False,
            "strategy.json 里有用不了的插件：" + "；".join(problems) + "，相关决策点会用内置逻辑",
            "运行 hiro plugins list 查已装插件，装好或从 strategy.json 里去掉",
            optional=True,
        )
    return _item("plugins", "插件", True, f"strategy.json 里配的 {len(want)} 个插件都能用", optional=True)


@command(
    "doctor",
    Risk.READ,
    "自检：Python 环境、游戏平台、游戏声音、模型、账号密码、资料库、账号扫描，每项给原因和下一步。--probe 真的连一次游戏（只读截图）。",
    args=(Arg("--probe", "真的连一次游戏，截一张图", type="bool"),),
)
def _doctor(a: dict) -> dict:
    items = [
        _python(),
        _platform(a["probe"]),
        _item(
            "sound",
            "游戏声音",
            None,
            "程序看不到游戏里的声音设置",
            "在游戏里打开设置 → 声音，把 BGM、语音、效果音都关掉（不用动电脑的音量）",
        ),
        _game_settings(),
        _model(),
        _secrets(),
        _catalog(),
        _scan(),
    ]
    if (pl := _plugins()) is not None:
        items.append(pl)
    ready = all(i["ok"] is not False for i in items if not i["optional"] and i["id"] not in ("scan",))
    return {"items": items, "ready": ready}
