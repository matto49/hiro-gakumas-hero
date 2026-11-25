"""看初星コミュ（主线剧情）：コミュ → 初星コミュ → N章 → 逐话「再生する」再跳过，直到看完指定的话数或遇到还锁着的话。

初星課題里「1章9話をみよう」这类（MissionType_AbsoluteStoryRead）只要看过一次就算，跳过也算看过。
页面（720x1280，11-25 账号 3 实机）：
- 章列表：每章一行「N章」+ 副标题，锁着的带「直前のコミュをみることで解放」；
- 话列表：新的在上，「第10話」左对齐（y 按行排），没看过的上方约 50 像素有「NEW」，锁着的读成「11話」且下方有「…解放」；
- 点一话 → 「再生確認」：画面设定选「縦画面」（横屏会转屏，坐标全变）、ボイス选「ボイスなし」（下载小）→「再生する」；
- 播放中右上角是跳过键（SKIP_BUTTON）→「スキップ確認：未読のコミュです」→「決定」→ 回到话列表，下一话解锁。
只点上面这些字样的按钮；带ジュエル的不点（Popup 中断层也会拦）。
"""

from __future__ import annotations

import logging
import re

from hiro.daily import common
from hiro.runtime import nav

logger = logging.getLogger(__name__)

EPISODE = re.compile(r"^第?\s*(\d+)\s*[話誌]$")
EPISODE_X_MAX = 220  # 话标题在左边
LIST_Y_MIN = 300  # 上面是章标题横幅
NEW_ABOVE = (25, 80)  # NEW 标签在话标题上方这么多像素
LOCK_BELOW = 70  # 「…解放」在锁着的话标题下方这么多像素以内
SKIP_BUTTON = (660, 52)  # 播放中右上角的跳过键
COMMU_TAB_Y = 1080  # 底栏「コミュ」在这条线下面
TIMEOUT = 30.0
PLAY_TIMEOUT = 60.0  # 第一次看要下载剧情资源


def _center(box) -> tuple[int, int]:
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def find(boxes, word: str, *, y_min: int = 0, y_max: int = 1280, x_max: int = 720):
    """第一个包含 word 的框的中心；没有 None。"""
    for t, _, b in boxes:
        cx, cy = _center(b)
        if word in _norm(t) and y_min <= cy <= y_max and cx <= x_max:
            return cx, cy
    return None


def episodes(boxes) -> list[dict]:
    """话列表 → [{n, y, new, locked}]，从上到下。"""
    rows = []
    for t, _, b in boxes:
        cx, cy = _center(b)
        m = EPISODE.match(_norm(t))
        if m is None or cx > EPISODE_X_MAX or cy < LIST_Y_MIN:
            continue
        new = any(_norm(t2) == "NEW" and NEW_ABOVE[0] <= cy - _center(b2)[1] <= NEW_ABOVE[1] for t2, _, b2 in boxes)
        locked = any("解放" in t2 and 0 < _center(b2)[1] - cy <= LOCK_BELOW for t2, _, b2 in boxes)
        rows.append({"n": int(m.group(1)), "y": cy, "new": new, "locked": locked})
    return sorted(rows, key=lambda r: r["y"])


def chapter_row(boxes, chapter: int):
    """章列表里「N章」那一行的中心（不认上面的章标题横幅）；没有 None。"""
    for t, _, b in boxes:
        cx, cy = _center(b)
        if _norm(t) == f"{chapter}章" and cy >= LIST_Y_MIN:
            return cx, cy
    return None


def in_chapter(boxes, chapter: int) -> bool:
    """在 N 章的话列表：顶部横幅是「N章」，下面有话。"""
    head = any(_norm(t) == f"{chapter}章" and _center(b)[1] < LIST_Y_MIN for t, _, b in boxes)
    return head and bool(episodes(boxes))


def next_episode(rows: list[dict], until: int) -> dict | None:
    """该看的下一话：没锁、标着 NEW、话数 ≤ until 里最小的；没有 None。"""
    todo = [r for r in rows if r["new"] and not r["locked"] and r["n"] <= until]
    return min(todo, key=lambda r: r["n"]) if todo else None


