"""下载 RapidOCR 后端（HIRO_OCR=rapidocr）用的 PP-OCRv3 日文 / 英文识别模型到 cache/rapidocr，校验 SHA256。

usage: python tools/assets/rapid_models.py [--out cache/rapidocr]

模型原来随 KAA 带（kotonebot-resource/models），去 KAA 后托管在本仓库的 GitHub Release ocr-models-v1：
PaddleOCR 的 PP-OCRv3（Apache-2.0），由 RapidOCR 转成 ONNX，见 Release 里的 NOTICE.txt。检测模型用 rapidocr_onnxruntime 包自带的。
已经下好、校验对的文件不重复下载。安装脚本 setup.sh / setup.ps1 会调它。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://github.com/matto49/hiro-gakumas-hero/releases/download/ocr-models-v1"
MODELS = {  # 文件名 → (地址, SHA256)
    "japan_PP-OCRv3_rec_infer.onnx": (
        f"{BASE}/japan_PP-OCRv3_rec_infer.onnx",
        "329eec1da950c729780712052b50aee602fadbdc985b7b4984394332a5cdd7e2",
    ),
    "en_PP-OCRv3_rec_infer.onnx": (
        f"{BASE}/en_PP-OCRv3_rec_infer.onnx",
        "ef7abd8bd3629ae57ea2c28b425c1bd258a871b93fd2fe7c433946ade9b5d9ea",
    ),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(out: Path) -> list[str]:
    """把缺的 / 校验不对的模型下到 out，返回这次下了哪些（按名字排序）。校验不对就退出（不留坏文件）。"""
    out.mkdir(parents=True, exist_ok=True)
    got = []
    for name, (url, sha) in sorted(MODELS.items()):
        dst = out / name
        if dst.exists() and _sha(dst.read_bytes()) == sha:
            continue
        print("下载", url, flush=True)
        with urllib.request.urlopen(url, timeout=300) as r:
            data = r.read()
        if (h := _sha(data)) != sha:
            sys.exit(f"{name} 校验不对：{h}（应为 {sha}）")
        tmp = dst.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dst)
        got.append(name)
    return got


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "cache" / "rapidocr"))
    args = ap.parse_args()
    got = fetch(Path(args.out))
    print("已下载：" + "、".join(got) if got else "模型都在，校验通过")


if __name__ == "__main__":
    main()
