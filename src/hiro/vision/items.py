# 来源：digital-hiro src/hiro/vision/items.py（develop c3fc27f）；开源版改：游戏素材经插件 AssetSource（hiro.vision.assets），拿不到时退路见各处注释
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""考试画面中持有的 P 饮料（左下 4 格）与 P 道具（右侧牌库图标下方）。

图标来自识别素材包（hiro.assets，icons/<类别>/ 下的 RGBA PNG 与 index.json 名称表）。位置与显示尺寸固定，
所以只在固定位置裁一块，与整个图标库做一次「带透明度遮罩的归一化相关」：每个图标只在自己的不透明区域内比较。
P 道具是半透明叠在场景上的，身后的气球等大块背景会把直接相关拉到 0.59；先做高通（减去高斯模糊）
只比细节，实测正确 0.77–0.85、次佳 ≤ 0.21、空位 ≤ 0.25。
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

from hiro.vision import assets
from hiro.vision.region_cache import RegionCache, cached

MIN_SCORE = 0.5
MIN_MARGIN = 0.15  # 领先次佳的幅度；背景误配时各候选分数接近
LOW_SCORE, LOW_MARGIN = 0.42, 0.22  # 分数略低但遥遥领先也算：ミックススムージー 在培育页底栏只有 0.445、次佳 0.19（qualify6 #293 等 14 帧）

DRINK_SIZE = 56
DRINK_CENTERS = ((62, 1200), (147, 1200), (233, 1200), (318, 1200))  # 考试页
DRINK_ROWS_DY = (0, 13)  # 培育页（选行动 / 相談 / 三选一 / 饮料溢出等）的饮料栏比考试页低约 13 像素，x 相同
PITEM_SIZE = 66
PITEM_TOP_LEFT = (634, 313)
PITEM_PITCH = 70  # 多个 P 道具时向下排列的间距（未在实机验证，配合 ±12 像素搜索）
PITEM_MAX = 4


@dataclass
class IconLibrary:
    keys: list[str]
    templates: np.ndarray  # N×D，遮罩内零均值、单位范数
    masks: np.ndarray  # N×D，0/1
    counts: np.ndarray  # N，遮罩像素数
    size: int
    names: dict[str, str]

    @classmethod
    def load(cls, folder: Path, size: int) -> "IconLibrary":
        keys, rows, masks = [], [], []
        for png in sorted(folder.glob("*.png")):
            im = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
            if im is None or im.ndim != 3 or im.shape[2] not in (3, 4):
                continue
            if im.shape[2] == 3:
                im = _white_bg_to_alpha(im)  # KAA 公开数据的饮料图：白底 RGB
            im = cv2.resize(im, (size, size), interpolation=cv2.INTER_AREA)
            m = np.repeat((im[:, :, 3] > 128)[:, :, None], 3, axis=2).ravel().astype(np.float32)
            t = _highpass(im[:, :, :3]).ravel()
            t = (t - (t * m).sum() / m.sum()) * m
            keys.append(png.stem)
            rows.append(t / (np.linalg.norm(t) + 1e-6))
            masks.append(m)
        if not keys:
            return cls.empty(size)
        masks_arr = np.stack(masks)
        index = folder / "index.json"  # 资源名 → 名称（可选；KAA 公开数据没有）
        names = json.loads(index.read_text()) if index.exists() else {}
        return cls(keys, np.stack(rows), masks_arr, masks_arr.sum(axis=1), size, names)

    @classmethod
    def empty(cls, size: int) -> "IconLibrary":
        """没有图标素材：匹配一律 0 分（低于各处阈值，读数给 None）。"""
        return cls([], np.zeros((0, 1), np.float32), np.zeros((0, 1), np.float32), np.zeros(0, np.float32), size, {})

    def match(self, patch: MatLike) -> list[tuple[float, str]]:
        """patch 为 size×size 的 BGR；返回前 2 名 (分数, 资源名)。"""
        return self.match_many([patch])[0]

    def match_many(self, patches: list[MatLike]) -> list[list[tuple[float, str]]]:
        """多块一起算：三次矩阵乘矩阵代替逐块的矩阵乘向量（同一位置附近的多个偏移一次算完）。"""
        if not self.keys:
            return [[(0.0, ""), (0.0, "")] for _ in patches]
        p = np.stack([_highpass(x).ravel() for x in patches], axis=1)  # D×K
        s1, s2 = self.masks @ p, self.masks @ (p * p)
        var = np.maximum(s2 - s1 * s1 / self.counts[:, None], 1e-6)
        scores = (self.templates @ p) / np.sqrt(var)  # N×K
        out = []
        for col in scores.T:
            top = np.argsort(-col)[:2]  # 自建图库可能只有一两个图标（argpartition(kth=2) 要求至少 3 个）
            out.append([(float(col[i]), self.keys[i]) for i in top] + [(0.0, "")] * (2 - len(top)))
        return out


