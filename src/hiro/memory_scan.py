"""回忆库存扫描（只读）：在实机上跑。先在编成页点开任意一格回忆，停在回忆选择页，再运行：

    python -m hiro.memory_scan explore      # 拍筛选 / 排序对话框、详情第 2 页、翻到底 → reports/live/memory-explore/<时间>/
    python -m hiro.memory_scan scan         # 先滑回顶部，逐格读卡 / 时机 / 属性 / 能力 → reports/live/memory-inventory.json（覆盖，旧文件留 .bak）

两条命令最后都会点「キャンセル」退出选择页，不会点「決定」，不改编成。
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from hiro.produce.memory_picker import SWIPE_DURATION, SWIPE_HOLD, Picker, explore, scan
from hiro.tool_device import open_tool_device

FORMATION_CHANGED_EXIT = (
    5  # 选择页在扫描中关了（编成可能被改）：保留现场，以这个退出码告诉调用方别接着往下做（#116 审查重要 3）
)
# 按片扫描设筛选出错（N43：Vision 坏了换 RapidOCR 后读不准「ソート・フィルタ」弹窗）：已经收尾、恢复过排序和筛选，
# 结果里 filter_reset 写明筛选有没有确认重置。调用方（一键全收集）据此决定能不能改用整表扫描（#173 review）
SLICE_FILTER_EXIT = 6


def scan_context(where: dict, rows: list[dict]) -> dict:
    """这次从哪个编成进的选择页（入库时比上下文用，设计 5.3 / D11）：empty=True 时属性是回忆本身的；
    否则带着那套编成其余几格的偏移，记下模式、编成号和那套编成里的卡（编成快照里的），偏移一样才能合并（#116 第三轮）。"""
    mode = where.get("used_mode") or where.get("mode")
    n = where.get("used") or where.get("set")
    ctx = {"mode": mode, "formation_set": n, "empty": bool(where.get("empty"))}
    if not ctx["empty"]:
        row = next((r for r in rows if r.get("mode") == mode and r.get("kind") == "memory" and r.get("set") == n), None)
        ctx["cards"] = list((row or {}).get("cards") or [])
    return ctx


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


def known_for_slice(store, key: str, *, empty: bool | None = True):
    """增量补扫用的「资料库里已有的」。下面几种都返回 None = 这一片全读：
    - 从没完整读过（终审 I3：没读完的片增量会读到已知就停、永远补不全）；
    - 最近一次没读完（被预算打断，#116 第二轮审查：中断处以下的新回忆补不进来）；
    - 库里是带偏移读的，或者和这次的编成上下文不同（empty 不一样：签名对不上，终审 I4 / 第二轮审查）。"""
    from hiro.produce.memory_sig import KnownSet

    cov = store.slice_coverage().get(key) or {}
    stored_empty = bool((cov.get("context") or {}).get("empty"))
    if (
        not cov.get("full_at")
        or not cov.get("complete")
        or not stored_empty
        or (empty is not None and empty != stored_empty)
    ):
        return None
    return KnownSet(store.slice_entries(key))


def _known_for(key: str, empty: bool):
    from hiro.cli.store import open_store

    return known_for_slice(open_store(), key, empty=empty)


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
    restored = closed = False
    filter_error = None
    try:
        for key in keys:
            if deadline is not None and picker.clock() >= deadline:
                break
            try:
                memory_filter.apply_slice(picker, key)
            except memory_filter.EmptySlice:  # 这一片没有回忆（弹窗还开着，下一片接着设）：记成全读 0 份
                rec = {
                    "key": key,
                    "mode": "full",
                    "complete": True,
                    "entries": [],
                    "new": 0,
                    "stopped": None,
                    "empty": True,
                }
                done.append(rec)
                if on_slice:
                    on_slice(rec)
                pending.remove(key)
                continue
            except memory_filter.MemoryFilterError as e:
                # 设筛选出错：不再往下扫，交给 finally 恢复排序 / 重置筛选，结果里记下原因（N43，#173 review）
                filter_error = str(e)
                logging.getLogger(__name__).warning("回忆片 %s：设筛选出错（%s），停止按片扫描", key, e)
                break
            known = known_for(key)
            inv = memory_picker.scan(
                picker, out_dir / f"slice-{key.replace(':', '-')}.json", known=known, deadline=deadline, close=False
            )
            rec = {"key": key, "mode": "full" if known is None else "incremental", "complete": bool(inv.get("complete")),
                   "entries": inv["entries"], "new": inv.get("new"), "stopped": inv.get("stopped")}  # fmt: skip
            if inv.get("formation_may_have_changed") or getattr(picker, "formation_may_have_changed", False):
                # 选择页在扫描中关了（多半点成了「決定」，编成可能被改）：立即停，什么都不再点，保留现场（#116 审查严重 1）
                closed = True
                picker.formation_may_have_changed = True
                rec.update(complete=False, formation_may_have_changed=True)
                done.append(rec)
                if on_slice:
                    on_slice(rec)
                break
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
        reset = False
        if not (closed or getattr(picker, "formation_may_have_changed", False)):
            restored, reset = _leave_picker(picker, state)
    return {"slices": done, "pending": pending, "restored": restored, "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "formation_may_have_changed": closed, "filter_reset": reset, "filter_error": filter_error,
            "complete": not closed and not pending and filter_error is None and all(s["complete"] for s in done)}  # fmt: skip


