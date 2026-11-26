"""N29（G1 第 3 轮 11-26）：Vision 在本进程坏了，HIRO_VISION_RESTART=1 时改到子进程里跑、坏了重启，不再整进程退到 RapidOCR。
子进程这里换成假的识别函数（不碰真 Vision：本机实机局在跑时 Vision 会互相弄坏）。"""

import hiro.vision.ocr as ocr
from hiro.vision import vision_child as vc

import numpy as np
import pytest

FAKE_OK = (
    "import hiro.vision.ocr as o\no._vision_request = lambda img, w, h, langs: ([('読めた', 0.9, (1, 2, w, h))], None)"
)
FAKE_DIE = "import hiro.vision.ocr as o, os\no._vision_request = lambda img, w, h, langs: os._exit(3)"
FAKE_ERR = "import hiro.vision.ocr as o\no._vision_request = lambda img, w, h, langs: ([], 'e5rtError 13')"


def _img():
    return np.full((20, 30, 3), 255, np.uint8)


def test_child_round_trip_and_stdout_noise_is_kept_out():
    c = vc.VisionChild(timeout=20, patch=FAKE_OK + "\nprint('PyObjC 的杂讯')")
    try:
        assert c.read(_img(), 10, 7, ("ja-JP",)) == ([("読めた", 0.9, (1, 2, 10, 7))], None)
        assert c.read(_img(), 3, 4, ("en-US",))[0][0][2] == (1, 2, 3, 4)  # 同一个子进程连着问
    finally:
        c.close()


def test_child_exit_raises_child_died():
    c = vc.VisionChild(timeout=20, patch=FAKE_DIE)
    try:
        with pytest.raises(vc.ChildDied, match="退出"):
            c.read(_img(), 10, 7, ("ja-JP",))
    finally:
        c.close()


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(ocr, "_vision_fail", {"streak": 0, "error": None, "child": False})
    monkeypatch.setattr(ocr, "_engine", "vision")
    monkeypatch.setattr(ocr, "_rapid_ready", lambda: True)
    yield
    vc.stop()


def test_switch_off_keeps_old_rapidocr_fallback(monkeypatch, fresh):
    monkeypatch.delenv("HIRO_VISION_RESTART", raising=False)
    for _ in range(ocr.VISION_FALLBACK_AFTER):
        ocr._note_vision("e5rtError 13")
    assert ocr.engine() == "rapidocr" and not ocr._vision_fail["child"]


def test_switch_on_moves_vision_to_child_and_keeps_engine_name(monkeypatch, fresh):
    monkeypatch.setenv("HIRO_VISION_RESTART", "1")
    for _ in range(ocr.VISION_FALLBACK_AFTER):
        ocr._note_vision("e5rtError 13")
    # 引擎名仍是 vision：各处 engine() != "vision" 的放宽匹配不该因为换了进程就打开
    assert ocr.engine() == "vision" and ocr._vision_fail["child"]
    monkeypatch.setattr(vc, "client", lambda: vc.VisionChild(timeout=20, patch=FAKE_OK))
    got = ocr.ocr_boxes(_img(), scale=1)
    assert got == [("読めた", 0.9, (1, 2, 30, 20))]


def test_child_keeps_failing_falls_back_to_rapidocr(monkeypatch, fresh):
    monkeypatch.setenv("HIRO_VISION_RESTART", "1")
    ocr._vision_fail["child"] = True
    started = []

    def client():
        started.append(1)
        c = vc.VisionChild(timeout=20, patch=FAKE_ERR)
        vc._child = c
        return c

    monkeypatch.setattr(vc, "client", client)
    monkeypatch.setattr(ocr, "_rapid_raw", lambda img, scale, langs: [("rapid", 1.0, (0, 0, 1, 1))])
    assert ocr.ocr_boxes(_img(), scale=1) == [("rapid", 1.0, (0, 0, 1, 1))]
    assert len(started) == ocr.CHILD_RESTARTS and ocr.engine() == "rapidocr" and not ocr._vision_fail["child"]
    assert vc._child is None  # 退到 RapidOCR 时把子进程关掉


def test_hung_child_times_out_and_is_killed():
    """#152 review：子进程卡住时，写请求 + 读回答整段受同一个截止时间管，超时先杀子进程。"""
    hang = "import hiro.vision.ocr as o, time\no._vision_request = lambda img, w, h, langs: time.sleep(60)"
    c = vc.VisionChild(timeout=3, patch=hang)
    try:
        with pytest.raises(vc.ChildDied, match="没回答"):
            c.read(_img(), 10, 7, ("ja-JP",))
        assert c.proc.poll() is not None
    finally:
        c.close()
