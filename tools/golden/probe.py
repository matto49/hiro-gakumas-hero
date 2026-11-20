"""Go 迁移阶段 3 的逐函数对照：在黄金数据的每张截图上跑 Python 版的某个识别函数，结果写成 JSON 行，Go 测试读来比对。

    PYTHONPATH=src python tools/golden/probe.py --golden ~/.cache/hiro/golden/vision --call hiro.vision.classify:template_scene \
        [--args '[...]'] [--kwargs '{...}'] [--crop] [--out FILE]

- --call 是「模块:函数」，函数第一个参数收截图（cv2 的 BGR 数组），后面接 --args / --kwargs（JSON）。
- 截图读 <golden>/<编号>.png（export_vision.py 存的无损像素），按 manifest.jsonl 的顺序；没有 png 的跳过。
- 每行 {"id", "out"} 或 {"id", "error"}（异常类名 + 消息）。out 经 export_vision.jsonable 转成普通 JSON。
- 写到 --out，缺省标准输出。Go 那边（internal/golden）按 --call + 参数做缓存，同一组参数只跑一次。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_vision import jsonable  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--golden", required=True)
    ap.add_argument("--call", required=True)
    ap.add_argument("--args", default="[]")
    ap.add_argument("--kwargs", default="{}")
    ap.add_argument("--ids", default="", help="只跑这些编号（逗号分隔）")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    mod, _, name = a.call.partition(":")
    fn = getattr(importlib.import_module(mod), name)
    args, kwargs = json.loads(a.args), json.loads(a.kwargs)
    golden = Path(a.golden).expanduser()
    only = set(filter(None, a.ids.split(",")))
    out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout  # noqa: SIM115
    try:
        for line in (golden / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            key = json.loads(line).get("id")
            if not key or (only and key not in only):
                continue
            png = golden / f"{key}.png"
            if not png.is_file():
                continue
            img = cv2.imread(str(png), cv2.IMREAD_COLOR)
            try:
                rec = {"id": key, "out": jsonable(fn(img, *args, **kwargs))}
            except Exception as e:  # noqa: BLE001 出错也记：Go 版要同样出错
                rec = {"id": key, "error": f"{type(e).__name__}: {e}"}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
