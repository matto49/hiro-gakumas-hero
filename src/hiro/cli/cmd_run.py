"""`hiro run …`：开局 / 扫描（交给 hiro 服务跑）、看状态和事件流、暂停 / 接手 / 继续 / 停止。"""

from __future__ import annotations

import json
import os
import sys
import time

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
    del sys


def _summary(a: dict) -> str:
    kind = KINDS.get(a["kind"])
    return kind.summary(_params(a)) if kind else f"开始任务 {a['kind']}"


@command(
    "run start",
    Risk.ACT,
    "开一个任务（同一时间只能一个）：hif H.I.F 培育、hajime「初」培育、nia N.I.A 培育、tower 偶像之路、daily 日常、scan-supports / scan-memories 扫描、selftest 自检。"
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
            "--items",
            "daily：只领这几项（逗号分隔：activity_funds、presents、assignment、contest、mission_reward、club_reward）",
        ),
        Arg("--layers", "tower：打几层", type="int"),
        Arg("--seconds", "selftest：跑几秒", type="int"),
        Arg("--no-llm", "决策只用规则和算法，不调用模型", type="bool"),
        Arg("--scenario", "write-formation：写哪个模式的编成", choices=("hif", "hajime", "nia")),
        Arg("--what", "write-formation：写哪些（support / memory，逗号分隔，缺省都写）"),
        Arg("--dry-run", "write-formation：只看差在哪几格，不改", type="bool"),
    ),
    confirm_summary=_summary,
    precheck=_precheck,
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
