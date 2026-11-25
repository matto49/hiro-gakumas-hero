"""下载社区公开的 master 快照（vertesan/gakumasu-diff 固定提交）并构建模拟器用的 catalog.sqlite。

    python vendor/gakumas-player/scripts/fetch_master.py [commit]

按 data/master/<commit>/source-files.json 逐个下载 YAML（约 216 MB，只当数据读、不执行），校验 Git blob SHA1，
gzip 存到 data/master/<commit>/raw/，写 manifest.json，再调 build-master.py 建库并写 data/master/current.json。
已下载且校验通过的文件会跳过，可以重复跑。构建需要 PyYAML（只在构建时用，运行时只用标准库）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMIT = "edc305aac3ac0204806c96164ce36fb795ba81e1"
REPO = "https://github.com/vertesan/gakumasu-diff"


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _get(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(2 * (i + 1))
    raise AssertionError


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
        return 0
    fetch(commit)
    print("  构建 catalog.sqlite（约 1 分钟）…", flush=True)
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "build-master.py"), commit])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
