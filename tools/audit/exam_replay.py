"""考试决策重放：沿实机 trace 的轨迹，逐步重新问考试决策器（可换 LLM / 开关），对比当时的决策并统计失误。

    PYTHONPATH=src python -m tools.audit.exam_replay traces/<run>/steps.jsonl [更多 steps.jsonl] \\
        [--llm codex|claude:sonnet|none] [--only skip] [--limit 20] [--out reports/analysis/<名>.json]

做法：每场考试（R1 / R2 每次挑战）从新记忆开始；每一步用 trace 里记录的识别字段组装局面（hiro.defaults.exam_state.screen_state），
在记忆的副本上让决策器重新决策（清掉本回合计划，逼它对这一步单独表态），再用当时**实际执行**的决策推进记忆（brain.commit），
保证后面的局面仍是实机走过的轨迹。只比较「这一步会怎么选」，不模拟后续得分。

失误口径（与 tools.audit.blunders 一致的那一条）：手里有可打的牌 / 饮料，却选择结束回合（skip）。
--only skip：只重放「当时就跳过了、而且有牌可打」的步（快速检查某个改动能不能消掉这类失误）。
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, Decision
from hiro.defaults.advisor import RuleLLMAdvisor
from hiro.defaults.exam_state import screen_state

EXAM_PAGE = "HIF_EXAM"


def _exam_steps(path: Path):
    """trace → [(label, step)]：只要考试页、有识别字段、有考试决策且执行成功的步。label = r<轮次>#<第几次挑战>。"""
    out, attempt, last_remaining, last_round = [], Counter(), None, None
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        s = json.loads(line)
        obs = s.get("observation") or {}
        f = obs.get("fields") or {}
        d = s.get("decision") or {}
        if (obs.get("page") or {}).get("name") != EXAM_PAGE or not f or not d.get("action"):
            continue
        try:
            Decision.from_dict(d).to_battle()
        except Exception:  # noqa: BLE001  不是考试动作（等待 / 界面操作）
            continue
        rnd, rem = f.get("finals_round") or 1, f.get("remaining_turns")
        if rnd != last_round or (rem is not None and last_remaining is not None and rem > last_remaining):
            attempt[rnd] += 1  # 换轮，或剩余回合变多（再挑戦 / 新开一场）
        last_round, last_remaining = rnd, rem if rem is not None else last_remaining
        out.append((f"r{rnd}#{attempt[rnd]}", s))
    return out


def _playable(legal: list[Action]) -> bool:
    return any(a.legal_key() not in (None, "skip") for a in legal)


def replay(paths: list[Path], llm: str | None, *, only: str | None = None, limit: int | None = None) -> dict:
    advisor = RuleLLMAdvisor(CAMPUS_HIRO, llm=None)
    if llm and llm != "none":
        from hiro.brain.llm import from_spec

        advisor.brain.llm = from_spec(llm)
    else:
        advisor.brain.llm = None
    brain, rows = advisor.brain, []
    for path in paths:
        memory, label = None, None
        for lab, s in _exam_steps(path):
            if lab != label:
                memory, label = brain.new_memory(lab), lab
                advisor.on_round_start(int(lab[1]), {"idol": CAMPUS_HIRO.idol_id})
            f = s["observation"]["fields"]
            try:
                state = screen_state(memory, CAMPUS_HIRO, f)
            except Exception as e:  # noqa: BLE001  个别帧字段不全，组装不出局面
                rows.append(
                    {"run": path.parent.name, "seq": s["seq"], "exam": lab, "error": f"局面组装失败：{e!r}"[:200]}
                )
                continue
            legal = [Action.from_legal(a) for a in state.legal_actions()]
            rec = Decision.from_dict(s["decision"])
            rec_key = rec.action.legal_key()
            row = {"run": path.parent.name, "seq": s["seq"], "exam": lab, "remaining": f.get("remaining_turns"), "hp": f.get("hp"),
                   "genki": f.get("genki"), "hand": [c.get("name") for c in f.get("hand") or []], "playable": _playable(legal),
                   "recorded": rec_key, "recorded_source": rec.source, "recorded_reason": (rec.reason or "")[:120]}  # fmt: skip
            wanted = only is None or (only == "skip" and rec_key == "skip" and row["playable"])
            if wanted and (limit is None or sum(1 for r in rows if "new" in r) < limit):
                m = copy.deepcopy(memory)
                m.plan = []  # 逼它对这一步单独表态
                t0 = time.monotonic()
                try:
                    new = advisor.decide(state, m, None, legal)
                    row.update(new=new.action.legal_key(), new_source=new.source, new_reason=(new.reason or "")[:120],
                               seconds=round(time.monotonic() - t0, 1))  # fmt: skip
                except Exception as e:  # noqa: BLE001
                    row.update(
                        new=None, new_source="error", new_reason=repr(e)[:160], seconds=round(time.monotonic() - t0, 1)
                    )
            rows.append(row)
            try:  # noqa: SIM105
                brain.commit(state, memory, rec.to_battle())  # 按实机执行的动作推进记忆
            except Exception:  # noqa: BLE001  记忆推进失败不影响后面的步（下一步重新读局面）
                pass
    asked = [r for r in rows if "new" in r]
    secs = [r["seconds"] for r in asked if r.get("seconds") is not None]
    summary = {
        "llm": llm or "none",
        "steps": len(rows),
        "asked": len(asked),
        "recorded_skip_with_playable": sum(1 for r in rows if r.get("recorded") == "skip" and r.get("playable")),
        "new_skip_with_playable": sum(1 for r in asked if r.get("new") == "skip" and r.get("playable")),
        "changed": sum(1 for r in asked if r.get("new") != r.get("recorded")),
        "new_sources": dict(Counter(r.get("new_source") for r in asked)),
        "seconds_median": round(statistics.median(secs), 1) if secs else None,
        "errors": sum(1 for r in rows if r.get("error") or r.get("new_source") == "error"),
    }
    return {"summary": summary, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("steps", nargs="+", type=Path, help="traces/<run>/steps.jsonl")
    ap.add_argument(
        "--llm", default="none", help="codex / codex:<model> / claude:sonnet / openai:<model> / none（只用规则）"
    )
    ap.add_argument("--only", choices=["skip"], default=None, help="只重放当时「有牌可打却跳过」的步")
    ap.add_argument("--limit", type=int, default=None, help="最多重新问几步（控制 LLM 调用次数）")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    res = replay(args.steps, args.llm, only=args.only, limit=args.limit)
    print(json.dumps(res["summary"], ensure_ascii=False))
    for r in res["rows"]:
        if "new" in r and (r["new"] != r["recorded"] or r["new"] == "skip"):
            print(f"  {r['run']} seq{r['seq']} {r['exam']} 剩{r['remaining']} 体力{r['hp']} 元気{r['genki']} 手牌{r['hand']}："
                  f"原 {r['recorded']}（{r['recorded_source']}）→ 新 {r['new']}（{r['new_source']}）{r.get('seconds')}s {r.get('new_reason', '')[:60]}")  # fmt: skip
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
