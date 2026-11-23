"""OCR 测试集样例页 / 报告页的意见后端：在服务器上以 systemd 服务运行，nginx 把 /ocr-dataset/api/ 转发到这里。

只用标准库（部署时单文件拷过去）。数据一行一条 JSON（追加写），同一个 id 以最后一条为准，deleted=true 表示删除。
    GET  /comments   → 全部意见（按提交时间）
    POST /comments   → 新增 / 修改（带 id）/ 删除（{id, deleted: true}）
意见挂在：target = "page"（整体）或帧 id；box = 框 id（可选）；kind 是意见类型。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("OCR_COMMENTS_FILE", "/var/lib/ocr-review/comments.jsonl")
PORT = int(os.environ.get("OCR_COMMENTS_PORT", "19141"))
MAX_BODY = 32 * 1024
TEXT_MAX = 4000
KINDS = ("格式", "标注错误", "类型/字段", "识别结果", "页面展示", "其他")
LOCK = threading.Lock()


def clean(body: dict) -> dict:
    """只留认识的字段、截断长度；target / kind / text 必填。"""
    out: dict = {}
    target = str(body.get("target") or "")[:160]
    kind = body.get("kind")
    text = str(body.get("text") or "").strip()
    if not target or kind not in KINDS or not text:
        raise ValueError(f"target、kind（{' / '.join(KINDS)}）、text 必填")
    out["target"], out["kind"], out["text"] = target, kind, text[:TEXT_MAX]
    if isinstance(body.get("box"), int) and not isinstance(body.get("box"), bool):
        out["box"] = body["box"]
    if body.get("quote"):
        out["quote"] = str(body["quote"])[:200]
    return out


def _load() -> list[dict]:
    rows: dict[str, dict] = {}
    if os.path.exists(DATA):
        with open(DATA, encoding="utf-8") as f:
            for line in f:
                try:
                    c = json.loads(line)
                except ValueError:
                    continue
                rows[c["id"]] = c
    return sorted((c for c in rows.values() if not c.get("deleted")), key=lambda c: c.get("created", 0))


def _append(rec: dict) -> None:
    os.makedirs(os.path.dirname(DATA) or ".", exist_ok=True)
    with LOCK, open(DATA, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "ocr-review/1"

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 不往 journal 里写每条请求
        pass

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") != "/comments":
            return self._send(404, {"error": "not found"})
        self._send(200, _load())

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/comments":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413 if n > MAX_BODY else 400, {"error": "body 大小不对"})
        try:
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("body 要是 JSON 对象")
            now = time.time()
            if body.get("deleted") and body.get("id"):
                rec = {"id": str(body["id"])[:40], "deleted": True, "updated": now}
            else:
                rec = {"id": str(body.get("id") or uuid.uuid4().hex)[:40], **clean(body), "created": now}
                old = next((c for c in _load() if c["id"] == rec["id"]), None)
                if old:
                    rec["created"], rec["updated"] = old.get("created", now), now
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        _append(rec)
        self._send(200, rec)


def main() -> None:
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
