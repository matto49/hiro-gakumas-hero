# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的 tasks/daily/contest
"""竞赛（コンテスト），移植 KAA tasks/daily/contest：首页竞赛按钮有红点就进去，把今天的挑战次数打完。

- 有没打完的挑战就接着打；没有就从三个对手里挑综合力最低的（OCR 读分数，读不出来挑最后一个，同 KAA）。
- 每场勾「全部跳过」、点 SKIP、次へ、終了，奖励框点閉じる。
- 回忆没编成：同 KAA 配置 when_no_set=skip（run_daily 一直这么设）——关掉提示框、不自动编成，结束这一项。
- 次数用完（「本日の挑戦回数」那句）就回首页。含ジュエル的确认框由日常中断 Popup 只点キャンセル。
"""

from __future__ import annotations

import logging
import os
import re

from hiro.daily import common
from hiro.runtime import nav
from hiro.vision.ui_templates import box, point

logger = logging.getLogger(__name__)

BUTTON = "Common.ButtonContest"
ROAD = "Daily.TextRoadToIdol"
LAST_ONGOING = "Daily.TextContestLastOngoing"
CONTEST_POINT = "Daily.PointContest"
DISMISS_POINT = "Daily.PointDissmissContestReward"
CHALLENGE_START = "Daily.ButtonContestChallengeStart"
RANKING = "Daily.ButtonContestRanking"
OVERALL = "Daily.TextContestOverallStats"
USED_UP = "Daily.Contest.TextUsedUp"
START = "Daily.ButtonContestStart"
NO_MEMORY = "Daily.TextContestNoMemory"
CROSS = "Common.IconButtonCross"
CHECKBOX = "Common.CheckboxUnchecked"
SKIP = "Daily.ButtonIconSkip"
NEXT = "Common.ButtonNextNoIcon"
END = "Common.ButtonEnd"
CLOSE = "Common.ButtonClose"
SCORE_BOXES = ("Daily.Contest.BoxScore1", "Daily.Contest.BoxScore2", "Daily.Contest.BoxScore3")
DOT = "#ff104a"  # 竞赛按钮右上角的通知红点（同 KAA）
SKIP_POINT = (12, 12)
TIMEOUT = 300.0
MAX_FIGHTS = 5  # 每天 5 次挑战权（画面右上「N/5」）
USED_UP_THRESHOLD = (
    0.7  # 11-25 DMM：「本日の挑戦権を消費しました」模板 0.79，缺省阈值 0.8 认不出 → 一直点空白到 300 秒超时（卡死）
)
USED_UP_WORDS = ("挑戦権を消費", "本日の挑戦回数")
IDLE_TICKS = 60  # 连续这么多拍什么都没认出（约 30 秒）就停：不再点空白干等
PICKS = {"lowest": None, "1": 0, "2": 1, "3": 2}


def pick_choice() -> str:
    """挑哪个对手：HIRO_CONTEST_PICK = lowest（缺省，综合力最低）/ 1 / 2 / 3（由上到下第几个，难度和奖励不同）。"""
    v = (os.environ.get("HIRO_CONTEST_PICK") or "lowest").strip().lower()
    return v if v in PICKS else "lowest"


def remaining(f) -> int | None:
    """右上角「N/5」的 N（今天还剩几次）；读不到 None。"""
    for t in f.texts:
        if t.rect.y2 < 80 and t.rect.x1 > 360 and (m := re.fullmatch(r"\s*(\d)\s*/\s*5\s*", t.text)):
            return int(m.group(1))
    return None


def used_up(f) -> bool:
    """今天的挑战次数用完了：模板（放宽到 0.7）或文字「本日の挑戦権を消費しました」或右上「0/5」。"""
    if nav.find(f, USED_UP, threshold=USED_UP_THRESHOLD) is not None:
        return True
    if any(w in t.text for t in f.texts for w in USED_UP_WORDS):
        return True
    return remaining(f) == 0


def has_dot(f, m) -> bool:
    """按钮往上、往右各扩 35 像素里有通知红点（同 KAA rect_expand(top=35, right=35)）。"""
    from hiro.vision.ocr import Box

    x1, y1, x2, y2 = m.rect
    return common.has_color(f.img, DOT, Box(x1, max(0, y1 - 35), x2 + 35, y2))


def read_scores(f) -> list[int | None]:
    """三个对手的综合力（由上到下），读不出来的位置是 None（同 KAA：一格里有几个数取最小的）。"""
    from hiro.vision.ocr import ocr

    out: list[int | None] = []
    for name in SCORE_BOXES:
        b = box(name)
        try:
            text = " ".join(t for t, _ in ocr(f.img[b.y1 : b.y2, b.x1 : b.x2])).replace(
                ",", ""
            )  # 段之间留空格：两段数字不拼成一个
        except Exception:  # noqa: BLE001
            logger.warning("读 %s 的分数失败", name, exc_info=True)
            out.append(None)
            continue
        nums = [int(n) for n in re.findall(r"\d+", text)]
        out.append(min(nums) if nums else None)
    return out


