"""初（はじめ）特有的读数：练习课（PRACTICE）的 HUD、考试结算的过线。初 Mac 采样 11-17（.temp/hajime-nia-mac/frames）。

- 练习课 HUD：左上「残りターン数」、正中「CLEARまで / PERFECTまで N」、右上「属性图标 + 评级 + 当前属性值」。剩余回合 / 体力 / 元气 / 手牌
  执行器的 Frame 已经按 KAA 的 InLesson 框读得出（hiro.exec.exam.Frame），这里只补属性和「还在不在练习课」。
- 属性按右上角图标颜色认（用户 11-17：图标优先）：粉 = Vo、蓝 = Da、橙 = Vi，同选行动页的 ATTR_HUE。
"""

from __future__ import annotations

import cv2
import numpy as np

LESSON_ICON = (503, 40, 545, 90)  # 右上属性图标（x1, y1, x2, y2）；Vo / Vi 实测各 1,100+ 像素
LESSON_ICON_MIN = 400


def lesson_attr(img: np.ndarray) -> str | None:
    """练习课右上角属性图标 → Vo / Da / Vi；认不出 None。"""
    from hiro.vision.action import ATTR_HUE

    if img is None or img.shape[:2] != (1280, 720):
        return None
    x1, y1, x2, y2 = LESSON_ICON
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    vivid = (hsv[..., 1] > 120) & (hsv[..., 2] > 150)
    hues = hsv[..., 0][vivid]
    counts = {a: int(((hues >= lo) & (hues <= hi)).sum()) for a, (lo, hi) in ATTR_HUE.items()}
    best = max(counts, key=counts.get)
    return {"vo": "Vo", "da": "Da", "vi": "Vi"}[best] if counts[best] >= LESSON_ICON_MIN else None


def in_lesson(img: np.ndarray) -> bool:
    """画面上还有练习课的「CLEARまで / PERFECTまで」（KAA 判 PRACTICE 用的同一对模板）。"""
    from hiro.vision.ui_templates import normalize, tpl

    if img is None:
        return False
    img = normalize(img)
    return any(tpl(n).find(img) is not None for n in ("InProduce.TextClearUntil", "InProduce.TextPerfectUntil"))


def exam_rank(img: np.ndarray) -> dict | None:
    """考试结算名次页（1st–6th 名单 + 左下「再挑戦」+「次へ」，版面同 H.I.F）→ {rank, score, retry}；不是这一页 None。
    H.I.F 的 passed 按突破线判，初没有突破线，过没过由调用方按名次（modes.pass_rank，初前 3）定。"""
    from hiro.vision.hif import read_hif_result

    try:
        r = read_hif_result(img)
    except Exception:  # noqa: BLE001  识别异常当没读到
        return None
    if r.get("rank") is None:
        # 「不合格」页自己那行（4–6 位）常被底部按钮挡住、分数读不到，名次给不出（11-25 账号 3 プロ期中「6th」还读成「Oth」）：
        # 左上写着「不合格」就是没过线，照样交给调用方走再挑戦，别返回 None 让主循环去点「次へ」
        if _failed_header(img):
            return {"rank": None, "score": None, "retry": r.get("retry"), "failed": True}
        return None
    out = {"rank": r["rank"], "score": r.get("score"), "retry": r.get("retry")}
    if _failed_header(img):
        out["failed"] = True
    return out


FAILED_HEADER = (20, 120, 260, 200)  # 名次页左上「不合格」


def _failed_header(img: np.ndarray) -> bool:
    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = FAILED_HEADER
    return any("不合格" in t for t, _ in ocr(img[y1:y2, x1:x2], scale=2))  # 小块不放大 Vision 读不出


def result_passed(res: dict, need: int) -> bool:
    """名次页读数过没过线：写着「不合格」就没过；否则名次进前 need。"""
    if res.get("failed"):
        return False
    return res.get("rank") is not None and res["rank"] <= need


LESSON_LEFT_BOX = (300, 125, 420, 172)  # 正中「CLEARまで / PERFECTまで」下面的数字（帧 00330：25 在 (359,146)）
LESSON_PARAM_BOX = (560, 45, 700, 82)  # 右上「评级 + 当前属性值」（课程里加的分实时加在这里）


