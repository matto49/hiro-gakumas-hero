"""初 LEGEND（produce-006）适配（11-28 Bjan：脚本里唯独初 LEGEND 打不了）：模式表、课程档位、属性上限、续跑接回难度、任务参数。
剧本页「初 ⇄」切换的端到端在 test_hajime_legend_switch.py。"""

import json
from pathlib import Path

from hiro.brain import hajime_nia
from hiro.cli import jobs
from hiro.runtime import hajime, nia_sample, segments
from hiro.state import modes

import pytest

HIRO = "i_card-skin-hski-3-0001"
AGGRESSIVE = "i_card-shro-1-000"  # 主数据 examEffectType = ExamCardPlayAggressive


# ---------------------------------------------------------------- 主数据
@pytest.mark.sim_master
def test_legend_steps_and_cap():
    assert modes.steps("produce-006") == 18
    assert hajime_nia.param_cap("produce-006") == 3000


def test_param_cap_fallback_without_master(monkeypatch):
    monkeypatch.setattr(modes, "master", lambda: (_ for _ in ()).throw(RuntimeError("没有主数据")))
    hajime_nia.param_cap.cache_clear()
    try:
        assert hajime_nia.param_cap("produce-006") == 3000
    finally:
        hajime_nia.param_cap.cache_clear()


@pytest.mark.sim_master
def test_exam_style_snake_case():
    assert modes.exam_style(AGGRESSIVE) == "card_play_aggressive"
    assert modes.exam_style("i_card-nonexistent") is None


@pytest.mark.sim_master
@pytest.mark.parametrize(
    ("kind", "clear", "suffix", "limits"),
    [("normal", 60, "card_play_aggressive-normal-01", (8, 120)), ("normal", 395, "card_play_aggressive-normal-05", (8, 630)),
     ("sp", 85, "card_play_aggressive-sp-01", (8, 160)), ("sp", 455, "card_play_aggressive-sp-05", (8, 710))],
)  # fmt: skip
def test_legend_lesson_levels_use_style_not_plan(kind, clear, suffix, limits):
    """LEGEND 档位 id 是 p_step_lesson_level-produce_006-<流派>-<kind>-0N（不带 plan）：原来按 plan 前缀查，永远是空。"""
    levels = modes.lesson_levels("produce-006", AGGRESSIVE, kind, clear)
    assert [r["id"] for r in levels] == [f"p_step_lesson_level-produce_006-{suffix}"]
    assert modes.lesson_limits(levels) == limits


@pytest.mark.sim_master
def test_legend_lesson_levels_edge_cases():
    assert modes.lesson_levels("produce-006", AGGRESSIVE, "hard", 90) == []  # LEGEND 没有追い込み
    assert modes.lesson_levels("produce-006", AGGRESSIVE, "normal", 85) == []  # 85 是 SP 的线，普通课没有
    # 偶像卡查不到流派：用任意流派的（数值一样），满分线照样唯一
    any_style = modes.lesson_levels("produce-006", "i_card-nonexistent", "normal", 60)
    assert len(any_style) > 1 and modes.lesson_limits(any_style) == (8, 120)
    # 别的模式不受影响
    assert [r["id"] for r in modes.lesson_levels("produce-003", AGGRESSIVE, "hard", 90)][:1] == [
        "p_step_lesson_level-003-plan2-hard-1"
    ]


# ---------------------------------------------------------------- 续跑接回难度
def _seg(root: Path, name: str, mode: str | None):
    d = root / name
    d.mkdir(parents=True)
    st = {"produce": {"mode": mode, "stamina": 30}} if mode else {"produce": {"stamina": 30}}
    (d / "steps.jsonl").write_text(json.dumps({"seq": 1, "state_after": st}) + "\n", encoding="utf-8")
    return d


