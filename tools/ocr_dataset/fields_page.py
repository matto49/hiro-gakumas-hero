"""字段真值核对页：每张图要读出的字段（真值）、识别方法、各后端读数，每个字段可以提意见（意见存到 comments_server）。

    build(dataset, reads, out=...)   reads = 字段评测 JSON 里的 "reads"：{后端: {帧 id: dmm_samples.read 的结果}}
页面静态，部署在 hiro.matto.top/ocr-dataset/（同目录下的 api/comments 是意见后端）。
"""

from __future__ import annotations

import base64
import html
import json
from collections.abc import Callable
from pathlib import Path

from tools.ocr_dataset import schema, truth

import cv2

KINDS_JS = '["标注错误", "不该读", "类型/方法", "识别结果", "格式", "页面展示", "其他"]'


def default_names(path: str, value, row: dict) -> str | None:
    """卡 / 饮料 ID → 日文名（读主数据）；其余返回 None。row 是同一层的兄弟字段（取 upgrade）。"""
    try:
        from hiro.state import context

        if isinstance(value, str) and path.endswith("card_id"):
            return context.card_name(value, int(row.get("upgrade") or 0))
        if isinstance(value, str) and ("drink_id" in path or path.startswith("drinks[") or "pdrink_" in value):
            return context.drink_name(value)
    except Exception:  # noqa: BLE001  主数据不可用时只显示 ID
        return None
    return None


def _flat_truth(fr: dict) -> list[tuple[str, str, object]]:
    """(带前缀的路径, 查方法用的路径, 真值)。"""
    t = fr.get("truth") or {}
    rows = [(p, p, v) for p, v in truth.flatten(t.get("fields") or {}).items()]
    rows += [("quick:" + p, p, v) for p, v in truth.flatten(t.get("quick_fields") or {}).items()]
    rows += [("check:" + k, k, v) for k, v in (t.get("checks") or {}).items()]
    return rows


def _flat_read(r: dict | None) -> dict:
    r = r or {}
    out = dict(truth.flatten(r.get("fields") or {}))
    out.update({"quick:" + p: v for p, v in truth.flatten(r.get("quick_fields") or {}).items()})
    out.update({"check:" + k: r.get(k) for k in ("quick", "scene", "tap") if k in r})
    return out


def _siblings(fields: dict, path: str) -> dict:
    """hand[0].card_id → hand[0] 这一层的 dict（拿 upgrade）。"""
    node = fields
    for part in path.replace("]", "").replace("[", ".").split(".")[:-1]:
        try:
            node = node[int(part)] if part.isdigit() else node[part]
        except (KeyError, IndexError, TypeError):
            return {}
    return node if isinstance(node, dict) else {}


def _show(v) -> str:
    return html.escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))