def _white_bg_to_alpha(bgr: np.ndarray, tol: int = 12) -> np.ndarray:
    """白底图标 → RGBA：从四边漫水填充出与边相连的近白区域当背景（图标内部的白色部分保留），收 1px 去掉白边。"""
    h, w = bgr.shape[:2]
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if (bgr[y, x] >= 255 - tol).all() and not mask[y + 1, x + 1]:
            cv2.floodFill(bgr.copy(), mask, (x, y), 0, (tol,) * 3, (tol,) * 3, flags)
    alpha = np.where(mask[1:-1, 1:-1] > 0, 0, 255).astype(np.uint8)
    alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8))
    return np.dstack([bgr, alpha])


def _highpass(bgr: MatLike) -> np.ndarray:
    f = bgr.astype(np.float32)
    return f - cv2.GaussianBlur(f, (0, 0), 4)


ICON_KIND = {"pdrink": "drink", "pitem": "pitem"}  # 识别层原名 → AssetSource.icon_dir 的 kind


@cache
def _library(kind: str, size: int) -> IconLibrary:
    folder = assets.icon_dir(ICON_KIND.get(kind, kind))
    return IconLibrary.load(folder, size) if folder is not None else IconLibrary.empty(size)


def _crop(img: MatLike, x: int, y: int, size: int) -> MatLike | None:
    if y < 0 or x < 0 or y + size > img.shape[0] or x + size > img.shape[1]:
        return None
    return img[y : y + size, x : x + size]


EMPTY_MAX = 0.35  # 空槽（半透明杯子占位）与饮料图库的最高分实测 ≤ 0.21；有饮料 ≥ 0.62
GREY_SAT_MAX = 50  # 饮料图标平均饱和度实测 68–93、空槽 ≤ 45；认出了饮料但饱和度低于此值视为压暗（不可用，未见过样本）


def read_drink_slots(img: MatLike, *, cache: RegionCache | None = None) -> list[dict]:
    """饮料栏 4 格，每格一个字典（从左到右）：
    {slot, state: "drink" / "empty" / "unknown", drink_id, asset, name, rect[x, y, w, h], center[x, y],
     available, score, margin, confidence}
    - drink：图库最高分 ≥ MIN_SCORE 且领先次佳 ≥ MIN_MARGIN；confidence 取最高分
    - empty：最高分 < EMPTY_MAX（空槽是半透明杯子，与任何饮料都不像）；confidence = 1 − 最高分
    - unknown：介于两者之间（被特效遮挡等），调用方应当作没读到
    available：认出饮料且图标未压暗为 True；压暗为 False（尚无样本，阈值按空槽 / 饮料的饱和度间隔取）；非饮料为 None。
    cache：饮料栏与上次相同则复用。"""
    x1, x2 = DRINK_CENTERS[0][0] - DRINK_SIZE // 2, DRINK_CENTERS[-1][0] + DRINK_SIZE // 2
    y1, y2 = DRINK_CENTERS[0][1] - DRINK_SIZE // 2, DRINK_CENTERS[0][1] + max(DRINK_ROWS_DY) + DRINK_SIZE // 2
    return cached(cache, "drinks", img[y1:y2, x1:x2], lambda: _read_drink_slots(img))


