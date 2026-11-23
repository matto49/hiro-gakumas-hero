"""tools.ocr_dataset.comments_server：样例页 / 报告页的意见后端。"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tools.ocr_dataset import comments_server as cs

pytestmark = pytest.mark.synthetic


def test_clean_keeps_known_fields_and_limits():
    c = cs.clean({"target": "dmm-x-00001", "box": 3, "kind": "标注错误", "text": "x" * 9000, "quote": "編成", "evil": 1})
    assert c == {"target": "dmm-x-00001", "box": 3, "kind": "标注错误", "text": "x" * cs.TEXT_MAX, "quote": "編成"}


@pytest.mark.parametrize("body", [{"target": "page", "kind": "格式"}, {"kind": "格式", "text": "a"}, {"target": "page", "kind": "乱写", "text": "a"}])
def test_clean_rejects_incomplete(body):
    with pytest.raises(ValueError):
        cs.clean(body)


def test_clean_box_must_be_int():
    assert "box" not in cs.clean({"target": "page", "kind": "其他", "text": "a", "box": "3"})


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DATA", str(tmp_path / "c.jsonl"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


def test_post_get_delete_roundtrip(server, tmp_path):
    a = _req(server + "/comments", {"target": "page", "kind": "格式", "text": "JSON 结构太深"})
    b = _req(server + "/comments", {"target": "dmm-x-00001", "box": 4, "kind": "标注错误", "text": "P 是图标"})
    assert a["id"] and b["created"]
    got = _req(server + "/comments")
    assert [c["text"] for c in got] == ["JSON 结构太深", "P 是图标"]
    _req(server + "/comments", {"id": a["id"], "deleted": True})
    assert [c["id"] for c in _req(server + "/comments")] == [b["id"]]
    _req(server + "/comments", {"id": b["id"], "target": "dmm-x-00001", "box": 4, "kind": "标注错误", "text": "P 是金币图标，应忽略"})
    assert [c["text"] for c in _req(server + "/comments")] == ["P 是金币图标，应忽略"]
    assert len((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()) == 4  # 追加写，留历史


def test_bad_post_is_400(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _req(server + "/comments", {"target": "page"})
    assert e.value.code == 400


def test_clean_keeps_field_path():
    c = cs.clean({"target": "dmm-x-00001", "field": "hand[2].card_id", "kind": "标注错误", "text": "应该是 えいえいおー+"})
    assert c["field"] == "hand[2].card_id"
    assert len(cs.clean({"target": "p", "field": "x" * 500, "kind": "其他", "text": "a"})["field"]) == 200


@pytest.mark.parametrize("kind", ["漏了", "不需要", "重要度不对", "示例写错", "描述不对", "不该读", "类型/方法", "标注错误", "其他"])
def test_kinds_used_by_review_pages_are_accepted(kind):
    """样例页 / 字段核对页 / 场景清单页的意见类型都要收（11-23 场景清单页提交全被 400）。"""
    assert cs.clean({"target": "scenarios", "field": "exam/info/0", "kind": kind, "text": "a"})["kind"] == kind


def test_fields_page_kinds_are_all_accepted():
    import json as _json

    from tools.ocr_dataset.fields_page import KINDS_JS

    for k in _json.loads(KINDS_JS):
        assert k in cs.KINDS, k
