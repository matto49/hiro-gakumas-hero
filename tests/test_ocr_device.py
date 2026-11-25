"""RapidOCR 推理设备选择（HIRO_OCR_DEVICE）：auto 按 CUDA → DirectML → CPU，装了哪种 onnxruntime 就有哪种；最后总有 CPU 兜底。"""

from hiro.vision import ocr

import pytest

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


def test_dml_device_env(monkeypatch):
    """HIRO_OCR_DML_DEVICE：DirectML 用第几块显卡。rapidocr 固定用 0 号，Windows 台式机（11-24）0 号是核显 / 虚拟显卡，
    比 CPU 还慢；RTX 5060 是 1 号，整帧检测快 10 倍。不设时保持原样（返回 None，不改 rapidocr 的设置）。"""
    monkeypatch.delenv("HIRO_OCR_DML_DEVICE", raising=False)
    assert ocr.dml_device_id() is None
    monkeypatch.setenv("HIRO_OCR_DML_DEVICE", "1")
    assert ocr.dml_device_id() == 1
    monkeypatch.setenv("HIRO_OCR_DML_DEVICE", "x")
    with pytest.raises(ValueError):
        ocr.dml_device_id()


def test_set_dml_device_only_touches_directml():
    eps = [
        ("DmlExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
        ("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
    ]
    got = ocr.set_dml_device(eps, 1)
    assert got[0] == ("DmlExecutionProvider", {"arena_extend_strategy": "kSameAsRequested", "device_id": 1})
    assert got[1] == eps[1] and "device_id" not in eps[0][1]  # 不改原列表
    assert ocr.set_dml_device(eps, None) == eps
