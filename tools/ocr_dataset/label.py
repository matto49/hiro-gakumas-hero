"""AI 标注：出框、三方答案、定正确文字、打标签（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.2 / §6.3）。

出框：Apple Vision 和 RapidOCR 各做一次整帧检测（放大 SCREEN_SCALE），重叠的框合成一组取并集。
三方答案：vision（裁图放大 3 倍识别）、llm（每帧一次，把各框裁图编号拼成一张图让 LLM 抄写）、lexicon（前两方在游戏词表里吸附）。
定正确文字见 decide；rapidocr 的读法只记录，不参与。只能在 Mac 上跑（要 Vision）。
"""

from __future__ import annotations

import base64
import html
import json
import random
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.ocr_dataset import fields, schema
from tools.ocr_dataset.lexicon import Lexicon, norm

import cv2
import numpy as np

OVERLAP = 0.5  # 两个检测框交集 / 较小框面积 ≥ 这个 = 同一段字
PAD = 4  # 裁图四周多留几像素
LANGS = ("ja-JP", "en-US")
BODY_MIN_CHARS = 10  # 兜底：这么长的一段按说明文 / 效果文算
NUMBER_RE = re.compile(r"^[0-9０-９,，.．+＋\-−ー%％/／×xX:：~〜()（）\[\]PpTt\s]+$")

# ---------- 出框 ----------