def _read_drink_slots(img: MatLike) -> list[dict]:
    """考试页与培育页的饮料栏高度不同：两行都比一次，取 4 格最高分之和大的那行（空槽两行都低，不影响）。"""
    from kaa.db.drink import Drink

    lib = _library("pdrink", DRINK_SIZE)
    half = DRINK_SIZE // 2
    best = None
    for dy in DRINK_ROWS_DY:
        centers = [(cx, cy + dy) for cx, cy in DRINK_CENTERS]
        crops = [_crop(img, cx - half, cy - half, DRINK_SIZE) for cx, cy in centers]
        tops = lib.match_many(crops)
        total = sum(t[0][0] for t in tops)
        if best is None or total > best[0]:
            best = (total, centers, crops, tops)
    _, centers, crops, tops = best
    out = []
    for slot, ((cx, cy), crop, top) in enumerate(zip(centers, crops, tops, strict=True)):
        (score, asset), (second, _) = top
        entry = {"slot": slot, "state": "unknown", "drink_id": None, "asset": None, "name": None,
                 "rect": [cx - half, cy - half, DRINK_SIZE, DRINK_SIZE], "center": [cx, cy], "available": None,
                 "score": round(score, 3), "margin": round(score - second, 3), "confidence": None}  # fmt: skip
        if (score >= MIN_SCORE and score - second >= MIN_MARGIN) or (score >= LOW_SCORE and score - second >= LOW_MARGIN):
            drink = Drink.from_asset_id(asset)
            sat = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
            entry.update(state="drink", drink_id=drink.id if drink else None, asset=asset, name=lib.names.get(asset) or (drink.name if drink else None),  # KAA 图库没有名称表：按主数据取
                         available=sat >= GREY_SAT_MAX, confidence=round(score, 3))  # fmt: skip
        elif score < EMPTY_MAX:
            entry.update(state="empty", confidence=round(1 - score, 3))
        out.append(entry)
    return out


FULL_BADGE = (240, 1105, 480, 1160)  # 选饮料页「受け取る」下方的红标「Pドリンク所持上限」（饮料满、受け取る变灰）
FULL_BADGE_MIN_PX = 1500  # 红色像素够了才 OCR 确认（不满时不花 OCR）


def read_held_drinks(img: MatLike) -> dict:
    """培育页底栏已有的饮料 → {held, full}。
    held：[{index, drink_id, name, center, method, confidence}]，index = 底栏格号（0–3，点它打开详情「捨てる」），格式同 PDRINK_MAX.held；
    空槽不列，图标没把握的格 drink_id / name 为 None。full：「Pドリンク所持上限」红标在（选饮料页，饮料满时「受け取る」不可点）。"""
    held = [{"index": d["slot"], "drink_id": d["drink_id"], "name": d["name"], "center": d["center"], "method": "icon",
             "confidence": d["confidence"]} for d in read_drink_slots(img) if d["state"] != "empty"]  # fmt: skip
    return {"held": held, "full": drink_full_badge(img)}


def drink_full_badge(img: MatLike) -> bool:
    """选饮料页「受け取る」下方的红标「Pドリンク所持上限」在不在（饮料满）。"""
    from hiro.vision.ocr import ocr_boxes

    x1, y1, x2, y2 = FULL_BADGE
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    h, sat, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    red = int((((h < 5) | (h >= 170)) & (sat >= 120) & (v >= 170)).sum())  # 红标实测 4600–4700；可点的橙色「受け取る」≤ 50
    return red >= FULL_BADGE_MIN_PX and any("上限" in t for t, _, _ in ocr_boxes(img[y1:y2, x1:x2]))


def read_bar_drinks(img: MatLike) -> dict:
    """培育页底栏饮料 → ProduceState 格式 {drinks: [drink_id…]（从左到右）, drink_capacity}。
    有一格没把握（unknown）或认出的饮料没有 id 时 drinks 为 None；4 格都是饮料或空槽时 drink_capacity = 4（H.I.F 全部培育页实测都是 4 格）。"""
    slots = read_drink_slots(img)
    sure = all(d["state"] in ("drink", "empty") for d in slots) and all(d["drink_id"] for d in slots if d["state"] == "drink")
    return {"drinks": [d["drink_id"] for d in slots if d["state"] == "drink"] if sure else None,
            "drink_capacity": len(slots) if sure else None}  # fmt: skip


