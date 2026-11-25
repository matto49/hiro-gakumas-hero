"""装 Windows 缺省的文字识别后端 SIMD PaddleOCR（PP-OCRv6 medium）：辅助程序、模型、.NET 10 Runtime。

usage: python tools/assets/simd_ocr.py [--deps DIR] [--no-dotnet] [--force]

只在 Windows 上做事（macOS 用 Apple Vision，什么都不装；其他系统用 RapidOCR）。装的东西都在依赖目录 deps 下
（HIRO_SIMD_DEPS，缺省仓库根的 .deps），不要管理员权限、不改系统 PATH：
1. 辅助程序 + 模型：本仓库 GitHub Release ocr-simd-v1 的一个 zip（tools/assets/pack_simd_ocr.py 打的），下载、校验 SHA256、解压到
   deps/simd-ocr 和 deps/SimdPaddleOCR/models；已经装好且是同一个版本就不重复下载。
2. .NET 10 Runtime：系统里已有就用系统的；没有就用微软官方的 dotnet-install.ps1 装到 deps/dotnet（运行时 hiro.vision.simd_deps 给辅助进程设 DOTNET_ROOT）。

SIMD 是「装好才用」：这个脚本失败不算安装失败。退出码 0 = SIMD 可用（或不是 Windows、不用装）；1 = 没装成，识别会退回 RapidOCR，原因打在输出里。
安装脚本 scripts/setup.ps1 和桌面版首次安装都把非零当成「可选步骤没做成」，只提示、不中断。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG = "ocr-simd-v1"
ASSET = "ocr-simd-v1.zip"
URL = f"https://github.com/matto49/hiro-gakumas-hero/releases/download/{TAG}/{ASSET}"
# 资产还没发布：发了 Release 之后，把 pack_simd_ocr.py 打印的 SHA256 填到这里（填之前下载会直接跳过，识别用 RapidOCR）
SHA256 = "0812bc5457e23c24bf30b6b0a8ca356fec95763fc6f5e6f322471b189edc5c8f"  # Release ocr-simd-v1（11-26 发）
DOTNET_INSTALL_URL = "https://dot.net/v1/dotnet-install.ps1"
DOTNET_CHANNEL = "10.0"
TOP_DIRS = ("simd-ocr", "SimdPaddleOCR")  # zip 里的两个顶层目录；顶层的文件（NOTICE、许可证）装好后放进 deps/simd-ocr
STAMP = ".simd-asset-sha256"  # 装好的资产的 SHA256，写在 deps 根（不在会被整个换掉的两个目录里）；和 SHA256 对得上就不重复下载

CONNECT_TIMEOUT = 30  # 连不上 / 一次读卡住超过这么久就放弃这次
DOWNLOAD_DEADLINE = 1800  # 一个文件整个下载最多这么久
ATTEMPTS = 3
BACKOFF = 5
DOTNET_TIMEOUT = 900  # dotnet-install.ps1 最多跑这么久


def _simd_deps():
    """hiro.vision.simd_deps（只用标准库）按路径加载：本脚本可以用任何 Python 跑，不要求先装好 hiro。"""
    spec = importlib.util.spec_from_file_location("hiro_simd_deps", ROOT / "src/hiro/vision/simd_deps.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


simd_deps = _simd_deps()


class InstallError(Exception):
    """没装成；消息是给人看的原因。"""


def pinned(sha: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", sha) is not None


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dst: Path, *, timeout: float = CONNECT_TIMEOUT, deadline: float = DOWNLOAD_DEADLINE) -> None:
    """下到 dst（先写 .part 再改名）。连接 / 读超时 timeout 秒、整个下载超过 deadline 秒就报错。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    end = time.monotonic() + deadline
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, part.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                if time.monotonic() > end:
                    raise TimeoutError(f"下载超过 {deadline:.0f} 秒")
        part.replace(dst)
    finally:
        part.unlink(missing_ok=True)