def _area(b) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _overlap(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min(_area(a), _area(b))
    return ix * iy / small if small else 0.0


def _reading_key(box) -> tuple:
    return ((box[1] + box[3]) // 2 // 12, (box[0] + box[2]) // 2)


def merge_boxes(
    vision: list[tuple[str, tuple]], rapid: list[tuple[str, tuple]], overlap: float = OVERLAP
) -> list[dict]:
    """两路检测结果（文字, (x1, y1, x2, y2)）合并：重叠的连成一组（可以连锁：一条长行盖住两个短框），取并集框。
    返回 [{box, vision, rapidocr}]（各方这一组里的文字按阅读顺序拼接），自上而下排序。"""
    items = [("vision", t, tuple(int(v) for v in b)) for t, b in vision] + [
        ("rapidocr", t, tuple(int(v) for v in b)) for t, b in rapid
    ]
    items = [it for it in items if _area(it[2]) > 0]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _overlap(items[i][2], items[j][2]) >= overlap:
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    out = []
    for members in groups.values():
        box = [
            min(m[2][0] for m in members),
            min(m[2][1] for m in members),
            max(m[2][2] for m in members),
            max(m[2][3] for m in members),
        ]
        g = {"box": box}
        for src in ("vision", "rapidocr"):
            g[src] = "".join(
                t for _, t, b in sorted((m for m in members if m[0] == src), key=lambda m: _reading_key(m[2]))
            )
        out.append(g)
    return sorted(out, key=lambda g: _reading_key(g["box"]))


# ---------- 定正确文字 ----------


EDGE = "→←↑↓▶◀►◄▷◁)(（）[]【】|｜★・•"  # 读者常把旁边的图标读成这些；比较时只去两端的（横线、+、% 有意义，不去）


def _edge(s: str) -> str:
    return s.strip(EDGE)


def _visions(answers: dict) -> list[str]:
    """Vision 的两次读法（裁图、整帧检测），归一、去空、去重，裁图在前。"""
    out = []
    for k in ("vision", "vision_frame"):
        v = norm(answers.get(k) or "")
        if v and v not in out:
            out.append(v)
    return out


def _same(a: str, b: str) -> bool:
    return a == b or (bool(_edge(a)) and _edge(a) == _edge(b))


def _clean(a: str, b: str) -> str:
    """两个算一致的读法里取不带多余图标符号的那个。"""
    if a == b:
        return a
    return a if a == _edge(a) else b


def decide(answers: dict, lex: Lexicon) -> tuple[str, str]:
    """(正确文字, status)。独立的读者：Vision（裁图 / 整帧两次读法算同一个读者）、LLM，以及可选的第二个 LLM（llm2，
    只问前两方不一致的框，和第一个不同源）。「一致」= 归一后相同，或去掉两端图标符号（EDGE）后相同。
    - 都没读到字 → ignored；
    - Vision 与 LLM 一致 → agreed（规格：词表里没有这个词也不拦）；
    - 不一致时：其中一方正好是词表词、另一方吸附后也落到同一个词 → agreed；
    - 有 llm2 时：三家里任意两家一致 → agreed；
    - LLM（和 llm2）明确说没字、Vision 两次读法又不一致 → ignored（图标被读成了乱码）；LLM 漏答不算说没字；
    - 两个 LLM 一致但都少了装饰「+」、Vision 两次都读到「+」 → 取 Vision 的；
    - 其余 disputed（文字先填最可能的：Vision 吸附到的词 > LLM 吸附到的词 > Vision > LLM）。"""
    raw_l, raw_l2 = answers.get("llm"), answers.get("llm2", "")  # llm2 没问过 = 没意见；值为 None = 漏答
    vs, la, lb = _visions(answers), norm(raw_l or ""), norm(raw_l2 or "")
    said_empty = raw_l == "" and raw_l2 == ""  # LLM 明确说这段没字（漏答不算）
    if not vs and not la and not lb:
        return "", "ignored" if said_empty else "disputed"
    if la:
        for v in vs:
            if _same(v, la):
                return _clean(la, v) if v != la else la, "agreed"
    sl = lex.snap(la)
    for v in vs:
        sv = lex.snap(v)
        if la and sv is not None and sv == sl and sv in (v, la):
            return sv, "agreed"
    if lb:
        for v in vs:
            if _same(v, lb):
                return _clean(lb, v), "agreed"
        if la and _same(la, lb):
            t = _clean(la, lb)
            crop, frame = norm(answers.get("vision") or ""), norm(answers.get("vision_frame") or "")
            if crop and crop == frame and crop == "+" + t:  # 两个 LLM 都没写出装饰「+」，Vision 两次都读到了
                return crop, "agreed"
            return t, "agreed"
    crop, frame = norm(answers.get("vision") or ""), norm(answers.get("vision_frame") or "")
    if said_empty and not (crop and crop == frame):
        return "", "ignored"
    v0 = vs[0] if vs else ""
    return lex.snap(v0) or sl or v0 or la, "disputed"


def lexicon_answer(answers: dict, lex: Lexicon) -> str:
    for k in ("vision", "vision_frame", "llm", "llm2"):
        if s := lex.snap(answers.get(k)):
            return s
    return ""


def adjudicate(
    dataset: str | Path, reader, lex: Lexicon | None = None, workers: int = 1, log: Callable[[str], None] = print
) -> dict[str, int]:
    """第二个 LLM 读一遍还待定的框（每帧一次，只拼待定框），答案存进 answers.llm2，再按 decide 重判。问过的框不再问（漏答写成 null 的算没问过）。"""
    root = Path(dataset)
    lex = lex or _load_lexicon(root)
    jobs = []
    for s in schema.SETS:
        for js in sorted((root / s).glob("*.json")) if (root / s).is_dir() else []:
            fr = json.loads(js.read_text(encoding="utf-8"))
            todo = [
                b
                for b in fr.get("boxes", [])
                if b.get("status") == "disputed"
                and (b.get("answers") or {}).get("llm2") is None
                and not b.get("reviewed")  # null = 漏答，要再问；"" 是明确没字
            ]
            if todo:
                jobs.append((s, js, fr, todo))

    def one(job):
        s, _js, fr, todo = job
        img = cv2.imread(str(root / s / fr["image"]))
        return reader.read_llm(img, [b["box"] for b in todo])

    changed = asked = 0
    pool = ThreadPoolExecutor(max(1, workers))
    try:
        for k, (job, fut) in enumerate([(j, pool.submit(one, j)) for j in jobs], 1):
            s, js, fr, todo = job
            try:
                texts = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"[{k}/{len(jobs)}] {fr['id']} 第二个 LLM 失败：{type(e).__name__}: {e}")
                continue
            asked += 1
            for b, t in zip(todo, texts):
                b["answers"]["llm2"] = t
                text, status = decide(b["answers"], lex)
                if status != b["status"]:
                    changed += 1
                b["text"], b["status"], b["ignore"] = text, status, status == "ignored"
            js.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
            log(
                f"[{k}/{len(jobs)}] {fr['id']}：{len(todo)} 个待定框，还剩 {sum(b['status'] == 'disputed' for b in todo)}"
            )
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return {"frames": asked, "changed": changed, "disputed": len(rebuild_disputed(root))}


def reask(dataset: str | Path, reader, lex: Lexicon | None = None, multi_sheet_only: bool = True, pad: int = PAD,
          workers: int = 1, log: Callable[[str], None] = print) -> dict[str, int]:  # fmt: skip
    """重新问一遍 LLM（每帧一次）：换掉 answers.llm、清掉 llm2（之后要重新 adjudicate），再按 decide 重判；人看过的框不动。
    multi_sheet_only：只重问拼图超过一张的帧（修编号错位 bug 之前标的帧，第二张起的答案不可信）。"""
    root = Path(dataset)
    lex = lex or _load_lexicon(root)
    jobs = []
    for s in schema.SETS:
        for js in sorted((root / s).glob("*.json")) if (root / s).is_dir() else []:
            fr = json.loads(js.read_text(encoding="utf-8"))
            if not fr.get("boxes"):
                continue
            if multi_sheet_only:
                img = cv2.imread(str(root / s / fr["image"]))  # 只为数拼图张数，数完就放掉；像素数组不留在任务列表里
                if len(crop_sheets(img, [b["box"] for b in fr["boxes"]], pad)) <= 1:
                    continue
                del img
            jobs.append((js, fr, root / s / fr["image"]))

    def ask(job):
        _js, fr, path = job
        return reader.read_llm(
            cv2.imread(str(path)), [b["box"] for b in fr["boxes"]]
        )  # 用到时才读，同时只有 workers 张在内存里

    pool = ThreadPoolExecutor(max(1, workers))
    asked = changed = 0
    try:
        futs = [(j, pool.submit(ask, j)) for j in jobs]
        for k, ((js, fr, _), fut) in enumerate(futs, 1):
            try:
                texts = fut.result()
            except Exception as e:  # noqa: BLE001
                log(f"[{k}/{len(jobs)}] {fr['id']} 重问失败：{type(e).__name__}: {e}")
                continue
            asked += 1
            for b, t in zip(fr["boxes"], texts):
                if b.get("reviewed") or b.get("status") == "resolved":
                    continue
                b["answers"]["llm"] = t
                b["answers"].pop("llm2", None)
                text, status = decide(b["answers"], lex)
                changed += status != b["status"]
                b["text"], b["status"], b["ignore"] = text, status, status == "ignored"
            js.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"[{k}/{len(jobs)}] {fr['id']}：重问 {len(texts)} 框")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return {"frames": asked, "changed": changed, "disputed": len(rebuild_disputed(root))}


