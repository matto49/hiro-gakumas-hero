"""导出 / 核对各契约的 JSON schema 文件。

python -m hiro.contracts.schemas            # 写入 src/hiro/contracts/schemas/*.json
python -m hiro.contracts.schemas --check    # 只核对文件与代码一致（CI / 测试用）
"""

import argparse
import json
import sys
from pathlib import Path

from hiro.contracts import CONTRACTS

OUT = Path(__file__).with_name("schemas")


def dump(cls) -> str:
    return json.dumps(cls.schema(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    stale = []
    for cls in CONTRACTS:
        path = OUT / f"{cls.__name__}.json"
        text = dump(cls)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
            print(path)
    if stale:
        print("schema 文件与代码不一致，请运行 python -m hiro.contracts.schemas：", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
