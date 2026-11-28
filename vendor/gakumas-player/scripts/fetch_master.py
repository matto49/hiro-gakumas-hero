"""下载社区公开的 master 快照（vertesan/gakumasu-diff 固定提交）并构建模拟器用的 catalog.sqlite。

    python vendor/gakumas-player/scripts/fetch_master.py [commit] [--force] [--from-yaml]

优先从本仓库 GitHub Release（master-<commit12>）下载预构建的 catalog.sqlite.gz（约 38 MB，校验 SHA256，数秒落盘，
免去下载 309 个原始 YAML 与本地 1–2 分钟建库）；Release 尚未发布或传 --from-yaml 时，自动退回按
data/master/<commit>/source-files.json 逐个下载 YAML（约 216 MB）、校验 Git blob SHA1 并调 build-master.py 建库。
已就绪时直接跳过；切到新提交后自动清理旧提交目录里的运行产物（raw/、catalog.sqlite 等）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMIT = "928da0de728506cac1e90deeffd4e0f9aa56fa27"
REPO = "https://github.com/vertesan/gakumasu-diff"
RELEASE_BASE = "https://github.com/matto49/hiro-gakumas-hero/releases/download"


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, tries: int = 4, timeout: float = 60) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hiro-gakumas-hero-setup"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (i + 1))
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (i + 1))
    raise AssertionError


def _download_to(url: str, dst: Path, *, tries: int = 3, timeout: float = 120) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hiro-gakumas-hero-setup"})
            with urllib.request.urlopen(req, timeout=timeout) as r, part.open("wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f, length=1 << 20)
            part.replace(dst)
            return
        except urllib.error.HTTPError as e:
            part.unlink(missing_ok=True)
            if e.code == 404:
                raise
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (i + 1))
        except Exception as e:  # noqa: BLE001
            part.unlink(missing_ok=True)
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (i + 1))


def fetch_prebuilt(commit: str, *, base: str | None = None, root: Path | None = None) -> bool:
    """尝试从本仓 GitHub Release master-<commit12> 下载预构建的 catalog.sqlite.gz。成功返回 True，404/失败返回 False。"""
    base_url = (base if base is not None else os.environ.get("HIRO_MASTER_RELEASE_BASE") or RELEASE_BASE).rstrip("/")
    data_root = (root or ROOT) / "data" / "master"
    tag = f"master-{commit[:12]}"
    try:
        mf_bytes = _get(f"{base_url}/{tag}/manifest.json", tries=2, timeout=20)
        mf = json.loads(mf_bytes.decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
        print(f"  未找到预构建主数据包 {tag}（{e}），改走 YAML 源码构建…", flush=True)
        return False
    if mf.get("commit") != commit or not mf.get("file") or not mf.get("sha256") or not mf.get("sqlite_sha256"):
        print(f"  预构建主数据清单 {tag} 字段不匹配，改走 YAML 源码构建…", flush=True)
        return False

    snap = data_root / commit
    snap.mkdir(parents=True, exist_ok=True)
    gz_path = snap / (mf["file"] + ".dl")
    db_tmp = snap / "catalog.sqlite.part"
    db_path = snap / "catalog.sqlite"
    try:
        print(
            f"  下载预构建主数据 {base_url}/{tag}/{mf['file']}（约 {mf.get('bytes', 0) / (1 << 20):.1f} MB）…",
            flush=True,
        )
        _download_to(f"{base_url}/{tag}/{mf['file']}", gz_path, tries=3, timeout=180)
        if (got_gz := _sha256_file(gz_path)) != mf["sha256"]:
            raise ValueError(f"压缩包 SHA256 不一致：{got_gz[:12]} ≠ {mf['sha256'][:12]}")
        with gzip.open(gz_path, "rb") as src, db_tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
        if (got_db := _sha256_file(db_tmp)) != mf["sqlite_sha256"]:
            raise ValueError(f"catalog.sqlite SHA256 不一致：{got_db[:12]} ≠ {mf['sqlite_sha256'][:12]}")
        db_tmp.replace(db_path)
    except Exception as e:  # noqa: BLE001
        db_tmp.unlink(missing_ok=True)
        print(f"  预构建主数据下载/校验失败（{e}），改走 YAML 源码构建…", flush=True)
        return False
    finally:
        gz_path.unlink(missing_ok=True)

    cur_data = {
        "commit": commit,
        "database": f"{commit}/catalog.sqlite",
        "manifest_sha256": mf.get("manifest_sha256") or "",
        "database_sha256": mf["sqlite_sha256"],
    }
    (data_root / "current.json").write_text(json.dumps(cur_data, indent=2) + "\n", encoding="utf-8")
    print(f"  预构建主数据已就绪：{db_path}", flush=True)
    return True


def cleanup_old_snapshots(keep_commit: str, *, root: Path | None = None) -> None:
    """清理旧主数据提交目录里的运行产物（raw/、catalog.sqlite 等），保留 tracked 的 source-files.json。"""
    data_root = (root or ROOT) / "data" / "master"
    if not data_root.is_dir():
        return
    for d in data_root.iterdir():
        if not d.is_dir() or d.name == keep_commit or not re.fullmatch(r"[0-9a-f]{40}", d.name):
            continue
        shutil.rmtree(d / "raw", ignore_errors=True)
        for name in ("catalog.sqlite", "catalog.building.sqlite", "manifest.json", "build.json"):
            (d / name).unlink(missing_ok=True)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


def fetch(commit: str) -> Path:
    snap = ROOT / "data" / "master" / commit
    files = json.loads((snap / "source-files.json").read_text(encoding="utf-8"))
    raw = snap / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    def one(item: dict) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_]+\.yaml", item["name"]):
            raise ValueError(f"意外的文件名：{item['name']}")
        target = raw / (item["name"] + ".gz")
        data = None
        if target.exists():
            try:
                data = gzip.decompress(target.read_bytes())
                if _blob_sha(data) != item["git_blob_sha"]:
                    data = None
            except OSError:
                data = None
        if data is None:
            data = _get(item["url"])
            if _blob_sha(data) != item["git_blob_sha"]:
                raise ValueError(f"Git blob 校验失败：{item['name']}")
            target.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
        comp = target.read_bytes()
        return {**item, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "gzip_sha256": hashlib.sha256(comp).hexdigest(), "stored": f"raw/{item['name']}.gz"}  # fmt: skip

    out, done = [], 0
    with ThreadPoolExecutor(6) as ex:
        for rec in ex.map(one, files):
            out.append(rec)
            done += 1
            if done % 25 == 0 or done == len(files):
                print(f"  master {done}/{len(files)}", flush=True)
    manifest = {"schema_version": 1, "repository": REPO, "commit": commit, "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "authority": "community-extracted master snapshot; not matched to the installed client version",
                "files": sorted(out, key=lambda x: x["name"])}  # fmt: skip
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return snap


def main(argv: list[str]) -> int:
    commit = next((a for a in argv if not a.startswith("-")), DEFAULT_COMMIT)
    snap = ROOT / "data" / "master" / commit
    current = ROOT / "data" / "master" / "current.json"
    pointed = json.loads(current.read_text(encoding="utf-8")).get("commit") if current.exists() else None
    if (snap / "catalog.sqlite").exists() and pointed == commit and "--force" not in argv:
        print(f"模拟器主数据已就绪：{snap / 'catalog.sqlite'}（--force 重建）")
        cleanup_old_snapshots(commit)
        return 0
    if "--from-yaml" not in argv and fetch_prebuilt(commit):
        cleanup_old_snapshots(commit)
        return 0
    fetch(commit)
    print("  构建 catalog.sqlite（约 1 分钟）…", flush=True)
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "build-master.py"), commit])
    if rc == 0:
        cleanup_old_snapshots(commit)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
