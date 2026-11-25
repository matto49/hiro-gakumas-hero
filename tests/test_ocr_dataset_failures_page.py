"""tools.ocr_dataset.failures_page：识别不通过的项按原因聚合成核对页。"""

from tools.ocr_dataset import failures_page as fp

import pytest

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
    assert 'data-target="failures"' in html and 'data-field="覆盖缺口|exam deck_left"' in html
    assert 'href="check.html#a"' in html and 'src="img/a.jpg"' in html
    # 人看得懂：中文标题、白话的问题类型、每处一句话
    assert "考试 · 牌堆剩余张数" in html and "所有 OCR 后端都没读出来" in html and "画面上" in html
    import re

    visible = re.sub(r"<[^>]+>", " ", html.split("<script>")[0])  # 只看页面上显示的文字（标签属性里的内部名不算）
    assert "deck_left" not in visible and "exam " not in visible
    # 一键判定
    for k in ("识别逻辑要改", "真值 / 定义问题", "可以接受"):
        assert f'data-verdict="{k}"' in html


def test_page_crops_the_region_of_the_item(tmp_path):
    import cv2
    import numpy as np

    ds = tmp_path / "ds"
    (ds / "core").mkdir(parents=True)
    cv2.imwrite(str(ds / "core" / "a.jpg"), np.full((1280, 720, 3), 120, np.uint8))
    frames = [_fr("a", {"deck_left": 5})]
    frames[0]["image"] = "a.jpg"
    reads = {b: {"a": {"fields": {}}} for b in B}
    groups = fp.aggregate([("core", frames)], {"core": reads}, B)
    out = fp.render(groups, B, out=tmp_path / "site" / "f.html", dataset=ds)
    html = out.read_text(encoding="utf-8")
    assert 'src="crops/a__deck_left.jpg"' in html
    crop = cv2.imread(str(tmp_path / "site" / "crops" / "a__deck_left.jpg"))
    assert crop is not None and crop.shape[0] > 170  # 放大过（牌堆区域 200×180 → 约 2 倍）


def test_values_are_shown_in_plain_words():
    assert fp.plain(True) == "有" and fp.plain(False) == "没有" and fp.plain(None) == "—"
    assert fp.plain("green_heart") == "绿心" and fp.plain("vocal") == "Vo" and fp.plain([58, 2]) == "58、2"


def test_excluded_frames_are_skipped_in_aggregate():
    frames = [{**_fr("a", {"deck_left": 5}), "excluded": "不是这个场景"}]
    reads = {b: {"a": {"fields": {}}} for b in B}
    assert fp.aggregate([("core", frames)], {"core": reads}, B) == []


def test_failures_page_delete_link_does_not_build_script_from_comment_id():
    """备注的删除链接不能把评论 id 拼进 onclick（存储型 XSS，PR #104）。"""
    import re

    js = fp.JS_PAGE
    assert not re.findall(r"""\bon\w+\s*=\s*"[^"]*\$\{[^"]*\}""", js)
    assert 'data-id="${esc(c.id)}"' in js
    assert 'addEventListener("click"' in js and "closest" in js
