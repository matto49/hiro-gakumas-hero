"""从 trace 目录 / 图片目录挑帧，打包到数据仓库的 inbox/（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.1）。

流程：collect 读帧（页面、平台、去重、去掉横屏和全黑帧）→ select 按「平台 × 页面」分层抽样、按文字类型补足 → write_inbox 拷图 + meta.json。
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tools.ocr_dataset import schema

import cv2
import numpy as np

N_CELL = 3  # 每个「平台 × 页面」格最多几张
N_CELL_DMM540 = 5  # DMM 540 的格放宽
K_TYPE = 30  # 每种文字类型至少几个框
BLANK_STD = 4.0  # 灰度标准差低于这个 = 全黑 / 纯色加载帧
IMAGE_EXT = (".jpg", ".jpeg", ".png")
SKIP_PAGES = {"loading"}  # 加载中的帧不收（规格 §7），页面名不分大小写


@dataclass
class Candidate:
    path: Path | None
    run: str
    seq: int
    platform: str
    page: str | None
    sha: str
    run_meta: dict = field(default_factory=dict)  # run.json 里这一段的 platform（没有为空）
    native_path: Path | None = None  # HIRO_TRACE_NATIVE=1 存下的原生 PNG
    captured: str | None = None  # YYYY-MM-DD


# ---------- 平台 ----------


def guess_platform(run_name: str, run_json: dict | None) -> str:
    """平台标签：run.json 的 platform 优先（DMM 按原生宽度细分），没有就按运行名猜；老 DMM 一律 dmm（尺寸未知）。"""
    plat = (run_json or {}).get("platform") or {}
    device = (plat.get("device") or "").lower()
    if device:
        if device == "dmm":
            w = (plat.get("native") or [None])[0]
            return {540: "dmm540", 720: "dmm720", 1080: "dmm1080"}.get(w, "dmm")
        if device in ("maatools", "auto", "playcover", "mac"):
            return "mac"
        if device == "mumu":
            return "mumu"
        if device in ("adb", "leidian"):
            return "adb"
        return "other"
    name = run_name.lower()
    if "mumu" in name:  # 先认 mumu / mac：「mumu-windows-…」「hif-mac-win2」不是 DMM
        return "mumu"
    if "mac" in name:
        return "mac"
    if "dmm" in name or re.search(r"(^|[-_])win", name):
        return "dmm"
    return "other"


def _segment_platform(run_json: dict) -> dict:
    """续写过的 trace 以第一段为准（老帧占多数）；只有 segments 里有 platform 时取最后一个有的。"""
    if isinstance(run_json.get("platform"), dict):
        return run_json
    for seg in reversed(run_json.get("segments") or []):
        if isinstance(seg.get("platform"), dict):
            return seg
    return run_json


# ---------- 读帧 ----------


def _thumb(img) -> np.ndarray:
    from hiro.loop.repeat_guard import _thumb as thumb

    return thumb(img)


def _screen_diff() -> float:
    from hiro.loop.repeat_guard import SCREEN_DIFF

    return SCREEN_DIFF


def _usable(img) -> bool:
    """不收横屏（LIVE 演出）、全黑 / 纯色加载帧。"""
    if img is None or img.ndim != 3:
        return False
    h, w = img.shape[:2]
    if w > h:
        return False
    return float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).std()) >= BLANK_STD


def _default_classify(img) -> str | None:
    from hiro.vision.classify import classify

    hit = classify(img)
    return hit.name if hit and hit.name != "unknown" else None


def _date(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else None


def _trace_frames(d: Path) -> tuple[str, dict, list[tuple[int, Path, str | None]]]:
    run_json = {}
    if (d / "run.json").is_file():
        try:
            run_json = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except ValueError:
            run_json = {}
    pages: dict[str, str] = {}
    if (d / "steps.jsonl").is_file():
        for line in (d / "steps.jsonl").open(encoding="utf-8", errors="replace"):
            try:
                obs = json.loads(line).get("observation") or {}
            except ValueError:
                continue
            shot, page = obs.get("screenshot"), (obs.get("page") or {}).get("name")
            if shot and page:
                pages[shot] = page
    frames = []
    for p in sorted((d / "frames").glob("*.jpg")):
        if p.stem.isdigit():
            frames.append((int(p.stem), p, pages.get(f"frames/{p.name}")))
    return d.name, run_json, frames


def _image_frames(d: Path) -> list[tuple[str, int, Path]]:
    out = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in IMAGE_EXT or p.name.endswith(".native.png"):
            continue
        m = re.match(r"^(.*)-(\d+)$", p.stem)
        out.append((m.group(1), int(m.group(2)), p) if m else (p.stem, 0, p))
    return out


def collect(
    paths: Iterable[str | Path], platform: str | None = None, classify: Callable | None = None
) -> list[Candidate]:
    """读一批 trace 目录（有 frames/）或图片目录；同一运行里和上一张留下的帧差小于 SCREEN_DIFF 的去掉，跨运行按内容哈希去重。"""
    classify = classify or _default_classify
    diff = _screen_diff()
    out: list[Candidate] = []
    seen: set[str] = set()

    def add(run: str, seq: int, path: Path, page, run_meta: dict, plat: str, captured, last: list):
        img = cv2.imread(str(path))
        if not _usable(img):
            return
        th = _thumb(img)
        if last and last[0] is not None and float(np.abs(th - last[0]).mean()) < diff:
            return
        sha = schema.sha256(path)
        if sha in seen:
            return
        if page is None:
            page = classify(img)
        if page and page.lower() in SKIP_PAGES:
            return  # 被页面过滤掉的帧不进去重状态：否则紧跟着的相似有效帧会被当成它的重复丢掉
        last[:] = [th]
        seen.add(sha)
        native = path.with_name(f"{path.stem}.native.png")
        out.append(Candidate(path, run, seq, plat, page, sha, run_meta, native if native.is_file() else None, captured))

    for raw in paths:
        d = Path(raw)
        if (d / "frames").is_dir():
            run, run_json, frames = _trace_frames(d)
            seg = _segment_platform(run_json)
            meta = seg.get("platform") if isinstance(seg.get("platform"), dict) else {}
            # run.json 记了设备就以它为准，--platform 只给没记的老 trace 用
            plat = guess_platform(run, seg) if meta.get("device") else platform or guess_platform(run, seg)
            captured = _date(run_json.get("started"))
            last: list = []
            for seq, path, page in frames:
                add(run, seq, path, page, meta, plat, captured, last)
        elif d.is_dir():
            last_by_run: dict[str, list] = {}
            for run, seq, path in _image_frames(d):
                plat = platform or guess_platform(run, {})
                add(run, seq, path, None, {}, plat, _date(path.stat().st_mtime), last_by_run.setdefault(run, []))
    return out


# ---------- 抽样 ----------


def _spread(items: list[Candidate]) -> list[Candidate]:
    """一格里的取帧顺序：各运行轮流，每个运行内按时间均匀散开（先中间，再四分点……）。"""
    by_run: dict[str, list[Candidate]] = {}
    for c in sorted(items, key=lambda c: (c.run, c.seq)):
        by_run.setdefault(c.run, []).append(c)
    orders = []
    for lst in by_run.values():
        n, taken, order = len(lst), set(), []
        denom = 2
        while len(order) < n:
            for num in range(1, denom, 2):
                i = min(n - 1, int(num * n / denom))
                if i not in taken:
                    taken.add(i)
                    order.append(lst[i])
            denom *= 2
            if denom > 4 * n:
                order += [c for i, c in enumerate(lst) if i not in taken]
                break
        orders.append(order)
    out = []
    for i in range(max((len(o) for o in orders), default=0)):
        out += [o[i] for o in orders if i < len(o)]
    return out


def select(
    cands: list[Candidate],
    n: int = N_CELL,
    n_dmm540: int = N_CELL_DMM540,
    k: int = K_TYPE,
    counter: Callable[[Candidate], dict[str, int]] | None = None,
    known: set[str] | None = None,
) -> list[Candidate]:
    """按「平台 × 页面」分格，每格最多 n 张（dmm540 n_dmm540）；给了 counter（帧 → 各文字类型框数）时，
    不够 k 个框的类型从剩下的帧里补。known 是数据仓库里已有帧的哈希，不再收。结果按输入顺序。"""
    known = known or set()
    pool = [c for c in cands if c.sha not in known]
    cells: dict[tuple, list[Candidate]] = {}
    for c in pool:
        cells.setdefault((c.platform, c.page), []).append(c)
    chosen: set[int] = set()
    for (plat, _), items in cells.items():
        cap = n_dmm540 if plat == "dmm540" else n
        chosen |= {id(c) for c in _spread(items)[:cap]}
    if counter is not None:
        cache: dict[int, dict[str, int]] = {}

        def cnt(c):
            if id(c) not in cache:
                cache[id(c)] = counter(c) or {}
            return cache[id(c)]

        total: dict[str, int] = {}
        for c in pool:
            if id(c) in chosen:
                for t, v in cnt(c).items():
                    total[t] = total.get(t, 0) + v
        types = set(schema.TYPES) | set(total)
        rest = [c for c in pool if id(c) not in chosen]
        while True:
            lack = {t: k - total.get(t, 0) for t in types if total.get(t, 0) < k}
            if not lack or not rest:
                break
            best, gain = None, 0
            for c in rest:
                g = sum(min(v, lack[t]) for t, v in cnt(c).items() if t in lack)
                if g > gain:
                    best, gain = c, g
            if best is None:
                break
            rest.remove(best)
            chosen.add(id(best))
            for t, v in cnt(best).items():
                total[t] = total.get(t, 0) + v
    return [c for c in pool if id(c) in chosen]


# ---------- 输出 ----------


def known_hashes(dataset: str | Path) -> set[str]:
    """数据仓库里已经收过的帧（core / hard 的标注 + inbox 的 meta.json）。"""
    root, out = Path(dataset), set()
    for s in schema.SETS:
        for j in (root / s).glob("*.json") if (root / s).is_dir() else []:
            with contextlib.suppress(ValueError):
                out.add(json.loads(j.read_text(encoding="utf-8")).get("sha256"))
    for m in (root / "inbox").rglob("meta.json") if (root / "inbox").is_dir() else []:
        with contextlib.suppress(ValueError):
            out |= {f.get("sha256") for f in json.loads(m.read_text(encoding="utf-8")).get("frames", [])}
    out.discard(None)
    return out


def _known_ids(dataset: str | Path, today_meta: Path) -> set[str]:
    """数据仓库里已经有的帧 id：各分区（含 legacy-*）的标注文件名，和别的日子 / 别人的 inbox meta.json。今天这份 meta 不算（同天追加照旧）。"""
    root, out = Path(dataset), set()
    for d in root.iterdir() if root.is_dir() else []:
        if d.is_dir() and (d.name in schema.SETS or d.name.startswith("legacy-")):
            out |= {j.stem for j in d.glob("*.json")}
    for m in (root / "inbox").rglob("meta.json") if (root / "inbox").is_dir() else []:
        if m.resolve() == today_meta.resolve():
            continue
        with contextlib.suppress(ValueError, OSError):
            out |= {f.get("id") for f in json.loads(m.read_text(encoding="utf-8")).get("frames", [])}
    out.discard(None)
    return out


def _note(notes: dict[str, str], c: Candidate) -> str | None:
    return notes.get(f"{c.run}:{c.seq}") or notes.get(str(c.seq))


def write_inbox(
    sel: list[Candidate],
    dataset: str | Path,
    contributor: str,
    notes: dict[str, str],
    date: str | None = None,
    skipped: list | None = None,
) -> Path:
    """图片原样拷到 inbox/<贡献者>/<日期>/<frame_id>.<扩展名>（有原生 PNG 一起拷），写 meta.json；同一天再挑一次会追加。
    notes 的键是 `<运行名>:<seq>` 或 `<seq>`。
    同一个帧 id 在数据仓库别处（别的日子的 inbox、core / hard、legacy-*）已经有了就跳过，记进 skipped（(id, 原因)）：
    标注流程按「core/<id>.json 在不在」判断一帧标没标完，同 id 不同内容的新帧会被当成已标注、悄悄从 meta.json 里删掉。"""
    out = Path(dataset) / "inbox" / contributor / (date or time.strftime("%Y-%m-%d"))
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    meta = {"schema": schema.SCHEMA_VERSION, "contributor": contributor, "created": out.name, "frames": []}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    have = {f["id"] for f in meta["frames"]}
    elsewhere = _known_ids(dataset, meta_path)
    for c in sel:
        fid = schema.frame_id(c.platform, c.run, c.seq)
        if fid in have or c.path is None:
            continue
        if fid in elsewhere:
            if skipped is not None:
                skipped.append((fid, "数据仓库里已有同 id 的帧（别的日子的 inbox 或已标注）"))
            continue
        image = fid + c.path.suffix.lower()
        shutil.copyfile(c.path, out / image)
        h, w = cv2.imread(str(out / image)).shape[:2]
        native = c.run_meta.get("native")
        frame = {
            "id": fid,
            "image": image,
            "sha256": schema.sha256(out / image),
            "source": {"run": c.run, "seq": c.seq, "contributor": contributor, "captured": c.captured},
            "platform": {
                "kind": c.platform,
                "native": native,
                "stored": [w, h],
                "device": c.run_meta.get("screenshot") or c.run_meta.get("device"),
                "ocr": c.run_meta.get("ocr"),
            },
            "page": c.page,
            "note": _note(notes, c),
        }
        if c.native_path is not None:
            frame["native_image"] = fid + ".native.png"
            shutil.copyfile(c.native_path, out / frame["native_image"])
            if native is None:
                nh, nw = cv2.imread(str(c.native_path)).shape[:2]
                frame["platform"]["native"] = [nw, nh]
        meta["frames"].append(frame)
        have.add(fid)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def summary(sel: list[Candidate]) -> str:
    """按平台 × 页面数一下，打印给投稿人看。"""
    cells: dict[tuple, int] = {}
    for c in sel:
        cells[(c.platform, c.page)] = cells.get((c.platform, c.page), 0) + 1
    by_plat: dict[str, int] = {}
    for (p, _), v in cells.items():
        by_plat[p] = by_plat.get(p, 0) + v
    lines = [f"共 {len(sel)} 帧、{len(cells)} 格：" + "，".join(f"{p} {v}" for p, v in sorted(by_plat.items()))]
    lines += [
        f"  {p:8} {pg or '(未判页)':28} {v}"
        for (p, pg), v in sorted(cells.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))
    ]
    return "\n".join(lines)
