"""字段评测（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-fields-design.md §6）。

每个后端各跑一遍实机的分页读取（tools.audit.dmm_samples.read：判页 + 该页 extract，取稳定项），和帧里的字段真值逐字段严格相等比对。
三类真值：truth.fields（extract 字段）、truth.quick_fields（快速判页带出的字段，路径前缀 quick:）、truth.checks（判页结果，前缀 check:）。
field_status 里 disputed / ignored 的字段不计分。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tools.ocr_dataset import schema, truth

import numpy as np

SKIP = ("disputed", "ignored")
N_ERRORS = 200


class _Acc:
    def __init__(self):
        self.n = self.ok = 0

    def add(self, ok: bool):
        self.n += 1
        self.ok += bool(ok)

    def to_dict(self):
        return {"n": self.n, "ok": self.ok, "acc": self.ok / self.n if self.n else None}


def _rows(fr: dict, got: dict | None):
    """(路径, 方法, 是否高代价, 真值, 读到, 对不对)；待定 / 忽略的字段跳过。"""
    got = got or {}
    t, page, st = fr.get("truth") or {}, fr.get("page"), fr.get("field_status") or {}
    for r in truth.compare(t.get("fields") or {}, got.get("fields")):
        if st.get(r.path) not in SKIP:
            yield r.path, truth.method_of(page, r.path), truth.is_critical(page, r.path), r.want, r.got, r.ok
    for r in truth.compare(t.get("quick_fields") or {}, got.get("quick_fields")):
        p = "quick:" + r.path
        if st.get(p) not in SKIP:
            yield p, truth.method_of(page, r.path), False, r.want, r.got, r.ok
    for k, want in (t.get("checks") or {}).items():
        p = "check:" + k
        if st.get(p) not in SKIP:
            g = got.get(k)
            yield p, "page", False, want, g, g == want


def score(
    frames: list[dict], reads: dict[str, dict], backend: str = "?", seconds: dict[str, float] | None = None
) -> dict:
    total = _Acc()
    groups: dict[str, dict[str, _Acc]] = {"method": {}, "page": {}, "field": {}, "critical": {}, "platform": {}}
    errors, disputed = [], 0
    for fr in frames:
        if fr["id"] not in reads:
            continue
        disputed += sum(1 for v in (fr.get("field_status") or {}).values() if v == "disputed")
        page, plat = fr.get("page") or "?", (fr.get("platform") or {}).get("kind") or "?"
        for path, method, crit, want, got, ok in _rows(fr, reads[fr["id"]]):
            key = f"{page} {truth.norm_path(path)}"
            for g, k in (("method", method), ("page", page), ("field", key), ("platform", plat)):
                groups[g].setdefault(k, _Acc()).add(ok)
            if crit:
                groups["critical"].setdefault(key, _Acc()).add(ok)
            total.add(ok)
            if not ok:
                errors.append(
                    {
                        "frame": fr["id"],
                        "page": page,
                        "path": path,
                        "method": method,
                        "critical": crit,
                        "want": want,
                        "got": got,
                    }
                )
    errors.sort(key=lambda e: (not e["critical"], e["page"], e["path"], e["frame"]))
    times = [seconds[f["id"]] for f in frames if seconds and f["id"] in seconds]
    warm = times[1:] if len(times) > 1 else times
    return {
        "backend": backend,
        "frames": sum(1 for f in frames if f["id"] in reads),
        "groups": {
            "all": total.to_dict(),
            **{g: {k: a.to_dict() for k, a in sorted(v.items())} for g, v in groups.items()},
        },
        "disputed": disputed,
        "errors": errors[:N_ERRORS],
        "timing": {
            "p50": float(np.percentile(warm, 50)) if warm else None,
            "p90": float(np.percentile(warm, 90)) if warm else None,
        },
    }


# ---------- 报告 ----------


def _pct(d: dict | None) -> str:
    if not d or d.get("acc") is None:
        return "—"
    return f"{d['acc'] * 100:.1f}%（{d['ok']}/{d['n']}）"


def _sec(v) -> str:
    return "—" if v is None else f"{v:.2f}s"


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 40 else s[:38] + "…"


def report_md(results: list[dict], title: str = "字段评测") -> str:
    names = [r["backend"] for r in results]
    head = "| " + " | ".join(names) + " |"
    sep = "|---" * len(names) + "|"
    r0 = results[0]
    lines = [f"# {title}", "", f"{r0['frames']} 帧，计分字段 {r0['groups']['all']['n']} 个，待定字段 {r0['disputed']} 个（不计分）。"
             "每个后端跑实机分页读取（判页 + extract 稳定项），和真值逐字段严格相等比对。", ""]  # fmt: skip

    def table(title: str, group: str, label: str, keys=None, split_key=False):
        keys = keys if keys is not None else sorted({k for r in results for k in r["groups"][group]})
        if not keys:
            return
        cols = "| 页面 | 字段 " if split_key else f"| {label} "
        lines.extend(["", f"## {title}", "", cols + head, ("|---|---" if split_key else "|---") + sep])
        for k in keys:
            cells = " | ".join(_pct(r["groups"][group].get(k)) for r in results)
            lines.append(("| " + " | ".join(k.split(" ", 1)) if split_key else f"| {k}") + f" | {cells} |")

    table("高代价字段（考试手牌 / 分数、商店价格）", "critical", "字段", split_key=True)
    lines.extend(
        [
            "",
            "## 总表",
            "",
            "| 后端 | 全部 | ocr | icon | pixel | 判页 | P50 | P90 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in results:
        m = r["groups"]["method"]
        t = r["timing"]
        lines.append(f"| {r['backend']} | {_pct(r['groups']['all'])} | {_pct(m.get('ocr'))} | {_pct(m.get('icon'))} | {_pct(m.get('pixel'))} | {_pct(m.get('page'))}"
                     f" | {_sec(t['p50'])} | {_sec(t['p90'])} |")  # fmt: skip
    table("按页面", "page", "页面")
    bad = sorted({k for r in results for k, v in r["groups"]["field"].items() if v["acc"] is not None and v["acc"] < 1})
    table("有读错的字段", "field", "字段", keys=bad, split_key=True)
    lines.extend(["", "方法：ocr = 文字识别；icon = 卡面 / 图标模板匹配；pixel = 颜色 / 像素规则（按钮种类、可点、角标）；判页 = 快速判页和页面判定。"
                  "方法表是按代码整理的初版（tools/ocr_dataset/truth.py），待核对。"])  # fmt: skip
    for r in results:
        if r["errors"]:
            lines.extend(
                [
                    "",
                    f"## 错例：{r['backend']}（{len(r['errors'])} 个，高代价在前）",
                    "",
                    "| 帧 | 页面 | 字段 | 方法 | 真值 | 读到 |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for e in r["errors"][:80]:
                lines.append(
                    f"| {e['frame']} | {e['page']} | {'**' + e['path'] + '**' if e['critical'] else e['path']} | {e['method']} | {_short(e['want'])} | {_short(e['got'])} |"
                )
    return "\n".join(lines) + "\n"


# ---------- 跑后端 ----------


def load_frames(dataset: Path, set_: str = "core") -> list[dict]:
    d = dataset / set_
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        fr = json.loads(p.read_text(encoding="utf-8"))
        if fr.get("schema") == schema.SCHEMA_FIELDS:
            out.append(fr)
    return out


def read_all(dataset: Path, frames: list[dict], backend: str, set_: str = "core", log=print) -> tuple[dict, dict]:
    """切到 backend，逐帧跑实机分页读取。每帧前清考试页的区域缓存（否则会复用上一帧 / 上一个后端的 OCR 结果）。"""
    from hiro.vision import hif, ocr
    from tools.audit.dmm_samples import read

    if backend not in ocr.ENGINES or backend == "auto":
        raise ValueError(f"后端 {backend!r} 不认识；可选：{', '.join(e for e in ocr.ENGINES if e != 'auto')}")
    ocr._engine = backend
    reads, secs = {}, {}
    for i, fr in enumerate(frames, 1):
        hif.EXAM_CACHE.clear()
        t0 = time.perf_counter()
        try:
            reads[fr["id"]] = read(dataset / set_ / fr["image"], fr.get("page"))
        except Exception as e:  # noqa: BLE001  一帧读崩算全错，不中断
            reads[fr["id"]] = {"error": f"{type(e).__name__}: {e}"}
        secs[fr["id"]] = time.perf_counter() - t0
        if i % 20 == 0:
            log(f"  {backend}：{i}/{len(frames)}")
    return reads, secs


def main(dataset: Path, backends: list[str], set_: str = "core", name: str = "fields", log=print) -> tuple[Path, Path]:
    frames = load_frames(dataset, set_)
    if not frames:
        raise SystemExit(f"{dataset / set_} 里没有字段真值帧（schema 2）")
    results, all_reads = [], {}
    for b in backends:
        log(f"跑 {b}（{len(frames)} 帧）…")
        reads, secs = read_all(dataset, frames, b, set_, log)
        all_reads[b] = reads
        results.append(score(frames, reads, backend=b, seconds=secs))
    stamp = time.strftime("%Y-%m-%d")
    out = dataset / "reports" / f"{stamp}-{name}"
    out.parent.mkdir(exist_ok=True)
    Path(f"{out}.md").write_text(report_md(results, f"字段评测 {stamp} · {name}"), encoding="utf-8")
    Path(f"{out}.json").write_text(json.dumps({"created": stamp, "set": set_, "results": results, "reads": all_reads}, ensure_ascii=False, indent=1, default=str),
                                   encoding="utf-8")  # fmt: skip
    return Path(f"{out}.md"), Path(f"{out}.json")
