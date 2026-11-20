"""按游戏 UI 的真实结构渲染技能卡小图标（没有角标的「干净」版本），给回忆 / 牌组小图标认卡用。

结构取自客户端 data.unity3d 里开局准备页回忆格的技能卡图标
（ProduceStartScreen/…/Memory/List/MemoryThumbnail/ProduceCardImageButton，Il2CppDumper DummyDll + UnityPy 读出，
2026-11-06，导出脚本在作者本机，未随仓库发布），设计尺寸 96×96：

- NameBaseColor：四边内缩 4，bg_round_r12_24px 九宫格，染成 0.94 灰。
- BaseGradationMask：四边内缩 4 的圆角裁剪；里面是稀有度底色：SSR 用 card_gradation_ssr（外扩 2），
  其余用 rarity_gradiant_<r|sr|n>（比裁剪区宽 144、高 104，居中拉伸）。
- IlustRoot：252×281 缩放 0.31 居中；IlustMask 用分类遮罩九宫格（主动卡六边形、精神卡圆形、麻烦卡圆角方形）裁 281×281 的卡面插画，
  IlustFrame 叠分类外框（254×283）。
- BaseFrame：整个方框 card_frame 九宫格，染成 (0.32, 0.36, 0.43)。
叠在上面的角标（OnCardIcon：效果、「+」、费用、元気、课程数值、分类、定制计数）不画，认卡时按 OVERLAY_BOXES 遮掉。

开源版：渲染要的 13 张界面精灵图和卡面都在随仓库的素材包 assets/pack/ 里（关掉素材包时可用 tools/assets/export_card_sprites.py
从自己的客户端导出精灵图、卡面退到 KAA 公开数据 skill_cards/），稀有度 / 分类查 KAA game.db。生成渲染库：

    python -m hiro.vision.card_icon_render build      # → cache/card_icons.npz（约 2300 张，几分钟）

没有渲染库时 hiro.vision.memory_icons 退回「和整张插画比」的旧方法。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

SIZE = 96  # 设计尺寸
# 精灵图九宫格边距（左, 下, 右, 上，精灵图像素；data.unity3d Sprite.m_Border）
BORDERS = {
    "bg_round_r12_24px": (13, 13, 13, 13),
    "card_frame": (17, 17, 17, 17),
    "card_category_mask_activeskill": (0, 59, 0, 59),
    "card_category_frame_activeskill": (0, 61, 0, 61),
    "card_category_mask_mentalskill": (0, 0, 0, 0),
    "card_category_frame_mentalskill": (0, 0, 0, 0),
    "card_category_mask_trouble": (11, 11, 11, 11),
    "card_category_frame_trouble": (11, 11, 11, 11),
}
CATEGORY = {"ActiveSkill": "activeskill", "MentalSkill": "mentalskill", "Trouble": "trouble"}
ILUST_SCALE = 0.31
# 角标可能出现的区域（96 设计单位，x0, y0, x1, y1；由 OnCardIcon 的布局取并集）：左列效果、右中「+」、右下费用、右上元気、顶部课程数值、底部分类 / 定制
OVERLAY_BOXES = ((0, 0, 37, 96), (59, 28, 96, 68), (59, 66, 96, 96), (59, 0, 96, 34), (0, 0, 70, 30), (20, 68, 76, 96))


@lru_cache(maxsize=None)
def _sprite(sprites_dir: str, name: str) -> np.ndarray:
    """素材包 sprites/<图集>/<名>.png → RGBA float32（0–1）。"""
    d = Path(sprites_dir)
    hit = d / f"{name}.png" if (d / f"{name}.png").is_file() else next(d.glob(f"*/{name}.png"), None)
    if hit is None:
        raise FileNotFoundError(f"素材包里没有精灵图 {name}")
    im = cv2.imread(str(hit), cv2.IMREAD_UNCHANGED)
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA)
    elif im.shape[2] == 3:
        im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
    return im.astype(np.float32) / 255


def _sliced(sprites_dir: str, name: str, w: int, h: int, unit: float) -> np.ndarray:
    """九宫格拉伸到 w×h 像素；边距按所在层「每设计单位多少像素」unit 换算。中间格宽 0 时拉伸边缘那一列（同 Unity）。"""
    im = _sprite(sprites_dir, name)
    l, b, r, t = BORDERS.get(name, (0, 0, 0, 0))
    H, W = im.shape[:2]
    if not any((l, b, r, t)):
        return cv2.resize(im, (w, h), interpolation=cv2.INTER_LINEAR)
    L, B, R, T = (int(v * unit) for v in (l, b, r, t))
    s = min(1.0, w / max(1, L + R), h / max(1, T + B))
    L, R, T, B = int(L * s), int(R * s), int(T * s), int(B * s)
    out = np.zeros((h, w, 4), np.float32)

    def fix(a0, a1):
        return (a0, a1) if a1 > a0 else (max(0, a0 - 1), max(0, a0 - 1) + 1)

    for sy0, sy1, dy0, dy1 in ((0, t, 0, T), (t, H - b, T, h - B), (H - b, H, h - B, h)):
        sy0, sy1 = fix(sy0, sy1)
        for sx0, sx1, dx0, dx1 in ((0, l, 0, L), (l, W - r, L, w - R), (W - r, W, w - R, w)):
            sx0, sx1 = fix(sx0, sx1)
            if dy1 > dy0 and dx1 > dx0:
                out[dy0:dy1, dx0:dx1] = cv2.resize(im[sy0:sy1, sx0:sx1], (dx1 - dx0, dy1 - dy0), interpolation=cv2.INTER_LINEAR)
    return out


def _over(dst: np.ndarray, src: np.ndarray, x: int, y: int, clip: np.ndarray | None = None, tint=(1.0, 1.0, 1.0)) -> None:
    """RGBA src 以左上 (x, y) 叠到 dst；clip 是 dst 坐标系的裁剪 alpha；tint 为 RGB 染色。"""
    src = src * np.array([tint[2], tint[1], tint[0], 1.0], np.float32)
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y : y1 - y, x0 - x : x1 - x]
    a = s[..., 3:4] if clip is None else s[..., 3:4] * clip[y0:y1, x0:x1, None]
    d = dst[y0:y1, x0:x1]
    d[..., :3] = s[..., :3] * a + d[..., :3] * (1 - a)
    d[..., 3:4] = a + d[..., 3:4] * (1 - a)


def render(art: np.ndarray, rarity: str, category: str, sprites_dir: str | Path, *, out_size: int = 48, k: int = 3,
           background=(0.8, 0.8, 0.8)) -> np.ndarray:  # fmt: skip
    """卡面插画（BGR，任意尺寸方图）+ 稀有度（Ssr / Sr / R / N / Legend）+ 分类（ActiveSkill / MentalSkill / Trouble）→ out_size² 的 BGR uint8 图标。
    k：每设计单位渲染几个像素（之后缩到 out_size）。"""
    sd = str(sprites_dir)
    S = SIZE * k
    img = np.zeros((S, S, 4), np.float32)
    inset = 4 * k
    inner = S - 2 * inset
    base = _sliced(sd, "bg_round_r12_24px", inner, inner, k)
    _over(img, base, inset, inset, tint=(0.94, 0.94, 0.94))
    clip = np.zeros((S, S), np.float32)
    clip[inset : S - inset, inset : S - inset] = base[..., 3]
    if rarity not in ("Sr", "R", "N"):  # SSR；Legend（游戏里另有底纹和金框）按 SSR 的彩虹底近似
        w = inner + 4 * k
        _over(img, cv2.resize(_sprite(sd, "card_gradation_ssr"), (w, w)), inset - 2 * k, inset - 2 * k, clip)
    else:
        name = f"rarity_gradiant_{(rarity or 'r').lower()}"
        w, h = inner + 144 * k, inner + 104 * k
        _over(img, cv2.resize(_sprite(sd, name), (w, h), interpolation=cv2.INTER_LINEAR), inset - 72 * k, inset - 52 * k, clip)
    cat = CATEGORY.get(category, "activeskill")
    unit = ILUST_SCALE * k
    iw, ih = int(252 * unit), int(281 * unit)
    mask = np.zeros((S, S), np.float32)
    mask[(S - ih) // 2 : (S - ih) // 2 + ih, (S - iw) // 2 : (S - iw) // 2 + iw] = _sliced(sd, f"card_category_mask_{cat}", iw, ih, unit)[..., 3]
    aw = int(281 * unit)
    a = cv2.resize(art, (aw, aw), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    _over(img, np.dstack([a, np.ones((aw, aw), np.float32)]), (S - aw) // 2, (S - aw) // 2, ((mask * clip) > 0.5).astype(np.float32))
    fw, fh = int(254 * unit), int(283 * unit)
    _over(img, _sliced(sd, f"card_category_frame_{cat}", fw, fh, unit), (S - fw) // 2, (S - fh) // 2, clip)
    _over(img, _sliced(sd, "card_frame", S, S, k), 0, 0, tint=(0.32, 0.36, 0.43))
    out = cv2.resize(img, (out_size, out_size), interpolation=cv2.INTER_AREA)
    bg = np.array(background, np.float32)[None, None, ::-1]
    return (np.clip(out[..., :3] * out[..., 3:4] + bg * (1 - out[..., 3:4]), 0, 1) * 255).astype(np.uint8)


def overlay_mask(size: int) -> np.ndarray:
    """size×size 的布尔图：True = 不会被角标盖住、参与比对的像素。"""
    m = np.ones((SIZE, SIZE), np.float32)
    for x0, y0, x1, y1 in OVERLAY_BOXES:
        m[y0:y1, x0:x1] = 0
    return cv2.resize(m, (size, size), interpolation=cv2.INTER_AREA) > 0.5


SPRITES = ("bg_round_r12_24px", "card_frame", "card_gradation_ssr", "rarity_gradiant_ssr", "rarity_gradiant_sr", "rarity_gradiant_r",
           "rarity_gradiant_n", "card_category_mask_activeskill", "card_category_mask_mentalskill", "card_category_mask_trouble",
           "card_category_frame_activeskill", "card_category_frame_mentalskill", "card_category_frame_trouble")  # fmt: skip
CACHE = Path("cache") / "card_icons.npz"
NAME_RARITY = {"0": "N", "1": "R", "2": "Sr", "3": "Ssr", "100": "Ssr"}
NAME_CATEGORY = {"act": "ActiveSkill", "men": "MentalSkill"}


def _rarity_category() -> dict[str, tuple[str, str]]:
    """主数据（AssetSource.card_catalog）：资源名 → (稀有度 Ssr/Sr/R/N, 分类 ActiveSkill/MentalSkill/Trouble)。"""
    from hiro import plugins

    src = plugins.get("asset_source")
    cat = (src.card_catalog() if src is not None else None) or {}
    out: dict[str, tuple[str, str]] = {}
    for c in cat.values():
        if c.get("upgrade", 0) == 0 and c.get("asset"):
            out.setdefault(c["asset"], (c["rarity"].removeprefix("ProduceCardRarity_"), c["category"].removeprefix("ProduceCardCategory_")))
    return out


def _from_name(name: str) -> tuple[str, str] | None:
    import re

    m = re.search(r"skillcard_(act|men)-(\d+)_\d+", name)
    return (NAME_RARITY.get(m.group(2), "R"), NAME_CATEGORY[m.group(1)]) if m else None


def build(out: Path = CACHE) -> int:
    """卡面目录的每张卡 → 渲染图标，存 out（names, imgs）。返回张数。"""
    from hiro.vision import assets

    cards, sprites = assets.card_art_dir(), assets.sprites_dir()
    if cards is None:
        raise SystemExit("没有卡面目录（KAA 公开数据 skill_cards/ 或 HIRO_USER_ASSETS/cards/）")
    missing = [n for n in SPRITES if sprites is None or not (any(Path(sprites).glob(f"{n}.png")) or any(Path(sprites).glob(f"*/{n}.png")))]
    if missing:
        raise SystemExit(f"素材目录 sprites/ 缺精灵图 {missing}：先运行 python tools/assets/export_card_sprites.py")
    meta = _rarity_category()
    names, imgs = [], []
    for png in sorted(Path(cards).glob("*.png")):
        base = png.stem
        while base not in meta and "-" in base.removeprefix("img_general_skillcard_"):
            base = base.rsplit("-", 1)[0]
        rc = meta.get(base) or _from_name(png.stem)
        if rc is None or rc[1] not in CATEGORY:
            continue
        names.append(png.stem)
        imgs.append(render(cv2.imread(str(png)), rc[0], rc[1], sprites))
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, names=np.array(names), imgs=np.stack(imgs))
    return len(names)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["build"]:
        raise SystemExit("用法：python -m hiro.vision.card_icon_render build")
    print(f"card_icons：{build()} 张 → {CACHE}")
