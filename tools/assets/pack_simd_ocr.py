"""把本机已有的 SIMD OCR 依赖打成发 Release ocr-simd-v1 用的 zip，打印 SHA256（不上传，发布是手动的）。

usage: python tools/assets/pack_simd_ocr.py [--deps .deps] [--out ocr-simd-v1.zip]

zip 里放：
- simd-ocr/：辅助程序（tools/build-simd-ocr.ps1 编出来的 .NET 程序集；不带 .pdb 和 Mac / Linux 的 apphost 可执行文件，只留 dotnet 直接跑的 .dll / .json）；
- SimdPaddleOCR/models/：PP-OCRv6 medium 的 medium_det.onnx、medium_rec.onnx 和 rec_keys.txt（不带 small / 旧版模型）；
- NOTICE.txt 和各上游的许可证文件（SimdPaddleOCR、SimdOnnx 和 PaddleOCR 模型都是 Apache-2.0）。
打包是确定的（固定时间戳、固定顺序）：同样的内容每次打出来的 SHA256 一样，可以先打一次把 SHA256 填进 tools/assets/simd_ocr.py。
发布：gh release create ocr-simd-v1 <zip> --latest=false，再把 SHA256 填进 simd_ocr.py 的 SHA256 常量。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import simd_ocr  # noqa: E402  同目录：用它的 simd_deps 和文件名常量

HELPER_SUFFIXES = (".dll", ".json")  # simd-ocr 里要带的；.pdb 和没有后缀的 apphost（只能在编译它的系统上跑）不带
LICENSES = (  # (deps 下的相对路径, zip 里的名字)
    ("SimdPaddleOCR/LICENSE", "LICENSE.SimdPaddleOCR"),
    ("SimdPaddleOCR/THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.SimdPaddleOCR.md"),
    ("SimdOnnx/LICENSE", "LICENSE.SimdOnnx"),
)
NOTICE = """初星助手 SIMD OCR 资产（Release ocr-simd-v1）

simd-ocr/：HiroSimdOcr 辅助程序（本仓库 tools/simd-ocr，AGPL-3.0），引用 Sdcb.SimdPaddleOCR（Apache-2.0）和 Sdcb.SimdOnnx（Apache-2.0）编译。
SimdPaddleOCR/models/：PaddleOCR PP-OCRv6 medium 检测 / 识别模型的 ONNX 转换，来自 sdcb/SimdPaddleOCR（Apache-2.0）；PaddleOCR 本身是 Apache-2.0。
上游许可证见同目录的 LICENSE.* 与 THIRD-PARTY-NOTICES.*。
"""
EPOCH = (2026, 11, 26, 0, 0, 0)  # 固定时间戳：打包可重复


def entries(deps: Path) -> list[tuple[str, bytes]]:
    """(zip 里的名字, 内容)，按名字排序。缺必要文件就退出。"""
    if missing := simd_ocr.simd_deps.missing_files(deps):
        sys.exit(
            "缺文件（先用 tools/build-simd-ocr.ps1 编好辅助程序、放好模型）：" + "、".join(str(p) for p in missing)
        )
    out: list[tuple[str, bytes]] = []
    helper_dir = deps / simd_ocr.simd_deps.HELPER.parent
    for p in sorted(helper_dir.iterdir()):
        if p.is_file() and p.suffix in HELPER_SUFFIXES:
            out.append((f"simd-ocr/{p.name}", p.read_bytes()))
    for name in simd_ocr.simd_deps.MODEL_FILES:
        out.append((f"SimdPaddleOCR/models/{name}", (deps / simd_ocr.simd_deps.MODELS / name).read_bytes()))
    for rel, name in LICENSES:
        if (deps / rel).is_file():
            out.append((name, (deps / rel).read_bytes()))
    out.append(("NOTICE.txt", NOTICE.encode("utf-8")))
    return sorted(out)


def pack(deps: Path, out: Path) -> str:
    """打包到 out，返回 SHA256。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in entries(deps):
            info = zipfile.ZipInfo(name, EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data, compresslevel=9)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deps", default=None, help="依赖目录（缺省 HIRO_SIMD_DEPS，再缺省仓库根的 .deps）")
    ap.add_argument("--out", default=str(ROOT / simd_ocr.ASSET))
    args = ap.parse_args()
    deps = Path(args.deps) if args.deps else simd_ocr.simd_deps.deps_dir()
    out = Path(args.out)
    sha = pack(deps, out)
    print(f"{out}（{out.stat().st_size / (1 << 20):.1f} MB）")
    print(f"SHA256 {sha}")
    print(f"发布后把 {sha} 填进 tools/assets/simd_ocr.py 的 SHA256 常量")


if __name__ == "__main__":
    main()
