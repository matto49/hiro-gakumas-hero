"""从使用者自己的考试截图里提取考试效果图标与 P 道具图标，补齐默认素材下认不出的效果名 / P 道具名。

随仓库的素材包 assets/pack/ 已经带了这些图标；素材包缺的（游戏更新后新出的）或关掉素材包时，用这个工具从你自己的截图补，
产物写到自建素材目录（HIRO_USER_ASSETS，默认 resources/user_assets/）：
    icons/status/img_general_icon_exam-effect_<类型>.png + index.json   考试效果（やる気、好印象 …）
    icons/pitem/<资源名>.png + index.json                              P 道具

三步（每步可重复跑；已认得的图标不会再出现）：

    # 1. 收集：按固定位置裁出效果栏 / P 道具栏，按外观聚类去重，每类出一张预览
    python tools/assets/capture_icons.py collect 截图目录或文件… --work .temp/icons
    # 2. 标名：二选一
    #    a) 手填：打开 .temp/icons/sheet_status.png / sheet_pitem.png 对照编号，在 .temp/icons/labels.json 里填 name
    #       （效果名的候选在 candidates_status.txt；P 道具填游戏里的道具名；不是图标 / 认不出的填 null 或删掉）
    #    b) 交给 LLM 看图标名：python tools/assets/capture_icons.py label --work .temp/icons --llm claude:sonnet
    # 3. 写入自建素材目录
    python tools/assets/capture_icons.py apply --work .temp/icons

截图要求：竖屏、与 720x1280 同比例（其他分辨率会缩放），考试画面（左侧效果栏、右侧 P 道具栏可见）。
打一两场考试的录屏帧 / 截图就够：同一个图标出现多次会合成一张（取中位数，去掉透出来的背景）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

W, H = 720, 1280

# ---------------------------------------------------------------- 几何（与 hiro.vision.status / items 一致）
# 效果栏：固定间距的竖排菱形，首槽中心与间距取自客户端布局（hiro.vision.layout），没有布局时用实测值
SLOT_HALF = 28
MAX_SLOTS = 10
BLUE = ((95, 120, 150), (110, 255, 255))
BLUE_MIN_RATIO = 0.08  # 槽内蓝底占比：考试效果 0.19–0.32，卡牌持续效果（卡面局部）为 0
STATUS_SIZE = 42  # 保存的效果图标边长：游戏里图标显示约 42 像素（hiro.vision.status.GLYPH_SIZES 的最大一档）
# P 道具栏
PITEM_SIZE = 66
PITEM_TOP_LEFT = (634, 313)
PITEM_PITCH = 70
PITEM_MAX = 4

STATUS_SAME = 0.8  # 两张效果字形相关 ≥ 此值算同一个图标
PITEM_SAME = 0.6  # P 道具半透明、透出背景，同一道具不同帧约 0.65–0.9
KNOWN_SCORE = 0.85  # 与已有图标这么像就不再收集


def _slot_geometry() -> tuple[int, int, float]:
    try:
        from hiro.vision import layout

        node = "BuffListRoot/BuffList/ExamMainBuffView"
        x, y = (round(v) for v in layout.center(layout.EXAM, node))
        return x, y, layout.node(layout.EXAM, node)[1]["rect"][3]
    except Exception:  # noqa: BLE001 布局读不到时用实测值
        return 45, 262, 61.3


SLOT_X, SLOT_Y0, SLOT_PITCH = _slot_geometry()


def _norm_frame(img: np.ndarray) -> np.ndarray | None:
    h, w = img.shape[:2]
    if h < w or abs(h / w - H / W) > 0.02:
        return None  # 不是竖屏 720x1280 比例：不是考试截图
    return img if (w, h) == (W, H) else cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- 考试效果：白色图案
def _white(patch: np.ndarray) -> np.ndarray:
    return cv2.inRange(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV), (0, 0, 215), (180, 70, 255))


def status_glyph(img: np.ndarray, k: int) -> np.ndarray | None:
    """第 k 个效果槽 → 去掉菱形描边与右下角数值后的白色图案（STATUS_SIZE 见方，0/255）；不是蓝底效果槽返回 None。"""
    cy = round(SLOT_Y0 + SLOT_PITCH * k)
    patch = img[cy - SLOT_HALF : cy + SLOT_HALF, SLOT_X - SLOT_HALF : SLOT_X + SLOT_HALF]
    if patch.shape[:2] != (2 * SLOT_HALF, 2 * SLOT_HALF):
        return None
    blue = cv2.inRange(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV), *BLUE)
    if np.count_nonzero(blue) / blue.size < BLUE_MIN_RATIO:
        return None
    mask = _white(patch)
    yy, xx = np.mgrid[: 2 * SLOT_HALF, : 2 * SLOT_HALF]
    ring = np.abs(xx - SLOT_HALF + 0.5) + np.abs(yy - SLOT_HALF + 0.5)  # 到中心的菱形距离：菱形描边约 27
    mask[ring >= 26] = 0
    n, lab, stats, cents = cv2.connectedComponentsWithStats(mask)
    keep = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i][cv2.CC_STAT_AREA] < 6:
            continue
        cx, cy2 = cents[i] - SLOT_HALF
        band = (ring[lab == i] >= 21).mean()
        if band > 0.6:
            continue  # 贴着菱形边：描边残段或右下角的数值
        if cx > 6 and cy2 > 6 and band > 0.3:
            continue  # 右下角的数值（「18」「20%」）伸进了菱形
        keep[lab == i] = 255
    if np.count_nonzero(keep) < 40:
        return None  # 图案被特效盖住 / 槽里是别的东西
    ys, xs = np.nonzero(keep)
    mx, my = (xs.min() + xs.max() + 1) // 2, (ys.min() + ys.max() + 1) // 2  # 以图案外框中心为准，各帧对齐
    h = STATUS_SIZE // 2
    out = np.zeros((STATUS_SIZE, STATUS_SIZE), np.uint8)
    x1, y1 = mx - h, my - h
    sx1, sy1, sx2, sy2 = (
        max(0, x1),
        max(0, y1),
        min(keep.shape[1], x1 + STATUS_SIZE),
        min(keep.shape[0], y1 + STATUS_SIZE),
    )
    out[sy1 - y1 : sy2 - y1, sx1 - x1 : sx2 - x1] = keep[sy1:sy2, sx1:sx2]
    return out


ROUGH_MAX = 0.3  # 图案边缘粗糙度上限：游戏图标缩到 42 像素最粗糙的（熱意増加）0.255；被特效盖住 / 碎片 0.4–1.0
FILL_MAX = 0.5  # 白色占比上限：图标最多 0.41；白闪盖住整个菱形 0.67


def glyph_is_clean(g: np.ndarray) -> bool:
    """像一个完整图标：边缘平滑（开闭运算前后差得少）、没有整片发白。被特效遮住、只剩碎片的类不出给人标。"""
    m = (g > 127).astype(np.uint8)
    if not m.any() or m.mean() > FILL_MAX:
        return False
    k = np.ones((3, 3), np.uint8)
    smooth = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
    return float((smooth != m).sum()) / m.sum() <= ROUGH_MAX


def _glyph_sim(a: np.ndarray, b: np.ndarray) -> float:
    """两张效果图案的相似度：±2 像素内平移取最大的归一化相关。"""
    fa = cv2.GaussianBlur(a.astype(np.float32), (0, 0), 1.0)
    fb = cv2.copyMakeBorder(
        cv2.GaussianBlur(b.astype(np.float32), (0, 0), 1.0), 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0
    )
    return float(cv2.minMaxLoc(cv2.matchTemplate(fb, fa, cv2.TM_CCOEFF_NORMED))[1])


# ---------------------------------------------------------------- P 道具：彩色图块
def pitem_crop(img: np.ndarray, k: int) -> np.ndarray | None:
    x0, y0 = PITEM_TOP_LEFT
    y = y0 + PITEM_PITCH * k
    crop = img[y : y + PITEM_SIZE, x0 : x0 + PITEM_SIZE]
    return crop if crop.shape[:2] == (PITEM_SIZE, PITEM_SIZE) else None


CONTEXT_W = 24  # P 道具左侧的一条背景


def pitem_context(img: np.ndarray, k: int) -> np.ndarray:
    """P 道具左边紧挨着的一条背景：同一道具在不同背景前出现过，才说明裁到的是道具而不是背景（空栏）。"""
    x0, y0 = PITEM_TOP_LEFT
    y = y0 + PITEM_PITCH * k
    return img[y : y + PITEM_SIZE, x0 - CONTEXT_W - 4 : x0 - 4]


def _highpass(bgr: np.ndarray) -> np.ndarray:
    f = bgr.astype(np.float32)
    return f - cv2.GaussianBlur(f, (0, 0), 4)


def _pitem_sim(a: np.ndarray, b: np.ndarray) -> float:
    ha, hb = _highpass(a).ravel(), _highpass(b).ravel()
    ha, hb = ha - ha.mean(), hb - hb.mean()
    return float(ha @ hb / (np.linalg.norm(ha) * np.linalg.norm(hb) + 1e-6))


# ---------------------------------------------------------------- 聚类
@dataclass
class Cluster:
    kind: str  # status / pitem
    id: int
    members: list[np.ndarray] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    contexts: list[np.ndarray] = field(default_factory=list)

    def image(self) -> np.ndarray:
        """代表图：多张取逐像素中位数（P 道具半透明，透出的背景各帧不同，中位数留下图标本身）。"""
        if self.kind == "status":
            return ((np.mean([m > 127 for m in self.members], axis=0) >= 0.5) * 255).astype(np.uint8)
        return np.median(np.stack(self.members), axis=0).astype(np.uint8)


def _similar(kind: str, a: np.ndarray, b: np.ndarray) -> float:
    return _glyph_sim(a, b) if kind == "status" else _pitem_sim(a, b)


CONTEXT_SAME = 0.5  # 两帧的背景条纹理相关低于此值算「背景不同」
CONTEXT_COLOR = 30  # 或平均颜色差这么多
TEXTURE_MIN = 4.0  # 纹理（高通标准差）低于此值的背景条只比颜色：平涂背景的高通是噪声，相关总是很低


def _background_differs(a: np.ndarray, b: np.ndarray) -> bool:
    if float(np.linalg.norm(a.reshape(-1, 3).mean(0) - b.reshape(-1, 3).mean(0))) >= CONTEXT_COLOR:
        return True
    if min(_highpass(a).std(), _highpass(b).std()) < TEXTURE_MIN:
        return False
    return _pitem_sim(a, b) < CONTEXT_SAME


def _on_varied_background(c: Cluster) -> bool:
    """P 道具类：至少有两张的背景条不同（道具没变、背景变了）。空栏裁到的是背景，背景一变就凑不成类；
    连续几帧背景不动时空栏也会凑成类，用这条排除。"""
    ctx = c.contexts
    return any(_background_differs(ctx[i], ctx[j]) for i in range(len(ctx)) for j in range(i + 1, len(ctx)))


def _chain_pitems(clusters: list[Cluster]) -> list[Cluster]:
    """P 道具从上往下连续排列：第 k 格（k ≥ 1）只有同一帧第 k-1 格也是道具时才算，否则多半是空栏裁到的背景。
    只留至少 2 张算数的类。"""
    where = {src: c for c in clusters for src in c.sources}
    ok: set[str] = set()
    for k in range(PITEM_MAX):
        for src in where:
            frame, slot = src.rsplit("#p", 1)
            if int(slot) == k and (k == 0 or f"{frame}#p{k - 1}" in ok):
                ok.add(src)
    out = []
    for c in clusters:
        keep = [i for i, src in enumerate(c.sources) if src in ok]
        if len(keep) >= 2:
            out.append(
                Cluster(
                    c.kind,
                    c.id,
                    [c.members[i] for i in keep],
                    [c.sources[i] for i in keep],
                    [c.contexts[i] for i in keep],
                )
            )
    return out


def cluster(kind: str, samples: list[tuple], known: list[np.ndarray] = ()) -> list[Cluster]:
    """贪心聚类：与某类第一张足够像就归入，否则新开一类。与已有图标（known）像的样本直接丢掉。
    samples：效果 (图案, 来源)；P 道具 (图块, 来源, 左侧背景条)。P 道具类只留在不同背景前出现过的（见 _on_varied_background）。"""
    same = STATUS_SAME if kind == "status" else PITEM_SAME
    out: list[Cluster] = []
    for img, src, *ctx in samples:
        if any(_similar(kind, img, k) >= KNOWN_SCORE for k in known):
            continue
        for c in out:
            if _similar(kind, img, c.members[0]) >= same:
                break
        else:
            c = Cluster(kind, len(out))
            out.append(c)
        c.members.append(img)
        c.sources.append(src)
        c.contexts.extend(ctx)
    if kind == "pitem":
        out = _chain_pitems([c for c in out if _on_varied_background(c)])
    else:
        out = [c for c in out if glyph_is_clean(c.image())]
    out.sort(key=lambda c: -len(c.members))
    for i, c in enumerate(out):
        c.id = i
    return out


# ---------------------------------------------------------------- 主数据：候选名
def _master_db() -> sqlite3.Connection | None:
    try:
        from hiro.defaults.assets import PublicAssetSource

        return PublicAssetSource().master_db()
    except Exception:  # noqa: BLE001
        return None


def status_candidates(db: sqlite3.Connection | None = None) -> list[dict]:
    """考试效果候选 [{type, key, name}]：主数据 ProduceDescriptionExamEffect 里有图标的效果类型。"""
    db = db or _master_db()
    if db is None:
        return []
    rows = db.execute(
        "select type, name from ProduceDescriptionExamEffect where coalesce(noIcon, 0) = 0 and name != ''"
    ).fetchall()
    return [{"type": t, "key": t.removeprefix("ProduceExamEffectType_").lower(), "name": n} for t, n in rows]


def pitem_names(db: sqlite3.Connection | None = None) -> dict[str, str]:
    """P 道具名 → 资源名（assetId）。"""
    db = db or _master_db()
    if db is None:
        return {}
    return dict(db.execute("select name, assetId from ProduceItem where name != '' and assetId != ''").fetchall())


def _resolve(name: str, choices: list[str]) -> str | None:
    if name in choices:
        return name
    hit = difflib.get_close_matches(name, choices, n=1, cutoff=0.8)
    return hit[0] if hit else None


# ---------------------------------------------------------------- 自建素材目录
def user_dir(out: str | Path | None = None) -> Path:
    return Path(out or os.environ.get("HIRO_USER_ASSETS") or "resources/user_assets")


def _known(folder: Path, kind: str) -> list[np.ndarray]:
    if not folder.is_dir():
        return []
    out = []
    for png in sorted(folder.glob("*.png")):
        im = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        if im is None:
            continue
        if kind == "status":
            alpha = im[:, :, 3] if im.ndim == 3 and im.shape[2] == 4 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            out.append(cv2.resize(alpha, (STATUS_SIZE, STATUS_SIZE), interpolation=cv2.INTER_AREA))
        else:
            out.append(cv2.resize(im[:, :, :3], (PITEM_SIZE, PITEM_SIZE), interpolation=cv2.INTER_AREA))
    return out


# ---------------------------------------------------------------- 1. collect
def _exam_filter():
    """考试画面判定（hiro.vision.hif.is_hif_exam，要 KAA）；导入不了时不过滤，所有帧都当考试画面。"""
    try:
        from hiro.vision.hif import is_hif_exam

        return is_hif_exam
    except Exception:  # noqa: BLE001
        return None


def _images(paths: list[str], exam_only: bool = True):
    is_exam = _exam_filter() if exam_only else None
    for p in map(Path, paths):
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in (".png", ".jpg", ".jpeg")) if p.is_dir() else [p]
        for f in files:
            img = cv2.imread(str(f))
            if img is None or (img := _norm_frame(img)) is None:
                continue
            if is_exam is None or is_exam(img):
                yield f, img


def collect(
    paths: list[str], work: Path, out: Path | None = None, *, exam_only: bool = True
) -> dict[str, list[Cluster]]:
    """exam_only：装了 KAA 时只取考试画面（录屏目录里混着别的画面也行）；没装 KAA 时不过滤。"""
    status, pitem = [], []
    for f, img in _images(paths, exam_only):
        for k in range(MAX_SLOTS):
            g = status_glyph(img, k)
            if g is None:
                break  # 效果栏从上往下连续排列
            status.append((g, f"{f.name}#s{k}"))
        for k in range(PITEM_MAX):
            c = pitem_crop(img, k)
            if c is not None:
                pitem.append((c, f"{f.name}#p{k}", pitem_context(img, k)))
    root = user_dir(out)
    clusters = {"status": cluster("status", status, _known(root / "icons" / "status", "status")),
                "pitem": cluster("pitem", pitem, _known(root / "icons" / "pitem", "pitem"))}  # fmt: skip
    _write_work(work, clusters)
    return clusters


def _preview(kind: str, img: np.ndarray, scale: int = 3) -> np.ndarray:
    if kind == "status":  # 白图案画回蓝底，接近游戏里的样子
        bg = np.full((*img.shape, 3), (230, 160, 80), np.uint8)
        bg[img > 127] = 255
        img = bg
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def _sheet(kind: str, clusters: list[Cluster]) -> np.ndarray:
    cells = []
    for c in clusters:
        im = _preview(kind, c.image())
        im = cv2.copyMakeBorder(im, 0, 26, 4, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        cv2.putText(
            im,
            f"#{c.id} x{len(c.members)}",
            (4, im.shape[0] - 7),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
        cells.append(im)
    if not cells:
        return np.full((40, 200, 3), 255, np.uint8)
    h, w = max(c.shape[0] for c in cells), max(c.shape[1] for c in cells)
    cells = [
        cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, w - c.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
        for c in cells
    ]
    cols = 8
    rows = [np.hstack(cells[i : i + cols] + [np.full((h, w, 3), 255, np.uint8)] * (cols - len(cells[i : i + cols])))
            for i in range(0, len(cells), cols)]  # fmt: skip
    return np.vstack(rows)


def _write_work(work: Path, clusters: dict[str, list[Cluster]]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    labels = {}
    for kind, cs in clusters.items():
        (work / kind).mkdir(exist_ok=True)
        for old in (work / kind).glob("*.png"):
            old.unlink()
        for c in cs:
            cv2.imwrite(str(work / kind / f"{c.id}.png"), c.image())
            cv2.imwrite(str(work / kind / f"{c.id}_preview.png"), _preview(kind, c.image()))
        cv2.imwrite(str(work / f"sheet_{kind}.png"), _sheet(kind, cs))
        labels[kind] = {str(c.id): {"name": None, "count": len(c.members), "examples": c.sources[:3]} for c in cs}
    (work / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    cands = status_candidates()
    (work / "candidates_status.txt").write_text("\n".join(c["name"] for c in cands) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 2. label（LLM）
LLM_SYSTEM = "你在帮忙给手机游戏《学園アイドルマスター》的界面图标标名字。只回答 JSON。"

# 常见效果图标的外观（文字描述，不含素材）。没有这张表时 LLM 会把握り拳认成「強気」、親指を立てた手认成「好調」
ICON_HINTS = """常见效果图标的样子（白色图案）：
やる気=握り拳 / 好印象=親指を立てた手（いいね）/ 好調=力こぶ（曲げた腕）/ 絶好調=力こぶ＋上向き矢印 / 集中=ギザギザの折れ線（王冠形）/
元気=上が開いた盾（ポケット）形＋上向き矢印 / 強気=炎 / 熱意=放射状に弾ける線 / 全力値=六芒星 / 全力=六芒星＋きらめき / 温存=電池 /
体力回復=塗りつぶしのハート / 体力回復効果増加=輪郭だけのハート＋上向き矢印 / 体力回復無効=ハート＋小さな「×」/
消費体力減少=ハート＋小さな「%」/ 体力消費軽減=ハート＋下向き矢印 / 消費体力削減=カードにハート＋下向き矢印 / 消費体力追加=カードにハート＋上向き矢印 /
低下状態無効=下向き矢印＋小さな「×」/ 発動予約=時計 / パラメータ上昇量増加=太い上向き矢印 / パラメータ上昇量減少=太い下向き矢印 / パラメータ上昇値変更=上向き山形三つ /
のんびり=亀 / 不調=縦線三本 / 緊張=しかめ顔 / 弱気=しずく / スランプ=「!?」/ 不安=竜巻 / 気まぐれ=渦巻き / うわの空=考え雲 / 高揚=人差し指を立てた手 / 成長=芽。
やる気 / 好印象 / 好調 / 集中 / 熱意 / 全力値 の図案に小さな記号が付くもの：「%」=〜増加量増加、「+」=〜増加量追加、「^」=〜強化
（ハートの系統は上の個別の説明に従う）。"""


def label_with_llm(work: Path, llm) -> dict:
    """每类代表图交给 LLM（hiro.brain.llm 适配器的 ask_json_image），效果名从主数据候选里选，P 道具名自由回答再对主数据名。
    LLM 不确定时应回答 null；对不上候选的名字不写入。"""
    labels = json.loads((work / "labels.json").read_text(encoding="utf-8"))
    cands = [c["name"] for c in status_candidates()]
    items = list(pitem_names())
    for kind, entries in labels.items():
        for cid, entry in entries.items():
            if entry.get("name"):
                continue  # 已填的不覆盖
            png = cv2.imencode(
                ".jpg", _preview(kind, cv2.imread(str(work / kind / f"{cid}.png"), cv2.IMREAD_UNCHANGED))
            )[1].tobytes()
            if kind == "status":
                prompt = ("这是考试画面左侧效果栏的一个效果图标（蓝色底、白色图案）。从下面的效果名里选出它对应的一个，"
                          '回答 {"name": "效果名", "sure": true/false}。图案残缺、有杂点、像被特效盖住，或在几个候选之间拿不准时，'
                          '回答 {"name": null, "sure": false}，不要猜：标错的名字会让程序一直读错。\n' + ICON_HINTS + "\n候选：" + "、".join(cands))  # fmt: skip
            else:
                prompt = ('这是考试画面右侧的一个 P 道具（Pアイテム）图标（半透明，可能透出背景）。回答它的道具名 '
                          '{"name": "道具名", "sure": true/false}（日文，游戏内名称，含颜色等后缀）；'
                          '不是道具图标（只是背景）或认不出时回答 {"name": null, "sure": false}，不要猜。')  # fmt: skip
            r = llm.ask_json_image(LLM_SYSTEM, prompt, png)
            data = (r.data or {}) if r.error is None else {}
            name = data.get("name")
            if name:
                entry["llm"] = name
                if data.get("sure") is True:  # 拿不准的只记下来供人参考，不写 name
                    entry["name"] = _resolve(name, cands if kind == "status" else items)
    (work / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    return labels


# ---------------------------------------------------------------- 3. apply
def apply(work: Path, out: Path | None = None) -> dict[str, list[str]]:
    """labels.json 里填了名字的类 → 写到自建素材目录（已有的 index.json 合并，不删别的图标）。返回 {kind: [写入的键]}。"""
    labels = json.loads((work / "labels.json").read_text(encoding="utf-8"))
    root = user_dir(out)
    by_name = {}
    for c in status_candidates():
        by_name.setdefault(c["name"], c)  # 同名多类型取第一个
    items = pitem_names()
    written: dict[str, list[str]] = {"status": [], "pitem": []}
    named: dict[str, set[str]] = {"status": set(), "pitem": set()}
    for kind, entries in labels.items():
        folder = root / "icons" / kind
        index_path = folder / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        for cid, entry in entries.items():
            name = entry.get("name")
            src = work / kind / f"{cid}.png"
            if not name or not src.exists():
                continue
            if name in named[kind]:
                continue  # 同一个图标分成了几类（特效遮挡等）：留样本最多的那类（编号小的）
            img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
            folder.mkdir(parents=True, exist_ok=True)
            if kind == "status":
                hit = by_name.get(_resolve(name, list(by_name)) or "")
                key = hit["key"] if hit else "user_" + "".join(ch for ch in name if ch.isalnum())
                rgba = np.dstack([np.full_like(img, 255)] * 3 + [img])  # 识别只用 alpha（白色图案）
                cv2.imwrite(str(folder / f"img_general_icon_exam-effect_{key}.png"), rgba)
                # source=capture：识别层对自建图标用更严的分数 / 领先门槛（图库不全时没收进来的图标会被最像的顶上）
                index[key] = {
                    "type": hit["type"] if hit else None,
                    "name": hit["name"] if hit else name,
                    "source": "capture",
                }
            else:
                real = _resolve(name, list(items))
                key = items[real] if real else f"user_pitem_{cid}"
                cv2.imwrite(str(folder / f"{key}.png"), np.dstack([img, np.full(img.shape[:2], 255, np.uint8)]))
                index[key] = real or name
            written[kind].append(key)
            named[kind].add(name)
        if written[kind]:
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return written


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="从截图裁图标、聚类、出预览")
    c.add_argument("paths", nargs="+")
    c.add_argument("--all-frames", action="store_true", help="不按考试画面过滤（默认装了 KAA 时只取考试画面）")
    lb = sub.add_parser("label", help="交给 LLM 标名字（也可以手改 labels.json）")
    lb.add_argument(
        "--llm", default=None, help="hiro.brain.llm 的 spec，如 claude:sonnet / openai:<model>；缺省读 HIRO_LLM"
    )
    a = sub.add_parser("apply", help="把标了名字的图标写进自建素材目录")
    for p in (c, lb, a):
        p.add_argument("--work", default=".temp/icons", help="工作目录（预览图、labels.json）")
        p.add_argument("--out", default=None, help="自建素材目录，缺省 HIRO_USER_ASSETS 或 resources/user_assets")
    args = ap.parse_args(argv)
    work = Path(args.work)
    if args.cmd == "collect":
        cs = collect(args.paths, work, args.out and Path(args.out), exam_only=not args.all_frames)
        print(f"效果图标 {len(cs['status'])} 类、P 道具 {len(cs['pitem'])} 类 → {work}/sheet_*.png、{work}/labels.json")
        print("下一步：在 labels.json 里填 name（或 label --llm），然后 apply")
    elif args.cmd == "label":
        from hiro.brain.llm import from_spec

        labels = label_with_llm(work, from_spec(args.llm))
        for kind, entries in labels.items():
            done = [f"#{cid} {e['name']}" for cid, e in entries.items() if e.get("name")]
            print(f"{kind}: {len(done)}/{len(entries)} 类标上了名字：{'、'.join(done) or '无'}")
        print(
            f"LLM 也会认错：apply 前请对照 {work}/sheet_*.png 检查 labels.json（拿不准的写在 llm 字段、name 为 null，可手填）"
        )
    else:
        written = apply(work, args.out and Path(args.out))
        print(f"写入 {user_dir(args.out)}：效果图标 {len(written['status'])} 个、P 道具 {len(written['pitem'])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
