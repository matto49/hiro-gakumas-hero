"""初（hiro.runtime.hajime）：选行动页的初读数、练习课属性 / 结束判据、标签、名次判过线。实机帧在私有 fixtures pages/hajime-*
（初 REGULAR Mac 采样 11-17）。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "pages"


def _img(name):
    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.offline import load

    return load(cv2.imread(str(p)))


def _action(name):
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("ACTION_SELECT", _img(name))
    return observation(1, 0, "ACTION_SELECT", raw).fields


def _opts(fields):
    return [(o["option"], o["attr"], o.get("sp"), o.get("oikomi")) for o in fields["options"]]


def test_lesson_week_options():
    f = _action("hajime-action-lessons.jpg")
    assert _opts(f) == [
        ("training", "Vo", False, None),
        ("training", "Da", False, None),
        ("training", "Vi", False, None),
    ]
    assert f["exam_next"] == "mid1" and f["complete"]
    f = _action("hajime-action-lessons-sp.jpg")
    assert [o for o in _opts(f) if o[0] == "training"] == [("training", "Vo", False, None), ("training", "Da", True, None),
                                                          ("training", "Vi", False, None)]  # fmt: skip
    assert f["exam_next"] == "final"


def test_class_has_no_attr_and_allowance_read():
    """初的「授業」只有一个按钮、不分属性（原来按右下小图标读成幻影 Da）；「活動支給」= allowance（原来读不出）。"""
    f = _action("hajime-action-class-allowance.jpg")
    assert _opts(f) == [("rest", None, None, None), ("class", None, None, None), ("allowance", None, None, None)]
    assert f["complete"]
    f = _action("hajime-action-allowance-shop.jpg")
    assert [o[0] for o in _opts(f)] == ["rest", "outing", "shop", "allowance"]


def test_oikomi_three_choices():
    """追い込み：三条「〜を重点的に」按左边图标颜色认属性，带 oikomi；三维数值是大号排版，单独读。"""
    f = _action("hajime-action-oikomi.jpg")
    assert _opts(f) == [("training", "Vo", None, True), ("training", "Da", None, True), ("training", "Vi", None, True)]
    assert [o["center"][1] for o in f["options"]] == pytest.approx([699, 800, 902], abs=15)
    assert f["params"] == {"Vo": 252, "Da": 260, "Vi": 180} and f["exam_next"] == "mid1" and f["complete"]


def test_oikomi_page_classified_action_select():
    from hiro.vision.classify import classify

    assert classify(_img("hajime-action-oikomi.jpg")).name == "ACTION_SELECT"


def test_exam_next_reaches_page_fields():
    """exam_next 是页面局部字段（3940a9 的决策器读 page_fields.exam_next），不进 produce。"""
    from hiro.loop.reduce import PAGE_LOCAL

    assert "exam_next" in PAGE_LOCAL


def test_lesson_hud():
    from hiro.vision.hajime import in_lesson, lesson_attr

    assert lesson_attr(_img("hajime-lesson-vo.jpg")) == "Vo" and in_lesson(_img("hajime-lesson-vo.jpg"))
    assert lesson_attr(_img("hajime-lesson-vi.jpg")) == "Vi" and in_lesson(_img("hajime-lesson-vi.jpg"))
    assert not in_lesson(_img("hajime-lesson-end.jpg")) and lesson_attr(_img("hajime-lesson-end.jpg")) is None


def test_exam_rank():
    from hiro.vision.hajime import exam_rank

    r = exam_rank(_img("hajime-exam-result.jpg"))
    assert r["rank"] == 1 and r["score"] == 900
    assert exam_rank(_img("hajime-exam.jpg")) is None
    assert exam_rank(_img("hajime-lesson-vo.jpg")) is None


def test_exam_classified_as_exam_by_sampler(tmp_path):
    """初的考试页被偶像之路的考试判据认成 TOWER_EXAM：Sampler 改成 EXAM（走考试处理器）。"""
    from hiro.runtime.nia_sample import Sampler
    from hiro.vision.classify import classify

    s = Sampler(classify, tmp_path, nia=False)
    assert s(_img("hajime-exam.jpg")).name == "EXAM"
    assert s(_img("hajime-lesson-vo.jpg")).name == "PRACTICE"


def test_lesson_label():
    from hiro.runtime.hajime import lesson_label

    choice = NS(option="training", attr="Da", sp=True)
    assert lesson_label("Vo", choice, []) == "hajime-lesson-vo-sp"  # 画面图标优先
    assert lesson_label(None, choice, []) == "hajime-lesson-da-sp"
    assert lesson_label(None, NS(option="training", attr="Vi", sp=False), []) == "hajime-lesson-vi"
    assert lesson_label("Vi", choice, [{"option": "training", "oikomi": True}]) == "hajime-lesson-vi-hard"
    assert lesson_label(None, None, []) == "hajime-lesson"


def test_label_parsed_by_bench_bridge():
    from hiro.bench_bridge.screen import exam_fields

    assert exam_fields("hajime-lesson-vi-hard", idol=None, plan=None)["step"] == 9
    assert exam_fields("hajime-lesson-da-sp", idol=None, plan=None)["step"] == 5
    assert exam_fields("hajime-mid1", idol=None, plan=None)["step"] == "AUDITION_MID1"


def _core(tmp_path, monkeypatch):
    from hiro.runtime import hajime

    Core = hajime.hajime_core("produce-001")
    core = Core.__new__(Core)
    core.exam_count = 0
    core.lesson_count = 0
    return core


def test_exam_label_by_header(tmp_path, monkeypatch):
    core = _core(tmp_path, monkeypatch)
    assert core._battle_label(None) == "hajime-mid1"  # 没读到页头：第 1 场
    assert core._battle_label(None) == "hajime-final"
    core.last_exam_next = "final"
    core.exam_count = 0
    assert core._battle_label(None) == "hajime-final"  # 页头为准


def test_retry_when_rank_below_three(monkeypatch):
    """名次第 4：点再挑戦（_click_retry 管免费 / 券 / ジュエル）；第 3：过线不点。"""
    from hiro.runtime import hajime
    from hiro.vision import hajime as hv

    Core = hajime.hajime_core("produce-001")
    core = Core.__new__(Core)
    core.exam_count, core.exam_restarted = 1, False
    notes, clicked = [], []
    core.rec = NS(log=lambda *a, **k: None)
    core.s = NS(observe=lambda **k: None, note=notes.append)
    core._click_retry = lambda label, btn, texts: clicked.append(btn.text) or "free"
    btn = NS(text="再挑戦", rect=NS(center=NS(x=100, y=1150)))
    import hiro.devices.active as act

    monkeypatch.setattr(act, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr("hiro.vision.ocr.screen_texts", lambda img: [btn])
    monkeypatch.setattr(hv, "exam_rank", lambda img: {"rank": 3, "score": 500, "retry": None})
    assert core._retry_if_failed("hajime-mid1") is False and not clicked
    monkeypatch.setattr(hv, "exam_rank", lambda img: {"rank": 4, "score": 300, "retry": {"left": 1, "free": True}})
    assert (
        core._retry_if_failed("hajime-mid1") is True
        and clicked == ["再挑戦"]
        and core.exam_restarted
        and core.exam_count == 0
    )
    assert "マニー" in Core.RETRY_JEWEL and "ジュエル" in Core.RETRY_JEWEL


# ---------------------------------------------------------------- 续跑后考试标签（协调 11-18：最終续跑被标成 hajime-mid1）
def test_exam_label_from_trace_on_resume(tmp_path):
    import json

    from hiro.runtime import hajime

    Core = hajime.hajime_core("produce-002")
    core = Core.__new__(Core)
    core.exam_count, core.last_exam_next = 0, None
    core.s = NS(state=NS(exam_session=NS(lesson=None), produce={"mode": "produce-002"}))
    prev = tmp_path / "hajime-20261118-190000"
    prev.mkdir()
    rows = [{"seq": 1, "state": {"page_fields": {"exam_next": "mid1"}}}, {"seq": 9, "state": {"page_fields": {"exam_next": "final"}}},
            {"seq": 10, "state": {"page_fields": {}}}]  # fmt: skip
    (prev / "steps.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    core._prior_trace_dirs = lambda: [prev]
    core._stage_by_turns = lambda: pytest.fail("trace 里有页头就不用再看回合数")
    assert core._battle_label(None) == "hajime-final"


def test_exam_label_by_turns_when_no_trace(monkeypatch):
    from hiro.runtime import hajime

    Core = hajime.hajime_core("produce-002")
    core = Core.__new__(Core)
    core.exam_count, core.last_exam_next = 0, None
    core.s = NS(state=NS(exam_session=NS(lesson=None), produce={"mode": "produce-002"}))
    core._prior_trace_dirs = list
    core._stage_by_turns = lambda: "final"
    assert core._battle_label(None) == "hajime-final"
    core.exam_count = 0
    core._stage_by_turns = lambda: None
    assert core._battle_label(None) == "hajime-mid1"  # 都读不到：照旧按第几场


@pytest.mark.sim_master
def test_audition_turns_hajime():
    from hiro.state import modes

    assert modes.audition_turns("produce-002", "i_card-shro-3-007", "mid1") == 9
    assert modes.audition_turns("produce-002", "i_card-shro-3-007", "final") == 11


def test_idle_measured_from_handler_return():
    """处理器（考试）跑了很久：返回后按当时的时刻记操作，出来第一帧不能马上判「N 秒没有任何操作」停机（初 PRO 最終 11-18 284s）。"""
    from hiro.runtime.loop import DONE
    from hiro.vision.classify import PageHit

    from test_runtime import frame, runtime

    hits = iter([PageHit("EXAM", "test"), PageHit("TRANSITION", "test"), PageHit("TRANSITION", "test")])
    holder = {}

    def long_exam(rt, f, hit):
        holder["clock"].sleep(300.0)  # 考试打了 5 分钟
        return DONE

    rt, clock = runtime([frame(fill=10), frame(fill=20), frame(fill=30)], classify=lambda img: next(hits),
                        handlers={"EXAM": long_exam}, advance=lambda rt, f, h: False, idle_stop_s=180.0)  # fmt: skip
    holder["clock"] = clock
    rt.interrupts = ()
    rt.step()
    clock.sleep(3.0)
    assert rt.step().kind == "stay"  # 原来这里抛 ResumeBlocked「TRANSITION 页 300s 没有任何操作」
