"""初采样驱动（tools/sample_hajime.py）的存帧逻辑：画面变了 / KAA 页面变了才存，原尺寸，frames.jsonl；3 分钟不动就停。"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

pytestmark = [pytest.mark.kaa]


def _mod():
    p = Path(__file__).resolve().parents[1] / "tools" / "sample_hajime.py"
    spec = importlib.util.spec_from_file_location("sample_hajime", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_sampler_saves_on_change_and_scene_and_stalls(tmp_path):
    m = _mod()
    now = [0.0]
    s = m.Sampler(tmp_path / "frames", diff=4.0, clock=lambda: now[0])
    raws = iter([np.full((1838, 1032, 3), v, np.uint8) for v in (10, 10, 200, 200, 200)])
    dev = NS(screenshot_raw=lambda: next(raws), screenshot_hook_after=None)
    s.wrap_raw(dev)
    logical = lambda v: np.full((1280, 720, 3), v, np.uint8)  # noqa: E731
    for v, scene in ((10, "start"), (10, "start"), (200, "start"), (200, "ACTION_SELECT")):
        dev.screenshot_raw()
        s.scene = scene
        assert dev.screenshot_hook_after(logical(v)) is not None
        now[0] += 1
    rows = [json.loads(l) for l in (tmp_path / "frames" / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["scene"] for r in rows] == ["start", "start", "ACTION_SELECT"]  # 第二张没变不存；页面类型变了就存
    assert rows[0]["native"] == [1032, 1838] and (tmp_path / "frames" / rows[0]["file"]).exists()  # 原尺寸
    assert rows[0]["file"].endswith(".jpg")  # JPEG q95（PNG 一局 4.1 GB 把盘写满）
    import cv2

    assert cv2.imread(str(tmp_path / "frames" / rows[0]["file"])).shape == (1838, 1032, 3)
    dev.screenshot_raw()
    now[0] += m.STALL_S
    with pytest.raises(m.SampleStall):
        dev.screenshot_hook_after(logical(200))


def test_blind_choice_three_hits_saves_frame_and_stops(tmp_path, monkeypatch):
    """kaa_patches 拦下 KAA 盲点的选项页（BLIND_CHOICE 置位）：8 秒内第 3 次就存一帧、抛 SampleBlocked；隔得久的不累计。"""
    m = _mod()
    flag = {"pending": False}
    monkeypatch.setitem(__import__("sys").modules, "hiro.loop.kaa_patches", NS(BLIND_CHOICE=flag))
    import hiro.loop as hl

    monkeypatch.setattr(hl, "kaa_patches", __import__("sys").modules["hiro.loop.kaa_patches"], raising=False)
    now = [0.0]
    s = m.Sampler(tmp_path / "frames", diff=4.0, clock=lambda: now[0])
    img = np.full((1280, 720, 3), 50, np.uint8)
    s.after(img)  # 第一张照常存
    for t in (1.0, 20.0, 21.0):  # 第 1 次和后两次隔了 19 秒：不够 3 次
        now[0] = t
        flag["pending"] = True
        s.after(img)
        assert flag["pending"] is False
    now[0] = 22.0
    flag["pending"] = True
    with pytest.raises(m.SampleBlocked):
        s.after(img)
    rows = [json.loads(l) for l in (tmp_path / "frames" / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["scene"] == "BLIND_CHOICE" and len(rows) == 2  # 画面没变也存这一帧


def test_resume_enters_and_runs_kaa_controller(monkeypatch):
    """--resume：enter_produce 进培育，再用 KAA 的 ProduceController + StandardStrategy（场景按 --difficulty，is_resumed=True）。"""
    import sys

    m = _mod()
    calls = []
    monkeypatch.setitem(sys.modules, "hiro.loop.resume", NS(enter_produce=lambda: calls.append("enter") or "resumed"))
    monkeypatch.setitem(sys.modules, "kaa.kaa_context", NS(
        produce_solution=lambda: NS(data=NS(card_deck_id="deck-1")),
        init_produce_session=lambda sess: calls.append(("session", sess.kw)),
        clear_produce_session=lambda: calls.append("clear")))  # fmt: skip
    monkeypatch.setitem(sys.modules, "kaa.tasks.produce.session", NS(ProduceSession=lambda **kw: NS(kw=kw)))

    class Controller:
        def __init__(self, scenario, strategy):
            calls.append(("controller", scenario, strategy))

        def run(self):
            calls.append("run")

    pm = NS(resolve_deck=lambda idol, deck_id: f"{idol}/{deck_id}", ProduceController=Controller, StandardStrategy="std")
    m.resume(pm, "REGULAR", "i_card-x")
    assert calls == ["enter", ("session", {"idol_card": "i_card-x", "scenario": "REGULAR", "is_resumed": True, "deck": "i_card-x/deck-1"}),
                     ("controller", "REGULAR", "std"), "run", "clear"]  # fmt: skip


def test_landscape_guard_ticked_with_native_frame_and_no_stall_in_live(tmp_path):
    """每次截图都用原尺寸帧喂 LandscapeGuard（横屏 LIVE 黑屏切全屏）；LIVE 中画面不变也不按 STALL_S 停。"""
    m = _mod()
    now = [0.0]
    seen = []

    class Guard:
        live = True

        def tick(self, frame):
            seen.append(frame.shape)
            return False

        def in_live(self, frame):
            return self.live

    g = Guard()
    s = m.Sampler(tmp_path / "frames", diff=4.0, clock=lambda: now[0], landscape=g)
    raw = np.zeros((1032, 1838, 3), np.uint8)  # 横屏原尺寸
    dev = NS(screenshot_raw=lambda: raw, screenshot_hook_after=None)
    s.wrap_raw(dev)
    logical = np.zeros((1280, 720, 3), np.uint8)
    for _ in range(3):
        dev.screenshot_raw()
        dev.screenshot_hook_after(logical)
        now[0] += m.STALL_S
    assert seen == [(1032, 1838, 3)] * 3  # 原尺寸，不是逻辑分辨率
    g.live = False
    dev.screenshot_raw()
    now[0] += m.STALL_S
    with pytest.raises(m.SampleStall):  # LIVE 结束后照常按 STALL_S 停
        dev.screenshot_hook_after(logical)
