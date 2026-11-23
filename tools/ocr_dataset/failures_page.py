"""识别不通过的项按原因聚合成核对页（真实系统在各 OCR 后端下读错 / 没读的地方）。

分四类：
- 覆盖缺口：所有后端都没读（系统不产出这一项）；
- 三后端一致但≠真值：各后端读出同一个值、和真值不同（可能是真值 / 定义问题，也可能是识别逻辑本身的问题）；
- 三后端都没读对：读法各不相同（难读）；
- 后端差异：有的后端对、有的不对（选哪个 OCR 看这一类）。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from tools.ocr_dataset import scenario, scenario_eval as se

CATS = ("覆盖缺口", "三后端一致但≠真值", "三后端都没读对", "后端差异")
CHECK_PAGE = {"core": "check.html", "hard": "check-hard.html"}


def _rows(fr: dict, read: dict | None) -> dict[str, dict]:
    sid = fr["scenario"]
    r = read or {}
    rows = se.score_frame(sid, fr.get("truth") or {}, se.adapt(sid, r.get("fields")), fr.get("item_status"))
    rows += se.score_buttons(fr.get("buttons"), r.get("texts") or [])
    return {x["path"]: x for x in rows}


def aggregate(sets: list[tuple[str, list[dict]]], reads: dict[str, dict[str, dict]], backends) -> list[dict]:
    """sets：[(集合名, 帧列表)]；reads：{集合名: {后端: {帧 id: 系统读数}}}。→ 按（类别, 信息项）聚合的组，组内是每个不通过的实例。"""
    groups: dict[tuple[str, str], dict] = {}
    for set_name, frames in sets:
        for fr in frames:
            per = {b: _rows(fr, reads[set_name][b].get(fr["id"])) for b in backends}
            paths = dict.fromkeys(p for b in backends for p in per[b])
            for p in paths:
                res = {b: per[b][p]["result"] if p in per[b] else "对" for b in backends}
                if all(v == "对" for v in res.values()):
                    continue
                got = {b: per[b][p]["got"] if p in per[b] else None for b in backends}
                want = next((per[b][p]["want"] for b in backends if p in per[b]), None)
                if all(v != "对" for v in res.values()):
                    if all(v == "没读" for v in res.values()):
                        cat = "覆盖缺口"
                    elif all(v == "错" for v in res.values()) and len({json.dumps(g, ensure_ascii=False) for g in got.values()}) == 1:
                        cat = "三后端一致但≠真值"
                    else:
                        cat = "三后端都没读对"
                else:
                    cat = "后端差异"
                sid = fr["scenario"]
                key = (cat, f"{sid} {se._norm_item(p)}")
                g = groups.setdefault(key, {"category": cat, "item": key[1], "importance": se._importance(sid, p), "instances": [], "failing": {}})
                g["instances"].append({"set": set_name, "frame": fr["id"], "path": p, "want": want, "got": got, "results": res})
                for b, v in res.items():
                    if v != "对":
                        g["failing"][b] = g["failing"].get(b, 0) + 1
    imp = {"高": 0, "中": 1, "低": 2}
    return sorted(groups.values(), key=lambda g: (CATS.index(g["category"]), imp.get(g["importance"], 3), -len(g["instances"]), g["item"]))


def _show(v) -> str:
    if v is None:
        return "<i>null</i>"
    return html.escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))


def render(groups: list[dict], backends, out: str | Path) -> Path:
    from tools.ocr_dataset import fields_page

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    e = html.escape
    desc = {
        "覆盖缺口": "所有后端都没读：系统不产出这一项，换 OCR 也没用，要补识别逻辑。",
        "三后端一致但≠真值": "各后端读出同一个值、和真值不同：先看是不是真值或定义有问题（之前「剩余回合 +N」就是这一类）；真值没问题的话，是识别逻辑本身错了。",
        "三后端都没读对": "各后端读法不同、都不对：难读的地方，看看是图像本身难还是读取区域不对。",
        "后端差异": "有的后端对、有的不对：比较 OCR 后端时看这一类。",
    }
    summary, secs = [], []
    for i, g in enumerate(groups):
        key = f"{g['category']}|{g['item']}"
        fail = " ".join(f"{e(b)} {g['failing'].get(b, 0)}" for b in backends)
        summary.append(f'<tr><td>{e(g["category"])}</td><td><a href="#g{i}">{e(g["item"])}</a></td><td>{e(g["importance"])}</td>'
                       f'<td>{len(g["instances"])}</td><td>{fail}</td></tr>')  # fmt: skip
        rows = []
        for x in g["instances"]:
            page = CHECK_PAGE.get(x["set"], "check.html")
            cells = "".join(f'<td class="r-{x["results"][b]}">{x["results"][b]}<br><small>{_show(x["got"][b])}</small></td>' for b in backends)
            rows.append(f'<tr><td><a href="{page}#{e(x["frame"])}" target="_blank"><img class="thumb" src="img/{e(x["frame"])}.jpg" loading="lazy"></a></td>'
                        f'<td class="mono">{e(x["set"])} · {e(x["frame"])}<br>{e(x["path"])}</td><td class="want">{_show(x["want"])}</td>{cells}</tr>')  # fmt: skip
        secs.append(
            f'<section id="g{i}" data-cat="{e(g["category"])}"><h2><span class="cat">{e(g["category"])}</span> {e(g["item"])}'
            f' <small>（{len(g["instances"])} 处 · 重要度 {e(g["importance"])} · 不通过：{fail}）</small></h2>'
            f'<p><button class="cmb" data-target="failures" data-field="{e(key)}" data-quote="{e(g["item"])}" onclick="openForm(this)">对这一组提意见</button></p>'
            f'<div data-cm data-target="failures" data-field="{e(key)}"></div>'
            f'<details{" open" if len(g["instances"]) <= 6 else ""}><summary>展开 {len(g["instances"])} 处</summary><table><tr><th>截图</th><th>位置</th><th>真值</th>'
            + "".join(f"<th>{e(b)}</th>" for b in backends) + f'</tr>{"".join(rows)}</table></details></section>'
        )
    cats = "".join(f"<li><b>{c}</b>（{sum(1 for g in groups if g['category'] == c)} 组 / {sum(len(g['instances']) for g in groups if g['category'] == c)} 处）：{desc[c]}</li>" for c in CATS)
    out.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>识别不通过的项（聚合）</title><style>'
        "body{font:13px system-ui,sans-serif;margin:16px;color:#222;max-width:1500px}section{border-top:2px solid #333;padding:6px 0 14px}h2{font-size:15px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:3px 6px;vertical-align:top}th{background:#f4f4f4}.thumb{width:90px}"
        ".mono{font-family:ui-monospace,monospace;font-size:11px}.want{font-weight:600}.cat{background:#333;color:#fff;border-radius:3px;padding:1px 6px;font-size:12px}"
        ".r-对{background:#eaf7ea}.r-错{background:#fde0e0}.r-没读{background:#eee;color:#666}#bar{position:sticky;top:0;background:#fff;border-bottom:2px solid #333;padding:8px 0;z-index:5}"
        ".cmb{font-size:12px;cursor:pointer}.c{background:#fff7d6;border-left:3px solid #e0a800;padding:3px 6px;margin:3px 0;font-size:12px;white-space:pre-wrap;max-width:900px}"
        ".k{background:#e0a800;color:#fff;border-radius:3px;padding:0 4px;font-size:11px}.form{background:#f4f8ff;border:1px solid #9bd;padding:6px;margin:4px 0;max-width:700px}"
        ".form textarea{width:100%;min-height:60px;box-sizing:border-box}.c a{color:#a00;font-size:11px;margin-left:6px}</style>"
        f"<h1>识别不通过的项（聚合，{len(groups)} 组 / {sum(len(g['instances']) for g in groups)} 处）</h1>"
        f"<p>核心集 + 进阶集，真实系统在 {' / '.join(e(b) for b in backends)} 下读错或没读的地方，按原因分组。待定项不计。点缩略图跳到核对页上那一帧。</p><ul>{cats}</ul>"
        '<div id="bar"><b id="count">意见加载中…</b> · <button class="cmb" data-target="failures" data-field="" onclick="openForm(this)">提整体意见</button>'
        ' · 类别 <select onchange="filt(this.value)"><option value="">全部</option>' + "".join(f"<option>{c}</option>" for c in CATS) + "</select>"
        '<div data-cm data-target="failures" data-field=""></div></div>'
        f'<h2>总览</h2><table><tr><th>类别</th><th>信息项</th><th>重要度</th><th>处数</th><th>各后端不通过</th></tr>{"".join(summary)}</table>'
        + "".join(secs)
        + "<script>" + fields_page.JS.replace("__KINDS__", '["识别逻辑要改", "真值 / 定义问题", "可以接受", "OCR 选型", "其他"]').replace("s.dataset.page === p", "s.dataset.cat === p").replace('document.querySelectorAll("section.frame")', 'document.querySelectorAll("section[data-cat]")')
        + "</script>",
        encoding="utf-8",
    )
    return out


__all__ = ["aggregate", "render", "CATS", "scenario"]
