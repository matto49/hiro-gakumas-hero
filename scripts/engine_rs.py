"""考试引擎 Rust 后端（vendor/gakumas-player/gakumas_exam_rs）的预编译包：算源码哈希、从 GitHub Release 下载安装。

    python scripts/engine_rs.py hash                 # 源码哈希（12 位），预编译包的 Release 标签是 engine-rs-<哈希>
    python scripts/engine_rs.py platform             # 本机平台键（darwin-arm64 / darwin-x86_64 / win32-amd64 / linux-x86_64）
    python scripts/engine_rs.py install [--python 解释器]   # 下载本机平台的包、校验 sha256、装进该解释器；成功退出 0
    python scripts/engine_rs.py manifest <目录>      # CI 用：给目录里的 .whl 生成 manifest.json（平台键 → 文件名 + sha256）

setup.sh / setup.ps1 在没有 Rust 工具链（cargo）时调用 install；下载不到（新改的源码还没出包、离线）就退回 Python 版引擎（同一套规则，慢一些）。
包只认源码哈希：源码一改哈希就变，旧包不会被装到新代码上。只用标准库。

环境变量：
- HIRO_ENGINE_WHEEL_BASE：Release 下载地址前缀（默认 https://github.com/matto49/hiro-gakumas-hero/releases/download），
  测试或镜像时可指向 file:// 目录（目录下按 <标签>/<文件> 存放）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "vendor" / "gakumas-player" / "gakumas_exam_rs"
SOURCES = ("Cargo.toml", "Cargo.lock", "pyproject.toml", "src")
BASE = "https://github.com/matto49/hiro-gakumas-hero/releases/download"
TAG_PREFIX = "engine-rs-"


def source_hash(crate: Path = CRATE) -> str:
    """源码哈希：按相对路径排序，逐个文件（行尾统一成 LF，Windows 检出的 CRLF 也一样）喂进 sha256，取前 12 位。"""
    h = hashlib.sha256()
    files = []
    for name in SOURCES:
        p = crate / name
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            files.append(p)
    for f in sorted(files, key=lambda f: f.relative_to(crate).as_posix()):
        h.update(f.relative_to(crate).as_posix().encode() + b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:12]


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """平台键：系统 + CPU（统一成小写；Apple Silicon 的 arm64 / Linux 的 aarch64 都记作 arm64，AMD64 / x86_64 记作 x86_64，Windows 例外记 amd64）。"""
    system = (system or sys.platform).lower()
    machine = (machine or platform.machine()).lower()
    if system.startswith("win"):
        return "win32-amd64" if machine in ("amd64", "x86_64") else f"win32-{machine}"
    cpu = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    return f"{'darwin' if system == 'darwin' else 'linux' if system.startswith('linux') else system}-{cpu}"


def wheel_platform_key(filename: str) -> str | None:
    """wheel 文件名里的平台标签 → 平台键（manifest 用）。认不出返回 None。"""
    tag = filename[:-4].rsplit("-", 1)[-1] if filename.endswith(".whl") else ""
    if tag.startswith("win_amd64"):
        return "win32-amd64"
    if tag.startswith("macosx_"):
        return "darwin-arm64" if tag.endswith("arm64") else "darwin-x86_64" if tag.endswith("x86_64") else None
    if "linux" in tag:
        return "linux-arm64" if "aarch64" in tag else "linux-x86_64" if "x86_64" in tag else None
    return None


def write_manifest(dist: Path) -> dict:
    wheels = {}
    for w in sorted(dist.glob("*.whl")):
        key = wheel_platform_key(w.name)
        if key is None:
            raise SystemExit(f"认不出平台：{w.name}")
        wheels[key] = {"file": w.name, "sha256": hashlib.sha256(w.read_bytes()).hexdigest()}
    manifest = {"source_hash": source_hash(), "wheels": wheels}
    (dist / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def _fetch(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "hiro-gakumas-hero-setup"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  地址来自本仓库固定前缀或使用者自设的环境变量
        return r.read()


def install(python: str | None = None, base: str | None = None, key: str | None = None) -> tuple[bool, str]:
    """下载并安装本机平台的预编译包。返回 (是否装好, 说明)。不抛异常：任何失败都交给调用方退回 Python 版。"""
    base = (base or os.environ.get("HIRO_ENGINE_WHEEL_BASE") or BASE).rstrip("/")
    tag = TAG_PREFIX + source_hash()
    key = key or platform_key()
    try:
        manifest = json.loads(_fetch(f"{base}/{tag}/manifest.json"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"没有找到预编译包 {tag}（当前源码还没出包或网络不通：{e}）"
    if manifest.get("source_hash") != tag[len(TAG_PREFIX):]:
        return False, f"预编译包 {tag} 的源码哈希对不上（{manifest.get('source_hash')}），不装"
    entry = (manifest.get("wheels") or {}).get(key)
    if not entry:
        return False, f"预编译包 {tag} 里没有本机平台 {key}（有 {sorted(manifest.get('wheels') or {})}）"
    try:
        data = _fetch(f"{base}/{tag}/{entry['file']}", timeout=120)
    except (urllib.error.URLError, OSError) as e:
        return False, f"下载 {entry['file']} 失败：{e}"
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        return False, f"{entry['file']} 的 sha256 对不上，不装"
    with tempfile.TemporaryDirectory() as tmp:
        wheel = Path(tmp) / entry["file"]
        wheel.write_bytes(data)
        py = python or sys.executable
        cmd = ["uv", "pip", "install", "--python", py, "-q", "--reinstall", str(wheel)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:  # 没有 uv：用 pip
            r = subprocess.run([py, "-m", "pip", "install", "-q", "--force-reinstall", str(wheel)], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"安装 {entry['file']} 失败：{(r.stderr or r.stdout).strip()[-300:]}"
    check = subprocess.run([python or sys.executable, "-c", "import gakumas_exam_rs as m; assert hasattr(m, 'Exam')"], capture_output=True, text=True)
    if check.returncode != 0:
        return False, f"装上了但导入失败：{check.stderr.strip()[-300:]}"
    return True, f"已装预编译 Rust 后端 {entry['file']}（{tag}，{key}）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash")
    sub.add_parser("platform")
    i = sub.add_parser("install")
    i.add_argument("--python", default=None, help="装进哪个解释器（缺省当前解释器）")
    m = sub.add_parser("manifest")
    m.add_argument("dist", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "hash":
        print(source_hash())
    elif args.cmd == "platform":
        print(platform_key())
    elif args.cmd == "manifest":
        print(json.dumps(write_manifest(args.dist), ensure_ascii=False, indent=1))
    else:
        ok, msg = install(args.python)
        print(("  " if ok else "  ⚠ ") + msg)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
