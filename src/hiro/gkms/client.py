"""GKMS 子进程的调用端（spec 4.1 / 4.3）：懒启动、单次超时、挂了重启一次，再挂这一局就不用 GKMS。

超时时直接杀掉子进程（它是单线程的，还在算的那次结果会串到下一次请求上），下一次调用再起一个，算一次重启。
子进程的 stderr 写到日志文件（默认系统临时目录），不接管道，免得写满管道把子进程卡住。
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from hiro.gkms.locate import Install

WORKER = Path(__file__).with_name("worker.py")
DEFAULT_TIMEOUT_S = 20.0
MAX_STARTS = 2  # 第一次启动 + 重启一次
DEFAULT_HUB = "http://m5.ctymc.cn:23331"  # GKMS 网站（选卡包 /api/pickv/<偶像卡>）；GKMS 没配网站地址时用。gkms.nyarot.org 对 urllib 回 403


class GkmsError(Exception):
    pass


class Client:
    def __init__(self, install: Install, *, log_path: Path | str | None = None) -> None:
        self.install = install
        self.log_path = Path(log_path) if log_path else Path(tempfile.gettempdir()) / "hiro-gkms-worker.log"
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.starts = 0
        self.dead = False

    def _start(self) -> None:
        if self.starts >= MAX_STARTS:
            self.dead = True
            raise GkmsError(f"GKMS 子进程已经重启过 {MAX_STARTS - 1} 次还是不行，这一局不再用")
        self.starts += 1
        log = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115  子进程活着期间一直写
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        env.setdefault("GKMS_HUB_URL", DEFAULT_HUB)
        self.proc = subprocess.Popen(
            [self.install.python, "-I", "-u", str(WORKER), str(self.install.home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=str(self.install.home),
            text=True, encoding="utf-8", env=env,
        )  # fmt: skip
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self._lines), daemon=True, name="gkms-worker-out").start()

    @staticmethod
    def _pump(proc: subprocess.Popen, q: queue.Queue) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line)
        q.put(None)  # 子进程退出

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self.proc = None

    def call(self, op: str, *, timeout: float | None = None, **args: Any) -> dict:
        """调一次 worker；出错、超时、子进程退出都抛 GkmsError。"""
        with self._lock:
            if self.dead:
                raise GkmsError("GKMS 子进程挂了两次，这一局不再用")
            if self.proc is None or self.proc.poll() is not None:
                self._kill()
                self._start()
            rid = next(self._ids)
            assert self.proc is not None and self.proc.stdin is not None
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "op": op, "args": args}, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._kill()
                raise GkmsError(f"GKMS 子进程收不到请求：{e}") from e
            wait = DEFAULT_TIMEOUT_S if timeout is None else timeout
            while True:
                try:
                    line = self._lines.get(timeout=wait)
                except queue.Empty:
                    self._kill()
                    raise GkmsError(f"GKMS {op} 超时（{wait:g} 秒），子进程已结束") from None
                if line is None:
                    self._kill()
                    if self.starts >= MAX_STARTS:
                        self.dead = True
                    raise GkmsError(f"GKMS 子进程退出了（日志 {self.log_path}）")
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # 不是协议行（GKMS 绕过重定向直接写的），跳过
                if reply.get("id") != rid:
                    continue
                if not reply.get("ok"):
                    raise GkmsError(f"GKMS {op} 出错：{reply.get('error')}")
                return reply.get("result") or {}

    def close(self) -> None:
        with self._lock:
            self._kill()
