"""scripts/engine_rs.py：考试引擎 Rust 后端预编译包的源码哈希、平台键、manifest 与下载校验（失败路径都退回 Python 版，不抛异常）。"""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("engine_rs", ROOT / "scripts" / "engine_rs.py")
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)


def _crate(tmp_path, newline=b"\n"):
    c = tmp_path / "crate"
    (c / "src").mkdir(parents=True)
    (c / "Cargo.toml").write_bytes(b"[package]" + newline + b"name = 'x'" + newline)
    (c / "src" / "lib.rs").write_bytes(b"fn main() {}" + newline)
    return c


def test_hash_ignores_line_endings_and_tracks_changes(tmp_path):
    lf = er.source_hash(_crate(tmp_path / "a"))
    crlf = er.source_hash(_crate(tmp_path / "b", b"\r\n"))
    assert lf == crlf and len(lf) == 12  # Windows 检出（CRLF）和 Mac / Linux 算出同一个标签
    c = _crate(tmp_path / "c")
    (c / "src" / "lib.rs").write_bytes(b"fn main() { 1; }\n")
    assert er.source_hash(c) != lf
    (c / "target").mkdir()
    (c / "target" / "junk").write_bytes(b"x")  # 编译产物不进哈希
    assert er.source_hash(c) == er.source_hash(c)


def test_real_crate_hash_is_stable():
    assert er.source_hash() == er.source_hash()


@pytest.mark.parametrize("system,machine,key", [
    ("darwin", "arm64", "darwin-arm64"), ("darwin", "x86_64", "darwin-x86_64"),
    ("win32", "AMD64", "win32-amd64"), ("linux", "x86_64", "linux-x86_64"), ("linux", "aarch64", "linux-arm64"),
])  # fmt: skip
def test_platform_key(system, machine, key):
    assert er.platform_key(system, machine) == key


@pytest.mark.parametrize("name,key", [
    ("gakumas_exam_rs-0.1.0-cp39-abi3-win_amd64.whl", "win32-amd64"),
    ("gakumas_exam_rs-0.1.0-cp39-abi3-macosx_11_0_arm64.whl", "darwin-arm64"),
    ("gakumas_exam_rs-0.1.0-cp39-abi3-macosx_10_12_x86_64.whl", "darwin-x86_64"),
    ("gakumas_exam_rs-0.1.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "linux-x86_64"),
    ("gakumas_exam_rs-0.1.0.tar.gz", None),
])  # fmt: skip
def test_wheel_platform_key(name, key):
    assert er.wheel_platform_key(name) == key


def _release(tmp_path, *, wheel=b"fake wheel", sha=None, key="darwin-arm64", source_hash=None):
    tag = er.TAG_PREFIX + er.source_hash()
    d = tmp_path / "rel" / tag
    d.mkdir(parents=True)
    name = "gakumas_exam_rs-0.1.0-cp39-abi3-macosx_11_0_arm64.whl"
    (d / name).write_bytes(wheel)
    manifest = {"source_hash": source_hash or er.source_hash(),
                "wheels": {key: {"file": name, "sha256": sha or hashlib.sha256(wheel).hexdigest()}}}  # fmt: skip
    (d / "manifest.json").write_text(json.dumps(manifest))
    return (tmp_path / "rel").as_uri()


def test_install_missing_release_falls_back(tmp_path):
    ok, msg = er.install(base=(tmp_path / "nothing").as_uri(), key="darwin-arm64")
    assert not ok and "没有找到预编译包" in msg


def test_install_rejects_other_source_hash(tmp_path):
    ok, msg = er.install(base=_release(tmp_path, source_hash="000000000000"), key="darwin-arm64")
    assert not ok and "源码哈希对不上" in msg


def test_install_rejects_missing_platform(tmp_path):
    ok, msg = er.install(base=_release(tmp_path), key="win32-amd64")
    assert not ok and "没有本机平台" in msg


def test_install_rejects_bad_sha(tmp_path):
    ok, msg = er.install(base=_release(tmp_path, sha="0" * 64), key="darwin-arm64")
    assert not ok and "sha256 对不上" in msg


def test_manifest_from_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for n in ("gakumas_exam_rs-0.1.0-cp39-abi3-win_amd64.whl", "gakumas_exam_rs-0.1.0-cp39-abi3-macosx_11_0_arm64.whl"):
        (dist / n).write_bytes(n.encode())
    m = er.write_manifest(dist)
    assert set(m["wheels"]) == {"win32-amd64", "darwin-arm64"} and m["source_hash"] == er.source_hash()
    assert json.loads((dist / "manifest.json").read_text()) == m
    shutil.rmtree(dist)
