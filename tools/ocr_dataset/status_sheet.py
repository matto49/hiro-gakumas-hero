"""考试状态图标对照表：状态栏图标上没有字，标注时把「放大的状态栏」和「素材包图标 + 名字」拼成一张图给模型。

图标来自识别素材包（assets/pack/icons/status，和识别系统用的同一套）；字形完全相同的图标合成一格；
图库里没有单独的「消費体力増加」图标（游戏里是红底的「消費体力減少」字形），单独补一格红底的。
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import cache
from pathlib import Path

import cv2
import numpy as np

FONTS = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
BLUE, RED = (36, 132, 228), (214, 52, 64)
NOTE = (
    "【状态栏怎么看】图里拼了放大的左侧状态栏（从上到下）和白底的状态图标对照表（图标 + 名字）。"
    "状态名照对照表认图案写；对照表里没有、或认不准的写 null。"
)


def _font(size: int):
    from PIL import ImageFont

    for p in [os.environ.get("HIRO_CJK_FONT")] + list(FONTS):
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    raise RuntimeError("找不到能写日文的字体：设 HIRO_CJK_FONT 指向一个 CJK 字体文件")


def cells(icon_dir: Path) -> list[tuple[np.ndarray, str, tuple]]:
    """→ [(RGBA 图标, 名字, 底色)]，按名字排序；字形相同的合成一格，名字用「 / 」连。"""
    from PIL import Image

    meta = json.loads((icon_dir / "index.json").read_text(encoding="utf-8"))
    groups: dict[str, dict] = {}
    for key, m in meta.items():
        if not m.get("name"):
            continue
        im = np.asarray(Image.open(icon_dir / f"img_general_icon_exam-effect_{key}.png").convert("RGBA"))
        g = groups.setdefault(hashlib.md5(im[..., 3].tobytes()).hexdigest(), {"im": im, "names": []})
        if m["name"] not in g["names"]:
            g["names"].append(m["name"])
    out = [(g["im"], " / ".join(g["names"]), BLUE) for g in groups.values()]
    down = next((g for g in groups.values() if "消費体力減少" in g["names"]), None)
    if down is not None:
        out.append((down["im"], "消費体力増加（红底）", RED))
    return sorted(out, key=lambda c: c[1])


@cache
def sheet(icon_dir: str) -> np.ndarray:
    """对照表（BGR）。"""
    from PIL import Image, ImageDraw

    cs = cells(Path(icon_dir))
    cols, cw, ch, s = 8, 200, 118, 64
    rows = (len(cs) + cols - 1) // cols
    img = Image.new("RGB", (cols * cw, rows * ch + 40), "white")
    d = ImageDraw.Draw(img)
    font = _font(15)
    d.text((10, 8), "考试状态图标对照表（游戏里图标画在菱形底上，右下角是数字）", fill="black", font=font)
    for i, (im, name, bg) in enumerate(cs):
        x, y = (i % cols) * cw, (i // cols) * ch + 40
        cx = x + cw // 2
        d.polygon([(cx, y + 4), (cx + 36, y + 40), (cx, y + 76), (cx - 36, y + 40)], fill=bg)
        icon = Image.fromarray(im).resize((s, s))
        img.paste(icon, (cx - s // 2, y + 8), icon)
        lines, cur = [], ""
        for c in name:
            if d.textlength(cur + c, font=font) > cw - 8:
                lines.append(cur)
                cur = ""
            cur += c
        lines.append(cur)
        for j, ln in enumerate(lines[:2]):
            d.text((cx - d.textlength(ln, font=font) / 2, y + 80 + j * 17), ln, fill="black", font=font)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def default_icon_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "pack" / "icons" / "status"


def compose(img: np.ndarray, region: list[int], icon_dir: Path | None = None) -> np.ndarray:
    """整帧（任意尺寸，按 720x1280 的坐标取 region）→ [放大的状态栏 | 对照表]，高度对齐对照表。"""
    ref = sheet(str(icon_dir or default_icon_dir()))
    h, w = img.shape[:2]
    sx, sy = w / 720, h / 1280
    x1, y1, x2, y2 = region
    crop = img[round(y1 * sy) : round(y2 * sy), round(x1 * sx) : round(x2 * sx)]
    H = ref.shape[0]
    crop = cv2.resize(crop, (max(1, round(crop.shape[1] * H / crop.shape[0])), H), interpolation=cv2.INTER_CUBIC)
    gap = np.full((H, 16, 3), 255, np.uint8)
    return np.hstack([crop, gap, ref])
