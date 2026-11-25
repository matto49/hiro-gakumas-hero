"""竞赛编队（コンテスト → ユニット編成）：用「おまかせ」按现有回忆重排，读出「総合力合計」。

初星課題「コンテストのユニット総合力を{N}以上にしよう」（MissionType_AbsolutePvpRateUnitOverallPower）看的就是这个数；
总合力只取决于回忆，打完一局有了更强的回忆，再跑一次就会变高。
页面（720x1280，11-25 账号 3 实机）：
- 首页底栏「コンテスト」→ 竞赛入口页（大按钮「コンテスト」，GRADE / レートPt）→ 竞赛主页（左上「コンテスト」，右侧「ユニット編成」）；
- ユニット編成页：左上「ユニット編成」，顶部「総合力合計 20640」（OCR 常连成一个框），底部「おまかせ」；
- おまかせ →「おまかせ編成：…よろしいですか？」→「決定」→「おまかせ確認：おまかせでメモリーが編成されました」→「閉じる」。
只点上面这些按钮；不点「リハーサル開始」、对战、交換所。
"""

from __future__ import annotations

import logging
import re

from hiro.runtime import nav

logger = logging.getLogger(__name__)

TAB_Y = 1080  # 底栏在这条线下面
TIMEOUT = 30.0


def _center(box) -> tuple[int, int]:
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def find(boxes, word: str, *, y_min: int = 0, y_max: int = 1280, x_min: int = 0):
    for t, _, b in boxes:
        cx, cy = _center(b)
        if word in _norm(t) and y_min <= cy <= y_max and cx >= x_min:
            return cx, cy
    return None


def total_power(boxes) -> int | None:
    """最上面那个「総合力合計」（自己的；竞赛主页下面还有对手的）的数：同一个框里（「総合力合計 20640」）或右边紧挨的框里；读不到 None。"""
    for t, _, b in sorted(boxes, key=lambda x: _center(x[2])[1]):
        s = _norm(t)
        cy = _center(b)[1]
        if "総合力合計" not in s:
            continue
        if m := re.search(r"(\d{4,6})", s.replace(",", "")):
            return int(m.group(1))
        right = sorted(
            ((t2, b2) for t2, _, b2 in boxes if b2[0] > b[2] - 5 and abs(_center(b2)[1] - cy) <= 25),
            key=lambda tb: tb[1][0],
        )
        for t2, _b2 in right:
            if m := re.fullmatch(r"(\d{4,6})", _norm(t2).replace(",", "")):
                return int(m.group(1))
        return None  # 最上面那个读不出数：不往下拿对手的
    return None


def at_unit_page(boxes) -> bool:
    return find(boxes, "ユニット編成", y_max=100) is not None and find(boxes, "おまかせ", y_min=1000) is not None


def open_unit(rt):
    """首页 → コンテスト（底栏）→ コンテスト（入口大按钮）→ ユニット編成，返回编队页那一帧。"""
    nav.goto_home(rt)

    def step(f):
        b = f.boxes
        if at_unit_page(b):
            return f
        if find(b, "コンテスト", y_max=100) and (p := find(b, "ユニット編成", y_min=480, y_max=600, x_min=450)):
            rt.device.tap(*p)  # 竞赛主页右侧「ユニット編成」
        elif (p := find(b, "コンテスト", y_min=800, y_max=1000, x_min=400)) is not None and find(b, "GRADE"):
            rt.device.tap(*p)  # 竞赛入口页的大按钮
        elif nav.at_home(f) and (p := find(b, "コンテスト", y_min=TAB_Y, x_min=400)) is not None:
            rt.device.tap(*p)  # 首页底栏
        else:
            return None
        rt.sleep(2.0)
        return None

    return rt.drive(step, TIMEOUT, "进竞赛编队", every=0.5)


def omakase(rt) -> None:
    """点「おまかせ」→「決定」→「閉じる」，回到编队页。"""
    state = {"asked": False}

    def step(f):
        b = f.boxes
        if find(b, "おまかせ確認"):
            if (p := find(b, "閉じる", y_min=1000)) is not None:
                rt.device.tap(*p)
                rt.sleep(1.5)
            return None
        if find(b, "おまかせ編成"):
            if (p := find(b, "決定", y_min=1000)) is not None:
                rt.device.tap(*p)
                rt.sleep(2.0)
            return None
        if at_unit_page(b):
            if state["asked"]:
                return f
            if (p := find(b, "おまかせ", y_min=1000)) is None:
                return None
            rt.device.tap(*p)
            state["asked"] = True
            rt.sleep(1.5)
        return None

    rt.drive(step, TIMEOUT, "おまかせ编队", every=0.5)


def run(rt, *, apply: bool = True) -> dict:
    """进编队页读总合力；apply=True 时先おまかせ重排再读。返回 {before, after}。"""
    f = open_unit(rt)
    before = total_power(f.boxes)
    after = before
    if apply:
        omakase(rt)
        rt.sleep(1.0)
        f = rt.drive(lambda f: f if at_unit_page(f.boxes) else None, TIMEOUT, "回到编队页", every=0.5)
        after = total_power(f.boxes)
    logger.info("竞赛编队：总合力 %s → %s", before, after)
    return {"before": before, "after": after}


def main(argv: list[str] | None = None) -> int:
    """命令行（初星助手的「竞赛编队」任务）：python -m hiro.daily.contest_unit --out out.json [--read-only]。"""
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="竞赛编队：おまかせ重排后读总合力")
    ap.add_argument("--out", required=True)
    ap.add_argument("--read-only", action="store_true", help="只读总合力，不重排")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.daily import Popup, make_runtime
    from hiro.devices import open_device

    dev = open_device()
    dev.connect()
    rt = make_runtime(dev, popup=Popup())
    result = run(rt, apply=not args.read_only)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"竞赛编队：总合力 {result['before']} → {result['after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