def _jpg(path: Path, width: int = 540) -> bytes:
    img = cv2.imread(str(path))
    if img is None:
        return b""
    img = cv2.resize(img, (width, int(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
    _ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes()


def build(dataset: str | Path, reads: dict[str, dict], out: str | Path | None = None, names: Callable | None = None, set_: str = "core",
          image_files: bool = False) -> Path:  # fmt: skip
    """image_files：图片另存到页面旁边的 img/（懒加载，部署用）；否则内嵌 base64（单文件，本地看）。"""
    root = Path(dataset)
    out = Path(out) if out else root / ".work" / "fields.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    if image_files:
        (out.parent / "img").mkdir(exist_ok=True)

    def img_src(fr: dict) -> str:
        data = _jpg(root / set_ / fr["image"])
        if image_files:
            (out.parent / "img" / f"{fr['id']}.jpg").write_bytes(data)
            return f'img/{e(fr["id"])}.jpg" loading="lazy'
        return "data:image/jpeg;base64," + base64.b64encode(data).decode()

    names = names or default_names
    backends = list(reads)
    e = html.escape
    secs = []
    frames = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / set_).glob("*.json"))]
    frames = [f for f in frames if f.get("schema") == schema.SCHEMA_FIELDS]
    for fr in frames:
        fid, page = fr["id"], fr.get("page") or "?"
        got = {b: _flat_read(reads[b].get(fid)) for b in backends}
        st = fr.get("field_status") or {}
        fields = (fr.get("truth") or {}).get("fields") or {}
        rows = []
        for path, mpath, want in _flat_truth(fr):
            method = "page" if path.startswith("check:") else truth.method_of(page, mpath)
            crit = (not path.startswith(("quick:", "check:"))) and truth.is_critical(page, mpath)
            nm = names(path, want, _siblings(fields, path) if not path.startswith(("quick:", "check:")) else {})
            cells = []
            bad_any = False
            for b in backends:
                g = got[b].get(path)
                ok = (path in got[b] and g == want) or (
                    want is None and g is None
                )  # 同 truth.compare：真值 None、读数没这个键算对
                bad_any |= not ok
                cells.append(
                    f'<td class="{"ok" if ok else "bad"}">{"（没读到）" if path not in got[b] else _show(g)}</td>'
                )
            status = st.get(path, "")
            crit_tag = ' <span class="crit">高代价</span>' if crit else ""
            rows.append(
                f'<tr class="{"err" if bad_any else ""}"><td class="path">{e(path)}{crit_tag}</td>'
                f'<td class="want">{_show(want)}{f"<br><small>{e(nm)}</small>" if nm else ""}{f" <small class=st>{e(status)}</small>" if status else ""}</td>'
                f'<td class="m m-{method}">{method}</td>{"".join(cells)}'
                f'<td class="cmcell"><button class="cmb" data-target="{e(fid)}" data-field="{e(path)}" data-quote="{e(_show(want))}" onclick="openForm(this)">评</button>'
                f'<div data-cm data-target="{e(fid)}" data-field="{e(path)}"></div></td></tr>'
            )
        meta = f"页面 <b>{e(page)}</b> · 平台 {e((fr.get('platform') or {}).get('kind') or '?')} · {len(rows)} 个字段 · 真值来源 {e(fr.get('truth_source') or '?')}"
        extra = ""
        if fr.get("note"):
            extra += f'<p class="note">说明：{e(fr["note"])}</p>'
        if fr.get("known_fail"):
            extra += f'<p class="note">已知读不对：{e(json.dumps(fr["known_fail"], ensure_ascii=False))}</p>'
        secs.append(
            f'<section class="frame" data-page="{e(page)}"><h2>{e(fid)}</h2><p class="meta">{meta}</p>{extra}'
            f'<p><button class="cmb" data-target="{e(fid)}" data-field="" onclick="openForm(this)">对这张图提意见</button></p>'
            f'<div data-cm data-target="{e(fid)}" data-field=""></div>'
            f'<div class="row"><img class="shot" src="{img_src(fr)}" onclick="this.classList.toggle(\'big\')">'
            f'<div class="tbl"><table><tr><th>字段</th><th>真值（要读出的内容）</th><th>方法</th>'
            + "".join(f"<th>{e(b)} 读到</th>" for b in backends)
            + f"<th>意见</th></tr>{''.join(rows)}</table></div></div></section>"
        )
    pages = sorted({f.get("page") or "?" for f in frames})
    out.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>识别测试集：每张图要读出的内容</title><style>'
        "body{font:13px system-ui,sans-serif;margin:20px;color:#222}section{border-top:2px solid #333;padding:8px 0 20px}"
        ".row{display:flex;gap:14px;align-items:flex-start}.shot{width:300px;flex:none;border:1px solid #ccc;cursor:zoom-in}.shot.big{width:540px;cursor:zoom-out}"
        ".tbl{flex:1;min-width:0;overflow:auto}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:3px 6px;vertical-align:middle}"
        "th{background:#f4f4f4}.path{font-family:ui-monospace,monospace;font-size:12px}.want{font-weight:600}.want small{font-weight:400;color:#555}"
        ".ok{background:#eaf7ea}.bad{background:#fde8e8}.crit{background:#c00;color:#fff;border-radius:3px;padding:0 4px;font-size:10px}"
        ".m{font-size:11px;color:#fff;text-align:center}.m-ocr{background:#36c}.m-icon{background:#a5c}.m-pixel{background:#888}.m-page{background:#4a4}.m-other{background:#c80}"
        ".meta{color:#666}.note{background:#fff7e0;padding:5px}.st{color:#c60}#bar{position:sticky;top:0;background:#fff;border-bottom:2px solid #333;padding:8px 0;z-index:5}"
        ".cmb{font-size:12px;cursor:pointer}.cmcell{min-width:200px;max-width:300px}.c{background:#fff7d6;border-left:3px solid #e0a800;padding:3px 6px;margin:3px 0;font-size:12px;white-space:pre-wrap}"
        ".k{background:#e0a800;color:#fff;border-radius:3px;padding:0 4px;font-size:11px}.form{background:#f4f8ff;border:1px solid #9bd;padding:6px;margin:4px 0}"
        ".form textarea{width:100%;min-height:60px;font:13px system-ui;box-sizing:border-box}.c a{color:#a00;font-size:11px;margin-left:6px}"
        "body.only-err tr:not(.err):not(:first-child){display:none}</style>"
        f"<h1>识别测试集：每张图要读出的内容（{len(frames)} 张）</h1>"
        "<p>每行是一张图里系统要读出的一个字段：「真值」是应该读出的值（卡 / 饮料附日文名），后面是各后端实际读到的，绿 = 对、红 = 错。"
        "方法：<span class='m m-ocr'>&nbsp;ocr&nbsp;</span> 文字识别 · <span class='m m-icon'>&nbsp;icon&nbsp;</span> 卡面 / 图标匹配 · "
        "<span class='m m-pixel'>&nbsp;pixel&nbsp;</span> 颜色 / 像素规则 · <span class='m m-page'>&nbsp;page&nbsp;</span> 判页（方法表是按代码整理的初版，标错了也请评）。"
        "路径前缀 quick: = 快速判页带出的字段，check: = 判页结果。点图片放大。</p>"
        '<div id="bar"><b id="count">意见加载中…</b> · <button class="cmb" data-target="page" data-field="" onclick="openForm(this)">提整体意见</button>'
        ' · <label><input type="checkbox" onchange="document.body.classList.toggle(\'only-err\',this.checked)"> 只看有读错的字段</label>'
        ' · 页面 <select onchange="filt(this.value)"><option value="">全部</option>'
        + "".join(f"<option>{e(p)}</option>" for p in pages)
        + '</select><div data-cm data-target="page" data-field=""></div></div>'
        + "".join(secs)
        + "<script>"
        + JS.replace("__KINDS__", KINDS_JS)
        + "</script>",
        encoding="utf-8",
    )
    return out


