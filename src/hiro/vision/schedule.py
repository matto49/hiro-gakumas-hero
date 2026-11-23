"""考试审查日程面板（考试左上「残りターン / 属性 %」那块点开，客户端 ExamAuditionBonusScheduleView）的读数。

实拍（初 PRO 中間第 1 回合前，协调 11-18，fixtures pages/exam-schedule-panel-hajime-mid1.png）：白色弹窗，
- 标题「現在1ターン目」，下一行过线条件「3位以上またはスコア1700以上で中間試験突破」；
- 一行三属性倍率（图标 + 「644%」），y ≈ 250；
- 逐行「現在のターン / 1ターン後 … 8ターン後」+ 属性图标（x ≈ 231）+ 属性文字，行距 ≈ 88，一屏 9 行（更长的考试要往下滑）；
- 底部中间圆形 ✕（360, 1160）关闭。
属性按图标颜色认（用户 11-17：图标优先），认不出再看文字。读不到的项不给 / 给 None，不猜。
"""

from __future__ import annotations

import re

import cv2
import numpy as np

CLOSE = (360, 1160)  # 底部圆形 ✕
ICON_X = (205, 258)  # 每行属性图标的 x 范围
ICON_HALF_H = 20
ICON_MIN = 250  # 图标彩色像素（实测 1,000+）
ATTR_WORDS = (("ボーカル", "Vo"), ("ダンス", "Da"), ("ビジュアル", "Vi"))
PERMIL_Y = (225, 280)  # 三属性倍率那一行
EFFECT_MAX_H = 160  # 最后一行标签下面最多往下看这么远找回合效果
EFFECT_Y_MAX = 1130  # 面板底边（✕ 上方）
PANEL_WHITE_MIN = 0.6  # 面板开着：中间区域白色占比（实拍 0.88–0.89；考试 / 课程 / 结算画面 ≤ 0.24）


def panel_open(img: np.ndarray) -> bool:
    """审查日程面板还开着（大块白色弹窗盖在考试画面上）。"""
    if img is None or img.shape[:2] != (1280, 720):
        return False
    hsv = cv2.cvtColor(img[300:1100, 40:680], cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 2] > 230) & (hsv[..., 1] < 25)).mean()) >= PANEL_WHITE_MIN


PERMIL_X = ((60, 260), (260, 460), (460, 660))  # Vo / Da / Vi 三格（图标 + 数字，从左到右固定顺序）


def _attr_by_icon(hsv: np.ndarray, y: int) -> str | None:
    from hiro.vision.action import ATTR_HUE

    reg = hsv[max(0, y - ICON_HALF_H) : y + ICON_HALF_H, ICON_X[0] : ICON_X[1]]
    vivid = (reg[..., 1] > 120) & (reg[..., 2] > 150)
    hues = reg[..., 0][vivid]
    counts = {a: int(((hues >= lo) & (hues <= hi)).sum()) for a, (lo, hi) in ATTR_HUE.items()}
    best = max(counts, key=counts.get)
    return {"vo": "Vo", "da": "Da", "vi": "Vi"}[best] if counts[best] >= ICON_MIN else None


def _row_offset(text: str) -> int | None:
    """「現在のターン」→ 0，「3ターン後」→ 3；不是行标签 None。"""
    t = text.replace(" ", "")
    if "現在のターン" in t:
        return 0
    m = re.search(r"([\dろ]{1,2})ターン後", t)  # OCR 常把「3」读成「ろ」（初 PRO 最終：「ろターン後」漏了第 3 回合）
    return int(m.group(1).replace("ろ", "3")) if m else None


PERMIL_RATIO_MAX = 5.0  # 一项和另外两项的中位数比超过这个倍数（或不到它的倒数）：OCR 多读 / 少读了一位，丢掉


