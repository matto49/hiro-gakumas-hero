"""tools.ocr_dataset.failures_page：识别不通过的项按原因聚合成核对页。"""

import pytest

from tools.ocr_dataset import failures_page as fp

pytestmark = pytest.mark.synthetic

B = ("vision", "rapidocr", "simd")


def _fr(fid, truth, status=None):
    return {"id": fid, "scenario": "exam", "truth": truth, "item_status": status or {}, "buttons": []}


def test_classify_instances():
    frames = [_fr("a", {"deck_left": 5, "hp": 27, "genki": 3, "turns_left": 4, "multiplier_pct": 100})]
    # 系统读数用 extract 的原始字段名（remaining_turns 等），由适配器换成场景信息项
    reads = {"vision": {"a": {"fields": {"hp": 21, "genki": 1, "remaining_turns": 4, "multiplier_pct": 100}}},
             "rapidocr": {"a": {"fields": {"hp": 21, "genki": 2, "remaining_turns": 3}}},
             "simd": {"a": {"fields": {"hp": 21, "genki": None, "remaining_turns": 4, "multiplier_pct": 100}}}}  # fmt: skip
    groups = fp.aggregate([("core", frames)], {"core": reads}, B)
    cats = {(g["category"], g["item"]): len(g["instances"]) for g in groups}
    assert cats == {
        ("覆盖缺口", "exam deck_left"): 1,  # 三个都没读
        ("三后端一致但≠真值", "exam hp"): 1,  # 都读成 21
        ("三后端都没读对", "exam genki"): 1,  # 1 / 2 / 没读
        ("后端差异", "exam turns_left"): 1,  # 只有 rapidocr 错
        ("后端差异", "exam multiplier_pct"): 1,  # 只有 rapidocr 没读
    }
    g = next(g for g in groups if g["item"] == "exam turns_left")
    assert g["instances"][0]["results"] == {"vision": "对", "rapidocr": "错", "simd": "对"}
    assert g["failing"] == {"rapidocr": 1}


def test_disputed_items_are_skipped():
    frames = [_fr("a", {"hp": 27}, status={"hp": "disputed"})]
    reads = {b: {"a": {"fields": {"hp": 1}}} for b in B}
    assert fp.aggregate([("core", frames)], {"core": reads}, B) == []


def test_page_renders_groups_with_comment_hooks(tmp_path):
    frames = [_fr("a", {"deck_left": 5})]
    reads = {b: {"a": {"fields": {}}} for b in B}
    groups = fp.aggregate([("core", frames)], {"core": reads}, B)
    html = fp.render(groups, B, out=tmp_path / "f.html").read_text(encoding="utf-8")
    assert "覆盖缺口" in html and 'data-target="failures"' in html and 'data-field="覆盖缺口|exam deck_left"' in html
    assert 'href="check.html#a"' in html and 'src="img/a.jpg"' in html
