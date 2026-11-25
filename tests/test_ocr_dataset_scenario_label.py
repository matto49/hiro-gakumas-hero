"""tools.ocr_dataset.scenario_label：两个 LLM 按场景读整帧、比对出真值（规格 v3 §3）。"""

from hiro.brain.llm import MockLLM
from tools.ocr_dataset import scenario_label as sl

import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


@pytest.fixture(autouse=True)
def _blank_sheet(monkeypatch):
    """对照表依赖 Pillow、CJK 字体和图标素材；这些模拟测试只看拼图和提示词的逻辑，一律换成同尺寸的白图（装了 Pillow 但没字体 / 没图标索引的机器上也不会先挂）。
    真实对照表另有一个单独的测试（缺字体 / 素材时跳过）。"""
    from tools.ocr_dataset import status_sheet

    monkeypatch.setattr(status_sheet, "sheet", lambda icon_dir: np.full((1220, 1600, 3), 255, np.uint8))


def test_ask_parses_items_and_buttons():
    llm = MockLLM(replies=[{"items": {"turns_left": "9", "hp": 27, "hand": [{"name": "イメトレ", "plus": False, "cost_type": "genki", "cost": 4}]},
                            "buttons": ["SKIP", " スキップ "]}])  # fmt: skip
    got = sl.ask(llm, np.zeros((1280, 720, 3), np.uint8), "exam")
    assert got["items"]["turns_left"] == 9 and got["items"]["hand"][0]["cost"] == 4
    assert got["buttons"] == ["SKIP", "スキップ"]
    _system, user = llm.calls[0]
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

    _system, user = scenario.prompt("exam", keys=["extra_turns"])
    assert "extra_turns" in user and "hand" not in user
    a = MockLLM(replies=[{"items": {"extra_turns": 1, "hp": 5}, "buttons": []}])
    b = MockLLM(replies=[{"items": {"extra_turns": "+1"}, "buttons": []}])
    got = sl.label_frame(np.zeros((10, 10, 3), np.uint8), "exam", {"claude": a, "codex": b}, keys=["extra_turns"])
    assert got["truth"] == {"extra_turns": 1} and got["item_status"] == {}  # 只留指定的项


def test_backfill_items_updates_truth_only_for_those_keys(tmp_path):
    import json

    from tools.ocr_dataset import schema

    import cv2

    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "a.jpg"), np.zeros((20, 20, 3), np.uint8))
    fr = {"schema": 3, "id": "a", "image": "a.jpg", "scenario": "exam", "truth": {"turns_left": 3, "hp": 9},
          "item_status": {"hp": "resolved"}, "answers": {"claude": {"items": {"turns_left": 3}}, "codex": {"items": {"turns_left": 3}}}}  # fmt: skip
    (ds / "core" / "a.json").write_text(json.dumps(fr), encoding="utf-8")
    readers = {
        "claude": MockLLM(replies=[{"items": {"extra_turns": 1}, "buttons": []}]),
        "codex": MockLLM(replies=[{"items": {"extra_turns": 2}, "buttons": []}]),
    }
    assert sl.backfill(ds, "core", "exam", ["extra_turns"], readers) == {
        "frames": 1,
        "disputed": 1,
        "dropped_reviewed": [],
        "unanswered": [],
    }
    got = json.loads((ds / "core" / "a.json").read_text(encoding="utf-8"))
    assert got["truth"] == {"turns_left": 3, "hp": 9, "extra_turns": 1}
    assert got["item_status"] == {"hp": "resolved", "extra_turns": "disputed"}
    assert got["answers"]["codex"]["items"] == {"turns_left": 3, "extra_turns": 2}


def test_backfill_replaces_list_items_and_reports_dropped_human_answers(tmp_path):
    """重定义「手牌」后补问：整列换成新读法，旧的状态（包括人定过的）清掉，并报告哪些帧的人工答案被清了。"""
    import json

    from tools.ocr_dataset import schema

    import cv2

    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "a.jpg"), np.zeros((20, 20, 3), np.uint8))
    fr = {"schema": 3, "id": "a", "image": "a.jpg", "scenario": "exam",
          "truth": {"hand": [{"name": "x"}, {"name": "成長痛"}, {"name": "えいえいおー"}], "hp": 9},
          "item_status": {"hand[1].name": "resolved", "hp": "resolved"}, "reviewed": {"hand[1].name": "2026-11-23", "hp": "2026-11-23"}}  # fmt: skip
    (ds / "core" / "a.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    item = {
        "items": {
            "hand": [{"name": "x", "plus": False}, {"name": "えいえいおー", "plus": True}],
            "playing_name": "成長痛",
            "playing_plus": True,
        },
        "buttons": [],
    }
    readers = {"claude": MockLLM(replies=[item]), "codex": MockLLM(replies=[item])}
    got = sl.backfill(ds, "core", "exam", ["hand", "playing_name", "playing_plus"], readers)
    assert got == {"frames": 1, "disputed": 0, "dropped_reviewed": ["a"], "unanswered": []}
    fr = json.loads((ds / "core" / "a.json").read_text(encoding="utf-8"))
    assert [h["name"] for h in fr["truth"]["hand"]] == ["x", "えいえいおー"] and fr["truth"]["playing_name"] == "成長痛"
    assert fr["item_status"] == {"hp": "resolved"} and fr["reviewed"] == {"hp": "2026-11-23"}


