"""本机 SIMD PaddleOCR 辅助进程（.NET 10，PP-OCRv6 medium）。

HIRO_OCR=simd 时用。辅助进程挂掉（自己崩溃、读超时被关掉）后，下一次识别会自动重新拉起，再试一次。"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import subprocess
from functools import cache
from queue import Empty, Queue
from threading import RLock, Thread

from hiro.vision import simd_deps

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 辅助进程开机时在 stderr 报一句用的是哪块 GPU（「[metal] device: Apple M4」「[vulkan] …」）。原来 stderr 直接继承，
# 这句会进任务日志、显示在对话卡上（G1 第 5 轮 11-26），小白看不懂：这类行降成 debug，其它行（.NET 报错）照旧打成警告
_DEVICE_LINE = ("[metal]", "[vulkan]", "[cpu]", "[gpu]")

START_TIMEOUT = 45  # 辅助进程启动（加载模型）最多等这么久
READ_TIMEOUT = 15  # 一次识别最多等这么久


def _log_stderr(stream) -> None:
    """转发辅助进程的 stderr：报设备的那句只记 debug，其它（.NET 异常等）记警告，出错时日志里还看得到。"""
    for line in stream:
        text = line.rstrip()
        if not text:
            continue
        if text.lower().startswith(_DEVICE_LINE):
            logger.debug("SIMD 辅助进程：%s", text)
        else:
            logger.warning("SIMD 辅助进程：%s", text)


class SimdOcr:
    def __init__(self):
        deps = simd_deps.deps_dir()
        helper = deps / simd_deps.HELPER
        models = deps / simd_deps.MODELS
        if missing := simd_deps.missing_files(deps):
            raise RuntimeError(f"HIRO_OCR=simd 缺少 {missing[0]}；安装方法见 docs/assets.md")
        if (found := simd_deps.find_dotnet(deps)) is None:
            raise RuntimeError("HIRO_OCR=simd 找不到 .NET 10 Runtime；装法见 docs/assets.md")
        dotnet, self.env = found
        self.cmd = [dotnet, str(helper.resolve()), str(models.resolve())]
        self.lock = RLock()
        self._start()
        atexit.register(self.close)

    def _start(self):
        """拉起辅助进程，等它加载完模型回 ready。每个进程配一个自己的回复队列和读线程：重启后旧线程不会往新队列里塞东西。"""
        self.replies = Queue()
        self.proc = subprocess.Popen(
            self.cmd,
            env={**os.environ, **self.env},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )  # fmt: skip
        Thread(target=self._read_replies, args=(self.proc, self.replies), daemon=True).start()
        Thread(target=_log_stderr, args=(self.proc.stderr,), daemon=True).start()
        try:
            if self._read(START_TIMEOUT).get("ready") is not True:
                raise RuntimeError("SIMD OCR 启动时没有返回 ready")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _read_replies(proc, replies):
        for line in proc.stdout:
            replies.put(line)
        replies.put(None)

    def _read(self, timeout):
        try:
            reply = self.replies.get(timeout=timeout)
        except Empty:
            self.close()
            raise RuntimeError(f"SIMD OCR 在 {timeout}s 内没有返回") from None
        if reply is None:
            raise RuntimeError("SIMD OCR 子进程已退出；见上方 .NET 错误")
        result = json.loads(reply)
        if "error" in result:
            raise RuntimeError(f"SIMD OCR: {result['error']}")
        return result

    def _request(self, line: str):
        stdin = self.proc.stdin
        assert stdin is not None  # Popen 时给了 stdin=PIPE
        stdin.write(line)
        stdin.flush()
        return self._read(READ_TIMEOUT)["lines"]

    def _infer(self, pixels, only_rec):
        h, w = pixels.shape[:2]
        request = {"width": w, "height": h, "only_rec": only_rec,
                   "bgr": base64.b64encode(memoryview(pixels)).decode("ascii")}  # fmt: skip
        line = json.dumps(request, separators=(",", ":")) + "\n"
        with self.lock:
            if self.proc.poll() is not None:  # 上次超时被关掉，或者自己崩了：重新拉起
                logger.warning("SIMD OCR 辅助进程已退出（退出码 %s），重新启动", self.proc.returncode)
                self._start()
            try:
                return self._request(line)
            except (RuntimeError, OSError) as e:  # 超时 / 子进程中途退出 / 管道断开：关掉、重启，再试一次
                logger.warning("SIMD OCR 识别失败（%s），重启辅助进程再试一次", e)
                self.close()
                self._start()
                return self._request(line)

    def close(self):
        with self.lock:
            if self.proc.poll() is None:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)

    def read(self, img, *, scale: float = 1, only_rec: bool = False):
        if img is None or img.size == 0:
            return []
        from hiro.vision.ocr import _SIMPLIFIED

        original_h, original_w = img.shape[:2]
        # 和原来的后端一样先转灰度、按 ROI 缩放
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        scale = min(scale, 2000 / max(original_h, original_w))
        if scale != 1:
            gray = cv2.resize(
                gray,
                (max(1, round(original_w * scale)), max(1, round(original_h * scale))),
                interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
            )
        h, w = gray.shape
        lines = self._infer(np.ascontiguousarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)), only_rec)
        out = []
        for row in lines:
            text = row["Text"].translate(_SIMPLIFIED).strip()
            if not text:
                continue
            x1, x2 = (min(original_w, max(0, round(float(row[k]) * original_w / w))) for k in ("X1", "X2"))
            y1, y2 = (min(original_h, max(0, round(float(row[k]) * original_h / h))) for k in ("Y1", "Y2"))
            if x2 > x1 and y2 > y1:
                out.append((text, float(row["Score"]), (x1, y1, x2, y2)))
        return sorted(out, key=lambda r: (r[2][1], r[2][0]))


@cache
def simd_engine() -> SimdOcr:
    return SimdOcr()
