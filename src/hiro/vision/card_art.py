# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""手牌卡面插图识别（灰度归一化相关）。

KAA 的 HOG 图库对「不可打出」的卡失效：游戏会把这类卡整体压暗，HOG 距离排不进前 60。
灰度后零均值、单位方差再做相关，对亮度/对比度变化不敏感：同一张灰卡，正确答案 0.83，次佳 0.54。
全部 2000+ 卡面预先裁成与截图相同的半卡区域并展平成矩阵，查询是一次矩阵乘法（约 10ms）。
"""

import os
from functools import lru_cache

from hiro.vision import assets
from hiro.vision.hand_slots import CARD_OFFSET, CARD_SCALE

import cv2
import numpy as np
from cv2.typing import MatLike

QUERY_SIZE = (28, 74)  # (w, h)，半卡区域 57x148 缩小一半


def _normalize(img: MatLike) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, QUERY_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    return (g / (np.linalg.norm(g) + 1e-6)).ravel()


CACHE = os.path.join("cache", "card_art_index.npz")


@lru_cache(maxsize=1)
def _index() -> tuple[list[str], np.ndarray]:
    root = assets.card_art_dir()
    if root is None:
        return [], np.zeros((0, 1), np.float32)  # 没有卡面素材：卡面匹配关闭，只按卡名认
    folder = str(root)
    stamp = f"{len(os.listdir(folder))}-{int(os.path.getmtime(folder))}-{QUERY_SIZE}"
    if os.path.exists(CACHE):
        data = np.load(CACHE, allow_pickle=False)
        if str(data["stamp"]) == stamp:
            return list(data["keys"]), data["matrix"]
    h = int(CARD_OFFSET[1] / CARD_SCALE)
    w = int(CARD_OFFSET[0] / CARD_SCALE)
    keys, rows = [], []
    for name in sorted(os.listdir(folder)):
        img = cv2.imread(os.path.join(folder, name))
        if img is None:
            continue
        half = img[img.shape[0] - h :, img.shape[1] // 2 - w : img.shape[1] // 2]
        keys.append(name.rsplit(".", 1)[0])
        rows.append(_normalize(half))
    matrix = np.stack(rows)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    np.savez(CACHE, keys=np.array(keys), matrix=matrix, stamp=np.array(stamp))
    return keys, matrix


def match_card_art(crop: MatLike, k: int = 3) -> list[tuple[str, float]]:
    keys, matrix = _index()
    if not keys:
        return [("", 0.0)]  # 没有卡面素材：一条 0 分占位（低于各处阈值），调用方不用判空
    scores = matrix @ _normalize(crop)
    top = np.argsort(-scores)[:k]
    return [(keys[i], float(scores[i])) for i in top]
