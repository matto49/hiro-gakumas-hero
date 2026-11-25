"""判页来源（PageObs.source，去 KAA 第 0 步）：主循环按判出页面的那一层记来源，只记不改行为；统计工具出基线表。
测旧主循环（HiroController._update）按层记来源、KAA 出手计数（kaa_usage）的测试 2026-11-24 随旧主循环删了。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))  # page_sources


def test_page_source_in_observation_and_old_traces_load():
    from hiro.contracts import Observation
    from hiro.vision.observe import observation

    obs = observation(1, 0.0, "LOADING", None, area="produce", page_source="quick")
    assert obs.page.source == "quick" and not obs.problems(strict=True)
    assert Observation.from_json(obs.to_json()) == obs
    old = json.loads(obs.to_json())
    del old["page"]["source"]  # 第 0 步之前的 trace
    assert Observation.from_dict(old).page.source is None


def test_page_sources_table(tmp_path):
    import page_sources

    seg = tmp_path / "run"
    seg.mkdir()
    page = lambda name, src=None: {  # noqa: E731
        "name": name,
        "area": "produce",
        "confidence": 1.0,
        "stable_frames": 1,
        **({"source": src} if src else {}),
    }
    steps = [
        {"observation": {"seq": 1, "source": "screen", "page": page("ACTION_SELECT", "kaa")},
         "observations_before": [{"seq": 0, "source": "screen", "page": page("LOADING", "quick")}],
         "notes": ["recover:page_misjudged: KAA 判不出，像素兜底补判为 ACTION_SELECT"]},
        {"observation": {"seq": 2, "source": "screen", "page": page("ACTION_SELECT", "pixel")},
         "observations_after": [{"seq": 2, "source": "screen", "page": page("ACTION_SELECT", "pixel")}],  # 同一 seq 只算一次
         "notes": ["recover:page_misjudged: 决策帧与点击前画面不一致"]},
        {"observation": {"seq": 3, "source": "screen", "page": page("HIF_EXAM")}},  # 旧 trace：没有 source
    ]  # fmt: skip
    (seg / "steps.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps), encoding="utf-8")
    pages, misjudged = page_sources.count(seg)
    assert pages == {
        ("ACTION_SELECT", "kaa"): 1,
        ("ACTION_SELECT", "pixel"): 1,
        ("LOADING", "quick"): 1,
        ("HIF_EXAM", "—"): 1,
    }
    md = page_sources.table(pages, misjudged, "run")
    assert "| ACTION_SELECT | 1 |" in md and "KAA 判不出、我们补判（recover:page_misjudged）1 次" in md
    assert "其他页面不一致（不是判页）（recover:page_misjudged）1 次" in md


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