def read_drinks(img: MatLike, *, cache: RegionCache | None = None) -> list[dict | None]:
    """旧格式：4 格，有饮料给 {slot, asset, name, center, score, margin}，空槽与没把握都给 None。"""
    return [{k: d[k] for k in ("slot", "asset", "name", "center", "score", "margin")} if d["state"] == "drink" else None
            for d in read_drink_slots(img, cache=cache)]  # fmt: skip


def read_pitems(img: MatLike, *, cache: RegionCache | None = None) -> list[dict]:
    """P 道具列（牌库图标下方，向下排列）。cache：整列与上次相同则复用。"""
    x0, y0 = PITEM_TOP_LEFT
    region = img[y0 : y0 + PITEM_PITCH * (PITEM_MAX - 1) + PITEM_SIZE + 12, x0 : x0 + PITEM_SIZE]
    return cached(cache, "pitems", region, lambda: _read_pitems(img))


def _read_pitems(img: MatLike) -> list[dict]:
    lib = _library("pitem", PITEM_SIZE)
    x0, y0 = PITEM_TOP_LEFT
    out = []
    for k in range(PITEM_MAX):
        crops = [c for dy in ((0,) if k == 0 else range(-12, 13, 4)) if (c := _crop(img, x0, y0 + PITEM_PITCH * k + dy, PITEM_SIZE)) is not None]
        best = max(lib.match_many(crops), key=lambda top: top[0][0]) if crops else None
        if best is None or best[0][0] < MIN_SCORE or best[0][0] - best[1][0] < MIN_MARGIN:
            break
        (score, asset), (second, _) = best
        out.append({"slot": k, "asset": asset, "name": lib.names.get(asset), "score": round(score, 3), "margin": round(score - second, 3)})
    return out


# ---------------------------------------------------------------- 饮料溢出页（PDRINK_MAX）
# H.I.F 实机（第四局 r4）：「Pドリンク所持上限」弹窗，上面「受け取ったドリンク」是新饮料，下面「手持ちのドリンク」是已有饮料；
# 每行左侧图标（带「i」角标、浅蓝底，图库匹配分数只有 0.3–0.5）+ 多行效果说明 + 右侧勾选框；列表会滚动（右侧滚动条）。
# 所以按效果说明认饮料：同一瓶的几行行距约 36，两瓶之间至少 50；拼起来与主数据 ProduceDrink.produceDescriptions 比相似度。
OVERFLOW_TEXT_X = (150, 620)  # 效果说明左端 x 范围（左边是图标，右边是勾选框）
OVERFLOW_ROW_GAP = 50
OVERFLOW_LIST_BOTTOM = 1075  # 列表视口下缘（「残す」按钮之上）
OVERFLOW_TEXT_MIN, OVERFLOW_TEXT_MARGIN = 0.75, 0.15  # 完整行 0.86–1.00、领先 ≥0.36；被视口切掉的行 0.6 / 领先 0.06
OVERFLOW_ICON_X = 104
OVERFLOW_MIN, OVERFLOW_MARGIN = 0.45, 0.15  # 图标兜底（KAA 参考图 0.48–0.83）
SCROLL_X = (676, 684)
SCROLL_TRACK_BOTTOM = 1050  # 滑块下端到这里以下才算滚到底


def _norm_effect(text: str) -> str:
    import re

    text = text.replace("<nobr>", "").replace("</nobr>", "")
    return re.sub(r"[\s（）()・%、。<>/a-zA-Z\[\]♥●★◆◇◎○•+＋\-ー.]", "", text)


@cache
def _drink_effects() -> dict[str, tuple[str, str]]:
    """drink_id → (名称, 规范化的效果说明)。"""
    import json

    from kaa.db.sqlite import select_many

    out = {}
    for r in select_many("SELECT id, name, produceDescriptions FROM ProduceDrink;"):
        try:
            parts = json.loads(r["produceDescriptions"] or "[]")
        except ValueError:
            continue
        out[r["id"]] = (r["name"], _norm_effect("".join(x.get("text", "") for x in parts)))
    return out


