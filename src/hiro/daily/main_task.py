"""初星課題（首页左下「TAP」横幅进去的「課題」页）：读 NEXT 这一步（编号、要求、本步进度、总进度），能领的奖励领掉。

页面（720x1280，11-25 账号 3 实机，OCR 框）：
- 左上标题「課題」；NEXT 卡片的要求文字 y≈550–645（多行，左半边）；本步进度「0/1」在要求下方 y≈650–675；
- 卡片下方一排编号徽章（36 / 37 / 38），中间那个是当前这一步；
- 右下「N で獲得」与「進捗 36/ 60」（已完成步数 / 总步数）。
课题完成后**奖励自动到账**（11-25 实机）：回主页时弹「課題CLEAR」（完成的那一步 + 获得的道具）、里程碑再弹「報酬獲得」，
底部写「報酬は自動で受け取られます」，只有中下的 ✕。这里只点 ✕ 关掉并记下完成了哪几步，不点别的。
"""

from __future__ import annotations

import logging
import re

from hiro.daily import common
from hiro.runtime import nav

logger = logging.getLogger(__name__)

TEXT_Y = (540, 648)  # NEXT 要求文字的行（中心 y）
TEXT_X_MAX = 470  # 要求文字在左半边（右边是奖励图标的数字）
STEP_PROGRESS_Y = (640, 690)
NUMBER_Y = (715, 770)  # 编号徽章那一排
NUMBER_X = (290, 430)  # 中间（当前这一步）的徽章
OVERALL_Y = (1100, 1145)
OVERALL_X_MIN = 560
HOME_BANNER = (360, 870)  # 首页「TAP」横幅（初星課題 NEXT 的要求文字）：点这里进课题页
POPUP_WORDS = ("課題CLEAR", "報酬獲得")
AUTO_CLAIM_HINT = "自動で受け取"  # 「報酬は自動で受け取られます」
POPUP_CLOSE = (360, 1188)  # 弹窗中下的 ✕
CLEARED_ROW_Y = (470, 580)  # 「課題CLEAR」里完成的那一步：编号圆在左（x<130），要求文字在右
TIMEOUT = 30.0

_FRACTION = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")  # 「20,640/25,000」带千分位逗号


def _center(box) -> tuple[int, int]:
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def at_task_page(boxes) -> bool:
    """课题页：左上「課題」标题 + 右下「進捗」。"""
    title = any("課題" in t and _center(b)[1] < 100 and _center(b)[0] < 250 for t, _, b in boxes)
    return title and any("進捗" in t for t, _, b in boxes)


def parse(boxes) -> dict:
    """课题页 OCR 框 → {number, text, progress: [当前, 目标] | None, done: 已完成步数 | None, total: 总步数 | None}。
    读不到的字段给 None，不猜。编号读不到时用「已完成步数 + 1」（NEXT 就是下一步）。"""
    lines = sorted(
        ((t, b) for t, _, b in boxes if TEXT_Y[0] <= _center(b)[1] <= TEXT_Y[1] and _center(b)[0] <= TEXT_X_MAX),
        key=lambda tb: (tb[1][1], tb[1][0]),
    )
    text = "".join(t.strip() for t, _ in lines)
    progress = None
    for t, _, b in boxes:
        cx, cy = _center(b)
        if (
            STEP_PROGRESS_Y[0] <= cy <= STEP_PROGRESS_Y[1]
            and cx <= TEXT_X_MAX
            and (m := _FRACTION.fullmatch(t.strip()))
        ):
            progress = [int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))]
            break
    done = total = None
    for t, _, b in boxes:
        cx, cy = _center(b)
        if OVERALL_Y[0] <= cy <= OVERALL_Y[1] and cx >= OVERALL_X_MIN and (m := _FRACTION.search(t)):
            done, total = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            break
    number = None
    for t, _, b in boxes:
        cx, cy = _center(b)
        if NUMBER_Y[0] <= cy <= NUMBER_Y[1] and NUMBER_X[0] <= cx <= NUMBER_X[1] and (m := re.search(r"\d+", t)):
            number = int(m.group(0))
            break
    if number is None and done is not None:
        number = done + 1
    return {"number": number, "text": text, "progress": progress, "done": done, "total": total}


