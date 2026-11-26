"""固定区域的数字和短文本 OCR。两个后端，接口相同：

- vision：Apple Vision（macOS 系统文字识别）。实测在作者机器上比 KAA 的 RapidOCR 快 2–7 倍且更准（见 README），
  macOS 上默认用它。区域先放大 3 倍再识别，小号描边数字更稳。
- rapidocr：RapidOCR（rapidocr_onnxruntime + PaddleOCR PP-OCRv3 日文 / 英文识别模型，见 _rapid_model；不经过 kotonebot）。
  Windows 没装 SIMD 时和其他平台的缺省。
- winocr：Windows 自带的文字识别（Windows.Media.Ocr，经 winrt 调用；要装了日文 OCR 语言，Windows 设置里加日语即可）。
  Windows 实测单块区域约 0.05 s。它按「词」返回结果，日文是一字一词：这里按间隙把一行拆成段、段内拼回文字（见 _split_words / _join_words）。

- ppocr5：rapidocr 包里的 PaddleOCR PP-OCRv5（中日英一个模型，onnxruntime），比 KAA 自带的 RapidOCR 模型新；
  Mac / Windows 结果一致。中日混合模型会把部分汉字认成简体（强化、交换済），按 _SIMPLIFIED 换回日文写法。

- simd：本机 SIMD PaddleOCR PP-OCRv6 medium（.NET 10 辅助进程，GPU 走 Vulkan / Metal），供 Hiro 使用（安装见 docs/assets.md）。
  Windows 上装好了就是缺省：识别测试集（11-24）上准确率和 Apple Vision 差不到 1 个点，比 RapidOCR CPU 快 5–6 倍。

HIRO_OCR=auto（缺省：macOS 且能导入 Vision → vision；Windows 装好了 SIMD → simd；否则 rapidocr）/ vision / rapidocr / winocr / ppocr5 / simd。
rapidocr 的推理设备：HIRO_OCR_DEVICE=auto（缺省 CUDA → DirectML → CPU）/ cuda / dml / cpu；CPU 线程数 HIRO_OCR_THREADS（见 ocr_device_order）；
DirectML 用哪块显卡 HIRO_OCR_DML_DEVICE（缺省按 rapidocr 用 0 号，见 dml_device_id）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import cv2
from cv2.typing import MatLike

logger = logging.getLogger(__name__)

ENGINES = ("auto", "vision", "rapidocr", "winocr", "ppocr5", "simd")
_engine: str | None = None


def _vision_available() -> bool:
    try:
        import Foundation  # noqa: F401
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def _simd_usable(fallback: str = "RapidOCR") -> bool:
    """SIMD 能不能用：辅助程序、模型、.NET 10 都在且能启动（启动一次，之后复用同一个进程）。不能用时日志写明原因和改用什么。"""
    from hiro.vision import simd_ocr

    try:
        simd_ocr.simd_engine()
    except Exception as e:  # noqa: BLE001  缺文件、没装 .NET、启动超时都退回 RapidOCR
        logger.warning("文字识别：SIMD 用不了（%s），用 %s", e, fallback)
        return False
    return True


def engine() -> str:
    """当前 OCR 后端（首次调用时按 HIRO_OCR 和平台确定，之后不变）。"""
    global _engine
    if _engine is None:
        want = (os.environ.get("HIRO_OCR") or "auto").strip().lower()
        if want not in ENGINES:
            raise ValueError(f"HIRO_OCR={want!r} 不认识；可选：{', '.join(ENGINES)}")
        if want == "auto":
            # Mac 也先用 SIMD（用户 11-26 21:4x：Mac 上 Apple Vision 在 macOS 27.2.1 里每个进程只能读一次，N29），
            # 没装好再用 Vision，都不行用 RapidOCR；Windows 先 SIMD 再 RapidOCR
            if sys.platform in ("darwin", "win32") and _simd_usable(
                fallback="Apple Vision" if sys.platform == "darwin" else "RapidOCR"
            ):
                want = "simd"
            elif sys.platform == "darwin" and _vision_available():
                want = "vision"
            else:
                want = "rapidocr"
        _engine = want
    return _engine


def _vision_raw(img: MatLike, w: int, h: int, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框按归一化坐标换回原图尺寸 (w, h)。失败记进 _vision_fail（连续失败到 VISION_FALLBACK_AFTER 次换后端）。"""
    out, err = _vision_request(img, w, h, languages)
    _note_vision(err)
    return out


