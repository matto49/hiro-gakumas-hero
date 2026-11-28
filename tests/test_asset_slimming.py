"""资源分发瘦身测试：分离源码包与增量素材包、硬链补全基础卡面、预构建主数据下载、bench 缓存清理。"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import sqlite3
import tarfile
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def server(tmp_path):
    www = tmp_path / "www"
    www.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(www), **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield www, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def test_pack_assets_dedups_base_skill_cards_and_is_deterministic(tmp_path):
    mod = _load("pack_assets_bundle", "tools/assets/pack_assets_bundle.py")
    pack = tmp_path / "pack"
    (pack / "cards").mkdir(parents=True)
    (pack / "icons" / "status").mkdir(parents=True)
    (pack / "cards" / "base1.png").write_bytes(b"base1")
    (pack / "cards" / "variant-hmsz.png").write_bytes(b"var1")
    (pack / "icons" / "status" / "a.png").write_bytes(b"icon")
    dedup = tmp_path / "dedup.txt"
    dedup.write_text("base1.png\n", encoding="utf-8")

    m1 = mod.pack_assets(pack, tmp_path / "out1", dedup_list=dedup)
    m2 = mod.pack_assets(pack, tmp_path / "out2", dedup_list=dedup)
    assert m1["sha256"] == m2["sha256"] and m1["tag"] == m2["tag"]
    assert m1["dedup_skipped"] == 1
    with tarfile.open(tmp_path / "out1" / m1["asset"], "r:gz") as tf:
        names = set(tf.getnames())
    tag = m1["tag"]
    assert f"{tag}/cards/base1.png" not in names
    assert f"{tag}/cards/variant-hmsz.png" in names
    assert f"{tag}/icons/status/a.png" in names
    assert f"{tag}/.cards-delta" in names


def test_hydrate_delta_pack_hardlinks_without_overwriting_variants(tmp_path):
    gi = _load("game_images", "tools/assets/game_images.py")
    gd = tmp_path / "game_data"
    (gd / "skill_cards").mkdir(parents=True)
    (gd / "skill_cards" / "base1.png").write_bytes(b"rgba-base1")
    (gd / "skill_cards" / "base2.png").write_bytes(b"rgba-base2")

    pack = tmp_path / "assets-pack"
    (pack / "cards").mkdir(parents=True)
    (pack / ".cards-delta").write_text("game-images-b9577da\n", encoding="utf-8")
    (pack / "cards" / "variant-hmsz.png").write_bytes(b"variant")

    n = gi.hydrate_delta_pack(gd, pack)
    assert n == 2
    assert (pack / "cards" / "base1.png").read_bytes() == b"rgba-base1"
    assert (pack / "cards" / "variant-hmsz.png").read_bytes() == b"variant"
    if os.name == "posix":
        assert (pack / "cards" / "base1.png").stat().st_ino == (gd / "skill_cards" / "base1.png").stat().st_ino
    # 再次运行幂等，返回 0
    assert gi.hydrate_delta_pack(gd, pack) == 0


def test_public_asset_source_reads_pointer_file(tmp_path, monkeypatch):
    from hiro.defaults.assets import PublicAssetSource

    shared = tmp_path / "shared" / "assets-pack" / "assets-pack-123"
    (shared / "cards").mkdir(parents=True)
    (shared / "cards" / "a.png").write_bytes(b"x")
    fake_root = tmp_path / "src"
    (fake_root / "src" / "hiro" / "defaults").mkdir(parents=True)
    (fake_root / ".hiro-assets-pack").write_text(str(shared) + "\n", encoding="utf-8")

    import hiro.defaults.assets as assets_mod

    monkeypatch.setattr(assets_mod, "__file__", str(fake_root / "src" / "hiro" / "defaults" / "assets.py"))
    monkeypatch.delenv("HIRO_BUNDLED_ASSETS", raising=False)
    src = PublicAssetSource(kaa_game_data=tmp_path / "kaa", user_assets=tmp_path / "u")
    assert src.bundled == shared
    assert src.card_art_dir() == shared / "cards"


def test_pack_master_and_fetch_prebuilt_and_cleanup(tmp_path, server):
    pack_m = _load("pack_master", "tools/assets/pack_master.py")
    fetch_m = _load("fetch_master", "vendor/gakumas-player/scripts/fetch_master.py")

    commit = "1" * 40
    old_commit = "2" * 40
    src_master = tmp_path / "build_master"
    snap = src_master / commit
    snap.mkdir(parents=True)
    db_path = snap / "catalog.sqlite"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (id TEXT)")
    con.execute("INSERT INTO t VALUES ('ok')")
    con.commit()
    con.close()

    www, base = server
    tag = f"master-{commit[:12]}"
    meta = pack_m.pack(commit, www / tag, master_root=src_master)
    assert meta["commit"] == commit and (www / tag / meta["file"]).is_file()

    # 客户端下载预构建包 + 清理旧快照（保留 source-files.json）
    client_root = tmp_path / "client"
    old_snap = client_root / "data" / "master" / old_commit
    (old_snap / "raw").mkdir(parents=True)
    (old_snap / "raw" / "a.yaml.gz").write_bytes(b"old")
    (old_snap / "catalog.sqlite").write_bytes(b"old-db")
    (old_snap / "source-files.json").write_text("[]", encoding="utf-8")

    ok = fetch_m.fetch_prebuilt(commit, base=base, root=client_root)
    assert ok is True
    got_db = client_root / "data" / "master" / commit / "catalog.sqlite"
    assert got_db.read_bytes() == db_path.read_bytes()
    cur = json.loads((client_root / "data" / "master" / "current.json").read_text(encoding="utf-8"))
    assert cur["commit"] == commit and cur["database"] == f"{commit}/catalog.sqlite"

    fetch_m.cleanup_old_snapshots(commit, root=client_root)
    assert not (old_snap / "raw").exists()
    assert not (old_snap / "catalog.sqlite").exists()
    assert (old_snap / "source-files.json").is_file()

    # SHA256 损坏时拒绝并返回 False（回退 YAML 构建）
    (www / tag / meta["file"]).write_bytes(b"corrupted")
    assert fetch_m.fetch_prebuilt(commit, base=base, root=tmp_path / "client2") is False


def test_bench_cache_cleanup_removes_old_models_and_masters(tmp_path, monkeypatch):
    from hiro.bench_bridge import advisor as A

    monkeypatch.setenv("HIRO_BENCH_CACHE", str(tmp_path))
    monkeypatch.delenv("GKMS_UINET_MODEL", raising=False)
    monkeypatch.setattr(A, "_ort_reads_ir10", lambda: False)

    # 模拟存在旧版模型和原始未转换模型
    stale_model = tmp_path / "gkms_uinet-000000000000-opset18.onnx"
    stale_model.write_bytes(b"stale")
    orig = tmp_path / f"gkms_uinet-{A.UINET_COMMIT[:12]}.onnx"
    orig.write_bytes(b"orig")

    monkeypatch.setattr(A, "_download_model", lambda dl: orig)
    monkeypatch.setattr(A, "_convert", lambda src, dst: dst.write_bytes(b"converted-opset18"))

    got = A.ensure_model(download=True)
    assert got.name == f"gkms_uinet-{A.UINET_COMMIT[:12]}-opset18.onnx"
    assert got.read_bytes() == b"converted-opset18"
    assert not orig.exists(), "转换完成后应清理 31 MB 原始未转换模型"
    assert not stale_model.exists(), "应清理旧提交模型"

    # 二次调用直接命中已转换缓存，不再调用 _download_model
    monkeypatch.setattr(A, "_download_model", lambda dl: (_ for _ in ()).throw(AssertionError("不应重下")))
    assert A.ensure_model(download=True) == got

    # 旧 uinet-master-* 清理
    old_m = tmp_path / "uinet-master-aaaaaaaaaaaa"
    cur_m = tmp_path / "uinet-master-bbbbbbbbbbbb"
    old_m.mkdir()
    cur_m.mkdir()
    A._cleanup_old_uinet_masters(cur_m.name)
    assert not old_m.exists() and cur_m.is_dir()
