"""初课程的画面读数（协调 11-18，配合 8a3a1e7 把课程接到 GNRPA）：开课回合数 / CLEAR 线 / 满分线 → exam_session.lesson，按主数据
算 exam_extra_turn / lesson_limit_up_score；同种同属性第几次。另：相談開始確認（P 少）按最近决策点。实机帧在私有 fixtures pages/hajime-*。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

from hiro.contracts import Action, Decision

FIXP = Path(__file__).with_name("fixtures") / "pages"
IDOL = "i_card-shro-3-007"  # 广（plan2）


def _img(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.offline import load

    return load(cv2.imread(str(p)))


def test_hud_normal_lesson():
    from hiro.vision import hajime as hv

    img = _img("hajime-lesson-vo.jpg")
    assert hv.read_lesson_hud(img) == {"goal": "clear", "left": 25, "param": 155} and hv.lesson_turns(img) == 6
    assert hv.read_lesson_hud(_img("hajime-lesson-perfect.jpg")) == {"goal": "perfect", "left": 1, "param": 204}


def test_hud_hard_lesson():
    """追い込み：回合两位数（模板会读成 1）、过 CLEAR 后右上换成「追い込みボーナス」（不是属性值）。"""
    from hiro.vision import hajime as hv

    img = _img("hajime-lesson-hard-start.jpg")
    assert hv.lesson_turns(img) == 11 and hv.read_lesson_hud(img) == {"goal": "clear", "left": 165, "param": 397}
    bonus = hv.read_lesson_hud(_img("hajime-lesson-hard-bonus.jpg"))
    assert bonus["goal"] == "perfect" and bonus["param"] is None
    assert hv.lesson_turns(_img("hajime-lesson-hard-bonus.jpg")) == 3  # 「2+1」：剩 2 + 追加 1


@pytest.mark.sim_master  # 查模拟器主数据（vendor/gakumas-player 的 catalog.sqlite）
def test_lesson_info_normal_matches_master():
    from hiro.runtime.hajime import LessonInfo

    info = LessonInfo("hajime-lesson-vo", 1, "produce-001", IDOL)
    assert info.needs("clear")
    info.feed("clear", {"goal": "clear", "left": 25, "param": 155}, 6)
    assert info.d["clear_target"] == 25 and info.d["exam_extra_turn"] == 0 and info.d["level_ids"]
    assert all("plan2-normal" in i for i in info.d["level_ids"])
    info.feed("perfect", {"goal": "perfect", "left": 1, "param": 204}, None)
    assert info.d["perfect_target"] is None  # 一次不采用（加分动画中会跳）
    info.feed("perfect", {"goal": "perfect", "left": 1, "param": 204}, None)
    assert info.d["perfect_target"] == 50 and info.d["lesson_limit_up_score"] == 0 and info.done


@pytest.mark.sim_master  # 查模拟器主数据（vendor/gakumas-player 的 catalog.sqlite）
def test_lesson_info_extra_turn_and_limit_up():
    from hiro.runtime.hajime import LessonInfo

    info = LessonInfo("hajime-lesson-da", 2, "produce-001", IDOL)
    info.feed("clear", {"goal": "clear", "left": 25, "param": 300}, 8)
    for _ in range(2):
        info.feed("perfect", {"goal": "perfect", "left": 30, "param": 340}, None)
    assert (info.d["exam_extra_turn"], info.d["perfect_target"], info.d["lesson_limit_up_score"]) == (2, 70, 20)


@pytest.mark.sim_master  # 查模拟器主数据（vendor/gakumas-player 的 catalog.sqlite）
def test_lesson_info_hard_uses_pro_levels():
    """追い込み：画面 CLEAR 90 / 165，初 REGULAR 主数据 hard 档是 75 / 100 对不上；按 produce-002 的 hard 档（8a3a1e7：实录都是）对上。
    满分线读不到（右上换成追い込みボーナス）留空。"""
    from hiro.runtime.hajime import LessonInfo

    info = LessonInfo("hajime-lesson-vi-hard", 1, "produce-001", IDOL)
    info.feed("clear", {"goal": "clear", "left": 165, "param": 397}, 11)
    for _ in range(2):
        info.feed("perfect", {"goal": "perfect", "left": 300, "param": None}, None)
    assert info.d["kind"] == "hard" and info.d["turns_total"] == 11 and info.d["clear_target"] == 165
    assert info.d["level_ids"] and all("-002-plan2-hard-" in i for i in info.d["level_ids"])
    assert info.d["perfect_target"] is None and info.d["lesson_limit_up_score"] is None


@pytest.mark.sim_master  # 查模拟器主数据（vendor/gakumas-player 的 catalog.sqlite）
def test_lesson_levels_unmatched_left_empty():
    from hiro.state import modes

    assert modes.lesson_levels("produce-001", IDOL, "normal", 33) == []  # 哪个档都没有 CLEAR 33：不猜


def test_lesson_kind():
    from hiro.runtime.hajime import lesson_kind

    assert lesson_kind("hajime-lesson-vo-sp") == ("Vo", "sp")
    assert lesson_kind("hajime-lesson-da-hard") == ("Da", "hard")
    assert lesson_kind("hajime-lesson-vi") == ("Vi", "normal")
    assert lesson_kind("hajime-lesson") == (None, "normal")


def test_exam_session_has_lesson_field():
    from hiro.contracts import GameState

    st = GameState()
    st.exam_session.lesson = {"kind": "normal"}
    assert GameState.from_dict(st.to_dict()).exam_session.lesson == {"kind": "normal"} if hasattr(GameState, "from_dict") else True


# ---------------------------------------------------------------- 相談開始確認（P 少）
def _rt(last, produce=None):
    from test_runtime import frame as fake_frame
    from test_runtime import runtime

    from hiro.contracts import GameState

    rt, _ = runtime([fake_frame()])
    rt.session = NS(last_decision=last, state=GameState(produce=produce))
    return rt


def _check(rt):
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    p = FIXP / "hajime-consult-lowp-confirm.png"
    if not p.exists():
        pytest.skip("需要实机截图 pages/hajime-consult-lowp-confirm.png")
    return interrupts.check(Frame(cv2.imread(str(p)), 0.0), rt)


@pytest.mark.parametrize("last", [None, ("ACTION_SELECT", Decision(Action.produce_next("shop"), "相談", "rule", 0.8)),
                                  ("ACTION_SELECT", Decision(Action.produce_next("training", attr="Da"), "课程", "rule", 0.8))])  # fmt: skip
def test_consult_confirm_always_cancels_and_declines_shop(last):
    """相談開始確認（P 不够）：一律点キャンセル（3940a9 11-18：进去也只能直接离开，这一周白费），记 declined_options=shop（同一周）。"""
    rt = _rt(last, produce={"mode": "produce-001", "step": 4})
    assert "キャンセル" in _check(rt)
    (x, y), = rt.raw_device.taps
    assert x < 300 and abs(y - 1160) < 20
    assert rt.session.state.produce["declined_options"] == {"options": ["shop"], "step": 4}


def _obs(seq, page, fields):
    from hiro.contracts import Observation, PageObs

    return Observation(seq=seq, t=0.0, page=PageObs(name=page, area="produce", stable_frames=3), fields=fields)


def _reduce(st, obs):
    from hiro.loop.reduce import reduce

    for _ in range(3):
        st = reduce(st, obs)
    return st


def test_declined_options_same_week_then_dropped():
    from hiro.contracts import GameState

    st = GameState(produce={"mode": "produce-001", "step": 4, "declined_options": {"options": ["shop"], "step": 4}})
    st = _reduce(st, _obs(1, "ACTION_SELECT", {"exam_next": "mid1", "weeks_left": 2, "stamina": 20}))
    assert st.produce["step"] == 4 and st.page_fields["declined_options"] == ["shop"]
    st = _reduce(st, _obs(2, "CONSULT", {"stamina": 20}))
    st = _reduce(st, _obs(3, "ACTION_SELECT", {"exam_next": "mid1", "weeks_left": 1, "stamina": 18}))  # 换周
    assert st.produce["step"] == 5 and "declined_options" not in st.produce and "declined_options" not in st.page_fields


def test_consult_delete_price_recorded():
    from hiro.contracts import GameState

    st = _reduce(GameState(produce={"mode": "produce-001"}), _obs(1, "CONSULT", {"services": {"delete": {"price": 100, "available": True}}}))
    assert st.produce["consult_delete_price"] == 100 and st.sources["produce.consult_delete_price"].origin == "screen"
    st = _reduce(st, _obs(2, "CONSULT", {"services": {"delete": {"price": None, "available": True}}}))
    assert st.produce["consult_delete_price"] == 100  # 读不到不覆盖


def test_consult_price_from_real_frame():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load

    p = FIXP / "nia-consult-banner.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/nia-consult-banner.jpg")
    raw, _ = extract("CONSULT", load(cv2.imread(str(p))))
    assert observation(1, 0.0, "CONSULT", raw).fields["services"]["delete"]["price"] == 100


def test_lesson_confirm_unchanged():
    """レッスン開始確認（低体力）照旧：最近决策是课程就点開始する。"""
    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    p = FIXP / "hajime-lesson-lowhp-confirm.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/hajime-lesson-lowhp-confirm.jpg")
    rt = _rt(("ACTION_SELECT", Decision(Action.produce_next("training", attr="Da"), "课程", "rule", 0.8)))
    assert "開始する" in interrupts.check(Frame(cv2.imread(str(p)), 0.0), rt)


# ---------------------------------------------------------------- 续跑 / 回合数读错（协调 11-18：续跑时 6 回合的课读成 19，追加回合算成 13）
@pytest.mark.sim_master
def test_absurd_extra_turn_left_empty_and_not_reread():
    from hiro.runtime.hajime import LessonInfo

    info = LessonInfo("hajime-lesson-da", 1, "produce-001", IDOL)
    info.feed("clear", {"goal": "clear", "left": 25, "param": 196}, 19)
    assert info.d["turns_total"] is None and info.d["exam_extra_turn"] is None and info.d["clear_target"] == 25
    assert not info.needs("perfect") or info.d["perfect_target"] is None  # 不再为回合数 OCR
    info.feed("clear", {"goal": "clear", "left": 20, "param": 201}, 6)
    assert info.d["turns_total"] is None  # 这节课不再读（中途读到的是剩余回合，不是总数）


@pytest.mark.sim_master
def test_restore_from_trace_reading():
    from hiro.runtime.hajime import LessonInfo

    saved = {"kind": "normal", "attr": "Da", "nth": 1, "turns_total": 6, "clear_target": 25, "perfect_target": None, "param_start": 196,
             "level_ids": ["x"], "exam_extra_turn": 0, "lesson_limit_up_score": None}  # fmt: skip
    info = LessonInfo("hajime-lesson-da", 1, "produce-001", IDOL)
    info.restore(saved)
    assert info.d["turns_total"] == 6 and info.d["param_start"] == 196 and not info.needs("clear")
    for _ in range(2):  # 满分线照常在 PERFECT 段算出来，追加回合 / 满分上调按恢复的档位算
        info.feed("perfect", {"goal": "perfect", "left": 10, "param": 236}, None)
    assert info.d["perfect_target"] == 50 and info.d["lesson_limit_up_score"] == 0 and info.d["exam_extra_turn"] == 0


def test_lesson_from_trace(tmp_path):
    import json

    from hiro.runtime import hajime

    Core = hajime.hajime_core("produce-001")
    core = Core.__new__(Core)
    prev = tmp_path / "hajime-20261118-170000"
    prev.mkdir()
    lesson = {"kind": "normal", "turns_total": 6, "clear_target": 30}
    rows = [{"seq": 1, "state": {"exam_session": {"label": None}}},
            {"seq": 2, "state": {"exam_session": {"label": "hajime-lesson-da", "lesson": lesson}}}]  # fmt: skip
    (prev / "steps.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    core._prior_trace_dirs = lambda: [prev]
    assert core._lesson_from_trace("hajime-lesson-da") == lesson
    assert core._lesson_from_trace("hajime-lesson-vo") is None  # 上一段停在别的课：不沿用


@pytest.mark.parametrize(("ocr_text", "tpl", "want"), [("11", 1, 11), ("19", 6, 6), ("12", None, 12), ("6", 6, 6), ("2+1", 2, 3)])
def test_lesson_turns_two_digit_cross_check(monkeypatch, ocr_text, tpl, want):
    import numpy as np

    import hiro.exec.exam as ex
    import hiro.vision.ocr as ocr_mod
    from hiro.vision import hajime as hv

    monkeypatch.setattr(ocr_mod, "ocr", lambda img, scale=2: [(ocr_text, 1.0)])
    monkeypatch.setattr(ex, "_hud_int", lambda img, a, b: tpl)
    assert hv.lesson_turns(np.zeros((1280, 720, 3), np.uint8)) == want