def _vision_request(
    img: MatLike, w: int, h: int, languages
) -> tuple[list[tuple[str, float, tuple[int, int, int, int]]], str | None]:
    """跑一次 Vision 识别：返回 (结果, 错误)。不碰进程里的失败计数（Vision 子进程 hiro.vision.vision_child 也用它）。"""
    import Vision
    from Foundation import NSData

    _ok, buf = cv2.imencode(".png", img)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None
    )
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)  # accurate
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(False)
    ok, err = handler.performRequests_error_([request], None)
    if not ok and (hasattr(request, "setComputeDevice_forComputeStage_") or hasattr(request, "setUsesCPUOnly_")):
        # 失败时换 CPU 重试一次：macOS 27.2.1 上神经引擎会报 e5rtError（create_precompiled_compute_operation failed, 13），
        # CPU 还能用（11-26 实机）。新系统 setUsesCPUOnly_ 不再起作用，要用 setComputeDevice_forComputeStage_ 指定 CPU 设备
        retry = Vision.VNRecognizeTextRequest.alloc().init()
        retry.setRecognitionLevel_(0)
        retry.setRecognitionLanguages_(list(languages))
        retry.setUsesLanguageCorrection_(False)
        _use_cpu(retry)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None
        )
        ok2, err2 = handler.performRequests_error_([retry], None)
        if ok2:
            logger.warning("文字识别：Apple Vision 请求失败（%s），改用 CPU 重试成功", err)
            ok, request = True, retry
        else:
            err = err2 or err
    if not ok:
        # 原来不看返回值：请求失败时 results() 是 None，静默当成「没有字」（G1-F7 11-26：同一进程里 Vision 坏掉后
        # 选行动页 options / 体力 / P 点 / 牌组张数全读空，决策 Wait 三次停机；重开进程同一画面正常）
        return [], (str(err) if err is not None else "performRequests 返回 False")
    out = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)[0]
        bb = obs.boundingBox()  # 归一化坐标，原点在左下
        x1, y1 = bb.origin.x * w, (1 - bb.origin.y - bb.size.height) * h
        out.append(
            (
                # macOS 27.2.1 的 Vision（ja-JP）有时把日文汉字读成简体：「審査基準」读成「審查基準」，
                # 初的考前说明页判不出（A16 11-26，私有帧 pages/hajime-briefing-fktn.png）。按 _VISION_SIMPLIFIED 换回日文写法
                str(cand.string()).translate(_VISION_SIMPLIFIED),
                float(cand.confidence()),
                (round(x1), round(y1), round(x1 + bb.size.width * w), round(y1 + bb.size.height * h)),
            )
        )
    return out, None


def _use_cpu(request) -> None:
    """让 Vision 请求只用 CPU：有 computeDevice 接口就按阶段指定 CPU 设备，否则退回旧的 setUsesCPUOnly_。"""
    if hasattr(request, "setComputeDevice_forComputeStage_") and hasattr(
        request, "supportedComputeStageDevicesAndReturnError_"
    ):
        got = request.supportedComputeStageDevicesAndReturnError_(None)
        devices = got[0] if isinstance(got, tuple) else got
        for stage, devs in dict(devices or {}).items():
            cpu = next((d for d in devs if "CPU" in type(d).__name__ or "CPU" in str(d)), None)
            if cpu is not None:
                request.setComputeDevice_forComputeStage_(cpu, stage)
        return
    if hasattr(request, "setUsesCPUOnly_"):
        request.setUsesCPUOnly_(True)


# Apple Vision 连续失败次数和最后一次错误（ocr_health 用）；child：已改到子进程里跑（HIRO_VISION_RESTART=1，N29）
_vision_fail: dict = {"streak": 0, "error": None, "child": False}


