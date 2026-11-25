"""HIRO_OCR=auto 选后端：macOS 用 Apple Vision；Windows 装好了 SIMD（辅助程序 + 模型 + .NET 10，能启动）就用 SIMD，
否则退回 RapidOCR（11-24 识别测试集：Windows 上 SIMD + Vulkan 准确率和 Vision 差不到 1 个点、比 RapidOCR CPU 快 5–6 倍）。"""

from hiro.vision import ocr, simd_ocr

import pytest

pytestmark = pytest.mark.synthetic


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.delenv("HIRO_OCR", raising=False)


def _boom():
    raise RuntimeError("HIRO_OCR=simd 缺少 HiroSimdOcr.dll")


def test_windows_auto_uses_simd_when_installed(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(simd_ocr, "simd_engine", lambda: object())
    assert ocr.engine() == "simd"


def test_windows_auto_falls_back_to_rapidocr_without_simd(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(simd_ocr, "simd_engine", _boom)
    assert ocr.engine() == "rapidocr"


def test_linux_auto_stays_rapidocr(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    monkeypatch.setattr(simd_ocr, "simd_engine", lambda: object())
    assert ocr.engine() == "rapidocr"


def test_explicit_choice_wins_on_windows(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setenv("HIRO_OCR", "rapidocr")
    monkeypatch.setattr(simd_ocr, "simd_engine", lambda: object())
    assert ocr.engine() == "rapidocr"