def _drink_by_effect(text: str) -> tuple[str | None, float, float]:
    import difflib

    norm = _norm_effect(text)
    if not norm:
        return None, 0.0, 0.0
    sims = sorted(((difflib.SequenceMatcher(None, norm, eff).ratio(), did) for did, (_, eff) in _drink_effects().items()), reverse=True)
    (best, did), (second, _) = sims[0], sims[1]
    return (did if best >= OVERFLOW_TEXT_MIN and best - second >= OVERFLOW_TEXT_MARGIN else None), best, best - second


PARTIAL_GAP = 60  # 最后一行已有饮料的末行文字离视口下缘不到这么多：这一行多半被视口切掉了（只露出效果说明的开头）
PREFIX_MIN = 0.9


def _drinks_by_prefix(text: str) -> list[str]:
    """被视口切掉的行只露出效果说明的开头：与每瓶饮料效果说明的同长前缀比，够像的都列出来（drink_id，按相似度降序）。"""
    import difflib

    norm = _norm_effect(text)
    if len(norm) < 4:
        return []
    sims = sorted(((difflib.SequenceMatcher(None, norm, eff[: len(norm)]).ratio(), did) for did, (_, eff) in _drink_effects().items()), reverse=True)
    return [did for sim, did in sims if sim >= PREFIX_MIN]


def _match_drink_icon(img: MatLike, cx: int, cy: int) -> tuple[float, float, str]:
    lib = _library("pdrink", 56)
    crops = []
    for size in (56, 60, 64):
        h = size // 2
        for dx in (-12, -6, 0, 6, 12):
            for dy in (-8, 0, 8):
                crops.append(cv2.resize(_crop(img, cx - h + dx, cy - h + dy, size), (56, 56), interpolation=cv2.INTER_AREA))
    (score, asset), (second, _) = max(lib.match_many(crops), key=lambda t: t[0][0])
    return score, second, asset


def _scroll_complete(img: MatLike) -> bool:
    """右侧滚动条：没有滑块（不滚动）或滑块下端到了视口底部 → 列表看全了。"""
    hsv = cv2.cvtColor(img[250:OVERFLOW_LIST_BOTTOM, SCROLL_X[0] : SCROLL_X[1]], cv2.COLOR_BGR2HSV)
    grey = ((hsv[:, :, 1] < 30) & (hsv[:, :, 2] > 120) & (hsv[:, :, 2] < 215)).mean(axis=1) > 0.5
    ys = np.where(grey)[0]
    if len(ys) < 50:
        return True
    return int(ys.max()) + 250 >= SCROLL_TRACK_BOTTOM


LINE_DY = 12  # 同一行文字的框中心 y 差在这以内算同一视觉行


def _reading_order(part: list[tuple[int, str, int]]) -> list[tuple[int, str]]:
    """一个饮料行里的文字框按阅读顺序排：先分视觉行（y 差 ≤ LINE_DY），行内按 x。
    只按 y 排时，RapidOCR 把一行拆成几个框、中心 y 差几个像素就会乱序（q33-00111「以降、ターン終了時 / は / やる気+1」读成「はやる気+1以降…」）。"""
    out: list[list[tuple[int, str, int]]] = []
    for y, t, x in sorted(part):
        if out and y - out[-1][0][0] <= LINE_DY:
            out[-1].append((y, t, x))
        else:
            out.append([(y, t, x)])
    return [(y, t) for line in out for y, t, _ in sorted(line, key=lambda p: p[2])]


