"""python -m tools.ocr_dataset <子命令>。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.ocr_dataset import schema


def dataset_root(arg: str | None = None) -> Path:
    return Path(arg or os.environ.get("HIRO_OCR_DATASET") or Path(__file__).resolve().parents[2].parent / "hiro-ocr-dataset")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ocr_dataset", description="OCR 测试集工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="生成数据仓库骨架")
    p.add_argument("dir", nargs="?")
    p = sub.add_parser("validate", help="校验数据仓库")
    p.add_argument("dir", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        root = schema.init(dataset_root(a.dir))
        print(f"骨架已生成：{root}")
        return 0
    if a.cmd == "validate":
        return schema.main([str(dataset_root(a.dir))])
    return 2


if __name__ == "__main__":
    sys.exit(main())
