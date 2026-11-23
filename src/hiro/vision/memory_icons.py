"""回忆小图标认卡：开局第 3 步「メモリー選択」每格回忆右侧的技能卡小图，以及回忆选择页详情栏的大图标。

主方法（2026-11-06 起，「渲染模板」）：cache/card_icons.npz 里每张卡面都按游戏 UI 真实结构渲染成了干净的小图标
（hiro.vision.card_icon_render：稀有度底色、六边形 / 圆形分类遮罩、外框都和游戏一样；生成方法见那个模块的说明）。
截图里的图标缩到 48×48 转 Lab，遮掉角标可能出现的区域（card_icon_render.OVERLAY_BOXES），剩下的像素逐个算 Lab 距离求平均。
同一张卡的各偶像插画版本都比，按卡汇总。
离线验证（r305 素材包，2026-11-06）：正例 12 个（第 3 步实机帧 4 格 + 详情栏 8 个大图标）全部排第一，最佳分 ≤15.1、
和第二名差 ≥13.9（旧方法 ≥3.6）；反例 9 个（低清角标压满的卡、纯灰图、截到背景 / 偏位的位置）和第二名差 ≤6.9。
比过的其他做法：只取最像的 60–80% 像素、只遮常见角标、不遮，正反例的分差都会重叠或更近。

退路（没有渲染库时，「插画」）：图标四边裁 7%，和卡面目录的整张插画比、取最像的 60% 像素；没有卡面目录时一律「认不出」。

「认出」要同时满足：最佳分 ≤ 认不出线、和第二名差 ≥ 分差线、图标不是一片平色。两种方法的线不同（THRESHOLDS）。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

import cv2
import numpy as np
from cv2.typing import MatLike

# 第 3 步「メモリー選択」四格回忆的技能卡小图标（720x1280，x1, y1, x2, y2）：用正确答案的渲染图在 2026-11-04 实机帧上
# 搜出的位置，边长 87（96 设计单位 × 0.906）；顺序 左上、右上、左下、右下
STEP3_ICONS = ((261, 744, 348, 831), (587, 744, 674, 831), (261, 875, 348, 962), (587, 875, 674, 962))
DETAIL_ICON = (44, 222, 192, 370)  # 回忆选择页详情栏的大图标（同法在 scan-0001 帧上搜出，边长 148）

ICON = 48  # 渲染模板的边长
KEEP_RENDER = 1.0  # 遮掉角标后剩下的像素全部参与
KEEP_ART, INSET_ART, SIZE_ART = 0.6, 0.07, 24
MIN_STD = 15.0  # 图标灰度标准差小于这个 = 不是图标（平色）
# 方法 → (认不出线, 分差线)。渲染模板：正例最佳分 ≤15.1、分差 ≥13.9，反例分差 ≤6.9，分差线取中间 10；
# 插画（退路）：正例最佳分 4.6–18.7、分差 ≥3.6，反例分差 ≤1.9
THRESHOLDS = {"render": (20.0, 10.0), "art": (22.0, 3.0)}
ART_CACHE = os.path.join("cache", "memory_icon_index.npz")


def card_key(name: str) -> str | None:
    """素材名（img_general_skillcard_act-2_062-shro）或卡 ID（p_card-02-act-2_062）→ 卡键 act-2_062；对不上返回 None。"""
    m = re.search(r"(acc|act|men|sup|ido)-(\d+_\d+)", name)
    return m.group(0) if m else None


def _lab(img: MatLike) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


@lru_cache(maxsize=1)
def _render_index() -> tuple[list[str], list[str], np.ndarray, np.ndarray] | None:
    """(素材名, 卡键, Lab 特征 N×P×3, 参与比对的像素掩码)。cache/card_icons.npz 不存在时 None。"""
    from hiro.vision.card_icon_render import CACHE, overlay_mask

    path = CACHE
    if not path.exists():
        return None
    data = np.load(path, allow_pickle=False)
    names = [str(n) for n in data["names"]]
    mask = overlay_mask(ICON)
    feats = np.stack([_lab(im)[mask] for im in data["imgs"]])
    return names, [card_key(n) or n for n in names], feats, mask


@lru_cache(maxsize=1)
def _art_index() -> tuple[list[str], list[str], np.ndarray]:
    """退路：卡面目录的整张插画（四边不裁），24×24 Lab。按目录文件数 / 修改时间缓存到 cache/。没有卡面目录时为空。"""
    from hiro.vision import assets

    root = assets.card_art_dir()
    if root is None:
        return [], [], np.zeros((0, SIZE_ART * SIZE_ART, 3), np.float32)
    folder = str(root)
    files = sorted(n for n in os.listdir(folder) if n.endswith(".png") and "_mid-" not in n)
    stamp = f"{len(files)}-{int(os.path.getmtime(folder))}-{SIZE_ART}"
    if os.path.exists(ART_CACHE):
        data = np.load(ART_CACHE, allow_pickle=False)
        if str(data["stamp"]) == stamp:
            names = list(data["names"])
            return names, [card_key(n) or n for n in names], data["matrix"]
    names, rows = [], []
    for n in files:
        img = cv2.imread(os.path.join(folder, n))
        if img is not None:
            names.append(n.rsplit(".", 1)[0])
            rows.append(_art_feat(img, 0.0))
    matrix = np.stack(rows)
    os.makedirs(os.path.dirname(ART_CACHE), exist_ok=True)
    np.savez(ART_CACHE, names=np.array(names), matrix=matrix, stamp=np.array(stamp))
    return names, [card_key(n) or n for n in names], matrix


def _art_feat(img: MatLike, inset: float) -> np.ndarray:
    h, w = img.shape[:2]
    dy, dx = int(h * inset), int(w * inset)
    g = cv2.resize(img[dy : h - dy, dx : w - dx], (SIZE_ART, SIZE_ART), interpolation=cv2.INTER_AREA)
    return _lab(g).reshape(-1, 3)


def method() -> str:
    """当前用的认卡方法：render（素材包有渲染模板）/ art（退路）。"""
    return "render" if _render_index() is not None else "art"


def rank_cards(icon: MatLike) -> list[tuple[str, float, str]]:
    """图标 → [(卡键, 分数, 最像的素材名)]，分数越小越像，从好到差排。"""
    idx = _render_index()
    if idx is not None:
        names, keys, feats, mask = idx
        q = _lab(cv2.resize(icon, (ICON, ICON), interpolation=cv2.INTER_AREA))[mask]
        d = np.linalg.norm(feats - q[None], axis=2)
        keep = KEEP_RENDER
    else:
        names, keys, matrix = _art_index()
        d = np.linalg.norm(matrix - _art_feat(icon, INSET_ART)[None], axis=2)
        keep = KEEP_ART
    k = int(d.shape[1] * keep)
    score = np.sort(d, axis=1)[:, :k].mean(1)
    best: dict[str, tuple[float, str]] = {}
    for key, name, s in zip(keys, names, score):
        if key not in best or s < best[key][0]:
            best[key] = (float(s), name)
    return sorted(((key, s, name) for key, (s, name) in best.items()), key=lambda x: x[1])


def read_icon(icon: MatLike, expect: list[str] | None = None) -> dict:
    """一个图标 → {card, score, second, margin, known, asset, method, expect_scores}。
    card：最像的卡键；margin：和第二名的分差；known：认出了（见模块说明）；
    expect：想核对的卡键 / 卡 ID 列表时，另给它们各自的分数（expect_scores），供判断「是不是方案里的卡」。"""
    r = rank_cards(icon)
    m = method()
    if not r:
        return {
            "card": None,
            "score": None,
            "asset": None,
            "second": None,
            "margin": None,
            "method": m,
            "known": False,
            "expect_scores": {},
        }
    unknown_line, margin_line = THRESHOLDS[m]
    top = r[0]
    margin = r[1][1] - top[1] if len(r) > 1 else 0.0
    std = float(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY).std()) if icon.ndim == 3 else float(icon.std())
    out = {"card": top[0], "score": round(top[1], 2), "asset": top[2], "second": r[1][0] if len(r) > 1 else None,
           "margin": round(margin, 2), "method": m, "known": top[1] <= unknown_line and margin >= margin_line and std >= MIN_STD}  # fmt: skip
    if expect:
        scores = {k: s for k, s, _ in r}
        out["expect_scores"] = {e: round(scores[card_key(e)], 2) for e in expect if card_key(e) in scores}
    return out


EMPTY_STD = (
    20.0  # 第 3 步空格（「+」，没编回忆）图标区灰度标准差 12.9–15.1；有图标 32–69（账号 2 11-16 第 120 / 132 号帧）
)
PLAN_RED_MIN = 0.3  # 图标上方「プラン不一致」红标签的红色比例：有标签 0.50，没有 0
PLAN_BOOST = (
    1.3  # プラン不一致 的卡图被压暗：饱和度、亮度各乘这个再认（魅惑の視線：原样 25.1 认成别的，提亮后 13.1、领先 4.6）
)


def _plan_mismatch(img: MatLike, box) -> bool:
    x1, y1, x2, _y2 = box
    hsv = cv2.cvtColor(img[max(0, y1 - 40) : y1 - 4, max(0, x1 - 10) : x2 + 20], cv2.COLOR_BGR2HSV)
    red = ((hsv[..., 0] <= 8) | (hsv[..., 0] >= 170)) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 180)
    return float(red.mean()) >= PLAN_RED_MIN


def _brighten(icon: MatLike, gain: float = PLAN_BOOST) -> MatLike:
    hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1:] *= gain
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)


def read_step3_icons(img: MatLike, expect: list[str] | None = None) -> list[dict]:
    """第 3 步截图（720x1280）→ 四格回忆的图标读数，顺序为 左上、右上、左下、右下。
    空格（「+」）给 empty=True、card=None；图标上方有「プラン不一致」红标签的给 plan_mismatch=True，
    它的卡图被压暗，原样认不出时提亮再认一次（账号 2 11-16：一格 プラン不一致 + 三格空，四格都报认不出）。"""
    out = []
    for i, (x1, y1, x2, y2) in enumerate(STEP3_ICONS):
        icon = img[y1:y2, x1:x2]
        if float(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY).std()) < EMPTY_STD:
            out.append({"card": None, "score": None, "asset": None, "second": None, "margin": None, "method": method(),
                        "known": False, "empty": True, "slot": i})  # fmt: skip
            continue
        r = read_icon(icon, expect)
        if _plan_mismatch(img, (x1, y1, x2, y2)):
            if not r["known"] and (r2 := read_icon(_brighten(icon), expect))["known"]:
                r = r2
            r["plan_mismatch"] = True
        out.append(dict(r, slot=i))
    return out