def _download_retry(url: str, dst: Path, **kw) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            download(url, dst, **kw)
            return
        except OSError as e:  # URLError / TimeoutError / 连接中断都是 OSError
            if attempt == ATTEMPTS:
                raise InstallError(f"下载 {url} 失败：{e}") from e
            print(f"  下载没成功（{e}），{BACKOFF * attempt} 秒后重试（{attempt}/{ATTEMPTS}）", flush=True)
            time.sleep(BACKOFF * attempt)


def _extract(zf: zipfile.ZipFile, out: Path) -> None:
    """解到 out；有路径越界的条目（../ 或绝对路径）就整个拒绝。"""
    root = out.resolve()
    for info in zf.infolist():
        target = (out / info.filename).resolve()
        if root != target and root not in target.parents:
            raise InstallError(f"资产里有越界路径 {info.filename!r}，不解压")
    zf.extractall(out)


def _check_layout(staging: Path) -> None:
    missing = simd_deps.missing_files(staging)
    if missing:
        raise InstallError("资产里缺 " + "、".join(str(p.relative_to(staging)) for p in missing))


class RollbackFailed(InstallError):
    """换入新版本失败、恢复旧版本也没做完：旧文件的备份还在临时目录里，不能删。"""


def _swap_in(staging: Path, deps: Path, work: Path, sha: str) -> None:
    """把 staging 里的 TOP_DIRS 当一次更新换进 deps：都换成功才写新 stamp；任一步失败就把已经换进去的恢复成旧的
    （旧的备份在 work 里，提交成功之前不删），再把原来的异常抛出去。恢复也没做完抛 RollbackFailed（备份留着）。
    换的过程中 stamp 先写成 updating：中途进程被杀，下次因为 stamp 对不上会重新装，不会把半新半旧当成装好了。"""
    stamp = deps / STAMP
    prev_stamp = stamp.read_text() if stamp.is_file() else None
    state: dict[str, list[bool]] = {}  # 目录名 → [旧的已经挪到备份, 新的已经放进 deps]
    try:
        stamp.write_text("updating\n")
        for name in TOP_DIRS:
            cur, old = deps / name, work / f"old-{name}"
            state[name] = flags = [False, False]
            if cur.exists():
                cur.replace(old)
                flags[0] = True
            (staging / name).replace(cur)
            flags[1] = True
        stamp.write_text(sha + "\n")
    except BaseException:
        try:
            for name, (moved_old, placed_new) in reversed(state.items()):
                if placed_new:
                    shutil.rmtree(deps / name)
                if moved_old:
                    (work / f"old-{name}").replace(deps / name)
            if prev_stamp is None:
                stamp.unlink(missing_ok=True)
            else:
                stamp.write_text(prev_stamp)
        except OSError as e:
            raise RollbackFailed(
                f"换入新版本失败、恢复旧版本也没做完：{e}；旧文件的备份在 {work}，可以手动拷回 {deps}"
            ) from e
        raise


