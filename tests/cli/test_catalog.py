import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.synthetic  # 不读截图样例（只有假的 PNG 字节）

CAT = {
    "schema": 18,
    "cards": {
        "p_card-01-ido-3_105": {
            "name": "エウレカ！",
            "rarity": "SSR",
            "plan": "PLAN2",
            "category": "ACTIVE",
            "asset": "img_general_skillcard_ido-3_105",
            "up": {"0": {"name": "エウレカ！", "text": "好調3ターン"}},
        }
    },
    "items": {
        "pitem_3-076": {
            "name": "選びとった熱",
            "rarity": "SSR",
            "plan": "PLAN2",
            "text": "試験開始時",
            "asset": "img_general_pitem_3-076",
        }
    },
    "drinks": {},
    "enchants": {},
    "idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広", "rarity": "SSR", "plan": "PLAN2"}},
    "memories": {"ability-1": {"name": "", "rarity": "R", "text": "初期ビジュアル上昇+15"}},
}
CAT_ZH = json.loads(json.dumps(CAT))
CAT_ZH["cards"]["p_card-01-ido-3_105"]["name"] = "尤里卡！"
CAT_ZH["memories"]["ability-1"]["text"] = "初始视觉+15"
SUPPORTS = {
    "format": "gkbench.supports.v1",
    "characters": {},
    "source": {"master_date": "2026-11-16"},
    "cards": {
        "s_card-3-0000": {
            "name": {"ja": "あなたにも作ってあげる！", "zh": "我也会做给你的！"},
            "rarity": "SSR",
            "type": "visual",
            "caps": [40, 45, 50, 55, 60],
        }
    },
}


@pytest.fixture
def server(monkeypatch):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/catalog"):
                body = json.dumps(CAT_ZH if "lang=zh" in self.path else CAT).encode()
            elif self.path == "/api/supports":
                body = json.dumps(SUPPORTS).encode()
            elif self.path == "/sprites/img_general_pitem_3-076.png":
                body = b"\x89PNG fake"
            elif self.path == "/sprites/support-s_card-3-0000.webp":
                body = b"RIFF fake webp"
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
    _code, env = run_cli("catalog", "search", "エウレカ")
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
    _code, env = run_cli("catalog", "sprite", "img_general_pitem_3-076")
    p = env["data"]["path"]
    assert open(p, "rb").read() == b"\x89PNG fake"  # noqa: SIM115
    server.shutdown()
    _code, env = run_cli("catalog", "sprite", "img_general_pitem_3-076")
    assert env["ok"] and env["data"]["path"] == p


def test_sync_also_fetches_supports_and_lookup_gives_chinese_names(run_cli, server):
    """初星助手信息页用 catalog supports / lookup 配中文名和小图（同 Go 版 go/internal/catalog）。"""
    code, env = run_cli("catalog", "supports")
    assert env["ok"] is False and env["error"]["code"] == "catalog_missing"
    code, env = run_cli("catalog", "sync")
    assert code == 0 and env["data"]["counts"]["supports"] == 1
    code, env = run_cli("catalog", "supports")
    assert env["data"]["cards"]["s_card-3-0000"]["name"]["zh"] == "我也会做给你的！"
    code, env = run_cli("catalog", "lookup", "--ids", "p_card-01-ido-3_105,i_card-skin-shro-3-007,nope", "--lang", "zh")
    items = env["data"]["items"]
    assert items["p_card-01-ido-3_105"]["name"] == "尤里卡！" and items["p_card-01-ido-3_105"]["text"] == "好調3ターン"
    assert items["i_card-skin-shro-3-007"]["asset"] == "idol-i_card-shro-3-007" and "nope" not in items
    code, env = run_cli(
        "catalog", "lookup", "--texts", "初期ビジュアル上昇+15\n初期ビジュアル上昇+15B9", "--lang", "zh"
    )
    assert env["data"]["texts"] == {"初期ビジュアル上昇+15": "初始视觉+15"}


def test_sprite_falls_back_to_webp(run_cli, server):
    _code, env = run_cli("catalog", "sprite", "support-s_card-3-0000")
    p = env["data"]["path"]
    assert p.endswith(".webp") and Path(p).read_bytes() == b"RIFF fake webp"
    _, env = run_cli("catalog", "sprite", "support-s_card-3-0000")
    assert env["data"]["path"] == p
    _, env = run_cli("catalog", "sprite", "nothing-here")
    assert env["ok"] is False


def test_idol_name_prefers_chinese(run_cli, server, monkeypatch):
    """开局待确认卡片里的偶像卡名：界面默认中文，先用中文名；没同步资料库时原样给编号。"""
    from hiro.cli.catalog import idol_name

    assert idol_name("i_card-skin-shro-3-007") == "i_card-skin-shro-3-007"
    monkeypatch.setitem(CAT_ZH["idols"]["i_card-shro-3-007"], "name", "【Campus mode!!】筱泽广")
    run_cli("catalog", "sync")
    assert idol_name("i_card-skin-shro-3-007") == "【Campus mode!!】筱泽广"
