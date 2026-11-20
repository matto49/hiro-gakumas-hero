"""初 / N.I.A 局面的 step（协调 11-17，3940a9 查出实机局面没有 step → produce_state() 构造不出，选卡 / 选饮料 / 相談等全走兜底）：
选行动页头「中間 / 1次 / 2次 / 最終まで N週」→ step；续跑接上一段 trace 打底。实机帧在私有 fixtures pages/hajime-* / nia-*。"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

from hiro.state import modes

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _real(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.offline import load

    return load(cv2.imread(str(p)))


@pytest.mark.parametrize(("pid", "stage", "weeks", "step"), [
    ("produce-001", "mid1", 5, 1), ("produce-001", "mid1", 1, 5), ("produce-001", "final", 6, 7), ("produce-001", "final", 1, 12),
    ("produce-004", "mid1", 8, 1), ("produce-004", "mid2", 8, 10), ("produce-004", "mid2", 1, 17), ("produce-004", "final", 8, 19),
    ("produce-004", "final", 1, 26),
    ("produce-002", "mid1", 3, None),  # 初 PRO 中間的位置没有实机数据：不猜
    ("produce-007", "mid1", 3, None),  # H.I.F 不走这里（识别层直接给 step）
    ("produce-001", "final", 20, None),  # 算出来 < 1：不写
    (None, "final", 3, None), ("produce-001", None, 3, None), ("produce-001", "final", None, None),
])  # fmt: skip
def test_step_from_header(pid, stage, weeks, step):
    assert modes.step_from_header(pid, stage, weeks) == step


@pytest.mark.sim_master  # 查模拟器主数据（vendor/gakumas-player 的 catalog.sqlite）
def test_final_uses_master_steps():
    assert modes.exam_step("produce-002", "final") == modes.steps("produce-002")


@pytest.mark.parametrize(("name", "stage", "weeks"), [
    ("hajime-action-lessons.jpg", "mid1", 5), ("hajime-action-class-allowance.jpg", "mid1", 4), ("hajime-action-oikomi.jpg", "mid1", 1),
    ("nia-action-sales.png", "mid1", 2),
])  # fmt: skip
def test_header_weeks_read(name, stage, weeks):
    from hiro.vision.action import read_action_select

    a = read_action_select(_real(name))
    assert (a.exam_next, a.weeks_left) == (stage, weeks)


def test_weeks_ro_read_as_3():
    """OCR 把「3週」读成「ろ週」（初 / N.I.A 帧都有）。"""
    import numpy as np

    from hiro.vision.action import _weeks

    assert _weeks("中間まで ろ週", np.zeros((1280, 720, 3), np.uint8)) == 3
    assert _weeks("2次まで 8週", np.zeros((1280, 720, 3), np.uint8)) == 8


def _obs(seq, fields):
    from hiro.contracts import Observation, PageObs

    return Observation(seq=seq, t=0.0, page=PageObs(name="ACTION_SELECT", area="produce", stable_frames=3), fields=fields)


def _reduce(mode, *obs_list):
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce

    st = GameState(produce={"mode": mode})
    for o in obs_list:
        for _ in range(3):
            st = reduce(st, o)
    return st


def test_reduce_writes_step_for_hajime():
    st = _reduce("produce-001", _obs(1, {"exam_next": "final", "weeks_left": 3, "stamina": 20}))
    assert st.produce["step"] == 10 and st.sources["produce.step"].origin == "screen"
    assert "weeks_left" not in st.produce and st.page_fields["weeks_left"] == 3


def test_reduce_keeps_step_when_header_missing():
    st = _reduce("produce-004", _obs(1, {"exam_next": "mid2", "weeks_left": 5}), _obs(2, {"stamina": 20}))
    assert st.produce["step"] == 13


def test_reduce_hif_step_untouched():
    st = _reduce("produce-007", _obs(1, {"step": 4, "exam_next": "final", "weeks_left": 2}))
    assert st.produce["step"] == 4


def test_real_frame_to_step():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("ACTION_SELECT", _real("nia-action-sales.png"))
    st = _reduce("produce-004", observation(1, 0.0, "ACTION_SELECT", raw, stable_frames=3))
    assert st.produce["step"] == 7  # 1次まで 2週 → 9 − 2


# ---------------------------------------------------------------- 续跑接上一段 trace
def test_trace_name_new_and_given(tmp_path):
    from hiro.runtime.segments import trace_name

    now = datetime(2026, 11, 17, 20, 29, 31)
    assert trace_name("hajime", "new", root=tmp_path, now=now) == "hajime-20261117-202931"
    assert trace_name("hajime", "resume", "mine", root=tmp_path) == "mine"
    assert trace_name("hajime", "resume", root=tmp_path, now=now) == "hajime-20261117-202931"  # 没有上一段：新起


def test_trace_name_resume_continues_latest_segment(tmp_path):
    import os
    import time

    from hiro.runtime.segments import trace_name

    for i, n in enumerate(("hajime-20261117-190000", "hajime-20261117-202931", "hajime-20261117-202931-r1", "nia-20261117-230000")):
        (tmp_path / n).mkdir()
        t = time.time() - 100 + i
        os.utime(tmp_path / n, (t, t))
    assert trace_name("hajime", "resume", root=tmp_path) == "hajime-20261117-202931-r2"
    assert trace_name("nia", "resume", root=tmp_path) == "nia-20261117-230000-r1"


def test_resume_seeds_produce_from_previous_segment(tmp_path):
    """续跑段的 HifCore._prior_trace_dirs 找得到上一段，_seed_produce_from_trace 用它最后的 produce 打底（含 step）。"""
    from hiro.contracts import GameState
    from hiro.produce.hif import HifCore
    from hiro.runtime.segments import trace_name

    prev = tmp_path / "hajime-20261117-202931"
    prev.mkdir()
    prod = {"mode": "produce-001", "step": 9, "stamina": 18, "max_stamina": 30, "produce_point": 195, "params": {"Vo": 400, "Da": 450, "Vi": 300}}
    (prev / "steps.jsonl").write_text(json.dumps({"seq": 40, "state": {"produce": prod, "mode": "produce"}}) + "\n", encoding="utf-8")
    cur = tmp_path / trace_name("hajime", "resume", root=tmp_path)
    cur.mkdir()
    notes = []
    core = NS(s=NS(trace=NS(dir=cur), state=GameState(produce={"mode": "produce-001"}), note=notes.append))
    core._prior_trace_dirs = lambda: HifCore._prior_trace_dirs(core)
    assert core._prior_trace_dirs() == [prev]
    HifCore._seed_produce_from_trace(core)
    assert core.s.state.produce == prod and core.s.state.sources["produce.step"].origin == "infer" and notes
