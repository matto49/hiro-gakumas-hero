# 来源：digital-hiro src/hiro/vision/support.py（develop c3fc27f）；开源版改：游戏素材经插件 AssetSource（hiro.vision.assets），拿不到时退路见各处注释
"""培育开始前「4.開始確認」页（PRODUCE_START_CONFIRM）：读支援卡编成 → produce.supports。

KAA 开局走 auto_set_support_card，编成只能在现场读；一局读一次即可（主属性按支援卡类型多数定）。
- 页面判定：顶部步骤「4.開始確認」与「サポート」标签的白色字形模板（与 hif._match_white 同法）。
- 卡：左下 2 列 × 3 行，每格是支援卡横版缩略图（素材包 support/，tools.assets.build_pack support 导出）。
  只用插画上半部分做灰度相关（左下「Lv」与右下属性图标不参与），在 ±6 像素、三种宽度内取最高。
  样本上 6 张卡 0.85–0.94，领先第二名 0.26–0.48。
- 等级：左下「Lv 60」是白字浅阴影，游戏字体比对与直接 OCR 都不稳；取亮度 ≥ 235 的字形转白底黑字再 OCR。
"""

import json
from functools import cache

import cv2
import numpy as np
from cv2.typing import MatLike

from hiro.vision import assets
from hiro.vision.hif import _match_white
from hiro.vision.ocr import engine, ocr_digits

STEP_BOX = (120, 25, 280, 75)
SUPPORT_LABEL_BOX = (40, 535, 170, 590)
PAGE_MIN = 0.6
# 720x1280 下各格左上角（插画区域），2 列 × 3 行
CELLS = ((64, 579), (235, 579), (64, 677), (235, 677), (64, 776), (235, 776))
CARD_W = 163
ART_TOP = 0.55  # 只比插画上部，避开左下等级与右下属性图标
VEC = (64, 20)
MIN_SCORE = 0.7
LEVEL_BOX = (1, 60, 40, 84)  # 相对格左上角
LEVEL_WHITE = 235
ATTR = {"SupportCardType_Vocal": "Vo", "SupportCardType_Dance": "Da", "SupportCardType_Visual": "Vi"}


def is_start_confirm(img: MatLike) -> bool:
    return (_match_white(img, "start_confirm_step", STEP_BOX)[0] >= PAGE_MIN
            and _match_white(img, "start_confirm_support", SUPPORT_LABEL_BOX)[0] >= PAGE_MIN)  # fmt: skip


def _vec(bgr: MatLike) -> np.ndarray:
    g = cv2.cvtColor(cv2.resize(bgr, VEC, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32).ravel()
    g -= g.mean()
    return g / (np.linalg.norm(g) + 1e-6)


@cache
def _library() -> tuple[list[str], np.ndarray, dict]:
    folder = assets.support_dir()
    if folder is None:
        return [], np.zeros((0, 1), np.float32), {}  # 没有支援卡图：support_card_id 给 None
    index = json.loads((folder / "index.json").read_text()) if (folder / "index.json").exists() else {}
    keys, rows = [], []
    for png in sorted(folder.glob("*.png")):
        im = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        if im is None:
            continue
        keys.append(png.stem)
        rows.append(_vec(im[: int(im.shape[0] * ART_TOP), :, :3]))
    return keys, np.stack(rows), index


def _match_cell(img: MatLike, x: int, y: int) -> tuple[float, float, str, tuple[int, int]]:
    """→ (分数, 领先第二名, 资源名, 实际左上角)。各偏移 / 宽度一次矩阵乘算完。"""
    keys, matrix, _ = _library()
    if not keys:
        return 0.0, 0.0, "", (x, y)
    tries, vecs = [], []
    for w in (CARD_W - 3, CARD_W, CARD_W + 3):
        for dy in range(-6, 7, 2):
            for dx in range(-6, 7, 2):
                crop = img[y + dy : y + dy + int(w / 2 * ART_TOP), x + dx : x + dx + w]
                if crop.size:
                    tries.append((x + dx, y + dy))
                    vecs.append(_vec(crop))
    scores = matrix @ np.stack(vecs, axis=1)
    i, j = np.unravel_index(int(scores.argmax()), scores.shape)
    col = np.sort(scores[:, j])
    return float(scores[i, j]), float(col[-1] - col[-2]), keys[i], tries[j]


def read_supports(img: MatLike) -> dict:
    """→ {"supports": [{slot, support_card_id, name, type, rarity, level, score, margin}], "main_attr": "Vo" / "Da" / "Vi" / None}。
    认不出的格 support_card_id 为 None；main_attr 为 Vo / Da / Vi 中张数最多的一种（并列时 None）。"""
    _, _, index = _library()
    supports = []
    for slot, (x, y) in enumerate(CELLS):
        score, margin, key, (cx, cy) = _match_cell(img, x, y)
        info = index.get(key) if score >= MIN_SCORE else None
        lx1, ly1, lx2, ly2 = LEVEL_BOX
        level = ocr_digits(img[cy + ly1 : cy + ly2, cx + lx1 : cx + lx2], white_min=LEVEL_WHITE)
        if engine() != "vision":
            # RapidOCR 把等级「60」稳定读成「30」（start_confirm 00025 第 4 格，放大几倍都一样）；ddddocr 读对，两者不一致时以它为准
            from hiro.vision.digits import read_dddd

            d = read_dddd(img, (cx + lx1, cy + ly1, cx + lx2, cy + ly2))
            if d is not None and 1 <= d <= 70:
                level = d
        supports.append({"slot": slot, "support_card_id": info["id"] if info else None, "name": info["name"] if info else None,
                         "type": info["type"] if info else None, "rarity": info["rarity"] if info else None,
                         "level": level if level is not None and 1 <= level <= 70 else None,
                         "score": round(score, 3), "margin": round(margin, 3)})  # fmt: skip
    counts: dict[str, int] = {}
    for s in supports:
        if s["type"] in ATTR:
            counts[ATTR[s["type"]]] = counts.get(ATTR[s["type"]], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    main = ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else None
    return {"supports": supports, "main_attr": main}
