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
