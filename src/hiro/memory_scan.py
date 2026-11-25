"""回忆库存扫描（只读）：在实机上跑。先在编成页点开任意一格回忆，停在回忆选择页，再运行：

    python -m hiro.memory_scan explore      # 拍筛选 / 排序对话框、详情第 2 页、翻到底 → reports/live/memory-explore/<时间>/
    python -m hiro.memory_scan scan         # 先滑回顶部，逐格读卡 / 时机 / 属性 / 能力 → reports/live/memory-inventory.json（覆盖，旧文件留 .bak）

两条命令最后都会点「キャンセル」退出选择页，不会点「決定」，不改编成。
"""

import argparse
import json
import logging
import time
from pathlib import Path

from hiro.produce.memory_picker import SWIPE_DURATION, SWIPE_HOLD, Picker, explore, scan
from hiro.tool_device import open_tool_device


def pick_empty_set(rows: list[dict], mode: str | None) -> tuple[str, int] | None:
    """编成快照里全空的回忆编成 → (模式, 编成号)，取号最大的；当前模式有就用当前模式的，没有就用别的模式的
    （11-26 实机：「初」没有全空的回忆编成；回忆列表各模式一样，切过去扫、扫完切回）。都没有返回 None（设计 5.3）。"""
    from hiro.produce.formation_menu import MODES

    def empty(m):
        sets = [r["set"] for r in rows if r.get("mode") == m and r.get("kind") == "memory" and r.get("filled") == 0]
        return max(sets) if sets else None

    for m in ([mode] if mode else []) + [m for m in MODES if m != mode]:
        if (n := empty(m)) is not None:
            return m, n
    return None


def _formation_rows() -> list[dict]:
    """资料库里最新的编成快照（挑空编成用）；读不到就当没有（照旧从当前编成进）。"""
    try:
        from hiro.cli.store import open_store

        return open_store().latest("formations")[1]
    except Exception as e:  # noqa: BLE001  资料库不可用不影响扫描
        logging.getLogger(__name__).info("读不到编成快照，不挑空编成：%s", e)
        return []


def known_for_slice(store, key: str):
    """增量补扫用的「资料库里已有的」：这一片完整读过、而且属性是本身的（从空编成进去读的）才给 KnownSet；
    否则返回 None = 这一片全读（终审 I3：没读完的片增量会读到已知就停、永远补不全；I4：带偏移的旧读数签名对不上）。"""
    from hiro.produce.memory_sig import KnownSet

    cov = store.slice_coverage().get(key) or {}
    if not cov.get("full_at") or not (cov.get("context") or {}).get("empty"):
        return None
    return KnownSet(store.slice_entries(key))


def _known_for(key: str):
    from hiro.cli.store import open_store

    return known_for_slice(open_store(), key)


def _back_to_set(d, where: dict) -> None:
    """扫完（选择页已经キャンセル）把编成页切回进来时的模式和编成号；切不回只记日志，不影响扫描结果。"""
    if not where.get("used") or not where.get("set"):
        return
    from hiro.produce import formation_menu

    try:
        formation_menu.back_to(formation_menu.tool_device(d), where)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("扫完没能切回 %s 编成 %s：%s", where.get("mode"), where["set"], e)