def open_chapter(rt, chapter: int):
    """首页 → コミュ → 初星コミュ → N章，返回话列表那一帧。"""
    nav.goto_home(rt)

    def step(f):
        b = f.boxes
        if in_chapter(b, chapter):
            return f
        if (
            ((p := chapter_row(b, chapter)) is not None and find(b, "初星コミュ", y_max=100))
            or (p := find(b, "初星コミュ", y_min=700, y_max=900)) is not None
            or (nav.at_home(f) and (p := find(b, "コミュ", y_min=COMMU_TAB_Y, x_max=200)) is not None)
        ):
            rt.device.tap(*p)
        else:
            return None
        rt.sleep(1.5)
        return None

    return rt.drive(step, TIMEOUT, f"进初星コミュ {chapter}章", every=0.5)


def watch(rt, chapter: int, row: dict) -> None:
    """点一话 → 再生確認（縦画面 / ボイスなし）→ 再生 → 跳过 → 決定 → 回到话列表。"""
    rt.device.tap(360, row["y"])
    rt.sleep(1.5)

    def dialog(f):
        return f if find(f.boxes, "再生確認") else None

    f = rt.drive(dialog, TIMEOUT, f"等第 {row['n']} 話的再生確認", every=0.5)
    for word in ("縦画面", "ボイスなし"):
        if (p := find(f.boxes, word)) is not None:
            rt.device.tap(*p)
            rt.sleep(0.5)
    p = find(f.boxes, "再生する")
    if p is None:
        raise common.TaskTimeout(f"第 {row['n']} 話：再生確認上没有「再生する」")
    rt.device.tap(*p)
    rt.sleep(2.0)

    seen = {"player": False}  # 见过播放画面 / 跳过确认才认「回到列表」（点了再生后的头几帧还是列表）

    def skip(f):
        b = f.boxes
        if find(b, "スキップ確認"):
            seen["player"] = True
            if (p := find(b, "決定", y_min=1000)) is not None:
                rt.device.tap(*p)
                rt.sleep(2.0)
            return None
        if in_chapter(b, chapter):
            return f if seen["player"] and not find(b, "再生確認") else None
        if find(b, "話", y_max=120):  # 播放中：左上「1章9話」，点右上跳过键
            seen["player"] = True
            rt.device.tap(*SKIP_BUTTON)
            rt.sleep(1.0)
        return None

    rt.drive(skip, PLAY_TIMEOUT, f"跳过第 {row['n']} 話", every=0.5)


def run(rt, *, chapter: int = 1, until: int = 99) -> dict:
    """看 N 章里所有 NEW、没锁、话数 ≤ until 的话（看完一话下一话才解锁，看一话重读一次列表）。
    返回 {watched: [话数…], next_locked: 停在哪一话（锁着）| None}。"""
    f = open_chapter(rt, chapter)
    watched: list[int] = []
    for _ in range(30):
        rows = episodes(f.boxes)
        row = next_episode(rows, until)
        if row is None:
            break
        logger.info("初星コミュ %d章：看第 %d 話", chapter, row["n"])
        watch(rt, chapter, row)
        watched.append(row["n"])
        rt.sleep(1.0)
        f = rt.drive(lambda f: f if in_chapter(f.boxes, chapter) else None, TIMEOUT, "回到话列表", every=0.5)
    locked = [r["n"] for r in episodes(f.boxes) if r["locked"]]
    result = {"chapter": chapter, "watched": watched, "next_locked": min(locked) if locked else None}
    logger.info("初星コミュ %d章：看了 %s，下一话（锁着）%s", chapter, watched or "无", result["next_locked"])
    return result


def main(argv: list[str] | None = None) -> int:
    """命令行（初星助手的「看初星コミュ」任务）：python -m hiro.daily.story --out out.json [--chapter 1] [--until 14]。"""
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="看初星コミュ：逐话再生并跳过，直到 --until 或遇到锁着的话")
    ap.add_argument("--out", required=True)
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--until", type=int, default=99)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.daily import Popup, make_runtime
    from hiro.devices import open_device

    dev = open_device()
    dev.connect()
    rt = make_runtime(dev, popup=Popup())
    result = run(rt, chapter=args.chapter, until=args.until)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"初星コミュ {args.chapter}章：看了 {result['watched'] or '无'}，下一话（锁着）{result['next_locked']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
