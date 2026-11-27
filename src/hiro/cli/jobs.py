"""任务种类与任务进程（hiro 服务里跑）。

每个任务是一个子进程（argv 是列表，不走 shell），stdout / stderr 的每一行变成一条事件，结束时补一条 done / failed。
事件写 HIRO_HOME/jobs/<编号>/events.jsonl，每行 {"seq", "t", "type", "text", ...}。
暂停 / 接手：往这个任务的 HIRO_PAUSE_FLAG 旗标文件写一行（主循环在检查点停下，不再点击）；继续：删掉旗标。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import hiro
from hiro.cli import reason_text
from hiro.cli.catalog import idol_name

REPO = Path(hiro.__file__).resolve().parents[2]  # 仓库根（tools/ 下的脚本从这里找）
LIVE = ("running", "paused", "takeover")
# 对局里用的 LLM（hiro.brain.llm.from_spec 读这些）：桌面程序按模型设置在确认开局时传过来；HIRO_LLM=off 表示只用规则和算法
LLM_VARS = ("HIRO_LLM", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HIRO_CODEX_BIN")
STOP_GRACE = 10.0
TRACE_POLL = 0.5
FRAME_EVERY = 1.5  # 实时画面事件最多这么久一条（帧存得很密，不全进事件流）
REASON_MAX = 160

_LOG_PREFIX = re.compile(r"^\[\d{4}-\d\d-\d\d [\d:,.]+\]\s*\[([A-Z]+)\]\s*\[[^\]]*\]\s*")
_SHOT_SUFFIX = re.compile(r"（截图在 [^）]*）\s*$")


# 没接住的异常：traceback 最后一行「模块.异常名: 说明」（G1 §8 错误文案：游戏没开时任务直接抛 DeviceUnavailable，
# 日志里没有「停下：」也没有 ERROR，原因是空的）
_EXC_LINE = re.compile(
    r"^(?:[A-Za-z_][\w]*\.)*((?:[A-Za-z_]\w*)?(?:Error|Exception|Gone|Unavailable|Blocked|Page)): (.+)$"
)

_TB_END = re.compile(r"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)(?:: (.*))?$")
_EXC_SUFFIX = re.compile(r"(?:Error|Exception|Gone|Unavailable|Blocked|Page)$")


# 已经自己恢复了的 ERROR（N40，G2 11-26：「Apple Vision 连续 3 次失败…这个进程改用 RapidOCR」之后程序因别的原因崩了，
# 对话里的原因却是这条），不当作任务失败的原因
_RECOVERED = re.compile(r"改用 RapidOCR|改到子进程里跑")


def center_dmm_window() -> None:
    """任务结束后把 DMM 游戏窗口放回屏幕正中（11-25 用户：后台点击会把窗口挪到最后一次点击的位置，
    标题栏有时跑到屏幕外，手动都拖不回来）。只在 Windows + HIRO_DEVICE=dmm；没有窗口 / 没权限就算了。"""
    if sys.platform != "win32" or (os.environ.get("HIRO_DEVICE") or "").strip().lower() != "dmm":
        return
    try:
        from hiro.devices import win_window

        win_window.center()
    except Exception:  # noqa: BLE001, S110  居中失败不影响任务结果
        pass


def failure_reason(lines: list[str]) -> str | None:
    """任务出错时给人看的一句原因：最后一条「停下：…」，没有就最后一条 ERROR 日志（去掉时间 / 级别 / 模块前缀和本机截图路径）。
    11-25 review：failed 事件只写「看上面的日志」，对话和对局卡里看不到原因。"""
    stop = err = exc = None
    in_tb = False
    for line in lines:
        t = line.strip()
        if t == "Traceback (most recent call last):":
            in_tb = True
        elif in_tb and line[:1] not in (" ", "\t") and (m := _TB_END.match(t)):
            # traceback 后第一行不缩进的就是异常行：异常名不限后缀（StopIteration）、可以没有说明（AssertionError）（#130 review）
            in_tb = False
            name, msg = m.group(1), m.group(2)
            if msg and _EXC_SUFFIX.search(name):
                exc = f"{name}: {msg}"
            else:
                exc = f"程序出错停下了（{name}）" + (f"：{msg}" if msg else "")
        elif (m := _EXC_LINE.match(t)) and not _LOG_PREFIX.match(t):  # traceback 开头被截掉时（日志只留最后 20 行）
            exc = f"{m.group(1)}: {m.group(2)}"
        if t.startswith("停下："):
            stop = t[len("停下：") :]
        elif t.endswith("停下") and not _LOG_PREFIX.match(t) and len(t) > 2:
            stop = t[: -len("停下")].rstrip(
                "，, "
            )  # 工具把「停下」写在句尾（G1-F2：扫偶像卡「没进到偶像详情页（…），停下」）
        elif (m := _LOG_PREFIX.match(t)) and m.group(1) in ("ERROR", "CRITICAL") and not _RECOVERED.search(t):
            err = t[m.end() :]
    why = stop or err or exc
    if not why:
        return None
    why = _SHOT_SUFFIX.sub("", why).strip()
    why = reason_text.plain(why)  # 卡号换卡名、程序原话换白话（先换再截：名字比编号短）
    return why if len(why) <= REASON_MAX else why[: REASON_MAX - 1] + "…"


def step_event(rec: dict, trace: Path) -> dict:
    """trace 的一步（steps.jsonl 一行）→ 事件字段：页面、动作、理由、谁定的（规则 / 算法 / LLM / 兜底）、截图。"""
    obs = rec.get("observation") or {}
    dec = rec.get("decision") or {}
    seq = rec.get("seq")
    frame = trace / "frames" / f"{int(seq):05d}.jpg" if isinstance(seq, int) or str(seq).isdigit() else None
    return {
        "step": seq,
        "page": ((obs.get("page") or {}).get("name")),
        "action": ((dec.get("action") or {}).get("kind")),
        "args": ((dec.get("action") or {}).get("args")),
        "source": f"plugin:{dec['plugin']}" if dec.get("plugin") else dec.get("source"),
        "decider": dec.get("decider"),
        "warnings": dec.get("warnings") or [],
        "notes": rec.get("notes") or [],
        "frame": str(frame) if frame is not None and frame.is_file() else None,
        "text": dec.get("reason") or "",
    }


def read_new_frames(index: Path, pos: int) -> tuple[int, dict | None]:
    """帧索引 frames.jsonl 从 pos 往后读完整的行：返回 (新位置, 最后一条记录或 None)。半行留到下次。"""
    if not index.is_file():
        return pos, None
    last = None
    with index.open("rb") as f:
        f.seek(pos)
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            pos += len(raw)
            try:
                last = json.loads(raw)
            except ValueError:
                continue
    return pos, last


@dataclass(frozen=True)
class Kind:
    name: str
    help: str
    argv: Callable[[dict, Path], list[str]]  # (参数, 任务目录) → 子进程命令
    summary: Callable[[dict], str]  # 确认卡上的一句话
    output: str | None = None  # 任务目录里的结果文件（扫描任务）；结束后由 scan_ingest 导入信息库
    env: dict = field(default_factory=dict)
    pausable: bool = True  # 主循环认 HIRO_PAUSE_FLAG（hif / tower / selftest）；日常和扫描脚本不认，只能停止


_PY = sys.executable
ACCOUNT_SCAN = "hiro.daily.account_scan"  # 首页顶栏读数 + プロフィール的プロデューサーID（scan-account）

_SELFTEST = r"""
import json, os, sys, time
flag = os.environ.get("HIRO_PAUSE_FLAG")
print("llm=%s base=%s key=%s" % (os.environ.get("HIRO_LLM", ""), os.environ.get("OPENAI_BASE_URL", ""),
                                 "set" if os.environ.get("OPENAI_API_KEY") else "unset"), flush=True)
