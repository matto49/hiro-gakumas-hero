"""hiro 服务：常驻后台进程，跑对局和扫描（同一时间只跑一个）。命令行和桌面程序都是它的客户端。

- 只监听 127.0.0.1，端口随机；HIRO_HOME/service.json 记 {port, token, pid}（权限 600），请求头 X-Hiro-Token 要对得上。
- 接口都是 POST + JSON：/ping /status /start /events /pause /takeover /resume /stop /shutdown。
- 服务重启时，上次没结束的任务标成 lost（子进程跟着服务退了，游戏停在原地，可以 resume 续跑）。

    python -m hiro.cli.service     # 一般不用手动起：命令行发现服务没在跑会自己拉起
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.jobs import KINDS, LIVE, Job, jobs_dir

RECENT = 10
PRODUCE_KINDS = ("hif", "hajime", "nia", "tower")


def _is_our_python(pid: int) -> bool:
    """pid 还活着，而且是一个 python 进程（防 pid 被别的程序复用时误杀）。"""
    import subprocess

    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10
            ).stdout
        else:
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10
            ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "python" in out.lower()


def _kill_orphan(pid) -> None:
    import signal
    import time

    if not isinstance(pid, int) or pid <= 0 or not _is_our_python(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5 and _is_our_python(pid):
        time.sleep(0.1)
    if sys.platform != "win32" and _is_our_python(pid):
        try:  # noqa: SIM105
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class Manager:
    def __init__(self, home: Path):
        self.home = home
        self.lock = threading.Lock()
        self.current: Job | None = None
        self.jobs: dict[str, Job] = {}
        self._mark_lost()

    def _mark_lost(self) -> None:
        for p in jobs_dir(self.home).glob("*/job.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("state") in LIVE + ("stopping",):
                _kill_orphan(rec.get("pid"))  # 上一个服务的子进程还在点游戏：先停掉，免得两个主循环同时操作
                rec["state"] = "lost"
                p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                (p.parent / "pause.flag").unlink(missing_ok=True)

    def _recent(self) -> list[dict]:
        recs = []
        for p in sorted(jobs_dir(self.home).glob("*/job.json"), reverse=True)[:RECENT]:
            job = self.jobs.get(p.parent.name)
            if job:
                recs.append(job.describe())
                continue
            try:
                recs.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return recs

    def status(self) -> dict:
        cur = self.current if self.current and self.current.state in LIVE + ("stopping",) else None
        return {"current": cur.describe() if cur else None, "recent": self._recent()}

    def start(self, kind: str, params: dict, llm_env: dict | None = None) -> dict:
        if kind not in KINDS:
            return _err("bad_args", f"没有这种任务：{kind}", "可选：" + "、".join(KINDS))
        with self.lock:
            cur = self.current
            if cur and cur.state in LIVE + ("stopping",):
                return _err(
                    "busy",
                    f"已经有任务在跑：{cur.kind.summary(cur.params)}",
                    "同一时间只能跑一个对局或扫描；等它结束，或 hiro run stop",
                )
            job = Job(self.home, KINDS[kind], params, on_exit=self._exited, llm_env=llm_env)
            self.jobs[job.id] = job
            self.current = job
            try:
                job.start()
            except OSError as e:
                job.state = "failed"
                job.emit("failed", f"任务没能启动：{e}")
                self.current = None
                return _err("start_failed", f"任务没能启动：{e}", "运行 hiro doctor 检查环境")
        return {"ok": True, "data": {"job": job.describe()}}

    def _exited(self, job: Job) -> None:
        if job.kind.name in PRODUCE_KINDS:  # 对局结束：成绩摘要（在结束事件之前）
            try:
                from hiro.cli import report

                segs = report.segments(report.traces_dir(), job.id)
                if segs:
                    exams = report.exam_scores(job.id, report.traces_dir())
                    line = report.score_line(exams)
                    if line:
                        job.emit("report", line, exams=exams)
            except Exception as e:  # noqa: BLE001 成绩摘要出错不影响任务
                job.emit("log", f"成绩摘要没算出来：{e}")
        if job.kind.output:
            try:
                from hiro.cli.scan_ingest import ingest

                ingest(job)
            except ImportError:
                pass

    def _job(self, jid: str | None) -> Job | None:
        if jid:
            return self.jobs.get(jid)
        return self.current

    def events(self, jid: str | None, since: int) -> dict:
        job = self._job(jid)
        if job is None and jid:  # 服务重启前的任务：直接读文件
            p = jobs_dir(self.home) / Path(jid).name / "events.jsonl"
            rec_p = p.parent / "job.json"
            if not p.is_file():
                return _err("not_found", f"没有这个任务：{jid}", "hiro run status 看最近的任务")
            evs = [e for e in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if e["seq"] >= since]
            state = json.loads(rec_p.read_text(encoding="utf-8")).get("state") if rec_p.is_file() else None
            return {"ok": True, "data": {"job": jid, "state": state, "events": evs}}
        if job is None:
            return _err("no_job", "现在没有任务", "hiro run start 开一个")
        return {"ok": True, "data": {"job": job.id, "state": job.state, "events": job.events(since)}}

    def control(self, action: str, jid: str | None) -> dict:
        job = self._job(jid)
        if job is None or job.state not in LIVE:
            return _err("no_job", "现在没有在跑的任务", "hiro run status 看状态")
        if action in ("pause", "takeover") and not job.kind.pausable:
            return _err(
                "not_pausable",
                f"「{job.kind.summary(job.params)}」不能暂停或接手，只能停止",
                "要自己操作就先 hiro run stop（不会放弃培育）",
            )
        if action == "pause":
            job.pause("用户暂停", "paused")
        elif action == "takeover":
            job.pause("用户接手：自动操作停在检查点，不再点击", "takeover")
        elif action == "resume":
            job.resume()
        elif action == "stop":
            threading.Thread(target=job.stop, daemon=True).start()
            job.state = "stopping"
        return {"ok": True, "data": {"job": job.describe()}}


def _err(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def make_server(home: Path) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_hex(16)
    mgr = Manager(home)

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if not hmac.compare_digest(self.headers.get("X-Hiro-Token", ""), token):
                return self._send(401, _err("unauthorized", "令牌不对"))
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, _err("bad_request", "请求不是 JSON"))
            path = self.path.strip("/")
            if path == "ping":
                res = {"ok": True, "data": {"pid": os.getpid()}}
            elif path == "status":
                res = {"ok": True, "data": mgr.status()}
            elif path == "start":
                res = mgr.start(body.get("kind", ""), body.get("params") or {}, body.get("llm_env") or {})
            elif path == "events":
                res = mgr.events(body.get("job"), int(body.get("since") or 0))
            elif path in ("pause", "takeover", "resume", "stop"):
                res = mgr.control(path, body.get("job"))
            elif path == "shutdown":
                cur = mgr.current
                if cur and cur.state in LIVE:
                    cur.stop()
                threading.Thread(target=srv.shutdown, daemon=True).start()
                res = {"ok": True, "data": {}}
            else:
                return self._send(404, _err("not_found", f"没有这个接口：{path}"))
            self._send(200, res)

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    return srv, token


def write_info(home: Path, port: int, token: str) -> Path:
    p = home / "service.json"
    tmp = home / "service.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid()}, f)
    os.replace(tmp, p)
    return p


def main() -> int:
    home = ensure_home()
    from hiro.cli.lock import FileLock

    lock = FileLock(home / "service.lock")  # 整个服务进程一直拿着：同一个 HIRO_HOME 只能有一个服务
    if not lock.acquire(blocking=False):
        print("已经有一个 hiro 服务在跑", file=sys.stderr)
        return 3
    srv, token = make_server(home)
    info = write_info(home, srv.server_port, token)
    try:
        srv.serve_forever()
    finally:
        try:
            if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
