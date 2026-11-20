"""Local SIMD PaddleOCR worker and KotoneBot OCR adapter."""
from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import subprocess
from functools import cache
from pathlib import Path
from queue import Empty, Queue
from threading import RLock, Thread

import cv2
import numpy as np


class SimdOcr:
    def __init__(self):
        deps = Path(os.environ.get("HIRO_SIMD_DEPS") or Path(__file__).resolve().parents[3] / ".deps")
        helper = deps / "simd-ocr/HiroSimdOcr.dll"
        models = deps / "SimdPaddleOCR/models"
        for path in (helper, *(models / name for name in ("medium_det.onnx", "medium_rec.onnx", "rec_keys.txt"))):
            if not path.is_file():
                raise RuntimeError(f"HIRO_OCR=simd 缺少 {path}；安装方法见 docs/assets.md")
        if (dotnet := shutil.which("dotnet")) is None:
            raise RuntimeError("HIRO_OCR=simd 找不到 .NET 10 (dotnet)")
        self.lock = RLock()
        self.replies = Queue()
        self.proc = subprocess.Popen(
            [dotnet, str(helper.resolve()), str(models.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        Thread(target=self._read_replies, daemon=True).start()
        try:
            if self._read(45).get("ready") is not True:
                raise RuntimeError("SIMD OCR 启动时没有返回 ready")
        except Exception:
            self.close()
            raise
        atexit.register(self.close)

    def _read_replies(self):
        for line in self.proc.stdout:
            self.replies.put(line)
        self.replies.put(None)

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

    def _infer(self, pixels, only_rec):
        h, w = pixels.shape[:2]
        request = {"width": w, "height": h, "only_rec": only_rec,
                   "bgr": base64.b64encode(memoryview(pixels)).decode("ascii")}
        with self.lock:
            self.proc.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
            return self._read(15)["lines"]

    def close(self):
        with self.lock:
            if self.proc.poll() is None:
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
        # Preserve the previous backend's grayscale preprocessing and ROI scale.
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        scale = min(scale, 2000 / max(original_h, original_w))
        if scale != 1:
            gray = cv2.resize(gray, (max(1, round(original_w * scale)), max(1, round(original_h * scale))),
                              interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
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


@cache
def install_kotonebot() -> None:
    """Keep KotoneBot's find/expect and matching behavior, swapping only its image OCR."""
    from kotonebot.backend.ocr import Ocr, OcrResult, OcrResultList, sanitize_text
    from kotonebot.primitives.geometry import Rect

    client = simd_engine()  # fail at startup before any device input if the requested backend is unavailable

    def simd_ocr(self, img, *, rect=None, pad=True, only_rec=False):
        if rect is None:
            x, y, crop = 0, 0, img
        else:
            x, y, w, h = rect.xywh
            crop = img[y:y+h, x:x+w]
        results = OcrResultList()
        # The 631px padding in KotoneBot is specific to its RapidOCR model.
        scale = 2 if rect is not None else 1
        for text, confidence, (x1, y1, x2, y2) in client.read(crop, scale=scale, only_rec=only_rec):
            absolute = Rect(x + x1, y + y1, x2 - x1, y2 - y1)
            relative = absolute if only_rec else Rect(x1, y1, x2 - x1, y2 - y1)
            results.append(OcrResult(sanitize_text(text), relative, confidence, absolute))
        return results

    Ocr.ocr = simd_ocr
