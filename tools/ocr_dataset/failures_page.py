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

from tools.ocr_dataset import scenario
from tools.ocr_dataset import scenario_eval as se

CATS = ("覆盖缺口", "三后端一致但≠真值", "三后端都没读对", "后端差异")
CHECK_PAGE = {"core": "check.html", "hard": "check-hard.html"}


def _rows(fr: dict, read: dict | None) -> dict[str, dict]:
    sid = fr["scenario"]
    r = read or {}
    rows = se.score_frame(sid, fr.get("truth") or {}, se.adapt(sid, r.get("fields")), fr.get("item_status"))
    rows += se.score_buttons(fr.get("buttons"), r.get("texts") or [])
    return {x["path"]: x for x in rows}


def _region(fr: dict, path: str) -> list[int] | None:
    """这一项在画面上的位置（720×1280）：按钮用真值框外扩一圈，其余用场景定义里的显示区域。"""
    if path.startswith("button:"):
        b = next((x for x in fr.get("buttons") or [] if f"button:{x['label']}" == path and x.get("box")), None)
        if b:
            x1, y1, x2, y2 = b["box"]
            return [max(0, x1 - 90), max(0, y1 - 70), min(720, x2 + 90), min(1280, y2 + 70)]
        return None
    key = path.split("[")[0].split(".")[0]
    it = next((i for i in scenario.load()[fr["scenario"]]["items"] if i["key"] == key), None)
    return it.get("region") if it else None


def aggregate(sets: list[tuple[str, list[dict]]], reads: dict[str, dict[str, dict]], backends) -> list[dict]:
    """sets：[(集合名, 帧列表)]；reads：{集合名: {后端: {帧 id: 系统读数}}}。→ 按（类别, 信息项）聚合的组，组内是每个不通过的实例。"""
    groups: dict[tuple[str, str], dict] = {}
    for set_name, frames in sets:
        for fr in frames:
            if fr.get("excluded"):
                continue
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
                    elif (
                        all(v == "错" for v in res.values())
                        and len({json.dumps(g, ensure_ascii=False) for g in got.values()}) == 1
                    ):
                        cat = "三后端一致但≠真值"
                    else:
                        cat = "三后端都没读对"
                else:
                    cat = "后端差异"
                sid = fr["scenario"]
                key = (cat, f"{sid} {se._norm_item(p)}")
                g = groups.setdefault(
                    key,
                    {
                        "category": cat,
                        "item": key[1],
                        "importance": se._importance(sid, p),
                        "instances": [],
                        "failing": {},
                    },
                )
                g["instances"].append({"set": set_name, "frame": fr["id"], "image": fr.get("image"), "scenario": sid, "region": _region(fr, p),
                                       "path": p, "want": want, "got": got, "results": res})  # fmt: skip
                for b, v in res.items():
                    if v != "对":
                        g["failing"][b] = g["failing"].get(b, 0) + 1
    imp = {"高": 0, "中": 1, "低": 2}
    return sorted(
        groups.values(),
        key=lambda g: (CATS.index(g["category"]), imp.get(g["importance"], 3), -len(g["instances"]), g["item"]),
    )


SCENARIO_SHORT = {"exam": "考试", "consult": "相談商店", "round_interval": "轮间商店", "action_select": "选行动"}
BACKEND_NAME = {
    "vision": "Vision",
    "rapidocr": "PP-OCRv3",
    "simd": "SIMD",
    "ppocr5": "PP-OCRv5",
    "winocr": "Windows OCR",
}
CAT_TITLE = {
    "覆盖缺口": "所有 OCR 后端都没读出来",
    "三后端一致但≠真值": "所有 OCR 后端读成同一个值，但和画面不一样",
    "三后端都没读对": "所有 OCR 后端各读各的，都不对",
    "后端差异": "有的 OCR 对、有的不对",
}
CAT_HINT = {
    "覆盖缺口": "系统在这一屏没有产出这一项，换 OCR 也没用。要么补识别，要么确认用不到。",
    "三后端一致但≠真值": "要么是画面真值标错了、定义没写清，要么是系统的读法本身有问题。",
    "三后端都没读对": "难读的地方：看是图本身难，还是读的区域不对。",
    "后端差异": "比较 OCR 时看这里：谁读对了、谁读错了。",
}
VERDICTS = (("识别逻辑要改", "系统要改"), ("真值 / 定义问题", "真值或定义有问题"), ("可以接受", "可以接受"))
PLAIN = {"green_heart": "绿心", "red_heart": "红心", "buff": "状态图标", "none": "无", "vocal": "Vo", "dance": "Da", "visual": "Vi",
         "card": "技能卡", "drink": "饮料"}  # fmt: skip


