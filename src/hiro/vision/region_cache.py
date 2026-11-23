"""按区域缓存识别结果：同一区域的画面与上一次几乎相同时直接复用结果，跳过模板匹配 / OCR。

考试里连续几帧之间，状态栏、P 道具、饮料、手牌、本回合属性大多不变；只有变了的区域才需要重读。
比较用全分辨率灰度图：差值超过 PIXEL_DIFF 的像素不超过 MAX_CHANGED 个才算相同。平均差不行——
状态值 18→19 只改了一个小字，整块的平均差不到 2。一次比较 < 0.2ms。
"""

import copy

import cv2
import numpy as np
from cv2.typing import MatLike

PIXEL_DIFF = 40  # JPG 噪声 < 20；数字笔画处的变化 > 100
MAX_CHANGED = 3


class RegionCache:
    def __init__(self, max_changed: int = MAX_CHANGED):
        self.max_changed = max_changed
        self._items: dict[str, tuple[np.ndarray, object]] = {}
        self.hits = self.misses = 0

    @staticmethod
    def _thumb(crop: MatLike) -> np.ndarray:
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()

    def get(self, key: str, crop: MatLike):
        """命中返回 (True, 值)，否则 (False, 缩略图)；缩略图交给 put，避免重复计算。"""
        thumb = self._thumb(crop)
        item = self._items.get(key)
        if (
            item is not None
            and item[0].shape == thumb.shape
            and np.count_nonzero(cv2.absdiff(item[0], thumb) > PIXEL_DIFF) <= self.max_changed
        ):
            self.hits += 1
            return True, item[1]
        self.misses += 1
        return False, thumb

    def put(self, key: str, thumb: np.ndarray, value) -> None:
        self._items[key] = (thumb, value)

    def clear(self) -> None:
        self._items.clear()


def cached(cache: "RegionCache | None", key: str, crop: MatLike, compute):
    """cache 为 None 时直接计算。"""
    if cache is None:
        return compute()
    hit, got = cache.get(key, crop)
    if hit:
        return copy.deepcopy(got)  # 调用方可能改动结果（加 slot 等），缓存里留原样
    value = compute()
    cache.put(key, got, value)
    return value