def read_pdrink_max(img: MatLike) -> dict | None:
    """饮料溢出页 → {held: [{index, drink_id, name, y, center, method, score}]（从上到下，只含画面上看到的行）,
    incoming: {...} | None（最下面那瓶新饮料，兼容旧读法）, incomings: [...]（全部新饮料，从上到下；一次可能来两瓶，calib 151258）,
    select_left: int | None, complete: bool}。认不出页面（没有「手持ち」标题）返回 None。
    method：effect（效果说明与主数据匹配）/ icon（图标兜底）/ None（认不出，drink_id 为 None）。
    complete=False：列表没滚到底，下面可能还有没看到的已有饮料（index 只对看到的行有效）。格式见 docs/llm-context.md「PDRINK_MAX」。"""
    import re

    from kaa.db.drink import Drink

    from hiro.vision.ocr import ocr_boxes

    boxes = [(t.replace(" ", ""), b) for t, _, b in ocr_boxes(img)]  # 整屏：「あと N個選択」随弹窗高度在 y≈780–1210（原来只看 0:1150，长弹窗读不到）
    header = next((b for t, b in boxes if "手持ち" in t), None)
    if header is None:
        return None
    top = next((b[3] for t, b in boxes if "受け取った" in t), 0)
    hy = (header[1] + header[3]) // 2
    keep = next((b for t, b in boxes if "残す" in t and b[1] > hy), None)
    bottom = min(OVERFLOW_LIST_BOTTOM, keep[1] - 20) if keep else OVERFLOW_LIST_BOTTOM  # 「残す」之下是按钮与弹窗文字
    lines = sorted(((b[1] + b[3]) // 2, t, b[0]) for t, b in boxes
                   if OVERFLOW_TEXT_X[0] <= b[0] <= OVERFLOW_TEXT_X[1] and b[1] > top and b[3] < bottom
                   and "手持ち" not in t and "受け取った" not in t and abs((b[1] + b[3]) // 2 - hy) > 20)  # fmt: skip
    rows: list[list[tuple[int, str]]] = []
    for y, t, x in lines:
        if rows and y - rows[-1][-1][0] < OVERFLOW_ROW_GAP and (rows[-1][0][0] < hy) == (y < hy):
            rows[-1].append((y, t, x))
        else:
            rows.append([(y, t, x)])
    rows = [_reading_order(r) for r in rows]
    lib = _library("pdrink", 56)
    complete = _scroll_complete(img)

    def row(index: int, part: list[tuple[int, str]]) -> dict:
        y = (part[0][0] + part[-1][0]) // 2
        did, sim, margin = _drink_by_effect("".join(t for _, t in part))
        entry = {"index": index, "drink_id": did, "name": _drink_effects()[did][0] if did else None, "y": int(y),
                 "center": [OVERFLOW_ICON_X, int(y)], "method": "effect" if did else None, "score": round(sim, 3), "margin": round(margin, 3)}  # fmt: skip
        if did is None:
            score, second, asset = _match_drink_icon(img, OVERFLOW_ICON_X, y)
            drink = Drink.from_asset_id(asset) if score >= OVERFLOW_MIN and score - second >= OVERFLOW_MARGIN else None
            if drink is not None:
                entry.update(drink_id=drink.id, name=lib.names.get(asset) or drink.name, method="icon", score=round(score, 3), margin=round(score - second, 3))
        return entry

    new = [r for r in rows if r[0][0] < hy]
    held_rows = [r for r in rows if r[0][0] > hy]
    held = [row(i, r) for i, r in enumerate(held_rows)]
    if held and held[-1]["drink_id"] is None and not complete and bottom - held_rows[-1][-1][0] < PARTIAL_GAP:
        # 第三十三局 D41：最后一行被视口切掉，只露出「パラメータ上昇量増加」，效果说明和图标都认不出。
        # 标 partial、给前缀候选；只剩一个候选才当认出。已有饮料的行序与底栏饮料顺序一致，调用方可用底栏（state.produce.drinks）核对候选。
        cands = _drinks_by_prefix("".join(t for _, t in held_rows[-1]))
        held[-1]["partial"] = True
        held[-1]["candidates"] = cands
        if len(cands) == 1:
            held[-1].update(drink_id=cands[0], name=_drink_effects()[cands[0]][0], method="effect_prefix")
    incomings = [row(i, r) for i, r in enumerate(new)]
    left = next((t for t, _ in boxes if "あと" in t and "選択" in t), None)
    m = re.search(r"あと(\d+)", left or "")
    return {"held": held, "incoming": incomings[-1] if incomings else None, "incomings": incomings, "select_left": int(m.group(1)) if m else None,
            "complete": complete}  # fmt: skip