def fetch_asset(deps: Path, *, url: str | None = None, sha: str | None = None, force: bool = False) -> bool:
    """装辅助程序和模型到 deps。已经是这个版本就不动，返回 False；这次装了返回 True；没装成抛 InstallError。
    url / sha 缺省取模块常量 URL / SHA256（测试里传本机 http.server 的）。"""
    url, sha = url or URL, sha or SHA256
    if not pinned(sha):
        raise InstallError(f"{TAG} 资产还没发布（SHA256 没填）")
    stamp = deps / STAMP
    # 文件齐了、且不是别的版本的资产（没有 stamp 的是开发者自己用 build-simd-ocr.ps1 编的：不覆盖，要换用 --force）就不动
    if not force and not simd_deps.missing_files(deps) and (not stamp.is_file() or stamp.read_text().strip() == sha):
        return False
    deps.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="simd-", dir=deps))
    keep_work = False
    try:
        zpath = work / ASSET
        print(f"下载 {url}", flush=True)
        _download_retry(url, zpath)
        if (got := _sha_file(zpath)) != sha:
            raise InstallError(f"{ASSET} 校验不对：{got}（应为 {sha}）")
        staging = work / "out"
        try:
            with zipfile.ZipFile(zpath) as zf:
                _extract(zf, staging)
        except zipfile.BadZipFile as e:
            raise InstallError(f"{ASSET} 不是有效的 zip：{e}") from e
        _check_layout(staging)
        for extra in staging.iterdir():  # 许可证 / NOTICE 跟着辅助程序放
            if extra.is_file():
                shutil.move(extra, staging / "simd-ocr" / extra.name)
        _swap_in(staging, deps, work, sha)
        return True
    except RollbackFailed:
        keep_work = True
        raise
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def install_dotnet(deps: Path, *, timeout: float = DOTNET_TIMEOUT) -> bool:
    """系统里或 deps/dotnet 里已经有 .NET 10 Runtime 就不装，返回 False；装了返回 True；没装成抛 InstallError。
    用微软官方 dotnet-install.ps1，装到 deps/dotnet：不要管理员权限、不改系统 PATH（-NoPath）。"""
    if simd_deps.find_dotnet(deps) is not None:
        return False
    target = deps / "dotnet"
    script = deps / "tmp" / "dotnet-install.ps1"
    print(f"下载 {DOTNET_INSTALL_URL}（.NET {DOTNET_CHANNEL} Runtime 约 30 MB）", flush=True)
    _download_retry(DOTNET_INSTALL_URL, script, deadline=120)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    cmd += ["-Runtime", "dotnet", "-Channel", DOTNET_CHANNEL, "-InstallDir", str(target), "-NoPath"]
    try:
        r = subprocess.run(cmd, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        raise InstallError(f"dotnet-install.ps1 没能跑完：{e}") from e
    finally:
        script.unlink(missing_ok=True)
    if r.returncode:
        raise InstallError(f"dotnet-install.ps1 退出码 {r.returncode}")
    if simd_deps.find_dotnet(deps) is None:
        raise InstallError(f"装完了但在 {target} 里找不到能用的 .NET {DOTNET_CHANNEL} Runtime")
    return True


def run(
    deps: Path, *, dotnet: bool = True, force: bool = False, url: str | None = None, sha: str | None = None
) -> list[str]:
    """两样都试一遍（一样失败不挡着另一样），返回没装成的原因（空 = 都好了）。"""
    problems = []
    try:
        new = fetch_asset(deps, url=url, sha=sha, force=force)
        print("辅助程序和模型：" + ("装好了（新装）" if new else "已经是最新"))
    except (InstallError, OSError, zipfile.BadZipFile) as e:
        problems.append(f"辅助程序 / 模型没装成：{e}")
    if dotnet:
        try:
            print(".NET 10 Runtime：" + ("装好了（新装）" if install_dotnet(deps) else "已经有"))
        except (InstallError, OSError) as e:
            problems.append(f".NET 10 Runtime 没装成：{e}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deps", default=None, help="依赖目录（缺省 HIRO_SIMD_DEPS，再缺省仓库根的 .deps）")
    ap.add_argument("--no-dotnet", action="store_true", help="不装 .NET 10 Runtime（只装辅助程序和模型）")
    ap.add_argument("--force", action="store_true", help="已经装好也重新下载")
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        print("SIMD OCR 只在 Windows 上装（macOS 用 Apple Vision，其他系统用 RapidOCR）：跳过")
        return 0
    deps = Path(args.deps) if args.deps else simd_deps.deps_dir()
    problems = run(deps, dotnet=not args.no_dotnet, force=args.force)
    if why := simd_deps.unusable_reason(deps):
        problems.append(f"SIMD 现在用不了：{why}")
    if problems:
        # 都打到标准输出：安装脚本把 stderr 当错误记录，这里不算安装错误
        print("SIMD OCR 没装成，文字识别用 RapidOCR（不影响使用；网络好了重跑本脚本即可）：")
        for p in dict.fromkeys(problems):
            print("  - " + p)
        return 1
    print(f"SIMD OCR 可用（{deps}）：文字识别缺省用 SIMD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
