"""防空转回放（hiro.loop.repeat_guard）：拿历史 trace / 日志重放，看重复无效动作会在第几次被拦下、实际空转了多久。

    python tools/audit/repeat_replay.py trace traces/main23-20261103-201834-r4
    python tools/audit/repeat_replay.py log reports/live/qualify21-20261103-044408.log

trace：每个有执行结果的决策步按 Session.record 的口径（页面 + 决策输入状态签名 + 动作）计数；
log：考试弹窗阶段「卡牌停在选中态（SELECT）→ 再点一次 (x, y)」按 run_exam 弹窗阶段的口径计数（同一画面 = 同一点击位置）。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path


def _ts(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def replay_trace(trace: Path) -> list[dict]:
    from hiro.loop.repeat_guard import REPEAT_MAX, Guard, action_key, state_sig

    g, out, run = Guard(), [], None
    for ln in (trace / "steps.jsonl").open(encoding="utf-8"):
        st = json.loads(ln)
        for o in [
            *(st.get("observations_before") or []),
            st["observation"],
        ]:  # 同 Session.observe：中间出现别的页面会清零
            g.seen(o["page"]["name"], "")
        d, ex = st.get("decision"), st.get("execution")
        for o in st.get("observations_after") or []:
            g.seen(o["page"]["name"], "")
        if not d or not ex or (d.get("action") or {}).get("kind") in ("Wait", "Noop"):
            continue
        page = st["observation"]["page"]["name"]
        s = st.get("state") or {}
        n = g.attempt(
            page, state_sig(page, s.get("page_fields") or {}, s.get("exam"), s.get("produce")), action_key(d["action"])
        )
        if n == REPEAT_MAX:
            run = {
                "seq": st["seq"],
                "t": st["t"],
                "page": page,
                "action": d["action"],
                "until_seq": st["seq"],
                "until_t": st["t"],
                "steps": n,
            }
            out.append(run)
        elif n > REPEAT_MAX and run is not None:
            run.update(until_seq=st["seq"], until_t=st["t"], steps=n)
    return out


RETAP = re.compile(r"^\[(\S+ \S+)\].*卡牌停在选中态（SELECT）→ 再点一次 \((\d+), (\d+)\)")


def replay_log(log: Path) -> list[dict]:
    from hiro.loop.repeat_guard import REPEAT_MAX, Guard

    g, out, run = Guard(), [], None
    for ln in log.open(encoding="utf-8", errors="replace"):
        m = RETAP.match(ln)
        if not m:
            continue
        t, x, y = m.group(1), int(m.group(2)) // 30, int(m.group(3)) // 30
        n = g.attempt("HIF_EXAM:dialogs", (x, y), "last_action")
        if n == REPEAT_MAX:
            run = {"t": t, "until_t": t, "steps": n}
            out.append(run)
        elif n > REPEAT_MAX and run is not None:
            run.update(until_t=t, steps=n)
    return out


def main(argv: list[str]) -> int:
    kind, path = argv[0], Path(argv[1])
    if kind == "trace":
        for r in replay_trace(path):
            print(
                f"seq {r['seq']} {_ts(r['t'])} {r['page']} {r['action']}：第 3 次拦下；原来连续 {r['steps']} 次，"
                f"到 seq {r['until_seq']} {_ts(r['until_t'])}（{(r['until_t'] - r['t']) / 60:.1f} 分钟）"
            )
    else:
        for r in replay_log(path):
            print(f"{r['t']}：第 3 次拦下；原来连续再点 {r['steps']} 次，到 {r['until_t']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
