"""RapidOCR 推理设备选择（HIRO_OCR_DEVICE）：auto 按 CUDA → DirectML → CPU，装了哪种 onnxruntime 就有哪种；最后总有 CPU 兜底。"""

import pytest

from hiro.vision import ocr

CPU = ["CPUExecutionProvider"]
DML = ["DmlExecutionProvider", "CPUExecutionProvider"]
CUDA = ["CUDAExecutionProvider", "CPUExecutionProvider"]


@pytest.mark.parametrize(
    "want, available, order",
    [
        ("auto", CPU, ["cpu"]),
        ("auto", DML, ["dml", "cpu"]),
        ("auto", CUDA, ["cuda", "cpu"]),
        ("", DML, ["dml", "cpu"]),
        ("cpu", DML, ["cpu"]),
        ("dml", DML, ["dml", "cpu"]),
        ("cuda", DML, ["cpu"]),  # 要 CUDA 但装的是 DirectML 版：退回 CPU
        ("DML", DML, ["dml", "cpu"]),
    ],
)
def test_device_order(want, available, order):
    assert ocr.ocr_device_order(want, available) == order


def test_device_order_rejects_typo():
    with pytest.raises(ValueError):
        ocr.ocr_device_order("gpu", DML)


def test_threads_env(monkeypatch):
    monkeypatch.setenv("HIRO_OCR_THREADS", "4")
    assert ocr._ocr_threads() == 4
    monkeypatch.setenv("HIRO_OCR_THREADS", "x")
    assert ocr._ocr_threads() == -1
    monkeypatch.delenv("HIRO_OCR_THREADS")
    assert ocr._ocr_threads() == -1