def _note_vision(error: str | None) -> None:
    if error is None:
        _vision_fail["streak"] = 0
        return
    _vision_fail["streak"] += 1
    _vision_fail["error"] = error
    if _vision_fail["streak"] in (1, 10, 100):
        logger.warning("文字识别：Apple Vision 请求失败（第 %d 次连续）：%s", _vision_fail["streak"], error)
    if _vision_fail["streak"] < VISION_FALLBACK_AFTER:
        return
    if _vision_restart_on():
        # N29（G1 第 3 轮 11-26）：改用 RapidOCR 后扫描慢三四倍、CPU 400%。Vision 坏了是这个进程的事，重开进程就好：
        # 改在子进程里跑 Vision，子进程坏了重启（_vision_child_raw）；重启也救不回来再退到 RapidOCR
        _vision_fail["child"] = True  # 引擎名仍是 vision：各处按 engine() != "vision" 放宽匹配的逻辑不变
        _vision_fail["streak"] = 0
        logger.error("文字识别：Apple Vision 连续 %d 次失败（%s），改到子进程里跑 Vision", VISION_FALLBACK_AFTER, error)
        return
    global _engine
    if _rapid_ready():
        # 同一进程里 Vision 坏了重开进程才好（G1-F6 / F7 / F8）：别再悄悄读空，这个进程余下的识别改用 RapidOCR
        _engine = "rapidocr"
        logger.error(
            "文字识别：Apple Vision 连续 %d 次失败（%s），这个进程改用 RapidOCR", _vision_fail["streak"], error
        )


VISION_FALLBACK_AFTER = 3
CHILD_RESTARTS = 3  # Vision 子进程连着重启这么多次还读不出，就退到 RapidOCR


def _vision_restart_on() -> bool:
    """HIRO_VISION_RESTART=1：Vision 在本进程坏了改到子进程里跑、坏了重启子进程（N29）。缺省关：照旧退到 RapidOCR。"""
    return os.environ.get("HIRO_VISION_RESTART", "").strip() == "1"


