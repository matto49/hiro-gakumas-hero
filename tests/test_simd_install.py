"""Windows 安装时自动带上 SIMD OCR（A15）：下载 / 校验 / 解压辅助程序和模型、没有 .NET 10 就装、装不上退回 RapidOCR 不让安装失败。
全程不连外网：下载用本机 http.server，dotnet-install.ps1 用假的。"""

import hashlib
import http.server
import importlib.util
import json
import threading
import zipfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load("simd_ocr_tool", "tools/assets/simd_ocr.py")


@pytest.fixture(scope="module")
def pack_tool():
    return _load("pack_simd_ocr_tool", "tools/assets/pack_simd_ocr.py")


def make_deps(root: Path, *, apphost=True, extra_models=True) -> Path:
    """模拟本机编好的 .deps：辅助程序（含 .pdb 和 apphost）、models（含不打包的 small / 旧模型）。"""
    helper = root / "simd-ocr"
    helper.mkdir(parents=True)
    for name in ("HiroSimdOcr.dll", "HiroSimdOcr.deps.json", "HiroSimdOcr.runtimeconfig.json", "HiroSimdOcr.pdb"):
        (helper / name).write_bytes(name.encode())
    if apphost:
        (helper / "HiroSimdOcr").write_bytes(b"mach-o")
    models = root / "SimdPaddleOCR/models"
    models.mkdir(parents=True)
    for name in ("medium_det.onnx", "medium_rec.onnx", "rec_keys.txt") + (
        ("small_det.onnx", "cls.onnx") if extra_models else ()
    ):
        (models / name).write_bytes(("model-" + name).encode())
    (root / "SimdPaddleOCR/LICENSE").write_text("Apache License 2.0")
    return root


@pytest.fixture
def server(tmp_path):
    """本机 http.server：把 tmp_path/www 下的文件按路径发出去。"""
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


@pytest.fixture(autouse=True)
def _no_sleep(tool, monkeypatch):
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)


@pytest.fixture
def packed(tmp_path, pack_tool, server):
    """打好的资产放进 http.server，返回 (url, sha)。"""
    www, base = server
    sha = pack_tool.pack(make_deps(tmp_path / "src-deps"), www / "ocr-simd-v1.zip")
    return base + "/ocr-simd-v1.zip", sha


# ---------------------------------------------------------------- 打包


def test_pack_is_reproducible_and_leaves_out_what_windows_cannot_use(tmp_path, pack_tool):
    deps = make_deps(tmp_path / "d")
    sha1 = pack_tool.pack(deps, tmp_path / "a.zip")
    sha2 = pack_tool.pack(deps, tmp_path / "b.zip")
    assert sha1 == sha2 == hashlib.sha256((tmp_path / "a.zip").read_bytes()).hexdigest()
    names = set(zipfile.ZipFile(tmp_path / "a.zip").namelist())
    assert names == {
        "simd-ocr/HiroSimdOcr.dll",
        "simd-ocr/HiroSimdOcr.deps.json",
        "simd-ocr/HiroSimdOcr.runtimeconfig.json",
        "SimdPaddleOCR/models/medium_det.onnx",
        "SimdPaddleOCR/models/medium_rec.onnx",
        "SimdPaddleOCR/models/rec_keys.txt",
        "LICENSE.SimdPaddleOCR",
        "NOTICE.txt",
    }, "不带 .pdb、Mac 的 apphost、没用到的模型"


def test_pack_refuses_when_deps_are_incomplete(tmp_path, pack_tool):
    deps = make_deps(tmp_path / "d")
    (deps / "SimdPaddleOCR/models/medium_rec.onnx").unlink()
    with pytest.raises(SystemExit, match=r"medium_rec\.onnx"):
        pack_tool.pack(deps, tmp_path / "a.zip")


# ---------------------------------------------------------------- 下载 / 校验 / 解压