def plain(v) -> str:
    """真值 / 读数 → 人看的写法。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "有" if v else "没有"
    if isinstance(v, list):
        return "、".join(plain(x) for x in v)
    if isinstance(v, str):
        return PLAIN.get(v, v)
    return str(v)


def _title(item: str) -> str:
    """「exam hand[].name」→「考试 · 手牌 · 卡名」；按钮 →「考试 · 按钮「SKIP」」。"""
    sid, path = item.split(" ", 1)
    head = SCENARIO_SHORT.get(sid, sid)
    if path.startswith("button:"):
        return f"{head} · 按钮「{path[7:]}」"
    key, _, sub = path.partition(".")
    key = key.replace("[]", "")
    it = next((i for i in scenario.load()[sid]["items"] if i["key"] == key), None)
    if it is None:
        return f"{head} · {path}"
    name = it.get("label", key)
    if sub:
        f = next((f for f in it.get("fields", []) if f["key"] == sub), None)
        name += " · " + (f.get("label", sub) if f else sub)
    return f"{head} · {name}"


def _where(path: str) -> str:
    """实例在这一帧里的哪一个：手牌[1] → 第 2 张。"""
    import re

    m = re.search(r"\[(\d+)\]", path)
    if m:
        return f"第 {int(m.group(1)) + 1} 个"
    m = re.search(r"\[([^\]]+)\]", path)
    return f"「{m.group(1)}」" if m else ""


def _crop(dataset: Path, inst: dict, crops: Path) -> str | None:
    """裁出这一项所在的区域并放大，存成 crops/<帧>__<项>.jpg；同一帧同一区域只裁一次。"""
    import cv2

    if not inst.get("region") or not inst.get("image"):
        return None
    key = inst["path"].split("[")[0].split(".")[0].replace("button:", "btn-").replace("/", "_")
    name = f"{inst['frame']}__{key}.jpg"
    dst = crops / name
    if not dst.exists():
        img = cv2.imread(str(dataset / inst["set"] / inst["image"]))
        if img is None:
            return None
        x1, y1, x2, y2 = inst["region"]
        c = img[y1:y2, x1:x2]
        if c.size == 0:
            return None
        f = min(2.0, 420 / c.shape[1], 340 / c.shape[0])  # 放大到约 420 宽或 340 高（细长的状态栏不会撑满屏）
        c = cv2.resize(c, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
        crops.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dst), c, [cv2.IMWRITE_JPEG_QUALITY, 86])
    return f"crops/{name}"


CSS = """
@font-face{font-family:"Barlow Condensed";font-style:italic;font-weight:700;font-display:swap;src:url(fonts/barlow-condensed-latin-700-italic.woff2) format("woff2")}
:root{--surface:oklch(1 0 0);--surface-2:oklch(.975 .002 60);--surface-3:oklch(.95 .003 60);--line:oklch(.925 .002 60);--line-strong:oklch(.86 .003 60);
--ink:oklch(.2 .005 60);--ink-2:oklch(.42 .005 60);--ink-3:oklch(.55 .005 60);--brand:oklch(.7 .185 48);--brand-deep:oklch(.58 .19 40);--brand-ink:oklch(.53 .165 40);
--brand-soft:oklch(.955 .038 62);--brand-tint:oklch(.972 .01 62);--good:oklch(.6 .16 145);--good-soft:oklch(.96 .04 145);--bad:oklch(.55 .2 25);--bad-soft:oklch(.96 .03 25);
--sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans","Noto Sans CJK SC",system-ui,sans-serif;--disp:"Barlow Condensed",var(--sans);--mono:ui-monospace,"SF Mono",Menlo,monospace}
*{box-sizing:border-box}html{scroll-padding-top:120px}
body{margin:0;background:var(--surface-2);color:var(--ink);font:14px/1.6 var(--sans)}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 80px}
header.top{position:sticky;top:0;z-index:10;background:var(--surface);border-bottom:1px solid var(--line)}
header.top .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:10px 20px;padding:12px 24px}
h1{font-size:20px;margin:0;font-weight:700;display:flex;align-items:center;gap:10px}
h1::before,.sec-title::before{content:"";width:6px;height:18px;background:var(--brand);transform:skewX(-18deg);border-radius:1px}
.progress{display:flex;align-items:center;gap:8px;color:var(--ink-2)}
.progress .bar{width:140px;height:6px;background:var(--surface-3);border-radius:3px;overflow:hidden}
.progress .bar i{display:block;height:100%;background:var(--brand);width:0;transition:width .4s cubic-bezier(.22,1,.36,1)}
.num{font-family:var(--disp);font-style:italic;font-weight:700;font-size:20px;color:var(--ink);letter-spacing:.01em}
.filters{display:flex;gap:10px;align-items:center;margin-left:auto;color:var(--ink-2);font-size:13px}
select,button{font:inherit}
select{border:1px solid var(--line-strong);border-radius:999px;padding:4px 12px;background:var(--surface);color:var(--ink)}
.intro{padding:28px 0 8px;color:var(--ink-2);max-width:72ch}
.legend{background:var(--surface);border:1px solid var(--line);border-radius:12px;margin:16px 0 8px;padding:4px 18px}
.legend div{display:grid;grid-template-columns:64px 260px 1fr;gap:12px;align-items:baseline;padding:10px 0;border-bottom:1px solid var(--line)}
.legend div:last-child{border-bottom:0}.legend b{font-size:14px}.legend span{color:var(--ink-3);font-size:13px}.legend .num{font-size:22px;text-align:right}
@media (max-width:760px){.legend div{grid-template-columns:48px 1fr}.legend div span:last-child{grid-column:2}}
.sec-title{display:flex;align-items:center;gap:10px;font-size:16px;font-weight:700;margin:36px 0 12px}
table.ov{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ov th,.ov td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
.ov th{background:var(--surface-2);color:var(--ink-2);font-weight:600}.ov tr:last-child td{border-bottom:0}
.ov a{color:var(--ink);text-decoration:none}.ov a:hover{color:var(--brand-ink)}
.ov td.n{font-family:var(--disp);font-style:italic;font-weight:700;font-size:16px}
.vtag{display:inline-block;font-size:12px;border-radius:999px;padding:1px 8px;background:var(--surface-3);color:var(--ink-3)}
.vtag.on{background:var(--brand-soft);color:var(--brand-ink)}
section.group{background:var(--surface);border:1px solid var(--line);border-radius:12px;margin:18px 0;overflow:hidden}
section.group.done{border-color:var(--line-strong)}
.g-head{padding:16px 20px 12px;display:grid;grid-template-columns:1fr auto;gap:4px 16px;align-items:start}
.g-head h2{margin:0;font-size:17px}.g-head .what{color:var(--ink-2);font-size:13.5px}
.g-head .meta{grid-row:span 2;text-align:right;color:var(--ink-3);font-size:12px;white-space:nowrap}
.g-head .meta .num{display:block;font-size:26px;line-height:1}
.imp{font-size:12px;border:1px solid var(--line-strong);border-radius:999px;padding:0 8px;color:var(--ink-2)}
.imp.high{border-color:var(--brand);color:var(--brand-ink)}
.who{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.who span{font-size:12px;background:var(--surface-2);border-radius:999px;padding:1px 9px;color:var(--ink-2)}
.who span b{font-family:var(--disp);font-style:italic;font-size:14px;margin-left:3px;color:var(--ink)}
.verdict{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:0 20px 14px}
.verdict button{border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:999px;padding:5px 14px;cursor:pointer;transition:background .2s,border-color .2s,color .2s}
.verdict button:hover{border-color:var(--brand);color:var(--brand-ink)}
.verdict button.on{background:var(--brand-deep);border-color:var(--brand-deep);color:oklch(.99 .003 60);box-shadow:0 2px 0 oklch(.42 .15 37)}
.verdict .note-btn{margin-left:auto;background:transparent;border-color:transparent;color:var(--ink-3)}
.notes{padding:0 20px}.note{background:var(--brand-tint);border:1px solid var(--brand-soft);border-radius:8px;padding:6px 10px;margin:0 0 8px;font-size:13px;white-space:pre-wrap}
.note .k{font-size:11.5px;color:var(--brand-ink);margin-right:6px}.note a{color:var(--ink-3);font-size:11.5px;margin-left:8px}
.form{margin:0 20px 12px;border:1px solid var(--line-strong);border-radius:10px;padding:10px;background:var(--surface-2)}
.form textarea{width:100%;min-height:64px;border:1px solid var(--line-strong);border-radius:8px;padding:8px;font:inherit;background:var(--surface)}
.form .row{display:flex;gap:8px;margin-top:8px}.form button{border-radius:999px;padding:4px 14px;border:1px solid var(--line-strong);background:var(--surface);cursor:pointer}
.form button.primary{background:var(--brand-deep);border-color:var(--brand-deep);color:oklch(.99 .003 60)}
.cases{border-top:1px solid var(--line)}
.case{display:grid;grid-template-columns:minmax(220px,360px) 1fr;gap:18px;padding:16px 20px;border-bottom:1px solid var(--line)}
.case:last-child{border-bottom:0}.case.more{display:none}.group.open .case.more{display:grid}
.case img{max-width:100%;max-height:340px;width:auto;border-radius:8px;border:1px solid var(--line);background:var(--surface-3);display:block}
.case .truth{font-size:13px;color:var(--ink-3)}.case .truth b{display:block;font-size:22px;color:var(--ink);font-weight:700;line-height:1.3;word-break:break-all}
.reads{display:grid;gap:6px;margin:12px 0 10px}
.read{display:flex;align-items:baseline;gap:10px;border-radius:8px;padding:6px 10px;font-size:13.5px}
.read .b{width:76px;flex:none;color:var(--ink-2);font-size:12.5px}
.read.ok{background:var(--good-soft)}.read.ok .v::before{content:"✓ ";color:var(--good);font-weight:700}
.read.bad{background:var(--bad-soft)}.read.bad .v::before{content:"读成 ";color:var(--bad)}
.read.miss{background:var(--surface-2);color:var(--ink-3)}
.case .src{font:12px var(--mono);color:var(--ink-3)}.case .src a{color:var(--brand-ink)}
.toggle{display:block;width:100%;border:0;border-top:1px solid var(--line);background:var(--surface-2);color:var(--ink-2);padding:10px;cursor:pointer;font-size:13px}
.toggle:hover{color:var(--brand-ink)}
.hidden{display:none!important}
@media (max-width:760px){.case{grid-template-columns:1fr}.filters{margin-left:0}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS_PAGE = r"""
const API = "api/comments", VKINDS = ["识别逻辑要改", "真值 / 定义问题", "可以接受"];
let all = [];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mine = () => all.filter((c) => c.target === "failures");
function verdictOf(key) { const v = mine().filter((c) => c.field === key && VKINDS.includes(c.kind)).sort((a, b) => a.created - b.created); return v.length ? v[v.length - 1] : null; }
async function post(body) { const r = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw new Error(r.status); }
async function load() {
  try { const r = await fetch(API, { cache: "no-store" }); if (!r.ok) throw new Error(r.status); all = await r.json(); }
  catch (e) { document.getElementById("status").textContent = "意见服务连不上（" + e.message + "），判定和备注提交不了"; }
  render();
}
function render() {
  let done = 0; const groups = document.querySelectorAll("section.group");
  groups.forEach((g) => {
    const key = g.dataset.key, v = verdictOf(key);
    g.classList.toggle("done", !!v); if (v) done++;
    g.querySelectorAll(".verdict button[data-verdict]").forEach((b) => b.classList.toggle("on", !!v && b.dataset.verdict === v.kind));
    const notes = mine().filter((c) => c.field === key && !VKINDS.includes(c.kind));
    g.querySelector(".notes").innerHTML = notes.map((c) => `<div class="note"><span class="k">${esc(c.kind)}</span>${esc(c.text)}<a href="#" class="del" data-id="${esc(c.id)}">删除</a></div>`).join("");
    const row = document.querySelector(`tr[data-key="${CSS.escape(key)}"] .vtag`);
    if (row) { row.textContent = v ? VLABEL[v.kind] : "未判定"; row.classList.toggle("on", !!v); }
  });
  document.getElementById("done").textContent = done;
  document.querySelector(".progress .bar i").style.width = (groups.length ? done / groups.length * 100 : 0) + "%";
  filt();
}
async function verdict(btn) {
  const g = btn.closest("section.group"), key = g.dataset.key;
  const cur = verdictOf(key);
  try {
    if (cur && cur.kind === btn.dataset.verdict) await post({ id: cur.id, deleted: true });
    else await post({ target: "failures", field: key, kind: btn.dataset.verdict, text: btn.textContent.trim(), quote: g.dataset.title });
  } catch (e) { alert("提交失败：" + e.message); return; }
  await load();
}
function note(btn) {
  const g = btn.closest("section.group");
  const old = g.querySelector(".form"); if (old) { old.remove(); return; }
  const f = document.createElement("div"); f.className = "form";
  f.innerHTML = `<textarea placeholder="写备注，比如：这项其实不用读 / 第 3 处真值错了（⌘+Enter 提交）"></textarea><div class="row"><button class="primary">提交</button><button type="button">取消</button></div>`;
  const [ta, ok, cancel] = [f.querySelector("textarea"), ...f.querySelectorAll("button")];
  const send = async () => { const t = ta.value.trim(); if (!t) return; ok.disabled = true;
    try { await post({ target: "failures", field: g.dataset.key, kind: "其他", text: t, quote: g.dataset.title }); } catch (e) { ok.disabled = false; alert("提交失败：" + e.message); return; }
    f.remove(); await load(); };
  ok.onclick = send; cancel.onclick = () => f.remove();
  ta.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) send(); });
  g.querySelector(".verdict").insertAdjacentElement("afterend", f); ta.focus();
}
// 删除链接用 data-id + 委托监听，不把评论 id 拼进内联脚本（id 是投稿者给的，拼进去就是 XSS）
document.addEventListener("click", (ev) => { const a = ev.target.closest("a.del"); if (a) { ev.preventDefault(); del(a.dataset.id); } });
async function del(id) { if (!confirm("删除这条备注？")) return; await post({ id, deleted: true }); await load(); }
function more(btn) { const g = btn.closest("section.group"); g.classList.toggle("open"); btn.textContent = g.classList.contains("open") ? "收起" : btn.dataset.label; }
function filt() {
  const sc = document.getElementById("f-sc").value, cat = document.getElementById("f-cat").value, todo = document.getElementById("f-todo").checked;
  document.querySelectorAll("section.group").forEach((g) => g.classList.toggle("hidden", (sc && g.dataset.sc !== sc) || (cat && g.dataset.cat !== cat) || (todo && g.classList.contains("done"))));
  document.querySelectorAll("tr[data-key]").forEach((r) => r.classList.toggle("hidden", (sc && r.dataset.sc !== sc) || (cat && r.dataset.cat !== cat)));
}
load();
"""


def render(groups: list[dict], backends, out: str | Path, dataset: str | Path | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    e = html.escape
    bname = lambda b: BACKEND_NAME.get(b, b)  # noqa: E731
    secs, ov = [], []
    for g in groups:
        key = f"{g['category']}|{g['item']}"
        title = _title(g["item"])
        sid = g["item"].split(" ", 1)[0]
        n = len(g["instances"])
        who = "".join(f"<span>{e(bname(b))}<b>{g['failing'].get(b, 0)}</b></span>" for b in backends)
        cases = []
        for k, x in enumerate(g["instances"]):
            src = None
            if dataset is not None:
                src = _crop(Path(dataset), x, out.parent / "crops")
            src = src or f"img/{x['frame']}.jpg"
            reads = []
            for b in backends:
                r = x["results"][b]
                cls = {"对": "ok", "错": "bad", "没读": "miss"}[r]
                val = "没读" if r == "没读" else plain(x["got"][b])
                reads.append(
                    f'<div class="read {cls}"><span class="b">{e(bname(b))}</span><span class="v">{e(val)}</span></div>'
                )
            page = CHECK_PAGE.get(x["set"], "check.html")
            where = _where(x["path"])
            cases.append(
                f'<div class="case{" more" if k >= 4 else ""}"><a href="{page}#{e(x["frame"])}" target="_blank" title="在核对页看整帧"><img src="{e(src)}" loading="lazy" alt=""></a>'
                f'<div><div class="truth">画面上{e(" " + where if where else "")}<b>{e(plain(x["want"]))}</b></div><div class="reads">{"".join(reads)}</div>'
                f'<div class="src">{"核心集" if x["set"] == "core" else "进阶集"} · {e(x["frame"])} · <a href="{page}#{e(x["frame"])}" target="_blank">看整帧 ›</a></div></div></div>'
            )
        toggle = (
            f'<button class="toggle" data-label="再看 {n - 4} 处" onclick="more(this)">再看 {n - 4} 处</button>'
            if n > 4
            else ""
        )
        imp = g["importance"]
        secs.append(
            f'<section class="group" id="{e(key)}" data-key="{e(key)}" data-title="{e(title)}" data-sc="{e(sid)}" data-cat="{e(g["category"])}">'
            f'<div class="g-head"><h2>{e(title)}</h2><div class="meta"><span class="num">{n}</span>处</div>'
            f'<div class="what">{e(CAT_TITLE[g["category"]])} · <span class="imp{" high" if imp == "高" else ""}">重要度 {e(imp)}</span></div>'
            f'<div class="who" title="各 OCR 不通过的处数">{who}</div></div>'
            f'<div class="verdict">'
            + "".join(f'<button data-verdict="{k}" onclick="verdict(this)">{lab}</button>' for k, lab in VERDICTS)
            + f'<button class="note-btn" data-target="failures" data-field="{e(key)}" onclick="note(this)">写备注</button></div>'
            f'<div class="notes"></div><div class="cases">{"".join(cases)}</div>{toggle}</section>'
        )
        ov.append(f'<tr data-key="{e(key)}" data-sc="{e(sid)}" data-cat="{e(g["category"])}"><td><a href="#{e(key)}">{e(title)}</a></td>'
                  f'<td>{e(CAT_TITLE[g["category"]])}</td><td>{e(imp)}</td><td class="n">{n}</td><td><span class="vtag">未判定</span></td></tr>')  # fmt: skip
    legend = "".join(
        f'<div><span class="num">{sum(len(g["instances"]) for g in groups if g["category"] == c)}</span><b>{e(CAT_TITLE[c])}</b><span>{e(CAT_HINT[c])}</span></div>'
        for c in CATS
    )
    sc_opts = "".join(f'<option value="{k}">{v}</option>' for k, v in SCENARIO_SHORT.items())
    cat_opts = "".join(f'<option value="{c}">{e(CAT_TITLE[c])}</option>' for c in CATS)
    vlabel = json.dumps(dict(VERDICTS), ensure_ascii=False)
    total = sum(len(g["instances"]) for g in groups)
    out.write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        f"<title>识别问题复核</title><style>{CSS}</style>"
        f'<header class="top"><div class="wrap"><h1>识别问题复核</h1><div class="progress">已判定 <span class="num" id="done">0</span> / <span class="num">{len(groups)}</span> 组<span class="bar"><i></i></span></div>'
        f'<div class="filters"><select id="f-sc" onchange="filt()"><option value="">全部场景</option>{sc_opts}</select><select id="f-cat" onchange="filt()"><option value="">全部问题</option>{cat_opts}</select>'
        '<label><input type="checkbox" id="f-todo" onchange="filt()"> 只看没判定的</label></div></div></header>'
        '<main class="wrap"><p class="intro">'
        f"核心集和进阶集里，真实系统在 {e('、'.join(bname(b) for b in backends))} 下没有读对的地方，同一个信息项的问题合成一组（共 {len(groups)} 组、{total} 处）。"
        "每处左边是画面上那一块的放大图，右边是画面上的正确值和各 OCR 后端读到什么。看完一组点一个判定；要补充就写备注。点图片在核对页看整帧。"
        f'<span id="status"></span></p><div class="legend">{legend}</div>'
        f'<div class="sec-title">总览</div><table class="ov"><tr><th>信息项</th><th>问题</th><th>重要度</th><th>处数</th><th>判定</th></tr>{"".join(ov)}</table>'
        f'<div class="sec-title">逐组复核</div>{"".join(secs)}</main>'
        f"<script>const VLABEL = {vlabel};{JS_PAGE}</script></html>",
        encoding="utf-8",
    )
    return out


__all__ = ["CATS", "aggregate", "plain", "render"]
