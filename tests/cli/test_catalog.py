import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.synthetic  # 不读截图样例（只有假的 PNG 字节）

CAT = {
    "schema": 18,
    "cards": {"p_card-01-ido-3_105": {"name": "エウレカ！", "rarity": "SSR", "plan": "PLAN2", "category": "ACTIVE",
                                      "asset": "img_general_skillcard_ido-3_105",
                                      "up": {"0": {"name": "エウレカ！", "text": "好調3ターン"}}}},
    "items": {"pitem_3-076": {"name": "選びとった熱", "rarity": "SSR", "plan": "PLAN2", "text": "試験開始時", "asset": "img_general_pitem_3-076"}},
    "drinks": {}, "enchants": {}, "idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広", "rarity": "SSR", "plan": "PLAN2"}},
    "memories": {},
}
CAT_ZH = json.loads(json.dumps(CAT))
CAT_ZH["cards"]["p_card-01-ido-3_105"]["name"] = "尤里卡！"


@pytest.fixture
def server(monkeypatch):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/catalog"):
                body = json.dumps(CAT_ZH if "lang=zh" in self.path else CAT).encode()
            elif self.path == "/sprites/img_general_pitem_3-076.png":
                body = b"\x89PNG fake"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HIRO_CATALOG_URL", f"http://127.0.0.1:{srv.server_port}")
    yield srv
    srv.shutdown()


def test_search_before_sync_explains(run_cli):
    code, env = run_cli("catalog", "search", "エウレカ")
    assert env["ok"] is False and env["error"]["code"] == "catalog_missing"
    assert "hiro catalog sync" in env["error"]["hint"]


def test_sync_then_search_and_get(run_cli, server):
    code, env = run_cli("catalog", "sync")
    assert code == 0 and env["data"]["langs"] == ["ja", "zh"] and env["data"]["counts"]["cards"] == 1
    code, env = run_cli("catalog", "search", "エウレカ")
    hits = env["data"]["items"]
    assert hits[0]["id"] == "p_card-01-ido-3_105" and hits[0]["kind"] == "cards"
    code, env = run_cli("catalog", "search", "尤里卡", "--lang", "zh")
    assert env["data"]["items"][0]["name"] == "尤里卡！"
    code, env = run_cli("catalog", "search", "熱", "--kind", "cards")
    assert env["data"]["items"] == []
    code, env = run_cli("catalog", "get", "pitem_3-076")
    assert env["data"]["kind"] == "items" and env["data"]["entry"]["name"] == "選びとった熱"


def test_sprite_downloads_once_and_caches(run_cli, server, hiro_home):
    run_cli("catalog", "sync")
    code, env = run_cli("catalog", "sprite", "img_general_pitem_3-076")
    p = env["data"]["path"]
    assert open(p, "rb").read() == b"\x89PNG fake"
    server.shutdown()
    code, env = run_cli("catalog", "sprite", "img_general_pitem_3-076")
    assert env["ok"] and env["data"]["path"] == p
