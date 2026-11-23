"""界面模板匹配（去 KAA 第 3 步：替换 KAA 的 R.* 模板）。

模板图和位置在 assets/pack/ui/templates.json（从 KAA 的界面截图切片搬来，名字沿用 R 的路径，如 "InProduce.ButtonUse"），
匹配做法和 KAA 一致：画面统一到 720x1280，灰度 TM_CCOEFF_NORMED，阈值缺省 0.8；fixed 的模板只在 slice_rect 里找，
有 region 的只在 region 里找，其余全屏找；同一处只留分数最高的一个（中心点落在已命中区域里的跳过）。
不做 KAA 的 colored 直方图比对（用到的模板都没开）。

    from hiro.vision.ui_templates import box, tpl
    if (m := tpl("InProduce.ButtonUse").find(img)) is not None:
        click(*m.center)
    find_text(img, "0枚", rect=box("InProduce.BoxNoSkillCard"))
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from hiro.vision.ocr import Box, Point

import numpy as np

PACK = Path(__file__).resolve().parents[3] / "assets" / "pack" / "ui"
SIZE = (720, 1280)  # 模板和坐标的基准分辨率（宽, 高）


@dataclass(frozen=True)
class Match:
    rect: Box
    score: float

    @property
    def center(self) -> Point:
        return self.rect.center


def normalize(img: np.ndarray) -> np.ndarray:
    """截图统一到 720x1280（竖屏）；已经是就原样返回。"""
    import cv2

    return (
        img
        if img.shape[1] == SIZE[0] and img.shape[0] == SIZE[1]
        else cv2.resize(img, SIZE, interpolation=cv2.INTER_AREA)
    )


def _gray(img: np.ndarray) -> np.ndarray:
    import cv2

    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


@dataclass(frozen=True)
class Template:
    name: str
    file: str
    slice_rect: tuple[int, int, int, int] | None = None  # 模板在 720x1280 画面里的位置 (x, y, w, h)
    threshold: float = 0.8
    fixed: bool = False
    region: tuple[int, int, int, int] | None = None  # 限定搜索区域 (x, y, w, h)
    source: str = field(default="", compare=False)

    @property
    def pixels(self) -> np.ndarray:
        return _pixels(self.file)

    def _search_rect(self, region) -> tuple[int, int, int, int] | None:
        if region is not None:
            return tuple(region)
        if self.region is not None:
            return self.region
        if self.fixed:
            return self.slice_rect
        return None

    def find_all(self, img: np.ndarray, *, threshold: float | None = None, region=None) -> list[Match]:
        """画面里所有命中（分数从高到低）。region 为 (x, y, w, h)，覆盖模板自己的搜索区域。"""
        import cv2

        screen = _gray(normalize(img))
        ox = oy = 0
        if (r := self._search_rect(region)) is not None:
            x, y, w, h = r
            ox, oy = max(0, x), max(0, y)
            screen = screen[oy : y + h, ox : x + w]
        tpl = self.pixels
        th, tw = tpl.shape[:2]
        if screen.shape[0] < th or screen.shape[1] < tw:
            return []
        res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= (self.threshold if threshold is None else threshold))
        used = np.zeros(screen.shape[:2], np.uint8)
        out = []
        for i in np.argsort(-res[ys, xs]):
            y, x = int(ys[i]), int(xs[i])
            if used[y + th // 2, x + tw // 2]:
                continue
            used[y : y + th, x : x + tw] = 1
            out.append(Match(Box(x + ox, y + oy, x + ox + tw, y + oy + th), float(res[y, x])))
        return out

    def find(self, img: np.ndarray, *, threshold: float | None = None, region=None) -> Match | None:
        hits = self.find_all(img, threshold=threshold, region=region)
        return hits[0] if hits else None

    def exists(self, img: np.ndarray, *, threshold: float | None = None, region=None) -> bool:
        return self.find(img, threshold=threshold, region=region) is not None


@cache
def _pixels(file: str) -> np.ndarray:
    import cv2

    img = cv2.imread(
        str(PACK / file), cv2.IMREAD_COLOR
    )  # 彩色读再转灰度（同 KAA；直接按灰度读分数差在千分位，并列时选点会不同）
    if img is None:
        raise FileNotFoundError(PACK / file)
    return _gray(img)


@cache
def _catalog() -> dict:
    return json.loads((PACK / "templates.json").read_text(encoding="utf-8"))


def _tuple(v):
    return tuple(v) if v is not None else None


# 素材包阈值之外单独放宽的模板：开局第 1–4 步左上角的步骤条。DMM 小窗口（541x961 放大）时正确的那个只有 0.77 分，
# 同一帧上别的步骤条最高 0.56（11-21 / 11-22 实测：KAA 主循环和新主循环都卡在「等第 2 步」）。KAA 的 R 模板另在 kaa_patches 放宽
THRESHOLD_OVERRIDE = {"Produce.TextStepIndicator1": 0.7, "Produce.TextStepIndicator2": 0.7, "Produce.TextStepIndicator3": 0.7,
                      "Produce.TextStepIndicator4Or2": 0.7}  # fmt: skip


@cache
def tpl(name: str) -> Template:
    e = _catalog()["templates"][name]
    threshold = THRESHOLD_OVERRIDE[name] if name in THRESHOLD_OVERRIDE else e.get("threshold", 0.8)
    return Template(name, e["file"], _tuple(e.get("slice_rect")), threshold, e.get("fixed", False),
                    _tuple(e.get("region")), e.get("source", ""))  # fmt: skip


def tap(name: str, img: np.ndarray, click) -> bool:
    """在 img 上找模板，找到就 click(x, y) 点中心（同 KAA 的 try_click）；返回是否点了。"""
    m = tpl(name).find(img)
    if m is None:
        return False
    click(*m.center)
    return True


def find_enabled(name: str, img: np.ndarray) -> Match | None:
    """找主按钮，且按钮是亮的（同 KAA 的 GakumasPrimaryButtonPrefab.q(enabled=True)）。"""
    m = tpl(name).find(img)
    return m if m is not None and primary_button_enabled(img, m) else None


def box(name: str) -> Box:
    """固定区域（KAA 的 HintBox），(x1, y1, x2, y2)，720x1280。"""
    return Box(*_catalog()["boxes"][name])


def point(name: str) -> Point:
    """固定点（KAA 的 HintPoint），720x1280：翻页箭头之类没有模板、按位置点的地方。"""
    return Point(*_catalog()["points"][name])


def _crop(img: np.ndarray, m: Match) -> np.ndarray:
    r = m.rect
    return normalize(img)[r.y1 : r.y2, r.x1 : r.x2]


def primary_button_enabled(img: np.ndarray, m: Match) -> bool | None:
    """主按钮（橙底）亮没亮：红通道五箱直方图的峰在第 5 箱 = 亮，第 4 箱 = 灰，其余不确定（同 KAA primary_button_state）。"""
    import cv2

    crop = _crop(img, m)
    if crop.size == 0:
        return None
    hist = cv2.calcHist([crop[..., 2]], [0], None, [5], [0, 256]).ravel()
    peak = int(np.argmax(hist))
    return True if peak == 4 else False if peak == 3 else None


def checkbox_checked(img: np.ndarray, m: Match) -> bool | None:
    """复选框选中（橙色占比 > 30% 且平均饱和度 > 100，同 KAA checkbox_checked_state）。"""
    import cv2

    crop = _crop(img, m)
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, (10, 50, 50), (25, 255, 255))
    return bool(np.count_nonzero(orange) / orange.size > 0.3 and float(hsv[..., 1].mean()) > 100)
