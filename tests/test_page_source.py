"""判页来源（PageObs.source，去 KAA 第 0 步）：主循环按判出页面的那一层记来源，只记不改行为；统计工具出基线表。"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))  # page_sources


class FakeSession:
    debug_recog = False

    def __init__(self):
        self.state = NS(page=NS(name="unknown"))
        self.observed, self.recovers = [], []

    def observe(self, **kw):
        self.observed.append(kw)
        self.state.page.name = kw["scene"]

    def record(self):
        pass

    def recover(self, kind, detail):
        self.recovers.append(kind)


def _controller(monkeypatch, kaa_scene, *, fallback=None):
    from hiro.loop import controller as ctl
    from hiro.loop import kaa_patches
    from hiro.loop.controller import HiroController

    import kotonebot

    portrait = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(kotonebot, "device", NS(screenshot=lambda: portrait, click=lambda *a: None))
    monkeypatch.setattr(ctl, "interval_title_visible", lambda img: False)
    monkeypatch.setitem(kaa_patches.BLIND_CHOICE, "pending", False)
    monkeypatch.setattr(HiroController, "_landscape", NS(tick=lambda img: False))
    s = FakeSession()
    monkeypatch.setattr(HiroController, "session", s)
    c = HiroController.__new__(HiroController)
    c.page = NS(check_scene=lambda: kaa_scene)
    c._last_scene = None
    c._quick_page = lambda: None
    c._is_hif_exam = lambda: False
    c._fallback_page = lambda: fallback
    for name in ("_is_date_changed", "_clear_hif_drink_dialog", "_skip_settings_dialog", "_is_start_confirm", "_is_produce_finished",
                 "_is_drink_offer", "_retry_network_error", "_skip_commu", "_first_run_settings", "_cancel_recover_dialog",
                 "_resume_special_guidance", "_record_finals_ranking", "_close_produce_log", "_is_pitem_select", "_watch_unknown"):  # fmt: skip
        setattr(c, name, lambda *a, **k: False)
    c._ocr_boxes = list
    c._ocr_texts = list
    c._dispatch = lambda scene, last: None
    c._advance = lambda: 0
    return c, s


def test_kaa_hit_recorded_as_kaa(monkeypatch):
    from hiro.loop.controller import Scene, SceneType

    c, s = _controller(monkeypatch, Scene(SceneType.ACTION_SELECT), fallback="SELECT_DRINK")
    c._update()
    assert s.observed[-1] == {**s.observed[-1], "scene": "ACTION_SELECT", "page_source": "kaa"} and s.recovers == []


def test_pixel_fallback_recorded(monkeypatch):
    c, s = _controller(monkeypatch, None, fallback="SELECT_DRINK")
    c._update()
    assert s.observed[-1]["page_source"] == "pixel" and s.recovers == ["page_misjudged"]


def test_nothing_recognized_recorded_as_none(monkeypatch):
    c, s = _controller(monkeypatch, None)
    c._update()
    assert s.observed[-1]["page_source"] == "none" and s.observed[-1]["scene"] == "unknown"


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


def test_kaa_usage_counts_outermost_kaa_calls_only(monkeypatch):
    """KAA 出手计数：只数最外层，已经换成我们函数的方法不算，重复 install 不重复包，返回值照旧。"""
    from hiro.loop import kaa_usage

    class Context:  # 假的 KAA 上下文（模块名伪装成 kaa.*）
        __module__ = "kaa.fake"

        def fetch(self):
            return 7

        def commit(self):
            return self.fetch() + 1  # 内部再调 KAA 方法：不重复算

        def _private(self):
            return 0

    def ours(self):
        return "hiro"

    ours.__module__ = "hiro.loop.kaa_patches"
    Context.ours = ours
    monkeypatch.setattr(kaa_usage, "USAGE", kaa_usage.Counter())
    assert kaa_usage._wrap_class(Context, lambda n: not n.startswith("_")) == 2
    assert kaa_usage._wrap_class(Context, lambda n: not n.startswith("_")) == 0
    c = Context()
    assert c.commit() == 8 and c.fetch() == 7 and c.ours() == "hiro" and c._private() == 0
    kaa_usage.count("skip(12,12):UNKNOWN")
    assert kaa_usage.snapshot() == {"Context.commit": 1, "Context.fetch": 1, "skip(12,12):UNKNOWN": 1}


def test_kaa_usage_install_on_real_kaa_classes():
    from hiro.loop import kaa_usage

    from kaa.tasks.produce.new.page import ActionSelectContext, ProducePage, StudyContext
    from kaa.tasks.produce.new.strategies.hif_grind import HifGrindStrategy

    kaa_usage.install()
    assert kaa_usage.install() == 0  # 第二次不重复包
    for fn in (
        StudyContext.commit,
        ActionSelectContext.fetch_available_actions,
        ProducePage.check_scene,
        HifGrindStrategy.on_study,
    ):
        assert getattr(fn, kaa_usage._INSTALLED, False), fn


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
