"""把构建好的模拟器主数据 catalog.sqlite 打成单文件压缩包与清单，供 GitHub Release 分发（master-<commit12>）。

相比在玩家电脑上逐个下载 309 个 YAML（~216 MB）再花 1–2 分钟用 PyYAML 建库，预构建的 catalog.sqlite.gz 约 38 MB，
下载解压只需数秒，且无需保留 raw/*.yaml.gz。

用法：
    python tools/assets/pack_master.py [--commit <40位>] --out dist/
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER_ROOT = ROOT / "vendor" / "gakumas-player" / "data" / "master"
EPOCH = 1795564800


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_commit(commit: str | None = None, master_root: Path = MASTER_ROOT) -> str:
    if commit:
        return commit.strip()
    cur = master_root / "current.json"
    if not cur.is_file():
        raise FileNotFoundError(f"找不到 {cur}，请先构建主数据或传 --commit")
    return str(json.loads(cur.read_text(encoding="utf-8"))["commit"])


def pack(commit: str, out_dir: Path, *, master_root: Path = MASTER_ROOT) -> dict:
    """将 master_root/<commit>/catalog.sqlite 压成确定性 .sqlite.gz，并在 out_dir 写入 manifest.json。"""
    snap = master_root / commit
    sqlite_path = snap / "catalog.sqlite"
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"找不到 {sqlite_path}")
    short = commit[:12]
    tag = f"master-{short}"
    asset = f"{tag}.sqlite.gz"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / asset
    tmp = out.with_suffix(".gz.part")

    with (
        sqlite_path.open("rb") as src,
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=EPOCH) as gz,
    ):
        shutil.copyfileobj(src, gz, length=1 << 20)
    tmp.replace(out)

    manifest_sha = ""
    mf_path = snap / "manifest.json"
    if mf_path.is_file():
        manifest_sha = sha256_file(mf_path)
    meta = {
        "schema": "hiro.master.v1",
        "tag": tag,
        "commit": commit,
        "file": asset,
        "sha256": sha256_file(out),
        "bytes": out.stat().st_size,
        "sqlite_sha256": sha256_file(sqlite_path),
        "sqlite_bytes": sqlite_path.stat().st_size,
        "manifest_sha256": manifest_sha,
    }
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", default=None, help="主数据 40 位 commit（默认读 current.json）")
    ap.add_argument("--out", required=True, help="输出目录（写入 master-<commit12>.sqlite.gz 与 manifest.json）")
    args = ap.parse_args(argv)
    commit = resolve_commit(args.commit)
    meta = pack(commit, Path(args.out))
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
