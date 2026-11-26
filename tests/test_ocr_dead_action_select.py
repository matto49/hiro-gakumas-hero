"""G1-F7（11-26 初 REGULAR 中考后）：同一进程里 Apple Vision 坏掉，选行动页 options / 体力 / P 点全读空（属性数值走字体比对照样有），
决策 Wait、重读 3 次都一样，报成「没有可选的行动」停机；重开进程同一画面正常。
要求：Vision 请求失败不再静默；选行动 Wait 停机前做 OCR 自检，坏了报 OcrUnavailable（可重开进程续跑），没坏仍报原来的 UnhandledPage。"""

import sys
from types import SimpleNamespace as NS

import hiro.vision.ocr as ocr
from hiro.produce.hif import HifCore, OcrUnavailable, UnhandledPage
from tests.test_action_select_wait import WAIT, _st

import numpy as np
import pytest


def _fake_vision(monkeypatch, ok: bool):
    """假的 Vision / Foundation 模块：performRequests 按 ok 返回 (True, None) 或 (False, NSError)，results() 失败时为 None。"""

    class Req:
        def setRecognitionLevel_(self, v): ...
        def setRecognitionLanguages_(self, v): ...
        def setUsesLanguageCorrection_(self, v): ...
        def results(self):
            return [] if ok else None

    class Handler:
        def initWithData_options_(self, data, opts):
            return self

        def performRequests_error_(self, reqs, err):
            return (True, None) if ok else (False, "Error Domain=com.apple.Vision Code=9 inference context")

    vision = NS(VNImageRequestHandler=NS(alloc=Handler), VNRecognizeTextRequest=NS(alloc=lambda: NS(init=Req)))
    monkeypatch.setitem(sys.modules, "Vision", vision)
    monkeypatch.setitem(sys.modules, "Foundation", NS(NSData=NS(dataWithBytes_length_=lambda b, n: b)))
    monkeypatch.setattr(ocr, "_vision_fail", {"streak": 0, "error": None})


def test_vision_request_failure_is_recorded_not_silent(monkeypatch):
    _fake_vision(monkeypatch, ok=False)
    img = np.zeros((10, 10, 3), np.uint8)
    assert ocr._vision_raw(img, 10, 10, ("en-US",)) == []
    assert ocr._vision_fail["streak"] == 1 and "inference context" in ocr._vision_fail["error"]
    _fake_vision(monkeypatch, ok=True)
    ocr._vision_fail["streak"] = 3
    ocr._vision_raw(img, 10, 10, ("en-US",))
    assert ocr._vision_fail["streak"] == 0  # 成功一次就清零


def test_ocr_health(monkeypatch):
    monkeypatch.setattr(ocr, "ocr_boxes", lambda img, **k: [("123", 1.0, (0, 0, 1, 1))])
    assert ocr.ocr_health() is None
    monkeypatch.setattr(ocr, "ocr_boxes", lambda img, **k: [])
    assert "读成" in ocr.ocr_health()


def test_dead_ocr_on_action_select_raises_ocr_unavailable(monkeypatch):
    st, ctx, calls, committed = _st(monkeypatch, [WAIT] * 10)
    monkeypatch.setattr(ocr, "ocr_health", lambda: "自检图读成 ''")  # _st 里缺省是健康的，这里改成坏了
    with pytest.raises(OcrUnavailable, match="文字识别失效"):
        st.on_action_select(ctx)
    assert committed == [] and len(calls) == 1 + HifCore.ACTION_WAIT_RETRIES  # 重读次数不变，只是停机原因分开


def test_healthy_ocr_keeps_old_stop(monkeypatch):
    st, ctx, _calls, _committed = _st(monkeypatch, [WAIT] * 10)
    with pytest.raises(UnhandledPage) as e:
        st.on_action_select(ctx)
    assert not isinstance(e.value, OcrUnavailable) and "仍是 Wait" in str(e.value)


def test_vision_failure_retries_on_cpu(monkeypatch):
    """神经引擎 / GPU 那条路失败时换 CPU 重试一次，成功就不算失败。"""

    class Req:
        def __init__(self):
            self.cpu = False

        def setRecognitionLevel_(self, v): ...
        def setRecognitionLanguages_(self, v): ...
        def setUsesLanguageCorrection_(self, v): ...
        def setUsesCPUOnly_(self, v):
            self.cpu = v

        def results(self):
            return [] if self.cpu else None

    class Handler:
        def initWithData_options_(self, data, opts):
            return self

        def performRequests_error_(self, reqs, err):
            return (True, None) if reqs[0].cpu else (False, "Code=9 inference context")

    monkeypatch.setitem(
        sys.modules,
        "Vision",
        NS(VNImageRequestHandler=NS(alloc=Handler), VNRecognizeTextRequest=NS(alloc=lambda: NS(init=Req))),
    )
    monkeypatch.setitem(sys.modules, "Foundation", NS(NSData=NS(dataWithBytes_length_=lambda b, n: b)))
    monkeypatch.setattr(ocr, "_vision_fail", {"streak": 0, "error": None})
    assert ocr._vision_raw(np.zeros((10, 10, 3), np.uint8), 10, 10, ("en-US",)) == []
    assert ocr._vision_fail["streak"] == 0


@pytest.mark.parametrize("rapid_ok", [True, False])
def test_vision_dead_falls_back_to_rapidocr(monkeypatch, rapid_ok):
    """同一进程里 Vision 连续失败 3 次：RapidOCR 能接手就切过去（这个进程余下的识别不再悄悄读空）；接不了就保持（停机前的自检会说清楚）。"""
    _fake_vision(monkeypatch, ok=False)
    monkeypatch.setattr(ocr, "_engine", "vision")
    monkeypatch.setattr(ocr, "_rapid_ready", lambda: rapid_ok)
    img = np.zeros((10, 10, 3), np.uint8)
    for _ in range(ocr.VISION_FALLBACK_AFTER):
        ocr._vision_raw(img, 10, 10, ("en-US",))
    assert ocr._engine == ("rapidocr" if rapid_ok else "vision")


def test_use_cpu_prefers_compute_device_api():
    """新系统上 setUsesCPUOnly_ 不再起作用：有 computeDevice 接口就按阶段指定 CPU 设备。"""

    class CPU:
        pass

    class ANE:
        pass

    set_calls = []

    class Req:
        def supportedComputeStageDevicesAndReturnError_(self, err):
            return ({"main": [ANE(), CPU()], "post": [CPU()]}, None)

        def setComputeDevice_forComputeStage_(self, dev, stage):
            set_calls.append((type(dev).__name__, stage))

        def setUsesCPUOnly_(self, v):
            set_calls.append(("old", v))

    ocr._use_cpu(Req())
    assert set_calls == [("CPU", "main"), ("CPU", "post")]