def _leave_picker(picker, state: dict) -> tuple[bool, bool]:
    """恢复排序 / 方向、重置筛选，关弹窗，点キャンセル退出选择页；返回 (排序和方向确认恢复了, 筛选确实重置了)。"""
    from hiro.produce import memory_filter

    restored, report = False, {}
    try:
        restored = memory_filter.restore(picker, state, report=report)
    except Exception as e:  # noqa: BLE001  恢复失败要写明，但不丢已经扫到的
        logging.getLogger(__name__).warning("回忆列表的排序 / 筛选没能恢复：%s", e)
    try:  # 恢复失败时弹窗可能还开着：先关弹窗，下面的キャンセル才是退出选择页（终审 I6）
        memory_filter.close_popup(picker)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("关「ソート・フィルタ」弹窗失败：%s", e)
    picker.cancel()
    return restored, bool(report.get("filter_reset"))


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
    where, rows = None, []
    filter_error = None  # 按片扫描设筛选出错的原因（N43）
    keep_scene = False  # 编成可能被改了：保留现场给人核对，收尾不再点
    try:
        if args.from_home:
            from hiro.produce import formation_menu
            from hiro.produce.memory_picker import picker_open

            rows = _formation_rows()
            where = formation_menu.open_picker(
                formation_menu.tool_device(d),
                "memory",
                ready=lambda img: picker_open(picker.ocr(img)),
                # 按片扫才切到空编成（读回忆本身的属性）；整表扫照旧从当前编成进（#116 审查重要 5：不改缺省行为）
                use_set=(lambda mode: pick_empty_set(rows, mode)) if args.slices else None,
            )
        ctx = None
        if where is not None:
            # 从哪个编成进去的：empty=True 时属性是回忆本身的；否则带着其余 3 格的常数偏移（设计 5.3 / D11）
            ctx = scan_context(where, rows)
        if args.command == "explore":
            out = Path(f"reports/live/memory-explore/{stamp}")
            explore(picker, out)
            print(out)
        elif args.slices:
            from hiro.state.memory_slices import SLICES

            keys = list(SLICES) if args.slices == "all" else [k.strip() for k in args.slices.split(",") if k.strip()]
            now_empty = bool(ctx and ctx.get("empty"))  # 这次是不是从空编成进的：和库里不一样就全读（签名对不上）
            known_for = (lambda key: _known_for(key, now_empty)) if args.incremental else (lambda key: None)
            inv = scan_slices(picker, keys, Path(args.out).parent, known_for=known_for, budget_s=args.budget_s,
                              on_slice=slices_progress_writer(Path(args.out), ctx))  # fmt: skip
            keep_scene = bool(inv.get("formation_may_have_changed"))
            filter_error = inv.get("filter_error")
            if ctx is not None:
                inv["context"] = ctx
            if not keep_scene:
                # 筛选按「フィルタをリセット」回到默认（玩家原来自己设的筛选会被清掉）；排序 / 方向确认不了时也说清楚（#116 审查重要 4）
                if inv.get("filter_reset"):
                    print("提示：回忆列表的筛选已重置为默认（扫描前自己设过筛选的，要在游戏里重新设）", flush=True)
                else:
                    print("注意：回忆列表的筛选可能没恢复（还停在扫描时设的那一片），请到游戏里看一下", flush=True)
                if not inv.get("restored"):
                    print(
                        "注意：回忆列表的排序 / 升降序可能被改了，请到游戏里看一下（选择页右下的排序按钮）", flush=True
                    )
            Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
            n = sum(len(x["entries"]) for x in inv["slices"])
            new = sum(x["new"] or 0 for x in inv["slices"])
            print(f"{len(inv['slices'])} 片、{n} 格（新的 {new}）→ {args.out}（complete={inv['complete']}）")
        else:
            inv = scan(picker, Path(args.out), max_pages=args.max_pages)
            keep_scene = bool(inv.get("formation_may_have_changed"))
            if ctx is not None:
                inv["context"] = ctx
                Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{len(inv['entries'])} 份回忆 → {args.out}（complete={inv.get('complete')}）")
    except Exception:
        # 选择页关了的标记打上之后又出错（存盘失败之类）：照样按「编成可能被改」退出，别让调用方当普通失败接着开局（#116 第二轮审查）
        if not getattr(picker, "formation_may_have_changed", False):
            raise
        logging.getLogger(__name__).exception("回忆扫描：选择页关了之后又出错")
    finally:
        # 从首页进来的就回首页（G1-F1：读完停在メモリー編成页，下一个任务开头不在首页）。
        # 但选择页在扫描中关了（多半点成了「決定」，编成可能被改）时 scan 已经停手要人核对，不能再点回首页离开现场；
        # 这个标记在发现的当下就打在 picker 上，存盘出错时也看得到（#108 / #110 review）
        keep_scene = keep_scene or bool(getattr(picker, "formation_may_have_changed", False))
        if where is not None and not keep_scene:
            _back_to_set(d, where)  # 先切回进来时的模式和编成号（设计 5.3）
        if args.from_home and not keep_scene:
            from hiro.runtime import nav

            nav.back_home(d)
    if keep_scene:
        print("选择页在扫描中关了，编成可能被改：停在原地，请到游戏里核对编成", flush=True)
        return FORMATION_CHANGED_EXIT
    if filter_error is not None:
        print(f"停下：按片扫描回忆时设筛选出错（{filter_error}）", flush=True)
        return SLICE_FILTER_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
