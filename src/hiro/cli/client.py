"""连 hiro 服务：读 HIRO_HOME/service.json，连不上就拉起一个（后台、脱离当前终端），等它写好 service.json。"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.registry import CliError

SPAWN_TIMEOUT = 15.0


def _info() -> dict | None:
    p = ensure_home() / "service.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _post(info: dict, path: str, body: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"X-Hiro-Token": info["token"], "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 本机
        return json.loads(r.read())


def _alive(info: dict | None) -> bool:
    if not info:
        return False
    try:
        return _post(info, "ping", {}, timeout=5).get("ok") is True
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):  # 服务正在退出时响应会被截断
        return False


def _spawn() -> dict:
    """拉起服务。拿着 spawn.lock 做：几个客户端同时发现服务没在跑时只拉起一个；服务锁被拿着（服务在跑、只是慢）就等它。"""
    from hiro.cli.lock import FileLock, held_elsewhere

    home = ensure_home()
    with FileLock(home / "spawn.lock"):
        info = _info()
        if _alive(info):
            return info
        if not held_elsewhere(home / "service.lock"):
            (home / "service.json").unlink(missing_ok=True)
            _start_process(home)
        return _wait_alive(home)


def _wait_alive(home) -> dict:
    t0 = time.time()
    while time.time() - t0 < SPAWN_TIMEOUT:
        info = _info()
        if _alive(info):
            return info
        time.sleep(0.1)
    raise CliError("service_down", "hiro 服务没能启动", f"看日志：{home / 'service.log'}")


def _start_process(home) -> None:
    src = str(Path(__file__).resolve().parents[2])
    env = os.environ.copy()
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    log = open(home / "service.log", "ab")  # noqa: SIM115 交给子进程
    kw: dict = (
        {"start_new_session": True}
        if sys.platform != "win32"
        else {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    )
    subprocess.Popen(
        [sys.executable, "-m", "hiro.cli.service"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        env=env,
        cwd=home,
        **kw,
    )


def call(path: str, body: dict | None = None) -> dict:
    """调服务接口，返回信封；服务没在跑就先拉起。"""
    info = _info()
    if not _alive(info):
        info = _spawn()
    try:
        return _post(info, path, body or {})
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise CliError("service_down", f"连不上 hiro 服务：{e}", "再试一次；一直这样运行 hiro doctor") from None


def data(path: str, body: dict | None = None) -> dict:
    """调服务接口，失败转成 CliError。"""
    env = call(path, body)
    if not env.get("ok"):
        e = env.get("error") or {}
        raise CliError(
            e.get("code", "error"),
            e.get("message", "服务出错"),
            e.get("hint", ""),
            exit_code=2 if e.get("code") == "bad_args" else 1,
        )
    return env["data"]


def shutdown() -> None:
    info = _info()
    if _alive(info):
        try:  # noqa: SIM105
            _post(info, "shutdown", {}, timeout=5)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        t0 = time.time()
        while time.time() - t0 < 5 and _alive(info):
            time.sleep(0.1)
