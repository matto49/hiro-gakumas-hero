"""培育日程列表（选行动页左上「中間まで N週」框点开，客户端 ScheduleListView）的读数。

实拍（初 MASTER 第 4 周，协调 11-18 手动滑动截全 18 周，fixtures schedule_list/schedule-list-master-*.png）：白色面板，
- 顶部三属性当前值；下面竖向时间轴，每行左边「N週」（x ≈ 175；OCR 常把「3」读成「ろ」），越往上周数越大，要上下滑动才看得全；
- 每行右边是这一周能选的行动图标（中心 x ≈ 306 / 406 / 506 / 606，约 80x80），按图标底色认（用户 11-17：图标优先）：
  课程 Vo 粉 / Da 蓝 / Vi 黄、授業 紫、お出かけ 青绿、相談 绿、活動支給 橙；
- 追い込み周是一条彩色长条「追い込みレッスン」；考试周是带「中間試験 / 最終試験」标题和审查基准柱（分数线 150 / 400）的大卡；
- 当前周左边有青色胶囊 + 头像；已经过去的周，选了的图标带蓝色 ✓、没选的变淡；
- 底部 tab「スケジュール / 育成目標 / 編成」，✕ 在 (360, 1190)。
"""

from __future__ import annotations

import re

import cv2
import numpy as np

CLOSE = (360, 1190)
LIST_Y = (285, 1075)  # 时间轴可见范围（上面是三属性、下面是 tab）
LABEL_X_MAX = 260
ICON_XS = (306, 406, 506, 606)
ICON_HALF = 30
ICON_MIN = 0.5  # 图标格里饱和像素占比（实测 0.71–0.84；空格 0；变淡的 < 0.5）
ROW_H = 145  # 普通行行距（相邻「N週」的 y 差）
# 图标底色（OpenCV 色相，饱和像素的中位数；实拍）
KIND_HUE = (("present", 0, 9), ("lesson_vo", 155, 175), ("allowance", 10, 19), ("sales", 125, 140), ("lesson_vi", 20, 30), ("shop", 65, 84), ("outing", 85, 94),
            ("lesson_da", 95, 104), ("class", 105, 118))  # fmt: skip
KIND_OPTION = {"lesson_vo": ("training", "Vo"), "lesson_da": ("training", "Da"), "lesson_vi": ("training", "Vi"), "class": ("class", None),
               "outing": ("outing", None), "shop": ("shop", None), "allowance": ("allowance", None),
               "sales": ("sales", None), "present": ("present", None)}  # fmt: skip
# N.I.A（11-18 MASTER 实拍 nia-schedule-list-late.jpg）：営業 紫色文件夹（色相 132）、差し入れ 橙色（7）、相談 绿色（同初）、
# 自主レッスン 粉 / 蓝 / 黄（同初的课程，这里也记 training）
CURRENT_X = (40, 130)  # 当前周左边的青色胶囊 + 头像


def _week(text: str) -> int | None:
    m = re.fullmatch(r"([\dろ]{1,2})週", text.replace(" ", ""))
    return int(m.group(1).replace("ろ", "3")) if m else None


def _icon(hsv: np.ndarray, cx: int, y: int) -> tuple[str | None, bool | None]:
    """一格图标 → (种类, 是否变淡)。正常图标饱和像素占比 0.71–0.84；变淡的（过去的周没选的那项）饱和度掉到 30–90，
    按放宽的饱和度再看（实测 0.86），色相不变。空格 (None, None)。"""
    r = hsv[max(0, y - ICON_HALF) : y + ICON_HALF, cx - ICON_HALF : cx + ICON_HALF]
    for sat, faded in (((r[..., 1] > 90) & (r[..., 2] > 120), False), ((r[..., 1] > 30) & (r[..., 2] > 150), True)):
        if float(sat.mean()) >= ICON_MIN:
            h = int(np.median(r[..., 0][sat]))
            return next((k for k, lo, hi in KIND_HUE if lo <= h <= hi), None), faded
    return None, None


