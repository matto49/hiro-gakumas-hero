"""tools.ocr_dataset.fields_page：字段真值核对页（每张图要读出的内容 + 各后端读数 + 按字段提意见）。"""

import json

from tools.ocr_dataset import fields_page, schema

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.synthetic


def _ds(tmp_path):
    ds = schema.init(tmp_path / "ds")
    cv2.imwrite(str(ds / "core" / "dmm-r-00001.jpg"), np.full((1280, 720, 3), 80, np.uint8))
    fr = {"schema": 2, "id": "dmm-r-00001", "image": "dmm-r-00001.jpg", "sha256": schema.sha256(ds / "core" / "dmm-r-00001.jpg"),
          "source": {"run": "r", "seq": 1}, "platform": {"kind": "dmm", "stored": [720, 1280]}, "page": "HIF_EXAM", "note": "手牌第 2 张认错",
          "truth": {"fields": {"my_score": 1200, "hand": [{"card_id": "c1", "upgrade": 1}]}, "checks": {"quick": "HIF_EXAM"}},
          "field_status": {}, "known_fail": {"windows": "RapidOCR 认不出"}}  # fmt: skip
    (ds / "core" / "dmm-r-00001.json").write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    reads = {"vision": {"dmm-r-00001": {"quick": "HIF_EXAM", "fields": {"my_score": 1200, "hand": [{"card_id": "c1", "upgrade": 1}]}}},
             "rapidocr": {"dmm-r-00001": {"quick": "HIF_EXAM", "fields": {"my_score": 1280, "hand": [{"card_id": "c9", "upgrade": 1}]}}}}  # fmt: skip
    return ds, reads


def test_page_lists_truth_fields_reads_and_comment_hooks(tmp_path):
    ds, reads = _ds(tmp_path)
    out = fields_page.build(
        ds,
        reads,
        names=lambda path, value, row: "えいえいおー" if path.endswith("card_id") and value == "c1" else None,
        out=tmp_path / "p.html",
    )
    html = out.read_text(encoding="utf-8")
    assert html.count('class="frame"') == 1
    for path in ("my_score", "hand[0].card_id", "hand[0].upgrade", "check:quick"):
        assert f'data-field="{path}"' in html
    assert "えいえいおー" in html and "手牌第 2 张认错" in html and "RapidOCR 认不出" in html
    assert html.count('class="bad"') == 2  # rapidocr 的分数和卡读错
    assert 'class="crit"' in html  # 高代价字段有标记
    assert "api/comments" in html and "data:image/jpeg;base64," in html


def test_page_can_link_images_as_files(tmp_path):
    ds, reads = _ds(tmp_path)
    out = fields_page.build(ds, reads, names=lambda *a: None, out=tmp_path / "site" / "index.html", image_files=True)
    html = out.read_text(encoding="utf-8")
    assert 'src="img/dmm-r-00001.jpg"' in html and 'loading="lazy"' in html and "base64" not in html
    assert (tmp_path / "site" / "img" / "dmm-r-00001.jpg").is_file()


def _inline_handlers_with_interpolation(js: str) -> list[str]:
    """内联事件属性（onclick="…"）里拼了 ${…} 的片段：评论的 id / 文字不能这样拼进脚本。"""
    import re

    return re.findall(r"""\bon\w+\s*=\s*"[^"]*\$\{[^"]*\}""", js)


def test_comment_delete_link_does_not_build_script_from_comment_id():
    """复核页删除链接曾是 onclick="del('${esc(c.id)}')"：id 里带 ') 就能跳出字符串执行脚本（存储型 XSS，PR #104）。"""
    js = fields_page.JS
    assert not _inline_handlers_with_interpolation(js)
    assert 'data-id="${esc(c.id)}"' in js  # id 只当属性值放进去（已转义），不进脚本
    assert 'addEventListener("click"' in js and "closest" in js  # 委托监听，不靠内联脚本
