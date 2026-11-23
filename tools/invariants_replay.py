"""不变量回放验收（docs/tasks/H1-state-events.md 数据确认部分）。

一局的 trace 按段（<run>、<run>-r2、…-r10）顺序接起来，状态跨段延续；每个观测 reduce 前先跑 check_invariants，
read_error 的字段用 filter_observation 去掉后再 reduce，统计：
- 抓到的读错（按规则）与 model_gap；
- 决策点（有 Decision 的 Step）看到的 produce.params / exam.score 是否已排除读错；
- 牌组在每个决策点是否已知（produce.deck）——事件记账接上前应全部为「未知」，用来列缺口；
- 改变牌组 / P 点的决策（选卡、授業 / 外出、相談、删卡、换卡、轮间）清单：事件表需要覆盖的动作。

    PYTHONPATH=src .venv/bin/python tools/invariants_replay.py traces/<run> [...]
    （传一局的第一段即可，后续 -rN 段自动接上；--json 输出明细）
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from hiro.contracts import GameState, Observation
from hiro.loop.reduce import reduce
from hiro.state.invariants import check_invariants, filter_observation

DECK_DECIDERS = {
    "pick",
    "select_card",
    "study",
    "outing",
    "consult",
    "removal",
    "change",
    "interval",
    "skill_card_change",
}


def segments(first: Path) -> list[Path]:
    """<run> 与 <run>-rN，按 N 排序。"""
    base = re.sub(r"-r\d+$", "", first.name)
    segs = [
        p for p in first.parent.glob(base + "*") if p.name == base or re.fullmatch(re.escape(base) + r"-r\d+", p.name)
    ]
    return sorted(segs, key=lambda p: int(p.name.rsplit("-r", 1)[1]) if p.name != base else 1)


def replay(first: Path) -> dict:
    state = GameState()
    checks, decisions, deck_actions = [], [], []
    for seg in segments(first):
        for line in open(seg / "steps.jsonl", encoding="utf-8"):  # noqa: SIM115
            step = json.loads(line)
            raws = step.get("observations_before", []) + [step["observation"]] + step.get("observations_after", [])
            for raw in raws:
                obs = Observation.from_dict(raw)
                found = check_invariants(state, obs)
                checks += [{**c.to_dict(), "segment": seg.name, "screenshot": raw.get("screenshot")} for c in found]
                state = reduce(state, filter_observation(obs, found, state))
            d = step.get("decision")
            if d:
                produce = state.produce or {}
                decisions.append({
                    "segment": seg.name, "seq": step["seq"], "page": state.page.name, "decider": d.get("decider"),
                    "params": produce.get("params"), "score": (state.exam or {}).get("score"),
                    "deck_known": produce.get("deck") is not None,
                })  # fmt: skip
                kind = (d.get("action") or {}).get("kind")
                if d.get("decider") in DECK_DECIDERS or kind in ("PickCard", "ShopBuy", "RemoveCard", "ChangeCard"):
                    deck_actions.append({"segment": seg.name, "seq": step["seq"], "decider": d.get("decider"), "action": kind,
                                         "args": (d.get("action") or {}).get("args")})  # fmt: skip
    return {"run": first.name, "segments": [p.name for p in segments(first)], "checks": checks,
            "decisions": decisions, "deck_actions": deck_actions}  # fmt: skip


def report(res: dict) -> str:
    lines = [f"## {res['run']}（{len(res['segments'])} 段）"]
    by = Counter(f"{c['rule']}:{c['verdict']}" for c in res["checks"])
    lines.append("规则命中：" + ("、".join(f"{k} ×{v}" for k, v in sorted(by.items())) or "无"))
    for c in res["checks"]:
        if c["verdict"] == "read_error":
            lines.append(
                f"- read_error {c['segment']} seq {c['seq']} {c['field']}：读数 {c['observed']}，期望 {c['predicted']}（{c['rule']}）"
            )
    gaps = [c for c in res["checks"] if c["verdict"] == "model_gap"]
    if gaps:
        lines.append(
            f"model_gap {len(gaps)} 条："
            + "；".join(f"{c['segment']} seq {c['seq']} {c['field']} {c.get('note') or ''}" for c in gaps[:8])
        )
    dec = res["decisions"]
    lines.append(f"决策点 {len(dec)} 个，牌组已知 {sum(d['deck_known'] for d in dec)} 个")
    lines.append(
        f"改变牌组 / P 点的决策 {len(res['deck_actions'])} 个："
        + "、".join(f"{k} ×{v}" for k, v in Counter(a["decider"] or a["action"] for a in res["deck_actions"]).items())
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    results = [replay(p) for p in args.runs]
    for r in results:
        print(report(r), "\n")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