def goto_contest(rt) -> bool:
    """首页 → 竞赛页（点掉赛季奖励提示）。返回有没有没打完的挑战。"""
    state: dict = {"ongoing": None}

    def step(f):
        if (m := nav.find(f, BUTTON)) is not None:
            nav.tap_match(rt, m)
            return None
        if nav.find(f, ROAD) is not None:  # 竞赛 Tab
            if (m := nav.find(f, LAST_ONGOING)) is not None:
                nav.tap_match(rt, m)
                state["ongoing"] = True
            else:
                state["ongoing"] = False
            rt.device.tap(*point(CONTEST_POINT))
            return None
        if state["ongoing"] is True and nav.find(f, CHALLENGE_START) is not None:
            return True
        if state["ongoing"] is False:
            if nav.find(f, RANKING) is not None:
                return True
            rt.device.tap(*point(DISMISS_POINT))  # 赛季奖励提示：点空白处跳过
        return None

    rt.drive(step, 60, "进竞赛页", every=0.5)
    return bool(state["ongoing"])


def pick_contestant(rt, f, ongoing: bool) -> tuple[bool, bool]:
    """选对手。返回（处理了没有，还要不要接着打）；次数用完返回 (True, False)。"""
    if nav.find(f, USED_UP, threshold=USED_UP_THRESHOLD) is not None:
        return True, False
    if nav.find(f, RANKING) is not None and used_up(f):  # 竞赛页上再用文字 / 「0/5」确认一次（整帧 OCR，只在这一页做）
        return True, False
    if ongoing or nav.find(f, RANKING) is None or nav.find(f, OVERALL) is None:
        return False, True
    rows = sorted(common.find_all(f, OVERALL), key=lambda m: m.rect.y1)
    if not rows:
        return True, False
    choice = pick_choice()
    scores = read_scores(f)
    if PICKS[choice] is not None:
        target = PICKS[choice]  # 用户指定第几个（11-25：三个对手难度 / 奖励不同）
    else:
        scored = [(s, i) for i, s in enumerate(scores) if s is not None]
        target = min(scored)[1] if scored else len(rows) - 1
    target = min(target, len(rows) - 1)
    logger.info("对手分数 %s，按「%s」挑第 %d 位", scores, choice, target + 1)
    nav.tap_match(rt, rows[target])
    rt.sleep(2)
    return True, True


NO_MEMORY_SET = "no_memory"  # challenge 的返回值：回忆没编成（step 不能抛异常：Runtime 会接住重来）


def challenge(rt, f) -> bool | str:
    """打一场里的一拍。返回处理了没有；回忆没编成（已关掉提示框）返回 NO_MEMORY_SET。"""
    if (m := nav.find(f, START, threshold=0.75)) is not None:
        nav.tap_match(rt, m)
    if (m := nav.find(f, NO_MEMORY)) is not None:
        nav.tap_match(rt, m)
        rt.sleep(0.5)
        g = common.frame(rt)
        if (x := nav.find(g, CROSS)) is not None:
            nav.tap_match(rt, x)
        return NO_MEMORY_SET
    if (m := common.find_colored(f, CHECKBOX)) is not None:
        nav.tap_match(rt, m)
        return True
    if (m := common.find_white(f, SKIP)) is not None:
        nav.tap_match(rt, m)
        return True
    if (m := nav.find(f, NEXT)) is not None:
        nav.tap_match(rt, m)
    if (m := nav.find(f, END)) is not None:
        nav.tap_match(rt, m)
        return True
    if (m := nav.find(f, CLOSE)) is not None:
        nav.tap_match(rt, m)
    return False


def run(rt, *, home: bool = True) -> str:
    if home:
        nav.goto_home(rt)
    rt.sleep(0.3)
    f = common.frame(rt)
    btn = nav.find(f, BUTTON)
    if btn is None or not has_dot(f, btn):
        return "竞赛不用打"
    ongoing = goto_contest(rt)
    fights = {"n": 0, "idle": 0}

    def step(f):
        nonlocal ongoing
        if fights["n"] >= MAX_FIGHTS and not ongoing and nav.find(f, RANKING) is not None:
            return True  # 一天最多 5 场：打满了、回到竞赛页就收（不靠「用完」的提示认没认出）。要在选对手之前看，不然第 5 场刚点完就收了
        handled, go_on = pick_contestant(rt, f, ongoing)
        if not go_on:
            return True
        if handled and not ongoing:
            fights["n"] += 1
        if not handled:
            r = challenge(rt, f)
            if r == NO_MEMORY_SET:
                return r
            if not r:
                fights["idle"] += 1
                if fights["idle"] >= IDLE_TICKS:
                    return "idle"
                rt.device.tap(*SKIP_POINT)
            else:
                fights["idle"] = 0
        else:
            fights["idle"] = 0
        ongoing = False
        return None

    r = rt.drive(step, TIMEOUT, "打竞赛", every=0.5)
    if r == NO_MEMORY_SET:
        out = "回忆没编成，跳过竞赛（不自动编成）"
    elif r == "idle":
        out = f"打了 {fights['n']} 场后认不出画面，停下（约 30 秒没有可点的）"
    else:
        out = f"打了 {fights['n']} 场竞赛" if fights["n"] else "竞赛次数用完了"
    if home:
        nav.goto_home(rt)
    return out
