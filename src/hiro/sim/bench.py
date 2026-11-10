# 来源：digital-hiro src/hiro/sim/bench.py（develop 864e037）
"""离线批量对局与决策审计。

    .venv/bin/python -m hiro.sim.bench --exam r2 --policies community,brain-rules,expert --seeds 0-9
    .venv/bin/python -m hiro.sim.bench --exam r2 --policies brain-llm --seeds 0-2 --audit
    .venv/bin/python -m hiro.sim.bench --exam r1 --policies brain-rules --seeds 0-4 --hide-live   # 模拟实机读不到干劲/好印象/分数

输出 reports/sim/<时间>-<exam>/：episodes.jsonl（逐局）、decisions.jsonl（--audit 时逐决策）、summary.md。
审计时每个决策点都让推演专家评估全部合法动作，记录「专家最优 − 所选动作」的期望分差（遗憾），
遗憾最大的局面整理成候选黄金用例，供人工确认后写进 brain/cases。
"""

import argparse
import json
import statistics
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from hiro.brain.battle import BattleBrain, decision_record
from hiro.brain.llm import from_spec
from hiro.brain.state import CAMPUS_HIRO
from hiro.sim.env import LIVE_HIDDEN, SimExam
from hiro.sim.policies import BrainPolicy, HeuristicPolicy, RolloutExpert, action_key

EXAMS = {
    "r1": ("campus-20261019/r1-opening", "r1"),
    "r2": ("campus-20261019/r2-opening", "r2"),
}
REPO = Path(__file__).resolve().parents[3]


