"""选用配置 strategy.json（2026-11-25 插件架构 spec 4.2）。"""

import inspect
import json
import re

from hiro import strategy


def test_missing_file_is_all_builtin(tmp_path):
    s = strategy.load(tmp_path / "nope.json")
    assert s.sources == []
    assert s.produce_for("ACTION_SELECT") is None
    assert s.exam_for("hif") is None
    assert s.warnings == []
    assert strategy.load(None).produce_for("SELECT_CARD") is None


def test_file_values(tmp_path):
    p = tmp_path / "strategy.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": ["hook"],
                "exam": "gnrpa",
                "produce": {"ACTION_SELECT": "uinet", "SELECT_DRINK": "builtin"},
                "timeouts": {"uinet": 5},
            }
        )
    )
    s = strategy.load(p)
    assert s.sources == ["hook"]
    assert s.produce_for("ACTION_SELECT") == "uinet"
    assert s.produce_for("SELECT_DRINK") is None  # builtin = 内置
    assert s.produce_for("CONSULT") is None
    assert s.exam_for("hajime") == "gnrpa"
    assert s.timeout_for("uinet") == 5
    assert s.timeout_for("other") == strategy.DEFAULT_TIMEOUT_S


def test_overrides_beat_file():
    base = {"version": 1, "sources": ["a"], "exam": "x", "produce": {"ACTION_SELECT": "p1", "SELECT_CARD": "p2"}}
    over = strategy.parse_overrides("produce.ACTION_SELECT=p9,exam=y,exam.tower=t", "b,c")
    m = strategy.merge(base, over)
    s = strategy.from_dict(m)
    assert s.sources == ["b", "c"]
    assert s.produce_for("ACTION_SELECT") == "p9"
    assert s.produce_for("SELECT_CARD") == "p2"
    assert s.exam_for("hif") == "y"
    assert s.exam_for("tower") == "t"


def test_exam_mode_override():
    s = strategy.from_dict({"version": 1, "exam": "a", "exam.nia": "b"})
    assert s.exam_for("hif") == "a"
    assert s.exam_for("nia") == "b"


def test_unknown_page_warns():
    s = strategy.from_dict({"version": 1, "produce": {"NOT_A_PAGE": "x"}, "bogus": 1})
    assert s.produce_for("NOT_A_PAGE") is None
    assert any("NOT_A_PAGE" in w for w in s.warnings)
    assert any("bogus" in w for w in s.warnings)


def test_bad_override_warns():
    over = strategy.parse_overrides("garbage,produce.X", None)
    assert over["_warnings"]


def test_current_reads_env_file(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "produce": {"STUDY": "z"}}))
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(p))
    strategy.reset()
    assert strategy.current().produce_for("STUDY") == "z"
    monkeypatch.delenv("HIRO_STRATEGY_FILE")
    strategy.reset()
    assert strategy.current().produce_for("STUDY") is None


def test_produce_pages_match_decider_table():
    from hiro.brain import deciders

    src = inspect.getsource(deciders.ProduceDeciders.decide)
    table = set(re.findall(r'"([A-Z0-9_]+)": self\.', src))
    assert table == set(strategy.PRODUCE_PAGES)
