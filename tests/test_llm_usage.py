"""LLM 用量记全：缓存读 / 写、费用；报告里有总输入（三者之和）和费用合计。"""

from hiro.brain.llm import LLMResult, _claude_usage, _openai_usage
from hiro.contracts.decision import llm_trace_from_call


def test_claude_usage_fields():
    out = {
        "usage": {
            "input_tokens": 12,
            "output_tokens": 900,
            "cache_read_input_tokens": 30000,
            "cache_creation_input_tokens": 4000,
        },
        "total_cost_usd": 0.0421,
    }
    u = _claude_usage(out)
    assert u == {
        "input_tokens": 12,
        "output_tokens": 900,
        "cache_read_input_tokens": 30000,
        "cache_creation_input_tokens": 4000,
        "cost_usd": 0.0421,
    }
    rec = LLMResult({"a": 1}, "", 1.0, None, "claude-cli", "sonnet", **u).record()
    t = llm_trace_from_call(rec)
    assert (t.cache_read_input_tokens, t.cache_creation_input_tokens, t.cost_usd) == (30000, 4000, 0.0421)


def test_openai_cached_split():
    u = _openai_usage(
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 800}}}
    )
    assert u == {"input_tokens": 200, "output_tokens": 50, "cache_read_input_tokens": 800}
    assert _openai_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})["input_tokens"] == 10


def test_report_totals():
    from types import SimpleNamespace

    from hiro.loop import report

    calls = [
        SimpleNamespace(
            error=None,
            input_tokens=10,
            output_tokens=100,
            cache_read_input_tokens=3000,
            cache_creation_input_tokens=500,
            cost_usd=0.01,
            seconds=5.0,
            adapter="claude-cli",
            model="sonnet",
        ),
        SimpleNamespace(
            error=None,
            input_tokens=5,
            output_tokens=50,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            cost_usd=None,
            seconds=7.0,
            adapter="claude-cli",
            model="sonnet",
        ),
    ]
    llm = report.llm_summary(calls)
    assert (
        llm["total_input"] == 10 + 3000 + 500 + 5 and llm["cache_read"] == 3000 and abs(llm["cost_usd"] - 0.01) < 1e-9
    )
    r = {
        "trace": "t",
        "steps": 1,
        "decisions": 1,
        "decisions_by_source": {},
        "decisions_by_decider": {},
        "page_changes": 0,
        "final_page": None,
        "exam_shot_to_tap_ms": {"n": 0, "median": None, "p90": None, "max": None},
        "llm": llm,
        "recoveries": {},
        "execution": {},
        "exams": [],
    }
    md = report.markdown(r)
    assert "缓存读 3000" in md and "$0.0100" in md and "总输入 3515" in md
