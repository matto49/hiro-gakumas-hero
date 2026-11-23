"""培育决策黄金用例回放（离线，不连游戏）：data/brain_cases/produce/*.json。

    PYTHONPATH=src python -m hiro.brain.cases                      # 只跑规则（LLM 路径的用例看规则兜底选得对不对）
    PYTHONPATH=src python -m hiro.brain.cases --llm claude:sonnet  # 接 LLM（同 HIRO_LLM 写法），打开全部 LLM 路径
    PYTHONPATH=src python -m hiro.brain.cases --llm claude:sonnet --only change2-maindmmt2-20-14

每个用例：{"id", "decider"（页面名）, "source", "note", "accept"? , "forbid"?, "state"（GameState JSON）}；
accept / forbid 写 deciders.action_key 的简写（change:<card_id>、remove:<card_id>、buy:3 …）。
用例只放画面能看到 / 状态机记得的东西，不含种子和存档隐藏信息。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

CASES = Path(__file__).resolve().parents[3] / "data" / "brain_cases" / "produce"


def judge(case: dict, d) -> tuple[bool, str]:
    from hiro.brain.deciders import action_key

    key = action_key(d.action)
    if key in case.get("forbid", []):
        return False, f"选了禁止项 {key}"
    if "accept" in case and key not in case["accept"]:
        return False, f"选了 {key}，可接受的是 {case['accept']}"
    return True, key


def load_cases(only: str | None = None, root: Path = CASES) -> list[dict]:
    cases = [c for f in sorted(root.glob("*.json")) for c in json.loads(f.read_text(encoding="utf-8"))]
    return [c for c in cases if not only or c["id"] == only]


def run(llm=None, only: str | None = None, root: Path = CASES, out=print) -> tuple[int, int, list[dict]]:
    from hiro.brain.deciders import ProduceDeciders
    from hiro.brain.state import CAMPUS_HIRO
    from hiro.contracts import GameState

    rows, passed = [], 0
    for case in load_cases(only, root):
        d = ProduceDeciders(CAMPUS_HIRO, llm).decide(case["decider"], GameState.from_dict(case["state"]))
        ok, detail = judge(case, d)
        passed += ok
        secs = sum(t.seconds for t in d.llm)
        rows.append(
            {
                "id": case["id"],
                "ok": ok,
                "source": d.source,
                "detail": detail,
                "reason": d.reason,
                "seconds": round(secs, 1),
            }
        )
        out(f"{'PASS' if ok else 'FAIL'}  {case['id']}  [{d.source} {secs:.1f}s]  {detail}")
        out(f"      理由 {d.reason}")
        for w in d.warnings:
            out(f"      提醒 {w}")
    out(f"\n{passed}/{len(rows)} 通过")
    return passed, len(rows), rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--llm", default=None, help="LLM 适配器（claude:sonnet / codex / openai:<model> / mock），不给只跑规则"
    )
    ap.add_argument("--only", default=None)
    ap.add_argument("--paths", default="all", help="打开哪些新 LLM 路径（HIRO_PRODUCE_LLM 的写法），缺省 all")
    a = ap.parse_args(argv)
    llm = None
    if a.llm:
        from hiro.brain.llm import from_spec

        os.environ["HIRO_PRODUCE_LLM"] = a.paths
        llm = from_spec(a.llm)
    passed, total, _ = run(llm, a.only)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