def sane_permil(permil: dict) -> dict | None:
    """倍率读数合理性校验：和另外两项比差 5 倍以上的丢掉（协调 11-19 N.I.A MASTER 最終：Da 1467% 读成 31467%，图标被认成「3」）。
    只剩读得对的那几项；一项都不剩 None。不修补（不猜去掉哪一位）。"""
    if not permil:
        return None
    vals = sorted(permil.values())
    if len(vals) == 2:  # 只读到两项：互相差 5 倍以上分不出哪个错，两项都不要
        return dict(permil) if vals[0] > 0 and vals[1] / vals[0] <= PERMIL_RATIO_MAX else None
    ref = vals[len(vals) // 2]  # 三项按中位数比（错的那一项不会是中位数，除非错了两项）
    out = {a: v for a, v in permil.items() if ref > 0 and 1 / PERMIL_RATIO_MAX <= v / ref <= PERMIL_RATIO_MAX}
    return out or None


def read_schedule_panel(img: np.ndarray, texts=None) -> dict | None:
    """→ {current_turn, rows: {偏移: Vo/Da/Vi/None}, permil: {Vo, Da, Vi}（千分比，644% → 6440；过合理性校验，可能只有其中几项）, pass_rank, pass_score, pass_text（过线条件原文）, close}；
    不是这个面板 None。texts：hiro.vision.ocr.screen_texts 的结果（不给就自己读）。"""
    if img is None or img.shape[:2] != (1280, 720):
        return None
    if texts is None:
        from hiro.vision.ocr import screen_texts

        texts = screen_texts(img)
    tx = [(t.text.replace(" ", ""), int(t.rect.center.x), int(t.rect.center.y)) for t in texts]
    cur = next((int(m.group(1)) for s, _, _ in tx if (m := re.search(r"現在(\d{1,2})ターン目", s))), None)
    labels = sorted(((off, y) for s, x, y in tx if x < 200 and (off := _row_offset(s)) is not None), key=lambda r: r[1])
    if cur is None and not labels:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    rows: dict[int, str | None] = {}
    for off, y in labels:
        attr = _attr_by_icon(hsv, y)
        if attr is None:
            word = next((s for s, x, yy in tx if x > 240 and abs(yy - y) <= 20), "")
            attr = next((a for w, a in ATTR_WORDS if w in word), None)
        rows[off] = attr
    effects: dict[int, str] = {}
    for k, (off, y) in enumerate(
        labels
    ):  # 回合效果（gimmick）：行标签下面、下一行标签上面的文字（最終「1ターン後」下面的「やる気+5」）
        y_end = labels[k + 1][1] - 15 if k + 1 < len(labels) else min(y + EFFECT_MAX_H, EFFECT_Y_MAX)
        boxes = sorted(
            (yy, x, s)
            for s, x, yy in tx
            if x > 150 and y + 30 <= yy <= y_end and not any(w in s for w, _ in ATTR_WORDS)
        )
        lines: list[
            list[tuple[int, str]]
        ] = []  # 同一行（y 差 ≤ 12）的字按 x 排：「元気が18以上の場合、」「やる気+8」只差 2 像素
        last_y = None
        for yy, x, txt in boxes:
            if last_y is None or yy - last_y > 12:
                lines.append([])
            lines[-1].append((x, txt))
            last_y = yy
        if lines:
            effects[off] = "".join(t for line in lines for _, t in sorted(line))
    permil = {}
    for (x1, x2), a in zip(PERMIL_X, ("Vo", "Da", "Vi")):
        num = next((int(m.group(1)) for s, x, y in tx if x1 <= x < x2 and PERMIL_Y[0] <= y <= PERMIL_Y[1]
                    and (m := re.search(r"(\d{2,5})\s*[%％]", s))), None)  # fmt: skip
        if num is not None:
            permil[a] = num * 10
    # 过线条件一行（「3位以上で中間試験突破」「3位以上またはスコア1700以上で中間試験突破」；N.I.A「…オーディション突破」）：
    # 先认带「突破」的那行，没有再退回「以上 + 位」。原文写进 pass_text（8a3a1e7 11-18：场次按原文关键词认中間 / 最終 / 1次 / 2次）
    cond = next((s for s, _, _ in tx if "突破" in s), "") or next(
        (s for s, _, _ in tx if "以上" in s and "位" in s), ""
    )
    rank = re.search(r"(\d{1,2})位(?:以上|以内|で)", cond)
    score = re.search(r"スコア(\d[\d,]*)以上", cond)
    return {
        "current_turn": cur,
        "rows": rows,
        "effects": effects,
        "permil": sane_permil(permil),
        "pass_rank": int(rank.group(1)) if rank else None,
        "pass_score": int(score.group(1).replace(",", "")) if score else None,
        "pass_text": cond or None,
        "close": list(CLOSE),
    }


def turn_effects(panels: list[dict], total_turns: int | None) -> list[str | None] | None:
    """几屏面板的回合效果 → 全场每回合一项（下标 0 = 第 1 回合，没有效果 / 没看到的回合 None）；一条都没有给 None。"""
    cur = next((p["current_turn"] for p in panels if p.get("current_turn")), None)
    if cur is None:
        return None
    by_turn = {cur + off: e for p in panels for off, e in (p.get("effects") or {}).items() if e}
    if not by_turn:
        return None
    n = total_turns if total_turns else max(by_turn)
    return [by_turn.get(t) for t in range(1, n + 1)]


def turn_types(panels: list[dict], total_turns: int | None) -> list[str | None] | None:
    """一屏或几屏面板读数 → 全场每回合属性（下标 0 = 第 1 回合，长度 = 总回合数）。读不到的回合是 None；一个都没有给 None。
    总回合数不知道时按读到的最后一行算长度。"""
    cur = next((p["current_turn"] for p in panels if p.get("current_turn")), None)
    if cur is None:
        return None
    by_turn: dict[int, str | None] = {}
    for p in panels:
        for off, a in (p.get("rows") or {}).items():
            if a is not None or (cur + off) not in by_turn:
                by_turn[cur + off] = a
    if not any(by_turn.values()):
        return None
    n = total_turns if total_turns else max(by_turn)
    return [by_turn.get(t) for t in range(1, n + 1)]
