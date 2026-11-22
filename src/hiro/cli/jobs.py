"""任务种类与任务进程（hiro 服务里跑）。

每个任务是一个子进程（argv 是列表，不走 shell），stdout / stderr 的每一行变成一条事件，结束时补一条 done / failed。
事件写 HIRO_HOME/jobs/<编号>/events.jsonl，每行 {"seq", "t", "type", "text", ...}。
暂停 / 接手：往这个任务的 HIRO_PAUSE_FLAG 旗标文件写一行（主循环在检查点停下，不再点击）；继续：删掉旗标。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import hiro
from hiro.cli.catalog import idol_name

REPO = Path(hiro.__file__).resolve().parents[2]  # 仓库根（tools/ 下的脚本从这里找）
LIVE = ("running", "paused", "takeover")
# 对局里用的 LLM（hiro.brain.llm.from_spec 读这些）：桌面程序按模型设置在确认开局时传过来；HIRO_LLM=off 表示只用规则和算法
LLM_VARS = ("HIRO_LLM", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HIRO_CODEX_BIN")
STOP_GRACE = 10.0
TRACE_POLL = 0.5


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
        "source": dec.get("source"),
        "decider": dec.get("decider"),
        "warnings": dec.get("warnings") or [],
        "notes": rec.get("notes") or [],
        "frame": str(frame) if frame is not None and frame.is_file() else None,
        "text": dec.get("reason") or "",
    }


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
HOME_READER = "hiro.vision.home_values"  # 首页顶栏读数（scan-account）

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
    argv = [_PY, "-m", "hiro.run_hif", p.get("command") or "new", "--mode", p.get("mode") or "qualify", "--trace", d.name]
    if p.get("idol"):
        argv += ["--idol", p["idol"]]
    if p.get("main_attr"):
        argv += ["--main-attr", p["main_attr"]]
    if p.get("no_llm"):
        argv.append("--no-llm")
    return argv


DAILY_ITEMS = ("activity_funds", "presents", "assignment", "contest", "mission_reward", "club_reward")
DAILY_OWN = ("activity_funds", "presents", "mission_reward", "club_reward")  # 已迁到 hiro.daily 的（不经 KAA）
DAILY_LABEL = {"activity_funds": "活動費", "presents": "礼物", "assignment": "工作", "contest": "竞赛",
               "mission_reward": "任务奖励", "club_reward": "社团"}
HAJIME_LEVELS = ("regular", "pro", "master")
NIA_LEVELS = ("pro", "master")


def _daily_argv(p: dict, d: Path) -> list[str]:
    argv = [_PY, str(REPO / "tools" / "daily" / "run_daily.py")]
    items = [i for i in (p.get("items") or "").split(",") if i]
    if items:
        argv += ["--only", ",".join(items)]
        if all(i in DAILY_OWN for i in items):
            argv.append("--own")
    return argv


def _daily_summary(p: dict) -> str:
    items = [i for i in (p.get("items") or "").split(",") if i]
    return "领日常奖励" + (f"（{'、'.join(DAILY_LABEL.get(i, i) for i in items)}）" if items else "")


def _produce_argv(module: str, default_level: str):
    """初（hiro.runtime.hajime）/ N.I.A（hiro.runtime.nia_sample）：新主循环，编成号走参数，存帧放任务目录。"""

    def build(p: dict, d: Path) -> list[str]:
        argv = [_PY, "-m", module, p.get("command") or "new", "--idol", p.get("idol") or "",
                "--difficulty", p.get("difficulty") or default_level, "--trace", d.name, "--out", str(d / "frames")]
        for key, flag in (("main_attr", "--main-attr"), ("support_set", "--support-set"), ("memory_set", "--memory-set")):
            if p.get(key):
                argv += [flag, str(p[key])]
        if p.get("no_llm"):
            argv.append("--no-llm")
        return argv

    return build


def _produce_summary(name: str, default_level: str):
    def summary(p: dict) -> str:
        level = (p.get("difficulty") or default_level).upper()
        if (p.get("command") or "new") == "resume":
            return f"接着打进行中的{name}培育"
        return f"开一局{name} {level}" + (f"（{idol_name(p['idol'])}）" if p.get("idol") else "")

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
        return "接着打进行中的 H.I.F 培育"
    if (p.get("mode") or "qualify") == "main":
        return "开一局 H.I.F 本战（会用掉一份选拔回忆）"
    return "开一局 H.I.F 选拔" + (f"（{idol_name(p['idol'])}）" if p.get("idol") else "")


KINDS: dict[str, Kind] = {
    "selftest": Kind("selftest", "自检：只打印几行，不碰游戏", lambda p, d: [_PY, "-c", _SELFTEST, str(p.get("seconds") or 1), d.name],
                     lambda p: "跑一次自检（不碰游戏）"),
    "hif": Kind("hif", "H.I.F 培育：选拔 / 本战 / 续跑", _hif_argv, _hif_summary, env={"HIRO_RUNTIME": "own"}),
    "tower": Kind("tower", "偶像之路：按偶像路线往上爬",
                  lambda p, d: [_PY, "-m", "hiro.runtime.tower_play", "--idol-name", p.get("idol") or "",
                                "--layers", str(p.get("layers") or 1), "--trace", d.name]
                  + (["--no-llm"] if p.get("no_llm") else []),
                  lambda p: f"爬偶像之路（{p.get('idol') or '?'}，{p.get('layers') or 1} 层）", env={"HIRO_RUNTIME": "own"}),
    "hajime": Kind("hajime", "「初」培育（レギュラー / プロ / マスター）", _produce_argv("hiro.runtime.hajime", "regular"),
                   _produce_summary("「初」", "regular"), env={"HIRO_RUNTIME": "own"}),
    "nia": Kind("nia", "N.I.A 培育（プロ / マスター；认不出的页会停下交给你）", _produce_argv("hiro.runtime.nia_sample", "pro"),
                _produce_summary(" N.I.A", "pro"), env={"HIRO_RUNTIME": "own"}),
    "daily": Kind("daily", "日常：活動費、礼物、工作、竞赛、任务奖励、社团（可只选几项）", _daily_argv, _daily_summary, pausable=False),
    "write-formation": Kind("write-formation", "把开局编成方案写进游戏的编成位，写完回读核对（--dry-run 只报差在哪几格，不改）",
                            _write_formation_argv, _write_formation_summary, pausable=False),
    "check-settings": Kind("check-settings", "检查游戏设置里快进 / 跳过演出那几项开没开（只读：打开菜单看完回首页，不点开关）",
                           lambda p, d: [_PY, str(REPO / "tools" / "check_settings.py"), "--json", str(d / "out.json")],
                           lambda p: "检查游戏设置（只读，不改开关）", output="game_settings", pausable=False),
    "fit-window": Kind("fit-window", "Windows DMM：把游戏窗口调成适合识别的大小（9:16，按显示器自适应）",
                       lambda p, d: [_PY, "-m", "hiro.devices.win_window", "fit"],
                       lambda p: "调整 DMM 游戏窗口大小", pausable=False),
    "scan-account": Kind("scan-account", "读首页顶栏的 PLv、AP、ジュエル（只截一张图，不点）",
                         lambda p, d: [_PY, "-m", HOME_READER, "--live", "--out", str(d / "out.json")],
                         lambda p: "读首页的 PLv / AP / ジュエル（只截图，不点）", output="account", pausable=False),
    "scan-idols": Kind("scan-idols", "扫描偶像卡（只读：偶像一览里逐张点开读卡名，最后点「<<」退出，不点決定）",
                       lambda p, d: [_PY, "-m", "hiro.produce.idol_scan", "--out", str(d / "out.json")],
                       lambda p: "扫描偶像卡（只读，不改选中的偶像）", output="idols", pausable=False),
    "scan-supports": Kind("scan-supports", "扫描支援卡（只读：在「選択」列表里滚动，最后点キャンセル）",
                          lambda p, d: [_PY, str(REPO / "tools" / "support_inventory.py"), "--list", "--from-home", "--out", str(d / "out.json")],
                          lambda p: "扫描支援卡库存（只读，不改编成）", output="supports", pausable=False),
    "scan-memories": Kind("scan-memories", "扫描回忆（只读：在「メモリー選択」里逐格读）",
                          lambda p, d: [_PY, "-m", "hiro.memory_scan", "scan", "--from-home", "--out", str(d / "out.json")],
                          lambda p: "扫描回忆库存（只读，不改编成）", output="memories", pausable=False),
    "scan-formations": Kind("scan-formations", "读编成位现状（只读：编成菜单里翻 1–20 号、读图，不点格子）",
                            lambda p, d: [_PY, "-m", "hiro.produce.formation_survey", "--mode", p.get("scenario") or "hif", "--out", str(d / "out.json")],
                            lambda p: f"读 {({'hif': 'H.I.F', 'hajime': '初', 'nia': 'N.I.A'}).get(p.get('scenario') or 'hif')} 的编成位现状（只读，不改编成）",
                            output="formations", pausable=False),
}


def jobs_dir(home: Path) -> Path:
    d = home / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Job:
    def __init__(self, home: Path, kind: Kind, params: dict, on_exit: Callable[["Job"], None] | None = None,
                 llm_env: dict | None = None):
        self.id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        llm = {k: str(v) for k, v in (llm_env or {}).items() if k in LLM_VARS and v}
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
        self.trace = self.work / "traces" / self.id  # hif / tower / selftest 用 --trace <任务编号>
        self.state = "running"
        self.started_at, self.ended_at, self.exit_code = time.time(), None, None
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
        return {"id": self.id, "kind": self.kind.name, "params": self.params, "state": self.state,
                "summary": self.kind.summary(self.params), "started_at": self.started_at, "ended_at": self.ended_at,
                "exit_code": self.exit_code, "pause_flag": str(self.flag), "dir": str(self.dir),
                "pid": self.proc.pid if self.proc else None, "pausable": self.kind.pausable}

    def _save(self) -> None:
        (self.dir / "job.json").write_text(json.dumps(self.describe(), ensure_ascii=False), encoding="utf-8")

    # ----------------------------------------------------------------- 进程
    def child_env(self) -> dict:
        env = os.environ.copy()
        from hiro.cli import paths

        paths.apply_defaults(env, self.work.parent)  # self.work = HIRO_HOME/work
        env.update(self.kind.env)
        if self.kind.name == "hif":  # run_hif 的编成号走环境变量
            for key, var in (("support_set", "HIRO_SUPPORT_SET"), ("memory_set", "HIRO_MEMORY_SET")):
                if self.params.get(key):
                    env[var] = str(self.params[key])
        env.update(self._llm_env)
        if self._llm_off:
            env.pop("HIRO_LLM", None)
        env.update({"HIRO_PAUSE_FLAG": str(self.flag), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})
        src = str(REPO / "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def start(self) -> None:
        env = self.child_env()
        argv = self.kind.argv(self.params, self.dir)
        kw = {}
        if sys.platform == "win32":  # 服务没有控制台；不加 CREATE_NO_WINDOW 会给每个任务弹一个黑窗口，用户一关这局就没了
            kw["creationflags"] = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        self.emit("start", self.kind.summary(self.params), kind=self.kind.name)
        self.proc = subprocess.Popen(argv, cwd=self.work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **kw)
        self._save()
        self._tail_pos = 0
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

    def _tail_trace(self) -> None:
        while self.proc and self.proc.poll() is None:
            self._read_steps()
            time.sleep(TRACE_POLL)

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if line:
                self.emit("log", line)
        code = self.proc.wait()
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
            self.emit("done", "完成")
            self.state = "done"
        else:
            self.emit("failed", f"任务出错退出（代码 {code}），看上面的日志", exit_code=code)
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
        self.proc.terminate()
        try:
            self.proc.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            self.proc.kill()
