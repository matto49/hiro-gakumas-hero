"""复盘页（tools/viewer）：考试名走中文说法、回合条不画「剩 ? · ?」占位（N47，G3 第 3 轮 11-26 Windows rc.6）。"""

import json
import shutil
import subprocess
from pathlib import Path

import tools.viewer.build_exam_view as build_exam_view

import pytest

ROOT = Path(__file__).resolve().parents[1]

HTML = (ROOT / "tools" / "viewer" / "exam_view.html").read_text(encoding="utf-8")
CASES = json.loads((ROOT / "app" / "labels" / "testdata" / "exam_names.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("label,want", sorted(CASES.items()))
def test_exam_name_matches_go_labels(label, want):
    # 和 app/labels/labels.go 的 Exam 共用用例
    assert build_exam_view.exam_name(label) == want


def test_exam_name_none_is_empty():
    assert build_exam_view.exam_name(None) == ""


def _helpers_js() -> str:
    a = HTML.index("/*__helpers__*/")
    b = HTML.index("/*__end_helpers__*/")
    return HTML[a:b]


def _run_node(expr: str):
    out = subprocess.run(
        ["node", "-e", _helpers_js() + f"\nconsole.log(JSON.stringify({expr}))"],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="没有 node")


@needs_node
def test_turn_tag_omits_unknown_parts():
    assert _run_node("turnTag(null, null, null)") == ""
    assert _run_node('turnTag(null, "Vo", null)') == "Vo"
    assert _run_node("turnTag(5, null, 1.5)") == "剩 5 · ×1.5"
    assert _run_node('turnTag(5, "Da", 1.5)') == "剩 5 · Da · ×1.5"


@needs_node
def test_step_head_has_no_question_marks():
    text = _run_node('stepHeadText({turn: 3, rem: null, attr: null, mult: null, seg: "原段", seq: 7})')
    assert text == "第 3 回合 · 段 原段 #7"
    assert "?" not in text
    assert _run_node('stepHeadText({turn: 3, rem: 4, attr: "Vi", mult: 1.2, seg: "-r1", seq: 7})') == (
        "第 3 回合（剩 4，Vi，×1.2） · 段 -r1 #7"
    )


def test_template_has_no_placeholder_question_marks_for_turn_tags():
    assert '剩 " + (t.rem ?? "?")' not in HTML
    assert 'rem ?? "?"' not in HTML
    assert "m[e.label] || e.name || e.label" in HTML  # 考试名用 build 写进去的中文说法（选拔标签照旧优先，#174 review）


def test_build_writes_exam_name(tmp_path):
    seg = tmp_path / "job1"
    seg.mkdir()
    state = {"page": {"name": "HIF_EXAM"}, "exam": {"remaining_turns": 6, "attr": "Vo", "score": 1},
             "exam_session": {"label": "hajime-lesson-da-hard"}}  # fmt: skip
    step = {"seq": 1, "state": state, "decision": {"action": {"kind": "play_card", "args": {}}, "source": "rule"}}
    (seg / "steps.jsonl").write_text(json.dumps(step) + "\n", encoding="utf-8")
    data = build_exam_view.build("job1", tmp_path)
    assert len(data["exams"]) == 1
    assert data["exams"][0]["label"] == "hajime-lesson-da-hard"
    assert data["exams"][0]["name"] == "追い込み Da 课程"