def redecide(dataset: str | Path, lex: Lexicon | None = None) -> dict[str, int]:
    """规则改了以后，用帧里存的各方答案重新定 status / 文字（不重新识别、不问 LLM）；人看过的框（有 reviewed）不动。"""
    root = Path(dataset)
    lex = lex or _load_lexicon(root)
    changed = 0
    for s in schema.SETS:
        for js in sorted((root / s).glob("*.json")) if (root / s).is_dir() else []:
            fr = json.loads(js.read_text(encoding="utf-8"))
            dirty = False
            for b in fr.get("boxes", []):
                if b.get("reviewed") or b.get("status") == "resolved":
                    continue
                text, status = decide(b.get("answers") or {}, lex)
                if (text, status) != (b.get("text"), b.get("status")):
                    b["text"], b["status"], b["ignore"] = text, status, status == "ignored"
                    changed += 1
                    dirty = True
                tags = b.get("tags") or {}
                if tags.get("field") is None and tags.get("type") in ("other", "body") and norm(text) in lex.names:
                    b["tags"] = {**tags, "type": "name"}  # 主数据名字兜底（先标的帧没有这条规则）
                    dirty = True
            if dirty:
                js.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"changed": changed, "disputed": len(rebuild_disputed(root))}


# ---------- 标签 ----------


def header_bands(img) -> list[tuple[int, int]]:
    """弹窗标题条（橙黄底白字）的 y 范围；识别层不可用时为空。"""
    try:
        from hiro.vision import pages

        return [(y1, y2) for y1, y2 in pages._header_bands(img) if pages._title_white(img, y1, y2)]
    except Exception:  # noqa: BLE001
        return []


