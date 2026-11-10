# 来源：digital-hiro src/hiro/eval_hand.py（develop ce5085f）
"""对数据集中所有 HIF 考试帧跑手牌识别，统计识别率与耗时，导出卡面对照图供人工核对。

    .venv/bin/python -m hiro.eval_hand datasets/<目录>
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

import hiro  # noqa: F401
from hiro.vision.hif import read_hand
from hiro.vision.offline import load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    args = parser.parse_args()
    root = Path(args.root)
    frames = sorted((root / "frames").glob("*HIF_EXAM.png"))
    total, missing, ms, names = 0, 0, [], Counter()
    tiles = []
    for f in frames:
        img = load(cv2.imread(str(f)))
        t = time.perf_counter()
        hand, miss = read_hand(img)
        ms.append((time.perf_counter() - t) * 1000)
        total += len(hand)
        missing += miss
        for c in hand:
            names[c.get("name")] += 1
            if c.get("box") and len(tiles) < 400:
                x, y, w, h = c["box"]
                tile = cv2.resize(img[y : y + h, x : x + w], (57, 148))
                label = np.full((24, 57, 3), 255, np.uint8)
                cv2.putText(label, f"{c['art_score']:.2f}", (2, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
                tiles.append(np.vstack([tile, label]))
    summary = {"frames": len(frames), "cards": total, "unrecognized": missing,
               "ms_median": round(sorted(ms)[len(ms) // 2], 1) if ms else None, "names": dict(names.most_common())}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if tiles:
        rows = [np.hstack(tiles[i : i + 20] + [np.zeros_like(tiles[0])] * (20 - len(tiles[i : i + 20]))) for i in range(0, len(tiles), 20)]
        cv2.imwrite(str(root / "hand_tiles.png"), np.vstack(rows))


if __name__ == "__main__":
    main()
