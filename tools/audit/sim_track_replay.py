"""考试模拟器跟踪（hiro.sim.screen_source）离线重放：沿实机 trace 的考试步，用画面字段组局面、模拟器跟踪、出预演，
统计跟踪 / 重建 / 丢失、预演耗时，以及按预演选的动作（exam_policy.expert_pick）和实机当时的动作是否一致。

    PYTHONPATH=src python -m tools.audit.sim_track_replay traces/<run>/steps.jsonl [--exam r1|r2] [--limit 40] [--out x.json]

只比较「这一步会怎么选」，不模拟后续得分（同 tools.audit.exam_replay）。
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
from hiro.defaults.advisor import preview_rows
from hiro.defaults.exam_state import screen_state
from hiro.produce.exam_policy import expert_pick
from hiro.sim.screen_source import SimScreenExamStateSource
from tools.audit.exam_replay import _exam_steps


class _Session:
    def __init__(self):
        self.state = type("S", (), {"produce": {}})()
        self.notes = []

    def note(self, t):
        self.notes.append(t)


def replay(paths: list[Path], *, only: str | None = None, limit: int | None = None) -> dict:
    from hiro.defaults.advisor import RuleLLMAdvisor

    advisor = RuleLLMAdvisor(CAMPUS_HIRO, llm=None)
    brain, rows = advisor.brain, []
    for path in paths:
        src, memory, label = None, None, None
        for lab, s in _exam_steps(path):
            q = str(((s.get("state") or {}).get("exam_session") or {}).get("label") or "")
            if q.startswith("qualify"):
                lab = q + "#" + lab.split("#", 1)[-1]  # 选拔考试：_exam_steps 只认本战轮次，按 trace 记的考试标签区分
            if only and not lab.startswith(only):
                continue
            if limit is not None and len(rows) >= limit:
                break
            if lab != label:
                memory, label = brain.new_memory(lab), lab
                src = SimScreenExamStateSource()
                sess = _Session()
                src.begin({"exam": lab.split("#")[0], "profile": CAMPUS_HIRO, "session": sess})
            sess.state.produce = (s.get("state") or {}).get("produce") or {}
            f = s["observation"]["fields"]
            try:
                state = screen_state(memory, CAMPUS_HIRO, f)
            except Exception as e:  # noqa: BLE001
                rows.append({"seq": s["seq"], "exam": lab, "error": repr(e)[:160]})
                continue
            src.game = None
            src.screen_drinks = [getattr(d, "drink_id", None) for d in state.drinks]
            t0 = time.monotonic()
            try:
                src._sync(state, memory)
            except Exception as e:  # noqa: BLE001
                src._lose(f"sim_error {e!r}")
            t_sync = time.monotonic() - t0
            status = "tracked" if src.game is not None else "lost"
            if (
                src.game is not None
                and src.stats["rebuilt"]
                and rows
                and rows[-1].get("rebuilt_total", 0) < src.stats["rebuilt"]
            ):
                status = "rebuilt"
            t1 = time.monotonic()
            pv = src.previews(state) if src.game is not None else None
            t_pv = time.monotonic() - t1
            legal = [Action.from_legal(a) for a in state.legal_actions()]
            rec = Decision.from_dict(s["decision"])
            row = {"run": path.parent.name, "seq": s["seq"], "exam": lab, "remaining": f.get("remaining_turns"), "status": status,
                   "lost_why": src.lost if src.game is None else None, "rebuilt_total": src.stats["rebuilt"],
                   "sync_s": round(t_sync, 2), "preview_s": round(t_pv, 2), "recorded": rec.action.legal_key()}  # fmt: skip
            if pv:
                st2 = copy.copy(state)
                st2.action_previews = preview_rows(pv, legal)
                pick = expert_pick(st2)
                best = max(pv, key=lambda p: p.expected or 0)
                row.update(expert=pick.decision.action if pick.decision is not None else None, best=best.action.legal_key(),
                           best_expected=round(best.expected or 0),
                           recorded_expected=round(next((p.expected or 0 for p in pv if p.action.legal_key() == row["recorded"]), 0)))  # fmt: skip
                if row["expert"] is not None and not isinstance(row["expert"], str):
                    row["expert"] = (
                        Action.from_legal(row["expert"]).legal_key()
                        if isinstance(row["expert"], dict)
                        else str(row["expert"])
                    )
            rows.append(row)
            try:  # noqa: SIM105
                brain.commit(state, memory, rec.to_battle())
            except Exception:  # noqa: BLE001
                pass
            src.executed(rec.action, None)
    st = Counter(r.get("status") for r in rows)
    pvs = [r["preview_s"] for r in rows if r.get("best")]
    summary = {"steps": len(rows), "status": dict(st), "with_preview": len(pvs),
               "preview_s_median": round(statistics.median(pvs), 2) if pvs else None,
               "agree_best": sum(1 for r in rows if r.get("best") and r["best"] == r["recorded"]),
               "lost_reasons": dict(Counter((r.get("lost_why") or "")[:40] for r in rows if r.get("status") == "lost"))}  # fmt: skip
    return {"summary": summary, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("steps", nargs="+", type=Path)
    ap.add_argument("--exam", default=None, help="只看 r1 / r2")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    res = replay(args.steps, only=args.exam, limit=args.limit)
    print(json.dumps(res["summary"], ensure_ascii=False))
    for r in res["rows"]:
        print(f"  seq{r['seq']} {r['exam']} 剩{r.get('remaining')} {r.get('status')} {r.get('lost_why') or ''} "
              f"实机 {r.get('recorded')}（预演 {r.get('recorded_expected')}）  预演最好 {r.get('best')}（{r.get('best_expected')}） "
              f"{r.get('preview_s')}s")  # fmt: skip
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
