# 来源：digital-hiro src/hiro/perceive.py（develop ce5085f）
"""离线感知：对截图文件输出结构化状态。

    .venv/bin/python -m hiro.perceive <截图...>
"""

import argparse
import json
import time

import cv2

import hiro  # noqa: F401  兼容层
from hiro.context import ensure_offline_context
from hiro.perception.lesson import read_lesson


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    ensure_offline_context()
    for path in args.images:
        img = cv2.imread(path)
        if img is None:
            print(json.dumps({"image": path, "error": "无法读取"}, ensure_ascii=False))
            continue
        img = cv2.resize(img, (720, 1280)) if img.shape[:2] != (1280, 720) else img
        start = time.perf_counter()
        state = read_lesson(img).to_dict()
        state["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
        print(json.dumps({"image": path, **state}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
