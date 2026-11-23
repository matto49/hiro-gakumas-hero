"""编成第 1 步：在偶像一览里找到要培育的偶像（不依赖 KAA；旧主循环经 kaa_patches.locate_idol_by_swipe 用它）。

固定 4 列、滑动行的网格裁剪，逐格查偶像卡图库（hiro.vision.idols，和 KAA idols 库同一套描述子）。
KAA 自己的 extract_idols 轮廓检测会漏掉部分卡位（2026-10-31 实测漏掉 Campus 広），所以不用它。
先回到列表顶部，再逐页上滑，到底（画面不再变化）为止。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from hiro.devices.active import sleep as _sleep
from hiro.vision import idols
from hiro.vision.ocr import Box
from hiro.vision.ui_templates import box

logger = logging.getLogger(__name__)

MAX_PAGES = 20
TOP_SWIPES = 8
SWIPE_PX = 220  # 约 1 行，页间有重叠
SETTLE = 1.2
COLS = (30, 200, 371, 541)  # 720x1280 下四列卡片的左边缘
CARD_W, CARD_H = 149, 197
MIN_H = 120  # 裁剪高度不足时不查（被底部遮住的行）
ROW_STEP = 16
MATCH_THRESHOLD = 12  # 实测命中距离 2–8，误匹配 ≥ 10


def card_key(name: str) -> str:
    """图库文件名（i_card-skin-shro-3-007_1.png）或偶像卡编号（i_card-shro-3-007 / i_card-skin-shro-3-007）→ 统一的卡号 shro-3-007。"""
    name = name.rsplit("/", 1)[-1]
    if name.endswith(".png"):
        name = name[: -len(".png")].rsplit("_", 1)[0]
    return name.replace("i_card-skin-", "").replace("i_card-", "")


def find_on_page(img, skin_id: str) -> Box | None:
    """这一屏里 skin_id 那张卡的位置（整帧坐标）；没有 None。skin_id 带不带 skin- 都行（资料库和初星助手传的是不带的）。"""
    want = card_key(skin_id)
    _x0, y0, _w, h = box("Produce.BoxIdolOverviewIdols").xywh
    bottom = y0 + h
    for x in COLS:
        for y in range(y0 - 8, bottom - MIN_H, ROW_STEP):
            crop = img[y : min(y + CARD_H, bottom), x : x + CARD_W]
            if crop.shape[0] < MIN_H:
                continue
            hit = idols.query(crop, threshold=MATCH_THRESHOLD)
            if hit is not None and card_key(hit[0]) == want:
                logger.info("偶像 %s 在 (%d, %d)，距离 %.1f", skin_id, x, y, hit[1])
                return Box(x, y, x + CARD_W, y + crop.shape[0])
    return None


def locate(skin_id: str, *, shoot: Callable, swipe: Callable, sleep: Callable[[float], None] = _sleep,
           progress: Callable[[str], None] = lambda why: None) -> Box | None:  # fmt: skip
    """回到顶部后逐页上滑找 skin_id；找到返回卡位（整帧坐标），翻到底 / 翻满 MAX_PAGES 页仍没有返回 None。
    swipe(x1, y1, x2, y2, duration)。"""
    x0, y0, w, h = box("Produce.BoxIdolOverviewIdols").xywh
    cx, bottom = x0 + w // 2, y0 + h
    for _ in range(TOP_SWIPES):  # 回到顶部
        swipe(cx, y0 + 60, cx, bottom - 40, 0.4)
        sleep(0.5)
    sleep(0.8)
    last_sig = None
    for page in range(MAX_PAGES):
        img = shoot()
        if (hit := find_on_page(img, skin_id)) is not None:
            return hit
        sig = img[y0:bottom:8, x0 : x0 + w : 8].tobytes()
        if sig == last_sig:
            logger.info("偶像列表翻到底仍未找到 %s", skin_id)
            return None
        last_sig = sig
        progress(f"找偶像：第 {page + 1} 页没有")
        swipe(cx, bottom - 40, cx, bottom - 40 - SWIPE_PX, 0.6)
        sleep(SETTLE)
    logger.info("翻了 %d 页仍未找到 %s", MAX_PAGES, skin_id)
    return None
