"""场景真值核对 + 系统校验结果页（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-scenarios-design.md §3、§5）。

每帧：截图；每个信息项一行——真值（两个 LLM 一致的 / 待定）、Claude 和 Codex 各自的读法、各 OCR 后端下真实系统读到的（对 / 错 / 没读）；
待定项可以一键「取 Claude / 取 Codex」或手填，存成意见（kind = 定答案），之后拉回来写进数据仓库。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from tools.ocr_dataset import fields_page, scenario, schema
from tools.ocr_dataset import scenario_eval as se

KINDS = '["定答案", "标注错误", "漏了", "不需要", "识别结果", "其他"]'


def _show(v) -> str:
    if v is None:
        return "<i>null</i>"
    return html.escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))


def build(dataset: str | Path, reads: dict[str, dict], out: str | Path | None = None, set_: str = "core") -> Path:
    root = Path(dataset)
    out = Path(out) if out else root / ".work" / "scenarios.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / "img").mkdir(exist_ok=True)
    backends = list(reads)
    e = html.escape
    frames = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / set_).glob("*.json"))]
    frames = [f for f in frames if f.get("schema") == schema.SCHEMA_SCENARIO]
    secs, n_disputed = [], 0
    for fr in frames:
        fid, sid = fr["id"], fr["scenario"]
        (out.parent / "img" / f"{fid}.jpg").write_bytes(fields_page._jpg(root / set_ / fr["image"]))
        alias: dict[str, str] = {}
        ft = se._flat(sid, fr.get("truth") or {}, alias)
        ans = {n: se._flat(sid, (a or {}).get("items") or {}) for n, a in (fr.get("answers") or {}).items()}
        names = list(ans)[:2]
        sysrows = {}
        for b in backends:
            r = reads[b].get(fid) or {}
            rows = se.score_frame(sid, fr.get("truth") or {}, se.adapt(sid, r.get("fields")))
            rows += se.score_buttons(fr.get("buttons"), r.get("texts") or [])
            sysrows[b] = ({x["path"]: x for x in rows}, r.get("page"))
        st = fr.get("item_status") or {}
        trs = []
        paths = list(ft) + [p for n in names for p in ans[n] if p not in ft]
        paths = list(dict.fromkeys(paths)) + [f"button:{b['label']}" for b in fr.get("buttons") or []]
        for p in paths:
            is_btn = p.startswith("button:")
            disputed = "disputed" in (
                st.get(p),
                st.get(alias.get(p, "")),
            )  # item_status 的键是下标路径，按 label 对齐的列表要换算
            n_disputed += disputed
            if is_btn:
                b = next(x for x in fr["buttons"] if f"button:{x['label']}" == p)
                want = f"{b['label']} @ {b['box']}" if b.get("box") else f"{b['label']}（没找到位置）"
            else:
                want = _show(ft.get(p))
            cells = []
            for b in backends:
                x = sysrows[b][0].get(p)
                if x is None:
                    cells.append('<td class="r-na">—</td>')
                else:
                    cells.append(f'<td class="r-{x["result"]}">{x["result"]}<br><small>{_show(x["got"])}</small></td>')
            picks = ""
            if disputed:
                picks = "".join(f'<button class="pick" data-target="{e(fid)}" data-field="{e(p)}" data-pick="{e(json.dumps(ans[n].get(p), ensure_ascii=False))}" '
                                f'onclick="pick(this)">取 {e(n)}</button>' for n in names)  # fmt: skip
            trs.append(
                f'<tr class="{"disp" if disputed else ""}"><td class="path">{e(p)}</td>'
                f'<td class="want">{want}{" <span class=st>待定</span>" if disputed else ""}</td>'
                + "".join(f"<td>{'' if is_btn else _show(ans[n].get(p))}</td>" for n in names)
                + "".join(cells)
                + f'<td class="cmcell">{picks}<button class="cmb" data-target="{e(fid)}" data-field="{e(p)}" data-quote="{e(str(ft.get(p)))}" onclick="openForm(this)">评 / 手填</button>'
                f'<div data-cm data-target="{e(fid)}" data-field="{e(p)}"></div></td></tr>'
            )
        pages = " · ".join(f"{b} 判成 {e(str(sysrows[b][1]))}" for b in backends)
        secs.append(
            f'<section class="frame" id="{e(fid)}" data-sc="{e(sid)}" data-page="{e(sid)}"><h2>{e(fid)}</h2><p class="meta">场景 <b>{e(scenario.load()[sid]["name"])}</b> · 平台 '
            f"{e((fr.get('platform') or {}).get('kind') or '?')} · 系统判页：{pages}</p>"
            f'<div class="row"><img class="shot" src="img/{e(fid)}.jpg" loading="lazy" onclick="this.classList.toggle(\'big\')"><div class="tbl"><table>'
            f"<tr><th>信息项</th><th>真值</th>"
            + "".join(f"<th>{e(n)} 读</th>" for n in names)
            + "".join(f"<th>系统 · {e(b)}</th>" for b in backends)
            + f"<th>定答案 / 意见</th></tr>{''.join(trs)}</table></div></div></section>"
        )
    opts = "".join(f"<option value='{sid}'>{e(d['name'])}</option>" for sid, d in scenario.load().items())
    out.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>场景真值核对 + 系统校验</title><style>'
        "body{font:13px system-ui,sans-serif;margin:16px;color:#222}section{border-top:2px solid #333;padding:6px 0 16px}.row{display:flex;gap:14px;align-items:flex-start}"
        ".shot{width:300px;flex:none;border:1px solid #ccc;cursor:zoom-in}.shot.big{width:540px}.tbl{flex:1;min-width:0;overflow:auto}"
        "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:3px 6px;vertical-align:top}th{background:#f4f4f4}.path{font-family:ui-monospace,monospace;font-size:12px}"
        ".want{font-weight:600}.st{background:#f80;color:#fff;border-radius:3px;padding:0 4px;font-size:11px}tr.disp{background:#fff8ec}"
        ".r-对{background:#eaf7ea}.r-错{background:#fde0e0}.r-没读{background:#eee;color:#666}.meta{color:#666}"
        "#bar{position:sticky;top:0;background:#fff;border-bottom:2px solid #333;padding:8px 0;z-index:5}.cmb,.pick{font-size:12px;cursor:pointer;margin:1px}"
        ".pick{background:#fff3d6;border:1px solid #e0a800}.cmcell{min-width:200px;max-width:300px}.c{background:#fff7d6;border-left:3px solid #e0a800;padding:3px 6px;margin:3px 0;font-size:12px;white-space:pre-wrap}"
        ".k{background:#e0a800;color:#fff;border-radius:3px;padding:0 4px;font-size:11px}.form{background:#f4f8ff;border:1px solid #9bd;padding:6px;margin:4px 0}"
        ".form textarea{width:100%;min-height:50px;box-sizing:border-box}.c a{color:#a00;font-size:11px;margin-left:6px}"
        "body.only-disp tr:not(.disp):not(:first-child){display:none}body.only-bad tr:not(:has(.r-错,.r-没读)):not(:first-child){display:none}</style>"
        f"<h1>场景真值核对 + 系统校验（{len(frames)} 帧，待定 {n_disputed} 项）</h1>"
        "<p>真值照画面标：Claude 和 Codex 各自看图读，一致的直接定下，不一致的标「待定」，请你点「取 Claude / 取 Codex」或「评 / 手填」（意见类型选「定答案」，"
        "正文写正确值）。右边是真实系统（自己判页、自己读）在不同 OCR 后端下读到的：<span class='r-对'>&nbsp;对&nbsp;</span> "
        "<span class='r-错'>&nbsp;错&nbsp;</span> <span class='r-没读'>&nbsp;没读&nbsp;</span>（系统不产出这一项）。点截图放大。</p>"
        '<div id="bar"><b id="count">意见加载中…</b> · <button class="cmb" data-target="scenario-page" data-field="" onclick="openForm(this)">提整体意见</button>'
        ' · <label><input type="checkbox" onchange="document.body.classList.toggle(\'only-disp\',this.checked)"> 只看待定</label>'
        ' · <label><input type="checkbox" onchange="document.body.classList.toggle(\'only-bad\',this.checked)"> 只看系统读错 / 没读</label>'
        ' · 场景 <select onchange="filt(this.value)"><option value="">全部</option>' + opts + "</select>"
        '<div data-cm data-target="scenario-page" data-field=""></div></div>'
        + "".join(secs)
        + "<script>"
        + fields_page.JS.replace("__KINDS__", KINDS)
        + r"""
async function pick(btn) {
  btn.disabled = true;
  const body = { target: btn.dataset.target, field: btn.dataset.field, kind: "定答案", text: btn.dataset.pick, quote: btn.textContent };
  const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { btn.disabled = false; alert("提交失败：" + r.status); return; }
  await load();
}
</script>""",
        encoding="utf-8",
    )
    return out
