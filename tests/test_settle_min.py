"""考试稳帧：截图很快的后端（DMM PrintWindow 约 0.19s）3 次读数不到 1 秒就判稳，落在两段动画之间的停顿里，
回合还没切完就决策（dmm1m-r3：190 次决策 23 次「决策帧已过时」）。HIRO_SETTLE_MIN_S 要求一致读数持续够久。"""

import importlib.util as _iu

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(_iu.find_spec("kotonebot") is None or _iu.find_spec("kaa") is None, reason="要装 KAA / kotonebot")


def _run(monkeypatch, min_s, shot_s=0.19):
    from types import SimpleNamespace as NS

    from hiro.exec import exam as ex

    t = [0.0]
    img = np.zeros((1280, 720, 3), np.uint8)
    reads = []

    def shot():
        t[0] += shot_s
        return img

    monkeypatch.setattr(ex, "device", NS(screenshot=shot))
    monkeypatch.setattr(ex, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(ex, "_dialog_top", lambda im: None)
    monkeypatch.setattr(ex, "_anim_tap", lambda im: None)
    monkeypatch.setattr(ex, "_lite_sig", lambda im: (reads.append(t[0]), ((9, 100, 50), ((1, 2),)))[1])
    monkeypatch.setattr(ex, "Frame", lambda im: "frame")
    monkeypatch.setattr(ex, "_usable_fast", lambda f: True)
    monkeypatch.setattr(ex, "SETTLE_MIN_S", min_s)
    f = ex._settle_fast(img, clock=lambda: t[0])
    return f, reads


def test_default_three_reads(monkeypatch):
    f, reads = _run(monkeypatch, 0)
    assert f == "frame" and len(reads) == 3 and reads[-1] - reads[0] < 1.0  # 原行为：DMM 上 0.7 秒就判稳


def test_min_duration_waits(monkeypatch):
    f, reads = _run(monkeypatch, 1.5)
    assert f == "frame" and reads[-1] - reads[0] >= 1.5 and len(reads) > 3


def test_min_duration_capped_by_settle_max(monkeypatch):
    from hiro.exec import exam as ex

    f, reads = _run(monkeypatch, ex.SETTLE_FAST_MAX + 1)
    assert f is None  # 要求比上限还长：照旧超时交回 _stable_frame
