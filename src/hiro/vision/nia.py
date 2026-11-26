"""N.I.A 特有页面的识别（整帧 OCR 文字 + 少量像素）：営業场所、営業话题、试镜选档。字段按 3940a9 的决策接口（hiro.brain.deciders
sales_place / sales_event / audition_select）。实机样本：tests/fixtures/pages/nia-*（协调 11-17 N.I.A 采样）。

- NIA_SALES_PLACE：「営業先を選択してください」，三张场所卡（自治体イベント会場 黄 / 企業イベント会場 青绿 / 商業施設 紫 / リゾート 绿），
  每张 {index, kind, name, gain, hp_cost, center}；点一张会展开（大成功ボーナス、獲得報酬、「営業開始」），expanded / selected / start。
- NIA_SALES_EVENT：左上「営業」标签 + 2–3 个话题按钮（左侧图标颜色 = 属性：粉 Vo / 蓝 Da / 黄 Vi）+ 下方对话框。choices {index, attr, text, center}。
- NIA_AUDITION_SELECT：「オーディションを選択してください」，几条试镜横幅，下面写门槛（「9,000以上」「条件なし」）。
  tiers {index（屏幕从上到下）, number（按门槛从低到高 1..n）, vote_need, locked, center}；votes：右上的当前投票数。
  点一条会展开（评价、推奨投票数ランク、横幅上「オーディション開始」）：expanded / chosen_need / start。
"""

from __future__ import annotations

import re
from functools import lru_cache

import cv2
import numpy as np

PLACE_KINDS = (
    ("自治体", "local"),
    ("企業", "corporate"),
    ("商業", "commercial"),
    ("ゾート", "resort"),
)  # 「リゾート施設」展开时 OCR 读成「ノゾート施談」
ICON_ATTR_HUE = {"Vo": (155, 180), "Da": (95, 120), "Vi": (12, 35)}  # 话题按钮左侧圆形图标：粉 / 蓝 / 黄
VOTES_BOX = (540, 120, 700, 170)  # 右上投票数（「4,680」，上面一行「9,000」是下一档目标）