class _ImageSpy(MockLLM):
    def ask_json_image(self, system, user, image_jpeg):
        import cv2

        self.image = cv2.imdecode(np.frombuffer(image_jpeg, np.uint8), cv2.IMREAD_COLOR)
        return super().ask_json_image(system, user, image_jpeg)


def test_status_items_come_with_icon_reference_sheet():
    """状态图标上没有字：问状态栏时把放大的状态栏和素材包图标对照表拼在一张图里给模型，提示词里说明拼法。"""
    llm = _ImageSpy(
        replies=[{"items": {"statuses": [{"kind": "status", "name": "好印象", "value": 5}]}, "buttons": []}]
    )
    got = sl.ask(llm, np.zeros((1280, 720, 3), np.uint8), "exam", keys=["statuses"])
    assert got["items"]["statuses"] == [{"kind": "status", "name": "好印象", "value": 5, "pct": None}]
    assert llm.image.shape[1] > 720  # 拼了对照表
    assert "对照表" in llm.calls[0][1]
    plain = _ImageSpy(replies=[{"items": {"hp": 3}, "buttons": []}])
    sl.ask(plain, np.zeros((1280, 720, 3), np.uint8), "exam", keys=["hp"])
    assert plain.image.shape[:2] == (1280, 720) and "对照表" not in plain.calls[0][1]


def test_real_status_sheet_renders_when_font_and_icons_are_available(monkeypatch):
    """真实的状态栏对照表：需要 Pillow、CJK 字体和图标素材（index.json）；缺哪样就跳过，不当成失败。"""
    pytest.importorskip("PIL")
    import importlib

    from tools.ocr_dataset import status_sheet

    importlib.reload(status_sheet)  # 上面的 autouse 夹具已经把 sheet 换掉了，这里要真的那个
    icon_dir = status_sheet.default_icon_dir()
    try:
        out = status_sheet.sheet(str(icon_dir))
    except (RuntimeError, FileNotFoundError, OSError) as e:
        pytest.skip(f"缺字体或图标素材：{e}")
    assert out.ndim == 3 and out.shape[0] > 100


def _backfill_frame(tmp_path):
    import json

    from tools.ocr_dataset import schema

    import cv2

    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "a.jpg"), np.zeros((20, 20, 3), np.uint8))
    fr = {"schema": 3, "id": "a", "image": "a.jpg", "scenario": "exam", "truth": {"turns_left": 3, "hp": 9},
          "item_status": {"hp": "resolved"}, "reviewed": {"hp": "2026-11-23"},
          "answers": {"claude": {"items": {"hp": 9}}, "codex": {"items": {"hp": 9}}}}  # fmt: skip
    p = ds / "core" / "a.json"
    p.write_text(json.dumps(fr), encoding="utf-8")
    return ds, p, fr


def test_backfill_keeps_old_truth_when_both_llms_skip_the_item(tmp_path):
    """补问 hp 时两个 LLM 都漏答（items={}）：以前先删掉旧真值和复核状态再写空结果，却记成功（PR #104）。
    漏答要保留原值、原状态、原答案，不算补问成功。"""
    import json

    ds, p, fr = _backfill_frame(tmp_path)
    readers = {
        "claude": MockLLM(replies=[{"items": {}, "buttons": []}]),
        "codex": MockLLM(replies=[{"items": {}, "buttons": []}]),
    }
    got = sl.backfill(ds, "core", "exam", ["hp"], readers)
    assert got == {"frames": 0, "disputed": 0, "dropped_reviewed": [], "unanswered": ["a"]}
    assert json.loads(p.read_text(encoding="utf-8")) == fr  # 一个字都没动


def test_backfill_only_replaces_keys_that_were_answered(tmp_path):
    """两个目标项只有一个有人答：答了的换成新结果，漏答的保留原值；显式 null 是「画面上没有」，要和漏答区分。"""
    import json

    ds, p, fr = _backfill_frame(tmp_path)
    fr["truth"]["genki"] = 4
    fr["item_status"]["genki"] = "resolved"
    p.write_text(json.dumps(fr), encoding="utf-8")
    item = {"items": {"hp": None}, "buttons": []}  # hp 显式 null；genki 没人答
    readers = {"claude": MockLLM(replies=[item]), "codex": MockLLM(replies=[item])}
    got = sl.backfill(ds, "core", "exam", ["hp", "genki"], readers)
    assert got["frames"] == 1 and got["unanswered"] == ["a"]
    now = json.loads(p.read_text(encoding="utf-8"))
    assert now["truth"]["hp"] is None and now["truth"]["genki"] == 4
    assert now["item_status"].get("genki") == "resolved" and "hp" not in now["item_status"]
