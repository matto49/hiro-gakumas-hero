"""按开局编成方案在编成菜单里写支援卡 / 回忆，写完回读核对（hiro.produce.formation_menu）。

开局第 2 / 3 步只核对：回忆（以及想提前写好的支援卡）用这个工具在菜单里写。游戏停在主页或「プロデュース編成」页时运行：

    PYTHONPATH=src python tools/write_formation.py --mode hajime [--what support,memory] [--dry-run]

- --mode：hajime（初）/ nia（N.I.A）/ hif（H.I.F）。菜单里的编成按模式分开存，模式不对会先「プロデュース切替」。
- --what：写哪些，缺省两样都写（先支援卡后回忆）；编成号用方案的 support.set / memory.set。
- --dry-run：只进菜单、翻到编成号、报和方案差哪几格，不点格子。
- 方案文件：HIRO_FORMATION_PLAN，缺省 conf/formation-plan.json。回忆按方案里的库存位置（cell）找，库存是在菜单的回忆选择页扫的。
- 每一步的截图存 reports/live/calib/<时间>-编成菜单/。对不上就キャンセル 停下（退出码 3），不盲选。
- 写完核对通过（或 --dry-run 时两样都不用改）就在方案上记 written[模式]：hiro run start 开局前据此判断方案已经写进游戏。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="编成菜单里按方案写支援卡 / 回忆")
    ap.add_argument("--mode", required=True, choices=["hajime", "nia", "hif"])
    ap.add_argument("--what", default="support,memory", help="support / memory，逗号分隔，按顺序写")
    ap.add_argument("--dry-run", action="store_true", help="只报差异，不改")
    ap.add_argument(
        "--blank-only",
        default="",
        help="support / memory，逗号分隔：这几样只在编成位全空时才写，有卡就停下（开局前自动准备用）",
    )
    args = ap.parse_args()
    what = tuple(w.strip() for w in args.what.split(",") if w.strip())
    if not what or any(w not in ("support", "memory") for w in what):
        ap.error("--what 只能是 support / memory")
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.produce import formation_menu
    from hiro.produce.formation_writer import Device, FormationWriteError
    from hiro.produce.memory_picker import SWIPE_DURATION, SWIPE_HOLD, Picker
    from hiro.tool_device import open_tool_device
    from hiro.vision.ocr import screen_texts
    from hiro.vision.support_screens import read_support_formation, read_support_list

    import cv2

    plan_path = Path(os.environ.get("HIRO_FORMATION_PLAN") or "conf/formation-plan.json")
    if not plan_path.exists():
        print(f"没有方案文件 {plan_path}（python -m hiro.brain.formation_plan 生成）", file=sys.stderr)
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    d = open_tool_device()
    from hiro.imgio import calib_root, imwrite

    calib_dir = calib_root() / f"{time.strftime('%Y%m%d-%H%M%S')}-编成菜单"
    n = [0]

    def calib(img, tag):
        calib_dir.mkdir(parents=True, exist_ok=True)
        n[0] += 1
        # 目录名带中文（「编成菜单」）：cv2.imwrite 在 Windows 上会静默不写（11-25 两次停下都没留下截图）
        imwrite(calib_dir / f"{n[0]:02d}-{tag}.jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    dev = Device(shoot=d.screenshot, click=d.tap, swipe=lambda x1, y1, x2, y2, dur=None: d.swipe(x1, y1, x2, y2, duration=dur or 0.3),
                 ocr=lambda img: [(t.text, 1.0, tuple(int(v) for v in t.rect)) for t in screen_texts(img)], sleep=time.sleep,
                 calib=calib)  # fmt: skip

    def make_picker():  # 回忆列表慢滑 + 终点停住，不带惯性（同开局第 3 步、hiro.memory_scan）
        return Picker(
            d.screenshot,
            d.tap,
            lambda x1, y1, x2, y2: d.swipe(x1, y1, x2, y2, duration=SWIPE_DURATION, hold=SWIPE_HOLD),
            calib_dir=calib_dir,  # 回忆列表对不上时的帧也存进这次的截图目录（#148 review：原来不传，snap 什么都不写）
        )

    try:
        out = formation_menu.run(dev, plan, args.mode, what=what, make_picker=make_picker, read_formation=read_support_formation,
                                 read_list=read_support_list, dry_run=args.dry_run,
                                 blank_only=tuple(w.strip() for w in args.blank_only.split(",") if w.strip()))  # fmt: skip
    except FormationWriteError as e:
        print(f"停下：{e}（截图在 {calib_dir}）", file=sys.stderr)
        return 3
    for kind, done in out.items():
        print(f"{kind}：{'不用改' if not done else done}")  # noqa: SIM212
    print("只报了差异，没改" if args.dry_run else "写完，回读核对通过")
    if not args.dry_run or not any(out.values()):  # 写完核对通过，或只看差异时两样都不用改：游戏里就是这套编成
        from hiro.brain.formation_plan import mark_written

        mark_written(plan_path, args.mode, what)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
