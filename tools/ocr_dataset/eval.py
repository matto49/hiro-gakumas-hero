"""OCR 评测（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.5）。

后端走 hiro.vision.ocr 的整帧接口 screen_texts（和实机同一条路），一个后端 = 一个 HIRO_OCR 取值；另有实验后端 rapidocr+charset。
框对齐：识别框和标注框 IoU ≥ 0.5 或识别框中心落在标注框里就配对（一个标注框可以收几段，按阅读顺序拼起来）；
标注框没配上 = 整段漏读；哪个标注框都没配上的识别框算多读。只给 agreed / resolved 的框计分；
读到 disputed / ignored 框上的字既不计分也不算多读。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tools.ocr_dataset import schema
from tools.ocr_dataset.lexicon import Lexicon, levenshtein, norm

import numpy as np

IOU_MIN = 0.5
FORMAL_DISPUTED_MAX = 0.05  # 待定框占比超过这个不出正式报告
SCORED = ("agreed", "resolved")
N_ERRORS = 50
GROUP_KEYS = ("platform", "page", "type")


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _key(box) -> tuple:
    return ((box[1] + box[3]) // 2 // 12, (box[0] + box[2]) // 2)


def match(pred: list[tuple[str, tuple]], truth: list[dict]) -> tuple[dict[int, str | None], int]:
    """→ ({标注框 id: 读到的文字，没配上为 None}, 多读框数)。每个识别框只归一个标注框：中心落在里面的优先，其次 IoU 最大。"""
    got: dict[int, list] = {b["id"]: [] for b in truth}
    extra = 0
    for text, box in pred:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        best, best_score = None, 0.0
        for b in truth:
            x1, y1, x2, y2 = b["box"]
            inside = x1 <= cx <= x2 and y1 <= cy <= y2
            iou = _iou(box, b["box"])
            if not inside and iou < IOU_MIN:
                continue
            score = (1.0 if inside else 0.0) + iou
            if score > best_score:
                best, best_score = b["id"], score
        if best is None:
            extra += 1
        else:
            got[best].append((text, box))
    return {
        k: ("".join(t for t, _ in sorted(v, key=lambda p: _key(p[1]))) if v else None) for k, v in got.items()
    }, extra


class _Acc:
    def __init__(self):
        self.boxes = self.chars = self.edits = self.exact = self.missed = self.keywords = self.keyword_hits = (
            self.extra
        ) = 0

    def to_dict(self) -> dict:
        return {
            "boxes": self.boxes, "chars": self.chars, "edits": self.edits, "missed": self.missed, "extra": self.extra,
            "keywords": self.keywords, "keyword_hits": self.keyword_hits,
            "cer": self.edits / self.chars if self.chars else None,
            "line_exact": self.exact / self.boxes if self.boxes else None,
            "keyword_rate": self.keyword_hits / self.keywords if self.keywords else None,
            "missed_rate": self.missed / self.boxes if self.boxes else None,
        }  # fmt: skip


class _Keywords:
    """正确文字里出现的词表词（≥ 2 字），按首字建索引。"""

    def __init__(self, lex: Lexicon):
        self.by_first: dict[str, list[str]] = {}
        for w in lex.words:
            if len(w) >= 2:
                self.by_first.setdefault(w[0], []).append(w)

    def find(self, text: str) -> set[str]:
        out = set()
        for i, ch in enumerate(text):
            for w in self.by_first.get(ch, ()):
                if text.startswith(w, i):
                    out.add(w)
        return out


def _groups_of(fr: dict, box: dict) -> list[tuple[str, str]]:
    return [
        ("platform", (fr.get("platform") or {}).get("kind") or "?"),
        ("page", fr.get("page") or "?"),
        ("type", box["tags"]["type"]),
    ]


def score(frames: list[dict], preds: dict[str, dict], lex: Lexicon, backend: str = "?", set_: str = "core") -> dict:
    """frames：帧 JSON；preds：{帧 id: {"texts": [(文字, 框)], "seconds": 秒}}。→ 结果 dict（可存 JSON 当下次的 --compare 基线）。"""
    kw = _Keywords(lex)
    total = _Acc()
    groups: dict[str, dict[str, _Acc]] = {k: {} for k in GROUP_KEYS}
    errors, disputed = [], []
    n_disputed = n_counted = n_ignored = 0
    times = []
    for fr in frames:
        p = preds.get(fr["id"])
        if p is None:
            continue
        times.append(float(p.get("seconds") or 0))
        got, extra = match(p.get("texts") or [], fr["boxes"])
        total.extra += extra
        for key, val in (("platform", (fr.get("platform") or {}).get("kind") or "?"), ("page", fr.get("page") or "?")):
            groups[key].setdefault(val, _Acc()).extra += extra
        for b in fr["boxes"]:
            if b.get("ignore") or b.get("status") == "ignored":
                n_ignored += 1
                continue
            n_counted += 1
            if b.get("status") == "disputed":
                n_disputed += 1
                disputed.append(
                    {"frame": fr["id"], "box": b["id"], "pred": got.get(b["id"]), "answers": b.get("answers") or {}}
                )
                continue
            if b.get("status") not in SCORED:
                continue
            gt, raw = norm(b["text"]), got.get(b["id"])
            pr = norm(raw or "")
            d = levenshtein(gt, pr)
            words = kw.find(gt)
            hits = sum(1 for w in words if w in pr)
            accs = [total] + [groups[k].setdefault(v, _Acc()) for k, v in _groups_of(fr, b)]
            for a in accs:
                a.boxes += 1
                a.chars += len(gt)
                a.edits += d
                a.exact += gt == pr
                a.missed += raw is None
                a.keywords += len(words)
                a.keyword_hits += hits
            if d:
                errors.append({"frame": fr["id"], "box": b["id"], "bbox": b["box"], "truth": b["text"], "pred": raw,
                               "edits": d, "cer": d / max(1, len(gt)), "type": b["tags"]["type"]})  # fmt: skip
    errors.sort(key=lambda e: (-e["cer"], -e["edits"]))
    warm = times[1:] if len(times) > 1 else times
    return {
        "backend": backend,
        "set": set_,
        "frames": len(times),
        "groups": {
            "all": total.to_dict(),
            **{k: {v: a.to_dict() for v, a in sorted(groups[k].items())} for k in GROUP_KEYS},
        },
        "disputed_share": n_disputed / n_counted if n_counted else 0.0,
        "ignored_share": n_ignored / (n_counted + n_ignored) if n_counted + n_ignored else 0.0,
        "disputed": disputed,
        "errors": errors[:N_ERRORS],
        "timing": {
            "p50": float(np.percentile(warm, 50)) if warm else None,
            "p90": float(np.percentile(warm, 90)) if warm else None,
        },
    }


def formal(result: dict) -> bool:
    return result.get("disputed_share", 1.0) <= FORMAL_DISPUTED_MAX


def filter_frames(frames: list[dict], tags: dict[str, str]) -> list[dict]:
    """--tags platform=dmm540,page=CONSULT：按帧的平台 / 页面筛。"""
    out = []
    for fr in frames:
        if "platform" in tags and (fr.get("platform") or {}).get("kind") != tags["platform"]:
            continue
        if "page" in tags and fr.get("page") != tags["page"]:
            continue
        out.append(fr)
    return out


# ---------- 报告 ----------


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _delta(v, base) -> str:
    if v is None or base is None:
        return ""
    d = (v - base) * 100
    return f"（{'+' if d > 0 else '−' if d < 0 else '±'}{abs(d):.1f}）"


def _base_for(compare: dict | None, backend: str) -> dict | None:
    for r in (compare or {}).get("results", []):
        if r.get("backend") == backend:
            return r
    return None


def _sec(v) -> str:
    return "—" if v is None else f"{v:.2f}s"


def report_md(results: list[dict], compare: dict | None = None, title: str = "OCR 评测") -> str:
    lines = [f"# {title}", ""]
    if results:
        r0 = results[0]
        gate = "" if all(formal(r) for r in results) else "（**非正式**：待定框占比超过 5%，先在 review 页清理）"
        lines += [f"集合 `{r0['set']}`，{r0['frames']} 帧，计分框 {r0['groups']['all']['boxes']} 个，待定占比 {_pct(r0['disputed_share'])}，"
                  f"忽略占比 {_pct(r0.get('ignored_share'))}（不计分）{gate}", "",
                  "框对齐：一段识别只归一个标注框（中心落在里面的优先），分行比标注框粗的后端会被多扣（多出的字算错、旁边的框算漏读）；"
                  "标注框来自 Vision 和 RapidOCR 的检测，这两家天然占便宜。比较分行方式不同的后端时留意。", ""]  # fmt: skip
        if compare:
            lines += [
                f"对比基线：{compare.get('title') or compare.get('created') or '上一份报告'}（括号里是百分点变化）",
                "",
            ]
    lines += [
        "## 总表",
        "",
        "| 后端 | 字错率 | 整行全对 | 关键词命中 | 漏读 | 多读框 | P50 | P90 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        a, b = r["groups"]["all"], (_base_for(compare, r["backend"]) or {}).get("groups", {}).get("all", {})
        lines.append(f"| {r['backend']} | {_pct(a['cer'])}{_delta(a['cer'], b.get('cer'))} | {_pct(a['line_exact'])}{_delta(a['line_exact'], b.get('line_exact'))}"
                     f" | {_pct(a['keyword_rate'])}{_delta(a['keyword_rate'], b.get('keyword_rate'))} | {_pct(a['missed_rate'])} | {a['extra']}"
                     f" | {_sec(r['timing']['p50'])} | {_sec(r['timing']['p90'])} |")  # fmt: skip
    names = {"platform": "按平台", "page": "按页面", "type": "按文字类型"}
    for key in GROUP_KEYS:
        vals = sorted({v for r in results for v in r["groups"][key]})
        if not vals:
            continue
        lines += ["", f"## {names[key]}（字错率）", "", "| " + key + " | 框数 | " + " | ".join(r["backend"] for r in results) + " |",
                  "|---|---|" + "---|" * len(results)]  # fmt: skip
        for v in vals:
            n = max((r["groups"][key].get(v) or {}).get("boxes", 0) for r in results)
            cells = []
            for r in results:
                g = r["groups"][key].get(v) or {}
                bg = ((_base_for(compare, r["backend"]) or {}).get("groups", {}).get(key, {}) or {}).get(v, {})
                cells.append(f"{_pct(g.get('cer'))}{_delta(g.get('cer'), bg.get('cer'))}")
            lines.append(f"| {v} | {n} | " + " | ".join(cells) + " |")
    for r in results:
        if r["errors"]:
            lines += ["", f"## 错例：{r['backend']}（字错率最高的 {min(20, len(r['errors']))} 个，完整 {len(r['errors'])} 个见 JSON）", "",
                      "| 帧 | 框 | 类型 | 正确 | 读成 |", "|---|---|---|---|---|"]  # fmt: skip
            for e in r["errors"][:20]:
                lines.append(
                    f"| {e['frame']} | {e['box']} | {e['type']} | {e['truth']} | {e['pred'] if e['pred'] is not None else '（漏读）'} |"
                )
    dis = {(d["frame"], d["box"]): d for r in results for d in r["disputed"]}
    if dis:
        lines += ["", f"## 待定框里各后端的读法（{len(dis)} 个，列前 30 个）", "",
                  "| 帧 | 框 | 标注各方 | " + " | ".join(r["backend"] for r in results) + " |", "|---|---|---|" + "---|" * len(results)]  # fmt: skip
        for (fid, bid), d in list(dis.items())[:30]:
            ans = " / ".join(f"{k}:{v}" for k, v in d["answers"].items() if v)
            reads = []
            for r in results:
                m = next((x for x in r["disputed"] if x["frame"] == fid and x["box"] == bid), None)
                reads.append(str(m["pred"]) if m and m["pred"] is not None else "—")
            lines.append(f"| {fid} | {bid} | {ans} | " + " | ".join(reads) + " |")
    return "\n".join(lines) + "\n"


# ---------- 跑后端 ----------


def predictor(backend: str, lex: Lexicon):
    """后端名 → (img → [(文字, 框)])。HIRO_OCR 的取值直接切 hiro.vision.ocr 的后端；rapidocr+charset 用字表屏蔽版。"""
    if backend == "rapidocr+charset":
        from tools.ocr_dataset import charset_rapid

        charset = set(lex.charset)
        return lambda img: charset_rapid.screen_texts_masked(img, charset)
    from hiro.vision import ocr

    if backend not in ocr.ENGINES or backend == "auto":
        raise ValueError(
            f"后端 {backend!r} 不认识；可选：{', '.join(e for e in ocr.ENGINES if e != 'auto')}, rapidocr+charset"
        )

    def run(img):
        ocr._engine = backend  # engine() 首次调用后固定；评测里逐个后端切
        return [(t.text, tuple(t.rect)) for t in ocr.screen_texts(img)]

    return run


def load_frames(dataset: Path, set_: str) -> list[dict]:
    """只要框级帧（schema 1）：场景帧（3）、字段帧（2）没有 boxes，喂给 score 会 KeyError；它们各有自己的评测。"""
    d = dataset / set_
    frames = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
    return [fr for fr in frames if isinstance(fr, dict) and fr.get("schema") == schema.SCHEMA_VERSION]


def run_backend(dataset: Path, frames: list[dict], backend: str, lex: Lexicon, set_: str, log=print) -> dict:
    import cv2

    pred = predictor(backend, lex)
    preds = {}
    for i, fr in enumerate(frames, 1):
        img = cv2.imread(str(dataset / set_ / fr["image"]))
        t0 = time.perf_counter()
        texts = pred(img)
        preds[fr["id"]] = {"texts": texts, "seconds": time.perf_counter() - t0}
        if i % 25 == 0:
            log(f"  {backend}：{i}/{len(frames)}")
    return score(frames, preds, lex, backend=backend, set_=set_)


def main(dataset: Path, backends: list[str], set_: str = "core", tags: dict | None = None, compare: str | None = None,
         name: str = "eval", log=print) -> tuple[Path, Path]:  # fmt: skip
    from tools.ocr_dataset import lexicon

    frames = filter_frames(load_frames(dataset, set_), tags or {})
    if not frames:
        raise SystemExit(
            f"{dataset / set_} 里没有符合条件的帧（框级评测只看 schema 1 的框级帧；场景帧用 scenario_eval，字段帧用 eval-fields）"
        )
    lexdir = dataset / "lexicon"
    lex = lexicon.load(lexdir) if (lexdir / "words.txt").is_file() else lexicon.build()
    results = []
    for b in backends:
        log(f"跑 {b}（{len(frames)} 帧）…")
        results.append(run_backend(dataset, frames, b, lex, set_, log))
    base = json.loads(Path(compare).read_text(encoding="utf-8")) if compare else None
    stamp = time.strftime("%Y-%m-%d")
    title = f"OCR 评测 {stamp} · {name}"
    out = dataset / "reports" / f"{stamp}-{name}"
    out.parent.mkdir(exist_ok=True)
    md = report_md(results, base, title)
    Path(f"{out}.md").write_text(md, encoding="utf-8")
    Path(f"{out}.json").write_text(json.dumps({"title": title, "created": stamp, "set": set_, "tags": tags or {}, "lexicon": lex.version,
                                               "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")  # fmt: skip
    return Path(f"{out}.md"), Path(f"{out}.json")
