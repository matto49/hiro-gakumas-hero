"""离线对比：GKMS 育成插件 vs 实机记录里现有决策器的选择（GKMS 插件 spec 4.10）。

    PYTHONPATH=src python tools/gkms_compare.py <trace 目录…> [--out reports/gkms-compare/<日期>]

读每段 trace 的 steps.jsonl，在选行动和选卡类页面上用记录里的局面问 GKMS 插件，和当时实际的选择比：
- 两边选得一不一样（按模式、页面统计）；
- 选行动：实机的选择在 GKMS 路线模型里比最优差多少（vs_best）；
- 选卡：两边所选卡的 V（pickv 选卡包）；
- GKMS 不管的次数和原因。

只换一步、看不到后面的连锁，打分用的是 GKMS 自己的模型和 V，天然偏向 GKMS：只能说明「接得上、在哪里意见不同」，证明不了谁打得更好。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

from hiro.contracts import GameState
from hiro.gkms import state_map

PAGES = ("ACTION_SELECT", "SELECT_CARD", "SKILL_CARD_CHANGE_1", "SKILL_CARD_CHANGE_2", "SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE",
         "CONSULT", "HIF_ROUND_INTERVAL")  # fmt: skip


def _rows(trace: Path):
    f = trace / "steps.jsonl"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _key(action: dict | None) -> tuple:
    """动作 → 可比较的键（种类 + 决定性的参数）。"""
    if not action:
        return (None,)
    kind, a = action.get("kind"), action.get("args") or {}
    if kind == "ProduceNext":
        return (kind, a.get("option"), a.get("attr"), bool(a.get("sp")))
    if kind == "ActivateEffect":
        return (kind, tuple(a.get("pick_indexes") or ()))
    if kind == "ChangeCard":
        return (kind, a.get("stage"), a.get("pick_index"), a.get("remove_card_id"), a.get("cancel"))
    if kind in ("RemoveCard", "EnhanceCard"):
        return (kind, a.get("card_id"), a.get("cancel"))
    return (kind, a.get("index"), a.get("service"), a.get("card_index"))


def _recorded_index(state: GameState, action: dict) -> int | None:
    a = action.get("args") or {}
    for i, o in enumerate(state.page_fields.get("options") or []):
        if o.get("option") == a.get("option") and (o.get("attr") or None) == (a.get("attr") or None) and bool(o.get("sp")) == bool(a.get("sp")):
            return i
    return None


def compare_step(trace: Path, row: dict, adv) -> dict | None:
    st = row.get("state") or {}
    page = (st.get("page") or {}).get("name")
    if page not in PAGES:
        return None
    try:
        state = GameState.from_dict(st)
    except Exception:  # noqa: BLE001  老格式的局面读不了就跳过
        return None
    rec_action = (row.get("decision") or {}).get("action")
    out = {"trace": trace.name, "seq": row.get("seq"), "page": page, "mode": (state.produce or {}).get("mode"),
           "recorded": (row.get("decision") or {}).get("reason"), "recorded_key": _key(rec_action)}  # fmt: skip
    adv.last_why = None
    t0 = time.monotonic()
    if page in adv.card_scope_pages:
        p = (state.page_fields or {}).get("produce_point") or (state.produce or {}).get("produce_point") or 0
        d = adv.card_decide(page, state, budget=int(p), kw={})
    else:
        d = adv.decide(page, state, n_options=None, kw={})
    out["seconds"] = round(time.monotonic() - t0, 3)
    if d is None:
        out.update(gkms=None, deferred=adv.last_why or "插件不管（没有可做的）", same=None)
        return out
    gk = {"kind": d.action.kind.value, "args": d.action.args}
    out.update(gkms_reason=d.reason, gkms_key=_key(gk), same=_key(gk) == out["recorded_key"])
    if page == "ACTION_SELECT":
        out["gkms"] = (adv.last_route or {}).get("choice")
        dec = (adv.last_route or {}).get("decision") or {}
        ri = _recorded_index(state, rec_action or {})
        opts = state.page_fields.get("options") or []
        out["recorded_gap"] = next((o.get("vs_best") for o in dec.get("options") or [] if ri is not None and state_map.option_for(o["choice"], opts) == ri), None)
    elif page == "SELECT_CARD":
        cands = state.page_fields.get("candidates") or []
        take = (adv.last_values or {}).get("take") or {}
        ri = ((rec_action or {}).get("args") or {}).get("pick_indexes") or [None]
        gi = (d.action.args.get("pick_indexes") or [None])[0]
        cid = lambda i: cands[i].get("card_id") if isinstance(i, int) and 0 <= i < len(cands) else None  # noqa: E731
        out["gkms"] = cid(gi) or d.action.kind.value
        out["v_recorded"], out["v_gkms"] = take.get(cid(ri[0])), take.get(cid(gi))
    else:
        out["gkms"] = d.action.kind.value
    return out


def _trace_idol(trace: Path, default: str | None) -> str | None:
    try:
        return json.loads((trace / "run.json").read_text(encoding="utf-8")).get("idol") or default
    except (OSError, ValueError):
        return default


def compare(traces, adv, *, idol_default: str | None = None) -> list[dict]:
    res = []
    for t in traces:
        adv.context["idol"] = _trace_idol(Path(t), idol_default)
        for row in _rows(Path(t)):
            if (r := compare_step(Path(t), row, adv)) is not None:
                res.append(r)
    return res


def report(res: list[dict], *, title: str = "GKMS 插件离线对比") -> str:
    lines = [f"# {title}", "",
             "> 只换一步、看不到后面的连锁；打分用的是 GKMS 自己的模型和 pickv 的 V，天然偏向 GKMS。",
             "> 这份报告只能说明「接得上、在哪里意见不同」，证明不了谁打得更好；要证明更好得实机对比（另外请示）。",
             "> 「现有」一列是实机记录里当时的选择（规则 / LLM），不是重新跑的。", ""]  # fmt: skip
    groups = collections.defaultdict(list)
    for r in res:
        groups[(r["mode"], r["page"])].append(r)
    lines += ["## 总表", "", "| 模式 | 页面 | 步数 | GKMS 不管 | 一致 | 不一致 |", "|---|---|---|---|---|---|"]
    for (mode, page), rs in sorted(groups.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        n_def = sum(r["gkms"] is None for r in rs)
        n_same = sum(r.get("same") is True for r in rs)
        n_diff = sum(r.get("same") is False for r in rs)
        lines.append(f"| {mode} | {page} | {len(rs)} | {n_def} | {n_same} | {n_diff} |")
    acts = [r for r in res if r["page"] == "ACTION_SELECT" and r.get("recorded_gap") is not None]
    if acts:
        gaps = [r["recorded_gap"] for r in acts]
        lines += ["", "## 选行动：实机的选择在 GKMS 模型里比最优差多少", "",
                  f"{len(acts)} 步有评分；平均差 {sum(gaps) / len(gaps):.1f}，最差 {min(gaps):.1f}，和 GKMS 一样（差 0）的 {sum(g == 0 for g in gaps)} 步。"]  # fmt: skip
    cards = [r for r in res if r["page"] == "SELECT_CARD" and r.get("v_gkms") is not None]
    if cards:
        rv = [r["v_recorded"] for r in cards if r.get("v_recorded") is not None]
        gv = [r["v_gkms"] for r in cards]
        lines += ["", "## 三选一：两边所选卡的 V", "",
                  f"{len(cards)} 步；GKMS 所选平均 V {sum(gv) / len(gv):.3f}"
                  + (f"，实机所选平均 V {sum(rv) / len(rv):.3f}（{len(rv)} 步能对上卡）" if rv else "") + "。"]  # fmt: skip
    why = collections.Counter(r["deferred"] for r in res if r["gkms"] is None)
    if why:
        lines += ["", "## GKMS 不管的原因", "", "| 原因 | 次数 |", "|---|---|"]
        lines += [f"| {w} | {n} |" for w, n in why.most_common(15)]
    diffs = [r for r in res if r.get("same") is False][:20]
    if diffs:
        lines += ["", "## 不一致的例子（前 20 条）", "", "| trace | seq | 页面 | 现有 | GKMS |", "|---|---|---|---|---|"]
        for r in diffs:
            cut = lambda s: str(s or "").replace("|", "／").replace("\n", " ")[:90]  # noqa: E731
            lines.append(f"| {r['trace']} | {r['seq']} | {r['page']} | {cut(r['recorded'])} | {cut(r.get('gkms_reason'))} |")
    secs = [r["seconds"] for r in res if r.get("seconds") is not None]
    if secs:
        lines += ["", f"GKMS 每步耗时：平均 {sum(secs) / len(secs):.2f} 秒，最长 {max(secs):.2f} 秒（共 {len(secs)} 步）。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--idol-default", default=None, help="run.json 没写偶像卡的 trace 按这张算（报告里写明是假设）")
    a = ap.parse_args(argv)
    from hiro.gkms.advisor import GkmsProduceAdvisor

    adv = GkmsProduceAdvisor()
    adv.begin({"trace_dir": None})
    try:
        res = compare([Path(t) for t in a.traces if Path(t).is_dir()], adv, idol_default=a.idol_default)
    finally:
        adv.end(None)
    out = Path(a.out or f"reports/gkms-compare/{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in res) + "\n", encoding="utf-8")
    md = report(res)
    if a.idol_default:
        md = md.replace("\n## 总表", f"\n> run.json 没写偶像卡的 trace 按 {a.idol_default} 算（假设）。\n\n## 总表", 1)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(out / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
