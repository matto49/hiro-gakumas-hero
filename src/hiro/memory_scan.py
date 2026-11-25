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


def pick_empty_set(rows: list[dict], mode: str | None) -> int | None:
    """编成快照（info formations 的行）里 mode 下全空的回忆编成，取号最大的；没有返回 None（设计 5.3）。"""
    empty = [r["set"] for r in rows if r.get("mode") == mode and r.get("kind") == "memory" and r.get("filled") == 0]
    return max(empty) if mode and empty else None


def _formation_rows() -> list[dict]:
    """资料库里最新的编成快照（挑空编成用）；读不到就当没有（照旧从当前编成进）。"""
    try:
        from hiro.cli.store import open_store

        return open_store().latest("formations")[1]
    except Exception as e:  # noqa: BLE001  资料库不可用不影响扫描
        logging.getLogger(__name__).info("读不到编成快照，不挑空编成：%s", e)
        return []


def _back_to_set(d, where: dict) -> None:
    """扫完（选择页已经キャンセル）把编成页切回进来时的编成号；切不回只记日志，不影响扫描结果。"""
    if not where.get("used") or not where.get("set"):
        return
    from hiro.produce import formation_menu

    try:
        formation_menu.Menu(formation_menu.tool_device(d)).ensure_set(where["set"])
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("扫完没能切回编成 %s：%s", where["set"], e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["explore", "scan"])
    parser.add_argument("--out", default="reports/live/memory-inventory.json")
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--no-calib", action="store_true", help="scan 时不存每页截图")
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
    else:
        inv = scan(picker, Path(args.out), max_pages=args.max_pages)
        if where is not None:
            # 从哪个编成进去的：empty=True 时属性是回忆本身的；否则带着其余 3 格的常数偏移（设计 5.3 / D11）
            inv["context"] = {
                "mode": where["mode"],
                "formation_set": where["used"] or where["set"],
                "empty": where["used"] is not None,
            }
            Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
            _back_to_set(d, where)
        print(f"{len(inv['entries'])} 份回忆 → {args.out}（complete={inv.get('complete')}）")


if __name__ == "__main__":
    main()
