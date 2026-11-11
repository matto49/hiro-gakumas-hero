# 来源：digital-hiro src/hiro/vision/hif.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""H.I.F 考试画面：场景判定与 HUD/手牌读取。

KAA 的考试判定依赖普通考试的排名文字模板，H.I.F 考试布局不同，会被判为 unknown，所以这里单独实现。
数字用 KAA 自带的 ddddocr（原图放大 2 倍，单字也能读）；文字（本回合属性、倍率、突破线）用 Apple Vision。
"""

import re
from functools import cache, lru_cache
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike
from kaa.db.skill_card import SkillCard
from kaa.tasks.produce.new.play_cards import ui as kaa_ui
from kaa.tasks.produce.new.play_cards.ui import _locate_letters
from kotonebot.primitives import Rect

from hiro.vision import layout
from hiro.vision.card_art import match_card_art
from hiro.vision.cards import card_summary, card_version
from hiro.vision.digits import _iou, _scale, _templates, read_dddd, read_int, read_number, read_score
from hiro.vision.items import read_drink_slots, read_pitems
from hiro.vision.ocr import engine as ocr_engine
from hiro.vision.ocr import ocr, ocr_batch
from hiro.vision.region_cache import RegionCache, cached
from hiro.vision.status import read_status_column, status_summary

TEMPLATES = Path(__file__).with_name("templates")
ATTRS = {"ボーカル": "vocal", "ダンス": "dance", "ビジュアル": "visual"}

# 720x1280 逻辑坐标 (x1, y1, x2, y2)。HUD 数值取自客户端布局 + 截图校准（hiro.vision.layout），其余仍为实测
BOX_TURNS = layout.box(layout.EXAM, "ExamAuditionTurnView/txt/TurnValue")
BOX_ATTR = (105, 55, 230, 120)
BOX_MY_SCORE = (222, 128, 262, 166)
_GENKI_LAYOUT = layout.box(layout.EXAM, "GaugeRoot/GenkiGauge/IconRoot/Icon/GenkiValue")
# 布局框按三位数宽：四位数元気（≥1000）首位「1」在框左外、末位在框右外 → 1388 读成 388、1024 读成 102（第二十～二十五局 80 处，数据确认 d412748）。
# 左放宽 20、右放宽 10：全部 4398 帧考试画面新旧对比，补回首位 / 末位的约 200 处，其余 22 处都是三位数首位被切半（164→464 等），人工核对新读法对；
# 只有 1 帧蓝色动画数字（main21 #235「927」）由读不出变成读成 2。
BOX_GENKI = (_GENKI_LAYOUT[0] - 20, _GENKI_LAYOUT[1], min(720, _GENKI_LAYOUT[2] + 10), _GENKI_LAYOUT[3])
BOX_HP = layout.box(layout.EXAM, "GaugeRoot/Text/StaminaValue")
# P 道具图标右上角的剩余次数（不是山札张数：2026-10-31 复盘核实图标数字 4、4、3、2 随 えいえいおー 道具触发递减）。
# H.I.F 考试画面上没有山札张数，契约 deck_count 恒为 None
BOX_PITEM_CHARGES = (683, 228, 715, 256)
TOP_STRIP = (0, 0, 720, 175)


@cache
def _tpl(name: str) -> MatLike:
    return cv2.imread(str(TEMPLATES / f"{name}.png"))


def _match(img: MatLike, name: str, box) -> tuple[float, tuple[int, int]]:
    x1, y1, x2, y2 = box
    res = cv2.matchTemplate(img[y1:y2, x1:x2], _tpl(name), cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), (loc[0] + x1, loc[1] + y1)


def _white(img: MatLike) -> MatLike:
    return cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), (0, 0, 200), (180, 60, 255))


def _match_white(img: MatLike, name: str, box) -> tuple[float, tuple[int, int]]:
    """只比对白色字形。「突破ライン」的底色会随分数是否过线从纯色变成粉蓝渐变，带底色匹配会掉到 0.64。"""
    x1, y1, x2, y2 = box
    res = cv2.matchTemplate(_white(img[y1:y2, x1:x2]), _white(_tpl(name)), cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), (loc[0] + x1, loc[1] + y1)


def is_hif_exam(img: MatLike, threshold: float = 0.6) -> bool:
    # 考试帧经 JPG 压缩后低至 0.72，其余培育画面最高 0.21
    score, _ = _match(img, "hif_exam_remaining_turns", (0, 0, 160, 45))
    if score < threshold:
        # 本战 R2 开局画面压暗，「残りターン」只有 0.57：顶部有本战名次条时放宽到 0.45
        return score >= FINALS_TURNS_MIN and is_finals_strip(img)
    # 加分特效会压暗文字（实测低至 0.64）；非考试画面过不了上面的「残りターン」，这里只做确认
    score, _ = _match_white(img, "hif_exam_breakthrough", TOP_STRIP)
    return score >= 0.6 or is_finals_strip(img)  # 本战没有突破线，顶部是 1st / 2nd / 3rd 名次条（识别-05）


def is_hif_result(img: MatLike, threshold: float = 0.6) -> bool:
    """H.I.F 考试结算页（「H.I.F選抜試験 第N回結果」+ 突破/敗退名单）。KAA 会误判为剧情 commu。"""
    if _match_white(img, "hif_result_title", (0, 90, 360, 230))[0] < threshold:
        return False
    return _match_white(img, "hif_result_kekka", (0, 130, 360, 230))[0] >= threshold


RESULT_LIST = (0, 250, 720, 1080)  # 结算名单区（标题以下、「次へ」按钮以上）
RESULT_ME_EDGE = (20, 32)  # 玩家行左边白色描边所在的 x 范围；其他行此处无白色（实测 0.17 vs 0.0）
RESULT_ME_MIN = 0.08
RESULT_TOP_ROW_Y = 332  # 名单未滚动时第一行「突破 / 敗退」字样的中心 y


def _result_score(img: MatLike, listed: str, box: tuple[int, int, int, int]) -> tuple[int | None, list[str | None]]:
    """结算页一行分数：名单整块 OCR、这一格放大 3 倍再 OCR、ddddocr 三次读数取多数（至少两次一致），都不一致给 None。
    单次 Apple Vision 会静默读错一位（8,673→8073、2,438→2458、5,962→5902，数据确认 2026-10-31）；
    结算页数字带千位逗号、字体与考试画面不同，游戏字体比对用不上。"""
    from hiro.vision.digits import read_dddd

    x1, y1, x2, y2 = box
    crop = img[max(0, y1 - 6) : y2 + 6, max(0, x1 - 6) : min(img.shape[1], x2 + 30)]
    big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    again = re.sub(r"\D", "", "".join(t for t, _ in ocr(big)).split("P")[0])
    dddd = read_dddd(img, (max(0, x1 - 4), max(0, y1 - 4), x2 + 4, y2 + 4))
    votes = [listed or None, again or None, str(dddd) if dddd is not None else None]
    best, n = vote_truncated([int(v) if v else None for v in votes])  # ddddocr 掉末位时不跟着错（main9 #198）
    return (best if best is not None and n >= 2 else None), votes


RESULT_SCORE_DY = 22  # 分数行中心比该行「突破 / 敗退」字样中心低约 22 像素
RESULT_RETRY = (10, 1050, 250, 1180)  # 左下「本日あと1回無料 / あと2回」角标与「再挑戦」按钮


def read_hif_result(img: MatLike) -> dict:
    """H.I.F 考试结算页：第 N 回、名单各行（突破 / 敗退、名字、分数）、突破线（名次 / 人数）、再挑戦，玩家行由白色描边定位。

    名单是玩家与同场 NPC（与考试顶部排名栏同一批人）按分数排序，中间插着突破线横幅。
    行按分数框（「48,603Pt」）定位，「突破 / 敗退」字样可以缺：名单入场动画中玩家行的字样要晚一两秒才出现（识别-02）。
    这时玩家的 passed 由该行在突破线横幅之上 / 之下推出（passed_derived=True）；横幅也不在画面里时为 None。
    rank 是玩家在名单里的位置（从 1 起），只在名单未滚动（第一行在固定位置）时给出。
    retry：有「再挑戦」按钮时 {left: 剩余次数 | None, free: 本日免费与否 | None}；没有按钮为 None。
    """
    from hiro.vision.ocr import ocr_boxes

    title = "".join(t for t, _ in ocr(img[150:200, 40:240]))
    m = re.search(r"第\s*(\d+)\s*回", title)
    x0, y0, x1, y1 = RESULT_LIST
    boxes = [(t.replace(" ", ""), (bx1 + x0, by1 + y0, bx2 + x0, by2 + y0)) for t, _, (bx1, by1, bx2, by2) in ocr_boxes(img[y0:y1, x0:x1], scale=2)]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 225), (180, 40, 255)) > 0
    entries, line = [], {"rank_line": None, "participants": None, "y": None}
    # 分数行「48,603Pt」：Vision 有时把「Pt」拆成单独的框，所以只认「像分数的数字串」，带不带 Pt 都行
    scores = [(t, b) for t, b in boxes if b[0] >= 200 and re.fullmatch(r"\d[\d,.]*\s*(?:[pP][tT])?", t.strip()) and re.search(r"\d{2}", t)]
    for pts, (bx1, by1, bx2, by2) in scores:
        cy = (by1 + by2) // 2 - RESULT_SCORE_DY  # 行中心（= 字样中心）
        label = next((t for t, b in boxes if t in ("突破", "敗退") and b[0] < 160 and abs((b[1] + b[3]) // 2 - cy) <= 30), None)
        name = next((t for t, b in boxes if b[0] >= 200 and cy - 50 <= (b[1] + b[3]) // 2 < cy - 5 and not re.search(r"\d", t)), None)
        score, votes = _result_score(img, re.sub(r"\D", "", pts), (bx1, by1, bx2, by2))
        edge = white[max(0, cy - 50) : cy + 50, RESULT_ME_EDGE[0] : RESULT_ME_EDGE[1]]
        entries.append({"y": cy, "passed": None if label is None else label == "突破", "name": name,
                        "score": score, "score_votes": votes, "me": bool(edge.mean() >= RESULT_ME_MIN)})  # fmt: skip
    # 分数读不出（被特效盖住）的行：有「突破 / 敗退」字样就照样成行，score 为 None
    for text, (bx1, by1, bx2, by2) in boxes:
        cy = (by1 + by2) // 2
        if text in ("突破", "敗退") and bx1 < 160 and all(abs(e["y"] - cy) > 30 for e in entries):
            name = next((t for t, b in boxes if b[0] >= 200 and cy - 50 <= (b[1] + b[3]) // 2 < cy - 5 and not re.search(r"\d", t)), None)
            edge = white[max(0, cy - 50) : cy + 50, RESULT_ME_EDGE[0] : RESULT_ME_EDGE[1]]
            entries.append({"y": cy, "passed": text == "突破", "name": name, "score": None, "score_votes": None,
                            "me": bool(edge.mean() >= RESULT_ME_MIN)})  # fmt: skip
    for text, (bx1, by1, bx2, by2) in boxes:
        if "人" in text and bx1 < 160:
            p = re.search(r"(\d+)", text)
            line["participants"] = int(p.group(1)) if p else None
            above = [(t, b) for t, b in boxes if b[0] < 160 and b[3] <= by1 + 4 and by1 - 60 < b[1] and re.fullmatch(r"\d+", t)]
            if above:
                line["rank_line"] = int(above[-1][0])
            line["y"] = (by1 + by2) // 2
    entries.sort(key=lambda e: e["y"])
    me = next((i for i, e in enumerate(entries) if e["me"]), None)
    unscrolled = bool(entries) and abs(entries[0]["y"] - RESULT_TOP_ROW_Y) <= 20
    passed, derived = (entries[me]["passed"] if me is not None else None), False
    if me is not None and passed is None and line["y"] is not None:
        passed, derived = entries[me]["y"] < line["y"], True  # 突破线横幅之上 = 过线
    return {
        # 「第1回」的数字 RapidOCR 常漏（「第回結果」）：按突破线人数推（128 → 选拔①、64 → ②、32 → ③，与 _battle_label 同一对应）
        "exam_no": int(m.group(1)) if m else {128: 1, 64: 2, 32: 3}.get(line["participants"]),
        "my_score": entries[me]["score"] if me is not None else None,
        "score": entries[me]["score"] if me is not None else None,  # 同 my_score（主循环日志按 score 取，识别-06）
        "my_score_votes": entries[me]["score_votes"] if me is not None else None,
        "passed": passed,
        "passed_derived": derived,
        "rank": me + 1 if me is not None and unscrolled else None,
        "rank_line": line["rank_line"],
        "participants": line["participants"],
        "entries": [{k: v for k, v in e.items() if k not in ("y", "score_votes")} for e in entries],
        "retry": read_retry(img),
    }


def read_retry(img: MatLike) -> dict | None:
    """结算页左下「再挑戦」：{left, free}。「本日あと1回無料」→ left=1, free=True；「あと2回」→ left=2, free=False。
    有按钮但角标读不出时两项为 None；没有「再挑戦」按钮（过线 / 次数用完）返回 None。"""
    x1, y1, x2, y2 = RESULT_RETRY
    texts = [t.replace(" ", "") for t, _ in ocr(img[y1:y2, x1:x2])]
    if not any("再挑戦" in t for t in texts):
        return None
    badge = "".join(t for t in texts if "回" in t)
    n = re.search(r"あと(\d+)回", badge)
    return {"left": int(n.group(1)) if n else None, "free": ("無料" in badge) if badge else None}


def _digits(img: MatLike, box) -> int | None:
    return read_number(img, box)


def _attr_crop(img: MatLike) -> MatLike:
    x1, y1, x2, y2 = BOX_ATTR
    return img[y1:y2, x1:x2]


ATTR_TAILS = {"カル": "vocal", "ンス": "dance", "ュアル": "visual"}  # 首字偶尔读错（qualify6 r3 #77「ベジュアル」）


def _parse_attr(joined: str) -> dict:
    attr = next((v for k, v in ATTRS.items() if k in joined), None) or next((v for k, v in ATTR_TAILS.items() if k in joined), None)
    # RapidOCR 常把「%」读成「:」「“」或漏掉：没有「%」时取末尾那串 3–5 位数（之后还要和 ddddocr 一致才采用）
    m = re.search(r"(\d+)\s*%", joined) or re.search(r"(\d{3,5})\D{0,2}$", joined)
    return {"attr": attr, "multiplier_pct": int(m.group(1)) if m else None}


BOX_MULT = (105, 82, 205, 120)  # 「2512%」数字行（BOX_ATTR 下半）


_MULT_SEEN: dict[str, int] = {}  # 属性 → 最近一次两种读法一致的倍率（同一场考试里每个属性的倍率不变）


def _vote_multiplier(img: MatLike, ocr_pct: int | None, cache: RegionCache | None, attr: str | None = None) -> tuple[int | None, list]:
    """倍率两种读法（Apple Vision 整块 OCR、ddddocr 数字行）一致才给；不一致时，若有一个读数等于该属性上次一致读到的值就用它，否则 None。
    回合转盘上的白色箭头会转到数字左边、盖住首位：OCR 把「1」读成「7」或丢掉（1481→7481 / 485），ddddocr 有时丢掉首位（1905→905）。"""
    x1, y1, x2, y2 = BOX_MULT
    dddd = cached(cache, "num:mult", img[y1:y2, x1:x2], lambda: read_dddd(img, BOX_MULT))
    votes = [ocr_pct, dddd]
    if ocr_pct is not None and ocr_pct == dddd:
        if attr:
            _MULT_SEEN[attr] = ocr_pct
        return ocr_pct, votes
    seen = _MULT_SEEN.get(attr) if attr else None
    return (seen if seen is not None and seen in votes else None), votes


def _round_attr(img: MatLike) -> dict:
    return _parse_attr("".join(t for t, _ in ocr(_attr_crop(img))))


def _breakthrough_crop(img: MatLike) -> MatLike:
    _, (x, y) = _match_white(img, "hif_exam_breakthrough", TOP_STRIP)
    return img[max(0, y - 58) : y + 2, max(0, x - 6) : x + 80]


def _parse_breakthrough(texts: str) -> dict:
    m = re.search(r"(\d+)\s*/\s*(\d+)", texts)
    if m:
        return {"rank_line": int(m.group(1)), "participants": int(m.group(2))}
    # RapidOCR 读不出斜杠：「64」「128人」拼成「64128人」（DMM qualifydmm1-00257 / -00750、qualifydmm2-00155 / -00288）。
    # 选拔考的突破线是参赛人数的一半：只在「后半 = 前半 × 2」的切法唯一时取，否则仍给 None
    if d := re.search(r"(\d{3,})\s*人", texts):
        digits = d.group(1)
        cuts = [(int(digits[:k]), int(digits[k:])) for k in range(1, len(digits)) if digits[k] != "0" and int(digits[k:]) == 2 * int(digits[:k])]
        if len(cuts) == 1:
            return {"rank_line": cuts[0][0], "participants": cuts[0][1]}
    return {"rank_line": None, "participants": None}


def _breakthrough(img: MatLike) -> dict:
    return _parse_breakthrough("".join(t for t, _ in ocr(_breakthrough_crop(img))))


def _texts(regions: dict[str, MatLike], cache: RegionCache | None) -> dict[str, str]:
    """各区域的 OCR 文字：缓存命中的直接用，其余合成一次 Apple Vision 调用。"""
    out, pending = {}, {}
    for key, crop in regions.items():
        hit, got = cache.get(f"text:{key}", crop) if cache is not None else (False, None)
        if hit:
            out[key] = got
        else:
            pending[key] = (crop, got)
    if pending:
        texts = ocr_batch([crop for crop, _ in pending.values()])
        for (key, (_, thumb)), text in zip(pending.items(), texts, strict=True):
            out[key] = text
            if cache is not None:
                cache.put(f"text:{key}", thumb, text)
    return out


RANK_STRIP = (110, 5, 720, 170)
RANK_WHITE = (((0, 0, 235), (180, 25, 255)), ((0, 0, 215), (180, 45, 255)), ((0, 0, 200), (180, 60, 255)))  # 严格 → 放宽（实机截图外框偏灰、被遮挡断开）


RANK_CYAN_MIN_W = 50  # 玩家卡青色底宽约 80；NPC 卡是黄绿底
RANK_CYAN_TOL = 8


def _longest_run(flags) -> tuple[int, int] | None:
    best, x = None, 0
    while x < len(flags):
        if flags[x]:
            start = x
            while x < len(flags) and flags[x]:
                x += 1
            if best is None or x - start > best[1]:
                best = (start, x - start)
        x += 1
    return best


FINALS_BADGE_BOX = (220, 2, 480, 45)  # 本战排名条顶上的 1st / 2nd / 3rd 名次徽章（橙金色）
FINALS_TURNS_MIN = 0.45
FINALS_BADGE_MIN = 400  # 橙金像素：本战考试 1409–1451，选拔考试 0
FINALS_NPC_W = 69  # NPC 卡宽；玩家卡约 122


def is_finals_strip(img: MatLike) -> bool:
    """本战（produce-008）考试顶部是 3 人名次条（1st / 2nd / 3rd 徽章），选拔赛是突破线 + 多人分数。"""
    x1, y1, x2, y2 = FINALS_BADGE_BOX
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return int(((hsv[:, :, 0] >= 5) & (hsv[:, :, 0] <= 25) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)).sum()) >= FINALS_BADGE_MIN


FINALS_CARD_X0 = 223  # 名次条第一张卡左缘
FINALS_ME_W = 122  # 玩家卡宽（白框）
FINALS_SCORE_Y = (128, 168)
FINALS_SCORE_OVERHANG = 6  # 玩家分数框右缘超出白框的像素（见 read_finals_ranking）


FINALS_ME_RIGHTS = tuple(FINALS_CARD_X0 + FINALS_NPC_W * k + FINALS_ME_W for k in range(3))  # 第 1 / 2 / 3 名时玩家卡右端：345 / 414 / 483
FINALS_RIGHT_TOL = 10


FINALS_ME_LEFTS = tuple(FINALS_CARD_X0 + FINALS_NPC_W * k for k in range(3))  # 第 1 / 2 / 3 名时玩家卡左缘：223 / 292 / 361
FINALS_EDGE_MIN = 0.85  # 玩家卡左侧白框竖边：整列白 0.92–0.97；NPC 卡没有白边


def _finals_me_left(img: MatLike) -> int | None:
    """玩家卡左缘：白框的左侧竖边（y 10–160 整列近乎全白）。背景亮时顶边会和背景白色连成一片，竖边不受影响。"""
    def edge(x: int) -> float:
        hsv = cv2.cvtColor(img[10:160, x - 4 : x + 7], cv2.COLOR_BGR2HSV)
        return float((cv2.inRange(hsv, (0, 0, 200), (180, 60, 255)) > 0).mean(axis=0).max())

    # 左右两条竖边都要白：背景亮时第 1 张卡左缘外侧也是一整列白（r2 00149），NPC 卡右缘处是下一张卡或深色分数条
    best = None
    for left in FINALS_ME_LEFTS:
        lw, rw = edge(left), edge(left + FINALS_ME_W)
        if min(lw, rw) >= FINALS_EDGE_MIN and (best is None or lw + rw > best[0]):
            best = (lw + rw, left)
    return best[1] if best else None


def _finals_me_right(img: MatLike) -> int | None:
    """玩家卡白框顶边的右端（左上角被名次徽章盖住，左端不可靠）：前 14 行里长 60–140、右端贴近 FINALS_ME_RIGHTS 的白色横线，
    吸附到最近的那个位置（背景高光也有白色横线，但不在这三个位置）。"""
    hsv = cv2.cvtColor(img[0:14, 200:720], cv2.COLOR_BGR2HSV)
    for lo, hi in (((0, 0, 225), (180, 40, 255)), ((0, 0, 200), (180, 60, 255))):
        white = cv2.inRange(hsv, lo, hi) > 0
        for row in range(3, 14):
            x = 0
            while x < white.shape[1]:
                if white[row, x]:
                    start = x
                    while x < white.shape[1] and white[row, x]:
                        x += 1
                    if 40 <= x - start <= 140:  # 左段被「3rd」徽章盖住时只露出约 55 像素（r2 00143）
                        end = x + 200
                        near = min(FINALS_ME_RIGHTS, key=lambda r: abs(r - end))
                        if abs(near - end) <= FINALS_RIGHT_TOL:
                            return near
                x += 1
    return None


def _ocr_int(img: MatLike, box: tuple[int, int, int, int]) -> int | None:
    x1, y1, x2, y2 = box
    t = "".join(a for a, _ in ocr(cv2.resize(img[y1:y2, x1:x2], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)))
    m = re.fullmatch(r"\d+", t.replace(",", "").replace(" ", ""))
    return int(m.group()) if m else None


def vote_truncated(votes: list[int | None]) -> tuple[int | None, int]:
    """长数字多种读法投票，按已知的错法算支持：读数 a 支持 b ⇔ a == b，或 a 是 b 掉了末位（ddddocr 读 6 位数常掉末位，
    main9 #198 放大 OCR 也掉了：204412 读成 20441），或 a 是 b 掉了开头几位（字体比对 11415 读成 15）。
    支持数最多的胜出，并列取更长的。返回 (值, 支持数)。"""
    vals = [v for v in votes if v is not None]

    def supports(a: int, b: int) -> bool:
        sa, sb = str(a), str(b)
        return a == b or (len(sa) == len(sb) - 1 and sb.startswith(sa)) or (len(sa) < len(sb) and sb.endswith(sa))

    if not vals:
        return None, 0
    return max(((b, sum(supports(a, b) for a in vals)) for b in set(vals)), key=lambda bn: (bn[1], len(str(bn[0]))))


def read_finals_ranking(img: MatLike) -> dict:
    """本战考试顶部名次条：卡按名次从左到右紧挨着排（NPC 卡宽 69，玩家卡宽 122、白框），玩家卡位置随名次变。
    {my_rank_index, my_score, my_score_read, npc_scores（从左到右）, finals_round: 1 / 2 / None}。
    - 玩家卡：白框顶边右端 − 122；名次 = 左边 NPC 卡数 + 1（徽章文字 OCR 不可靠：「Lst」「2na」「13ral」）。
    - 玩家分数：整张卡宽的分数行，放大 OCR / 字体比对 / ddddocr 三票取多数（窄框会切掉末位：1085→108）。
    - NPC 分数：单格放大 OCR（5–6 位；ddddocr 会掉末位、字体比对认不出这号小字）。
    - finals_round：两位 NPC 的目标分按主数据区间归轮（finals_round_from_npc）；区间外 / 互相矛盾的读数作废。"""
    from hiro.vision.digits import read_dddd

    out = {"my_rank_index": None, "my_score": None, "my_score_read": None, "npc_scores": [], "finals_round": None}
    px = _finals_me_left(img)
    if px is None:
        right = _finals_me_right(img)
        if right is None:
            return out
        px = right - FINALS_ME_W
    right = px + FINALS_ME_W
    left_npcs = max(0, round((px - FINALS_CARD_X0) / FINALS_NPC_W))
    out["my_rank_index"] = left_npcs + 1
    y1, y2 = FINALS_SCORE_Y
    # 左缘紧挨左边 NPC 分数的末位，收 5 像素（否则读成 902660）；右缘放出 FINALS_SCORE_OVERHANG：
    # 6 位分数的末位会伸出白框 5 像素（GKMS 第四局 123406 → 12340、113066 → 11306），右边 NPC 卡的数字从白框外 8 像素起
    box = (px + 5, y1, right + FINALS_SCORE_OVERHANG, y2)
    font, _ = read_score(img, (px + 6, y1, right + FINALS_SCORE_OVERHANG - 1, y2 - 2))
    votes = [_ocr_int(img, box), font, read_dddd(img, box)]
    best, n = vote_truncated(votes)
    out["my_score"] = best if n >= 2 else None
    out["my_score_read"] = {"method": "vote", "confidence": round(n / 3, 2), "note": None if n == 3 else f"三次读数 {votes}（放大 OCR / 字体 / ddddocr）"}
    xs = [FINALS_CARD_X0 + FINALS_NPC_W * i for i in range(left_npcs)] + [right + 2 + FINALS_NPC_W * j for j in range(2 - left_npcs)]
    for x in xs:
        if x + FINALS_NPC_W > img.shape[1]:
            out["npc_scores"].append(None)
            continue
        if ocr_engine() == "vision":
            v = _ocr_int(img, (x, y1, x + FINALS_NPC_W + 1, y2))
        else:
            # RapidOCR / Windows OCR 读这号小字有系统性错（596029→598029、460505→430505，换放大倍数多次读也一致地错，投票选不对），
            # ddddocr 框向右放宽 4 像素读 6 位数：本战 11 帧要么全对、要么读出一两位被下限作废，没给过错值
            v = read_dddd(img, (x, y1, x + FINALS_NPC_W + 5, y2))
        out["npc_scores"].append(v if v is not None and FINALS_NPC_MIN <= v <= 999_999 else None)
    rnd = out["finals_round"] = finals_round_from_npc(out["npc_scores"])
    # 不在本轮区间的读数是读错的（两位互相矛盾时两位都不可信）：作废，别让决策拿 R2 的目标分打 R1
    out["npc_scores"] = [v if v is not None and rnd is not None and npc_round(i, v) == rnd else None
                         for i, v in enumerate(out["npc_scores"])]
    return out


# 本战 NPC 的分数是这一轮的目标分，整场考试不变：R1 约 29–40 万、R2 约 42–60 万。读成五位数一定是掉了首位
# （Windows 第二局 RapidOCR：451280 → 51280，名次自洽检查拿它当上限，把我方正确的 97371 等分数丢了 8 次），作废
FINALS_NPC_MIN = 250_000
# 本战 NPC 目标分（主数据 ProduceExamBattleNpcGroup produce_008-01 / -02，8 位 NPC 偶像的 scoreMin–scoreMax 合并）：
# 第一位 / 第二位各自落在很窄的区间，两轮不重叠。(第一位, 第二位) 的 (下限, 上限)
FINALS_NPC_BANDS = {1: ((397_457, 409_161), (293_847, 322_182)), 2: ((590_229, 618_779), (417_672, 507_672))}


def npc_round(pos: int, v: int) -> int | None:
    """第 pos 位（0 / 1）NPC 的目标分属于哪一轮；不在任何一轮的区间里给 None（读错）。"""
    return next((r for r, bands in FINALS_NPC_BANDS.items() if pos < len(bands) and bands[pos][0] <= v <= bands[pos][1]), None)


def finals_round_from_npc(npc: list[int | None]) -> int | None:
    """按 NPC 目标分判本战第几轮。npc 从左到右 = NPC 之间的名次（第一位总是分数高的那位）。
    每位按主数据区间归到一轮；区间外的读数当读错。两位都归上了必须是同一轮，否则 None：
    Windows 第三局 ddddocr 把 R1 的 399562 读成 599562（3→5），单看第一位落在 R2 区间，R1 整场被当成 R2 打；
    第二位 303083 仍是 R1，互相矛盾就不判，交给调用方的其他信号（剩余回合、考试场次）。
    都读不到（加分特效挡住，r2 00197）给 None。"""
    rounds = {npc_round(i, v) for i, v in enumerate(npc[:2]) if v is not None} - {None}
    return rounds.pop() if len(rounds) == 1 else None


def read_ranking(img: MatLike) -> dict:
    """顶部排名栏：玩家卡（白色描边、比其他卡大）与其余 NPC 卡的分数，从左到右。

    玩家卡位置随名次变化，所以先按白色外框找到玩家卡，再读其底部分数；NPC 卡按黄绿色底定位。
    """
    if is_finals_strip(img):
        return read_finals_ranking(img)
    x1, y1, x2, y2 = RANK_STRIP
    strip = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    # 玩家卡的白色外框约 131x158。分数数字的白描边会与外框连通，连通域宽度可达 200，
    # 所以只找外框顶边：最上面几行里长 110–145 的连续白色横线。
    # 实机（MaaTools 进程内截图）外框偏灰（S 24–65），严格阈值只得一小段；先严格、找不到再放宽，旧数据集结果不变
    me = None
    for lo, hi in RANK_WHITE:
        white = cv2.inRange(hsv, lo, hi)
        for row in range(14):
            line = white[row] > 0
            x = 0
            while x < len(line) and me is None:
                if line[x]:
                    start = x
                    while x < len(line) and line[x]:
                        x += 1
                    if 110 <= x - start <= 145:
                        me = (int(start + x1), int(row + y1), int(x - start), 160)
                x += 1
            if me is not None:
                break
        if me is not None:
            break
    # 白框顶边和背景（白衣人群、亮地面）连成一片时长度超过 145（实测 151–192），找不到；JPEG 压缩后这片白又可能断成
    # 恰好 110–145 的一段，定到别处去（读出半个数）。所以用玩家卡的青色底核对：青色区左缘比白框左缘靠右 2–4 像素
    # （173 帧中 166 帧），差得多或白框没找到时以青色底为准
    cyan = (hsv[15:45, :, 0] >= 85) & (hsv[15:45, :, 0] <= 105) & (hsv[15:45, :, 1] >= 150) & (hsv[15:45, :, 2] >= 170)
    run = _longest_run(cyan.mean(axis=0) >= 0.8)
    if run is not None and run[1] >= RANK_CYAN_MIN_W:
        cx = int(run[0] + x1 - 3)
        if me is None or abs(me[0] - cx) > RANK_CYAN_TOL:
            me = (cx, int(y1 + 7), 123, 160)
    green = cv2.inRange(hsv, (30, 80, 150), (45, 255, 255))
    cols = (green[:40] > 0).sum(axis=0)
    npcs, x = [], 0
    while x < len(cols):
        if cols[x]:
            start = x
            while x < len(cols) and cols[x]:
                x += 1
            if x - start >= 25:
                npcs.append((start + x1, x - start))
        x += 1
    out = {"my_rank_index": None, "my_score": None, "npc_scores": [], "my_score_read": None}
    if me is not None:
        mx, my, mw, mh = me
        out["my_score"], out["my_score_read"] = read_score(img, (mx + 4, my + mh - 42, mx + mw - 4, my + mh - 4))
    for nx, nw in npcs:
        if me is not None and me[0] <= nx <= me[0] + me[2]:
            continue
        # 黄绿底只占卡片左侧约 2/3，四位数会延伸到右侧竖条上
        out["npc_scores"].append(_digits(img, (nx - 2, 128, min(720, nx + nw + 34), 162)))
    if me is not None:
        out["my_rank_index"] = sum(1 for nx, _ in npcs if nx < me[0]) + 1
    return out


ART_MIN_SCORE = 0.55


def _asset_to_card(key: str) -> SkillCard | None:
    asset = key.removeprefix("img_general_skillcard_")
    base = asset.split("-")
    # 角色专属插图带 -角色 后缀（如 men-2_111-shro），数据库 assetId 不带
    asset_id = "img_general_skillcard_" + "-".join(base[:2])
    return SkillCard.from_asset_id(asset_id) or SkillCard.from_asset_id(key)


UNAVAILABLE_LETTER_MAX = 170  # 卡名首字母框平均亮度：可用约 225，不可用（压暗）约 115
# 过场白光（「スキルカードサポート」「もう一枚使用できます」等）叠在手牌上时，字母亮度落在中间段，可打 / 压暗分不开：
# 第二十七局 #168 压暗的两张 182 / 177 被判可打，第二十八局 #178 可打的 イメトレ 也是 177。全部 4398 帧里 140–210 只出现在这类帧
#（正常：压暗 90–120、可打 220–240）→ 整手牌标 hand_layout="overlay"，观测里手牌给 None（同「动画中」），等下一帧
AMBIGUOUS_LETTER_V = (140, 210)


CARD_HALF_W = 90  # 手牌卡面半宽（720x1280）；3 张时卡间距 217 不重叠，4 张时 165 会被下一张盖住右侧


def _name_crop(img: MatLike, letter: Rect, next_cx: int | None = None) -> MatLike:
    """卡名栏。next_cx 为右边下一张卡的中心：后出的卡盖在前一张上，可见区只到下一张卡的左缘。"""
    cx, top = letter.x1 + letter.w // 2, letter.y1
    right = cx + 95 if next_cx is None else min(cx + 95, next_cx - CARD_HALF_W)
    return img[top + 14 : top + 44, max(0, cx - 95) : right]


def _clean_name(text: str) -> str:
    return text.replace("＋", "+").replace(" ", "")


def _name_bar(img: MatLike, letter: Rect) -> str:
    """卡面下方的卡名栏，带强化标记（デイドリーミング+、セルフケアの基本++）。"""
    return _clean_name("".join(t for t, _ in ocr(_name_crop(img, letter))))


# 卡名比对前的归一化：小写假名 → 大写（OCR 常把「ティ」的「ィ」读成「イ」）、片假名 / 平假名形近字（ヘ/へ）、长音的各种写法
_KANA_FOLD = str.maketrans({**{a: b for a, b in zip("ァィゥェォャュョッヮぁぃぅぇぉゃゅょっゎ", "アイウエオヤユヨツワあいうえおやゆよつわ")},
                            "へ": "ヘ", "べ": "ベ", "ぺ": "ペ", "一": "ー", "-": "ー", "−": "ー", "ｰ": "ー", "!": "！", "?": "？"})  # fmt: skip
NAME_TRIM_MAX = 2  # 卡名末尾最多去掉几个字再按前缀找（被下一张卡盖住时末字常被读成「！」「く」「2」）
NAME_PREFIX_MIN = 3  # 前缀至少几个字才按前缀找唯一卡名


def _fold(name: str) -> str:
    return name.translate(_KANA_FOLD)


@lru_cache(maxsize=1)
def _base_names() -> dict[str, list[SkillCard]]:
    """归一化后的基础卡名（不带「+」）→ 未强化版本的卡（同名多张时有多项）。"""
    out: dict[str, list[SkillCard]] = {}
    for card in SkillCard.all():
        if not card.upgrade_count:
            out.setdefault(_fold(card.name.rstrip("+")), []).append(card)
    return out


def _card_from_name(text: str, art_top: list[tuple[str, float]]) -> tuple[SkillCard | None, bool]:
    """卡名栏 OCR 文字 → (未强化版本的卡, 是否完整卡名精确命中)。

    精确命中归一化后的基础卡名直接定卡；否则卡名可能被下一张卡盖住或末字读错：从末尾去掉 0–2 个字，
    剩下 ≥3 个字且只是一个卡名的前缀时才认（例：「きらきら紙！」→ きらきら紙吹雪，「セルフケアの基2」→ セルフケアの基本）。
    同名多张时取卡面候选里出现的那张。前缀不唯一或读不出时返回 (None, False)。"""
    base = _fold(text.rstrip("+"))
    names = _base_names()
    hit, exact = names.get(base), True
    if not hit:
        exact = False
        for k in range(len(base), max(NAME_PREFIX_MIN, len(base) - NAME_TRIM_MAX) - 1, -1):
            found = {n for n in names if n.startswith(base[:k])}
            if len(found) == 1:
                hit = names[found.pop()]
                break
            if found:  # 前缀对应多张卡：再短只会更多，不猜
                break
    if not hit:
        return None, False
    if len(hit) > 1:
        art_ids = {c._id for c in (_asset_to_card(k) for k, _ in art_top) if c is not None}
        hit = [c for c in hit if c._id in art_ids] or hit
    return hit[0], exact


def _overlay_on_hand(img: MatLike, letters: list) -> bool:
    """有卡名首字母的亮度落在 AMBIGUOUS_LETTER_V：过场白光叠在手牌上，可打 / 不可打判不准。"""
    lo, hi = AMBIGUOUS_LETTER_V
    return any(lo <= float(img[r.y1 : r.y1 + r.h, r.x1 : r.x1 + r.w].mean()) <= hi for r in letters)


def _available(img: MatLike, letter: Rect) -> bool:
    x1, y1, w, h = letter.xywh
    return float(img[y1 : y1 + h, x1 : x1 + w].mean()) > UNAVAILABLE_LETTER_MAX


# KAA 的卡名首字母框推出的卡面区域相对图库卡面系统性偏了约 (+3, +8) 像素。
# 插图细节多的卡不受影响，眠気、セルフケアの基本 这类淡色素面卡会因此掉到 0.4 以下而认错。
ART_SHIFTS = [(dx, dy) for dy in (-12, -8, -4, 0) for dx in (-6, -3, 0)]


ART_SHIFT_DEFAULT = (-3, -8)
ART_SHIFT_TRUST = 0.7  # 常见偏移下已达此分即不再搜索（实测正确卡 0.75–0.90），省去其余 11 次比对


def _match_shifted(img: MatLike, x1: int, y1: int, x2: int, y2: int) -> list[tuple[str, float]]:
    dx, dy = ART_SHIFT_DEFAULT
    best = match_card_art(img[y1 + dy : y2 + dy, x1 + dx : x2 + dx], k=5)
    if best[0][1] >= ART_SHIFT_TRUST:
        return best
    for dx, dy in ART_SHIFTS:
        top = match_card_art(img[y1 + dy : y2 + dy, x1 + dx : x2 + dx], k=5)
        if best is None or top[0][1] > best[0][1]:
            best = top
    return best


def _name_mask(crop: MatLike) -> np.ndarray:
    """卡名栏文字掩码（放大 3 倍）：Otsu 取少数一侧，再并上浅蓝色（第二次强化的「+」是浅蓝，Otsu 会丢掉横笔）。"""
    big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, b = cv2.threshold(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text = b > 0 if (b > 0).mean() < 0.5 else b == 0
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    blue = (hsv[:, :, 0] >= 90) & (hsv[:, :, 0] <= 130) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 120)
    return text | blue


def _is_plus(m: np.ndarray) -> bool:
    h, w = m.shape
    if not 0.7 <= w / h <= 1.4 or h < 12:
        return False
    cy, cx, t, q = h // 2, w // 2, max(1, h // 6), h // 3
    row = max(m[r, :].mean() for r in range(cy - t, cy + t + 1))
    col = max(m[:, c].mean() for c in range(cx - t, cx + t + 1))
    corners = np.concatenate([m[:q, :q].ravel(), m[:q, -q:].ravel(), m[-q:, :q].ravel(), m[-q:, -q:].ravel()]).mean()
    return row > 0.75 and col > 0.75 and corners < 0.2


CENTER_TOL = 15  # 放大 3 倍后，实际右端与按居中推算的右端之差在此以内视为卡名完整可见


def plus_count(crop: MatLike, *, center_x: int | None = None) -> int | None:
    """卡名栏末尾「+」的个数 = 强化次数。数据集 288 张手牌与 OCR 卡名 287 张一致，约 1ms。
    center_x：卡片中心在 crop 里的 x（原始像素）。卡名居中排版，可由可见文字左端推出完整卡名的右端；
    右端超出可见区（4 张以上手牌时被下一张卡盖住）说明末尾可能有看不到的「+」，返回 None（未知）。"""
    m = _name_mask(crop)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8))
    h, w_all = m.shape
    comps = sorted((st[i][0], i) for i in range(1, n) if st[i][4] >= 20 and st[i][0] > 0 and st[i][1] > 0 and st[i][1] + st[i][3] < h)
    k = 0
    for _, i in reversed(comps):
        x, y, w, ch = st[i][:4]
        if not _is_plus(lab[y : y + ch, x : x + w] == i):
            break
        k += 1
    if center_x is not None and comps:
        left = comps[0][0]
        full_right = 2 * center_x * 3 - left  # 居中排版：完整卡名的右端
        right = max(st[i][0] + st[i][2] for _, i in comps)
        if abs(right - full_right) > CENTER_TOL and full_right > w_all - 6:
            return None  # 右边有字被下一张卡盖住
    return k


ART_SURE = 0.7  # 卡面相关分达到此值不再 OCR 卡名核对（数据集 458 张手牌：≥0.7 的全部正确）；低于此值（含靠前五一致才认的）读卡名交叉核对
NAME_EXACT_CONF = 0.9  # 卡名与卡面不一致、以卡名为准时的置信度（卡名完整精确命中）
NAME_PREFIX_CONF = 0.7  # 同上，卡名只是前缀命中（被下一张卡盖住）


def _read_card(img: MatLike, slot: int, letter: Rect, next_cx: int | None = None) -> dict:
    """一张手牌。卡面相关定卡、卡名栏数「+」定强化次数；卡面没把握（< ART_SURE）时 OCR 卡名栏交叉核对（约 100ms）：
    两者一致 → 置信度 1.0；不一致且卡名唯一对应一张卡 → 以卡名为准（卡面结果记在 art_name）；卡名读不出 → 保留卡面结果。
    两者都定不了卡时 name 为 None（不猜）。"""
    x2, y2 = letter.center.x, letter.top_left.y
    x1, y1 = x2 - kaa_ui.CARD_OFFSET[0], y2 - kaa_ui.CARD_OFFSET[1]
    top = _match_shifted(img, x1, y1, x2, y2)
    key, score = top[0]
    base = lambda k: "-".join(k.removeprefix("img_general_skillcard_").split("-")[:2])  # noqa: E731
    consensus = sum(1 for k, _ in top if base(k) == base(key))
    # 角色专属卡的各角色插图几乎相同，同一卡占据前五中 ≥3 个时即使分数低也可信
    art = _asset_to_card(key) if score >= ART_MIN_SCORE or consensus >= 3 else None
    name_text, named, exact = None, None, False
    if art is None or score < ART_SURE:
        name_text = _clean_name("".join(t for t, _ in ocr(_name_crop(img, letter, next_cx))))
        named, exact = _card_from_name(name_text, top)
    art_name = None
    if named is not None and (art is None or named._id != art._id):
        found, source, conf = named, "name", NAME_EXACT_CONF if exact else NAME_PREFIX_CONF
        art_name = art.name if art is not None else None
    elif art is not None:
        found, source = art, ("art+name" if named is not None else "art")
        conf = 1.0 if named is not None else round(score, 3)
    else:
        return {"slot": slot, "name": None, "name_text": name_text, "art_key": key, "art_score": round(score, 3), "confidence": 0.0}
    cx = letter.x1 + letter.w // 2
    covered = next_cx is not None and next_cx - CARD_HALF_W < cx + 95
    plus = plus_count(_name_crop(img, letter, next_cx), center_x=(cx - max(0, cx - 95)) if covered else None)
    card = card_version(found._id, plus or 0) or found  # 强化次数未知（被盖住）时按基础版给效果，plus 为 None 提示调用方
    # 眠気等トラブル卡卡面本身就是灰的，按亮度会误判为不可打出；实测可以打出（无效果）
    available = card.category == "ProduceCardCategory_Trouble" or _available(img, letter)
    entry = {"slot": slot, "available": available, "box": [x1, y1, x2 - x1, y2 - y1], "source": source, "name_text": name_text,
             "art_score": round(score, 3), "art_consensus": consensus, "plus": plus, "confidence": conf, **card_summary(card)}  # fmt: skip
    if art_name is not None:
        entry["art_name"] = art_name  # 卡面给的另一张卡（被卡名否决）
    if plus is None:
        entry["upgrade"] = None  # 「+」被下一张卡盖住：强化次数未知（card_id / 效果按基础版给）
    return entry


# ---------------------------------------------------------------- 预计得分徽章
# 得分牌左上角的「预计得分」：彩色数字（Vo 粉 / Da 蓝 / Vi 橙）白描边，前面可能带一个同色属性图标，左对齐，字体同 HUD（Campus-Alphanumeric），等宽约 28.7px。
# 位置相对卡名首字母框中心 cx（720x1280）：卡左缘约 cx-95，数字顶约 y889、底约 y921。实测 161 帧手牌 1–5 张布局一致。
BADGE_X = (-92, 92)  # 读取框相对 cx（到卡右缘）；右端再被下一张卡的左缘（next_cx-95）截住。原来到 +58，6 位数末位被切掉（main8 #155 106837→10683）
BADGE_Y = (884, 934)
BADGE_PITCH = 28  # 字距（略小于实测 28.7，判「后面还能藏一位」时偏保守）
BADGE_SCALE = 3
BADGE_GLYPH_H = (24, 38)  # 字高（原始像素）；实测 31–32
BADGE_GLYPH_W_MAX = 30  # 字宽实测 19–25；卡框角等同色块 ≥ 34
BADGE_RING_WHITE = 0.2  # 字外圈白描边占比下限：徽章数字 0.26–0.57（Vi 橙字最低），卡面插图 / 底色 ≤ 0.05
BADGE_HUE_TOL = 8
NEXT_CARD_GAP = 95  # 下一张卡的左缘 = next_cx - 95


def read_badge(img: MatLike, cx: int, next_cx: int | None = None) -> dict:
    """一张手牌的预计得分徽章 → {"badge_score": int | None, "badge_text": 看得见的数字串 | None}。

    没有徽章（非得分牌）时两项都为 None。读不全时 badge_score 为 None、badge_text 留可见部分（不猜）：
    - 数字串里有字形认不出（特效遮挡）或颜色不一致；
    - 数字一直延伸到框右端（6 位以上）；
    - 被下一张卡盖住、且可见末位之后还容得下一位（5 张手牌时 4 位数可能其实是 5 位）。
    约 3–4ms / 张。"""
    x1, y1, y2 = cx + BADGE_X[0], BADGE_Y[0], BADGE_Y[1]
    limit_next = next_cx is not None and next_cx - NEXT_CARD_GAP < cx + BADGE_X[1]
    x2 = next_cx - NEXT_CARD_GAP if limit_next else cx + BADGE_X[1]
    x1, x2 = max(0, x1), min(img.shape[1], x2)
    none = {"badge_score": None, "badge_text": None}
    if x2 - x1 < BADGE_PITCH:
        return none
    s = BADGE_SCALE
    crop = cv2.resize(img[y1:y2, x1:x2], None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 1] < 50) & (hsv[:, :, 2] > 215)
    fill = ((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 120)).astype(np.uint8)
    fill = cv2.morphologyEx(fill, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(fill)
    kernel = np.ones((7, 7), np.uint8)
    tpl = _templates()  # 没有数字字体（开源默认素材）时为空：白描边字形记为「#」（是数字、不知道是几），整串交给 ddddocr
    glyphs = []  # (x, w, 字符或 None, 色相, 是否白描边)，原始像素
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not BADGE_GLYPH_H[0] * s <= h <= BADGE_GLYPH_H[1] * s or w > h or w > BADGE_GLYPH_W_MAX * s or area < 40 * s:
            continue
        m = (lab == i).astype(np.uint8)
        ring = (cv2.dilate(m, kernel) > 0) & (m == 0)
        outlined = white[ring].mean() >= BADGE_RING_WHITE  # 否：卡面插图、底色，或描边被压浅的字
        ch = None
        if outlined and not tpl:
            ch = "#"
        elif outlined:
            score, best = max((_iou(_scale(lab[y : y + h, x : x + w] == i), t), c) for c, t in tpl.items())
            ch = best if score >= 0.7 and best.isdigit() else None
        hue = int(np.median(hsv[:, :, 0][m > 0]))
        glyphs.append((x / s, w / s, ch, hue, outlined))
    glyphs.sort()

    def same_hue(a: int, b: int) -> bool:
        d = abs(a - b)
        return min(d, 180 - d) <= BADGE_HUE_TOL

    if tpl:
        start = next((k for k, g in enumerate(glyphs) if g[2] is not None), None)
    else:  # 没有字体分不出「是不是数字」：取同色白描边字形最多的那种颜色，从它的第一个起（前面异色的属性图标等不算）
        marked = [k for k, g in enumerate(glyphs) if g[2] is not None]
        votes = {k: sum(same_hue(glyphs[j][3], glyphs[k][3]) for j in marked) for k in marked}
        start = min((k for k in marked if votes[k] == max(votes.values())), default=None)
    if start is None:
        return none

    # 从第一个认得出的字起，向右取同色、连续的一串：字距 ≤ 1.5 个字距为相邻；1.5–2.6 个字距说明中间有一位没取到。
    # 串的前后紧挨着同色、字大小但认不出的连通域（描边被压浅、被特效盖住）也说明可能少了一位。这些情况整串不可信
    run, unsure = [glyphs[start]], False
    for g in glyphs[start + 1 :]:
        if not same_hue(g[3], run[0][3]):
            continue  # 别的颜色：卡面插图碎块（字间露出的底色）
        gap = g[0] - run[-1][0]
        if gap > BADGE_PITCH * 2.6 or (not g[4] and gap > BADGE_PITCH * 1.5):
            break  # 没有白描边的同色块（卡框、插图）只有紧挨着才算「可能少一位」
        if gap > BADGE_PITCH * 1.5:
            unsure = True
        run.append(g)
    before = [g for g in glyphs[:start] if same_hue(g[3], run[0][3]) and run[0][0] - g[0] <= BADGE_PITCH * 1.5]
    unsure = unsure or bool(before)
    width = x2 - x1
    last_right = run[-1][0] + run[-1][1]
    if not tpl:  # 没有字体：按这串字形的范围裁图交给 ddddocr，位数与字形数一致才用
        from hiro.vision.digits import read_dddd

        span = (max(0, x1 + int(run[0][0]) - 4), y1, min(img.shape[1], x1 + int(last_right) + 5), y2)
        value = read_dddd(img, span)
        if value is None or len(str(value)) != len(run):
            return {"badge_score": None, "badge_text": None}
        run = [(*g[:2], c, *g[3:]) for g, c in zip(run, str(value))]
    text = "".join(g[2] or "?" for g in run)
    visible = text.replace("?", "") or None
    if unsure or None in [g[2] for g in run]:
        return {"badge_score": None, "badge_text": visible}
    if last_right >= width - 3:
        return {"badge_score": None, "badge_text": visible}  # 末位贴着框右端：后面可能还有
    if limit_next and run[-1][0] + BADGE_PITCH >= width - 3:
        return {"badge_score": None, "badge_text": visible}  # 下一位的位置已在下一张卡底下
    return {"badge_score": int(text), "badge_text": text}


# ---------------------------------------------------------------- 手牌布局
# 卡名首字母框中心 x（720x1280），按手牌张数。数据集 330 帧实测偏差 ≤1px
HAND_LAYOUT = {1: (361,), 2: (252, 469), 3: (144, 361, 577), 4: (114, 278, 443, 607), 5: (114, 237, 361, 484, 608)}
HAND_LAYOUT_TOL = 4


def hand_layout(xs: list[int]) -> str:
    """手牌位置是否稳定：ok（与 n 张布局一致）/ empty（没有手牌）/ partial（是更多张布局的一部分：有一张被选中抬起或正在打出）/
    shifted（与任何布局都对不上：发牌 / 出牌动画中）。"""
    if not xs:
        return "empty"

    def fits(layout) -> bool:
        return all(any(abs(x - lx) <= HAND_LAYOUT_TOL for lx in layout) for x in xs)

    if len(xs) in HAND_LAYOUT and all(abs(x - lx) <= HAND_LAYOUT_TOL for x, lx in zip(xs, HAND_LAYOUT[len(xs)])):
        return "ok"
    if any(fits(HAND_LAYOUT[k]) for k in HAND_LAYOUT if k > len(xs)):
        return "partial"
    return "shifted"


def read_hand(img: MatLike, *, letters: list[Rect] | None = None, cache: RegionCache | None = None) -> tuple[list[dict], int]:
    """手牌：按 KAA 的卡名首字母框定位每张卡，用灰度相关识别卡面（对压暗的不可用卡也有效），卡面没把握时用卡名栏 OCR 核对；
    每张另读左上角预计得分徽章（badge_score）。cache：某张卡（卡面 + 卡名栏 + 首字母框）/ 徽章区与上次相同则复用。"""
    if letters is None:
        letters = sorted(_locate_letters(img), key=lambda r: r.x1)
    hand = []
    for slot, letter in enumerate(letters):
        x2, y2 = letter.center.x, letter.top_left.y
        x1, y1 = x2 - kaa_ui.CARD_OFFSET[0], y2 - kaa_ui.CARD_OFFSET[1]
        region = img[max(0, y1 - 12) : letter.y1 + 46, max(0, x1 - 8) : min(img.shape[1], letter.x1 + letter.w // 2 + 96)]
        next_cx = letters[slot + 1].x1 + letters[slot + 1].w // 2 if slot + 1 < len(letters) else None
        entry = cached(cache, f"card@{x2}", region, lambda slot=slot, letter=letter, nx=next_cx: _read_card(img, slot, letter, nx))
        cx = letter.x1 + letter.w // 2
        bx1, bx2 = max(0, cx + BADGE_X[0]), cx + BADGE_X[1] if next_cx is None else min(cx + BADGE_X[1], next_cx - NEXT_CARD_GAP)
        badge_crop = img[BADGE_Y[0] : BADGE_Y[1], bx1 : max(bx1 + 1, bx2)]
        entry.update(cached(cache, f"badge@{cx}:{bx2 - bx1}", badge_crop, lambda cx=cx, nx=next_cx: read_badge(img, cx, nx)))
        entry["slot"] = slot
        hand.append(entry)
    return hand, sum(1 for c in hand if c.get("name") is None)


def read_hand_badges(img: MatLike) -> list[int | None] | None:
    """只读每张手牌的预计得分徽章（不认卡），约 10 ms：给存档路径的手牌补 badge_score。
    手牌布局不稳定（有卡被抬起 / 动画中 / 没有手牌）时返回 None。不可打出的卡游戏不显示徽章（实测 46 张都没有），给 None。"""
    letters = sorted(_locate_letters(img), key=lambda r: r.x1)
    xs = [r.x1 + r.w // 2 for r in letters]
    if hand_layout(xs) != "ok":
        return None
    return [read_badge(img, cx, xs[i + 1] if i + 1 < len(xs) else None)["badge_score"] for i, cx in enumerate(xs)]


EXAM_CACHE = RegionCache()  # 考试循环逐帧调用时复用不变区域的结果；离线评测传 cache=None


def read_hif_hud(img: MatLike, *, fallback: bool = False) -> dict:
    """考试 HUD 三个数：剩余回合、体力、元气。只用游戏字体比对（约 10–30ms），用于稳定帧判断。
    字体比对没把握（被特效遮住）时为 None；fallback=True 时退回 ddddocr（每个约 200ms）。"""
    read = read_number if fallback else read_int
    return {"remaining_turns": read(img, BOX_TURNS, align=True), "hp": read(img, BOX_HP, align=True),
            "genki": read(img, BOX_GENKI, align=True)}  # fmt: skip


def read_hif_exam(img: MatLike, *, cache: RegionCache | None = EXAM_CACHE) -> dict:
    """考试画面完整读取。cache 为区域缓存：整帧与上次几乎相同时直接返回上次结果；
    否则只重读变了的区域（状态栏槽位、手牌、P 道具、饮料、文字、HUD 数字、排名栏）。"""
    if cache is None:
        return _read_hif_exam(img, None)
    return cached(cache, "frame", cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA),
                  lambda: _read_hif_exam(img, cache))  # fmt: skip


def _read_hif_exam(img: MatLike, cache: RegionCache | None) -> dict:
    letters = sorted(_locate_letters(img), key=lambda r: r.x1)
    finals = is_finals_strip(img)  # 本战：没有突破线，顶部是 3 人名次条
    texts = _texts({"attr": _attr_crop(img)} if finals else {"attr": _attr_crop(img), "break": _breakthrough_crop(img)}, cache)
    hand, missing = read_hand(img, letters=letters, cache=cache)
    statuses = read_status_column(img, cache=cache)

    def num(name: str, box) -> int | None:
        x1, y1, x2, y2 = box
        # HUD 三个数压在浅色地面 / 角标上：按行对齐过滤背景亮块（见 digits._aligned）
        align = name in ("turns", "hp", "genki")
        return cached(cache, f"num:{name}", img[y1:y2, x1:x2], lambda: read_number(img, box, align=align))

    x1, y1, x2, y2 = RANK_STRIP
    drink_slots = read_drink_slots(img, cache=cache)
    out = {
        "remaining_turns": num("turns", BOX_TURNS),
        **_parse_attr(texts["attr"]),
        "finals_round": None,  # 本战时由 read_finals_ranking 覆盖为 1 / 2
        **cached(cache, "ranking", img[y1:y2, x1:x2], lambda: read_ranking(img)),
        **(_parse_breakthrough(texts["break"]) if not finals else {"rank_line": None, "participants": None}),
        "finals": finals,
        "genki": num("genki", BOX_GENKI),
        "hp": num("hp", BOX_HP),
        "deck_count": None,  # 画面上没有山札张数（旧版读的是 P 道具剩余次数，见 pitem_charges）
        "pitem_charges": num("pitem_charges", BOX_PITEM_CHARGES),
        "drinks": [{k: d[k] for k in ("slot", "asset", "name", "center", "score", "margin")} if d["state"] == "drink" else None
                   for d in drink_slots],
        "drink_slots": drink_slots,
        "pitems": read_pitems(img, cache=cache),
        "statuses": statuses,
        "status": status_summary(statuses),
        "hand": hand,
        "hand_layout": "overlay" if _overlay_on_hand(img, letters) else hand_layout([r.x1 + r.w // 2 for r in letters]),
        "unrecognized_cards": missing,
    }
    out["multiplier_pct"], out["multiplier_votes"] = _vote_multiplier(img, out["multiplier_pct"], cache, out["attr"])
    if out["finals"] and out.get("finals_round") is None and (out.get("remaining_turns") or 0) > 9:
        out["finals_round"] = 2  # NPC 分数被特效挡住时：剩余回合 > 9 只可能是 R2（R1 9 回合、R2 12 回合）
    return out