JS = r"""
const API = "api/comments";
const KINDS = __KINDS__;
let all = [];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
function filt(p) { document.querySelectorAll("section.frame").forEach((s) => { s.style.display = !p || s.dataset.page === p ? "" : "none"; }); }
async function load() {
  try { const r = await fetch(API, { cache: "no-store" }); if (!r.ok) throw new Error(r.status); all = await r.json(); }
  catch (e) { document.getElementById("count").textContent = "意见服务连不上（" + e.message + "），本地打开的页面不能提交"; return; }
  render();
}
function render() {
  document.querySelectorAll("[data-cm]").forEach((el) => {
    const t = el.dataset.target, f = el.dataset.field;
    el.innerHTML = all.filter((c) => c.target === t && (c.field ?? "") === f && c.box == null)
      .map((c) => `<div class="c"><span class="k">${esc(c.kind)}</span> ${esc(c.text)}<a href="#" class="del" data-id="${esc(c.id)}">删除</a></div>`).join("");
  });
  document.getElementById("count").textContent = `已提 ${all.filter((c) => c.box == null).length} 条意见`;
}
function openForm(btn) {
  const old = btn.parentElement.querySelector(".form"); if (old) { old.remove(); return; }
  const f = document.createElement("div"); f.className = "form";
  f.innerHTML = `<select>${KINDS.map((k) => `<option>${k}</option>`).join("")}</select>
    <textarea placeholder="写意见（Ctrl/⌘+Enter 提交）"></textarea><button>提交</button> <button type="button">取消</button>`;
  const [sel, ta, ok, cancel] = [f.querySelector("select"), f.querySelector("textarea"), ...f.querySelectorAll("button")];
  if (!btn.dataset.field) sel.value = "其他";
  const send = async () => {
    const text = ta.value.trim(); if (!text) { ta.focus(); return; }
    ok.disabled = true;
    const body = { target: btn.dataset.target, kind: sel.value, text };
    if (btn.dataset.field) { body.field = btn.dataset.field; body.quote = btn.dataset.quote; }
    const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { ok.disabled = false; alert("提交失败：" + r.status); return; }
    f.remove(); await load();
  };
  ok.onclick = send; cancel.onclick = () => f.remove();
  ta.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) send(); });
  btn.insertAdjacentElement("afterend", f); ta.focus();
}
// 删除链接用 data-id + 委托监听，不把评论 id 拼进内联脚本（id 是投稿者给的，拼进去就是 XSS）
document.addEventListener("click", (ev) => { const a = ev.target.closest("a.del"); if (a) { ev.preventDefault(); del(a.dataset.id); } });
async function del(id) {
  if (!confirm("删除这条意见？")) return;
  await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, deleted: true }) });
  await load();
}
load();
"""
