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

REPO = Path(hiro.__file__).resolve().parents[2]  # 仓库根（tools/ 下的脚本从这里找）
LIVE = ("running", "paused", "takeover")
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


_PY = sys.executable

_SELFTEST = r"""
import json, os, sys, time
flag = os.environ.get("HIRO_PAUSE_FLAG")
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
    if p.get("no_llm"):
        argv.append("--no-llm")
    return argv


def _hif_summary(p: dict) -> str:
    if (p.get("command") or "new") == "resume":
        return "接着打进行中的 H.I.F 培育"
    if (p.get("mode") or "qualify") == "main":
        return "开一局 H.I.F 本战（会用掉一份选拔回忆）"
    return "开一局 H.I.F 选拔" + (f"（{p['idol']}）" if p.get("idol") else "")


KINDS: dict[str, Kind] = {
    "selftest": Kind("selftest", "自检：只打印几行，不碰游戏", lambda p, d: [_PY, "-c", _SELFTEST, str(p.get("seconds") or 1), d.name],
                     lambda p: "跑一次自检（不碰游戏）"),
    "hif": Kind("hif", "H.I.F 培育：选拔 / 本战 / 续跑", _hif_argv, _hif_summary, env={"HIRO_RUNTIME": "own"}),
    "tower": Kind("tower", "偶像之路：按偶像路线往上爬",
                  lambda p, d: [_PY, "-m", "hiro.runtime.tower_play", "--idol-name", p.get("idol") or "",
                                "--layers", str(p.get("layers") or 1), "--trace", d.name]
                  + (["--no-llm"] if p.get("no_llm") else []),
                  lambda p: f"爬偶像之路（{p.get('idol') or '?'}，{p.get('layers') or 1} 层）", env={"HIRO_RUNTIME": "own"}),
    "daily": Kind("daily", "日常：礼物、活動費、任务奖励、社团", lambda p, d: [_PY, str(REPO / "tools" / "daily" / "run_daily.py")],
                  lambda p: "领日常奖励"),
    "scan-supports": Kind("scan-supports", "扫描支援卡（只读：在「選択」列表里滚动，最后点キャンセル）",
                          lambda p, d: [_PY, str(REPO / "tools" / "support_inventory.py"), "--list", "--out", str(d / "out.json")],
                          lambda p: "扫描支援卡库存（只读，不改编成）", output="supports"),
    "scan-memories": Kind("scan-memories", "扫描回忆（只读：在「メモリー選択」里逐格读）",
                          lambda p, d: [_PY, "-m", "hiro.memory_scan", "scan", "--out", str(d / "out.json")],
                          lambda p: "扫描回忆库存（只读，不改编成）", output="memories"),
}


def jobs_dir(home: Path) -> Path:
    d = home / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Job:
    def __init__(self, home: Path, kind: Kind, params: dict, on_exit: Callable[["Job"], None] | None = None):
        self.id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
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
                "exit_code": self.exit_code, "pause_flag": str(self.flag), "dir": str(self.dir)}

    def _save(self) -> None:
        (self.dir / "job.json").write_text(json.dumps(self.describe(), ensure_ascii=False), encoding="utf-8")

    # ----------------------------------------------------------------- 进程
    def start(self) -> None:
        env = os.environ.copy()
        env.update(self.kind.env)
        env.update({"HIRO_PAUSE_FLAG": str(self.flag), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})
        src = str(REPO / "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        argv = self.kind.argv(self.params, self.dir)
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
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
        if self.state == "stopping":
            self.state = "stopped"
            self.emit("stopped", "已停止")
        elif code == 0:
            self.state = "done"
            self.emit("done", "完成")
        else:
            self.state = "failed"
            self.emit("failed", f"任务出错退出（代码 {code}），看上面的日志", exit_code=code)
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
