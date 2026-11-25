"""日常任务：跑一遍不花钱的项（用户 2026-11-02 经协调定），实现是 hiro.daily（hiro.runtime 上、设备走 hiro.devices），不 import KAA / kotonebot。
    python tools/daily/run_daily.py [--only contest,presents]
只开：activity_funds / presents / assignment（领取并重新派出）/ contest / mission_reward / club_reward。
不开：purchase / upgrade_support_card / capsule_toys / produce。
contest 回忆没编成就跳过并报告，不自动编成（同原来 KAA 配置 when_no_set=skip）。
每项前后截图到 reports/live/daily/<时间>/，结果写 summary.json。
兜底：Runtime 的中断层带 Popup（说明框点「閉じる」、含ジュエル的确认框只点「キャンセル」）和 Deadline（单项超过 --timeout 秒，
默认 300，就打断、截图记 timeout，继续下一项）。
原来缺省走 KAA 的 tasks/daily、--own 才走 hiro.daily；2026-11-25 后两项也迁完，KAA 那条路删了，--own 只为兼容留着、不起作用。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import hiro  # noqa: E402, F401  兼容层

TASKS = ("activity_funds", "presents", "assignment", "contest", "mission_reward", "club_reward")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔，只跑这些项")
    ap.add_argument("--timeout", type=float, default=300.0, help="单项超时（秒），超时打断并截图")
    ap.add_argument("--own", action="store_true", help="兼容旧调用，不起作用（现在只有 hiro.daily 一种实现）")
    args = ap.parse_args()
    names = [n for n in (args.only.split(",") if args.only else TASKS) if n]
    if unknown := [n for n in names if n not in TASKS]:
        ap.error(f"没有这些项：{unknown}")
    out = ROOT / "reports/live/daily" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    summary = run_own(names, args.timeout, out)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")
    return 0 if all(r["ok"] for r in summary) else 1


def _finish(rec: dict, summary: list) -> None:
    summary.append(rec)
    print(json.dumps({k: v for k, v in rec.items() if k != "traceback"}, ensure_ascii=False), flush=True)


def _error(rec: dict, err: BaseException) -> None:
    rec["error"] = f"{type(err).__name__}: {err}"
    rec["traceback"] = "".join(traceback.format_exception(type(err), err, err.__traceback__))[-1500:]


def run_own(names: list[str], timeout: float, out: Path) -> list[dict]:
    """hiro.daily：设备 hiro.devices（HIRO_DEVICE），Runtime 的中断层带 Popup（说明框 / ジュエル框）和 Deadline（单项超时）。"""
    from hiro.daily import TASKS as OWN
    from hiro.daily import Deadline, Popup, TaskTimeout, make_runtime
    from hiro.devices import open_device

    import cv2

    dev = open_device()
    dev.connect()
    popups: list[dict] = []
    current = {"task": ""}

    def on_popup(kind, img):
        p = out / f"{current['task']}-popup{len(popups) + 1}-{kind}.png"
        cv2.imwrite(str(p), img)
        popups.append({"kind": kind, "shot": str(p)})

    rt = make_runtime(dev, popup=Popup(on_popup))
    deadline = Deadline(timeout, rt.clock)
    rt.interrupts = (*rt.interrupts, deadline)

    def shot(tag: str) -> str:
        p = out / f"{tag}.png"
        cv2.imwrite(str(p), rt.screen.shot().img)
        return str(p)

    summary = []
    for n in names:
        current["task"] = n
        popups.clear()
        rec = {"task": n, "impl": "own", "before": shot(f"{n}-before")}
        t0 = time.monotonic()
        deadline.start()
        try:
            rec["result"] = OWN[n](rt)
            rec["ok"] = True
        except TaskTimeout:
            rec["ok"] = False
            rec["error"] = f"超时 {timeout:.0f}s，已打断"
            rec["timeout_shot"] = shot(f"{n}-timeout")
        except Exception as e:  # noqa: BLE001  一项失败（ResumeBlocked / DangerBlocked …）记下来，继续下一项
            rec["ok"] = False
            _error(rec, e)
        if popups:
            rec["popups"] = list(popups)
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["after"] = shot(f"{n}-after")
        _finish(rec, summary)
    return summary


if __name__ == "__main__":
    sys.exit(main())
