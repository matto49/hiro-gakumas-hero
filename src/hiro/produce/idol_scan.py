"""偶像卡库存扫描（只读）：首页 →「アイドル」→「Pアイドル」→「一覧」，逐张点卡读顶部的卡名 / 角色 / 稀有度 / プラン / 推荐效果，
往下滑到底，最后点左下「<<」退出（不点「決定」，选中的偶像不会变）。

    python -m hiro.produce.idol_scan --out reports/live/idol-inventory.json

一览页（720x1280 逻辑坐标，11-19 Mac 账号 2 实测）：上方是选中卡的详情（卡名 y≈39、角色名 y≈73、稀有度 x≈84 y≈58、
プラン x≈650 y≈89、推荐效果 x>480 y≈164），下方 4 列网格（列中心 x 105 / 275 / 445 / 615，可见区 y≈590–960，行距 220），
再往下被筛选按钮（y≈975–1025）和「決定」挡住。卡名 + 角色名在主数据里唯一（140 张实测无重复），入库时按资料库对上 idol_card_id。
输出：{scanned_at, complete, idols: [{title, character, rarity, plan, effect, idol_card_id?}]}。
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from hiro.devices.active import sleep as _sleep
from hiro.vision.textnorm import fold

logger = logging.getLogger(__name__)

NAV_IDOL = (225, 1150)  # 首页底栏「アイドル」
NAV_P_IDOL = (571, 925)  # アイドル页「Pアイドル」（育成）
LIST_BUTTON = (75, 880)  # 偶像详情页左下「一覧」
BACK = (85, 1210)  # 一览页左下「<<」：不点決定直接退出
HOME = (75, 1215)  # 偶像详情页左下「⌂」
COL_X = (105, 275, 445, 615)
# 到没到偶像详情页 / 一览页：认出其中任意一个字就算（G3 11-26 Windows DMM：游戏窗口 540x960，整屏 OCR 读不出左下带图标的小字「一覧」，
# 其实已经在详情页，扫描却停下）；整屏读不出时再把按钮那一小块放大读一次
DETAIL_MARKS = ("一覧", "才能開花", "特訓", "おすすめ効果")
DETAIL_CROP = (10, 845, 230, 915)  # 详情页左下「一覧」按钮附近（LIST_BUTTON (75, 880)）
LIST_MARKS = ("決定",)
LIST_CROP = (150, 1060, 570, 1230)  # 一览页底部「決定」附近
GRID_TOP, GRID_BOTTOM = 590, 960  # 卡片网格可见区（上面是「現在状態」，下面是筛选按钮和「決定」）
# 滑完卡片不一定停在固定位置（带惯性，实测挪动约为手指距离的 1.5 倍），所以每屏按 4 个高度把可见区点一遍；
# 点到卡片之间的缝没反应，点到同一张卡靠卡名去重
ROW_Y = (620, 730, 840, 940)
SWIPE = (110, 900, 110, 700)  # 在最左列里滑（x=110 在筛选按钮左边），一次挪约 300 像素，小于点击覆盖的高度
MAX_SCREENS = 40
PLANS = {"センス": "sense", "ロジック": "logic", "アノマリー": "anomaly"}
RARITIES = ("SSR", "SR", "R")


def _at(boxes, pred) -> list[str]:
    return [t for t, b in boxes if pred((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)]


def read_header(boxes) -> dict | None:
    """一览页 / 详情页顶部的选中卡：{title, character, rarity, plan, effect}；卡名或角色名读不到时 None（不猜）。
    boxes：[(文字, (x1, y1, x2, y2))]，720x1280 坐标。"""
    names = sorted(
        ((b[1], t.strip()) for t, b in boxes if (b[1] + b[3]) // 2 < 90 and (b[0] + b[2]) // 2 > 120 and t.strip()),
        key=lambda x: x[0],
    )
    names = [t for _, t in names if t not in RARITIES and not any(p in t for p in PLANS)]
    names = [t for t in names if len(t.replace(" ", "")) >= 2]  # 详情页右上角的「L」等单字图标不是卡名
    if len(names) < 2:
        return None
    plan_at = _at(boxes, lambda x, y: x > 560 and y < 110)
    rarity_at = _at(boxes, lambda x, y: x < 130 and y < 90)
    if not any(k in t for t in plan_at for k in PLANS) and not any(
        t.replace(" ", "") == r for t in rarity_at for r in RARITIES
    ):
        # 一张偶像卡的顶部总有プラン（稀有度 R 卡常读不到）；两样都没有就不是卡的详情（11-25 DMM：扫到最后读出「【rr】LL」「【-9】3J」）
        return None
    rarity = next(
        (r for t in _at(boxes, lambda x, y: x < 130 and y < 90) for r in RARITIES if t.replace(" ", "") == r), None
    )
    plan = next((v for t in _at(boxes, lambda x, y: x > 560 and y < 110) for k, v in PLANS.items() if k in t), None)
    effect = next((t.strip() for t in _at(boxes, lambda x, y: x > 480 and 145 < y < 185) if t.strip()), None)
    return {
        "title": names[0],
        "character": names[1].replace(" ", "").replace("　", ""),
        "rarity": rarity,
        "plan": plan,
        "effect": effect,
    }


def card_key(c: dict) -> tuple[str, str]:
    return c["title"], c["character"]


def scan(shoot, tap, swipe, ocr, sleep=_sleep, *, max_screens: int = MAX_SCREENS) -> dict:
    """停在一览页：逐行逐列点卡、读顶部；往下滑，连续两屏没有新卡算到底。最后点「<<」退出。返回输出 dict。"""
    seen: dict[tuple[str, str], dict] = {}
    idle, complete = 0, False
    for _ in range(max_screens):
        new = 0
        for y in ROW_Y:
            for x in COL_X:
                tap(x, y)
                sleep(0.8)
                head = read_header(ocr(shoot()))
                if head is None:
                    continue
                if card_key(head) not in seen:
                    seen[card_key(head)] = head
                    new += 1
                    logger.info(
                        "偶像卡 %d：【%s】%s %s %s %s",
                        len(seen),
                        head["title"],
                        head["character"],
                        head["rarity"],
                        head["plan"],
                        head["effect"],
                    )
        idle = idle + 1 if new == 0 else 0
        if idle >= 2:
            complete = True
            break
        swipe(*SWIPE)
        sleep(1.2)
    tap(*BACK)
    sleep(1.0)
    return {"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), "complete": complete, "idols": list(seen.values())}


# 汉字「一」和长音「ー」字形一样，OCR 会互认（11-26「【一つ踏み出した先に】」读成「ーつ」）：卡名两边都统一成长音再比
_DASHES = str.maketrans({"一": "ー", "－": "ー", "-": "ー", "―": "ー", "ｰ": "ー"})


def _title_key(title: str) -> str:
    return fold(title).translate(_DASHES)


def attach_ids(inv: dict) -> dict:
    """按主数据对上 idol_card_id：IdolCard.name == 卡名、角色姓名 == 读到的角色名，两边都 fold（去空格、全角半角统一）再比；
    主数据取 game.db + 模拟器主数据里 game.db 没有的新卡。对上后稀有度没读到的按主数据补。
    主数据有的卡名用全角标点（「Yellow Big Bang！」），OCR 读成半角「!」，不 fold 就对不上、卡面出不来（11-25）。对不上的留空，入库时再按资料库对。"""
    try:
        from hiro.state.context import catalog

        cat = catalog()
        chars = {fold(f"{r['lastName']}{r['firstName']}"): r["id"] for r in cat.rows_merged("Character")}
        cards = cat.rows_merged("IdolCard")
        index = {(_title_key(r["name"]), r["characterId"]): r["id"] for r in cards}
        rarity = {r["id"]: str(r.get("rarity") or "").rsplit("_", 1)[-1].upper() or None for r in cards}
    except Exception as e:  # noqa: BLE001  主数据不在：只出原文
        logger.info("主数据不可用，idol_card_id 留空：%s", e)
        return inv
    for c in inv["idols"]:
        cid = chars.get(fold(c["character"]))
        c["idol_card_id"] = index.get((_title_key(c["title"]), cid)) if cid else None
        if c["idol_card_id"] and not c.get("rarity"):  # 稀有度角标 OCR 常读不到（R 卡几乎全空）：按主数据补
            c["rarity"] = rarity.get(c["idol_card_id"])
    return inv


def page_seen(img, ocr, marks, crop=None, crop_ocr=None) -> bool:
    """img 上认得出 marks 里任意一个字：先看整屏 OCR（ocr(img) → [(文字, 框)]），没有再对 crop 那块放大读（crop_ocr(块) → [(文字, …)]）。"""
    if any(m in t for t, _ in ocr(img) for m in marks):
        return True
    if crop is None or crop_ocr is None or img is None:
        return False
    x1, y1, x2, y2 = crop
    return any(m in t for t, *_ in crop_ocr(img[y1:y2, x1:x2]) for m in marks)


def main() -> int:
    ap = argparse.ArgumentParser(description="偶像卡库存扫描（只读，从首页开始）")
    ap.add_argument("--out", default="reports/live/idol-inventory.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.tool_device import open_tool_device
    from hiro.vision.ocr import ocr as ocr_crop
    from hiro.vision.ocr import screen_texts

    d = open_tool_device()

    def ocr(img):
        return [(t.text, tuple(int(v) for v in t.rect)) for t in screen_texts(img)]

    def has(marks, crop, tries=8):
        for _ in range(tries):
            if page_seen(d.screenshot(), ocr, marks, crop, lambda im: ocr_crop(im, scale=3)):
                return True
            _sleep(0.8)
        return False

    from hiro.runtime import nav

    if not nav.back_home(
        d
    ):  # 底栏「アイドル」按坐标点，先确保在首页（G1-F1：上一个扫描停在メモリー編成页，盲点后 12 秒失败）
        print("停下：游戏不在首页，也没能自己退回首页；回到首页后再扫一次", flush=True)
        return 3
    d.tap(*NAV_IDOL)
    _sleep(2.0)
    d.tap(*NAV_P_IDOL)
    if not has(DETAIL_MARKS, DETAIL_CROP):
        print("停下：没进到偶像详情页（找不到「一覧」）", flush=True)
        return 3
    d.tap(*LIST_BUTTON)
    if not has(LIST_MARKS, LIST_CROP):
        print("停下：没打开偶像一览（找不到「決定」）", flush=True)
        return 3
    inv = attach_ids(scan(d.screenshot, d.tap, lambda x1, y1, x2, y2: d.swipe(x1, y1, x2, y2, duration=0.6), ocr))
    d.tap(*HOME)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(inv['idols'])} 张偶像卡 → {args.out}（complete={inv['complete']}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
