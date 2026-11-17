"""`hiro run …`：开局 / 扫描（交给 hiro 服务跑）、看状态和事件流、暂停 / 接手 / 继续 / 停止。"""

from __future__ import annotations

import json
import sys
import time

from hiro.cli import client
import os

from hiro.cli.jobs import KINDS, LIVE, LLM_VARS
from hiro.cli.registry import Arg, Risk, command

_TERMINAL_WAIT = 0.5


def _params(a: dict) -> dict:
    return {k: a[k] for k in ("command", "mode", "idol", "layers", "seconds", "no_llm") if a.get(k) not in (None, False)}


def _summary(a: dict) -> str:
    kind = KINDS.get(a["kind"])
    return kind.summary(_params(a)) if kind else f"开始任务 {a['kind']}"


@command("run start", Risk.ACT,
         "开一个任务（同一时间只能一个）：hif 培育、tower 偶像之路、daily 日常、scan-supports / scan-memories 扫描、selftest 自检。"
         "会动游戏，第一次返回待确认，用户同意后才开始。",
         args=(Arg("kind", "任务种类", choices=tuple(KINDS)),
               Arg("--command", "hif：new 新开 / resume 接着打进行中的培育", choices=("new", "resume")),
               Arg("--mode", "hif：qualify 选拔 / main 本战", choices=("qualify", "main")),
               Arg("--idol", "hif：偶像卡编号；tower：路线的偶像名（如 篠澤広）"),
               Arg("--layers", "tower：打几层", type="int"),
               Arg("--seconds", "selftest：跑几秒", type="int"),
               Arg("--no-llm", "决策只用规则和算法，不调用模型", type="bool")),
         confirm_summary=_summary)
def _start(a: dict) -> dict:
    # 对局用哪个模型：跟着发起开局的这个进程的环境（桌面程序按模型设置传；终端里就是自己 shell 的 HIRO_LLM）
    llm_env = {k: os.environ[k] for k in LLM_VARS if os.environ.get(k)}
    return client.data("start", {"kind": a["kind"], "params": _params(a), "llm_env": llm_env})


@command("run status", Risk.READ, "现在在跑什么任务（状态：running / paused / takeover / stopping），以及最近 10 个任务。")
def _status(a: dict) -> dict:
    return client.data("status")


@command("run events", Risk.READ, "读一个任务的事件（每一步的日志和结果）。--since 从第几条开始；--follow 一直跟到任务结束（每条事件一行 JSON）。",
         args=(Arg("--job", "任务编号（缺省：当前任务）"), Arg("--since", "从第几条事件开始", type="int", default=0),
               Arg("--follow", "一直跟到任务结束", type="bool", agent=False)))
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