def slices_progress_writer(out: Path, context: dict | None):
    """每扫完一片就把到目前为止的结果写进 out（partial=True）：后面的片出错 / 任务被停，扫完的片照样入库（终审 I2）。"""
    done: list[dict] = []

    def write(rec: dict) -> None:
        done.append(rec)
        inv = {"slices": done, "partial": True, "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if context is not None:
            inv["context"] = context
        out.write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")

    return write


_PLAN_CODES = {"sense": "ProducePlanType_Plan1", "logic": "ProducePlanType_Plan2", "anomaly": "ProducePlanType_Plan3",
               "common": "ProducePlanType_Common"}  # fmt: skip


def _card_plan(card_id: str) -> str | None:
    try:
        from hiro import master

        c = master.card(card_id)
    except Exception:  # noqa: BLE001  主数据不在：不核对
        return None
    return c.plan_type if c else None


def _plan_mismatch(key: str, entries: list[dict], plan_of) -> str | None:
    """这一片读到的技能卡流派核对（终审 C2）：筛选「プラン」只留一种，读到别的流派的卡说明筛选没设对。
    卡名偶尔认错，容 1 格或一成以内。返回原因，没问题返回 None。"""
    want = _PLAN_CODES[key.split(":")[0]]
    plans = [plan_of(cid) for e in entries if (cid := (e.get("card") or {}).get("card_id"))]
    plans = [p for p in plans if p]
    wrong = sum(p != want for p in plans)
    if wrong > 1 and wrong > len(plans) * 0.1:
        return f"流派对不上（{wrong}/{len(plans)} 格不是 {want}），筛选可能没设对"
    return None


def scan_slices(
    picker, keys: list[str], out_dir: Path, *, known_for, budget_s: float | None = None, plan_of=None, on_slice=None
) -> dict:
    """在回忆选择页里逐片扫（设计 5.4）：记下原来的排序 → 每片设筛选（入手排序）→ 扫（不退出选择页）→ 最后恢复排序、重置筛选、
    点キャンセル。known_for(片) 给 KnownSet 时这一片增量补扫，给 None 时全读。budget_s 到了就停，没扫的片记在 pending。"""
    from hiro.produce import memory_filter, memory_picker

    state = memory_filter.read_state(picker)
    deadline = picker.clock() + budget_s if budget_s else None
    done: list[dict] = []
    pending = list(keys)
    restored = False
    try:
        for key in keys:
            if deadline is not None and picker.clock() >= deadline:
                break
            memory_filter.apply_slice(picker, key)
            known = known_for(key)
            inv = memory_picker.scan(
                picker, out_dir / f"slice-{key.replace(':', '-')}.json", known=known, deadline=deadline, close=False
            )
            rec = {"key": key, "mode": "full" if known is None else "incremental", "complete": bool(inv.get("complete")),
                   "entries": inv["entries"], "new": inv.get("new"), "stopped": inv.get("stopped")}  # fmt: skip
            bad = _plan_mismatch(key, inv["entries"], plan_of or _card_plan)
            if bad:  # 终审 C2：筛选点错了，读到的不是这一片：不入库、停下
                rec["rejected"] = bad
                logging.getLogger(__name__).warning("回忆片 %s：%s，不入库、停止扫描", key, bad)
            done.append(rec)
            if on_slice:
                on_slice(rec)
            if rec["complete"] and not bad:  # 没读完（预算到了）的片留在待补里（终审 I3）
                pending.remove(key)
            if bad or inv.get("stopped") == "budget":
                break
    finally:
        try:
            restored = memory_filter.restore(picker, state)
        except Exception as e:  # noqa: BLE001  恢复失败要写明，但不丢已经扫到的
            logging.getLogger(__name__).warning("回忆列表的排序 / 筛选没能恢复：%s", e)
        try:  # 恢复失败时弹窗可能还开着：先关弹窗，下面的キャンセル才是退出选择页（终审 I6）
            memory_filter.close_popup(picker)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning("关「ソート・フィルタ」弹窗失败：%s", e)
        picker.cancel()
    return {"slices": done, "pending": pending, "restored": restored, "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "complete": not pending and all(s["complete"] for s in done)}  # fmt: skip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["explore", "scan"])
    parser.add_argument("--out", default="reports/live/memory-inventory.json")
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--no-calib", action="store_true", help="scan 时不存每页截图")
    parser.add_argument(
        "--slices", help="按片扫（设计 5.4）：片键逗号分隔（如 sense:hif,common:hif）或 all；不给就照旧整表扫"
    )
    parser.add_argument("--incremental", action="store_true", help="按片扫时增量补扫：连续 3 格资料库里有的就停")
    parser.add_argument(
        "--budget-s", type=float, default=None, help="按片扫的总时间预算（秒），到了就停、没扫的片记 pending"
    )
    parser.add_argument(
        "--from-home",
        action="store_true",
        help="从首页 / 编成菜单开始：先进回忆编成页、点第 1 格打开选择页（初星助手的扫描按钮用）",
    )
    args = parser.parse_args()
    # 不配的话扫描过程的 logger.info 全被丢掉：初星助手里只看到「开始」，半小时没有进度
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    d = open_tool_device()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    from hiro.imgio import calib_root

    calib = None if args.no_calib else calib_root() / f"{stamp}-回忆扫描"
    # 慢滑、终点停住再抬起：列表不带惯性滚动，扫描靠行重叠核对不漏行（以前只在 MaaTools 上停，现在各设备一样）
    picker = Picker(d.screenshot, d.tap,
                    lambda x1, y1, x2, y2: d.swipe(x1, y1, x2, y2, duration=SWIPE_DURATION, hold=SWIPE_HOLD), calib_dir=calib)  # fmt: skip
    where = None
    if args.from_home:
        from hiro.produce import formation_menu
        from hiro.produce.memory_picker import picker_open

        rows = _formation_rows()
        where = formation_menu.open_picker(
            formation_menu.tool_device(d),
            "memory",
            ready=lambda img: picker_open(picker.ocr(img)),
            use_set=lambda mode: pick_empty_set(rows, mode),
        )
    if args.command == "explore":
        out = Path(f"reports/live/memory-explore/{stamp}")
        explore(picker, out)
        print(out)
    elif args.slices:
        from hiro.state.memory_slices import SLICES

        keys = list(SLICES) if args.slices == "all" else [k.strip() for k in args.slices.split(",") if k.strip()]
        known_for = _known_for if args.incremental else (lambda key: None)
        ctx = None
        if where is not None:
            ctx = {
                "mode": where["mode"],
                "formation_set": where["used"] or where["set"],
                "empty": bool(where.get("empty")),
            }
        inv = scan_slices(picker, keys, Path(args.out).parent, known_for=known_for, budget_s=args.budget_s,
                          on_slice=slices_progress_writer(Path(args.out), ctx))  # fmt: skip
        if ctx is not None:
            inv["context"] = ctx
        if not inv.get("restored"):
            print("注意：回忆列表的排序 / 筛选没能恢复，请在游戏里手动改回（选择页右下的排序按钮）", flush=True)
        Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
        if where is not None:
            _back_to_set(d, where)
        n = sum(len(x["entries"]) for x in inv["slices"])
        print(
            f"{len(inv['slices'])} 片、{n} 格（新的 {sum(x['new'] or 0 for x in inv['slices'])}）→ {args.out}（complete={inv['complete']}）"
        )
    else:
        inv = scan(picker, Path(args.out), max_pages=args.max_pages)
        if where is not None:
            # 从哪个编成进去的：empty=True 时属性是回忆本身的；否则带着其余 3 格的常数偏移（设计 5.3 / D11）
            inv["context"] = {
                "mode": where["mode"],
                "formation_set": where["used"] or where["set"],
                "empty": bool(where.get("empty")),
            }
            Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
            _back_to_set(d, where)
        print(f"{len(inv['entries'])} 份回忆 → {args.out}（complete={inv.get('complete')}）")


if __name__ == "__main__":
    main()
