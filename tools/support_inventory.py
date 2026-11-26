"""支援卡库存（用户 2026-11-02：支援卡不再用おまかせ）→ reports/live/support-inventory.json，给决策的支援卡选择系统用。

两种来源（hiro.vision.support_screens）：
- --list（全量，推荐）：编成页点一个空格进入支援卡「選択」列表（3 列网格，按 Lv 排序，分页「1 / 2」），从顶滚到底、翻页，按卡 ID 合并；
  只读，最后点「キャンセル」，不点決定。
- --live：翻遍 20 个编成看到的卡（不在编成里的看不到）。

用法：
  # 实机：先进「選択」列表（编成页点空格），停在第 1 页顶部
  python tools/support_inventory.py --list
  # 实机：先进「サポートカード編成」页，从编成 1 开始
  python tools/support_inventory.py --live [--formations 20]
  # 离线：给编成页截图（540x960 会放大到 720x1280）
  python tools/support_inventory.py --offline reports/live/rental/01-formation.png
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import cv2

TILE_KEYS = ("support_card_id", "name", "type", "attr", "rarity", "level", "breaks")


logger = logging.getLogger(__name__)


def _load(path: str):
    img = cv2.imread(path)
    return (
        cv2.resize(img, (720, 1280), interpolation=cv2.INTER_CUBIC) if img is not None and img.shape[1] != 720 else img
    )


def aggregate(formations: list[dict]) -> dict:
    """各编成读数 → {cards: {support_card_id: {..., seen_in: [编成号]}}, formations: [...], unknown: [(编成号, 格)]}。
    同一张卡在不同编成读到的等级 / 突破取最大（编成里放的是同一张实卡，读不到的格不覆盖）。"""
    cards, unknown = {}, []
    for f in formations:
        for s in f["slots"]:
            cid = s.get("support_card_id")
            if not cid:
                unknown.append([f.get("number"), s["slot"]])
                continue
            c = cards.setdefault(cid, {k: s.get(k) for k in TILE_KEYS} | {"seen_in": []})
            for k in ("level", "breaks"):
                if s.get(k) is not None and (c[k] is None or s[k] > c[k]):
                    c[k] = s[k]
            if f.get("number") not in c["seen_in"]:
                c["seen_in"].append(f.get("number"))
    return {"cards": cards, "formations": [{k: f.get(k) for k in ("number", "name")} | {"slots": [s.get("support_card_id") for s in f["slots"]]}
                                           for f in formations], "unknown": unknown}  # fmt: skip


LIST_SWIPE = (
    360,
    960,
    360,
    685,
)  # 网格内上滑约 2 行（行距 137.5）：每张卡至少有一屏落在中间完整可见（上滑 2.5 行时有 3 张只出现在被盖住的首末行）
LIST_SETTLE, LIST_MAX_SCREENS = 1.2, 40
CONTROL_RETRIES = 3  # 读不到列表按钮时重截重读几次（N40）


def aggregate_list(screens: list[dict]) -> dict:
    """「選択」列表各屏读数（read_support_list，按页 / 滚动顺序）→ {cards: {id: {..., new, seen_in: ["list"], pages}}, unknown, screens}。
    partial（插画被分页栏挡住）的格不计；认不出卡的格记进 unknown（等级 / 突破 / 属性 + 第几屏），不当新卡。"""
    cards, unknown = {}, []
    for n, scr in enumerate(screens):
        for c in scr["cards"]:
            if c["partial"]:
                continue
            cid = c.get("support_card_id")
            if not cid:
                unknown.append(
                    {
                        "screen": n,
                        "page": scr.get("page"),
                        "level": c.get("level"),
                        "breaks": c.get("breaks"),
                        "attr": c.get("attr"),
                    }
                )
                continue
            e = cards.setdefault(
                cid, {k: c.get(k) for k in TILE_KEYS} | {"new": c.get("new"), "seen_in": ["list"], "pages": []}
            )
            for k in ("level", "breaks"):
                if e[k] is None and c.get(k) is not None:
                    e[k] = c[k]  # 被底部按钮压住的那一屏读不到等级，换一屏补上
            if scr.get("page") not in e["pages"]:
                e["pages"].append(scr.get("page"))
    return {"cards": cards, "unknown": unknown, "screens": len(screens)}


def list_scan() -> list[dict]:
    """实机：「選択」列表从第 1 页顶部开始，每页滚到底（连续两屏没有新卡 = 到底），翻到最后一页，最后点キャンセル。"""
    from hiro.tool_device import LIST_HOLD_S, open_tool_device
    from hiro.vision.support_screens import read_support_list

    d = open_tool_device()
    screens, seen = [], set()

    def read():
        """读一屏；读出来缺「次へ / キャンセル」按钮（文字识别那一帧读空，比如 Vision 刚坏、正在换 RapidOCR）就重截重读几次。
        N40（G2 11-26 Mac rc.7）：翻页时这一帧读空，controls 是 None，直接 r["controls"]["next"] 崩了。"""
        got = read_support_list(d.screenshot())
        for _ in range(CONTROL_RETRIES):
            if got is None or got.get("controls"):
                break
            time.sleep(1.0)
            got = read_support_list(d.screenshot())
        return got

    r = read()
    if r is None:
        print("停下：不在支援卡「選択」列表", flush=True)
        return screens
    pages = r["pages"]
    for page in range(1, pages + 1):
        idle = 0
        for _ in range(LIST_MAX_SCREENS):
            r = read()
            if r is None:
                break
            screens.append(r)
            new = {c["support_card_id"] for c in r["cards"] if c.get("support_card_id")} - seen
            seen |= new
            # 进度：桌面版的进度栏显示最后一行日志，原来这里直接打印卡表（G3 11-26：「1 4 +6 [('すっかり秋色ですね！', 50, 2), …]」）
            print(
                f"支援卡扫描：第 {page} 页第 {len(screens)} 屏，这屏新认出 {len(new)} 张，一共 {len(seen)} 张",
                flush=True,
            )
            logger.debug("读数 %s", [(c["name"], c["level"], c["breaks"]) for c in r["cards"] if not c["partial"]])
            idle = idle + 1 if not new else 0
            if idle >= 2:
                break
            d.swipe(*LIST_SWIPE, duration=1.0, hold=LIST_HOLD_S)  # 终点停住再松手：不带惯性
            time.sleep(LIST_SETTLE)
        if page < pages:
            if r is None or not (r.get("controls") or {}).get("next"):
                print(
                    f"停下：第 {page} 页扫完了，但读不到翻页按钮「次へ」（文字识别可能出了问题）；已认出的 {len(seen)} 张照样入库，可以再扫一次",
                    flush=True,
                )
                return screens
            d.tap(*r["controls"]["next"])
            time.sleep(2.0)
    if r is not None and (r.get("controls") or {}).get("cancel"):
        d.tap(*r["controls"]["cancel"])  # 只读：不点決定
    return screens


def offline(paths: list[str]) -> list[dict]:
    from hiro.vision.support_screens import read_support_formation

    out = []
    for p in paths:
        r = read_support_formation(_load(p))
        if r is not None:
            out.append(r)
    return out


def live(n: int) -> list[dict]:
    from hiro.tool_device import open_tool_device
    from hiro.vision.support_screens import read_support_formation

    d = open_tool_device()
    out, seen = [], set()
    for _ in range(n):
        r = read_support_formation(d.screenshot())
        if r is None:
            print("不在支援卡编成页，停止", flush=True)
            break
        if r["number"] in seen:
            break  # 翻了一圈回到起点
        seen.add(r["number"])
        out.append(r)
        from hiro.produce.formation_survey import support_row

        filled = support_row(r)[0]  # 同编成位调查：认出卡或读得到等级就算有卡（认不出的卡 name 是 None，#145 review）
        print(f"支援卡编成 {r['number']} 号（{r['name']}）：{filled} 格有卡", flush=True)
        logger.debug("读数 %s", [(s["name"], s["level"], s["breaks"]) for s in r["slots"]])
        d.tap(*r["arrows"]["next"])
        time.sleep(1.5)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--live", action="store_true")
    g.add_argument("--offline", nargs="+", metavar="PNG")
    g.add_argument("--list", action="store_true", help="「選択」列表全量扫描")
    g.add_argument("--list-offline", nargs="+", metavar="PNG", help="「選択」列表截图离线汇总")
    ap.add_argument("--formations", type=int, default=20)
    ap.add_argument("--out", default="reports/live/support-inventory.json")
    ap.add_argument(
        "--from-home",
        action="store_true",
        help="和 --list 一起用：从首页 / 编成菜单开始，先进支援卡编成页、点第 1 格打开「選択」列表",
    )
    a = ap.parse_args()
    if a.list or a.list_offline:
        from hiro.vision.support_screens import read_support_list

        try:
            if a.list and a.from_home:
                from hiro.produce import formation_menu
                from hiro.tool_device import open_tool_device
                from hiro.vision.support_screens import read_support_formation

                formation_menu.open_picker(formation_menu.tool_device(open_tool_device()), "support",
                                           ready=lambda img: read_support_list(img) is not None, read_formation=read_support_formation)  # fmt: skip
            screens = (
                list_scan() if a.list else [r for p in a.list_offline if (r := read_support_list(_load(p))) is not None]
            )
        finally:
            if a.list and a.from_home:  # 从首页进来的就回首页（G1-F1：读完停在编成页，下一个任务开头不在首页）
                from hiro.runtime import nav
                from hiro.tool_device import open_tool_device

                nav.back_home(open_tool_device())
        inv = {"generated": time.time(), "source": "list", **aggregate_list(screens)}
    else:
        formations = offline(a.offline) if a.offline else live(a.formations)
        inv = {
            "generated": time.time(),
            "source": "formations",
            "note": "只含编成里出现过的卡；全量用 --list",
            **aggregate(formations),
        }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"支援卡扫描完成：{len(inv['cards'])} 张", flush=True)
    logger.info("写入 %s", a.out)


if __name__ == "__main__":
    main()
