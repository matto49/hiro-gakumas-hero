"""考试每步耗时拆分（总控 2026-11-04：第二十四局截图→点击 中位 5.2s / p90 12.8s，要分清预演和读帧 / 执行）。

    python tools/audit/exam_latency.py traces/main24-20261104-000252

一步 = settle（上一步执行完 → 本帧稳定）+ shot_to_tap（截图 → 点击）+ execute + wait_change。
shot_to_tap 拆成：预演（preview）/ 读存档和核对（save_read、settle_playing、live_game、reconcile、badges、track_state、
screen_rebuild）/ 截图读数（screen_read、observe）/ 决策（decide，LLM 或专家挑选）/ 点击前核对（tap_check）/
重做（redo：决策帧过时、等存档时作废的那几轮）/ 未计时（剩下的）。旧 trace 没有 redo / settle_playing 等阶段，会落进「未计时」。
"""

from __future__ import annotations

import json
import statistics as stt
import sys
from pathlib import Path

GROUPS = {
    "预演": ("phase_preview",),
    "读存档 / 核对": ("phase_save_read", "phase_settle_playing", "phase_live_game", "phase_reconcile", "phase_badges",
                   "phase_track_state", "phase_screen_rebuild"),
    "截图读数": ("phase_screen_read", "phase_observe"),
    "决策": ("decide",),
    "点击前核对": ("phase_tap_check",),
    "重做": ("phase_redo",),
}  # fmt: skip
AFTER = {"执行": ("phase_execute",), "等画面变化": ("wait_change",), "执行后到稳定": ("phase_settle",)}


def _q(v: list[float]) -> str:
    if not v:
        return "-"
    v = sorted(v)
    return f"{stt.median(v) / 1000:.1f} / {v[int(len(v) * 0.9)] / 1000:.1f} / {sum(v) / 60000:.1f}"


def analyze(trace: Path) -> str:
    rows = [json.loads(ln) for ln in (trace / "steps.jsonl").open(encoding="utf-8")]
    ex = [r for r in rows if r["observation"]["page"]["name"] == "HIF_EXAM" and r.get("execution")]
    if not ex:
        return "没有考试步"

    def g(r, k):
        return float((r.get("timings_ms") or {}).get(k) or 0.0)

    lines = [f"{trace.name}：考试 {len(ex)} 步", "", "| 部分 | 中位 / p90（秒）/ 合计（分钟） |", "|---|---|"]
    lines.append(f"| **截图→点击** | {_q([g(r, 'shot_to_tap') for r in ex])} |")
    known = []
    for name, keys in GROUPS.items():
        vals = [sum(g(r, k) for k in keys) for r in ex]
        known.append(vals)
        lines.append(f"| 　{name} | {_q([v for v in vals if v])} |")
    rest = [g(r, "shot_to_tap") - sum(k[i] for k in known) for i, r in enumerate(ex)]
    lines.append(f"| 　未计时 | {_q([v for v in rest if v > 0])} |")
    for name, keys in AFTER.items():
        lines.append(f"| {name} | {_q([sum(g(r, k) for k in keys) for r in ex])} |")
    lines.append(f"| **一步合计** | {_q([g(r, 'cycle') for r in ex])} |")
    if any(g(r, "phase_unstable_settle_iters") for r in ex):  # 新计时（这个分支合入之后的局）：稳帧检测细分
        lines += ["", "稳帧检测（settle_fast）每步：", "", "| 项 | 中位 / p90 / 合计 |", "|---|---|"]
        for name, k in (("截图", "phase_settle_shot"), ("点舞台推进（含 SELECT OCR）", "phase_settle_tap"),
                        ("预演提前算（读存档 / 还原局面）", "phase_settle_kick"), ("读签名", "phase_settle_lite")):  # fmt: skip
            lines.append(f"| {name} | {_q([g(r, k) for r in ex if g(r, k)])} |")
        n = lambda k: sum(g(r, k) for r in ex)  # noqa: E731
        lines.append(f"\n轮数 {n('phase_unstable_settle_iters'):.0f}；签名读不出 {n('phase_unstable_settle_sig_none'):.0f} 次、"
                     f"HUD 变 {n('phase_unstable_settle_sig_hud'):.0f} 次、手牌变 {n('phase_unstable_settle_sig_hand'):.0f} 次（画面还在动）；"
                     f"等满 4s {n('phase_unstable_settle_timeout'):.0f} 步")  # fmt: skip
    plays = [r for r in ex if (r.get("decision") or {}).get("action", {}).get("kind") == "PlayCard"]
    if plays:
        # 出牌第一下丢点（第二十六 / 三十局）：按「上一步执行完 → 这次点击」分组，看丢点集中在哪；点下去到看到 SELECT 的时间
        buckets: dict[str, list[int]] = {}
        for r in plays:
            since = (g(r, "phase_settle") + g(r, "shot_to_tap")) / 1000
            k = "<1.5s" if since < 1.5 else "1.5–3.5s" if since < 3.5 else "≥3.5s"
            b2 = buckets.setdefault(k, [0, 0])
            b2[0] += 1
            b2[1] += g(r, "phase_unstable_play_retap") > 0
        lines += [
            "",
            "出牌第一下丢点（补点）按离上一步的时间：" + "，".join(f"{k} {v[1]}/{v[0]}" for k, v in buckets.items()),
        ]
        sel = [g(r, "phase_play_select_ms") for r in plays if g(r, "phase_unstable_play_select_try1")]
        if sel:
            lines.append(f"第一下就看到 SELECT 的用时：中位 / p90 / 最大 {_q(sel)}（合计列无意义）")
    redo = sum(g(r, "phase_redo_n") for r in ex)
    lines += ["", f"重做 {redo:.0f} 次（决策帧过时 / 等存档作废的轮次）。合计列只算非零的步。"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(analyze(Path(sys.argv[1])))
