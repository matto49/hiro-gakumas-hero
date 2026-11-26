"""在子进程里跑 Apple Vision 文字识别，坏了就重启（N29，HIRO_VISION_RESTART=1 时由 hiro.vision.ocr 启用）。

为什么：Vision 会在某个进程里自己坏掉（e5rtError，CPU 重试也不行），重开进程就好（G1-F6 / F7 / F8、G1 第 3 轮 11-26）。
原来坏了就整个进程改用 RapidOCR，扫回忆慢三四倍、CPU 400%。这里把识别挪进一个可以随时杀掉重开的子进程。

协议（子进程的 stdin / stdout，一问一答）：
- 请求：一行 JSON {"w": 原图宽, "h": 原图高, "languages": [...], "n": PNG 字节数}，接着 n 字节 PNG（已放大的图）
- 回答：一行 JSON {"out": [[文字, 置信度, [x1, y1, x2, y2]], ...], "err": 错误或 null}
子进程把自己的 sys.stdout 指到 stderr，回答只走原来的 fd 1，免得 PyObjC / 日志的输出混进协议。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

READ_TIMEOUT = 30.0  # 一次识别最多等这么久（正常 0.1–0.5 秒；第一次要加载模型）


class ChildDied(RuntimeError):
    """子进程没回答（退出、超时、回答读不懂）。"""


class VisionChild:
    def __init__(self, timeout: float = READ_TIMEOUT, patch: str = ""):
        """patch：在 serve() 之前执行的代码（只给测试换掉真的 Vision 用）。"""
        self.timeout = timeout
        src = str(Path(__file__).resolve().parents[2])  # src/：子进程按同一份代码导入 hiro
        code = f"import sys; sys.path.insert(0, {src!r}); {patch}\nfrom hiro.vision.vision_child import serve; serve()"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, "HIRO_OCR": "vision"},
        )
        logger.info("文字识别：Vision 子进程已启动（pid %d）", self.proc.pid)

    def read(
        self, img, w: int, h: int, languages
    ) -> tuple[list[tuple[str, float, tuple[int, int, int, int]]], str | None]:
        import cv2

        ok, buf = cv2.imencode(".png", img)
        if not ok:
            return [], "图编码失败"
        data = buf.tobytes()
        stdin, stdout = self.proc.stdin, self.proc.stdout
        assert stdin is not None and stdout is not None
        head = (json.dumps({"w": w, "h": h, "languages": list(languages), "n": len(data)}) + "\n").encode()
        got: dict = {}

        def talk() -> (
            None
        ):  # 写请求、读回答整段放在一个线程里，主线程用同一个截止时间等（#152 review：子进程卡住时写管道也会卡）
            try:
                stdin.write(head)
                stdin.write(data)
                stdin.flush()
                got["line"] = stdout.readline()
            except (OSError, ValueError) as e:
                got["exc"] = e

        t = threading.Thread(target=talk, daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            self.kill()  # 先杀子进程：管道断了，卡在读写上的线程也就出来了
            t.join(2)
            raise ChildDied(f"Vision 子进程 {self.timeout:.0f} 秒没回答")
        if "exc" in got:
            raise ChildDied(f"Vision 子进程收不到请求：{got['exc']}") from got["exc"]
        line = got.get("line") or b""
        if not line:
            raise ChildDied(f"Vision 子进程退出了（退出码 {self.proc.poll()}）")
        try:
            reply = json.loads(line)
        except ValueError as e:
            raise ChildDied(f"Vision 子进程的回答读不懂：{line[:80]!r}") from e
        return [(t, float(c), (int(b[0]), int(b[1]), int(b[2]), int(b[3]))) for t, c, b in reply["out"]], reply.get(
            "err"
        )

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()

    def close(self) -> None:
        """正常关：关 stdin 让子进程自己退出，2 秒不退就杀；子进程已经卡住或退出时直接杀（关管道可能卡在没写完的缓冲上）。"""
        if self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.kill()


_lock = threading.Lock()
_child: VisionChild | None = None


def client() -> VisionChild:
    """当前的子进程（没有或已经退出就起一个）。"""
    global _child
    with _lock:
        if _child is None or _child.proc.poll() is not None:
            _child = VisionChild()
        return _child


def restart() -> None:
    """杀掉当前子进程，下次 client() 起新的。"""
    stop()


def stop() -> None:
    global _child
    with _lock:
        if _child is not None:
            _child.close()
            _child = None


def serve() -> None:
    """子进程主循环：读请求、跑 Vision、写回答，直到 stdin 关闭。"""
    from hiro.vision.ocr import _vision_request

    import cv2
    import numpy as np

    out_fd = os.dup(1)
    os.dup2(2, 1)  # 之后谁 print 都进 stderr
    sys.stdout = sys.stderr
    reply = os.fdopen(out_fd, "wb")
    stdin = sys.stdin.buffer
    while True:
        head = stdin.readline()
        if not head:
            return
        req = json.loads(head)
        data = stdin.read(req["n"])
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        try:
            if img is None:
                raise ValueError("收到的图解不开")
            out, err = _vision_request(img, req["w"], req["h"], req["languages"])
        except Exception as e:  # noqa: BLE001  报给父进程，由它决定重启
            out, err = [], f"{type(e).__name__}: {e}"
        reply.write(
            (json.dumps({"out": [[t, c, list(b)] for t, c, b in out], "err": err}, ensure_ascii=False) + "\n").encode()
        )
        reply.flush()
