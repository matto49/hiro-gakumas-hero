"""tools.ocr_dataset.scenario_label：两个 LLM 按场景读整帧、比对出真值（规格 v3 §3）。"""

import numpy as np
import pytest

from hiro.brain.llm import MockLLM
from tools.ocr_dataset import scenario_label as sl

pytestmark = pytest.mark.synthetic


def test_ask_parses_items_and_buttons():
    llm = MockLLM(replies=[{"items": {"turns_left": "9", "hp": 27, "hand": [{"name": "イメトレ", "plus": False, "cost_type": "genki", "cost": 4}]},
                            "buttons": ["SKIP", " スキップ "]}])  # fmt: skip
    got = sl.ask(llm, np.zeros((1280, 720, 3), np.uint8), "exam")
    assert got["items"]["turns_left"] == 9 and got["items"]["hand"][0]["cost"] == 4
    assert got["buttons"] == ["SKIP", "スキップ"]
    system, user = llm.calls[0]
    assert "turns_left" in user


def test_ask_raises_on_bad_reply():
    with pytest.raises(sl.LabelError):
        sl.ask(MockLLM(replies=["没有 JSON"]), np.zeros((10, 10, 3), np.uint8), "exam")


def test_label_merges_two_readers():
    a = MockLLM(replies=[{"items": {"turns_left": 9, "hp": 27}, "buttons": ["SKIP"]}])
    b = MockLLM(replies=[{"items": {"turns_left": 9, "hp": 21}, "buttons": ["SKIP"]}])
    got = sl.label_frame(np.zeros((1280, 720, 3), np.uint8), "exam", {"claude": a, "codex": b})
    assert got["truth"] == {"turns_left": 9, "hp": 27} and got["item_status"] == {"hp": "disputed"}
    assert got["answers"]["codex"]["items"]["hp"] == 21 and got["button_labels"] == ["SKIP"]


def test_prompt_and_label_can_be_limited_to_some_items():
    from tools.ocr_dataset import scenario

    system, user = scenario.prompt("exam", keys=["extra_turns"])
    assert "extra_turns" in user and "hand" not in user
    a = MockLLM(replies=[{"items": {"extra_turns": 1, "hp": 5}, "buttons": []}])
    b = MockLLM(replies=[{"items": {"extra_turns": "+1"}, "buttons": []}])
    got = sl.label_frame(np.zeros((10, 10, 3), np.uint8), "exam", {"claude": a, "codex": b}, keys=["extra_turns"])
    assert got["truth"] == {"extra_turns": 1} and got["item_status"] == {}  # 只留指定的项


def test_backfill_items_updates_truth_only_for_those_keys(tmp_path):
    import json

    import cv2

    from tools.ocr_dataset import schema

    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "a.jpg"), np.zeros((20, 20, 3), np.uint8))
    fr = {"schema": 3, "id": "a", "image": "a.jpg", "scenario": "exam", "truth": {"turns_left": 3, "hp": 9},
          "item_status": {"hp": "resolved"}, "answers": {"claude": {"items": {"turns_left": 3}}, "codex": {"items": {"turns_left": 3}}}}  # fmt: skip
    (ds / "core" / "a.json").write_text(json.dumps(fr), encoding="utf-8")
    readers = {"claude": MockLLM(replies=[{"items": {"extra_turns": 1}, "buttons": []}]), "codex": MockLLM(replies=[{"items": {"extra_turns": 2}, "buttons": []}])}
    assert sl.backfill(ds, "core", "exam", ["extra_turns"], readers) == {"frames": 1, "disputed": 1}
    got = json.loads((ds / "core" / "a.json").read_text(encoding="utf-8"))
    assert got["truth"] == {"turns_left": 3, "hp": 9, "extra_turns": 1}
    assert got["item_status"] == {"hp": "resolved", "extra_turns": "disputed"}
    assert got["answers"]["codex"]["items"] == {"turns_left": 3, "extra_turns": 2}
