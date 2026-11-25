"""SIMD OCR 的依赖在哪、齐不齐（只用标准库：安装脚本 tools/assets/simd_ocr.py 和 hiro doctor 也用它，不能依赖 cv2 / numpy）。

依赖目录 deps：HIRO_SIMD_DEPS，缺省仓库根的 .deps。里面放：
- simd-ocr/HiroSimdOcr.dll：辅助程序（tools/build-simd-ocr.ps1 编出来的跨平台 .NET 程序集）；
- SimdPaddleOCR/models/{medium_det.onnx, medium_rec.onnx, rec_keys.txt}：PP-OCRv6 medium 模型；
- dotnet/：安装脚本在用户目录下装的 .NET 10 Runtime（系统里没有 .NET 10 时才用，不改系统 PATH）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HELPER = Path("simd-ocr/HiroSimdOcr.dll")
MODELS = Path("SimdPaddleOCR/models")
MODEL_FILES = ("medium_det.onnx", "medium_rec.onnx", "rec_keys.txt")
RUNTIME = "Microsoft.NETCore.App 10."  # dotnet --list-runtimes 里 .NET 10 Runtime 那行的开头


def deps_dir() -> Path:
    return Path(os.environ.get("HIRO_SIMD_DEPS") or Path(__file__).resolve().parents[3] / ".deps")


def required_files(deps: Path) -> list[Path]:
    """辅助程序和模型要的文件（不含 .NET）。"""
    return [deps / HELPER, *(deps / MODELS / name for name in MODEL_FILES)]


def missing_files(deps: Path) -> list[Path]:
    return [p for p in required_files(deps) if not p.is_file()]


def local_dotnet(deps: Path) -> Path:
    """安装脚本把 .NET 10 Runtime 装到 deps/dotnet；这是里面的 dotnet 可执行文件。"""
    return deps / "dotnet" / ("dotnet.exe" if sys.platform == "win32" else "dotnet")


def has_runtime10(dotnet: str | Path) -> bool:
    """这个 dotnet 带不带 .NET 10 Runtime（只装了 SDK 8 / 9 的机器上 PATH 里也有 dotnet，但跑不了辅助程序）。"""
    try:
        out = subprocess.run(
            [str(dotnet), "--list-runtimes"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.startswith(RUNTIME) for line in out.splitlines())


def find_dotnet(deps: Path) -> tuple[str, dict[str, str]] | None:
    """能跑辅助程序的 dotnet：先系统里的（带 .NET 10 Runtime 的话），再 deps/dotnet 里装的。
    返回 (dotnet 路径, 要额外设的环境变量)：用装在 deps/dotnet 的那份时设 DOTNET_ROOT 指过去。都没有返回 None。"""
    if (system := shutil.which("dotnet")) and has_runtime10(system):
        return system, {}
    local = local_dotnet(deps)
    if local.is_file() and has_runtime10(local):
        return str(local), {"DOTNET_ROOT": str(local.parent)}
    return None


def unusable_reason(deps: Path) -> str | None:
    """SIMD 现在能不能用（不启动辅助进程）：能用返回 None，否则返回白话原因。"""
    if missing := missing_files(deps):
        return f"缺少 {missing[0]}" + (f" 等 {len(missing)} 个文件" if len(missing) > 1 else "")
    if find_dotnet(deps) is None:
        return "找不到 .NET 10 Runtime"
    return None