def type_of(text: str, box, page: str | None, bands=(), names=()) -> dict:
    """{field, type}：先查字段映射表；对不上（或表里类型留空）走兜底：只有数字 → number，在标题条里 → title，
    是主数据里的名字 → name，≥ BODY_MIN_CHARS 字 → body，其余 other。"""
    field_name, typ = fields.lookup(page, box)
    if typ is None:
        t = norm(text)
        cy = (box[1] + box[3]) / 2
        if t and NUMBER_RE.match(t) and re.search(r"[0-9]", t):
            typ = "number"
        elif any(y1 - 4 <= cy <= y2 + 4 for y1, y2 in bands):
            typ = "title"
        elif t in names:
            typ = "name"
        elif len(t) >= BODY_MIN_CHARS:
            typ = "body"
        else:
            typ = "other"
    return {"field": field_name, "type": typ}


# ---------- 读者 ----------


def _crop(img, box, pad: int):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    return img[max(0, y1 - pad) : min(h, y2 + pad), max(0, x1 - pad) : min(w, x2 + pad)]


LLM_SYSTEM = (
    "你是抄写员。图里每一行左边是编号 [n]，右边是从游戏截图（学園アイドルマスター，日文界面）里裁下来的一段文字。"
    "逐个抄写每段文字：只抄看到的字，不纠正、不补全、不翻译、不加解释；保留原有的符号（＋、×、％、♪、「」等）；"
    "看不清的单个字写「？」；一段里没有文字写空字符串；多行文字按阅读顺序连起来，不加换行。"
    '只回复 JSON：{"texts": {"1": "…", "2": "…"}}，每个编号都要有。'
)
SHEET_ROW_H = 56  # 拼图时每段裁图缩放到的高度
SHEET_MAX_W = 1400
SHEET_MAX_H = 1500  # 超过就分几张图问（Claude 长边超过约 1568 会被缩小）