def _t(texts):
    """[(text, (x1, y1, x2, y2))] 或 screen_texts 的 Text → [(去空格文字, cx, cy)]。"""
    out = []
    for t in texts:
        if hasattr(t, "rect"):
            out.append((t.text.replace(" ", ""), t.rect.center.x, t.rect.center.y))
        else:
            s, b = t[0], t[-1]
            out.append((s.replace(" ", ""), (b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
    return out


def _num(s: str) -> int | None:
    m = re.search(r"\d[\d,]*", s)
    return int(m.group(0).replace(",", "")) if m else None


# ---------------------------------------------------------------- 営業场所：图形为主（用户 11-17：名字读不出不要紧，认准图标 / 颜色和收益）
CARD_X = 60  # 卡片左边缘那一列：按颜色切出每张卡
CARD_Y = (380, 1180)
CARD_MIN_H = 25
CARD_HUE = (
    ("local", 18, 32),
    ("corporate", 78, 96),
    ("commercial", 120, 150),
    ("resort", 45, 75),
)  # 黄 / 青绿 / 紫 / 绿
EXPANDED_MIN_H = 300  # 展开的那张特别长（实机 411 / 480 px）；收起的最下面一张到画面底也有 233
PILL_X = (480, 640)  # 「消費」药丸：卡片同色、亮度压到 95–175
HEART_X = (425, 465)  # 药丸里的爱心图标（高亮）：有爱心 = 耗体力（实机 540+），「消費なし」没有（≤ 20）
HEART_MIN = 200
START_X = (380, 660)  # 「営業開始」橙色按钮
REWARD_DX, REWARD_X0, REWARD_DY = (
    96,
    112,
    -28,
)  # 獲得報酬小图标：第一格中心 x 112、间距 96，在「営業開始」上方约 28 像素
# 有模板的：card（卡片带星，リゾート展开实机）/ card_plus（卡片右上带「+」，商業展开实机）/ stamina（心）。
# 两种卡图标的含义（技能卡获得 / 强化…）交给决策层按攻略对；P 点、饮料还没有样本，认不出记 unknown
REWARD_KINDS = ("card", "card_plus", "stamina")


def _card_kind(px) -> str | None:
    h, s, v = int(px[0]), int(px[1]), int(px[2])
    if s < 90 or v < 150:
        return None
    return next((k for k, lo, hi in CARD_HUE if lo <= h <= hi), None)


def _card_segments(hsv) -> list[tuple[str, int, int]]:
    y0, y1 = CARD_Y
    col = [_card_kind(hsv[y, CARD_X]) for y in range(y0, y1)]
    segs, cur, start = [], None, 0
    for i, k in enumerate([*col, None]):
        if k != cur:
            if cur is not None and i - start >= CARD_MIN_H:
                segs.append((cur, y0 + start, y0 + i))
            cur, start = k, i
    return segs


def _pill_rows(hsv, a: int, b: int) -> tuple[int, int] | None:
    band = hsv[:, PILL_X[0] : PILL_X[1]]
    # 「消費なし」的白字会把药丸中间几行切断：取窗口里所有符合的行，首尾当药丸上下沿（只看卡片头部 160 行）
    rows = [
        y
        for y in range(a, min(b, a + 160))
        if ((band[y, :, 2] >= 95) & (band[y, :, 2] <= 175) & (band[y, :, 1] >= 100)).mean() > 0.7
    ]
    groups: list[list[int]] = []
    for y in rows:  # 允许中间断几行（白字），卡片上沿的零星几行、下面「大成功ボーナス」压暗块分开算
        if groups and y - groups[-1][-1] <= 8:
            groups[-1].append(y)
        else:
            groups.append([y])
    pill = next((g for g in groups if 25 <= g[-1] - g[0] <= 60), None)
    return (pill[0], pill[-1]) if pill else None


def _has_heart(hsv, y1: int, y2: int) -> bool:
    reg = hsv[y1 + 4 : y2 - 3, HEART_X[0] : HEART_X[1]]
    return int(((reg[..., 2] >= 200) & (reg[..., 1] >= 150)).sum()) >= HEART_MIN


def _cost_icon(hsv, y1: int, y2: int) -> str | None:
    """「消費」药丸左边的图标 → stamina（爱心，黄绿渐变，色相 40–90 占多数；实机 540+ 像素）/ produce_point（P 点：黄色圆底，
    几乎没有绿色）/ None（「消費なし」没有图标，或认不出是哪种）。客户端 ScheduleBusinessFolderView.CostType 只有这两种（逆向 10.2）。
    P 点消耗的场所还没有实机帧，黄色判据按 P 图标的颜色定，没核过。"""
    reg = hsv[y1 + 4 : y2 - 3, HEART_X[0] : HEART_X[1]]
    m = (reg[..., 2] >= 200) & (reg[..., 1] >= 150)
    n = int(m.sum())
    if n < HEART_MIN:
        return None
    h = reg[..., 0][m]
    green, yellow = int(((h >= 40) & (h <= 90)).sum()), int(((h >= 15) & (h < 40)).sum())
    if green >= 0.4 * n:
        return "stamina"
    if yellow >= 0.6 * n and green < 0.2 * n:
        return "produce_point"
    return None


def _start_button(hsv, a: int, b: int) -> tuple[int, int] | None:
    reg = hsv[a:b, START_X[0] : START_X[1]]
    m = (reg[..., 0] >= 5) & (reg[..., 0] <= 18) & (reg[..., 1] >= 180) & (reg[..., 2] >= 200)
    if m.sum() < 1500:
        return None
    ys, xs = np.where(m)
    return int(xs.mean()) + START_X[0], int(ys.mean()) + a


@lru_cache(maxsize=1)
def _reward_templates() -> dict:
    from pathlib import Path

    d = Path(__file__).with_name("templates")
    return {k: cv2.imread(str(d / f"nia_reward_{k}.png"), cv2.IMREAD_GRAYSCALE) > 127 for k in REWARD_KINDS}


def _glyph(img, cx: int, cy: int, r: int = 22):
    hsv = cv2.cvtColor(img[cy - r : cy + r, cx - r : cx + r], cv2.COLOR_BGR2HSV)
    return (hsv[..., 1] < 60) & (hsv[..., 2] > 220)


def read_rewards(img, start: tuple[int, int]) -> list[str]:
    """展开卡「獲得報酬」那一行的小图标（白色图形）→ ["card", "stamina", "unknown", …]，按模板 IoU 认。"""
    out = []
    cy = start[1] + REWARD_DY
    for k in range(4):
        cx = REWARD_X0 + REWARD_DX * k
        if _glyph(img, cx, cy).sum() < 150:
            break  # 这一格没有图标
        best = (0.0, None)
        for dy in range(-8, 9, 2):  # 位置随卡片高度略有偏差：附近找最像的
            for dx in range(-8, 9, 2):
                g = _glyph(img, cx + dx, cy + dy)
                for name, t in _reward_templates().items():
                    sc = float((g & t).sum() / max(1, (g | t).sum()))
                    if (
                        sc > best[0]
                    ):  # 只比分数：打平（例如都是 0）时元组会去比名字和 None 抛 TypeError（同 _icon_attr，N.I.A 第二局 11-18 停机）
                        best = (sc, name)
        out.append(best[1] if best[0] >= 0.5 else "unknown")
    return out


def read_sales_place(texts, img=None) -> dict | None:
    """営業场所页。以卡片颜色定种类（黄 自治体 / 青绿 企業 / 紫 商業 / 绿 リゾート）、药丸里有没有爱心定耗不耗体力、
    「営業開始」橙色按钮定展开的是哪张、獲得報酬小图标定收益；名字、金额、体力数值读得到就补上（只用来记日志 / 兜底）。
    没有截图时退回纯文字读法。"""
    tx = _t(texts)
    prompt = any("営業先" in s for s, _, _ in tx) or any(s == "営業" and x < 180 and y < 140 for s, x, y in tx)
    if img is None:
        return _read_sales_place_text(tx)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    segs = _card_segments(hsv)
    if len(segs) < 2:
        return _read_sales_place_text(tx) if prompt else None
    starts = [(i, st) for i, (_, a, b) in enumerate(segs) if (st := _start_button(hsv, a, b)) is not None]
    if not (prompt or starts):
        return None
    places = []
    for i, (kind, a, b) in enumerate(segs):
        pill = _pill_rows(hsv, a, b)
        cost = cost_type = cost_num = p_cost = None
        if pill is not None:
            heart = _has_heart(hsv, *pill)
            num = next(
                (
                    _num(s)
                    for s, x, y in tx
                    if x > 480 and pill[0] - 10 <= y <= pill[1] + 10 and re.fullmatch(r"-?\d{1,3}", s)
                ),
                None,
            )
            cost_type = _cost_icon(hsv, *pill) if heart else None
            if not heart:  # 「消費なし」
                cost = p_cost = cost_num = 0
            elif cost_type == "stamina":
                cost, p_cost, cost_num = (num or 5), 0, (num or 5)  # 有爱心但数字没读到：N.I.A 営業实测都是 -5
            elif cost_type == "produce_point":
                cost, p_cost, cost_num = 0, num, num
            else:
                cost_num = num  # 有图标但认不出是体力还是 P 点：只给数值，hp_cost / p_cost 留空
        name = next(
            (s for s, x, y in tx if x < 360 and a <= y <= a + 60 and ("会場" in s or "施" in s or "ゾート" in s)), None
        )
        gain = next((_num(s) for s, x, y in tx if s.startswith("+") and x < 360 and a + 30 <= y <= a + 110), None)
        places.append({"index": i, "kind": kind, "name": name, "gain": gain, "hp_cost": cost, "p_cost": p_cost, "cost_type": cost_type,
                       "cost": cost_num, "center": [360, a + 70]})  # fmt: skip
    out = {"places": places, "expanded": bool(starts)}
    if starts:
        i, st = starts[0]
        out.update(selected=i, start=list(st), rewards=read_rewards(img, st))
        places[i]["rewards"] = out["rewards"]
        places[i].update(_great_success(tx, segs[i][1], segs[i][2]))
    return out


def _great_success(tx, a: int, b: int) -> dict:
    """展开的那张卡上的「大成功ボーナス ☆+840　確率 90%」→ {great_success_pct, great_success_votes}（客户端 BusinessGreatSuccessInfoSheet 的
    GreatSuccessPermil / VoteValue，逆向 10.2）。只有展开的卡看得到；读不到的不给。"""
    label = next(((x, y) for s, x, y in tx if "大成功" in s and a <= y <= b), None)
    if label is None:
        return {}
    out = {}
    pct = next(
        (
            int(m.group(1))
            for s, x, y in tx
            if 200 <= x <= 380 and label[1] - 10 <= y <= label[1] + 80 and (m := re.search(r"(\d{1,3})\s*[%％]", s))
        ),
        None,
    )
    if pct is not None and 0 <= pct <= 100:
        out["great_success_pct"] = pct
    votes = next(
        (
            _num(s)
            for s, x, y in tx
            if x < 250 and label[1] + 20 <= y <= label[1] + 80 and re.fullmatch(r"\+[\d,]+", s.replace(" ", ""))
        ),
        None,
    )
    if votes is not None:
        out["great_success_votes"] = votes
    return out


def _read_sales_place_text(tx) -> dict | None:
    """没有截图（或者颜色切不出卡）时的文字读法（原来的实现）。"""
    titles = []
    for s, x, y in tx:
        kind = next((k for w, k in PLACE_KINDS if w in s), None)
        if kind and x < 360 and ("会場" in s or "施設" in s or "施談" in s or "ゾート" in s):
            titles.append((y, kind, s))
    has_prompt = any("営業先を選択" in s for s, _, _ in tx)
    start = next(((x, y) for s, x, y in tx if "営業開始" in s), None)
    if len(titles) < 2 or not (has_prompt or start):
        return None
    titles.sort()
    places = []
    for i, (y, kind, name) in enumerate(titles):
        gain = next((_num(s) for s, x, yy in tx if s.startswith("+") and 20 <= yy - y <= 80 and x < 360), None)
        cost_txt = next(
            (
                s
                for s, x, yy in tx
                if x > 420 and 20 <= yy - y <= 90 and ("消費なし" in s or re.fullmatch(r"-?\d{1,2}", s))
            ),
            None,
        )
        hp = 0 if cost_txt and "なし" in cost_txt else (_num(cost_txt) if cost_txt else None)
        places.append({"index": i, "kind": kind, "name": name, "gain": gain, "hp_cost": hp, "center": [360, y + 40]})
    out = {"places": places, "expanded": start is not None}
    if start is not None:
        bonus_y = next((y for s, _, y in tx if "大成功" in s), start[1])
        sel = [p for p in places if p["center"][1] - 40 < bonus_y]
        out["selected"] = sel[-1]["index"] if sel else None
        out["start"] = list(start)
    return out


TOPIC_ICON = (40, 96, 24)  # 话题按钮左侧圆形图标：x 范围、上下半高（实机按钮中心 y 699 / 800 / 901）


@lru_cache(maxsize=1)
def _topic_templates() -> dict:
    from pathlib import Path

    d = Path(__file__).with_name("templates")
    return {a: cv2.imread(str(d / f"nia_topic_{a}.png"), cv2.IMREAD_GRAYSCALE) > 127 for a in ("Vo", "Da", "Vi")}


def _icon_mask(img, y: int, dx: int = 0, dy: int = 0):
    x1, x2, r = TOPIC_ICON
    hsv = cv2.cvtColor(img[max(0, y + dy - r) : y + dy + r, x1 + dx : x2 + dx], cv2.COLOR_BGR2HSV)
    return (hsv[..., 1] >= 110) & (hsv[..., 2] >= 150)


def _icon_attr(img, y: int) -> str | None:
    """话题按钮左侧图标 → 属性：先比图形（话筒 = Vo、鞋 = Da、人像 = Vi，实机切的形状模板，附近 ±6 像素找最像的），
    再按颜色（粉 / 蓝 / 橙黄）。用户 11-17：图标优先，文字只兜底。"""
    if img is None:
        return None
    best = (0.0, None)
    for dy in range(-6, 7, 2):
        for dx in range(-6, 7, 2):
            m = _icon_mask(img, y, dx, dy)
            for a, t in _topic_templates().items():
                if m.shape == t.shape:
                    s = float((m & t).sum() / max(1, (m | t).sum()))
                    if (
                        s > best[0]
                    ):  # 只比分数：分数打平（例如都是 0）时元组会去比属性名和 None，抛 TypeError（N.I.A 第二局 11-18 停机）
                        best = (s, a)
    if best[0] >= 0.45:
        return best[1]
    x1, x2, r = TOPIC_ICON
    hsv = cv2.cvtColor(img[max(0, y - r) : y + r, x1:x2], cv2.COLOR_BGR2HSV)
    sat = (hsv[..., 1] >= 120) & (hsv[..., 2] >= 150)
    if sat.sum() < 60:
        return None
    hue = hsv[..., 0][sat]
    cnt = {a: int(((hue >= lo) & (hue <= hi)).sum()) for a, (lo, hi) in ICON_ATTR_HUE.items()}
    a = max(cnt, key=cnt.get)
    return a if cnt[a] >= 40 else None


SALES_TEXT_X = (112, 660)  # 话题按钮里文字的横向范围：左边 x≈75–105 是属性图标


def _option_text(img, y: int, text: str) -> str:
    """话题按钮的文字：整屏 OCR 会把左边的属性图标读成一个字粘在前面（Vision：「日学生時代の話」「& 学生時代の話」
    「gアドバイスをもらう」，A16 11-26）。只裁图标右边的文字区再读一次，读到了就用它；读不到保留原来的。"""
    if img is None:
        return text
    from hiro.vision.ocr import ocr

    crop = img[max(0, y - 24) : y + 24, SALES_TEXT_X[0] : SALES_TEXT_X[1]]
    again = "".join(t for t, _ in ocr(crop, scale=2)).replace(" ", "")
    return again if len(again) >= 3 else text


def read_sales_event(texts, img=None) -> dict | None:
    tx = _t(texts)
    if not any(s == "営業" and x < 180 and y < 140 for s, x, y in tx):
        return None
    opts = sorted(
        (y, s, x)
        for s, x, y in tx
        if 600 <= y <= 960 and 90 <= x <= 470 and len(s) >= 3 and not re.fullmatch(r"[\d/,.%+-]+", s)
    )
    if len(opts) < 2:
        return None
    choices = [
        {"index": i, "text": _option_text(img, y, s), "attr": _icon_attr(img, y), "center": [360, y]}
        for i, (y, s, x) in enumerate(opts)
    ]
    for c in choices:
        if c["attr"] is None:  # 图标认不出时按文字（「ボーカル / ダンス / ビジュアル」）
            c["attr"] = next(
                (a for w, a in (("ボーカル", "Vo"), ("ダンス", "Da"), ("ビジュアル", "Vi")) if w in c["text"]), None
            )
    return {"choices": choices}


def read_votes(img) -> int | None:
    if img is None:
        return None
    from hiro.vision.ocr import screen_texts

    x1, y1, x2, y2 = VOTES_BOX
    nums = [
        _num(t.text)
        for t in screen_texts(img, (x1, y1, x2 - x1, y2 - y1))
        if re.fullmatch(r"[\d,]+", t.text.replace(" ", ""))
    ]
    return next((n for n in nums if n is not None), None)


STAGE_WORDS = (("最終", "final"), ("2次", "mid2"), ("1次", "mid1"))


def _audition_stage(tx) -> str | None:
    """左上「1次 / 2次 / 最終」+「オーディション」（实机 OCR「1次」在 (68,97)）→ mid1 / mid2 / final；页中间的大标题也认。"""
    for s, x, y in tx:
        if y < 260:
            for w, stage in STAGE_WORDS:
                if w in s and (x < 200 or "オーディ" in s or "ション" in s or y > 180):
                    return stage
    return None


def read_audition_select(texts, img=None) -> dict | None:
    tx = _t(texts)
    prompt = any("オーディションを選択" in s for s, _, _ in tx)
    # Vision 会把前半读歪（「オテデイション開始」「オーディシヨン開始」，A16 11-26 nia-audition-select-*-chosen）：认后半，小ョ也可能读成ヨ
    start = next(((x, y) for s, x, y in tx if re.search(r"シ[ョヨ]ン開始", s)), None)
    if not (prompt or start):
        return None
    labels = []
    for s, x, y in tx:
        if 250 <= x <= 470 and y >= 700:
            if "条件なし" in s:
                labels.append((y, 0))
            elif "以上" in s and (n := _num(s)) is not None:
                labels.append((y, n))
    if not labels:
        return None
    labels.sort()
    order = sorted({n for _, n in labels})
    tiers = [{"index": i, "number": order.index(n) + 1, "vote_need": n, "locked": False, "center": [360, y - 50]}
             for i, (y, n) in enumerate(labels)]  # fmt: skip
    out = {"tiers": tiers, "votes": read_votes(img), "expanded": start is not None, "stage": _audition_stage(tx)}
    if start is not None:
        below = [(y, n) for y, n in labels if 0 < y - start[1] < 120]
        out["chosen_need"] = below[0][1] if below else None
        out["start"] = list(start)
    return out


BONUS_TAP = (360, 1222)  # 「TAP」


def read_audition_bonus(texts) -> dict | None:
    """试镜结束后的「オーディションボーナス」（第 2 页再加「パラメータボーナス」、属性 +N、獲得スコア、底部「TAP」）：展示页，点 TAP 过。
    带金色标题条，原来判成 DIALOG、第 1 页判 unknown，16 秒后停（N.I.A 采样 11-17）。"""
    tx = _t(texts)
    if not any("オーディションボーナス" in s and y < 320 for s, _, y in tx):
        return None
    tap = next(((x, y) for s, x, y in tx if s == "TAP" and y > 1150), BONUS_TAP)
    return {"tap": list(tap), "param": any("パラメータボーナス" in s for s, _, _ in tx)}


def read_audition_result(texts) -> dict | None:
    """试镜结果（「最終オーディション」/「1次オーディション」大标题 + 名次 + 偶像名 + 底部「TAPで次へ」）：展示页，点 TAP 过。
    最终试镜第 1 名那一页原来判 unknown，16 秒后停（N.I.A 采样 11-17）。"""
    tx = _t(texts)
    title = next((s for s, _, y in tx if "オーディション" in s and "ボーナス" not in s and y < 420), None)
    tap = next(((x, y) for s, x, y in tx if s.startswith("TAP") and y > 1050), None)
    if title is None or tap is None:
        return None
    stage = next((st for w, st in STAGE_WORDS if w in title), None)
    return {"tap": list(tap), "stage": stage}


def read_briefing(texts, img=None) -> dict | None:
    """试镜考前说明页（審査基準 + 合格条件「1位」+ 三属性 >> 倍率 +「タップして次へ」）：版面和 H.I.F 的不一样（没有「突破ライン」，
    「合格条件」位置不同），H.I.F 的两条判据都没命中，判 unknown 停了（N.I.A 采样 11-17 最终试镜）。读数用 H.I.F 的
    read_exam_briefing（三属性和倍率），按快速判页的格式带上要点的位置，交主循环的 advance 点（同 H.I.F）。"""
    tx = _t(texts)
    if not (any("審査基準" in s for s, _, _ in tx) and any("合格条件" in s for s, _, _ in tx)):
        return None
    # 「タップして次へ」压在亮色衣装上时 OCR 只读出半截（初 PRO ことね 11-19：「プして次へ」），读到「次へ」就算
    tap = next(
        ((x, y) for s, x, y in tx if ("タップして次へ" in s or "して次へ" in s or s.endswith("次へ")) and y > 1100),
        None,
    )
    if tap is None or img is None:
        return None  # 倍率还在跳（ボーナス演出）：没有「タップして次へ」先不认
    from hiro.vision.pages import read_exam_briefing

    boxes = [(t.text, tuple(int(v) for v in t.rect)) if hasattr(t, "rect") else (t[0], tuple(t[-1])) for t in texts]
    brief = read_exam_briefing(img, boxes)
    return {**brief, "_quick": {"page": "HIF_EXAM_BRIEFING", "tap": tap, "dispatch": None, "fields": brief}}


def read_nia_page(texts, img=None) -> tuple[str, dict] | None:
    """N.I.A 特有页面 → (页面名, 字段)；都不是 None。考前说明页借用 H.I.F 的页面名（HIF_EXAM_BRIEFING，同一种页）。"""
    if (bonus := read_audition_bonus(texts)) is not None:
        return "NIA_AUDITION_BONUS", bonus
    if (brief := read_briefing(texts, img)) is not None:
        return "HIF_EXAM_BRIEFING", brief
    if (res := read_audition_result(texts)) is not None:
        return "NIA_AUDITION_RESULT", res
    for name, fn in (("NIA_AUDITION_SELECT", lambda: read_audition_select(texts, img)), ("NIA_SALES_PLACE", lambda: read_sales_place(texts, img)),
                     ("NIA_SALES_EVENT", lambda: read_sales_event(texts, img))):  # fmt: skip
        if (f := fn()) is not None:
            return name, f
    return None


__all__ = ["read_audition_select", "read_nia_page", "read_sales_event", "read_sales_place", "read_votes"]
