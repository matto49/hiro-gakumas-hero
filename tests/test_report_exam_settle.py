"""运行报告的考试判稳汇总（exam_settle）：settle_fast 用时分布、等满没稳次数、决策帧过时次数、重新决策次数，取自各步 timings_ms 的 phase_*。"""

from hiro.contracts.game_state import GameState, PageState
from hiro.contracts.observation import Observation, PageObs
from hiro.contracts.trace import Step
from hiro.loop.report import markdown, summarize


def _step(seq, page, timings):
    st = GameState(page=PageState(name=page))
    return Step(
        seq=seq, t=0.0, observation=Observation(seq=seq, t=0.0, page=PageObs(name=page)), state=st, timings_ms=timings
    )


def test_exam_settle_summary_and_markdown():
    steps = [
        _step(1, "ACTION_SELECT", {"total": 100}),  # 非考试步不算
        _step(2, "HIF_EXAM", {"phase_settle_fast": 600.0, "phase_unstable_settle_timeout": 1}),
        _step(3, "HIF_EXAM", {"phase_settle_fast": 900.0, "phase_unstable_stale_frame": 1, "phase_redo_n": 2}),
        _step(4, "HIF_EXAM", {"phase_settle_fast": 300.0, "phase_unstable_settle_timeout": 1}),
    ]
    r = summarize(steps)
    st = r["exam_settle"]
    assert st["n"] == 3 and st["settle_fast_ms"] == {"median": 600.0, "p90": 900.0, "max": 900.0}
    assert st["settle_timeout"] == 2 and st["stale_frames"] == 1 and st["redo"] == 2
    assert "settle_fast 中位 600.0 ms" in markdown({**r, "trace": "t"}) and "决策帧过时 1 次" in markdown(
        {**r, "trace": "t"}
    )


def test_no_exam_steps():
    r = summarize([_step(1, "ACTION_SELECT", {})])
    assert r["exam_settle"]["n"] == 0 and "没有考试步" in markdown({**r, "trace": "t"})
