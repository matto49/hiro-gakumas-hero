"""开局前自动补扫回忆（资料库扫描设计 5.6 / D4 / D5 / D14）。开关 HIRO_SCAN_BEFORE_START（缺省关）。

开关打开时，开新一局（hif / hajime / nia）的任务命令外面包这一层：
1. 这局要的 6 片（偶像卡流派 3 片 + 共通 3 片）里没扫过或超过 1 天的，增量补扫，总预算 3 分钟；超了先停，没补完的记下来；
2. 方案选的回忆还在不在（按签名核对）：不在了就停下，提示重出方案（不自动重出、不自动写编成：写编成会改游戏）；
3. 开局（原来的命令）；
4. 这局正常打完后，把第 1 步没补完的片补完。

    python -m hiro.memory_refresh --idol <偶像卡> [--budget-s 180] -- <开局命令…>

G0（hiro.runtime.prepare_start）合入后挪到那一层，接口不变。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

SWITCH = "HIRO_SCAN_BEFORE_START"
BUDGET_S = 180  # D5：开局前最多等 3 分钟
STALE_AFTER = timedelta(days=1)  # D4：超过 1 天算旧


class PlanMemoryMissing(RuntimeError):
    pass


class FormationMayHaveChanged(RuntimeError):
    """补扫时回忆选择页意外关了（编成可能被改）：不能接着开局。"""


FORMATION_CHANGED_EXIT = 5  # 同 hiro.memory_scan.FORMATION_CHANGED_EXIT


def enabled() -> bool:
    """开关：环境变量 HIRO_SCAN_BEFORE_START 设了以它为准；没设看初星助手设置（HIRO_HOME/settings.json 的 scan_before_start）。
    补扫是按片扫的，「回忆按片扫描」（hiro.state.memory_slices.enabled）也要开。"""
    from hiro.state import memory_slices

    return _switch_on() and memory_slices.enabled()


def _switch_on() -> bool:
    v = os.environ.get(SWITCH)
    if v is not None and v.strip():
        return v.strip().lower() in ("1", "on", "true", "yes")
    try:
        from hiro.cli.home import hiro_home

        return bool(json.loads((hiro_home() / "settings.json").read_text(encoding="utf-8")).get("scan_before_start"))
    except (OSError, ValueError, AttributeError):
        return False


def ensure_fresh(
    style: str | None, *, store, run_scan, budget_s: float | None = BUDGET_S, now: datetime | None = None
) -> dict:
    """这局要的片里没扫过或超过 1 天的，交给 run_scan(片列表, 预算) 增量补扫。返回 {scanned, pending（没补完的片）}。"""
    from hiro.state.memory_slices import slices_for_plan

    now = now or datetime.now()
    cov = store.slice_coverage()

    def stale(k: str) -> bool:
        if cov.get(k) and not cov[k].get("complete"):  # 最近一次没读完（预算打断）：不管多新都要补（#116 第二轮审查）
            return True
        at = (cov.get(k) or {}).get("scanned_at")
        try:
            return at is None or now - datetime.strptime(at, "%Y-%m-%d %H:%M:%S") > STALE_AFTER
        except ValueError:
            return True

    keys = [k for k in slices_for_plan(style) if stale(k)]
    if not keys:
        return {"scanned": [], "pending": []}
    r = run_scan(keys, budget_s) or {}
    return {"scanned": keys, "pending": list(r.get("pending") or [])}


def check_plan_memories(plan_path: Path, style: str | None, *, store) -> None:
    """方案选的回忆（带签名的，即分片库存出的方案）都还在库存里；不在了抛 PlanMemoryMissing。旧方案没有签名，不查。"""
    from hiro.produce.memory_sig import KnownSet

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    chosen = [c for c in (plan.get("memory") or {}).get("chosen") or [] if c.get("signature")]
    if not chosen:
        return
    inv = store.memories_for_plan(style) or {}
    known = KnownSet(inv.get("entries") or [])
    missing = [c.get("card_id") or "?" for c in chosen if not known.take_signature(c["signature"])]
    if missing:
        idol = (plan.get("idol") or {}).get("idol_card_id") or "<偶像卡编号>"
        raise PlanMemoryMissing(
            f"方案选的回忆不在库存里了：{'、'.join(missing)}（卖掉了、或强化过）。"
            f"重新出方案：hiro plan formation --idol {idol}；再写编成"
        )


def _scan(keys: list[str], budget_s: float | None, out_dir: Path) -> dict:
    """子进程按片增量补扫（同 scan-memories），结果入库。"""
    from hiro.cli.cmd_info import import_inventory

    out = out_dir / f"refresh-{datetime.now():%H%M%S}.json"
    argv = [sys.executable, "-m", "hiro.memory_scan", "scan", "--from-home", "--slices", ",".join(keys), "--incremental",
            *(["--budget-s", str(budget_s)] if budget_s else []), "--out", str(out)]  # fmt: skip
    print(
        f"开局前补扫回忆：{len(keys)} 片（{'、'.join(keys)}）" + (f"，最多 {int(budget_s)} 秒" if budget_s else ""),
        flush=True,
    )
    r = subprocess.run(argv, check=False)
    inv: dict = {}
    if out.is_file():  # 出错时已经逐片写好的部分也入库（设计 I2，#116 第二轮审查）
        try:
            inv = json.loads(out.read_text(encoding="utf-8"))
            res = import_inventory("memories", inv, source=f"refresh:{out.stem}")
            print(f"补扫回忆入库：读了 {res['count']} 格，新的 {res.get('added')} 份", flush=True)
        except (OSError, ValueError, KeyError) as e:
            print(f"补扫回忆的结果没能入库：{e}", flush=True)
            inv = {}
    if r.returncode == FORMATION_CHANGED_EXIT:
        raise FormationMayHaveChanged("补扫回忆时选择页在扫描中关了，编成可能被改")
    done = {x.get("key") for x in inv.get("slices") or [] if x.get("complete") and not x.get("rejected")}
    pending: list[str] = list(inv["pending"]) if inv.get("pending") is not None else [k for k in keys if k not in done]
    if r.returncode != 0:
        print(f"补扫回忆没做完（退出码 {r.returncode}），用现有库存继续；没补完 {len(pending)} 片", flush=True)
    return {**inv, "pending": pending}


def refresh_and_start(
    style, cmd: list[str], *, store, scan, run, plan_path: Path, budget_s: float | None = BUDGET_S
) -> int:
    """补扫 → 核对方案的回忆 → 开局（run(cmd)）→ 打完补完没补完的片。补扫时编成可能被改就停、不开局（#116 审查重要 3）。"""
    try:
        r = ensure_fresh(style, store=store, run_scan=scan, budget_s=budget_s)
    except FormationMayHaveChanged as e:
        print(f"停下、不开局：{e}。请到游戏里核对编成", flush=True)
        return FORMATION_CHANGED_EXIT
    try:
        check_plan_memories(plan_path, style, store=store)
    except PlanMemoryMissing as e:
        print(f"停下：{e}", flush=True)
        return 2
    rc = run(cmd)
    if rc == 0 and r["pending"]:
        print("这局打完了，补完开局前没补完的回忆片", flush=True)
        try:
            scan(r["pending"], None)
        except FormationMayHaveChanged as e:
            print(f"打完补扫时停下：{e}。请到游戏里核对编成", flush=True)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="开局前自动补扫回忆，再开局")
    ap.add_argument("--idol", required=True)
    ap.add_argument("--budget-s", type=float, default=BUDGET_S)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    from hiro.cli import paths
    from hiro.cli.home import hiro_home
    from hiro.cli.store import open_store

    home = hiro_home()
    paths.apply_to_process(home)
    try:
        from hiro.brain.formation_plan import idol_info
        from hiro.brain.guides import PLAN_OF

        plan = idol_info(a.idol).get("plan")
        style = PLAN_OF.get(plan) if plan else None
    except Exception:  # noqa: BLE001  认不出流派：12 片都看
        style = None
    out_dir = Path(tempfile.mkdtemp(prefix="memory-refresh-", dir=home))
    return refresh_and_start(style, cmd, store=open_store(), scan=lambda keys, budget: _scan(keys, budget, out_dir),
                             run=lambda c: subprocess.run(c, check=False).returncode, plan_path=paths.plan_path(home),
                             budget_s=a.budget_s)  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
