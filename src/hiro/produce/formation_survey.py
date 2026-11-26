"""编成位现状（只读）：进编成菜单、切到模式，支援卡 / 回忆各翻 1–20 号，记下每个编成里有几格放了东西、是哪些卡。
给初星助手的编成方案页用：推荐空着的编成号；要写进去的编成号已经有东西时先提醒（可能是你自己常用的编成）。

    python -m hiro.produce.formation_survey --mode hif --out reports/live/formation-survey.json

输出：{scanned_at, mode, complete, sets: [{kind: support|memory, set, filled, cards: [卡 ID 或 null]}]}。
支援卡格：认出卡、或读到等级就算有东西（认不出的卡也算有，宁可少推荐）；回忆格：大图标不是「未設定」的灰底就算有。
只翻页、读图，不点格子，不点決定。
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SETS = range(1, 21)


def support_row(page: dict) -> tuple[int, list]:
    slots = page.get("slots") or []
    filled = [s for s in slots if s.get("support_card_id") or s.get("level")]
    return len(filled), [s.get("support_card_id") for s in slots]


def memory_row(reads: list[dict]) -> tuple[int, list]:
    return sum(1 for r in reads if not r.get("empty")), [r.get("card") if r.get("known") else None for r in reads]


def survey(
    dev, mode: str, *, kinds=("support", "memory"), read_formation=None, read_icons=None, sets=SETS, stop_at_blank=False
) -> dict:
    """dev：formation_writer.Device。按 kinds 顺序翻遍 sets，返回输出 dict（一个编成号读不到就记 filled=None，接着翻下一个）。
    stop_at_blank：每样读到第一个空着的编成就不往下翻（开局前只要找一个空位，N33：G1 第 3 轮 40 组全读用了 8 分钟）；
    这时 complete=False、stopped_at_blank=True，没读的编成号不出现在 sets 里（按「不知道」处理，不会被当成空的）。"""
    from hiro.produce import formation_menu as fm
    from hiro.produce.formation_writer import FormationWriteError, _settled_step2

    menu = fm.Menu(dev)
    menu.open()
    menu.ensure_mode(mode)
    rows, complete, stopped = [], True, False
    for kind in kinds:
        menu.ensure_tab(kind)
        for n in sets:
            try:
                menu.ensure_set(n)
                if kind == "support":
                    page = _settled_step2(dev, read_formation, view="formation")
                    filled, cards = support_row(page) if page else (None, [])
                else:
                    filled, cards = memory_row(fm._settle_icons(dev) if read_icons is None else read_icons(dev.shoot()))
            except FormationWriteError as e:
                logger.warning("编成位：%s 编成 %d 读不到：%s", kind, n, e)
                filled, cards = None, []
            if filled is None:
                complete = False
            rows.append({"kind": kind, "set": n, "filled": filled, "cards": cards})
            logger.info("编成位：%s 编成 %d：%s", kind, n, "读不到" if filled is None else f"{filled} 格有东西")
            if stop_at_blank and filled == 0:
                if n != list(sets)[-1]:
                    complete, stopped = False, True
                break
    out = {"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode, "complete": complete, "sets": rows}
    if stopped:
        out["stopped_at_blank"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="编成位现状（只读：翻编成号、读图，不点格子）")
    ap.add_argument("--mode", required=True, choices=["hajime", "nia", "hif"])
    ap.add_argument("--out", default="reports/live/formation-survey.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.produce import formation_menu
    from hiro.tool_device import open_tool_device
    from hiro.vision.support_screens import read_support_formation

    dev = formation_menu.tool_device(open_tool_device())
    out = survey(dev, args.mode, read_formation=read_support_formation)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    empty = [r["set"] for r in out["sets"] if r["filled"] == 0]
    print(
        f"读了 {len(out['sets'])} 个编成位，空的 {len(empty)} 个 → {args.out}（complete={out['complete']}）", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