def crop_sheets(img, boxes, pad: int = PAD) -> list[tuple[list[int], np.ndarray]]:
    """把各框裁图拼成竖排的图：[(这张图里的框序号, 图)]；每张图里的编号从 [1] 开始。"""
    label_w = 80
    rows = []
    for i, box in enumerate(boxes):
        c = _crop(img, box, pad)
        if c.size == 0:
            c = np.full((8, 8, 3), 255, np.uint8)
        f = min(4.0, max(1.0, SHEET_ROW_H / c.shape[0]))
        f = min(f, (SHEET_MAX_W - label_w) / c.shape[1])
        c = cv2.resize(c, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        row = np.full((c.shape[0] + 12, label_w + c.shape[1] + 8, 3), 255, np.uint8)
        row[6 : 6 + c.shape[0], label_w : label_w + c.shape[1]] = c
        cv2.line(row, (0, row.shape[0] - 1), (row.shape[1], row.shape[0] - 1), (180, 180, 180), 1)
        rows.append((i, row))
    sheets, cur, cur_h = [], [], 0
    for i, row in rows:
        if cur and cur_h + row.shape[0] > SHEET_MAX_H:
            sheets.append(cur)
            cur, cur_h = [], 0
        cur.append((i, row))
        cur_h += row.shape[0]
    if cur:
        sheets.append(cur)
    out = []
    for sheet in sheets:
        w = max(r.shape[1] for _, r in sheet)
        canvas = np.full((sum(r.shape[0] for _, r in sheet), w, 3), 255, np.uint8)
        y = 0
        for k, (_, r) in enumerate(sheet, 1):  # 每张图从 [1] 编号，和提示词、解析一致
            canvas[y : y + r.shape[0], : r.shape[1]] = r
            cv2.putText(
                canvas, f"[{k}]", (4, y + min(r.shape[0] - 6, 34)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 200), 2
            )
            y += r.shape[0]
        out.append(([i for i, _ in sheet], canvas))
    return out


class LLMReadError(RuntimeError):
    pass


class RealReaders:
    """实机读者：Vision / RapidOCR 走 hiro.vision.ocr 的底层调用（同一个进程里两个后端都用，不经 HIRO_OCR），LLM 走 hiro.brain.llm。"""

    def __init__(self, llm_spec: str | None = None, timeout: float = 180, pad: int = PAD):
        from hiro.brain import llm

        self.llm = (
            llm.from_spec(llm_spec, timeout=timeout)
            if (llm_spec or "").split(":")[0] in ("", "claude", "claude-cli", "codex", "codex-cli")
            else llm.from_spec(llm_spec)
        )
        self.pad = pad

    @property
    def name(self) -> str:
        return f"{self.llm.name}:{self.llm.model}"

    @staticmethod
    def _scaled(img):
        from hiro.vision.ocr import SCREEN_SCALE

        return cv2.resize(img, None, fx=SCREEN_SCALE, fy=SCREEN_SCALE, interpolation=cv2.INTER_CUBIC), SCREEN_SCALE

    def detect_vision(self, img):
        from hiro.vision.ocr import _vision_raw

        h, w = img.shape[:2]
        big, _ = self._scaled(img)
        return [(t, b) for t, _, b in _vision_raw(big, w, h, LANGS)]

    def detect_rapid(self, img):
        from hiro.vision.ocr import _rapid_raw

        big, s = self._scaled(img)
        return [(t, b) for t, _, b in _rapid_raw(big, s, LANGS)]

    def read_vision(self, crop) -> str:
        from hiro.vision.ocr import _vision_raw

        if crop.size == 0:
            return ""
        h, w = crop.shape[:2]
        big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return "".join(t for t, _, b in sorted(_vision_raw(big, w, h, LANGS), key=lambda r: _reading_key(r[2])))

    def read_llm(self, img, boxes) -> list[str | None]:
        """每框的抄写；LLM 漏答的编号记 None（和「说这段没字」的空串区分开）。"""
        out: list[str | None] = [None] * len(boxes)
        for idx, sheet in crop_sheets(img, boxes, self.pad):
            _ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
            user = f"共 {len(idx)} 段，编号 1–{len(idx)}。"
            res = self.llm.ask_json_image(LLM_SYSTEM, user, buf.tobytes())
            texts = (res.data or {}).get("texts") if not res.error else None
            if not isinstance(texts, dict):
                raise LLMReadError(f"LLM 没给出 texts：{res.error or res.raw[:200]}")
            for k, i in enumerate(idx, 1):
                v = texts.get(str(k))
                out[i] = (
                    v
                    if isinstance(v, str)
                    else str(v)
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                    else None
                )
        return out


# ---------- 一帧 ----------


def label_frame(img, meta: dict, readers, lex: Lexicon, pad: int = PAD, bands=None) -> dict:
    """inbox 里一帧的 meta → 数据仓库的帧 JSON（框、三方答案、status、标签）。"""
    h, w = img.shape[:2]
    groups = merge_boxes(readers.detect_vision(img), readers.detect_rapid(img))
    for g in groups:
        b = g["box"]
        g["box"] = [max(0, b[0]), max(0, b[1]), min(w, b[2]), min(h, b[3])]
    groups = [g for g in groups if g["box"][0] < g["box"][2] and g["box"][1] < g["box"][3]]
    llm = readers.read_llm(img, [g["box"] for g in groups]) if groups else []
    page = meta.get("page") or "unknown"
    if bands is None:
        bands = header_bands(img)
    boxes = []
    for i, g in enumerate(groups):
        answers = {
            "vision": readers.read_vision(_crop(img, g["box"], pad)),
            "vision_frame": g["vision"],
            "llm": llm[i],
            "rapidocr": g["rapidocr"],
        }
        answers["lexicon"] = lexicon_answer(answers, lex)
        text, status = decide(answers, lex)
        boxes.append({"id": i, "box": g["box"], "text": text, "status": status, "answers": answers,
                      "tags": type_of(text or g["vision"] or g["rapidocr"], g["box"], page, bands, lex.names), "ignore": status == "ignored"})  # fmt: skip
    plat = dict(meta.get("platform") or {})
    plat.setdefault("stored", [w, h])
    fr = {
        "schema": schema.SCHEMA_VERSION,
        "id": meta["id"],
        "image": meta["image"],
        "sha256": meta.get("sha256"),
        "source": meta.get("source") or {},
        "platform": plat,
        "page": page,
        "boxes": boxes,
        "labeled": {"date": time.strftime("%Y-%m-%d"), "lexicon": lex.version, "llm": getattr(readers, "name", None)},
    }
    if meta.get("note"):
        fr["note"] = meta["note"]
    if meta.get("native_image"):
        fr["native_image"] = meta["native_image"]
    return fr


# ---------- 整批 ----------


def _load_lexicon(dataset: Path) -> Lexicon:
    from tools.ocr_dataset import lexicon

    d = dataset / "lexicon"
    return lexicon.load(d) if (d / "words.txt").is_file() else lexicon.build()


def rebuild_disputed(dataset: str | Path) -> list[dict]:
    """从 core / hard 的帧重新生成 disputed.json（待定框清单）。"""
    root = Path(dataset)
    out = []
    for s in schema.SETS:
        for js in sorted((root / s).glob("*.json")) if (root / s).is_dir() else []:
            fr = json.loads(js.read_text(encoding="utf-8"))
            for b in fr.get("boxes", []):
                if b.get("status") == "disputed":
                    out.append(
                        {
                            "frame": fr["id"],
                            "set": s,
                            "box": b["id"],
                            "bbox": b["box"],
                            "text": b.get("text"),
                            "answers": b.get("answers"),
                        }
                    )
    (root / "disputed.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def run(dataset: str | Path, inbox_dir: str | Path, readers=None, lex: Lexicon | None = None, set_: str = "core",
        pad: int = PAD, workers: int = 1, limit: int | None = None, log: Callable[[str], None] = print) -> int:  # fmt: skip
    """把一个 inbox 目录里的帧标注后移进 core/（或 hard/）；标坏的帧（LLM 出错等）留在 inbox，下次再跑。返回标完的帧数。"""
    root, inbox = Path(dataset), Path(inbox_dir)
    meta_path = inbox / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    readers = readers or RealReaders()
    lex = lex or _load_lexicon(root)
    dest = root / set_
    dest.mkdir(parents=True, exist_ok=True)
    todo = [f for f in meta["frames"] if not (dest / f"{f['id']}.json").exists()]
    if limit is not None:
        todo = todo[:limit]

    def one(f):
        img = cv2.imread(str(inbox / f["image"]))
        if img is None:
            raise RuntimeError(f"读不了图片 {f['image']}")
        return label_frame(img, f, readers, lex, pad=pad)

    done_ids: set[str] = {f["id"] for f in meta["frames"] if (dest / f"{f['id']}.json").exists()}
    n_done = 0
    pool = ThreadPoolExecutor(max(1, workers))
    try:
        futures = [(f, pool.submit(one, f)) for f in todo]
        for k, (f, fut) in enumerate(futures, 1):
            try:
                fr = fut.result()
            except Exception as e:  # noqa: BLE001  一帧失败不影响别的
                log(f"[{k}/{len(todo)}] {f['id']} 失败：{type(e).__name__}: {e}")
                continue
            # 先拷图、再原子写标注（临时文件 → 改名），最后才删 inbox 的原图：中途写盘失败 / 被打断，原图还在，下次接着标
            sources = [inbox / f["image"]]
            shutil.copyfile(sources[0], dest / f["image"])
            if f.get("native_image") and (inbox / f["native_image"]).is_file():
                sources.append(inbox / f["native_image"])
                shutil.copyfile(sources[1], dest / f["native_image"])
            fr["sha256"] = schema.sha256(dest / f["image"])
            final = dest / f"{f['id']}.json"
            tmp = final.with_name(final.name + ".tmp")
            tmp.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(final)
            for src in sources:
                src.unlink()
            done_ids.add(f["id"])
            n_done += 1
            st = [b["status"] for b in fr["boxes"]]
            log(
                f"[{k}/{len(todo)}] {f['id']}：{len(st)} 框，agreed {st.count('agreed')}，disputed {st.count('disputed')}"
            )
    except BaseException:  # Ctrl-C 等：排队的帧不再跑（否则退出前还会把剩下几百次 LLM 调用跑完再扔掉）
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    meta["frames"] = [f for f in meta["frames"] if f["id"] not in done_ids]
    if meta["frames"]:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        meta_path.unlink()
        d = inbox
        while d != root / "inbox" and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    rebuild_disputed(root)
    return n_done


# ---------- 抽查 ----------


def _png_b64(img) -> str:
    _ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode()


SPOT_JS = """
function upd(){const all=document.querySelectorAll('.item');let bad=0;all.forEach(e=>{if(e.querySelector('input[type=checkbox]').checked)bad++});
document.getElementById('acc').textContent=all.length?`错 ${bad} / ${all.length}，准确率 ${((all.length-bad)/all.length*100).toFixed(1)}%（≥ 98% 才合格）`:'没有可抽查的框'}
function save(){const out=[];document.querySelectorAll('.item').forEach(e=>{if(e.querySelector('input[type=checkbox]').checked)
{const fix=e.querySelector('input[type=text]').value.trim();const b={frame:e.dataset.frame,set:e.dataset.set,box:+e.dataset.box,was:e.dataset.text};
if(fix)b.text=fix;else b.disputed=true;out.push(b)}});
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));a.download='spot-result.json';a.click()}
document.addEventListener('change',upd);upd();
"""


def spot_check(dataset: str | Path, n: int = 50, seed: int | None = None) -> Path:
    """从 agreed 框里随机抽 n 个，生成本地核对页（.work/spot-<日期>.html）：看错的打勾，页面实时算准确率、导出错框 JSON
    （填了正确文字的写回成 resolved，没填的打回 disputed：python -m tools.ocr_dataset review --apply spot-result.json）。"""
    root = Path(dataset)
    pool = []
    for s in schema.SETS:
        for js in sorted((root / s).glob("*.json")) if (root / s).is_dir() else []:
            fr = json.loads(js.read_text(encoding="utf-8"))
            pool += [(s, fr, b) for b in fr.get("boxes", []) if b.get("status") == "agreed"]
    picked = random.Random(seed).sample(pool, min(n, len(pool)))
    imgs: dict[str, np.ndarray] = {}
    items = []
    for s, fr, b in picked:
        key = f"{s}/{fr['image']}"
        if key not in imgs:
            imgs[key] = cv2.imread(str(root / key))
        c = _crop(imgs[key], b["box"], 6)
        c = cv2.resize(c, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        ans = " · ".join(f"{html.escape(str(k))}: {html.escape(str(v))}" for k, v in (b.get("answers") or {}).items())
        items.append(
            f'<div class="item" data-frame="{html.escape(fr["id"])}" data-set="{s}" data-box="{b["id"]}" data-text="{html.escape(b["text"])}">'
            f'<img src="data:image/png;base64,{_png_b64(c)}"><div class="t">{html.escape(b["text"])}</div>'
            f'<div class="a">{html.escape(fr["id"])} #{b["id"]} · {html.escape(str(b["tags"]["type"]))} · {ans}</div>'
            f'<label><input type="checkbox"> 认错了</label> <input type="text" placeholder="正确文字（可不填）"></div>'
        )
    page = root / ".work" / f"spot-{time.strftime('%Y%m%d-%H%M%S')}.html"
    page.parent.mkdir(exist_ok=True)
    page.write_text(
        '<!doctype html><meta charset="utf-8"><title>标注抽查</title><style>'
        "body{font:14px system-ui,sans-serif;margin:24px;max-width:1100px}.item{border-bottom:1px solid #ddd;padding:12px 0}"
        "img{max-width:100%;image-rendering:auto;background:#eee}.t{font-size:22px;margin:6px 0}.a{color:#666;font-size:12px;margin-bottom:6px}"
        "#bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:2px solid #333}</style>"
        f'<div id="bar"><b>agreed 框抽查 {len(items)} 个</b> · <span id="acc">{"" if items else "没有可抽查的框"}</span> · <button onclick="save()">导出错框</button></div>'
        + "".join(items)
        + f"<script>{SPOT_JS}</script>",
        encoding="utf-8",
    )
    return page


# ---------- 给 pick 补类型用 ----------


def type_counter(readers) -> Callable:
    """pick.select(counter=…) 用：只做两路检测（不读字、不问 LLM），按检测文字估计各类型框数。"""

    def count(c) -> dict[str, int]:
        img = getattr(c, "image", None)
        if img is None:
            img = cv2.imread(str(c.path))
        out: dict[str, int] = {}
        for g in merge_boxes(readers.detect_vision(img), readers.detect_rapid(img)):
            t = type_of(g["vision"] or g["rapidocr"], g["box"], c.page)["type"]
            out[t] = out.get(t, 0) + 1
        return out

    return count
