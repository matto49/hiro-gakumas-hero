"""跨进程文件锁：一台机器上只跑一个 hiro 服务、只有一个客户端在拉起服务。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self, blocking: bool = True, timeout: float = 30.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        t0 = time.monotonic()
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = fd
                return True
            except OSError:
                if not blocking or time.monotonic() - t0 > timeout:
                    os.close(fd)
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"等不到锁 {self.path}")
        return self

    def __exit__(self, *exc):
        self.release()


def held_elsewhere(path: Path) -> bool:
    """这把锁现在被别的进程（或本进程别的句柄）拿着。"""
    lk = FileLock(path)
    if lk.acquire(blocking=False):
        lk.release()
        return False
    return True
