"""生成一份合成的演示 trace（不含任何游戏素材），用来离线试用复盘页和审计工具。

    python examples/make_demo_trace.py                       # → examples/traces/demo-run/steps.jsonl
    python tools/viewer/build_exam_view.py demo-run --traces examples/traces --out /tmp/hiro-view
    open /tmp/hiro-view/demo-run.html

内容是一场 6 回合的虚构考试：每回合 1–2 步决策，带候选动作的期望分、手牌、饮料、状态，以及结果页。
卡牌、饮料和分数都是编出来的，只为展示 trace 的格式（见 docs/contracts.md 的 Step）。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "traces" / "demo-run"
ATTRS = ["Vo", "Da", "Vi", "Da", "Vo", "Da"]
MULT = {"Vo": 2400, "Da": 3000, "Vi": 1700}
CARDS = [
    ("Warm-up", "元気+6"),
    ("Focus", "やる気+4"),
    ("Spark", "元気の110%分パラメータ上昇"),
    ("Encore", "スキルカード使用数追加+1"),
    ("Finale", "元気の420%分パラメータ上昇させ、元気を0にする レッスン中1回"),
]
DRINKS = [("Boost", "パラメータ上昇量増加50%（3ターン）"), ("Tea", "元気+7")]


def card(slot: int, name: str, text: str) -> dict:
    return {
        "slot": slot,
        "name": name,
        "text": text,
        "available": True,
        "upgrade": 0,
        "tags": {"once": "レッスン中1回" in text},
    }


def main() -> None:
    rng = random.Random(7)
    OUT.mkdir(parents=True, exist_ok=True)
    steps, seq, t = [], 0, 1_800_000_000.0
    score, genki, motivation, hp = 0, 0, 4, 30
    drinks = [{"slot": i, "name": n, "text": x} for i, (n, x) in enumerate(DRINKS)]
    for turn, attr in enumerate(ATTRS):
        rem = len(ATTRS) - turn
        hand = [card(i, *c) for i, c in enumerate(rng.sample(CARDS[:4], 3) + ([CARDS[4]] if rem <= 2 else []))]
        for k in range(2 if rem > 1 else 1):
            # 前段先叠元気 / やる気，后段兑现，最后一回合打终结牌
            want = ("Finale", "Spark") if rem <= 2 else ("Warm-up", "Focus", "Encore")
            pick = next(
                (c for c in hand if c["name"] in want and c is not getattr(main, "_last", None)), hand[k % len(hand)]
            )
            main._last = pick
            base = 40_000 + 9_000 * turn
            previews = []
            for c in hand:
                exp = base + rng.randint(-6000, 6000) + (25_000 if c is pick else 0)
                previews.append({"action": {"kind": "PlayCard", "args": {"slot": c["slot"]}}, "expected": exp, "spread": 3000,
                                 "summary": {"动作": "play:" + c["name"]}, "immediate": {"score": rng.randint(0, 4000)}})  # fmt: skip
            best = max(p["expected"] for p in previews)
            for p in previews:
                p["vs_best"], p["vs_best_se"] = p["expected"] - best, 1500
            ex = {"remaining_turns": rem, "attr": attr, "multiplier_permil": MULT[attr] * 10, "score": score, "genki": genki,
                  "hp": hp, "motivation": motivation, "impression": 0, "npc_scores": [8_000 + 12_000 * turn], "hand": hand,
                  "drinks": drinks, "statuses": [{"name": "やる気", "value": motivation}]}  # fmt: skip
            steps.append({
                "seq": seq, "t": t, "version": "1.2",
                "observation": {"page": {"name": "HIF_EXAM"}, "fields": {}},
                "state": {"page": {"name": "HIF_EXAM"}, "exam": ex, "exam_session": {"label": "demo"}},
                "decision": {"action": {"kind": "PlayCard", "args": {"slot": pick["slot"]}}, "source": "expert",
                             "reason": f"预演专家：play:{pick['name']} 期望终局分 {best:,}", "warnings": []},
                "previews": previews, "execution": {"ok": True, "seconds": 1.2},
            })  # fmt: skip
            seq, t = seq + 1, t + rng.uniform(3, 7)
            if "元気+" in pick["text"]:
                genki += 20 + motivation * 3
            if "やる気" in pick["text"]:
                motivation += 4
            if "パラメータ上昇" in pick["text"]:
                gain = genki * (4.2 if pick["name"] == "Finale" else 1.1) * MULT[attr] / 10
                score += int(gain)
                genki = 0 if pick["name"] == "Finale" else genki
            hp -= 2
    steps.append({"seq": seq, "t": t, "version": "1.2", "state": {"page": {"name": "HIF_EXAM_RESULT"}},
                  "observation": {"page": {"name": "HIF_EXAM_RESULT"}, "fields": {"kind": "result", "score": score, "rank": 1, "passed": True}}})  # fmt: skip
    path = OUT / "steps.jsonl"
    path.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in steps), encoding="utf-8")
    print(f"{path}（{len(steps)} 步，终局分 {score:,}）")


if __name__ == "__main__":
    main()