def read_schedule_list(img: np.ndarray, texts=None) -> dict | None:
    """一屏日程列表 → {weeks: {N: {...}}, current_week}；不是日程列表 None。每周：
    {"options": [{option, attr}], "faded": [bool…], "kind": normal / oikomi / exam, "exam": {stage, line}?}。"""
    if img is None or img.shape[:2] != (1280, 720):
        return None
    if texts is None:
        from hiro.vision.ocr import screen_texts

        texts = screen_texts(img)
    tx = [(t.text.replace(" ", ""), int(t.rect.center.x), int(t.rect.center.y)) for t in texts]
    if not any("スケジュール" in s for s, _, y in tx if y > 1070):
        return None
    labels = sorted(((w, y) for s, x, y in tx if x < LABEL_X_MAX and LIST_Y[0] <= y <= LIST_Y[1] and (w := _week(s)) is not None), key=lambda r: r[1])
    labels = _fill_gaps(labels)
    if not labels:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    weeks: dict[int, dict] = {}
    current = None
    for w, y in labels:
        row = [s for s, x, yy in tx if x >= LABEL_X_MAX and abs(yy - y) <= 70]
        exam_title = next((s for s, x, yy in tx if x >= LABEL_X_MAX and -90 <= yy - y <= 10 and "試験" in s), None)
        cur = hsv[y - 25 : y + 25, CURRENT_X[0] : CURRENT_X[1]]
        if float(((cur[..., 1] > 90) & (cur[..., 2] > 120)).mean()) > 0.35:
            current = w
        if exam_title is not None:
            line = next((int(m.group(1)) for s, x, yy in tx if x >= LABEL_X_MAX and abs(yy - y) <= 60 and (m := re.match(r"(\d{2,4})", s))), None)
            stage = "final" if "最終" in exam_title else "mid1" if "中間" in exam_title else None
            # 审查基准柱的高度和权重不成正比（柱顶压着属性图标、不从零画起；量出来中間 Vo/Da/Vi 0.38/0.39/0.23，主数据 0.40/0.33/0.27，
            # 连最高的是哪个都错）：不读柱子，审查基准按主数据查（hiro.brain.hajime_nia.exam_criteria）。这里只给场次和分数线
            weeks[w] = {"kind": "exam", "options": [], "faded": [], "exam": {"stage": stage, "line": line}}
            continue
        if any("追い込み" in s for s in row):
            weeks[w] = {"kind": "oikomi", "options": [{"option": "training", "attr": None, "oikomi": True}], "faded": [False]}
            continue
        if not (LIST_Y[0] + ICON_HALF + 5 <= y <= LIST_Y[1] - ICON_HALF - 5):
            continue  # 行被上面的属性栏 / 下面的 tab 盖住一截：不读（滑到边缘读出过乱的）
        opts, faded = [], []
        for cx in ICON_XS:
            kind, is_faded = _icon(hsv, cx, y)
            if kind is None:
                continue
            option, attr = KIND_OPTION[kind]
            opts.append({"option": option, "attr": attr})
            faded.append(bool(is_faded))
        weeks[w] = {"kind": "normal", "options": opts, "faded": faded}
    return {"weeks": weeks, "current_week": current, "close": list(CLOSE)}


def _fill_gaps(labels: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """OCR 漏掉的「N週」（灰字）：相邻两个读到的周差 2、y 差约两行（普通行）时补中间那周。"""
    out = list(labels)
    for (w1, y1), (w2, y2) in zip(labels, labels[1:]):
        if w1 - w2 == 2 and abs((y2 - y1) - 2 * ROW_H) <= 20:
            out.append((w1 - 1, (y1 + y2) // 2))
    return sorted(out, key=lambda r: r[1])


def merge(screens: list[dict], current_week: int | None = None) -> dict:
    """几屏合成整局日程 {"weeks": [{week, kind, options, faded, done, chosen?, exam?}…], "current_week", "total"}；
    同一周后读到的覆盖先读到的（越往后越停稳）。
    已经过去的周（< 当前周）标 done，选的那项 = 唯一没变淡的图标（读不出 None）。"""
    weeks: dict[int, dict] = {}
    cur = current_week
    for s in screens:
        for w, e in (s.get("weeks") or {}).items():
            old = weeks.get(w)
            if old is not None and old.get("kind") == "exam" and e.get("kind") == "exam" and not (e.get("exam") or {}).get("line"):
                continue  # 考试周：保留读到了分数线的那一屏
            weeks[w] = e
        cur = cur or s.get("current_week")
    out = []
    for w in sorted(weeks):
        e = {"week": w, **weeks[w]}
        if cur is not None:
            e["done"] = w < cur
            if e["done"] and e.get("faded") and e["faded"].count(False) == 1 and len(e["faded"]) > 1:
                e["chosen"] = e["options"][e["faded"].index(False)]
        out.append(e)
    # weeks 是按周排好的列表（每项带 week）：写进 trace 的 JSON 后整数键会变字符串，续跑打底读回来不一致
    return {"weeks": out, "current_week": cur, "total": max(weeks) if weeks else None}
