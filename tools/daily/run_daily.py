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
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import hiro  # noqa: E402, F401  兼容层

TASKS = ("activity_funds", "presents", "assignment", "contest", "shop", "coin_gacha", "mission_reward", "club_reward")
DEFAULT_TIMEOUT = 300.0  # 单项超时缺省
SHOP_TIMEOUT = 600.0  # 商店单项缺省（没显式给 --timeout 时）
LABEL = {
    "activity_funds": "活动费",
    "presents": "礼物",
    "assignment": "工作派遣",
    "contest": "竞赛",
    "shop": "商店",
    "coin_gacha": "硬币扭蛋",
    "mission_reward": "任务奖励",
    "club_reward": "社团",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔，只跑这些项")
    ap.add_argument("--timeout", type=float, default=None, help="单项超时（秒），超时打断并截图；缺省 300（商店 600）")
    ap.add_argument("--own", action="store_true", help="兼容旧调用，不起作用（现在只有 hiro.daily 一种实现）")
    ap.add_argument("--contest-pick", default=None, help="竞赛挑哪个对手：lowest（综合力最低，缺省）/ 1 / 2 / 3")
    ap.add_argument("--shop", default=None, help="商店：off（缺省）/ recommended（推荐商品）/ all")
    ap.add_argument("--shop-items", default=None, help="商店另外要买的商品名，逗号分隔（名字包含就算）")
    ap.add_argument("--shop-ap", default=None, help="体力（AP）商店买哪几样：pt,note,retry,regen（缺省不买）")
    ap.add_argument("--shop-refresh", default=None, help="商店买完用每日免费刷新再买一轮：on / off（缺省）")
    ap.add_argument("--coin-gacha", default=None, help="硬币扭蛋：off（缺省）/ auto（抽 1 次）/ friend:1,sense:2 …")
    args = ap.parse_args()
    for opt, var in (
        ("contest_pick", "HIRO_CONTEST_PICK"),
        ("shop", "HIRO_SHOP"),
        ("shop_items", "HIRO_SHOP_ITEMS"),
        ("shop_ap", "HIRO_SHOP_AP"),
        ("shop_refresh", "HIRO_SHOP_REFRESH"),
        ("coin_gacha", "HIRO_COIN_GACHA"),
    ):
        if getattr(args, opt) is not None:  # 空的也设：任务显式传「不买」时不继承环境里的值（CodeRabbit #138）
            os.environ[var] = getattr(args, opt)
    names = [n for n in (args.only.split(",") if args.only else TASKS) if n]
    if unknown := [n for n in names if n not in TASKS]:
        ap.error(f"没有这些项：{unknown}")
    out = ROOT / "reports/live/daily" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    summary = run_own(names, args.timeout, out)  # None = 缺省（300，商店 600）
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")

    # 给人看的一行（初星助手把它当这次任务的结果显示；11-25：原来只显示「完成」，看不出每项做了什么）
    def part(r: dict) -> str:
        label, text = LABEL.get(r["task"], r["task"]), r.get("result") or r.get("error") or "出错"
        # 结果自己已经以「硬币扭蛋：」「商店」开头的不再加一遍（11-27：「硬币扭蛋：硬币扭蛋：友情 1 次」）
        return text if text.startswith(label) else f"{label}：{text}"

    parts = [part(r) for r in summary]
    print("结果：" + "；".join(parts), flush=True)
    bad = [r for r in summary if not r["ok"]]
    if bad:
        print(
            "停下：" + "；".join(f"{LABEL.get(r['task'], r['task'])}出错（{r.get('error') or '?'}）" for r in bad),
            flush=True,
        )
    return 0 if not bad else 1


def _finish(rec: dict, summary: list) -> None:
    summary.append(rec)
    print(json.dumps({k: v for k, v in rec.items() if k != "traceback"}, ensure_ascii=False), flush=True)


def _error(rec: dict, err: BaseException) -> None:
    rec["error"] = f"{type(err).__name__}: {err}"
    rec["traceback"] = "".join(traceback.format_exception(type(err), err, err.__traceback__))[-1500:]


def run_own(names: list[str], timeout: float | None, out: Path) -> list[dict]:
    """hiro.daily：设备 hiro.devices（HIRO_DEVICE），Runtime 的中断层带 Popup（说明框 / ジュエル框）和 Deadline（单项超时）。"""
    from hiro.daily import TASKS as OWN
    from hiro.daily import Deadline, Popup, TaskTimeout, make_runtime
    from hiro.daily.common import PartialStop
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
    explicit = timeout is not None  # 命令行显式给了 --timeout：每项（含商店）都照它（CodeRabbit #122）
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    deadline = Deadline(timeout, rt.clock)
    rt.interrupts = (*rt.interrupts, deadline)

    def shot(tag: str) -> str:
        """诊断截图；截不到（设备断开等）不影响记录：已做的项照样进 summary、照样打结果行（CodeRabbit #138）。"""
        p = out / f"{tag}.png"
        try:
            cv2.imwrite(str(p), rt.screen.shot().img)
        except Exception as e:  # noqa: BLE001
            print(f"截图 {tag} 失败：{type(e).__name__}: {e}", flush=True)
            return ""
        return str(p)

    summary = []
    for n in names:
        current["task"] = n
        popups.clear()
        rec = {"task": n, "impl": "own", "before": shot(f"{n}-before")}
        t0 = time.monotonic()
        # 商店按名字买 + 免费刷新 + 体力商店，一项就可能超过 300 秒（按名字买一轮实测约 205 秒）：给它多一倍（#122 review）
        deadline.seconds = SHOP_TIMEOUT if n == "shop" and not explicit else timeout
        deadline.start()
        try:
            rec["result"] = OWN[n](rt)
            rec["ok"] = True
        except PartialStop as e:  # 做到一半停下：已经做了的照样报（结果行），这一项算失败
            rec["ok"] = False
            rec["result"], rec["error"] = e.result, e.reason
            rec["stop_all"] = e.stop_all  # 画面可能还停在框 / 抽卡结果页上：后面的项都不跑，交给人（CodeRabbit #197）
        except TaskTimeout:
            rec["ok"] = False
            rec["error"] = f"超时 {deadline.seconds:.0f}s，已打断"
            rec["timeout_shot"] = shot(f"{n}-timeout")
        except Exception as e:  # noqa: BLE001  一项失败（ResumeBlocked / DangerBlocked …）记下来，继续下一项
            rec["ok"] = False
            _error(rec, e)
            rec["stop_all"] = getattr(e, "stop_all", False)  # 画面上留着关不掉的框（商店免费刷新后）：后面的项都不跑
        if popups:
            rec["popups"] = list(popups)
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["after"] = shot(f"{n}-after")
        _finish(rec, summary)  # 弹框截图、耗时、完成截图都先记下，再按 stop_all 停（CodeRabbit #122）
        if rec.get("stop_all", False):  # summary.json 里留着这个标记（CodeRabbit #122）
            break
    return summary


if __name__ == "__main__":
    sys.exit(main())
