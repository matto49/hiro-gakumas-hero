from types import SimpleNamespace

from hiro.vision import simd_ocr as simd

import numpy as np


def test_empty_ocr_crop_never_loads_any_backend(monkeypatch):
    from hiro.vision import ocr

    def unexpected():
        raise AssertionError("empty crop must not load the backend")

    monkeypatch.setattr(ocr, "engine", unexpected)
    assert ocr.ocr_boxes(np.zeros((0, 20), dtype=np.uint8)) == []
    assert ocr.ocr_line(None) == ""


def test_helper_output_scales_boxes_back_and_clips():
    reader = simd.SimdOcr.__new__(simd.SimdOcr)

    def infer(img, only_rec):
        assert img.shape == (46, 74, 3) and not only_rec
        return [{"Text": "基本情報", "Score": 0.99, "X1": 10, "Y1": 12, "X2": 82, "Y2": 35}]

    reader._infer = infer
    assert reader.read(np.zeros((23, 37, 3), dtype=np.uint8), scale=2) == [("基本情報", 0.99, (5, 6, 37, 18))]


def test_kotonebot_find_and_only_rec_keep_absolute_rect(monkeypatch):
    from kotonebot.backend.ocr import Ocr
    from kotonebot.primitives.geometry import Rect

    seen = []

    def read(img, *, scale, only_rec):
        seen.append((img.shape[:2], only_rec))
        return [("次へ", 0.98, (0, 0, 30, 20) if only_rec else (2, 3, 13, 8))]

    monkeypatch.setattr(simd, "simd_engine", lambda: SimpleNamespace(read=read))
    monkeypatch.setattr(Ocr, "ocr", Ocr.ocr)
    simd.install_kotonebot.__wrapped__()
    ocr = Ocr(None)
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    roi = Rect(300, 500, 30, 20)
    found = ocr.find(image, "次へ", rect=roi)
    assert found.rect.xywh == (2, 3, 11, 5)
    assert found.original_rect.xywh == (302, 503, 11, 5)
    assert ocr.ocr(image, rect=roi, only_rec=True)[0].rect.xywh == roi.xywh
    assert seen == [((20, 30), False), ((20, 30), True)]


def test_merged_select_uses_only_verified_button_position(monkeypatch):
    from hiro.exec.exam import _select_markers
    from hiro.vision import ocr

    monkeypatch.setattr(ocr, "engine", lambda: "simd")
    monkeypatch.setattr(ocr, "ocr_line", lambda crop: "SELECT")
    merged = ocr.Text("カード名 SELECT", ocr.Box(100, 1096, 390, 1124))
    screen = np.zeros((1280, 720, 3), dtype=np.uint8)
    assert _select_markers(screen, [merged]) == []

    screen[1104:1120, 320:380] = (0, 150, 255)
    (hit,) = _select_markers(screen, [merged])
    assert hit.text == "SELECT"
    assert abs(hit.rect.center.x - 350) < 10

    monkeypatch.setattr(ocr, "ocr_line", lambda crop: "カード名")
    assert _select_markers(screen, [merged]) == []


def test_select_candidate_ocr_failure_does_not_abort_card_play(monkeypatch):
    """SIMD 辅助进程两次都失败时 ocr_line 会抛异常：这一个候选当作读不出，跳过，不能打断出牌（CodeRabbit #36）。"""
    from hiro.exec.exam import _select_markers
    from hiro.vision import ocr

    def broken(crop):
        raise RuntimeError("SIMD 辅助进程已退出")

    monkeypatch.setattr(ocr, "engine", lambda: "simd")
    monkeypatch.setattr(ocr, "ocr_line", broken)
    merged = ocr.Text("カード名 SELECT", ocr.Box(100, 1096, 390, 1124))
    screen = np.zeros((1280, 720, 3), dtype=np.uint8)
    screen[1104:1120, 320:380] = (0, 150, 255)
    assert _select_markers(screen, [merged]) == []


def _reader(starts, request):
    """不起真进程的 SimdOcr：_start 只记次数、换一个活着的假进程；_request 由用例给。"""
    from threading import RLock

    reader = simd.SimdOcr.__new__(simd.SimdOcr)
    reader.lock = RLock()

    def start():
        starts.append(1)
        reader.proc = SimpleNamespace(poll=lambda: None, returncode=None)

    reader._start = start
    reader._request = request
    reader.close = lambda: None
    return reader


def test_helper_restarts_after_it_exited():
    """review #9：辅助进程退出后（自己崩了 / 上次读超时被关掉），下一次识别自动重新拉起，不会一直报错。"""
    starts = []
    reader = _reader(starts, lambda line: [{"Text": "ok"}])
    reader.proc = SimpleNamespace(poll=lambda: 1, returncode=1)  # 已退出
    assert reader._infer(np.zeros((2, 2, 3), np.uint8), False) == [{"Text": "ok"}]
    assert starts == [1]


def test_helper_restarts_and_retries_once_on_failure():
    """识别中途超时 / 子进程退出：关掉、重启，再试一次；第二次还失败才往外抛。"""
    import pytest

    starts, calls = [], []

    def flaky(line):
        calls.append(line)
        if len(calls) == 1:
            raise RuntimeError("SIMD OCR 子进程已退出")
        return [{"Text": "ok"}]

    reader = _reader(starts, flaky)
    reader.proc = SimpleNamespace(poll=lambda: None, returncode=None)
    assert reader._infer(np.zeros((2, 2, 3), np.uint8), False) == [{"Text": "ok"}]
    assert starts == [1] and len(calls) == 2

    def always_dead(line):
        raise RuntimeError("SIMD OCR 子进程已退出")

    reader = _reader([], always_dead)
    reader.proc = SimpleNamespace(poll=lambda: None, returncode=None)
    with pytest.raises(RuntimeError):
        reader._infer(np.zeros((2, 2, 3), np.uint8), False)
