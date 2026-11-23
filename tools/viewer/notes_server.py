"""复盘页批注后端：在自己的服务器上以 systemd 服务运行，nginx 把 /hiro-view/api/ 转发到这里。

只用标准库。数据是一行一条的 JSON（追加写），同一个 id 以最后一条为准，deleted=true 表示删除。
    GET  /notes?run=<trace 基名>   → 这个 run 的全部批注
    GET  /notes                    → 全部批注（拉回本地做数据集）
    POST /notes                    → 新增 / 修改 / 删除一条（body 为 JSON）
部署：tools/viewer/deploy_notes.sh。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA = os.environ.get("HIRO_NOTES_FILE", "/var/lib/hiro-notes/notes.jsonl")
PORT = int(os.environ.get("HIRO_NOTES_PORT", "19139"))
MAX_BODY = 32 * 1024
TEXT_FIELDS = {"run": 120, "exam": 40, "seg": 20, "action": 120, "source": 20, "verdict": 20, "should": 200, "text": 4000,
               "attr": 8, "client": 64}  # fmt: skip
NUM_FIELDS = ("attempt", "seq", "turn", "rem", "mult", "score", "genki", "hp")
TAGS = {
    "出牌顺序",
    "饮料时机",
    "构筑选卡",
    "体力控制",
    "终结时机",
    "持续/延迟时机",
    "空过回合",
    "识别错误",
    "模拟器",
    "其他",
}
LOCK = threading.Lock()


def _load() -> dict[str, dict]:
    notes: dict[str, dict] = {}
    if not os.path.exists(DATA):
        return notes
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            try:
                n = json.loads(line)
            except ValueError:
                continue
            notes[n["id"]] = n
    return {k: v for k, v in notes.items() if not v.get("deleted")}


def _clean(body: dict) -> dict:
    out: dict = {}
    for k, limit in TEXT_FIELDS.items():
        v = body.get(k)
        if v is not None:
            out[k] = str(v)[:limit]
    for k in NUM_FIELDS:
        v = body.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    out["tags"] = [t for t in body.get("tags") or [] if t in TAGS][:10]
    if not out.get("run") or out.get("seq") is None:
        raise ValueError("run 和 seq 必填")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "hiro-notes/1"

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        # 本地直接打开的复盘页（file://，Origin 为 null）也要能提交
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # 交给 journald，只记简短一行
        print("%s %s" % (self.address_string(), fmt % args), flush=True)  # noqa: UP031

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.rstrip("/") not in ("/notes", ""):
            return self._send(404, {"error": "not found"})
        run = (parse_qs(u.query).get("run") or [None])[0]
        with LOCK:
            notes = list(_load().values())
        if run:
            notes = [n for n in notes if n.get("run") == run]
        notes.sort(key=lambda n: n.get("ts", 0))
        self._send(200, {"notes": notes})

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") != "/notes":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(n))
            nid = str(body.get("id") or "")[:40]
            with LOCK:
                cur = _load()
                if body.get("deleted"):
                    if nid not in cur:
                        return self._send(404, {"error": "no such note"})
                    rec = {"id": nid, "deleted": True, "ts": time.time()}
                else:
                    rec = _clean(body)
                    rec["id"] = nid if nid in cur else uuid.uuid4().hex[:12]
                    rec["created"] = cur.get(nid, {}).get("created", time.time())
                    rec["ts"] = time.time()
                os.makedirs(os.path.dirname(DATA), exist_ok=True)
                with open(DATA, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._send(200, {"ok": True, "note": rec})
        except ValueError as e:
            self._send(400, {"error": str(e)})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