def parse_seeds(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def make(name: str, exam: SimExam, args):
    if name == "heuristic":
        return HeuristicPolicy(seed=0)
    if name == "expert":
        return RolloutExpert(worlds=args.worlds, meta=exam.exam.meta)
    if name == "expert-preview":
        from hiro.sim.policies import PreviewExpert

        return PreviewExpert(exam.exam.meta, worlds=args.worlds)
    if name in ("brain-llm-preview", "brain-rules-preview"):
        from hiro.sim.policies import PreviewBrainPolicy

        llm = from_spec(f"claude:{args.model}") if name.startswith("brain-llm") else None
        return PreviewBrainPolicy(BattleBrain(CAMPUS_HIRO, llm), exam.exam.meta, worlds=args.worlds)
    if name in ("brain-rules", "brain-llm"):
        llm = from_spec(f"claude:{args.model}") if name == "brain-llm" else None
        return BrainPolicy(BattleBrain(CAMPUS_HIRO, llm))
    raise SystemExit(f"未知策略 {name}")


def play_episode(policy, exam: SimExam, seed: int, args, audit_out=None) -> dict:
    game = exam.episode(seed, hide=LIVE_HIDDEN if args.hide_live else frozenset())
    policy.start(game)
    expert = RolloutExpert(worlds=args.worlds, seed=seed, meta=exam.exam.meta) if audit_out is not None else None
    t0 = time.monotonic()
    steps = llm_calls = 0
    regret_total = 0.0
    n_calls0 = len(getattr(getattr(policy, "brain", None), "calls", []))
    while not game.ended and steps < 500:
        before_turn = game.env.turn
        action, d, state = policy.act(game)
        if expert is not None and d is not None:
            res = expert.evaluate(game.env)
            chosen = action_key(action, game.env)
            best = action_key(res.best, game.env)
            regret, se = res.regret(chosen)
            regret_total += regret
            audit_out.write(json.dumps({
                "policy": policy.name, "seed": seed, "turn": before_turn + 1, "chosen": chosen, "best": best,
                "regret": round(regret), "regret_se": round(se), "values": {k: round(v) for k, v in res.values.items()},
                "source": d.source, "reason": d.reason, **decision_record(state, d),
            }, ensure_ascii=False) + "\n")  # fmt: skip
            audit_out.flush()
        if d is not None and expert is None and audit_out is None and getattr(args, "trace", None) is not None:
            args.trace.write(json.dumps({"policy": policy.name, "seed": seed, "turn": before_turn + 1,
                                         "chosen": action_key(action, game.env), "source": d.source, "reason": d.reason,
                                         "previews": state.action_previews}, ensure_ascii=False) + "\n")  # fmt: skip
            args.trace.flush()
        game.step(action)
        if d is not None:
            policy.after(game, state, d)
        steps += 1
    if hasattr(policy, "brain"):
        llm_calls = len(policy.brain.calls) - n_calls0
    return {
        "policy": policy.name, "seed": seed, "score": game.score, "steps": steps,
        "seconds": round(time.monotonic() - t0, 1), "llm_calls": llm_calls,
        "regret_sum": round(regret_total) if expert else None, "turn_attributes": game.env.turn_attributes,
    }  # fmt: skip


def summarize(rows: list[dict], audits: list[dict], exam: str, args) -> str:
    lines = [f"# 离线对局 {exam}", "", f"种子 {args.seeds}；专家世界数 {args.worlds}；隐藏实机读不到的字段：{args.hide_live}", ""]
    lines += ["| 策略 | 局数 | 均分 | 中位数 | 最低 | 最高 | 平均耗时 s |", "|---|--:|--:|--:|--:|--:|--:|"]
    by = {}
    for r in rows:
        by.setdefault(r["policy"], []).append(r)
    for p, rs in by.items():
        sc = [r["score"] for r in rs]
        lines.append(
            f"| {p} | {len(sc)} | {statistics.fmean(sc):,.0f} | {statistics.median(sc):,.0f} | {min(sc):,} | {max(sc):,} | {statistics.fmean(r['seconds'] for r in rs):.1f} |"
        )
    if audits:
        lines += ["", "## 决策审计（专家推演对比）", ""]
        n = len(audits)
        agree = sum(a["chosen"] == a["best"] for a in audits)
        lines.append(f"{n} 个决策点，与专家一致 {agree}（{agree / n:.0%}），遗憾合计 {sum(a['regret'] for a in audits):,}。")
        sig = [a for a in audits if a["regret"] > 2 * a.get("regret_se", 0) and a["regret"] > 0]
        lines.append(f"显著分歧（遗憾 > 2 倍配对标准误）{len(sig)} 个，导出到 candidates.json。")
        lines += ["", "遗憾最大的 10 个显著分歧：", "", "| 种子 | 回合 | 所选 | 专家 | 遗憾 ± 标准误 | 来源 |", "|--:|--:|---|---|--:|---|"]
        for a in sorted(sig, key=lambda a: -a["regret"])[:10]:
            lines.append(f"| {a['seed']} | {a['turn']} | {a['chosen']} | {a['best']} | {a['regret']:,} ± {a['regret_se']:,} | {a['source']} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam", choices=EXAMS, default="r2")
    ap.add_argument("--policies", default="community,brain-rules")  # heuristic 只作对照（用户 2026-11-02）
    ap.add_argument("--seeds", default="0-4")
    ap.add_argument("--worlds", type=int, default=12)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--hide-live", action="store_true")
    args = ap.parse_args()

    fixture, label = EXAMS[args.exam]
    exam = SimExam(fixture, label, CAMPUS_HIRO)
    out = REPO / "reports" / "sim" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.exam}"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    audit_path = out / "decisions.jsonl"
    args.trace = (out / "trace.jsonl").open("w")  # 不开审计时也记录每步决策与预演
    with (out / "episodes.jsonl").open("w") as ep, audit_path.open("w") as au:
        for name in args.policies.split(","):
            policy = make(name, exam, args)
            for seed in parse_seeds(args.seeds):
                r = play_episode(policy, exam, seed, args, au if args.audit and name.startswith("brain") else None)
                rows.append(r)
                ep.write(json.dumps(r, ensure_ascii=False) + "\n")
                ep.flush()
                print(f"{name:12s} seed {seed:3d} score {r['score']:>9,}  {r['seconds']:6.1f}s  llm {r['llm_calls']}"
                      + (f"  regret {r['regret_sum']:,}" if r["regret_sum"] is not None else ""), flush=True)  # fmt: skip
    audits = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    (out / "summary.md").write_text(summarize(rows, audits, args.exam, args), encoding="utf-8")
    sig = sorted((a for a in audits if a["regret"] > 2 * a.get("regret_se", 0) and a["regret"] > 0), key=lambda a: -a["regret"])
    (out / "candidates.json").write_text(json.dumps(sig[:30], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告：{out / 'summary.md'}")


if __name__ == "__main__":
    main()
