# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）；--own 不经 KAA
"""用 KAA 的日常任务跑一遍不花钱的项（用户 2026-11-02 经协调定）。

    python tools/daily/run_daily.py [--only contest,presents]
    python tools/daily/run_daily.py --own [--only presents]   # 走自己的实现（hiro.daily），只有已迁的 4 项

--own（去 KAA 过渡期，缺省仍是 KAA）：presents / activity_funds / mission_reward / club_reward 跑 hiro.daily（hiro.runtime 上、
设备走 hiro.devices），不 import KAA / kotonebot；assignment / contest 还没迁，--own 时不能选。弹窗看守和单项超时改成
runtime 的中断（hiro.daily.common.Popup / Deadline），判据和时限同下。截图、summary.json 格式同 KAA 那条路。

只开：activity_funds / presents / assignment（领取并重新派出）/ contest / mission_reward / club_reward。
不开：purchase / upgrade_support_card / capsule_toys / produce。
contest 的 when_no_set 固定为 skip：没有编成就跳过并报告，不让 KAA 自动编成。
每项前后截图到 reports/live/daily/<时间>/，结果写 summary.json。
兜底（hiro.daily_guard）：每项执行期间后台看守弹窗（说明框点「閉じる」、含ジュエル的确认框只点「キャンセル」），
单项超过 --timeout 秒（默认 300）就打断、截图记 timeout，继续下一项，不再空转。
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
OWN_TASKS = ("activity_funds", "presents", "mission_reward", "club_reward")  # hiro.daily 已迁的（顺序同 TASKS）


def _config():
    from kaa.config.schema import KaaConfig
    from kaa.kaa_context import init as kaa_init

    c = KaaConfig()
    t = c.tasks
    t.activity_funds.enabled = True
    t.presents.enabled = True
    t.assignment.enabled = True
    t.assignment.mini_live_reassign_enabled = True
    t.assignment.online_live_reassign_enabled = True
    t.contest.enabled = True
    t.contest.when_no_set = "skip"
    t.mission_reward.enabled = True
    t.club_reward.enabled = True
    for off in ("purchase", "upgrade_support_card", "capsule_toys", "produce"):
        getattr(t, off).enabled = False
    kaa_init(c, "hiro-daily")
    return c


def _funcs():
    from kaa.tasks.daily.acquire_activity_funds import acquire_activity_funds
    from kaa.tasks.daily.acquire_presents import acquire_presents
    from kaa.tasks.daily.assignment import assignment
    from kaa.tasks.daily.club_reward import club_reward
    from kaa.tasks.daily.contest import contest
    from kaa.tasks.daily.mission_reward import mission_reward

    return {"activity_funds": acquire_activity_funds, "presents": acquire_presents, "assignment": assignment,
            "contest": contest, "mission_reward": mission_reward, "club_reward": club_reward}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔，只跑这些项")
    ap.add_argument("--timeout", type=float, default=300.0, help="单项超时（秒），超时打断并截图")
    ap.add_argument(
        "--own", action="store_true", help="走自己的实现 hiro.daily（不经 KAA），只有 " + " / ".join(OWN_TASKS)
    )
    args = ap.parse_args()
    names = [n for n in (args.only.split(",") if args.only else (OWN_TASKS if args.own else TASKS)) if n]
    if unknown := [n for n in names if n not in TASKS]:
        ap.error(f"没有这些项：{unknown}")
    if args.own and (todo := [n for n in names if n not in OWN_TASKS]):
        ap.error(f"{todo} 还没迁到 hiro.daily，去掉 --own 用 KAA 跑")
    out = ROOT / "reports/live/daily" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    summary = run_own(names, args.timeout, out) if args.own else run_kaa(names, args.timeout, out)
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


def run_kaa(names: list[str], timeout: float, out: Path) -> list[dict]:
    from hiro.daily_guard import PopupGuard, run_with_timeout
    from hiro.device import connect
    from hiro.produce.ops import ocr_boxes

    import cv2
    from kotonebot import device
    from kotonebot.backend.context import init_context, manual_context
    from kotonebot.backend.context import vars as kvars

    _config()
    dev = connect()
    init_context(target_device=dev)
    manual_context().begin()
    funcs = _funcs()
    flow = kvars.flow  # 在本线程取出真正的 FlowController，交给超时计时器线程

    def shot(tag: str) -> str:
        p = out / f"{tag}.png"
        cv2.imwrite(str(p), device.screenshot())
        return str(p)

    summary = []
    for n in names:
        rec = {"task": n, "before": shot(f"{n}-before")}
        t0 = time.monotonic()
        popups = []

        def on_popup(kind, img, n=n, popups=popups):
            p = out / f"{n}-popup{len(popups) + 1}-{kind}.png"
            cv2.imwrite(str(p), img)
            popups.append({"kind": kind, "shot": str(p)})

        guard = PopupGuard(dev.screenshot, lambda x, y: dev.click(x, y), ocr_boxes, on_action=on_popup)
        with guard:
            timed_out, err = run_with_timeout(funcs[n], timeout, flow)
        rec["ok"] = not timed_out and err is None
        if timed_out:
            rec["error"] = f"超时 {timeout:.0f}s，已打断"
            rec["timeout_shot"] = shot(f"{n}-timeout")
        elif err is not None:
            _error(rec, err)
        if popups:
            rec["popups"] = popups
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["after"] = shot(f"{n}-after")
        _finish(rec, summary)
    return summary


if __name__ == "__main__":
    sys.exit(main())