def cleared_popup(boxes) -> dict | None:
    """主页的课题完成弹窗 → {kind: clear | reward, number, text}；不是这种弹窗 None。"""
    texts = [t.replace(" ", "") for t, _, _ in boxes]
    if not any(AUTO_CLAIM_HINT in t for t in texts):
        return None
    if any("課題CLEAR" in t for t in texts):
        row = sorted(((t, b) for t, _, b in boxes if CLEARED_ROW_Y[0] <= _center(b)[1] <= CLEARED_ROW_Y[1]),
                     key=lambda tb: (tb[1][1], tb[1][0]))  # fmt: skip
        number = next((int(t) for t, b in row if t.strip().isdigit() and _center(b)[0] < 130), None)
        text = "".join(t.strip() for t, b in row if _center(b)[0] >= 130)
        return {"kind": "clear", "number": number, "text": text}
    if any("報酬獲得" in t for t in texts):
        return {"kind": "reward", "number": None, "text": ""}
    return None


def close_popups(rt, limit: int = 30) -> list[dict]:
    """把主页上的「課題CLEAR / 報酬獲得」弹窗逐个点 ✕ 关掉（奖励已自动到账），返回关掉的那些。"""
    closed: list[dict] = []
    for _ in range(limit):
        f = common.frame(rt)
        p = cleared_popup(f.boxes)
        if p is None:
            break
        if p["kind"] == "clear":
            logger.info("初星課題：第 %s 步完成「%s」（奖励自动到账）", p["number"], p["text"])
        closed.append(p)
        rt.device.tap(*POPUP_CLOSE)
        rt.sleep(1.5)
    return closed


def enter(rt, closed: list[dict] | None = None):
    """首页 → 课题页，返回课题页那一帧。途中弹出的课题完成弹窗（回首页后常晚一拍才弹）点 ✕ 关掉，记进 closed。"""
    nav.goto_home(rt)

    def step(f):
        if at_task_page(f.boxes):
            return f
        if (p := cleared_popup(f.boxes)) is not None:
            if p["kind"] == "clear":
                logger.info("初星課題：第 %s 步完成「%s」（奖励自动到账）", p["number"], p["text"])
            if closed is not None:
                closed.append(p)
            rt.device.tap(*POPUP_CLOSE)
            rt.sleep(1.5)
            return None
        if nav.at_home(f):
            rt.device.tap(*HOME_BANNER)
            rt.sleep(1.5)
        return None

    return rt.drive(step, TIMEOUT, "进初星課題页", every=0.5)


def run(rt) -> dict:
    """回主页、关掉课题完成弹窗（记下完成了哪几步），再进课题页读 NEXT。返回 {cleared: [...], next: {...}}。"""
    # 弹窗盖在首页上时 goto_home 认不出首页、会直接点 ✕（Common.ButtonIconClose），记不下完成了哪一步：先自己关一遍
    cleared = close_popups(rt)
    nav.goto_home(rt)
    cleared += close_popups(rt)
    f = enter(rt, closed=cleared)
    nxt = parse(f.boxes)
    logger.info("初星課題：NEXT 第 %s 步「%s」进度 %s，总进度 %s/%s", nxt["number"], nxt["text"], nxt["progress"],
                nxt["done"], nxt["total"])  # fmt: skip
    # 读完回主页：后面开的任务（扫描、写编成、开局）都要从主页开始（11-25 停在课题页，scan-memories 直接退出）
    nav.goto_home(rt)
    return {"cleared": [c for c in cleared if c["kind"] == "clear"], "rewards": sum(c["kind"] == "reward" for c in cleared),
            "next": nxt}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    """命令行（初星助手的「初星課題」任务）：python -m hiro.daily.main_task --out out.json。"""
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="初星課題：关掉课题完成弹窗（奖励自动到账），读 NEXT 这一步")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.daily import Popup, make_runtime
    from hiro.devices import open_device

    dev = open_device()
    dev.connect()
    rt = make_runtime(dev, popup=Popup())
    result = run(rt)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    a = result["next"]
    done = "、".join(str(c["number"]) for c in result["cleared"]) or "无"
    print(
        f"初星課題：这次完成 {done}；NEXT 第 {a['number']} 步：{a['text']}（进度 {a['progress']}，总 {a['done']}/{a['total']}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
