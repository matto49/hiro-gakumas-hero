"""考试复盘页：把一局的 trace（含 -rN 续跑段）生成单个本地 HTML，逐回合展示出牌、饮料、专家候选、状态曲线，并可在步骤上写批注导出。

用法：
    .venv/bin/python tools/viewer/build_exam_view.py <run>
    .venv/bin/python tools/viewer/build_exam_view.py <run> --out reports/viewer

数据都嵌在 HTML 里（含截图缩略图），可以单独拷到手机上打开。

有低级失误报告（tools/audit/blunders.py 产出的 reports/analysis/*-blunders.json，segments 含本局）时自动读入：
页首列出失误汇总，失误步在回合条和步骤上标红。也可以用 --blunders 指定文件。
每场考试的开局牌库（手牌 + 山札 + 弃牌，按一次性 / 再動 / 塞眠気标注，R2 对比 R1 的增减）见 deck_view.py。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).with_name("exam_view.html")
THUMB_W = 270
EXAM_PAGES = {"HIF_EXAM"}
RESULT_PAGES = {"HIF_EXAM_RESULT", "HIF_FINALS_RANKING"}


def _segments(traces: Path, base: str) -> list[Path]:
    segs = [traces / base] + sorted(traces.glob(base + "-r*"), key=lambda p: int(p.name.rsplit("-r", 1)[1]))
    return [s for s in segs if (s / "steps.jsonl").exists()]


def _thumb(path: Path, cache: dict) -> str | None:
    if path in cache:
        return cache[path]
    if not path.exists():
        cache[path] = None
        return None
    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        h = round(im.height * THUMB_W / im.width)
        im = im.resize((THUMB_W, h))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=62, optimize=True)
        cache[path] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        cache[path] = None
    return cache[path]


def _card_role(text: str) -> str | None:
    t = text or ""
    if "元気を0" in t:
        return "finisher"
    if "ターン後" in t:
        return "delayed"
    if "以降" in t and "ターン開始時" in t and "パラメータ上昇" in t:
        return "sustained"
    if "分パラメータ上昇" in t:
        return "cashout"
    return None


def _label(action: dict, ex: dict) -> str:
    kind, args = action.get("kind"), action.get("args") or {}
    if kind == "PlayCard":
        slot = args.get("slot")
        hit = next((c for c in ex.get("hand") or [] if c.get("slot") == slot), None)
        return hit["name"] if hit else f"第 {slot} 张"
    if kind == "UseDrink":
        slot = args.get("slot")
        hit = next((d for d in ex.get("drinks") or [] if isinstance(d, dict) and d.get("slot") == slot), None)
        return f"饮料：{hit['name']}" if hit and hit.get("name") else f"饮料第 {slot} 格"
    if kind == "Skip":
        return "结束回合"
    return kind or "?"


def _preview_label(p: dict) -> str:
    s = (p.get("summary") or {}).get("动作") or ""
    if s.startswith("play:"):
        return s[5:]
    if s.startswith("drink:"):
        return "饮料：" + s[6:]
    if s in ("end_turn", "skip"):
        return "结束回合"
    return s or (p.get("action") or {}).get("kind", "?")


def _statuses(ex: dict) -> list[dict]:
    out = []
    for s in ex.get("statuses") or []:
        if s and s.get("name"):
            out.append({"name": s["name"], "value": s.get("value"), "unit": s.get("unit")})
    for k, v in (ex.get("buffs") or {}).items():
        if not any(o["name"] == k for o in out):
            out.append({"name": k, "value": v, "unit": None})
    return out


def _immediate(im: dict) -> dict:
    """预演的即时结果：依赖抽牌时给的是 [最小, 最大] 范围，这里取中间值并标 approx。"""
    out, approx = {}, False
    for k, v in (im or {}).items():
        if isinstance(v, (list, tuple)) and v and all(isinstance(x, (int, float)) for x in v):
            out[k] = round((min(v) + max(v)) / 2)
            approx = True
        else:
            out[k] = v
    out["approx"] = approx
    return out


def _mult(ex: dict) -> float | None:
    if ex.get("multiplier_pct") is not None:
        return round(ex["multiplier_pct"] / 100, 2)
    if ex.get("multiplier_permil") is not None:
        return round(ex["multiplier_permil"] / 1000, 2)
    return None


def find_blunders(base: str, analysis: Path = ROOT / "reports" / "analysis") -> Path | None:
    """reports/analysis 下 segments 含本局（基名或其 -rN 段）的失误报告；有多份取最新。"""
    hits = []
    for p in analysis.glob("*-blunders.json"):
        try:
            segs = json.loads(p.read_text(encoding="utf-8")).get("segments") or []
        except (OSError, ValueError):
            continue
        if any(re.sub(r"-r\d+$", "", x) == base for x in segs):
            hits.append(p)
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def attach_blunders(data: dict, report: dict) -> None:
    """把失误挂到考试步上（按 段 + seq），并在 data["blunders"] 里给页首汇总；不在考试视图里的失误也列出（无跳转）。"""
    base = data["run"]
    where = {}
    for ei, e in enumerate(data["exams"]):
        for si, s in enumerate(e["steps"]):
            seg = base if s["seg"] == "原段" else base + s["seg"]
            where[(seg, s["seq"])] = (ei, si)
            s["blunders"] = []
    items = []
    for f in report.get("findings") or []:
        loc = where.get((f.get("segment"), f.get("seq")))
        item = {"category": f["category"], "kind": f["kind"], "label": f["label"], "detail": f["detail"], "seq": f.get("seq"),
                "seg": (f.get("segment") or "")[len(base):] or "原段", "at": list(loc) if loc else None}  # fmt: skip
        items.append(item)
        if loc:
            data["exams"][loc[0]]["steps"][loc[1]]["blunders"].append(
                {k: item[k] for k in ("category", "kind", "label", "detail")}
            )
    counts = [{"category": c, "kind": k, "label": label, "n": n} for c, kinds in (report.get("kinds") or {}).items()
              for k, label in kinds.items() if (n := (report.get("counts") or {}).get(c, {}).get(k))]  # fmt: skip
    data["blunders"] = {"name": report.get("name"), "total": len(items), "counts": counts, "items": items}


def build(base: str, traces: Path) -> dict:
    exams: list[dict] = []
    cur: dict | None = None
    prev_rem = None
    pending_result: dict | None = None  # noqa: F841
    thumbs: dict = {}
    for seg in _segments(traces, base):
        for line in (seg / "steps.jsonl").open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            st = d.get("state") or {}
            page = (st.get("page") or {}).get("name")
            # 结果页：挂到最近一场还没有结果的考试上
            for o in [d.get("observation")] + (d.get("observations_after") or []):
                if not o or o.get("page", {}).get("name") not in RESULT_PAGES:
                    continue
                f = o.get("fields") or {}
                if f.get("kind") == "standings" or (f.get("score") is None and not f.get("entries")):
                    continue
                target = next((e for e in reversed(exams) if e.get("result") is None), None)
                if target is not None and target is (exams[-1] if exams else None):
                    shot = seg / o["screenshot"] if o.get("screenshot") else None
                    target["result"] = {
                        "kind": f.get("kind") or "result", "score": f.get("score"), "round_score": f.get("round_score"),
                        "rank": f.get("rank"), "passed": f.get("passed"), "rank_line": f.get("rank_line"),
                        "entries": [{"name": e.get("name"), "score": e.get("score"), "round_score": e.get("round_score"),
                                     "me": bool(e.get("me"))} for e in (f.get("entries") or [])],
                        "thumb": _thumb(shot, thumbs) if shot else None,
                    }  # fmt: skip
                break
            ex = st.get("exam")
            dec = d.get("decision")
            if page not in EXAM_PAGES or not ex or not dec:
                continue
            label = (st.get("exam_session") or {}).get("label") or "exam"
            rem = ex.get("remaining_turns")
            new = (
                cur is None
                or cur["label"] != label
                or (rem is not None and prev_rem is not None and rem > prev_rem + 1)
            )
            if new:
                attempt = 1 + sum(1 for e in exams if e["label"] == label)
                cur = {"label": label, "attempt": attempt, "steps": [], "result": None}
                exams.append(cur)
                prev_rem = None
            turn_idx = (cur["steps"][-1]["turn"] if cur["steps"] else 0) + (1 if rem != prev_rem else 0)
            prev_rem = rem
            act = dec.get("action") or {}
            previews = []
            for p in d.get("previews") or []:
                previews.append({
                    "label": _preview_label(p), "expected": p.get("expected"), "spread": p.get("spread"),
                    "vs_best": p.get("vs_best"), "se": p.get("vs_best_se"), "immediate": _immediate(p.get("immediate")),
                    "chosen": (p.get("action") or {}) == act,
                })  # fmt: skip
            previews.sort(key=lambda p: -(p["expected"] or 0))
            hand = []
            for c in ex.get("hand") or []:
                hand.append({
                    "slot": c.get("slot"), "name": c.get("name"), "text": c.get("text") or "", "badge": c.get("badge_score"),
                    "available": c.get("available"), "upgrade": c.get("upgrade"), "once": bool((c.get("tags") or {}).get("once")),
                    "category": c.get("category"), "cost": c.get("cost"), "hp_cost": c.get("hp_cost"), "role": _card_role(c.get("text")),
                })  # fmt: skip
            drinks = [{"slot": x.get("slot"), "name": x.get("name"), "text": x.get("text") or ""}
                      for x in ex.get("drinks") or [] if isinstance(x, dict) and x.get("name")]  # fmt: skip
            shot = seg / d["observation"]["screenshot"] if (d.get("observation") or {}).get("screenshot") else None
            exe = d.get("execution") or {}
            cur["steps"].append({
                "seq": d.get("seq"), "seg": seg.name[len(base):] or "原段", "t": d.get("t"), "turn": turn_idx, "rem": rem,
                "attr": ex.get("attr"), "mult": _mult(ex), "score": ex.get("score"), "genki": ex.get("genki"), "hp": ex.get("hp"),
                "motivation": ex.get("motivation"), "impression": ex.get("impression"), "npc": ex.get("npc_scores") or [],
                "pitem_charges": ex.get("pitem_charges"), "statuses": _statuses(ex), "hand": hand, "drinks": drinks,
                "action": {"kind": act.get("kind"), "label": _label(act, ex), "args": act.get("args") or {}},
                "source": dec.get("source"), "reason": dec.get("reason") or "", "warnings": dec.get("warnings") or [],
                "plan": [str(x) for x in dec.get("plan") or []], "previews": previews,
                "exec_ok": exe.get("ok"), "exec_error": exe.get("error"), "thumb": _thumb(shot, thumbs) if shot else None,
            })  # fmt: skip
    for e in exams:
        e["turns"] = _turns(e["steps"])
        last = e["steps"][-1] if e["steps"] else {}
        e["final_score"] = (
            (e["result"] or {}).get("round_score") or (e["result"] or {}).get("score") or last.get("score")
        )
    hidden = False  # 2026-11-25 插件架构：任一段用过数据来源插件给的隐藏信息就在标题上标出来
    for seg in _segments(traces, base):
        with contextlib.suppress(OSError, ValueError, AttributeError):
            hidden = hidden or bool(json.loads((seg / "plugins.json").read_text(encoding="utf-8")).get("uses_hidden"))
    return {
        "run": base,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "exams": [e for e in exams if e["steps"]],
        "uses_hidden": hidden,
    }


def _turns(steps: list[dict]) -> list[dict]:
    turns: list[dict] = []
    for k, s in enumerate(steps):
        if not turns or turns[-1]["turn"] != s["turn"]:
            turns.append({"turn": s["turn"], "rem": s["rem"], "attr": s["attr"], "mult": s["mult"], "start": s["score"],
                          "npc": (s["npc"] or [None])[0], "steps": [], "events": []})  # fmt: skip
        t = turns[-1]
        t["steps"].append(k)
        kind = s["action"]["kind"]
        if kind == "UseDrink":
            t["events"].append({"type": "drink", "label": s["action"]["label"]})
        elif kind == "PlayCard":
            card = next((c for c in s["hand"] if c["slot"] == s["action"]["args"].get("slot")), None)
            if card and card["role"]:
                t["events"].append({"type": card["role"], "label": card["name"]})
    for i, t in enumerate(turns):
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        t["end"] = nxt["start"] if nxt else None
        t["gain"] = (t["end"] - t["start"]) if (t["end"] is not None and t["start"] is not None) else None
        # 回合切换得分 = 下一回合开始分 - 本回合最后一步观测分；有预演时减去最后一手的即时得分，剩下的算作回合开始结算
        last = steps[t["steps"][-1]]
        chosen = next((p for p in last["previews"] if p["chosen"]), None)
        if nxt and nxt["start"] is not None and last["score"] is not None:
            switch = nxt["start"] - last["score"]
            imm = (chosen or {}).get("immediate", {}).get("score")
            nxt["settle"] = switch - imm if imm is not None else None
            nxt["switch"] = switch
    return turns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="trace 基名（不带 -rN），例如 run-main11-20261101-153423")
    ap.add_argument("--traces", default=str(ROOT / "traces"))
    ap.add_argument("--out", default=str(ROOT / "reports" / "viewer"))
    ap.add_argument("--blunders", help="低级失误报告 json（默认在 reports/analysis 里找 segments 含本局的）")
    ap.add_argument("--api", default=None, help="标注后端地址（默认相对路径 api/notes，页面和后端同域时用）")
    args = ap.parse_args()
    data = build(args.base, Path(args.traces))
    if not data["exams"]:
        raise SystemExit(f"{args.base}：没有找到考试步骤")
    from deck_view import attach_decks

    attach_decks(data, _segments(Path(args.traces), args.base))
    bl = Path(args.blunders) if args.blunders else find_blunders(args.base)
    if bl is not None and bl.exists():
        attach_blunders(data, json.loads(bl.read_text(encoding="utf-8")))
        print(f"失误报告：{bl}（{data['blunders']['total']} 条）")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    if args.api:
        html = html.replace('/*__API__*/"api/notes"', json.dumps(args.api))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.base}.html"
    path.write_text(html, encoding="utf-8")
    n = sum(len(e["steps"]) for e in data["exams"])
    print(f"{path}（{len(data['exams'])} 场考试，{n} 步，{path.stat().st_size / 1e6:.1f} MB）")


if __name__ == "__main__":
    main()
