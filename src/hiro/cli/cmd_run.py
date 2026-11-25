"""`hiro run …`：开局 / 扫描（交给 hiro 服务跑）、看状态和事件流、暂停 / 接手 / 继续 / 停止。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from hiro.cli import client
from hiro.cli.jobs import KINDS, LIVE, LLM_VARS
from hiro.cli.registry import Arg, CliError, Risk, command

_TERMINAL_WAIT = 0.5


PARAMS = (
    "command",
    "mode",
    "idol",
    "layers",
    "seconds",
    "no_llm",
    "scenario",
    "what",
    "dry_run",
    "main_attr",
    "support_set",
    "memory_set",
    "difficulty",
    "items",
    "chapter",
    "until",
    "final_rank",
    "plugins",
    "sources",
)


def _params(a: dict) -> dict:
    return {k: a[k] for k in PARAMS if a.get(k) not in (None, False)}


def _precheck(a: dict) -> None:
    import sys

    from hiro.cli import jobs
    from hiro.cli.registry import CliError

    if a["kind"] == "fit-window" and jobs.sys.platform != "win32":
        raise CliError("windows_only", "调窗口大小只用于 Windows DMM 版", "Mac 上 PlayCover 的窗口不用调")
    if a["kind"] == "write-formation":
        from hiro.cli import paths
        from hiro.cli.home import hiro_home

        if not paths.plan_path(hiro_home()).is_file():
            raise CliError("no_plan", "还没有编成方案，没东西可写", "先出一份：hiro plan formation --idol <偶像卡编号>")
        if not a.get("dry_run") and "memory" in (a.get("what") or "support,memory"):
            _check_memory_freshness(paths.plan_path(hiro_home()), hiro_home())
    if a["kind"] in ("hajime", "nia") and (a.get("command") or "new") == "new" and not a.get("idol"):
        raise CliError(
            "bad_args", "开新的一局要指定偶像卡", "加 --idol <偶像卡编号>，例如 i_card-skin-shro-3-007", exit_code=2
        )
    if a["kind"] == "daily" and a.get("items"):
        bad = [i for i in a["items"].split(",") if i and i not in jobs.DAILY_ITEMS]
        if bad:
            raise CliError(
                "bad_args", f"日常里没有这些项：{'、'.join(bad)}", "可选：" + "、".join(jobs.DAILY_ITEMS), exit_code=2
            )
    if a["kind"] == "nia" and a.get("difficulty") == "regular":
        raise CliError("bad_args", "N.I.A 没有 REGULAR，只有 pro / master", "改成 --difficulty pro", exit_code=2)
    _check_formation_plan(a)
    del sys


FORMATION_KINDS = ("hif", "hajime", "nia")  # 开局要走编成步骤（第 2、3 步）的模式


def _check_memory_freshness(plan_path: Path, home: Path) -> None:
    """写回忆编成靠方案里记的库存位置（第几行第几列，按库存扫描时数的）：
    - 回忆扫描之后又打完过培育：每局多一份回忆，列表位置变了（Mac 11-24 16:44：11-23 11:22 扫的库存，
      20:33 那局「初」之后再写，翻了 28 格没找到才停）；
    - 方案比最近一次回忆扫描还早：方案里的位置是旧库存的。
    两种都在写之前说清楚，不进游戏。没有回忆扫描记录（导入的库存）时不判断。"""
    from datetime import datetime

    from hiro.cli.jobs import jobs_dir
    from hiro.cli.store import Store

    db = home / "hiro.db"
    if not db.is_file():
        return
    history = Store(db).history("memories")
    if not history:
        return
    scanned = history[0]["scanned_at"]
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        scanned_ts = datetime.strptime(scanned, "%Y-%m-%d %H:%M:%S").timestamp()
    except (OSError, ValueError):
        return
    idol = (plan.get("idol") or {}).get("idol_card_id") or "<偶像卡编号>"
    replan = f"重新出方案：hiro plan formation --idol {idol}；再写编成"
    played = []
    for p in jobs_dir(home).glob("*/job.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if j.get("kind") in FORMATION_KINDS and j.get("state") == "done" and (j.get("ended_at") or 0) > scanned_ts:
            played.append(j)
    if played:
        raise CliError(
            "inventory_stale",
            f"回忆库存是 {scanned} 扫的，之后又打完了 {len(played)} 局培育（每局会多一份回忆，列表位置变了）",
            f"先重扫回忆：hiro run start scan-memories；{replan}",
        )
    if (plan.get("created") or "") < scanned:
        raise CliError(
            "plan_stale",
            f"编成方案是 {plan.get('created') or '?'} 出的，之后回忆库存重扫过（{scanned}），方案里记的位置是旧的",
            replan,
        )


def _check_formation_plan(a: dict) -> None:
    """新开一局（H.I.F 只算选拔）又没指定编成号时，要有给这位偶像出的开局编成方案，否则任务进了游戏流程才报
    「开局要编成号」退出（Mac 11-24：方案是给月村手毬的，H.I.F、藤田ことね的「初」都这样白开了一次）。"""
    from hiro.cli import paths
    from hiro.cli.home import hiro_home

    kind = a["kind"]
    if kind not in FORMATION_KINDS or (a.get("command") or "new") != "new":
        return
    if kind == "hif" and (a.get("mode") or "qualify") == "main":
        return  # 本战不走编成步骤，用的是选拔得到的回忆
    if (
        a.get("support_set")
        or a.get("memory_set")
        or os.environ.get("HIRO_SUPPORT_SET")
        or os.environ.get("HIRO_MEMORY_SET")
    ):
        return
    env_plan = os.environ.get("HIRO_FORMATION_PLAN")
    if env_plan == "off":
        return
    path = Path(env_plan) if env_plan else paths.plan_path(hiro_home())
    idol = (a.get("idol") or "").replace("i_card-skin-", "i_card-")
    hint = (
        f"先给这位偶像出编成方案：hiro plan formation --idol {idol or '<偶像卡编号>'}；"
        f"再写进游戏：hiro run start write-formation --scenario {kind}；然后再开局。"
        "也可以用 --support-set / --memory-set 指定游戏里已经编好的编成号"
    )
    if not path.is_file():
        raise CliError("no_plan", "还没有开局编成方案，不知道这局用哪套支援卡和回忆", hint)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("no_plan", "开局编成方案读不出来", hint) from None
    want = (plan.get("idol") or {}).get("idol_card_id")
    if idol and want and idol != want:
        name = (plan.get("idol") or {}).get("name") or want
        raise CliError("plan_other_idol", f"现在的编成方案是给「{name}」出的，这局是 {idol}", hint)


EXAM_KINDS = ("hif", "hajime", "nia", "tower")  # 有考试、出牌靠算法的任务


def run_access(a: dict) -> str:
    """操作类别（11-25 review：权限档参考 dsh）：read 只看画面；daily 领日常（用户说不用确认）；
    blank 写空白编成位（用户说不用确认）；write 改游戏（コミュ、竞赛编队）；overwrite 会覆盖已有编成；all 用掉培育次数 / 资源（开局、续打、初星課題）。"""
    kind = a["kind"]
    if a.get("command") == "resume" or kind in EXAM_KINDS or kind == "main-task":
        return "all"
    if kind.startswith("scan-") or kind in ("check-settings", "selftest"):
        return "read"
    if kind == "daily":
        return "daily"
    if kind in ("write-formation", "contest-unit") and a.get("dry_run"):
        return "read"
    if kind == "write-formation":
        return "blank" if _formation_slots_blank(a) else "overwrite"
    return "write"


def _formation_slots_blank(a: dict) -> bool:
    """要写的编成位（方案里的支援卡 / 回忆编成号）在最近一次读的编成位现状里都是空的。读不到、读的是别的模式都按不空算。"""
    from hiro.cli import paths
    from hiro.cli.home import hiro_home
    from hiro.cli.store import open_store

    try:
        plan = json.loads(paths.plan_path(hiro_home()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    mode = a.get("scenario") or "hif"
    what = [w.strip() for w in (a.get("what") or "support,memory").split(",") if w.strip()]
    want = {(w, (plan.get(w) or {}).get("set")) for w in what}
    if any(n is None for _, n in want):
        return False
    meta, rows = open_store().latest("formations")
    if meta is None or _wrote_formation_since(meta.get("scanned_at"), mode):
        return False  # 读完以后写过编成：读到的「空」已经不准（CodeRabbit #79）
    filled = {(r.get("kind"), r.get("set")): r.get("filled") for r in rows if r.get("mode") == mode}
    return all(filled.get(k) == 0 for k in want)


def _wrote_formation_since(scanned_at: str | None, mode: str) -> bool:
    """编成位现状读完之后，有没有开过写这个模式编成的任务（对照不算；没跑完的也算，可能写了一半）。"""
    from hiro.cli.home import hiro_home
    from hiro.cli.jobs import jobs_dir

    try:
        t0 = time.mktime(time.strptime(scanned_at or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    for f in jobs_dir(hiro_home()).glob("*/job.json"):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        p = j.get("params") or {}
        if j.get("kind") != "write-formation" or p.get("dry_run") or (p.get("scenario") or "hif") != mode:
            continue
        if (j.get("started_at") or 0) >= t0:
            return True
    return False


def _summary(a: dict) -> str:
    kind = KINDS.get(a["kind"])
    text = kind.summary(_params(a)) if kind else f"开始任务 {a['kind']}"
    if a["kind"] in EXAM_KINDS and (why := exam_algo_problem()):
        text += f"\n⚠ 考试出牌只能用规则打，分数会明显低：出牌算法没加载上（{why}）"
    return text


_exam_algo_checked: list = []


def exam_algo_problem() -> str | None:
    """任务环境里考试出牌算法（hiro.bench_bridge，缺省 uinet）能不能加载；不能时返回一句原因。
    在任务同样的环境里跑一次 `python -m hiro.bench_bridge check`（不下载），同一进程只查一次。
    Mac 11-24 18:27：测试目录缺主数据，uinet 没加载上，整局考试退回规则，只有日志里一行 WARNING。"""
    if _exam_algo_checked:
        return _exam_algo_checked[0]
    import subprocess

    from hiro.cli import paths
    from hiro.cli.home import hiro_home
    from hiro.cli.jobs import REPO

    env = paths.apply_defaults(os.environ.copy(), hiro_home())
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run(
            [sys.executable, "-m", "hiro.bench_bridge", "check"],
            env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        why = None if r.returncode == 0 else _first_reason(r.stdout + r.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        why = f"检查没跑完：{type(e).__name__}"
    _exam_algo_checked.append(why)
    return why


def _first_reason(out: str) -> str:
    """bench_bridge check 的输出里挑写给人看的那句（「缺 Python 模块 …」/「下载失败 …」），没有就第一行。"""
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    pick = next((x for x in lines if x.startswith(("缺 ", "下载失败", "模型校验"))), lines[0] if lines else "原因不明")
    return pick[:160]


@command(
    "run start",
    Risk.ACT,
    "开一个任务（同一时间只能一个）：hif H.I.F 培育、hajime「初」培育、nia N.I.A 培育、tower 偶像之路、daily 日常、main-task 初星課題（关完成弹窗、读 NEXT）、story 看初星コミュ（逐话再生并跳过）、contest-unit 竞赛编队（おまかせ重排、读总合力）、scan-supports / scan-memories 扫描、selftest 自检。"
    "会动游戏，第一次返回待确认，用户同意后才开始。",
    args=(
        Arg("kind", "任务种类", choices=tuple(KINDS)),
        Arg("--command", "hif：new 新开 / resume 接着打进行中的培育", choices=("new", "resume")),
        Arg("--mode", "hif：qualify 选拔 / main 本战", choices=("qualify", "main")),
        Arg("--idol", "hif / hajime / nia：偶像卡编号（如 i_card-skin-shro-3-007）；tower：路线的偶像名（如 篠澤広）"),
        Arg("--main-attr", "hif / hajime / nia：主属性（不给按编成方案）", choices=("Vo", "Da", "Vi")),
        Arg("--support-set", "hif / hajime / nia：用游戏里第几号支援卡编成（不给按编成方案）", type="int"),
        Arg("--memory-set", "hif / hajime / nia：用第几号回忆编成", type="int"),
        Arg("--difficulty", "hajime：regular / pro / master；nia：pro / master", choices=("regular", "pro", "master")),
        Arg(
            "--final-rank",
            "hajime：期末要到第几名（如 1），没到就用再挑戦（免费 / チケット，不花ジュエル）",
            type="int",
        ),
        Arg(
            "--items",
            "daily：只领这几项（逗号分隔：activity_funds、presents、assignment、contest、mission_reward、club_reward）",
        ),
        Arg("--layers", "tower：打几层", type="int"),
        Arg("--chapter", "story：初星コミュ第几章（缺省 1）", type="int"),
        Arg("--until", "story：看到第几话为止（缺省看完所有解锁的新话）", type="int"),
        Arg("--seconds", "selftest：跑几秒", type="int"),
        Arg("--no-llm", "决策只用规则和算法，不调用模型", type="bool"),
        Arg("--scenario", "write-formation：写哪个模式的编成", choices=("hif", "hajime", "nia")),
        Arg("--what", "write-formation：写哪些（support / memory，逗号分隔，缺省都写）"),
        Arg("--dry-run", "write-formation：只看差在哪几格，不改；contest-unit：只读总合力，不重排", type="bool"),
        Arg(
            "--plugins",
            "这一局换用的插件（覆盖 HIRO_HOME/strategy.json）：决策点=插件 id，逗号分隔，如 produce.ACTION_SELECT=uinet,exam=gnrpa；查已装插件用 hiro plugins list",
        ),
        Arg("--sources", "这一局用的数据来源插件 id，逗号分隔（覆盖 strategy.json 的 sources）"),
    ),
    confirm_summary=_summary,
    precheck=_precheck,
    access=run_access,
)
def _start(a: dict) -> dict:
    # 对局用哪个模型：跟着发起开局的这个进程的环境（桌面程序按模型设置传；终端里就是自己 shell 的 HIRO_LLM）
    llm_env = {k: os.environ[k] for k in LLM_VARS if os.environ.get(k)}
    return client.data("start", {"kind": a["kind"], "params": _params(a), "llm_env": llm_env})


@command(
    "run status", Risk.READ, "现在在跑什么任务（状态：running / paused / takeover / stopping），以及最近 10 个任务。"
)
def _status(a: dict) -> dict:
    return client.data("status")


@command(
    "run events",
    Risk.READ,
    "读一个任务的事件（每一步的日志和结果）。--since 从第几条开始；--follow 一直跟到任务结束（每条事件一行 JSON）。",
    args=(
        Arg("--job", "任务编号（缺省：当前任务）"),
        Arg("--since", "从第几条事件开始", type="int", default=0),
        Arg("--follow", "一直跟到任务结束", type="bool", agent=False),
    ),
)
def _events(a: dict) -> dict:
    body = {"job": a["job"], "since": a["since"]}
    if not a["follow"]:
        return client.data("events", body)
    since, out = a["since"], None
    while True:
        out = client.data("events", {**body, "since": since})
        for ev in out["events"]:
            sys.stdout.write(json.dumps({"event": ev}, ensure_ascii=False) + "\n")
            since = ev["seq"] + 1
        sys.stdout.flush()
        body["job"] = out["job"]
        if out["state"] not in LIVE + ("stopping",):
            return {"job": out["job"], "state": out["state"], "next": since}
        time.sleep(_TERMINAL_WAIT)


def _control(action: str, help_: str):
    @command(f"run {action}", Risk.READ, help_, args=(Arg("--job", "任务编号（缺省：当前任务）"),))
    def _f(a: dict) -> dict:
        return client.data(action, {"job": a["job"]})

    return _f


_control("pause", "暂停当前任务：主循环停在下一个检查点，不再点击。")
_control("takeover", "用户接手：自动操作停下，用户在游戏里手动点；之后 hiro run resume 从当前画面接着打。")
_control("resume", "继续：从当前画面重新判断在哪一页，接着自动打。")
_control("stop", "停止当前任务（不会放弃培育：游戏停在原地，之后可以 resume 续跑）。")


@command(
    "run report",
    Risk.READ,
    "一局的成绩摘要：步数、决策来源（规则 / 算法 / LLM / 兜底）、LLM 次数、故障恢复次数、每场考试分数。",
    args=(Arg("--job", "任务编号", required=True),),
)
def _report(a: dict) -> dict:
    from hiro.cli import report

    return report.summarize(report.check_job(a["job"]))


@command(
    "run review",
    Risk.READ,
    "生成一局的复盘页（逐回合出牌、候选、状态曲线，带截图），返回本机文件路径。只给有考试的对局。",
    args=(Arg("--job", "任务编号", required=True),),
)
def _review(a: dict) -> dict:
    from hiro.cli import report

    jid = report.check_job(a["job"])
    if not report.segments(report.traces_dir(), jid):
        raise CliError("no_trace", f"任务 {jid} 没有对局记录", "只有对局才有复盘页")
    html = report.build_review_html(jid, report.traces_dir())
    from hiro.cli.home import ensure_home

    out = ensure_home() / "jobs" / jid / "review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"job": jid, "path": str(out), "url": f"review/{jid}.html"}