def lesson_goal(img: np.ndarray) -> str | None:
    """正中写的是「CLEARまで」还是「PERFECTまで」（KAA 同一对模板）；都不是 None。"""
    from hiro.vision.ui_templates import normalize, tpl

    if img is None:
        return None
    img = normalize(img)
    if tpl("InProduce.TextPerfectUntil").find(img) is not None:
        return "perfect"
    if tpl("InProduce.TextClearUntil").find(img) is not None:
        return "clear"
    return None


def _box_int(img: np.ndarray, box) -> int | None:
    import re

    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = box
    text = "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=2))
    m = re.search(r"(\d{1,4})\s*$", text)  # 属性值前面贴着评级字母（「D 204」）：取末尾的数字
    return int(m.group(1)) if m else None


LESSON_TURNS_BOX = (60, 62, 150, 118)  # 左上「残りターン数」下面的数字


def lesson_turns(img: np.ndarray) -> int | None:
    """左上剩余回合数。数字模板（hiro.exec.exam._hud_int，KAA InLesson 框）读一位数准、两位数会丢一位（追い込み 11 → 1）；
    OCR 读一位数不准（6 → 9）、两位数和「3+1」（追加回合）准。所以 OCR 给出两位数或「a+b」就用 OCR（a+b 相加），否则用模板。"""
    import re

    from hiro.exec.exam import _hud_int
    from hiro.vision import hif
    from hiro.vision.ocr import ocr
    from hiro.vision.ui_templates import box

    x1, y1, x2, y2 = LESSON_TURNS_BOX
    text = "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=2)).replace(" ", "")
    if m := re.fullmatch(r"(\d{1,2})\+(\d{1,2})", text):
        return int(m.group(1)) + int(m.group(2))
    tpl_v = _hud_int(img, hif.BOX_TURNS, box("InProduce.InLesson.BoxRemainingTurns"))
    if re.fullmatch(r"\d{2}", text):
        # 模板核对（协调 11-18：续跑时 6 回合的课读成 19）：模板两位数会丢一位，但剩下那位应该是 OCR 两位数里的一位（11 → 1）；
        # 对不上就信模板（19 vs 6 → 6）。模板读不到时照 OCR
        if tpl_v is None or str(tpl_v) in text:
            return int(text)
        return tpl_v
    return tpl_v


def lesson_turns_shown(img: np.ndarray) -> bool:
    """左上「残りターン数」的数字还读得出（只用数字模板，不走 OCR）。卡停在选中态时正中「CLEARまで」换成分数预览、
    lesson_goal 认不出，但这里还在：练习课没结束（G1-F6 11-26）。课程真结束后这里也没了（帧 00177–00199）。"""
    from hiro.exec.exam import _hud_int
    from hiro.vision import hif
    from hiro.vision.ui_templates import box

    if img is None:
        return False
    return _hud_int(img, hif.BOX_TURNS, box("InProduce.InLesson.BoxRemainingTurns")) is not None


def read_lesson_hud(img: np.ndarray) -> dict | None:
    """练习课 HUD → {goal: clear / perfect, left: 离目标还差多少, param: 当前属性值}；不在练习课 None。读不到的项给 None。
    课程分实时加在右上的属性值上：满分线 = (当前属性值 − 开课时属性值) + PERFECTまで 的数（帧 00512：204 − 155 + 1 = 50）。"""
    goal = lesson_goal(img)
    if goal is None:
        return None
    return {"goal": goal, "left": _box_int(img, LESSON_LEFT_BOX), "param": _param(img)}


def _param(img: np.ndarray) -> int | None:
    """右上属性值。追い込み课过了 CLEAR 以后这里换成「追い込みボーナス N」（帧 02290），那时不是属性值：给 None。"""
    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = LESSON_PARAM_BOX
    text = "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=2))
    if any(w in text for w in ("込み", "ナス", "ボーナス")):
        return None
    return _box_int(img, LESSON_PARAM_BOX)
