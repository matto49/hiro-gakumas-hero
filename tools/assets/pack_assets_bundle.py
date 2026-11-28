"""构建精简源码包（hiro-src-<version>.tar.gz）、独立识别素材包（assets-pack-<hash12>.tar.gz）和发布清单（release.json）。

对齐版本体系设计（2026-11-26-version-system-design.md §2）：
- 源码包只含运行与安装脚本所需代码（排除 assets/pack、tests、docs、app、.github），约 3–4 MB；
- 识别素材包按内容哈希独立发布（assets-pack-<hash12>），内容不变不重发、客户端升级不重下；
- release.json 记录两者的下载文件名、SHA256、字节数与内容哈希，供客户端按清单下载与校验。

用法：
    python tools/assets/pack_assets_bundle.py hash [assets/pack]
    python tools/assets/pack_assets_bundle.py pack-assets --out dist/ [--pack assets/pack]
    python tools/assets/pack_assets_bundle.py pack-source --version 0.1.2 --out dist/
    python tools/assets/pack_assets_bundle.py manifest --version 0.1.2 --source-meta s.json --assets-meta a.json --out dist/release.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EPOCH = 1795564800  # 2026-11-26T00:00:00Z，固定时间戳保证打包可重复
SOURCE_EXCLUDES = ("assets/pack", "tests", "docs", "app", ".github")
DEFAULT_DEDUP_LIST = Path(__file__).with_name("game_images_skill_cards.txt")
DELTA_MARKER = ".cards-delta"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_dedup_cards(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _pack_files(pack_dir: Path, dedup_cards: set[str]) -> list[tuple[str, Path]]:
    files = sorted((p for p in pack_dir.rglob("*") if p.is_file()), key=lambda p: p.relative_to(pack_dir).as_posix())
    out: list[tuple[str, Path]] = []
    for p in files:
        rel = p.relative_to(pack_dir).as_posix()
        if dedup_cards and rel.startswith("cards/") and rel[len("cards/") :] in dedup_cards:
            continue
        out.append((rel, p))
    return out


def content_hash(pack_dir: Path, *, dedup_list: Path | None = DEFAULT_DEDUP_LIST) -> str:
    """对目录内将打包的文件按 POSIX 相对路径排序，依次喂入「相对路径 + SHA256」计算整体内容哈希。"""
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"素材包目录不存在：{pack_dir}")
    dedup = load_dedup_cards(dedup_list)
    entries = _pack_files(pack_dir, dedup)
    if not entries:
        raise ValueError(f"素材包目录为空：{pack_dir}")
    h = hashlib.sha256()
    if dedup:
        h.update(f"dedup:{len(dedup)}\0".encode())
    for rel, p in entries:
        h.update(rel.encode() + b"\0" + sha256_file(p).encode("ascii") + b"\0")
    return h.hexdigest()


def pack_assets(pack_dir: Path, out_dir: Path, *, dedup_list: Path | None = DEFAULT_DEDUP_LIST) -> dict:
    """将 assets/pack 打成确定性 tar.gz（顶层目录 assets-pack-<hash12>/），自动去重与 game_data/skill_cards 重合的基础卡面。"""
    pack_dir, out_dir = Path(pack_dir), Path(out_dir)
    dedup = load_dedup_cards(dedup_list)
    ch = content_hash(pack_dir, dedup_list=dedup_list)
    short = ch[:12]
    tag = f"assets-pack-{short}"
    asset = f"{tag}.tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / asset
    tmp = out.with_suffix(".tar.gz.part")

    entries = _pack_files(pack_dir, dedup)
    with (
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=EPOCH) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf,
    ):
        for rel, p in entries:
            data = p.read_bytes()
            ti = tarfile.TarInfo(name=f"{tag}/{rel}")
            ti.size = len(data)
            ti.mtime = EPOCH
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
        if dedup:
            marker_data = b"game-images-b9577da\n"
            ti = tarfile.TarInfo(name=f"{tag}/{DELTA_MARKER}")
            ti.size = len(marker_data)
            ti.mtime = EPOCH
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(marker_data))
    tmp.replace(out)
    return {
        "tag": tag,
        "asset": asset,
        "sha256": sha256_file(out),
        "content_hash": ch,
        "bytes": out.stat().st_size,
        "dedup_skipped": len(dedup),
    }


def _should_exclude(rel: str) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in SOURCE_EXCLUDES)


def pack_source(repo_dir: Path, version: str, out_dir: Path, *, ref: str = "HEAD") -> dict:
    """从 git 仓库打精简源码包 hiro-src-<version>.tar.gz（排除 assets/pack、tests、docs、app、.github）。"""
    repo_dir, out_dir = Path(repo_dir), Path(out_dir)
    ver = version.lstrip("v").strip()
    commit = subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", ref], text=True).strip()
    top = f"hiro-src-{ver}"
    asset = f"{top}.tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / asset
    tmp = out.with_suffix(".tar.gz.part")

    # 用 git archive 取干净跟踪文件流，过滤掉排除目录后重新封装成确定性 tar.gz
    raw_tar = subprocess.check_output(["git", "-C", str(repo_dir), "archive", "--format=tar", ref])
    with (
        tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as src_tf,
        tmp.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=EPOCH) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as dst_tf,
    ):
        for m in src_tf.getmembers():
            rel = m.name.lstrip("./").rstrip("/")
            if not rel or _should_exclude(rel):
                continue
            if not (m.isfile() or m.isdir()):
                continue
            ti = tarfile.TarInfo(name=f"{top}/{rel}")
            ti.type = m.type
            ti.mode = 0o755 if (m.isdir() or (m.mode & 0o111)) else 0o644
            ti.mtime = EPOCH
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if m.isfile():
                fobj = src_tf.extractfile(m)
                data = fobj.read() if fobj else b""
                ti.size = len(data)
                dst_tf.addfile(ti, io.BytesIO(data))
            else:
                dst_tf.addfile(ti)
    tmp.replace(out)
    return {
        "asset": asset,
        "sha256": sha256_file(out),
        "bytes": out.stat().st_size,
        "commit": commit,
    }


def build_manifest(version: str, source_meta: dict, assets_meta: dict) -> dict:
    ver = version.lstrip("v").strip()
    channel = "prerelease" if "-" in ver else "stable"
    return {
        "schema": "hiro.release.v1",
        "version": ver,
        "channel": channel,
        "commit": source_meta.get("commit", ""),
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "asset": source_meta["asset"],
            "sha256": source_meta["sha256"],
            "bytes": source_meta["bytes"],
        },
        "assets_pack": {
            "tag": assets_meta["tag"],
            "asset": assets_meta["asset"],
            "sha256": assets_meta["sha256"],
            "content_hash": assets_meta["content_hash"],
            "bytes": assets_meta["bytes"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_hash = sub.add_parser("hash", help="打印 assets/pack 内容哈希（前 12 位与完整值）")
    p_hash.add_argument("pack", nargs="?", default=str(ROOT / "assets" / "pack"))

    p_a = sub.add_parser("pack-assets", help="打包 assets/pack 为独立 tar.gz")
    p_a.add_argument("--pack", default=str(ROOT / "assets" / "pack"))
    p_a.add_argument("--out", required=True)
    p_a.add_argument("--meta", default=None, help="可选：将元数据 JSON 写到此路径")

    p_s = sub.add_parser("pack-source", help="打包精简源码包 hiro-src-<version>.tar.gz")
    p_s.add_argument("--version", required=True)
    p_s.add_argument("--repo", default=str(ROOT))
    p_s.add_argument("--ref", default="HEAD")
    p_s.add_argument("--out", required=True)
    p_s.add_argument("--meta", default=None, help="可选：将元数据 JSON 写到此路径")

    p_m = sub.add_parser("manifest", help="生成 release.json")
    p_m.add_argument("--version", required=True)
    p_m.add_argument("--source-meta", required=True)
    p_m.add_argument("--assets-meta", required=True)
    p_m.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "hash":
        ch = content_hash(Path(args.pack))
        print(f"assets-pack-{ch[:12]} {ch}")
        return 0
    if args.cmd == "pack-assets":
        meta = pack_assets(Path(args.pack), Path(args.out))
        if args.meta:
            Path(args.meta).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False))
        return 0
    if args.cmd == "pack-source":
        meta = pack_source(Path(args.repo), args.version, Path(args.out), ref=args.ref)
        if args.meta:
            Path(args.meta).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False))
        return 0
    if args.cmd == "manifest":
        src_m = json.loads(Path(args.source_meta).read_text(encoding="utf-8"))
        ast_m = json.loads(Path(args.assets_meta).read_text(encoding="utf-8"))
        mf = build_manifest(args.version, src_m, ast_m)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(mf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(mf, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
