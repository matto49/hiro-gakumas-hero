# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""P 饮料图标识别：多尺度模板匹配，对比 KAA 饮料图库（resources/game_data/drinks，68x68）。

KAA 的 match_first_drinks 用颜色直方图，在选饮料弹窗里三个格子都会给出同一结果，所以这里改用模板相关。
"""

import os
from functools import lru_cache

from hiro import master

import cv2
import numpy as np
from cv2.typing import MatLike

SCALES = (1.05, 1.15, 1.25, 1.35)


@lru_cache(maxsize=1)
def _templates() -> list[tuple[str, list[np.ndarray]]]:
    from hiro import plugins

    out = []
    src = plugins.get("asset_source")
    folder = src.game_data("drinks") if src is not None and hasattr(src, "game_data") else None
    if folder is None:
        return out
    for name in sorted(os.listdir(folder)):
        img = cv2.imread(os.path.join(folder, name))
        if img is None:
            continue
        # 去掉外圈 6px 边框，避免背景色影响相关
        core = img[6:-6, 6:-6]
        out.append((name.rsplit(".", 1)[0], [cv2.resize(core, None, fx=s, fy=s) for s in SCALES]))
    return out


def match_drink(slot: MatLike) -> tuple[str | None, float, float]:
    """返回 (asset_id, 最佳分数, 与次佳的分差)。slot 为单个格子截图（约 100x100）。"""
    scores = []
    for asset_id, scaled in _templates():
        best = 0.0
        for tpl in scaled:
            if tpl.shape[0] > slot.shape[0] or tpl.shape[1] > slot.shape[1]:
                continue
            best = max(best, float(cv2.minMaxLoc(cv2.matchTemplate(slot, tpl, cv2.TM_CCOEFF_NORMED))[1]))
        scores.append((best, asset_id))
    scores.sort(reverse=True)
    if not scores:
        return None, 0.0, 0.0
    (s1, a1), (s2, _) = scores[0], scores[1] if len(scores) > 1 else (0.0, None)
    return a1, s1, s1 - s2


def drink_info(asset_id: str | None) -> dict | None:
    if asset_id is None:
        return None
    d = master.drink_by_asset(asset_id)
    if d is None:
        return {"asset_id": asset_id}
    return {
        "asset_id": asset_id,
        "name": d.name,
        "effect": getattr(d, "description", None) or getattr(d, "effect_display_text", None),
    }


DIALOG_SLOTS_X = (222, 360, 498)
DIALOG_SLOT_Y = 880


def read_drink_dialog(img: MatLike) -> list[dict]:
    """选饮料弹窗（三选一）的三个候选。"""
    out = []
    for i, cx in enumerate(DIALOG_SLOTS_X):
        slot = img[DIALOG_SLOT_Y - 50 : DIALOG_SLOT_Y + 50, cx - 50 : cx + 50]
        asset_id, score, margin = match_drink(slot)
        out.append({"slot": i, "score": round(score, 3), "margin": round(margin, 3), **(drink_info(asset_id) or {})})
    return out