def _vision_child_raw(img: MatLike, w: int, h: int, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """在 Vision 子进程里识别；子进程报错、超时或退出就重启再试，连着 CHILD_RESTARTS 次都不行退到 RapidOCR。"""
    from hiro.vision import vision_child

    for attempt in range(CHILD_RESTARTS):
        try:
            out, err = vision_child.client().read(img, w, h, languages)
        except vision_child.ChildDied as e:
            out, err = [], str(e)
        if err is None:
            _vision_fail["streak"] = 0
            return out
        _vision_fail["error"] = err
        logger.warning("文字识别：Vision 子进程失败（第 %d 次）：%s，重启子进程", attempt + 1, err)
        vision_child.restart()
    global _engine
    _vision_fail["child"] = False
    vision_child.stop()
    if _rapid_ready():
        _engine = "rapidocr"
        logger.error(
            "文字识别：Vision 子进程重启 %d 次都读不出（%s），这个进程改用 RapidOCR",
            CHILD_RESTARTS,
            _vision_fail["error"],
        )
        return _rapid_raw(img, img.shape[1] / w if w else 1.0, languages)
    return []


def _rapid_ready() -> bool:
    """RapidOCR 能不能接手：包装得上、识别模型在。"""
    import importlib.util

    if importlib.util.find_spec("rapidocr_onnxruntime") is None:
        return False
    return all((RAPID_DIR / name).exists() for name in RAPID_MODELS.values())


def _probe_image() -> MatLike:
    import numpy as np

    img = np.full((60, 200, 3), 255, np.uint8)
    cv2.putText(img, "123", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return img


def ocr_health() -> str | None:
    """文字识别还能不能用：对一张画好的「123」跑一次 OCR。读得出返回 None；读不出返回原因（Vision 的报错或「自检读空」）。
    选行动等页面重读仍读空时用它区分「画面真没有」和「本进程 OCR 坏了」（G1-F7）。"""
    try:
        text = "".join(t for t, _, _ in ocr_boxes(_probe_image(), scale=1, languages=("en-US",)))
    except Exception as e:  # noqa: BLE001  后端抛异常也算坏了
        return f"{type(e).__name__}: {e}"
    if "123" in text.replace(" ", ""):
        return None
    err = _vision_fail["error"] if engine() == "vision" and _vision_fail["streak"] else None
    return f"自检图读成 {text!r}" + (f"（Vision：{err}）" if err else "")


RAPID_DIR = Path(__file__).resolve().parents[3] / "cache" / "rapidocr"
RAPID_MODELS = {
    "ja": "japan_PP-OCRv3_rec_infer.onnx",
    "en": "en_PP-OCRv3_rec_infer.onnx",
}  # 识别模型；检测用 rapidocr_onnxruntime 自带的
RAPID_PAD = 631  # 小图居中补白到 631×631 再识别（PaddleOCR 模型图太小准确率低；与原先经 kotonebot 调用时一致）
_rapid: dict = {}


def _rapid_model(tag: str) -> Path:
    """识别模型路径（RAPID_DIR = cache/rapidocr）。PaddleOCR PP-OCRv3 日文 / 英文模型，原来随 KAA 带，
    去 KAA 后托管在本仓库 GitHub Release，由 tools/assets/rapid_models.py 下载（安装脚本会调）。"""
    own = RAPID_DIR / RAPID_MODELS[tag]
    if own.exists():
        return own
    raise RuntimeError(
        f"HIRO_OCR=rapidocr 要的模型 {own.name} 不在 {own.parent}：先跑 python tools/assets/rapid_models.py"
    )


OCR_DEVICES = ("cuda", "dml", "cpu")  # HIRO_OCR_DEVICE=auto 时按这个顺序试
_EP = {"cuda": "CUDAExecutionProvider", "dml": "DmlExecutionProvider", "cpu": "CPUExecutionProvider"}


def ocr_device_order(want: str | None = None, available: list[str] | None = None) -> list[str]:
    """RapidOCR 推理设备的尝试顺序。HIRO_OCR_DEVICE：auto（缺省：CUDA → DirectML → CPU，装了哪种 onnxruntime 就有哪种）/ cuda / dml / cpu。
    指定的设备这台机器的 onnxruntime 没有时退回 CPU（日志写明）。CUDA 要 onnxruntime-gpu 且 CUDA / cuDNN 版本对得上；
    DirectML 要 onnxruntime-directml（Windows，任意显卡）。三者互斥，同一个环境只能装一种。"""
    want = (want if want is not None else os.environ.get("HIRO_OCR_DEVICE", "auto")).strip().lower() or "auto"
    if available is None:
        import importlib

        # 按模块名导入：类型检查环境（CI 只装 .[dev]）没有 onnxruntime
        available = list(importlib.import_module("onnxruntime").get_available_providers())
    providers: list[str] = available
    if want not in ("auto", *OCR_DEVICES):
        raise ValueError(f"HIRO_OCR_DEVICE 只能是 auto / cuda / dml / cpu，不是 {want!r}")
    order = [d for d in (OCR_DEVICES if want == "auto" else (want,)) if _EP[d] in providers]
    if want not in ("auto", "cpu") and not order:
        logger.warning(
            "HIRO_OCR_DEVICE=%s，但 onnxruntime 没有 %s（现有 %s），文字识别用 CPU", want, _EP[want], providers
        )
    return order if "cpu" in order else [*order, "cpu"]


def dml_device_id() -> int | None:
    """HIRO_OCR_DML_DEVICE：DirectML 用第几块显卡（DXGI 适配器序号）。不设时 None，按 rapidocr 原样用 0 号。
    有核显 / 虚拟显卡的机器上 0 号常常不是独显：Windows 台式机（11-24）0 号比 CPU 还慢，RTX 5060 是 1 号。"""
    v = (os.environ.get("HIRO_OCR_DML_DEVICE") or "").strip()
    if not v:
        return None
    if not v.isdigit():
        raise ValueError(f"HIRO_OCR_DML_DEVICE 要是显卡序号（0、1、2……），不是 {v!r}")
    return int(v)


def set_dml_device(eps: list, device_id: int | None) -> list:
    """onnxruntime 的 providers 列表里给 DirectML 那一项加上 device_id（其他不动，不改原列表）。"""
    if device_id is None:
        return eps
    return [(n, {**o, "device_id": device_id}) if n == _EP["dml"] else (n, o) for n, o in eps]


def _ocr_threads() -> int:
    """HIRO_OCR_THREADS：CPU 推理用几个线程（缺省 -1 = onnxruntime 自己定，通常占满所有核）。CPU 模式下调小能降占用和风扇声，单帧会慢一些。"""
    try:
        return int(os.environ.get("HIRO_OCR_THREADS") or -1)
    except ValueError:
        return -1


def _session_device(engine) -> str:
    """引擎实际在用的设备（看检测模型 session 的第一个 provider；onnxruntime 加载 CUDA / DirectML 失败时会悄悄退回 CPU）。"""
    try:
        first = engine.text_det.infer.session.get_providers()[0]
    except Exception:  # noqa: BLE001
        return "cpu"
    return next((d for d, ep in _EP.items() if ep == first), "cpu")


@contextlib.contextmanager
def _dml_device_patch(device_id: int | None):
    """rapidocr_onnxruntime 建 session 时 DirectML 固定用 0 号显卡、没有参数可改：建引擎期间临时包一层 _get_ep_list。"""
    if device_id is None:
        yield
        return
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession  # pyright: ignore[reportMissingImports]

    orig = OrtInferSession._get_ep_list
    OrtInferSession._get_ep_list = lambda self: set_dml_device(orig(self), device_id)  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield
    finally:
        OrtInferSession._get_ep_list = orig


def _rapid_engine(languages):
    from rapidocr_onnxruntime import RapidOCR

    tag = "en" if tuple(languages) == ("en-US",) else "ja"  # 只要英文时用英文模型（数字），否则日文模型
    if tag not in _rapid:
        threads = _ocr_threads()
        engine = None
        for dev in ocr_device_order():
            flags = {
                f"{part}_{k}": dev == d
                for part in ("det", "rec")
                for k, d in (("use_cuda", "cuda"), ("use_dml", "dml"))
            }
            with _dml_device_patch(dml_device_id() if dev == "dml" else None):
                engine = RapidOCR(rec_model_path=str(_rapid_model(tag)), use_det=True, use_cls=False, use_rec=True,
                                  det_intra_op_num_threads=threads, rec_intra_op_num_threads=threads, **flags)  # fmt: skip
            got = _session_device(engine)
            if got == dev:
                break
            logger.warning("文字识别：%s 没加载成功（实际 %s），试下一个", dev, got)
        logger.info("文字识别（RapidOCR %s 模型）用 %s", tag, _session_device(engine))
        _rapid[tag] = engine
    return _rapid[tag]


def _pad_to(img: MatLike, size: int) -> tuple[MatLike, int, int]:
    """居中补白到至少 size×size，返回 (新图, x 偏移, y 偏移)。"""
    h, w = img.shape[:2]
    ph, pw = max(0, size - h), max(0, size - w)
    if not ph and not pw:
        return img, 0, 0
    top, left = ph // 2, pw // 2
    return (
        cv2.copyMakeBorder(img, top, ph - top, left, pw - left, cv2.BORDER_CONSTANT, value=(255, 255, 255)),
        left,
        top,
    )


def _rapid_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；四点框取外接矩形、减去补白偏移、除以 scale 换回原图坐标；文字做 sanitize（全角转半角）。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    padded, ox, oy = _pad_to(img, RAPID_PAD)
    res, _ = _rapid_engine(languages)(padded)
    out = []
    for box, text, conf in res or []:
        xs, ys = [int(p[0]) - ox for p in box], [int(p[1]) - oy for p in box]
        out.append(
            (
                sanitize(text),
                float(conf),
                (round(min(xs) / scale), round(min(ys) / scale), round(max(xs) / scale), round(max(ys) / scale)),
            )
        )
    return out


_win: dict = {}


def _win_engine(languages):
    """按语言缓存 OcrEngine（只要英文时用 en-US，否则 ja）；事件循环也缓存，recognize_async 在里面同步跑完。"""
    import asyncio

    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    tag = "en-US" if tuple(languages) == ("en-US",) else "ja"
    if tag not in _win:
        eng = OcrEngine.try_create_from_language(Language(tag))
        if eng is None:
            raise RuntimeError(f"Windows OCR 没有 {tag} 语言：在 Windows 设置 → 时间和语言 → 语言里添加日语（含 OCR）")
        _win[tag] = eng
    if "loop" not in _win:
        _win["loop"] = asyncio.new_event_loop()
    return _win[tag], _win["loop"]


def _is_ascii_word(t: str) -> bool:
    return bool(t) and t.isascii() and any(c.isalnum() for c in t)


def _split_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> list[list[tuple]]:
    """Windows OCR 会把同一水平线上相隔很远的几段字并成一行（货架一排价格「70 49 105 35」），Apple Vision 是分开的。
    间隙大于字高的 1.2 倍就断成新的一段，各自出框。"""
    segs: list[list[tuple]] = []
    for wd in words:
        x, _, _, h = wd[1]
        if segs:
            px, _, pw, ph = segs[-1][-1][1]
            if x - (px + pw) <= 1.2 * max(h, ph):
                segs[-1].append(wd)
                continue
        segs.append([wd])
    return segs


def _join_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> str:
    """一段里的词拼成文字：日文一字一词、字间不留空格；只有两边都是英文 / 数字且确实隔开（间隙 ≥ 字高 0.35 倍）才留一个空格。"""
    out = ""
    prev = None
    for text, (x, _, w, h) in words:
        if prev is not None:
            ptext, px, pw, ph = prev
            gap = x - (px + pw)
            if _is_ascii_word(ptext) and _is_ascii_word(text[:1]) and gap >= 0.35 * max(h, ph):
                out += " "
        out += text
        prev = (text, x, w, h)
    return out


def _win_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框除以 scale 换回原图坐标。Windows OCR 不给置信度，统一记 1.0。"""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    eng, loop = _win_engine(languages)
    bgra = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA if img.ndim == 2 else cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    if min(h, w) < 40:  # 太小的图 Windows OCR 读不出，向右下补到 40（不影响原坐标）
        bgra = cv2.copyMakeBorder(bgra, 0, max(0, 40 - h), 0, max(0, 40 - w), cv2.BORDER_REPLICATE)
        h, w = bgra.shape[:2]
    dw = DataWriter()
    dw.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = loop.run_until_complete(eng.recognize_async(bmp))
    out = []
    for line in res.lines:
        ws = [
            (wd.text, (wd.bounding_rect.x, wd.bounding_rect.y, wd.bounding_rect.width, wd.bounding_rect.height))
            for wd in line.words
        ]
        if not ws:
            continue
        ws.sort(key=lambda t: t[1][0])
        for seg in _split_words(ws):
            x1 = min(b[0] for _, b in seg)
            y1 = min(b[1] for _, b in seg)
            x2 = max(b[0] + b[2] for _, b in seg)
            y2 = max(b[1] + b[3] for _, b in seg)
            out.append(
                (_join_words(seg), 1.0, (round(x1 / scale), round(y1 / scale), round(x2 / scale), round(y2 / scale)))
            )
    return out


# 只在简体中文里出现、日文不用的字 → 日文写法（PP-OCRv5 中日混合模型的读法；两边写法相同的字不收）
_SIMPLIFIED = str.maketrans(
    dict(
        zip(
            "强换查选择认识确战试验费结气动习课讲获领张级时间开关闭门问题说话读买卖价钱饮剧场进还对应实际现发处务员东车运转轮输边过达连补态优胜败负记录设计让训练绩续继绝经给线红约纪细终组织编缘网罗义书页顶项顺须预频颜风飞饰马驱鱼鸟乐兴举产亲亿传伤众华单卫历压变叶听启园围图圣坏块报备头夺奋宽导层岁岛带广库废异弹归忆怀总恶惊惯戏户扩扫护损据挥摄敌无显晓暂术机杂权极构标样树桥检欢测济满灵烧热爱猎环电疗盘盖监码础种积稳竞笔简类紧纯纸绘统维综绿缓缩罚职联脑艺节范药营虑见观规视览觉议许论访证评诉词译诗该详语误请诸谁调谈谢谱贝贡财责贤货质贩贯贵贴贸资赏赖赞赠跃践轻载较辅辈迁远违适递遗邮邻释钟钢银锁锐错键镜长闪闻阳阶阵陆险隐难额馆骑鲜鸣",
            "強換査選択認識確戦試験費結気動習課講獲領張級時間開関閉門問題説話読買売価銭飲劇場進還対応実際現発処務員東車運転輪輸辺過達連補態優勝敗負記録設計譲訓練績続継絶経給線紅約紀細終組織編縁網羅義書頁頂項順須預頻顔風飛飾馬駆魚鳥楽興挙産親億伝傷衆華単衛歴圧変葉聴啓園囲図聖壊塊報備頭奪奮寛導層歳島帯広庫廃異弾帰憶懐総悪驚慣戯戸拡掃護損拠揮摂敵無顕暁暫術機雑権極構標様樹橋検歓測済満霊焼熱愛猟環電療盤蓋監碼礎種積穏競筆簡類緊純紙絵統維総緑緩縮罰職聯脳芸節範薬営慮見観規視覧覚議許論訪証評訴詞訳詩該詳語誤請諸誰調談謝譜貝貢財責賢貨質販貫貴貼貿資賞頼賛贈躍践軽載較輔輩遷遠違適逓遺郵隣釈鐘鋼銀鎖鋭錯鍵鏡長閃聞陽階陣陸険隠難額館騎鮮鳴",
            strict=True,
        )
    )
)
# Vision 用的那份去掉日文里本来就用的字（叶う、机、据える）：Vision 日文读得准，读到这几个字多半就是原文
_VISION_SIMPLIFIED = {k: v for k, v in _SIMPLIFIED.items() if chr(k) not in "叶机据"}
_pp: dict = {}


PP_DIR_DEFAULT = Path(__file__).resolve().parents[3] / "cache" / "ppocr5"


def _pp_engine(languages):
    """PP-OCRv5 mobile（检测 + 中日英识别）。模型从 HIRO_PPOCR_DIR（缺省 cache/ppocr5）加载：KAA 把 onnxruntime 固定在 1.14
    （Windows DLL 问题），只认 IR ≤ 8，rapidocr 自带的 v5 模型是 IR 10，要先用 tools/assets/ppocr_models.py 转一份。
    数字也用同一个模型（英文模型读本项目的小号描边数字没有更好），languages 不再区分。"""
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    if "eng" not in _pp:
        d = Path(os.environ.get("HIRO_PPOCR_DIR") or PP_DIR_DEFAULT)
        det, rec = d / "ch_PP-OCRv5_det_mobile.onnx", d / "ch_PP-OCRv5_rec_mobile.onnx"
        if not det.exists() or not rec.exists():
            raise RuntimeError(f"HIRO_OCR=ppocr5 要的模型不在 {d}：先跑 python tools/assets/ppocr_models.py")
        _pp["eng"] = RapidOCR(params={
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE, "Det.model_path": str(det),
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE, "Rec.model_path": str(rec),
            "Rec.lang_type": LangRec.CH, "Global.log_level": "error",
        })  # fmt: skip
    return _pp["eng"]


def _pp_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；四点框取外接矩形、减去补白偏移、除以 scale 换回原图坐标；简体字换回日文写法。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    padded, ox, oy = _pad_to(img, RAPID_PAD)  # 小图居中补白（同 RapidOCR）：不补白时 HUD 小框上检测不到字
    res = _pp_engine(languages)(padded)
    if res.boxes is None or res.txts is None:
        return []
    out = []
    for box, text, conf in zip(res.boxes, res.txts, res.scores, strict=False):
        xs, ys = [p[0] - ox for p in box], [p[1] - oy for p in box]
        out.append(
            (
                str(text).translate(_SIMPLIFIED),
                float(conf),
                (round(min(xs) / scale), round(min(ys) / scale), round(max(xs) / scale), round(max(ys) / scale)),
            )
        )
    return out


def ocr_boxes(
    img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")
) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """每段文字、置信度和它在 img 中的外接框 (x1, y1, x2, y2)。空图（裁剪框落到画面外）返回 []，不抛异常（Mac h8 11-15：
    弹窗标题条贴着画面底边，正文裁出空图，cv2.resize 报 !ssize.empty()，主循环崩溃）。"""
    if img is None or getattr(img, "size", 0) == 0 or min(img.shape[:2]) < 1:
        return []
    eng = engine()
    if eng == "simd":
        from hiro.vision.simd_ocr import simd_engine

        return simd_engine().read(img, scale=scale)
    h, w = img.shape[:2]
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if eng == "vision":
        if _vision_fail.get("child"):
            return _vision_child_raw(img, w, h, languages)
        return _vision_raw(img, w, h, languages)
    if eng == "winocr":
        return _win_raw(img, scale, languages)
    if eng == "ppocr5":
        return _pp_raw(img, scale, languages)
    return _rapid_raw(img, scale, languages)


def ocr(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float]]:
    return [(t, c) for t, c, _ in ocr_boxes(img, scale=scale, languages=languages)]


def ocr_line(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> str:
    """已知只有一行字的框（比如 HUD 上固定位置的数字）：SIMD 后端跳过文字检测、只跑识别。"""
    if img is None or not img.size:
        return ""
    if engine() == "simd":
        from hiro.vision.simd_ocr import simd_engine

        return "".join(t for t, _, _ in simd_engine().read(img, scale=scale, only_rec=True))
    return "".join(t for t, _ in ocr(img, scale=scale, languages=languages))


# ---------------------------------------------------------------- 整帧 / 区域文字（页面判定、找按钮）

_CHAR_FIX = {"ó": "6", "ą": "a"}  # RapidOCR 日文模型的常见误读


def sanitize(text: str) -> str:
    """NFKC 规整（全角英数 / 括号转半角）+ 常见误读修正。整帧文字都过一遍，匹配时不用再管全角半角。"""
    text = unicodedata.normalize("NFKC", text)
    for k, v in _CHAR_FIX.items():
        text = text.replace(k, v)
    return text


class Point(NamedTuple):
    x: int
    y: int


class Box(NamedTuple):
    """整帧坐标的外接框 (x1, y1, x2, y2)。点它用 device.click(*box.center)。"""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1

    @property
    def center(self) -> Point:
        return Point(self.x1 + self.w // 2, self.y1 + self.h // 2)

    @property
    def xywh(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.w, self.h

    @property
    def top_left(self) -> Point:
        return Point(self.x1, self.y1)


class Text(NamedTuple):
    """一段识别出的文字：.text、.rect（Box，整帧坐标）、.conf。"""

    text: str
    rect: Box
    conf: float = 1.0


SCREEN_SCALE = 1.5  # 整帧放大倍数（Vision 整帧约 0.5s；3 倍太慢，1 倍小字读不全）


def _xywh(rect) -> tuple[int, int, int, int]:
    return tuple(int(v) for v in (rect.xywh if hasattr(rect, "xywh") else rect))  # type: ignore[return-value]


def screen_texts(img: MatLike | None, rect=None, *, scale: float = SCREEN_SCALE) -> list[Text]:
    """img 整帧（或 rect=(x, y, w, h) / 带 .xywh 的区域）里的文字，坐标是整帧坐标，自上而下、同行从左到右。
    img 为 None（测试替身）时返回空；识别出错由调用方处理。"""
    if img is None:
        return []
    ox = oy = 0
    if rect is not None:
        ox, oy, w, h = _xywh(rect)
        img = img[oy : oy + h, ox : ox + w]
    out = [Text(sanitize(t), Box(ox + x1, oy + y1, ox + max(x2, x1 + 1), oy + max(y2, y1 + 1)), c)
           for t, c, (x1, y1, x2, y2) in ocr_boxes(img, scale=scale)]  # fmt: skip
    return sorted(out, key=lambda b: (b.rect.center.y // 12, b.rect.center.x))


def find_text(img: MatLike | None, match: str | Callable[[str], bool], rect=None) -> Text | None:
    """screen_texts 里第一段匹配的文字：match 是字符串时要求包含它，是函数时按函数判。"""
    pred = (lambda t: match in t) if isinstance(match, str) else match
    return next((t for t in screen_texts(img, rect) if pred(t.text)), None)


def digits_image(img: MatLike, *, white_min: int = 200) -> MatLike:
    """白色描边数字 → 白底黑字：取接近白色的像素作为字形，加边框，便于识别小号数字。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, mask = cv2.threshold(gray, white_min, 255, cv2.THRESH_BINARY)
    inv = 255 - mask
    return cv2.copyMakeBorder(inv, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def ocr_digits(img: MatLike, *, white_min: int = 200, scale: float = 3.0) -> int | None:
    pre = digits_image(img, white_min=white_min)
    text = " ".join(t for t, _ in ocr(cv2.cvtColor(pre, cv2.COLOR_GRAY2BGR), scale=scale, languages=("en-US",)))
    text = text.replace(",", "").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_text(img: MatLike, **kw) -> str:
    return " ".join(t for t, _ in ocr(img, **kw))


def ocr_int(img: MatLike, **kw) -> int | None:
    text = ocr_text(img, **kw).replace(",", "").replace("O", "0").replace("o", "0")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_batch(crops: list[MatLike], *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[str]:
    """多块区域各自 OCR，返回每块按 Vision 结果顺序拼接的文字。

    试过把多块竖向拼成一张图只调一次 Vision：快约一半，但精度明显下降（突破线「64」读成「9A」、卡名的「+」
    读成「-十」，换放大倍数和间隔都不稳），线程并发也不会更快（Vision 内部串行）。所以仍逐块调用，
    省时间靠调用方的区域缓存（hiro.vision.region_cache）跳过没变的区域。"""
    return ["".join(t for t, _ in ocr(c, scale=scale, languages=languages)) for c in crops]
