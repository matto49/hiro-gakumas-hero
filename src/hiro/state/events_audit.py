# 来源：digital-hiro src/hiro/state/events_audit.py（develop ce5085f）
"""预测器离线审计：用实机 trace 的选行动页序列核对 LESSON / STUDY / OUTING / GIFT / REST 的预测。

    PYTHONPATH=src python -m hiro.state.events_audit traces/lead35-qualify6-20261031-232418 [更多 trace 目录…] [--until 6] [--json 输出]

做法：相邻两个「做了决策的选行动页」之间，主事件由决策推出（training → LESSON、class → STUDY、outing → OUTING、gift → GIFT、rest → REST），
授業 / 外出的档位取自中间的 STUDY / OUTING 决策；中间每次三选一拿卡记一次ワッペン。用前一页的 produce 预测，和后一页的读数比。
相談日（P 点、牌组变化没有事件）跳过 P 点；读数缺失的字段不计。验收（任务单）：选拔① 之前一致率 ≥ 90%。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from hiro.state.events import SUPPORT_SLACK, check_value, make_event, predict

FIELDS = ("produce.params.Vo", "produce.params.Da", "produce.params.Vi", "produce.stamina", "produce.produce_point", "produce.star")
OPTION_EVENT = {"training": "LESSON", "class": "STUDY", "outing": "OUTING", "gift": "GIFT", "rest": "REST"}


def _steps(trace_dir: Path) -> list[dict]:
    path = trace_dir / "steps.jsonl"
    return [json.loads(line) for line in path.open(encoding="utf-8")] if path.exists() else []


def _choice(step: dict) -> str | None:
    """STUDY / OUTING 决策选的是哪一档：A / B / trouble（授業），paid / trouble / free（外出）。"""
    from hiro.brain.deciders import _event_roles

    d = step.get("decision") or {}
    sel = ((d.get("action") or {}).get("args") or {}).get("select")
    choices = (step["state"].get("page_fields") or {}).get("choices") or []
    if sel is None:
        return None
    if not choices:
        return None
    roles = _event_roles(choices)
    page = step["state"]["page"]["name"]
    if sel == roles["trouble"]:
        return "trouble"
    if page == "OUTING":
        return "paid" if sel == roles["paid"] else "free"
    plain = roles["plain"]
    return "A" if plain and sel == plain[0] else "B"


def _value(prod: dict, field: str):
    cur = prod
    for part in field.split(".")[1:]:
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def audit(trace_dirs: list[Path], until: int | None = 6) -> dict:
    """返回 {fields: {字段: Counter(verdict)}, events: {事件: Counter}, rows: [...逐条]}。"""
    rows = []
    for t in trace_dirs:
        steps = _steps(t)
        acts = [i for i, s in enumerate(steps) if s["state"]["page"]["name"] == "ACTION_SELECT" and (s.get("decision") or {}).get("action", {}).get("kind") == "ProduceNext"]
        for a, b in zip(acts, acts[1:] + [None]):
            s = steps[a]
            prod = dict(s["state"].get("produce") or {})
            if not prod.get("step") or (until is not None and prod["step"] > until) or prod.get("mode") not in (None, "produce-007"):
                continue
            prod.setdefault("mode", "produce-007")
            args = s["decision"]["action"]["args"]
            kind = OPTION_EVENT.get(args.get("option"))
            if kind is None:
                continue
            # 下一张已有读数的选行动页（同一步多帧取第一帧：决策后的那帧还是旧值）
            nxt = next((steps[j] for j in range(a + 1, len(steps)) if steps[j]["state"]["page"]["name"] == "ACTION_SELECT"
                        and (steps[j]["state"].get("produce") or {}).get("step") not in (None, prod["step"])), None)  # fmt: skip
            if nxt is None or nxt["state"]["produce"]["step"] != prod["step"] + 1:
                continue  # 中间隔着考试（第 7 / 13 / 20 天）：不是单一事件的结果
            between = steps[a + 1 : steps.index(nxt)]
            ev_args = {"attr": args.get("attr"), "sp": args.get("sp")}
            for st in between:
                if st["state"]["page"]["name"] in ("STUDY", "OUTING") and st.get("decision"):
                    c = _choice(st)
                    if c:
                        ev_args["choice" if kind == "STUDY" else "tier"] = c
            picks = sum(1 for st in between if st["state"]["page"]["name"] == "SELECT_CARD"
                        and (st.get("decision") or {}).get("action", {}).get("kind") == "ActivateEffect")  # fmt: skip
            drinks = sum(1 for st in between if st["state"]["page"]["name"] == "SELECT_DRINK"
                         and (st.get("decision") or {}).get("action", {}).get("kind") == "ActivateEffect")  # fmt: skip
            preds = predict({"produce": prod}, make_event(kind, ev_args))
            fields = {k: {kk: vv for kk, vv in vars(v).items() if vv is not None} for k, v in preds[0].fields.items()} if preds else {}
            obs = nxt["state"].get("produce") or {}
            shop = any(st["state"]["page"]["name"] == "CONSULT" for st in between)
            for f in FIELDS:
                exp = fields.get(f)
                got = _value(obs, f)
                if exp is None or got is None:
                    continue
                if f == "produce.produce_point" and shop:
                    continue
                if f == "produce.star" and picks:
                    exp = dict(exp)  # 中间每拿一张卡触发一次ワッペン：+10 × star_permil（倍率未识别时按预测器的区间上下界）
                    lo = exp.get("value", exp.get("min"))
                    hi = exp.pop("value", None) or exp.get("max")
                    exp["min"], exp["max"], exp["exact"] = lo + picks * 10, hi + picks * 20, False
                if f.startswith("produce.params.") and (picks or drinks) and "max" in exp:
                    exp = dict(exp)  # 拿卡 / 拿饮料也会触发支援卡技能（「スキルカード獲得時」「Pドリンク獲得時」）
                    exp["max"] += picks * SUPPORT_SLACK["card"] + drinks * SUPPORT_SLACK["drink"]
                ok = check_value(exp, got)
                excess = got - (exp.get("value", exp.get("min")) or 0) if isinstance(got, (int, float)) else None
                rows.append({"trace": t.name, "day": prod["step"], "event": kind, "args": ev_args, "field": f, "expect": exp, "observed": got,
                             "verdict": "consistent" if ok else "mismatch", "excess": excess})  # fmt: skip
    out_f, out_e = defaultdict(Counter), defaultdict(Counter)
    for r in rows:
        out_f[r["field"]][r["verdict"]] += 1
        out_e[r["event"]][r["verdict"]] += 1
    total = Counter(r["verdict"] for r in rows)
    return {"total": dict(total), "rate": total["consistent"] / max(1, len(rows)), "fields": {k: dict(v) for k, v in out_f.items()},
            "events": {k: dict(v) for k, v in out_e.items()}, "rows": rows}  # fmt: skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--until", type=int, default=6, help="只审计到第几天（默认 6：选拔① 之前）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    res = audit([Path(t) for t in args.traces], args.until)
    print(f"一致 {res['total'].get('consistent', 0)} / {sum(res['total'].values())}（{res['rate']:.0%}）")
    for k, v in res["events"].items():
        print(f"  {k}: {v}")
    for r in res["rows"]:
        if r["verdict"] != "consistent":
            print(f"  ✗ {r['trace']} 第 {r['day']} 天 {r['event']} {r['args']} {r['field']}：期望 {r['expect']} 读数 {r['observed']}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
