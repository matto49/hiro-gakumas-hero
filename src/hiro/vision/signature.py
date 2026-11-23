"""页面签名：用客户端 Sprite 在固定小窗口里做模板匹配，快速判断当前页面。

签名由 tools/assets/mine_signatures 从已标注截图挖掘（signatures.json）：每类页面若干个
（Sprite, 屏幕位置, 阈值）。运行时只在每个位置附近 ±MARGIN 像素内匹配，一类页面的签名
至少 MIN_VOTE 比例通过才算命中；有多个页面命中时取平均超出阈值最多的。
"""

import json
from functools import cache
from pathlib import Path

from hiro.vision import assets

import cv2
import numpy as np
from cv2.typing import MatLike

SIGNATURES = Path(__file__).with_name("signatures.json")
UI_SCALE = 720 / 1080
MARGIN = 10
MIN_VOTE = 0.66
COARSE_MIN_AREA = 6000  # 模板面积超过此值走「半分辨率定位 + 全分辨率精算」
REFINE = 3
FLAT_VAR = 1.0  # 窗口内灰度方差（每像素）低于此值视为纯色，不参与匹配


@cache
def _signatures() -> dict[str, list[dict]]:
    data = json.loads(SIGNATURES.read_text(encoding="utf-8")) if SIGNATURES.exists() else {}
    root = assets.sprites_dir()
    if root is None:
        return {}  # 没有界面 Sprite：签名判页关闭（走 KAA / quick_page / OCR 补判）
    out = {}
    for scene, sigs in data.items():
        loaded = []
        for s in sigs:
            im = cv2.imread(str(root / s["sprite"]), cv2.IMREAD_UNCHANGED)
            h, w = im.shape[:2]
            size = (max(6, round(w * UI_SCALE)), max(6, round(h * UI_SCALE)))
            small = cv2.resize(im, size, interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small[:, :, :3], cv2.COLOR_BGR2GRAY)
            mask = (small[:, :, 3] > 128).astype(np.uint8) * 255
            sig = {**s, "gray": gray, "mask": mask, "full": _prepare(gray, mask)}
            if gray.size >= COARSE_MIN_AREA:
                half = (gray.shape[1] // 2, gray.shape[0] // 2)
                sig["half"] = _prepare(cv2.resize(gray, half, interpolation=cv2.INTER_AREA),
                                       (cv2.resize(mask, half, interpolation=cv2.INTER_AREA) > 128).astype(np.uint8))  # fmt: skip
            loaded.append(sig)
        if loaded:
            out[scene] = loaded
    return out


def _prepare(gray: np.ndarray, mask: np.ndarray) -> dict:
    """遮罩 NCC 的模板侧常量：去均值后的模板 tc、遮罩 m、像素数 n、tc 的平方和。"""
    m = (mask > 0).astype(np.float32)
    n = float(m.sum())
    t = gray.astype(np.float32)
    tc = m * (t - float((m * t).sum()) / n)
    return {"tc": tc, "m": m, "n": n, "tt": float((tc * tc).sum())}


def _masked_max(crop: np.ndarray, t: dict) -> tuple[float, tuple[int, int]]:
    """与 cv2.matchTemplate(TM_CCOEFF_NORMED, mask=...) 数值一致，但只用三次不带遮罩的相关（快 3–10 倍）：
    分子 = Σ tc·P（tc 在遮罩内零均值），分母 = sqrt(Σtc² · (ΣmP² − (ΣmP)²/n))。
    窗口内近乎纯色（方差接近 0）的位置记 0，与 OpenCV 结果里的 inf / nan 被清零一致。"""
    p = crop.astype(np.float32)
    num = cv2.matchTemplate(p, t["tc"], cv2.TM_CCORR)
    s1 = cv2.matchTemplate(p, t["m"], cv2.TM_CCORR)
    s2 = cv2.matchTemplate(p * p, t["m"], cv2.TM_CCORR)
    var = s2 - s1 * s1 / t["n"]
    r = np.where(var > t["n"] * FLAT_VAR, num / np.sqrt(np.maximum(var, 1e-6) * t["tt"]), 0.0)
    _, score, _, loc = cv2.minMaxLoc(r)
    return float(score), loc


def _score(gray_img: np.ndarray, sig: dict) -> float:
    x, y, w, h = sig["box"]
    crop = gray_img[max(0, y - MARGIN) : y + h + MARGIN, max(0, x - MARGIN) : x + w + MARGIN]
    th, tw = sig["gray"].shape
    if crop.shape[0] < th or crop.shape[1] < tw:
        return 0.0
    if "half" not in sig:
        return _masked_max(crop, sig["full"])[0]
    # 大模板（按钮底图等）：先在半分辨率上定位，再在全分辨率 ±REFINE 像素内精算
    small = cv2.resize(crop, (crop.shape[1] // 2, crop.shape[0] // 2), interpolation=cv2.INTER_AREA)
    if small.shape[0] < sig["half"]["tc"].shape[0] or small.shape[1] < sig["half"]["tc"].shape[1]:
        return _masked_max(crop, sig["full"])[0]
    _, (lx, ly) = _masked_max(small, sig["half"])
    x0, y0 = max(0, 2 * lx - REFINE), max(0, 2 * ly - REFINE)
    window = crop[y0 : min(crop.shape[0], 2 * ly + REFINE + th), x0 : min(crop.shape[1], 2 * lx + REFINE + tw)]
    return _masked_max(window, sig["full"])[0]


def match(img: MatLike) -> tuple[str | None, dict]:
    """返回 (命中的页面或 None, {页面: [各签名得分]})。img 为 720x1280 BGR。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    detail, best = {}, (None, 0.0)
    for scene, sigs in _signatures().items():
        scores = [_score(gray, s) for s in sigs]
        detail[scene] = [round(s, 3) for s in scores]
        passed = [s - sig["threshold"] for s, sig in zip(scores, sigs, strict=True) if s >= sig["threshold"]]
        if len(passed) >= max(1, round(len(sigs) * MIN_VOTE)):
            excess = float(np.mean(passed))
            if excess > best[1]:
                best = (scene, excess)
    return best[0], detail