def test_last_produce_mode_reads_latest_segment(tmp_path):
    _seg(tmp_path, "hajime-20261128-010000", "produce-001")
    _seg(tmp_path, "hajime-20261128-010000-r1", "produce-006")
    _seg(tmp_path, "hajime-20261128-010000-r2", None)  # 这段没读到模式：用更早的
    cur = tmp_path / "hajime-20261128-010000-r3"
    assert segments.last_produce_mode(cur) == "produce-006"
    assert segments.resume_difficulty(cur, hajime.MODES, "regular") == "legend"


def test_resume_difficulty_without_trace_falls_back(tmp_path, caplog):
    cur = tmp_path / "hajime-20261128-010000-r1"
    assert segments.resume_difficulty(cur, hajime.MODES, "regular") == "regular"
    assert "没读到" in caplog.text
    # 上一段是别的家族（N.I.A 的 trace 名撞了也不会套进初）：按缺省
    _seg(tmp_path, "hajime-20261128-010000", "produce-005")
    assert segments.resume_difficulty(cur, hajime.MODES, "regular") == "regular"
    assert segments.resume_difficulty(cur, nia_sample.MODES, "pro") == "master"


def test_prior_trace_dirs_numeric_order(tmp_path):
    """-r10 排在 -r9 后面（#128 review），当前段有步骤时排最后。"""
    for n in ("b", "b-r9", "b-r10", "b-r2", "bx"):
        (tmp_path / n).mkdir()
    cur = _seg(tmp_path, "b-r11", "produce-006")
    assert [d.name for d in segments.prior_trace_dirs(cur)] == ["b", "b-r2", "b-r9", "b-r10", "b-r11"]


def test_difficulty_args_default_to_none(monkeypatch):
    """--difficulty 缺省 None：new 在 run() 里按 regular，resume 接上一段 trace 的；legend 在可选范围里。"""
    seen = []
    monkeypatch.setattr(hajime, "run", lambda args: seen.append(args.difficulty) or 0)
    hajime.main(["resume", "--idol", HIRO])
    hajime.main(["new", "--idol", HIRO, "--difficulty", "legend"])
    assert seen == [None, "legend"]
    with pytest.raises(SystemExit):
        hajime.main(["new", "--idol", HIRO, "--difficulty", "hard"])


# ---------------------------------------------------------------- 任务层
def test_job_argv_legend_new_and_resume():
    d = Path("/j/J1")
    a = jobs.KINDS["hajime"].argv({"command": "new", "idol": HIRO, "difficulty": "legend"}, d)
    assert a[a.index("--difficulty") + 1] == "legend"
    a = jobs.KINDS["hajime"].argv({"command": "new", "idol": HIRO}, d)
    assert a[a.index("--difficulty") + 1] == "regular"
    a = jobs.KINDS["hajime"].argv({"command": "resume"}, d)  # 续打不塞缺省难度，由 runtime 接上一段
    assert "--difficulty" not in a
    a = jobs.KINDS["nia"].argv({"command": "resume", "difficulty": "master"}, d)
    assert a[a.index("--difficulty") + 1] == "master"


def test_resume_chain_inherits_difficulty(tmp_path):
    import time

    jd = jobs.jobs_dir(tmp_path) / "20261128-003200-ab12"
    jd.mkdir(parents=True)
    prev = {"id": jd.name, "kind": "hajime", "params": {"idol": HIRO, "difficulty": "legend"}, "state": "stopped",
            "ended_at": time.time()}  # fmt: skip
    (jd / "job.json").write_text(json.dumps(prev), encoding="utf-8")
    traces = tmp_path / "work" / "traces"
    (traces / jd.name).mkdir(parents=True)
    out = jobs.resume_chain(jobs.jobs_dir(tmp_path), traces, "hajime", {"command": "resume"})
    assert out == {"trace": f"{jd.name}-r1", "idol": HIRO, "difficulty": "legend"}
    # 调用方给了难度就不覆盖
    out = jobs.resume_chain(jobs.jobs_dir(tmp_path), traces, "hajime", {"command": "resume", "difficulty": "pro"})
    assert "difficulty" not in out
