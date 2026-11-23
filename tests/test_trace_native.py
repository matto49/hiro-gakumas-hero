"""运行元信息与原生帧开关（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.6）：
run.json 记设备 / 截图方式 / OCR 后端 / 客户区原生尺寸；HIRO_TRACE_NATIVE=1 时存帧另存一张原生分辨率 PNG（缺省关、不改 trace 目录）。"""

import json

import numpy as np
import pytest

from hiro.contracts.trace import TraceWriter
from hiro.devices.base import LogicalScreen

pytestmark = pytest.mark.synthetic  # 图是测试里画的，不读 tests/fixtures


def _native(w=540, h=960):
    img = np.zeros((h, w, 3), np.uint8)
    img[100:200, 50:300] = 255
    return img


def test_run_json_records_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    monkeypatch.setenv("HIRO_SCREENSHOT", "windows_background")
    tw = TraceWriter(root=tmp_path, name="r1")
    plat = json.loads((tw.dir / "run.json").read_text(encoding="utf-8"))["platform"]
    assert plat["device"] == "dmm" and plat["screenshot"] == "windows_background" and "ocr" in plat and plat["native"] is None


def test_first_frame_fills_native_size(tmp_path, monkeypatch):
    monkeypatch.delenv("HIRO_TRACE_NATIVE", raising=False)
    screen = LogicalScreen()
    logical = screen.frame(_native())
    tw = TraceWriter(root=tmp_path, name="r2")
    tw.save_frame(tw.next_seq(), logical)
    plat = json.loads((tw.dir / "run.json").read_text(encoding="utf-8"))["platform"]
    assert plat["native"] == [540, 960]
    assert sorted(p.name for p in (tw.dir / "frames").iterdir()) == ["00001.jpg"]  # 缺省不另存原生图


def test_native_switch_saves_native_png(tmp_path, monkeypatch):
    import cv2

    monkeypatch.setenv("HIRO_TRACE_NATIVE", "1")
    screen = LogicalScreen()
    logical = screen.frame(_native())
    tw = TraceWriter(root=tmp_path, name="r3")
    tw.save_frame(tw.next_seq(), logical)
    native = cv2.imread(str(tw.dir / "frames" / "00001.native.png"))
    assert native is not None and native.shape[:2] == (960, 540)
    other = np.zeros((1280, 720, 3), np.uint8)  # 不是刚截的那一帧（处理器自己画的图）：不另存
    tw.save_frame(tw.next_seq(), other)
    assert not (tw.dir / "frames" / "00002.native.png").exists()


def test_resume_segment_keeps_first_run_and_records_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    TraceWriter(root=tmp_path, name="r4", meta={"idol": "x"})
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    tw = TraceWriter(root=tmp_path, name="r4")
    run = json.loads((tw.dir / "run.json").read_text(encoding="utf-8"))
    assert run["idol"] == "x" and run["platform"]["device"] == "mumu" and run["segments"][-1]["platform"]["device"] == "dmm"