trace = os.path.join("traces", sys.argv[2])
os.makedirs(os.path.join(trace, "frames"), exist_ok=True)
import base64
open(os.path.join(trace, "frames", "00001.jpg"), "wb").write(base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAQAAkDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkaKKKko//2Q=="))
step = {"seq": 1, "observation": {"page": {"name": "SELFTEST"}},
        "decision": {"action": {"kind": "Wait"}, "reason": "自检：不碰游戏", "source": "rule", "decider": "selftest"},
        "notes": []}
open(os.path.join(trace, "steps.jsonl"), "a", encoding="utf-8").write(json.dumps(step, ensure_ascii=False) + "\n")
n = max(1, int(float(sys.argv[1]) * 5))
i = 0
while i < n:
    if flag and os.path.exists(flag):
        time.sleep(0.1); continue
    i += 1
    print(f"selftest {i}", flush=True)
    time.sleep(0.2)
"""


def _hif_argv(p: dict, d: Path) -> list[str]:
    argv = [
        _PY,
        "-m",
        "hiro.run_hif",
        p.get("command") or "new",
        "--mode",
        p.get("mode") or "qualify",
        "--trace",
        p.get("trace") or d.name,
    ]
    if p.get("idol"):
        argv += ["--idol", p["idol"]]
    if p.get("main_attr"):
        argv += ["--main-attr", p["main_attr"]]
    if p.get("no_llm"):
        argv.append("--no-llm")
    return argv


DAILY_ITEMS = (
    "activity_funds",
    "presents",
    "assignment",
    "contest",
    "shop",
    "coin_gacha",
    "mission_reward",
    "club_reward",
)
DAILY_LABEL = {
    "activity_funds": "活动费",
    "presents": "礼物",
    "assignment": "工作派遣",
    "contest": "竞赛",
    "shop": "商店",
    "coin_gacha": "硬币扭蛋",
    "mission_reward": "任务奖励",
    "club_reward": "社团",
}
HAJIME_LEVELS = ("regular", "pro", "master")
NIA_LEVELS = ("pro", "master")


def _daily_opts(p: dict) -> dict[str, str]:
    """日常的花钱选项，缺的按缺省（不买、不抽、综合力最低）补齐。「另外要买的」只在买推荐 / 全部时算（#122 review）。"""
    shop = str(p.get("shop") or "off").strip().lower()
    return {
        "contest_pick": str(p.get("contest_pick") or "lowest"),
        "shop": shop,
        "shop_items": str(p.get("shop_items") or "") if shop != "off" else "",
        "shop_ap": str(p.get("shop_ap") or ""),
        "shop_refresh": "on" if shop != "off" and str(p.get("shop_refresh") or "off").lower() == "on" else "off",
        "coin_gacha": str(p.get("coin_gacha") or "off"),
    }


def _daily_argv(p: dict, d: Path) -> list[str]:
    argv = [_PY, str(REPO / "tools" / "daily" / "run_daily.py")]
    items = [i for i in (p.get("items") or "").split(",") if i]
    if items:
        argv += ["--only", ",".join(items)]
    # 每项都显式传（空的也传）：不让任务继承环境里的 HIRO_SHOP 等，执行的和确认摘要写的一样（CodeRabbit #138）
    for key, val in _daily_opts(p).items():
        argv += ["--" + key.replace("_", "-"), val]
    return argv


_AP_NAMES = {"pt": "支援强化点数获取提升", "note": "笔记获取数提升", "retry": "再挑战券", "regen": "回忆再生成券"}
_GACHA_NAMES = {"friend": "友情", "sense": "感性", "logic": "理性", "anomaly": "非凡"}


def _daily_summary(p: dict) -> str:
    """确认摘要：做哪几项，加上会花什么（商店、体力商店、扭蛋；不买不抽也写明，CodeRabbit #122）。"""
    items = [i for i in (p.get("items") or "").split(",") if i]
    o = _daily_opts(p)
    run = lambda k: not items or k in items  # noqa: E731
    spend = []
    if run("shop"):
        shop = {"recommended": "商店买推荐商品（花金币）", "all": "商店能买的都买（花金币）"}.get(o["shop"], "商店不买")
        if o["shop_items"]:
            shop += f"，另外买：{o['shop_items']}"
        if o["shop_refresh"] == "on":
            shop += "，用免费刷新再买一轮（不花钻石）"
        spend.append(shop)
        # 体力商店：和执行端同一套解析（小写、只认 pt / note / retry / regen；CodeRabbit #138）
        keys = [k for k in (x.strip().lower() for x in o["shop_ap"].split(",")) if k in _AP_NAMES]
        if ap := [_AP_NAMES[k] for k in dict.fromkeys(keys)]:
            spend.append("体力商店买" + "、".join(ap) + "（花体力）")
    if run("coin_gacha"):
        from hiro.daily.coin_gacha import plan

        # 和真正抽的时候同一套解析（大小写 / 空格、写错的次数不抽、同一个池合起来不超上限）：确认时看到的就是实际要抽的（CodeRabbit #122 / #138）
        g = o["coin_gacha"]
        got = plan(g)
        if got == "auto":
            spend.append("硬币扭蛋抽 1 次（花硬币）")
        elif got:
            spend.append("硬币扭蛋抽" + "、".join(f"{_GACHA_NAMES.get(k, k)} {n} 次" for k, n in got) + "（花硬币）")
        else:
            spend.append("硬币扭蛋不抽" if g.strip().lower() in ("", "off") else "硬币扭蛋不抽（次数没写对）")
    if run("contest"):  # 竞赛选哪个对手，确认时也写出来（CodeRabbit #138）
        pick = o["contest_pick"]
        spend.append("竞赛挑" + ("综合力最低的对手" if pick == "lowest" else f"第 {pick} 个对手"))
    head = "领日常奖励" + (f"（{'、'.join(DAILY_LABEL.get(i, i) for i in items)}）" if items else "")
    return head + ("：" + "；".join(spend) if spend else "")


def _produce_argv(module: str, default_level: str):
    """初（hiro.runtime.hajime）/ N.I.A（hiro.runtime.nia_sample）：新主循环，编成号走参数，存帧放任务目录。"""

    def build(p: dict, d: Path) -> list[str]:
        argv = [
            _PY,
            "-m",
            module,
            p.get("command") or "new",
            "--idol",
            p.get("idol") or "",
            "--difficulty",
            p.get("difficulty") or default_level,
            "--trace",
            p.get("trace") or d.name,
            "--out",
            str(d / "frames"),
        ]
        for key, flag in (
            ("main_attr", "--main-attr"),
            ("support_set", "--support-set"),
            ("memory_set", "--memory-set"),
        ):
            if p.get(key):
                argv += [flag, str(p[key])]
        if p.get("no_llm"):
            argv.append("--no-llm")
        if p.get("final_rank") and module.endswith(".hajime"):
            argv += ["--final-rank", str(p["final_rank"])]  # 只有初支持：期末没到这个名次就用再挑戦
        return argv

    return build


def _with_prepare(mode: str, build):
    """开局前准备（cmd_run._prepare_in_job 填 prepare）：任务先由 hiro.runtime.prepare_start 挑空白编成位、写编成，再 exec 原来的命令。"""

    def argv(p: dict, d: Path) -> list[str]:
        inner = build(p, d)
        if not p.get("prepare") or (p.get("command") or "new") != "new":
            return inner
        what = ["--what", p["prepare_what"]] if p.get("prepare_what") else []
        if p.get("overwrite"):  # N65：玩家同意覆盖的那几样，写编成时不加「只写空位」的检查
            what += ["--overwrite", str(p["overwrite"])]
        return [
            _PY,
            "-m",
            "hiro.runtime.prepare_start",
            "--mode",
            mode,
            *what,
            "--out",
            str(d / "prepare.json"),
            "--",
            *inner,
        ]

    return argv


def _prepare_suffix(p: dict) -> str:
    return f"\n开局前：{p['prepare_note']}" if p.get("prepare") and p.get("prepare_note") else ""


_ROUND_NAMES = {"mid1": "中間", "mid2": "中間 2", "final": "最終", "lesson": "练习课",
                "q1": "选拔 1", "q2": "选拔 2", "q3": "选拔 3", "r1": "R1", "r2": "R2"}  # fmt: skip


def _search_suffix(p: dict) -> str:
    """开局时按场次选了出牌方式（--exam-search）：确认摘要写出哪几场用搜索。"""
    if p.get("exam_search") is None:
        return ""
    keys = [k.strip().lower() for k in str(p["exam_search"]).split(",") if k.strip().lower() in _ROUND_NAMES]
    return f"（搜索出牌：{'、'.join(_ROUND_NAMES[k] for k in keys)}）" if keys else "（全部快速出牌）"


def _produce_summary(name: str, default_level: str):
    def summary(p: dict) -> str:
        level = (p.get("difficulty") or default_level).upper()
        if (p.get("command") or "new") == "resume":
            return f"接着打进行中的{name}培育" + _search_suffix(p)
        return (
            f"开一局{name} {level}"
            + (f"（{idol_name(p['idol'])}）" if p.get("idol") else "")
            + _search_suffix(p)
            + _prepare_suffix(p)
        )

    return summary


def _write_formation_argv(p: dict, d: Path) -> list[str]:
    argv = [_PY, str(REPO / "tools" / "write_formation.py"), "--mode", p.get("scenario") or "hif"]
    if p.get("what"):
        argv += ["--what", p["what"]]
    if p.get("dry_run"):
        argv.append("--dry-run")
    return argv


def _write_formation_summary(p: dict) -> str:
    mode = {"hif": "H.I.F", "hajime": "初", "nia": "N.I.A"}.get(p.get("scenario") or "hif", p.get("scenario"))
    if p.get("dry_run"):
        return f"对照编成方案看 {mode} 的编成差在哪几格（只看，不改）"
    return f"把编成方案写进游戏（{mode} 的支援卡 / 回忆编成）"


def _hif_summary(p: dict) -> str:
    if (p.get("command") or "new") == "resume":
        return "接着打进行中的 H.I.F 培育" + _search_suffix(p)
    if (p.get("mode") or "qualify") == "main":
        return "开一局 H.I.F 本战（会用掉一份选拔回忆）" + _search_suffix(p)
    return (
        "开一局 H.I.F 选拔"
        + (f"（{idol_name(p['idol'])}）" if p.get("idol") else "")
        + _search_suffix(p)
        + _prepare_suffix(p)
    )


def _slices_on() -> bool:
    from hiro.state.memory_slices import enabled

    return enabled()


def _memory_scan_argv(p: dict, d: Path) -> list[str]:
    """回忆扫描的命令：「回忆按片扫描」开关关着（缺省）照旧整表滚动扫；开了按片（#116 审查重要 5）。"""
    base = [_PY, "-m", "hiro.memory_scan", "scan", "--from-home"]
    if not _slices_on():
        return [*base, "--out", str(d / "out.json")]
    return [*base, "--slices", p.get("slices") or "all", *([] if p.get("full") else ["--incremental"]),
            *(["--budget-s", str(p["budget_s"])] if p.get("budget_s") else []), "--out", str(d / "out.json")]  # fmt: skip


KINDS: dict[str, Kind] = {
    "selftest": Kind(
        "selftest",
        "自检：只打印几行，不碰游戏",
        lambda p, d: [_PY, "-c", _SELFTEST, str(p.get("seconds") or 1), d.name],
        lambda p: "跑一次自检（不碰游戏）",
    ),
    "hif": Kind(
        "hif",
        "H.I.F 培育：选拔 / 本战 / 续跑",
        _with_prepare("hif", _hif_argv),
        _hif_summary,
        env={"HIRO_RUNTIME": "own"},
    ),
    "tower": Kind(
        "tower",
        "偶像之路：按偶像路线往上爬",
        lambda p, d: (
            [
                _PY,
                "-m",
                "hiro.runtime.tower_play",
                "--idol-name",
                p.get("idol") or "",
                "--layers",
                str(p.get("layers") or 1),
                "--trace",
                d.name,
            ]
            + (["--no-llm"] if p.get("no_llm") else [])
        ),
        lambda p: f"爬偶像之路（{p.get('idol') or '?'}，{p.get('layers') or 1} 层）",
        env={"HIRO_RUNTIME": "own"},
    ),
    "hajime": Kind(
        "hajime",
        "「初」培育（レギュラー / プロ / マスター）",
        _with_prepare("hajime", _produce_argv("hiro.runtime.hajime", "regular")),
        _produce_summary("「初」", "regular"),
        env={"HIRO_RUNTIME": "own"},
    ),
    "nia": Kind(
        "nia",
        "N.I.A 培育（プロ / マスター；认不出的页会停下交给你）",
        _with_prepare("nia", _produce_argv("hiro.runtime.nia_sample", "pro")),
        _produce_summary(" N.I.A", "pro"),
        env={"HIRO_RUNTIME": "own"},
    ),
    "daily": Kind(
        "daily",
        "日常：活動費、礼物、工作、竞赛、任务奖励、社团（可只选几项）",
        _daily_argv,
        _daily_summary,
        pausable=False,
    ),
    "write-formation": Kind(
        "write-formation",
        "把开局编成方案写进游戏的编成位，写完回读核对（--dry-run 只报差在哪几格，不改）",
        _write_formation_argv,
        _write_formation_summary,
        pausable=False,
    ),
    "check-settings": Kind(
        "check-settings",
        "检查游戏设置里快进 / 跳过演出那几项开没开（只读：打开菜单看完回首页，不点开关；--fix 不对的顺手改好）",
        lambda p, d: (
            [_PY, str(REPO / "tools" / "check_settings.py"), "--json", str(d / "out.json")]
            + (["--fix"] if p.get("fix") else [])
        ),
        lambda p: "把游戏设置改好（快进、跳过演出）" if p.get("fix") else "检查游戏设置（只读，不改开关）",
        output="game_settings",
        pausable=False,
    ),
    "fit-window": Kind(
        "fit-window",
        "Windows DMM：把游戏窗口调成适合识别的大小（9:16，按显示器自适应）",
        lambda p, d: [_PY, "-m", "hiro.devices.win_window", "fit"],
        lambda p: "调整 DMM 游戏窗口大小",
        pausable=False,
    ),
    "main-task": Kind(
        "main-task",
        "初星課題：回主页关掉课题完成弹窗（奖励自动到账，记下完成了哪几步），再进课题页读 NEXT（编号、要求、进度）",
        lambda p, d: [_PY, "-m", "hiro.daily.main_task", "--out", str(d / "out.json")],
        lambda p: "看初星課題（关完成弹窗、读 NEXT）",
        pausable=False,
    ),
    "story": Kind(
        "story",
        "看初星コミュ：N 章里没看过的话逐话再生并跳过（跳过也算看过），直到 --until 或遇到锁着的话",
        lambda p, d: (
            [_PY, "-m", "hiro.daily.story", "--out", str(d / "out.json"), "--chapter", str(p.get("chapter") or 1)]
            + (["--until", str(p["until"])] if p.get("until") else [])
        ),
        lambda p: f"看初星剧情第 {p.get('chapter') or 1} 章" + (f"（到第 {p['until']} 话）" if p.get("until") else ""),
        pausable=False,
    ),
    "contest-unit": Kind(
        "contest-unit",
        "竞赛编队：コンテスト → ユニット編成，用「おまかせ」按现有回忆重排，读出総合力合計（--dry-run 只读不重排）",
        lambda p, d: (
            [_PY, "-m", "hiro.daily.contest_unit", "--out", str(d / "out.json")]
            + (["--read-only"] if p.get("dry_run") else [])
        ),
        lambda p: "看竞赛编队总合力" + ("（只读）" if p.get("dry_run") else "（自动编队重排）"),
        pausable=False,
    ),
    "scan-account": Kind(
        "scan-account",
        "读首页顶栏的 PLv、AP、ジュエル，再进菜单的プロフィール读プロデューサーID（信息库按账号分开，读到新账号就切过去）",
        lambda p, d: [_PY, "-m", ACCOUNT_SCAN, "--out", str(d / "out.json")],
        lambda p: "读首页数值和制作人 ID（会点开菜单里的个人资料）",
        output="account",
        pausable=False,
    ),
    "collect-all": Kind(
        "collect-all",
        "一键全收集（只读）：首页数值 → 偶像卡 → 支援卡 → 编成 → 回忆（12 片全读），每步跑完就入库；--only 只做其中几步",
        lambda p, d: [
            _PY,
            "-m",
            "hiro.collect_all",
            "--dir",
            str(d),
            *(["--only", p["only"]] if p.get("only") else []),
        ],
        lambda p: _collect_all_summary(p.get("only")),
        pausable=False,
    ),
    "scan-idols": Kind(
        "scan-idols",
        "扫描偶像卡（只读：偶像一览里逐张点开读卡名，最后点「<<」退出，不点決定）",
        lambda p, d: [_PY, "-m", "hiro.produce.idol_scan", "--out", str(d / "out.json")],
        lambda p: "扫描偶像卡（只读，不改选中的偶像）",
        output="idols",
        pausable=False,
    ),
    "scan-supports": Kind(
        "scan-supports",
        "扫描支援卡（只读：在「選択」列表里滚动，最后点キャンセル）",
        lambda p, d: [
            _PY,
            str(REPO / "tools" / "support_inventory.py"),
            "--list",
            "--from-home",
            "--out",
            str(d / "out.json"),
        ],
        lambda p: "扫描支援卡库存（只读，不改编成）",
        output="supports",
        pausable=False,
    ),
    "scan-memories": Kind(
        "scan-memories",
        "扫描回忆（只读：在「メモリー選択」里逐格读）。设置里开了「回忆按片扫描」时按片（流派 × 模式，12 片）读：缺省增量补扫新回忆，full=True 时全读",
        lambda p, d: _memory_scan_argv(p, d),
        lambda p: (
            ("全读回忆库存" if p.get("full") else "补扫新回忆") + "（只读，不改编成）"
            if _slices_on()
            else "扫描回忆库存（只读，不改编成）"
        ),
        output="memories",
        pausable=False,
    ),
    "scan-formations": Kind(
        "scan-formations",
        "读编成位现状（只读：编成菜单里翻 1–20 号、读图，不点格子）",
        lambda p, d: [
            _PY,
            "-m",
            "hiro.produce.formation_survey",
            "--mode",
            p.get("scenario") or "hif",
            "--out",
            str(d / "out.json"),
        ],
        lambda p: (
            f"读 {({'hif': 'H.I.F', 'hajime': '初', 'nia': 'N.I.A'}).get(p.get('scenario') or 'hif')} 的编成位现状（只读，不改编成）"
        ),
        output="formations",
        pausable=False,
    ),
}


def _refresh_before_start(kind: Kind) -> Kind:
    """开局前自动补扫回忆（设计 5.6，开关 HIRO_SCAN_BEFORE_START，缺省关）：开新一局时命令外面包 hiro.memory_refresh，
    确认卡上写明。开关在调用时读（初星助手设置里改了不用重启服务）。续打（resume）不补扫。"""
    from dataclasses import replace

    def on(p: dict) -> bool:
        from hiro.memory_refresh import enabled

        return enabled() and (p.get("command") or "new") == "new" and bool(p.get("idol"))

    def argv(p: dict, d: Path) -> list[str]:
        orig = kind.argv(p, d)
        if not on(p):
            return orig
        return [_PY, "-m", "hiro.memory_refresh", "--idol", p["idol"], "--", *orig]

    def summary(p: dict) -> str:
        return kind.summary(p) + ("（开局前会先补扫回忆，最多 3 分钟）" if on(p) else "")

    return replace(kind, argv=argv, summary=summary)


for _k in ("hif", "hajime", "nia"):
    KINDS[_k] = _refresh_before_start(KINDS[_k])


RESUME_CHAIN = ("hif", "hajime", "nia")  # 续打时接上同一局上一段 trace 的任务种类
RESUME_CHAIN_MAX_AGE_S = 2 * 86400  # 上一段停下超过两天就不接（中间多半在别处打完 / 放弃了）


def resume_chain(jobs: Path, traces: Path, kind: str, params: dict) -> dict:
    """续打（command=resume）接上同一局：找同种类最近一个任务，它没打完（不是 done）就沿用它的 trace 基名和偶像卡。

    N10 / N11（11-26 G1）：桌面端「接着打」每次用新任务号当 trace 名、不带偶像卡编号，
    hif.Core._prior_trace_dirs 按基名找不到上一段——拿过的卡、考完的场数、局面打底都丢了；偶像退回空，
    提示词的「偶像」一节当成篠澤広写（藤田ことね的局 LLM 理由里出现「和篠澤広的打法契合」）。
    返回要并进参数的 {"trace": "<基名>-rN", "idol": ...}；接不上返回 {}。调用方已给的 idol 不覆盖。"""
    prev = None
    for d in sorted(jobs.glob("*"), reverse=True):
        try:
            j = json.loads((d / "job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if j.get("kind") == kind:
            prev = j
            break
    if prev is None or prev.get("state") in ("done", "running"):
        return {}  # 上一局打完了：这次续的是别处开的新局
    if time.time() - float(prev.get("ended_at") or prev.get("started_at") or 0) > RESUME_CHAIN_MAX_AGE_S:
        return {}
    pp = prev.get("params") or {}
    base = re.sub(r"-r\d+$", "", pp.get("trace") or prev.get("id") or "")
    if not base:
        return {}
    used = [
        int(m.group(1)) for t in traces.glob(base + "-r*") if (m := re.fullmatch(re.escape(base) + r"-r(\d+)", t.name))
    ]
    out = {"trace": f"{base}-r{max(used, default=0) + 1}"}
    if not params.get("idol") and pp.get("idol"):
        out["idol"] = pp["idol"]
    return out


def jobs_dir(home: Path) -> Path:
    d = home / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _signal_tree(proc: subprocess.Popen, *, kill: bool) -> None:
    """结束 proc 和它的子孙进程。POSIX：任务进程用 start_new_session 起，自成一组，向整组发信号；
    Windows：taskkill /T 结束进程树（/F 强制）。"""
    if sys.platform == "win32":
        r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", *(["/F"] if kill else [])],
                           capture_output=True, check=False)  # fmt: skip
        if r.returncode != 0:
            proc.kill() if kill else proc.terminate()
        return
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.kill() if kill else proc.terminate()


class Job:
    def __init__(
        self,
        home: Path,
        kind: Kind,
        params: dict,
        on_exit: Callable[[Job], None] | None = None,
        llm_env: dict | None = None,
    ):
        self.id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        llm = {k: str(v) for k, v in (llm_env or {}).items() if k in LLM_VARS and v}
        if not llm:  # 命令行 hiro confirm 开的（桌面程序会传）：按桌面设置推，和界面开局用同一个模型（设计稿 §5）
            from hiro.cli.llm_env import env_from_settings

            llm = env_from_settings(home)
        self._llm_off = llm.get("HIRO_LLM", "").lower() == "off"
        if self._llm_off:
            llm.pop("HIRO_LLM")
            params = {**params, "no_llm": True}
        self._llm_env = llm  # 只进子进程的环境，不写 job.json、不进事件（OPENAI_API_KEY）
        self.kind, self.params = kind, params
        self.dir = jobs_dir(home) / self.id
        self.dir.mkdir(parents=True)
        self.work = home / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self.flag = self.dir / "pause.flag"
        if params.get("command") == "resume" and kind.name in RESUME_CHAIN:
            self.params = params = {**params, **resume_chain(jobs_dir(home), self.work / "traces", kind.name, params)}
        # hif / tower / selftest 用 --trace <任务编号>；续打接上一段时是 <上一段基名>-rN（resume_chain）
        self.trace = self.work / "traces" / (params.get("trace") or self.id)
        self.state = "running"
        self.started_at, self.ended_at, self.exit_code = time.time(), None, None
        self.reason: str | None = None  # 出错原因（failed 时）
        self._lock = threading.Lock()
        self._seq = 0
        self._events = self.dir / "events.jsonl"
        self._on_exit = on_exit
        self.proc: subprocess.Popen | None = None

    # ----------------------------------------------------------------- 事件
    def emit(self, type_: str, text: str = "", **extra) -> dict:
        with self._lock:
            ev = {"seq": self._seq, "t": time.time(), "type": type_, "text": text, **extra}
            self._seq += 1
            with self._events.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def events(self, since: int = 0) -> list[dict]:
        if not self._events.is_file():
            return []
        out = []
        for line in self._events.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev["seq"] >= since:
                out.append(ev)
        return out

    def describe(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.name,
            "params": self.params,
            "state": self.state,
            "summary": self.kind.summary(self.params),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "pause_flag": str(self.flag),
            "dir": str(self.dir),
            "pid": self.proc.pid if self.proc else None,
            "pausable": self.kind.pausable,
        }

    def _save(self) -> None:
        (self.dir / "job.json").write_text(json.dumps(self.describe(), ensure_ascii=False), encoding="utf-8")

    # ----------------------------------------------------------------- 进程
    def _strategy_file(self) -> Path | None:
        """2026-11-25 插件架构：HIRO_HOME/strategy.json + 本局 --plugins / --sources 合成一份放进任务目录；都没有时返回 None。"""
        from hiro import strategy

        base = strategy.read_file(self.work.parent / "strategy.json")
        over = strategy.parse_overrides(self.params.get("plugins"), self.params.get("sources"))
        if not base and len(over) == 1 and not over["_warnings"]:
            return None
        path = self.dir / "strategy.json"
        path.write_text(json.dumps(strategy.merge(base, over), ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def strategy_warnings(self) -> None:
        """开局时把插件配置的问题写进任务事件和日志（spec 4.2 / 4.7）：写错的键 / 页面、开局参数写错、配了但用不了的插件。"""
        from hiro import plugins, strategy

        base = strategy.read_file(self.work.parent / "strategy.json")
        over = strategy.parse_overrides(self.params.get("plugins"), self.params.get("sources"))
        cfg = strategy.from_dict(strategy.merge(base, over))
        texts = list(cfg.warnings)
        want = [("observation_source", i) for i in cfg.sources]
        want += [("exam_advisor", i) for i in sorted({v for v in cfg.exam.values() if v != plugins.BUILTIN})]
        want += [("produce_advisor", i) for i in sorted(set(cfg.produce.values()))]
        for kind, pid in want:
            if plugins.get_by_id(kind, pid) is None:
                why = next((x.error for x in plugins.available(kind) if x.id == pid), None) or "没装"
                texts.append(f"插件 {pid!r} 用不了（{why}），相关决策点用内置逻辑")
        for t in texts:
            self.emit("log", f"插件配置：{t}")

    def child_env(self) -> dict:
        env = os.environ.copy()
        from hiro.cli import paths

        paths.apply_defaults(env, self.work.parent)  # self.work = HIRO_HOME/work
        env.update(self.kind.env)
        if not env.get("HIRO_SETTLE_MIN_S"):
            # DMM 截图约 0.19 秒一张，考试里判「画面稳了」要一致读数持续 1.5 秒（同 scripts/play.ps1 -Device dmm；
            # 原来只有脚本开局才设，初星助手开的局没有，回合没切完就决策）
            from hiro.runtime.close_game import device_kind

            if device_kind(env) == "dmm":
                env["HIRO_SETTLE_MIN_S"] = "1.5"
        from hiro.cli import play_prefs

        # 设置页「培育」那一节（LIVE / 照片 / 未读剧情 / 没过线重打 / H.I.F 攻略包和审查日程）：盖过启动环境（11-27）
        for var, val in play_prefs.env_from_settings(self.work.parent).items():
            if val:
                env[var] = val
            else:
                env.pop(var, None)
        env.update(exam_search_env(self.kind.name, self.params.get("exam_search"), env))
        if self.kind.name == "hif":  # run_hif 的编成号走环境变量
            for key, var in (("support_set", "HIRO_SUPPORT_SET"), ("memory_set", "HIRO_MEMORY_SET")):
                if self.params.get(key):
                    env[var] = str(self.params[key])
        env.update(self._llm_env)
        if self._llm_off:
            env.pop("HIRO_LLM", None)
        env.update({"HIRO_PAUSE_FLAG": str(self.flag), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})
        # 模型用量流水账（hiro.usage.ledger）：写到 HIRO_HOME/usage，每行带任务编号（设计稿 §1）
        env.update({"HIRO_JOB_ID": self.id, "HIRO_HOME": str(self.work.parent)})
        env.setdefault("HIRO_CALIB_DIR", str(self.dir / "calib"))  # 排查截图跟着任务走（hiro.imgio.calib_root）
        if (sf := self._strategy_file()) is not None:
            env["HIRO_STRATEGY_FILE"] = str(sf)
        src = str(REPO / "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def start(self) -> None:
        env = self.child_env()
        argv = self.kind.argv(self.params, self.dir)
        kw = {}
        if (
            sys.platform == "win32"
        ):  # 服务没有控制台；不加 CREATE_NO_WINDOW 会给每个任务弹一个黑窗口，用户一关这局就没了
            kw["creationflags"] = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True  # 自成一个进程组：停止时连下一层子进程一起结束（终审 I1）
        self.emit("start", self.kind.summary(self.params), kind=self.kind.name)
        try:
            self.strategy_warnings()
        except Exception as e:  # noqa: BLE001  配置检查出错不影响开局
            self.emit("log", f"插件配置检查出错：{e}")
        self.proc = subprocess.Popen(
            argv,
            cwd=self.work,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kw,
        )
        self._save()
        self._tail_pos = 0
        self._frame_pos, self._frame_at, self._frame_pending = 0, 0.0, None
        self._tail_lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._tail_trace, daemon=True).start()

    def _read_steps(self) -> None:
        with self._tail_lock:  # 跟读线程和收尾都会调
            self._read_steps_locked()

    def _read_steps_locked(self) -> None:
        p = self.trace / "steps.jsonl"
        if not p.is_file():
            return
        with p.open("rb") as f:
            f.seek(self._tail_pos)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # 半行：下次再读
                self._tail_pos += len(raw)
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                ev = step_event(rec, self.trace)
                self.emit("step", ev.pop("text"), **ev)

    def _read_frames(self) -> None:
        """初 / N.I.A 存在任务目录 frames/ 的画面：最新一张发成 frame 事件（驾驶舱的实时画面，开局三步也有）。"""
        index = self.dir / "frames" / "frames.jsonl"
        self._frame_pos, rec = read_new_frames(index, self._frame_pos)
        if rec is not None and rec.get("file"):
            self._frame_pending = rec
        rec = self._frame_pending
        if rec is None or time.time() - self._frame_at < FRAME_EVERY:
            return
        path = index.parent / rec["file"]
        if path.is_file():
            self.emit("frame", "", frame=str(path), page=rec.get("page"))
            self._frame_at = time.time()
        self._frame_pending = None

    def _tail_trace(self) -> None:
        while self.proc and self.proc.poll() is None:
            self._read_steps()
            self._read_frames()
            time.sleep(TRACE_POLL)

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        tail: list[str] = []  # 出错原因从这里找（「停下：」/ ERROR 都在日志里）
        # 最后几行原样留着：句尾写「…，停下」的工具（G1-F2）和没接住的异常（traceback 尾行，游戏没开）不带上面的标记（#130 review）
        recent: deque[str] = deque(maxlen=20)
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if line:
                self.emit("log", line)
                recent.append(line)
                if line.startswith("停下：") or "[ERROR]" in line or "[CRITICAL]" in line:
                    tail = [*tail[-19:], line]
        code = self.proc.wait()
        center_dmm_window()
        self._read_steps()  # 最后几步
        self.flag.unlink(missing_ok=True)
        self.exit_code, self.ended_at = code, time.time()
        if self._on_exit:  # 扫描结果入库等收尾：在结束事件之前做完，跟事件流的客户端看到结束时信息库已经更新
            try:
                self._on_exit(self)
            except Exception as e:  # noqa: BLE001 收尾出错不影响任务状态
                self.emit("import_failed", f"收尾出错：{e}")
        # 先写结束事件、再改状态：客户端读到结束状态时，结束事件（含失败原因）一定已经在事件流里
        if self.state == "stopping":
            self.emit("stopped", "已停止")
            self.state = "stopped"
        elif code == 0:
            # 日常等任务最后打一行「结果：…」（每项做了什么）：结束事件就写它，界面上看得到（11-25：原来只有「完成」）
            result = next((t.strip()[3:] for t in reversed(recent) if t.strip().startswith("结果：")), None)
            self.emit("done", result or "完成")
            self.state = "done"
        else:
            self.reason = failure_reason([*tail, *recent])
            # 日常有一项出错时退出码是 1，但别的项（含已买 / 已抽的）做了什么照样要看得到：界面显示的是 reason，接在后面（#122 review）
            result = next((t.strip()[3:] for t in reversed(recent) if t.strip().startswith("结果：")), None)
            if result:  # 原因没认出来时也留着做了的（CodeRabbit #198）
                self.reason = f"{self.reason or f'任务出错退出（代码 {code}）'}。做了的：{result}"
            text = f"出错停下：{self.reason}" if self.reason else f"任务出错退出（代码 {code}），看上面的日志"
            self.emit("failed", text, exit_code=code, reason=self.reason)
            self.state = "failed"
        self._save()

    def pause(self, why: str, state: str) -> None:
        self.flag.write_text(f"{int(time.time())} {why}\n", encoding="utf-8")
        self.state = state
        self.emit(state, why)
        self._save()

    def resume(self) -> None:
        self.flag.unlink(missing_ok=True)
        self.state = "running"
        self.emit("running", "继续自动打")
        self._save()

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        self.state = "stopping"
        self.flag.unlink(missing_ok=True)
        # 结束整个进程组 / 进程树：一键全收集、开局前补扫多套了一层进程，只结束直接子进程时下一层还会接着点游戏（终审 I1）
        _signal_tree(self.proc, kill=False)
        try:
            self.proc.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            _signal_tree(self.proc, kill=True)


def _collect_all_summary(only: str | None) -> str:
    """一键全收集的标题：只列要做的几步（中文），不露出 --only 的英文参数（N39，G2 11-26：标题里写着
    「（只做 account,idols,supports,formations,memories）」）。"""
    from hiro.collect_all import STEPS

    want = {w.strip() for w in (only or "").split(",") if w.strip()}
    names = [("回忆（12 片全读）" if k == "memories" else label) for k, _, label in STEPS if not want or k in want]
    return "一键全收集：" + " → ".join(names or ["（没有要做的）"]) + "（只读，不改编成）"


# 开局页按场次选「快速 / 搜索」（11-27 用户）：--exam-search 的场次 → 各家族的搜索开关
EXAM_SEARCH_KEYS = {
    "hajime": ("mid1", "final", "lesson"),
    "nia": ("mid1", "mid2", "final"),
    "hif": ("q1", "q2", "q3", "r1", "r2"),
}
SIM_STATE_SOURCE = "hiro.sim.screen_source:SimScreenExamStateSource"


def exam_search_env(kind: str, value, base: dict | None = None) -> dict[str, str]:
    """--exam-search（mid1,final / none）→ 环境变量。没给这个参数时不动（各家族的缺省）。
    H.I.F 本战要搜索得有模拟器跟踪的局面来源：没设 HIRO_EXAM_STATE_SOURCE、也没 HIRO_SIM=0 时补上 play.ps1 的那套（模拟器 1 个进程、预演只在 R2）。"""
    if value is None or kind not in EXAM_SEARCH_KEYS:
        return {}
    keys = EXAM_SEARCH_KEYS[kind]
    picked = [k for k in (x.strip().lower() for x in str(value).split(",")) if k in keys]
    if kind == "hajime":
        return {"HIRO_EXAM_SEARCH_HAJIME": ",".join(picked) or "off"}
    if kind == "nia":
        return {"HIRO_EXAM_SEARCH_NIA": ",".join(picked) or "off"}
    qual = [k[1] for k in picked if k.startswith("q")]
    main = [k for k in picked if k.startswith("r")]
    env = {"HIRO_EXAM_SEARCH_HIF": ",".join(qual) or "off", "HIRO_EXAM_SEARCH_MAIN": ",".join(main) or "off"}
    if main and not (base or {}).get("HIRO_EXAM_STATE_SOURCE") and (base or {}).get("HIRO_SIM") != "0":
        env["HIRO_EXAM_STATE_SOURCE"] = SIM_STATE_SOURCE
        if not (base or {}).get("HIRO_SIM_WORKERS"):
            env["HIRO_SIM_WORKERS"] = "1"  # 模拟器每个 worker 约 600 MB
        if not (base or {}).get("HIRO_EXAM_SIM_ROUNDS"):
            env["HIRO_EXAM_SIM_ROUNDS"] = "r2"
    return env
