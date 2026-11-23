"""运行报告的每轮得分：考试画面最后一帧是打出最后一张牌之前的分数（GKMS 第五局 458,946 实为 629,767），有本战结果页就用结果页。"""

from hiro.contracts.game_state import ExamSession, GameState, PageState
from hiro.contracts.observation import Observation, PageObs
from hiro.contracts.trace import Step
from hiro.loop.report import summarize


def _exam(seq, label, score):
    st = GameState(
        page=PageState(name="HIF_EXAM"), exam={"score": score}, exam_session=ExamSession(label=label, turn=1)
    )
    return Step(seq=seq, t=0.0, observation=Observation(seq=seq, t=0.0, page=PageObs(name="HIF_EXAM")), state=st)


def _result(seq, fields):
    st = GameState(page=PageState(name="HIF_FINALS_RANKING"))
    return Step(
        seq=seq,
        t=0.0,
        observation=Observation(seq=seq, t=0.0, page=PageObs(name="HIF_FINALS_RANKING"), fields=fields),
        state=st,
    )


def test_round_score_from_result_page_last_attempt():
    steps = [
        _exam(1, "r1", 372038),
        _result(2, {"kind": "round_result", "round_result": 1, "finals_round": 1, "round_score": 526566}),
        _exam(3, "r2", 300000),
        _result(4, {"kind": "final", "finals_round": 2, "round_score": 419510}),  # 第一次挑战
        _exam(5, "r2", 405413),
        _result(6, {"kind": "final", "finals_round": 2, "round_score": 584964}),  # 再挑戦后采用的这次
    ]
    ex = summarize(steps)["exams"]
    assert ex["r1"]["score"] == 526566 and ex["r2"]["score"] == 584964


def test_without_result_page_keeps_exam_score():
    ex = summarize([_exam(1, "r1", 372038)])["exams"]
    assert ex["r1"]["score"] == 372038
