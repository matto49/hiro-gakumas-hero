"""待定框核对（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.4）。

build：把 disputed.json 里的框做成本地 HTML 页（.work/review-<时间>.html，不发布到任何在线平台）：裁图、上下文、各方答案；
点一个答案或手填、或标「忽略」，最后导出答案 JSON。apply：把答案 JSON 写回帧 JSON（resolved / ignored），重建 disputed.json。
抽查页（label.spot_check）导出的错框 JSON 也用 apply 写回：给了正确文字 → resolved，没给 → 打回 disputed。
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from tools.ocr_dataset import label, schema

import cv2

CONTEXT_PAD = (90, 60)  # 上下文图在框外多取的 (x, y)
CONTEXT_SCALE = 2  # 上下文图放大倍数（原尺寸只有约 200 像素宽，看不清）

REVIEW_JS = """
function pick(btn){const it=btn.closest('.item');it.querySelector('input[type=text]').value=btn.dataset.v;mark(it,'text')}
function ign(btn){mark(btn.closest('.item'),'ignore')}
function mark(it,kind){it.dataset.done=kind;it.classList.toggle('ign',kind==='ignore');it.classList.add('done');upd()}
document.addEventListener('input',e=>{if(e.target.type==='text')mark(e.target.closest('.item'),'text')});
function upd(){const all=document.querySelectorAll('.item');const d=document.querySelectorAll('.item.done').length;
document.getElementById('cnt').textContent=`已处理 ${d} / ${all.length}`}
function save(){const out=[];document.querySelectorAll('.item.done').forEach(it=>{const b={frame:it.dataset.frame,set:it.dataset.set,box:+it.dataset.box};
if(it.dataset.done==='ignore')b.ignore=true;else b.text=it.querySelector('input[type=text]').value;out.push(b)});
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));
a.download='review-answers.json';a.click()}
upd();
"""

STYLE = (
    "body{font:14px system-ui,sans-serif;margin:24px;max-width:1100px}.item{border-bottom:1px solid #ddd;padding:14px 0}"
    ".item.done{background:#f3fbf3}.item.ign{background:#f6f6f6;opacity:.6}img{max-width:100%;background:#eee;display:block;margin:4px 0}"
    ".meta{color:#666;font-size:12px}.ans button{font-size:18px;margin:4px 6px 4px 0;padding:4px 10px}.ans small{color:#888;font-size:11px}"
    "input[type=text]{font-size:18px;width:420px;padding:4px}#bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:2px solid #333;z-index:1}"
)


def _context(img, box):
    h, w = img.shape[:2]
    px, py = CONTEXT_PAD
    x1, y1, x2, y2 = max(0, box[0] - px), max(0, box[1] - py), min(w, box[2] + px), min(h, box[3] + py)
    c = img[y1:y2, x1:x2].copy()
    c = cv2.resize(c, None, fx=CONTEXT_SCALE, fy=CONTEXT_SCALE, interpolation=cv2.INTER_CUBIC)
    s = CONTEXT_SCALE
    cv2.rectangle(
        c,
        (int((box[0] - x1) * s), int((box[1] - y1) * s)),
        (int((box[2] - x1) * s), int((box[3] - y1) * s)),
        (0, 0, 255),
        2,
    )
    return c


def _item(s: str, fr: dict, b: dict, img) -> str:
    crop = label._crop(img, b["box"], 6)
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    seen, buttons = {}, []
    for src, v in (b.get("answers") or {}).items():
        if isinstance(v, str) and v.strip():
            seen.setdefault(v, []).append(src)
    for v, srcs in seen.items():
        e = html.escape(v)
        buttons.append(
            f'<button data-v="{e}" onclick="pick(this)">{e} <small>{html.escape("/".join(srcs))}</small></button>'
        )
    e = html.escape
    return (
        f'<div class="item" data-frame="{e(fr["id"])}" data-set="{s}" data-box="{b["id"]}">'
        f'<div class="meta">{e(fr["id"])} #{b["id"]} · 页面 {e(fr.get("page") or "")} · {e(str(b["tags"]["type"]))} · 框 {b["box"]}</div>'
        f'<img src="data:image/png;base64,{label._png_b64(crop)}">'
        f'<img src="data:image/png;base64,{label._png_b64(_context(img, b["box"]))}" style="max-width:720px">'
        f'<div class="ans">{"".join(buttons)}</div>'
        f'<input type="text" value="{e(b.get("text") or "")}" placeholder="手填正确文字"> '
        f'<button onclick="ign(this)">忽略（装饰字 / 读不出 / 不是字）</button></div>'
    )


def build(dataset: str | Path) -> Path:
    """disputed.json 里的每个框一项；返回页面路径。"""
    root = Path(dataset)
    disputed = (
        json.loads((root / "disputed.json").read_text(encoding="utf-8")) if (root / "disputed.json").is_file() else []
    )
    frames: dict[tuple, dict] = {}
    imgs: dict[tuple, object] = {}
    items = []
    for d in disputed:
        key = (d["set"], d["frame"])
        if key not in frames:
            frames[key] = json.loads((root / d["set"] / f"{d['frame']}.json").read_text(encoding="utf-8"))
            imgs[key] = cv2.imread(str(root / d["set"] / frames[key]["image"]))
        fr = frames[key]
        b = next((x for x in fr["boxes"] if x["id"] == d["box"]), None)
        if b is not None and imgs[key] is not None:
            items.append(_item(d["set"], fr, b, imgs[key]))
    page = root / ".work" / f"review-{time.strftime('%Y%m%d-%H%M%S')}.html"
    page.parent.mkdir(exist_ok=True)
    page.write_text(
        f'<!doctype html><meta charset="utf-8"><title>待定框核对</title><style>{STYLE}</style>'
        f'<div id="bar"><b>待定框 {len(items)} 个</b> · <span id="cnt"></span> · <button onclick="save()">导出答案</button>'
        " · 导出后运行 <code>python -m tools.ocr_dataset review --apply review-answers.json</code></div>"
        + "".join(items)
        + f"<script>{REVIEW_JS}</script>",
        encoding="utf-8",
    )
    return page


def apply(dataset: str | Path, answers) -> dict[str, int]:
    """answers：列表或 JSON 文件路径，每项 {frame, set, box} 加 text（→ resolved）/ ignore: true（→ ignored）/ disputed: true（→ 打回待定）。"""
    root = Path(dataset)
    if not isinstance(answers, list):
        answers = json.loads(Path(answers).read_text(encoding="utf-8"))
    stats = {"resolved": 0, "ignored": 0, "disputed": 0, "missing": 0}
    by_frame: dict[tuple, list[dict]] = {}
    for a in answers:
        by_frame.setdefault((a.get("set") or "core", a.get("frame")), []).append(a)
    for (s, fid), items in by_frame.items():
        path = root / s / f"{fid}.json"
        if s not in schema.SETS or not path.is_file():
            stats["missing"] += len(items)
            continue
        fr = json.loads(path.read_text(encoding="utf-8"))
        boxes = {b["id"]: b for b in fr["boxes"]}
        for a in items:
            b = boxes.get(a.get("box"))
            if b is None:
                stats["missing"] += 1
                continue
            if a.get("ignore"):
                b["status"], b["ignore"] = "ignored", True
                stats["ignored"] += 1
            elif a.get("disputed"):
                b["status"], b["ignore"] = "disputed", False
                stats["disputed"] += 1
            elif isinstance(a.get("text"), str) and a["text"].strip():  # 空白不算答案（要表示没字用「忽略」）
                b["text"], b["status"], b["ignore"] = label.norm(a["text"]), "resolved", False
                stats["resolved"] += 1
            else:
                stats["missing"] += 1
                continue
            b["reviewed"] = time.strftime("%Y-%m-%d")
        path.write_text(json.dumps(fr, ensure_ascii=False, indent=1), encoding="utf-8")
    label.rebuild_disputed(root)
    return stats
