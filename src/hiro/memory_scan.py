"""回忆库存扫描（只读）：在实机上跑。先在编成页点开任意一格回忆，停在回忆选择页，再运行：

    python -m hiro.memory_scan explore      # 拍筛选 / 排序对话框、详情第 2 页、翻到底 → reports/live/memory-explore/<时间>/
    python -m hiro.memory_scan scan         # 先滑回顶部，逐格读卡 / 时机 / 属性 / 能力 → reports/live/memory-inventory.json（覆盖，旧文件留 .bak）

两条命令最后都会点「キャンセル」退出选择页，不会点「決定」，不改编成。
"""

import argparse
import time
from pathlib import Path

from hiro.produce.memory_picker import SWIPE_DURATION, SWIPE_HOLD, Picker, explore, scan
from hiro.tool_device import open_tool_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["explore", "scan"])
    parser.add_argument("--out", default="reports/live/memory-inventory.json")
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--no-calib", action="store_true", help="scan 时不存每页截图")
    parser.add_argument("--from-home", action="store_true", help="从首页 / 编成菜单开始：先进回忆编成页、点第 1 格打开选择页（初星助手的扫描按钮用）")
    args = parser.parse_args()
    d = open_tool_device()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    calib = None if args.no_calib else Path(f"reports/live/calib/{stamp}-回忆扫描")
    # 慢滑、终点停住再抬起：列表不带惯性滚动，扫描靠行重叠核对不漏行（以前只在 MaaTools 上停，现在各设备一样）
    picker = Picker(d.screenshot, d.tap,
                    lambda x1, y1, x2, y2: d.swipe(x1, y1, x2, y2, duration=SWIPE_DURATION, hold=SWIPE_HOLD), calib_dir=calib)  # fmt: skip
    if args.from_home:
        from hiro.produce import formation_menu
        from hiro.produce.memory_picker import picker_open

        formation_menu.open_picker(formation_menu.tool_device(d), "memory", ready=lambda img: picker_open(picker.ocr(img)))
    if args.command == "explore":
        out = Path(f"reports/live/memory-explore/{stamp}")
        explore(picker, out)
        print(out)
    else:
        inv = scan(picker, Path(args.out), max_pages=args.max_pages)
        print(f"{len(inv['entries'])} 份回忆 → {args.out}（complete={inv.get('complete')}）")


if __name__ == "__main__":
    main()
