"""准备 HIRO_OCR=ppocr5 要的 PP-OCRv5 模型（检测 + 中日英识别，mobile 版）。

usage: python tools/assets/ppocr_models.py [--out cache/ppocr5]

从 rapidocr 3.x 官方用的 ModelScope 地址下载（校验 SHA256），存到 --out，并把 ONNX 的 IR 版本从 10 改成 8：
KAA 把 onnxruntime 固定在 1.14（Windows 上的 DLL 问题），1.14 只认 IR ≤ 8。模型用的算子集是 opset 14，1.14 能跑；
IR 版本是 ModelProto 的第 1 个字段（字节 08 0A），只改这一个字节。运行时还要装 rapidocr 包（pip install rapidocr）。
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
MODELS = {  # 文件名 → (地址, SHA256)，取自 rapidocr 3.x 的 default_models.yaml
    "ch_PP-OCRv5_det_mobile.onnx": (
        f"{BASE}/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    "ch_PP-OCRv5_rec_mobile.onnx": (
        f"{BASE}/rec/ch_PP-OCRv5_rec_mobile.onnx",
        "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5",
    ),
}


def downgrade_ir(path: Path, target: int = 8) -> None:
    data = bytearray(path.read_bytes())
    if data[0] != 0x08:
        raise SystemExit(f"{path.name}: 开头不是 ir_version 字段（{data[:2].hex()}），不改")
    if data[1] > target:
        data[1] = target
        path.write_bytes(bytes(data))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "cache" / "ppocr5"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, (url, sha) in MODELS.items():
        dst = out / name
        print("下载", url)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            sys.exit(f"{name} 校验不对：{got}（应为 {sha}）")
        dst.write_bytes(data)
        downgrade_ir(dst)
        print("ok", dst)


if __name__ == "__main__":
    main()
