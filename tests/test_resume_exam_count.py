"""续跑后选拔场次（协调 2026-11-12）：dmmt2 选拔第 1 场考完后在饮料溢出页停机，续跑后第 2 场记成 qualify-1，
中转层按标签给 UINet 填 exam.step，第 2 场被当成第 1 场。现在：
- 续跑时从之前的 trace 段数已考完几场（停在考试中途的那场不算）；
- 考前说明页读到的「選抜試験N」记进 exam_session.brief_exam_no，标签取它和计数里大的那个。"""

import json
from types import SimpleNamespace as NS

from hiro.contracts import Observation

import pytest


def _seg(root, name, steps):
    d = root / name
    d.mkdir()
    with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
        for i, (page, label) in enumerate(steps, 1):
            f.write(
                json.dumps(
                    {"seq": i, "observation": {"page": {"name": page}}, "state": {"exam_session": {"label": label}}}
                )
                + "\n"
            )


def _strategy(tmp_path, cur):
    pytest.importorskip("kaa")

    from hiro.produce.hif import HifCore

    h = HifCore.__new__(HifCore)
    h.s = NS(trace=NS(dir=str(tmp_path / cur)))
    h.scenario = "qualify"
    return h


def test_resume_after_exam_counts_it_done(tmp_path):
    """dmmt2：第 1 场考完，停在饮料溢出页 → 已考完 1 场。"""
    _seg(
        tmp_path,
        "qualifyX-1",
        [("ACTION_SELECT", None), ("HIF_EXAM", "qualify-1"), ("HIF_EXAM", "qualify-1"), ("PDRINK_MAX", "qualify-1")],
    )
    (tmp_path / "qualifyX-1-r1").mkdir()
    assert _strategy(tmp_path, "qualifyX-1-r1")._exams_done_from_trace() == 1


def test_resume_mid_exam_does_not_count_it(tmp_path):
    """停在第 2 场考试中途 → 续跑会重进第 2 场，已考完只算 1 场。"""
    _seg(tmp_path, "qualifyY-1", [("HIF_EXAM", "qualify-1"), ("ACQUIRE", "qualify-1")])
    _seg(
        tmp_path,
        "qualifyY-1-r1",
        [("ACTION_SELECT", "qualify-1"), ("HIF_EXAM", "qualify-2"), ("HIF_EXAM", "qualify-2")],
    )
    (tmp_path / "qualifyY-1-r2").mkdir()
    assert _strategy(tmp_path, "qualifyY-1-r2")._exams_done_from_trace() == 1


def test_fresh_run_has_no_prior_exams(tmp_path):
    (tmp_path / "qualifyZ-1").mkdir()
    assert _strategy(tmp_path, "qualifyZ-1")._exams_done_from_trace() == 0


def test_briefing_exam_no_reaches_state():
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce

    obs = Observation.from_dict({"seq": 1, "t": 0.0, "page": {"name": "HIF_EXAM_BRIEFING", "area": "produce", "confidence": 1.0, "stable_frames": 1},
                                 "fields": {"exam_no": 2, "multiplier_pct": {"Vo": 120}}, "source": "screen"})  # fmt: skip
    assert reduce(GameState(), obs).exam_session.brief_exam_no == 2


def test_label_takes_briefing_when_counter_lags(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif
    from hiro.contracts import GameState

    s = hif.HifCore.__new__(hif.HifCore)
    s.scenario, s.exam_count = "qualify", 0  # 续跑后计数从头算
    st = GameState()
    st.exam_session.brief_exam_no = 2
    s.s = NS(state=st)
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr("hiro.vision.hif.read_hif_exam", lambda img: {"participants": None})  # DMM 上常读不到突破线人数
    monkeypatch.setattr("hiro.vision.scene.classify_current", lambda: NS(scene="HIF_EXAM"))
    assert s._battle_label(NS()) == "qualify-2"
    st.exam_session.brief_exam_no = 1  # 旧说明页不把计数往回拉
    s.exam_count = 2
    assert s._battle_label(NS()) == "qualify-3"