def test_release_constants_are_pinned_or_explicitly_pending(tool):
    assert tool.URL.startswith("https://github.com/matto49/hiro-gakumas-hero/releases/download/ocr-simd-v1/")
    assert tool.URL.endswith("/" + tool.ASSET) and tool.ASSET == "ocr-simd-v1.zip"
    assert tool.pinned(tool.SHA256) or tool.SHA256 == "PENDING-fill-after-release"  # 发资产后要填


def test_pending_sha_skips_download_without_touching_the_network(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda *a, **k: pytest.fail("不该联网"))
    with pytest.raises(tool.InstallError, match="还没发布"):
        tool.fetch_asset(tmp_path / "deps", sha="PENDING-fill-after-release")


def test_fetch_installs_verifies_and_is_idempotent(tool, tmp_path, packed):
    url, sha = packed
    deps = tmp_path / "deps"
    assert tool.fetch_asset(deps, url=url, sha=sha) is True
    assert tool.simd_deps.missing_files(deps) == []
    assert (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes() == b"model-medium_det.onnx"
    assert (deps / tool.STAMP).read_text().strip() == sha
    assert (deps / "simd-ocr/NOTICE.txt").is_file() and (deps / "simd-ocr/LICENSE.SimdPaddleOCR").is_file()
    assert not (deps / "SimdPaddleOCR/models/small_det.onnx").exists()
    assert list(deps.glob("simd-*")) == [deps / "simd-ocr"], "临时目录要清掉"
    assert tool.fetch_asset(deps, url="http://127.0.0.1:9/never", sha=sha) is False  # 已是这个版本：不下载


def test_fetch_replaces_another_version_but_not_a_hand_built_install(tool, tmp_path, packed):
    url, sha = packed
    deps = make_deps(tmp_path / "deps")  # 开发者自己编的：没有 stamp
    (deps / "SimdPaddleOCR/models/medium_det.onnx").write_bytes(b"mine")
    assert tool.fetch_asset(deps, url=url, sha=sha) is False
    assert (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes() == b"mine"
    assert tool.fetch_asset(deps, url=url, sha=sha, force=True) is True  # --force 才换
    assert (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes() == b"model-medium_det.onnx"
    (deps / tool.STAMP).write_text("old-version\n")  # 另一个版本的资产
    (deps / "SimdPaddleOCR/models/medium_det.onnx").write_bytes(b"old")
    assert tool.fetch_asset(deps, url=url, sha=sha) is True
    assert (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes() == b"model-medium_det.onnx"


def _fail_swap_into(monkeypatch, dirname):
    """让「把新解出来的 dirname 换进 deps」这一步失败（模拟目录被占用 / 杀毒软件锁住）。"""
    real = Path.replace

    def replace(self, target):
        if self.name == dirname and self.parent.name == "out":
            raise PermissionError(13, "目录被占用", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)


def _old_install(tool, deps):
    """装好了的旧版本（另一个版本的资产）：内容和新资产不同，方便看有没有被换掉。"""
    make_deps(deps)
    (deps / "simd-ocr/HiroSimdOcr.dll").write_bytes(b"OLD-helper")
    (deps / "SimdPaddleOCR/models/medium_det.onnx").write_bytes(b"OLD-model")
    (deps / tool.STAMP).write_text("old-version\n")


def _state(deps):
    return (deps / "simd-ocr/HiroSimdOcr.dll").read_bytes(), (
        deps / "SimdPaddleOCR/models/medium_det.onnx"
    ).read_bytes()


@pytest.mark.parametrize("failing", ["simd-ocr", "SimdPaddleOCR"])
def test_two_dirs_are_updated_together_or_rolled_back(tool, tmp_path, packed, monkeypatch, failing):
    """CodeRabbit #115：两个目录当一次更新。任一个换不进去，已经换进去的要恢复，旧 stamp 保留；
    不能半新半旧还写上新 stamp（否则下次因为四个文件都在而直接跳过）。"""
    url, sha = packed
    deps = tmp_path / "deps"
    _old_install(tool, deps)
    with monkeypatch.context() as m:
        _fail_swap_into(m, failing)
        with pytest.raises(PermissionError):
            tool.fetch_asset(deps, url=url, sha=sha)
    assert _state(deps) == (b"OLD-helper", b"OLD-model")
    assert (deps / tool.STAMP).read_text().strip() == "old-version"
    assert not any(p.name.startswith("simd-") and p.name != "simd-ocr" for p in deps.iterdir()), "临时目录要清掉"
    assert tool.fetch_asset(deps, url=url, sha=sha) is True  # 问题解决后下次能正常装好
    assert _state(deps) == (b"HiroSimdOcr.dll", b"model-medium_det.onnx")
    assert (deps / tool.STAMP).read_text().strip() == sha


def test_backup_is_kept_when_the_rollback_itself_fails(tool, tmp_path, packed, monkeypatch):
    url, sha = packed
    deps = tmp_path / "deps"
    _old_install(tool, deps)
    real = Path.replace

    def replace(self, target):
        if self.name == "old-simd-ocr":  # 恢复旧的 simd-ocr 时失败
            raise PermissionError(13, "目录被占用", str(target))
        return real(self, target)

    with monkeypatch.context() as m:
        _fail_swap_into(m, "SimdPaddleOCR")
        real_fail = Path.replace
        m.setattr(
            Path,
            "replace",
            lambda self, target: replace(self, target) if self.name == "old-simd-ocr" else real_fail(self, target),
        )
        with pytest.raises(tool.RollbackFailed, match="备份在"):
            tool.fetch_asset(deps, url=url, sha=sha)
    backups = list(deps.glob("simd-*/old-simd-ocr"))
    assert backups and (backups[0] / "HiroSimdOcr.dll").read_bytes() == b"OLD-helper", "恢复没做完时备份不能删"


def test_failed_first_install_leaves_neither_directory(tool, tmp_path, packed, monkeypatch):
    url, sha = packed
    deps = tmp_path / "deps"
    _fail_swap_into(monkeypatch, "SimdPaddleOCR")
    with pytest.raises(PermissionError):
        tool.fetch_asset(deps, url=url, sha=sha)
    assert not (deps / "simd-ocr").exists() and not (deps / "SimdPaddleOCR").exists()
    assert not (deps / tool.STAMP).exists()


def test_fetch_rejects_wrong_sha_and_keeps_existing_files(tool, tmp_path, packed):
    url, _sha = packed
    deps = make_deps(tmp_path / "deps")
    (deps / tool.STAMP).write_text("old-version\n")
    before = (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes()
    with pytest.raises(tool.InstallError, match="校验不对"):
        tool.fetch_asset(deps, url=url, sha="0" * 64)
    assert (deps / "SimdPaddleOCR/models/medium_det.onnx").read_bytes() == before
    assert not any(p.name.startswith("simd-") and p.name != "simd-ocr" for p in deps.iterdir())


def _evil_zip(path: Path, member: str) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, "x")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fetch_rejects_path_traversal(tool, tmp_path, server):
    www, base = server
    sha = _evil_zip(www / "evil.zip", "../escaped.txt")
    with pytest.raises(tool.InstallError, match="越界"):
        tool.fetch_asset(tmp_path / "deps", url=base + "/evil.zip", sha=sha)
    assert not (tmp_path / "escaped.txt").exists()


def test_fetch_rejects_zip_without_the_expected_files(tool, tmp_path, server):
    www, base = server
    sha = _evil_zip(www / "bad.zip", "simd-ocr/HiroSimdOcr.dll")
    with pytest.raises(tool.InstallError, match="缺"):
        tool.fetch_asset(tmp_path / "deps", url=base + "/bad.zip", sha=sha)


def test_fetch_reports_not_a_zip(tool, tmp_path, server):
    www, base = server
    (www / "junk.zip").write_bytes(b"not a zip")
    sha = hashlib.sha256(b"not a zip").hexdigest()
    with pytest.raises(tool.InstallError, match="不是有效的 zip"):
        tool.fetch_asset(tmp_path / "deps", url=base + "/junk.zip", sha=sha)


def test_download_failure_retries_then_reports(tool, tmp_path, server):
    _, base = server
    with pytest.raises(tool.InstallError, match="下载"):
        tool.fetch_asset(tmp_path / "deps", url=base + "/missing.zip", sha="a" * 64)  # 404 → HTTPError（OSError）


def test_download_has_an_overall_deadline(tool, tmp_path, monkeypatch):
    class Slow:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * 10

    clock = iter(range(0, 1000, 100))
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda url, timeout: Slow())
    monkeypatch.setattr(tool.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="超过"):
        tool.download("http://x/y", tmp_path / "y", deadline=250)
    assert not list(tmp_path.glob("y*")), "超时后不留半截文件"


def test_download_passes_a_socket_timeout(tool, tmp_path, monkeypatch):
    seen = {}

    class Done:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b""

    def fake(url, timeout):
        seen["timeout"] = timeout
        return Done()

    monkeypatch.setattr(tool.urllib.request, "urlopen", fake)
    tool.download("http://x/y", tmp_path / "y")
    assert seen["timeout"] == tool.CONNECT_TIMEOUT


# ---------------------------------------------------------------- .NET 10 Runtime


def _fake_runtimes(monkeypatch, tool, table):
    """table: dotnet 路径 → --list-runtimes 的输出。"""

    def run(cmd, **kw):
        out = table.get(cmd[0])
        if out is None:
            raise FileNotFoundError(cmd[0])
        return type("R", (), {"stdout": out})()

    monkeypatch.setattr(tool.simd_deps.subprocess, "run", run)


def test_dotnet_prefers_system_runtime_10(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.simd_deps.shutil, "which", lambda n: "C:/dotnet/dotnet.exe")
    _fake_runtimes(monkeypatch, tool, {"C:/dotnet/dotnet.exe": "Microsoft.NETCore.App 10.0.1 [C:/x]\n"})
    assert tool.simd_deps.find_dotnet(tmp_path) == ("C:/dotnet/dotnet.exe", {})


def test_system_dotnet_without_runtime_10_falls_to_the_local_install(tool, tmp_path, monkeypatch):
    local = tool.simd_deps.local_dotnet(tmp_path)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"")
    monkeypatch.setattr(tool.simd_deps.shutil, "which", lambda n: "sys-dotnet")
    _fake_runtimes(
        monkeypatch,
        tool,
        {"sys-dotnet": "Microsoft.NETCore.App 8.0.5 [x]\n", str(local): "Microsoft.NETCore.App 10.0.0 [y]\n"},
    )
    assert tool.simd_deps.find_dotnet(tmp_path) == (str(local), {"DOTNET_ROOT": str(local.parent)})


def test_no_usable_dotnet_means_none(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.simd_deps.shutil, "which", lambda n: None)
    assert tool.simd_deps.find_dotnet(tmp_path) is None
    assert "找不到 .NET 10" in tool.simd_deps.unusable_reason(make_deps(tmp_path / "d"))


def test_unusable_reason_names_the_missing_file(tool, tmp_path):
    assert "HiroSimdOcr.dll" in tool.simd_deps.unusable_reason(tmp_path)


def _installer(tool, monkeypatch, tmp_path, *, rc=0, creates=True, has_dotnet=lambda: False):
    """假的 dotnet-install.ps1：记录命令行；按需在 -InstallDir 里放一个假 dotnet。返回命令记录。"""
    calls = []
    monkeypatch.setattr(
        tool,
        "_download_retry",
        lambda url, dst, **kw: (
            dst.parent.mkdir(parents=True, exist_ok=True),
            dst.write_text("ps1"),
            calls.append(("get", url)),
        ),
    )

    def run(cmd, **kw):
        calls.append(("run", cmd, kw.get("timeout")))
        if creates:
            flag = "-InstallDir" if "-InstallDir" in cmd else "--install-dir"
            target = Path(cmd[cmd.index(flag) + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / tool.simd_deps.local_dotnet(tmp_path).name).write_bytes(b"")
        return type("R", (), {"returncode": rc})()

    monkeypatch.setattr(tool.subprocess, "run", run)

    def find(deps):
        if tool.simd_deps.local_dotnet(deps).exists() and calls and any(c[0] == "run" for c in calls):
            return str(tool.simd_deps.local_dotnet(deps)), {"DOTNET_ROOT": str(deps / "dotnet")}
        return None

    monkeypatch.setattr(tool.simd_deps, "find_dotnet", find)
    return calls


def test_install_dotnet_uses_the_official_script_without_admin_or_path(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "win32")
    calls = _installer(tool, monkeypatch, tmp_path)
    assert tool.install_dotnet(tmp_path / "deps") is True
    (_, url), (_, cmd, timeout) = calls
    assert url == "https://dot.net/v1/dotnet-install.ps1"
    assert cmd[:3] == ["powershell", "-NoProfile", "-ExecutionPolicy"] and "-NoPath" in cmd
    assert cmd[cmd.index("-Runtime") + 1] == "dotnet" and cmd[cmd.index("-Channel") + 1] == "10.0"
    assert Path(cmd[cmd.index("-InstallDir") + 1]) == tmp_path / "deps" / "dotnet"
    assert timeout == tool.DOTNET_TIMEOUT
    assert not (tmp_path / "deps/tmp/dotnet-install.ps1").exists()


def test_install_dotnet_is_skipped_when_a_runtime_exists(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.simd_deps, "find_dotnet", lambda deps: ("dotnet", {}))
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("已有 .NET 10 不该再装"))
    assert tool.install_dotnet(tmp_path) is False


def test_install_dotnet_on_mac_uses_the_sh_script(tool, tmp_path, monkeypatch):
    """11-26 起 Mac 也装 SIMD：用官方 dotnet-install.sh，同样装到 deps/dotnet、不改 PATH、不要管理员权限。"""
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    calls = _installer(tool, monkeypatch, tmp_path)
    assert tool.install_dotnet(tmp_path / "deps") is True
    (_, url), (_, cmd, _timeout) = calls
    assert url == "https://dot.net/v1/dotnet-install.sh"
    assert cmd[0] == "bash" and "--no-path" in cmd
    assert cmd[cmd.index("--runtime") + 1] == "dotnet" and cmd[cmd.index("--channel") + 1] == "10.0"
    assert Path(cmd[cmd.index("--install-dir") + 1]) == tmp_path / "deps" / "dotnet"
    assert not (tmp_path / "deps/tmp/dotnet-install.sh").exists()


def test_install_dotnet_failures_become_install_errors(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "win32")
    _installer(tool, monkeypatch, tmp_path, rc=1)
    with pytest.raises(tool.InstallError, match="退出码 1"):
        tool.install_dotnet(tmp_path / "deps")
    _installer(tool, monkeypatch, tmp_path, creates=False)
    with pytest.raises(tool.InstallError, match="找不到能用的"):
        tool.install_dotnet(tmp_path / "deps2")

    def timeout(cmd, **kw):
        raise tool.subprocess.TimeoutExpired(cmd, 1)

    _installer(tool, monkeypatch, tmp_path)
    monkeypatch.setattr(tool.subprocess, "run", timeout)
    with pytest.raises(tool.InstallError, match="没能跑完"):
        tool.install_dotnet(tmp_path / "deps3")


# ---------------------------------------------------------------- 入口：失败只返回非零，不抛


def test_main_does_nothing_off_windows_and_mac(tool, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.sys, "platform", "linux")
    monkeypatch.setattr(tool, "run", lambda *a, **k: pytest.fail("Linux 上什么都不装"))
    assert tool.main(["--deps", str(tmp_path)]) == 0
    assert "跳过" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_main_on_windows_reports_failure_but_does_not_raise(tool, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.sys, "platform", "win32")
    monkeypatch.setattr(tool.simd_deps.shutil, "which", lambda n: None)
    monkeypatch.setattr(tool, "SHA256", "PENDING-fill-after-release")
    monkeypatch.setattr(tool, "_download_retry", lambda *a, **k: (_ for _ in ()).throw(tool.InstallError("网络不通")))
    rc = tool.main(["--deps", str(tmp_path / "deps")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "RapidOCR" in out and "还没发布" in out and "网络不通" in out
    assert tool.main(["--deps", str(tmp_path / "deps"), "--no-dotnet"]) == 1


def test_main_on_windows_succeeds_end_to_end(tool, tmp_path, monkeypatch, capsys, packed):
    url, sha = packed
    monkeypatch.setattr(tool.sys, "platform", "win32")
    monkeypatch.setattr(tool, "URL", url)
    monkeypatch.setattr(tool, "SHA256", sha)
    monkeypatch.setattr(tool.simd_deps.shutil, "which", lambda n: "dotnet")
    _fake_runtimes(monkeypatch, tool, {"dotnet": "Microsoft.NETCore.App 10.0.0 [x]\n"})
    assert tool.main(["--deps", str(tmp_path / "deps")]) == 0
    assert "SIMD OCR 可用" in capsys.readouterr().out
    assert tool.simd_deps.unusable_reason(tmp_path / "deps") is None


def test_one_failed_part_does_not_block_the_other(tool, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(tool, "fetch_asset", lambda *a, **k: (_ for _ in ()).throw(tool.InstallError("没网")))
    monkeypatch.setattr(tool, "install_dotnet", lambda deps: seen.append("dotnet") or True)
    problems = tool.run(tmp_path)
    assert seen == ["dotnet"] and problems == ["辅助程序 / 模型没装成：没网"]


# ---------------------------------------------------------------- 运行时：SimdOcr 用装好的 .NET


def test_simd_ocr_starts_helper_with_dotnet_root_for_the_local_runtime(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from hiro.vision import simd_deps, simd_ocr

    deps = make_deps(tmp_path / "deps")
    local = simd_deps.local_dotnet(deps)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"")
    monkeypatch.setenv("HIRO_SIMD_DEPS", str(deps))
    monkeypatch.setattr(simd_deps.shutil, "which", lambda n: None)
    monkeypatch.setattr(simd_deps, "has_runtime10", lambda d: True)
    started = {}

    class FakeProc:
        stdin = None
        stdout = iter([json.dumps({"ready": True}) + "\n"])
        stderr = iter(["[metal] device: Apple M4\n"])

        def poll(self):
            return 0

    def popen(cmd, **kw):
        started.update(cmd=cmd, env=kw["env"])
        return FakeProc()

    monkeypatch.setattr(simd_ocr.subprocess, "Popen", popen)
    simd_ocr.SimdOcr()
    assert started["cmd"][0] == str(local)
    assert started["env"]["DOTNET_ROOT"] == str(local.parent)


def test_simd_ocr_errors_name_the_missing_runtime(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from hiro.vision import simd_deps, simd_ocr

    deps = make_deps(tmp_path / "deps")
    monkeypatch.setenv("HIRO_SIMD_DEPS", str(deps))
    monkeypatch.setattr(simd_deps.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match=r"\.NET 10 Runtime"):
        simd_ocr.SimdOcr()
    with pytest.raises(RuntimeError, match="缺少"):
        monkeypatch.setenv("HIRO_SIMD_DEPS", str(tmp_path / "empty"))
        simd_ocr.SimdOcr()
