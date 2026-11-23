"""Session（注入截图 / 分类 / 提取）：观测与 reduce、缺字段等待、执行校验、trace 往返、报告度量。"""

import json  # noqa: F401
from pathlib import Path  # noqa: F401

import hiro  # noqa: F401
from hiro.contracts import Action, Candidate, Decision, LLMTrace, TraceWriter, read_steps  # noqa: F401
from hiro.loop.report import markdown, report, summarize  # noqa: F401
from hiro.loop.session import Session

import numpy as np


class Fake:
    """脚本化的设备：每次 classify 按序给出页面；extract 给出预设字段。"""

    def __init__(self, pages: list[tuple[str, str]], fields: dict[str, list[dict]] | None = None):
        self.pages = list(pages)
        self.fields = {k: list(v) for k, v in (fields or {}).items()}
        self.shots = 0
        self.slept = 0.0
        self.now = 0.0

    def shoot(self):
        self.shots += 1
        self.now += 0.03
        return np.zeros((1280, 720, 3), np.uint8)

    def classify(self):
        self.now += 0.01
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def extract(self, scene, img):
        q = self.fields.get(scene)
        return (q.pop(0) if len(q) > 1 else q[0]) if q else None, 1.0

    def sleep(self, s):
        self.slept += s
        self.now += s

    def clock(self):
        return self.now


def _session(tmp_path, fake):
    tw = TraceWriter(root=tmp_path, name="t", meta={"test": True})
    return Session(
        tw, shoot=fake.shoot, classify=fake.classify, extract=fake.extract, sleep=fake.sleep, clock=fake.clock
    ), tw


def test_verify_detects_transition_and_misjudge(tmp_path):
    fake = Fake([("ACTION_SELECT", "produce"), ("LOADING", "produce"), ("CONSULT", "produce")])
    s, _ = _session(tmp_path, fake)
    s.observe(scene="ACTION_SELECT", fields=False, save_frame=False)
    r = s.verify(Action.produce_next("shop"), page_before="ACTION_SELECT", taps=[[100, 1000]], timeout=5)
    assert (
        r.ok
        and r.page_after == "CONSULT"
        and r.expected_transition
        and r.error is None
        and s.state.page.name == "CONSULT"
    )
    # 到了不该到的页面：记 page_misjudged
    fake = Fake([("ACTION_SELECT", "produce"), ("HIF_EXAM_RESULT", "produce")])
    s, _ = _session(tmp_path / "b", fake)
    s.observe(scene="ACTION_SELECT", fields=False, save_frame=False)
    r = s.verify(Action.produce_next("shop"), page_before="ACTION_SELECT", timeout=5)
    assert r.ok and not r.expected_transition and s.recoveries["page_misjudged"] == 1
    # 超时没变：ok=False
    fake = Fake([("ACTION_SELECT", "produce")])
    s, _ = _session(tmp_path / "c", fake)
    s.observe(scene="ACTION_SELECT", fields=False, save_frame=False)
    r = s.verify(Action.produce_next("shop"), page_before="ACTION_SELECT", timeout=2, interval=0.5)
    assert not r.ok and r.page_after == "ACTION_SELECT" and "仍是" in r.error and r.waited_s >= 2


def test_observe_complete_with_registered_adapter(tmp_path):
    """开源版：用注册的字段转换代替游戏专用识别。缺字段时最多等若干帧，读全就返回；一直缺就带着缺失继续。"""
    from hiro.vision import observe as ob

    ob.register_adapter("DEMO_PAGE", lambda raw: {"hp": raw.get("hp"), "gold": raw.get("gold")})
    try:
        fake = Fake(
            [("DEMO_PAGE", "demo")],
            {"DEMO_PAGE": [{"hp": None, "gold": 5}, {"hp": None, "gold": 5}, {"hp": 30, "gold": 5}]},
        )
        s, _ = _session(tmp_path, fake)
        obs = s.observe_complete("DEMO_PAGE", ("hp", "gold"), tries=5, interval=0.5)
        assert obs.fields["hp"] == 30 and obs.fields["gold"] == 5 and fake.slept == 1.0
        fake2 = Fake([("DEMO_PAGE", "demo")], {"DEMO_PAGE": [{"hp": None, "gold": 5}]})
        s2, _ = _session(tmp_path / "b", fake2)
        obs2 = s2.observe_complete("DEMO_PAGE", ("hp",), tries=3, interval=0.5)
        assert obs2.fields.get("hp") is None
    finally:
        ob.FIELD_ADAPTERS.pop("DEMO_PAGE", None)
