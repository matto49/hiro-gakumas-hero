"""P 道具定制三选一（DMM 11-14 own2 seq 170）：定制页进场动画里候选还没出来，三格蓝色背景被认成三个「ロボ」，决策只好兜底。
识别：候选格没有候选背包（白色外框）时给 None；页面层：定制页先等三个候选出来再观测，决策给 Wait 时重读，上限 3 次后停下。"""

from types import SimpleNamespace as NS

import hiro.produce.hif as hif
from hiro.contracts import Action, ActionKind, Decision
from hiro.vision import pitem_custom as pc

import cv2
import numpy as np
import pytest


def _blue_slots():
    """进场动画那一帧的样子：候选格里只有蓝色背景（会被当成蓝色的ロボ）。"""
    hsv = np.zeros((1280, 720, 3), np.uint8)
    hsv[...] = (110, 200, 160)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_empty_slots_read_none_not_robo():
    img = _blue_slots()
    assert pc.plush_kinds(img) == ["ロボ"] * 3  # 只按颜色就会认成ロボ
    assert pc.candidates_present(img) == [False] * 3 and pc.custom_names(img, "黄") == [None] * 3


def _core(monkeypatch, decisions, frames):
    core = hif.HifCore.__new__(hif.HifCore)
    shots = iter(frames)
    commits, stops = [], []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: next(shots)))
    monkeypatch.setattr(hif, "screen_texts", lambda img: [NS(text="カスタム後を選択してください")])
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr("hiro.vision.extract.extract", lambda page, img: ({"options": []}, 0))
    monkeypatch.setattr("hiro.vision.pitem_custom.candidates_present", lambda img: [img == "ready"] * 3)
    monkeypatch.setattr(
        hif.HifCore, "_custom_options", lambda self, img, opts: [{"slot": i, "name": f"x{i}"} for i in range(3)]
    )
    monkeypatch.setattr("hiro.produce.card_ops.pitem_commit", lambda ctx, i: commits.append(i) or True)
    state = NS(page=NS(name="SELECT_PITEM"), page_fields={"pitems": [1]})
    core.s = NS(state=state, observe=lambda **kw: kw["img"], record=lambda *a, **kw: None)
    core.deciders = NS(decide=lambda page, st, **kw: decisions.pop(0))
    core._note_fallback = lambda page, d: None
    core._act = lambda fn, action, **kw: fn()

    def stop(page, why, **kw):
        stops.append(why)
        raise RuntimeError(why)

    core._stop = stop
    return core, commits, stops


WAIT = Decision(Action(ActionKind.WAIT, {}), "三个候选名字一样，像是还没出来", "rule", 0.5)
PICK = Decision(Action.activate_effect(2), "花 > リボン > 羽", "rule", 0.9)


def test_waits_for_candidates_then_rereads_on_wait(monkeypatch):
    frames = ["anim", "anim", "ready", "ready", "ready"]  # 进场动画两帧后候选出来；决策第一次 Wait，重读后选第 2 个
    core, commits, stops = _core(monkeypatch, [WAIT, PICK], frames)
    core.on_select_pitem(None)
    assert commits == [2] and not stops


def test_stops_after_three_waits(monkeypatch):
    core, commits, stops = _core(monkeypatch, [WAIT, WAIT, WAIT], ["ready"] * 3)
    with pytest.raises(RuntimeError):
        core.on_select_pitem(None)
    assert not commits and "重读 3 次" in stops[0]
