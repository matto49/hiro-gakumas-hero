# 来源：digital-hiro src/hiro/vision/extract.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""按场景提取决策用字段。新增场景的字段读取在这里登记。"""

import re
import time

from cv2.typing import MatLike

from hiro.perception.lesson import read_lesson
from hiro.vision.action import read_action_select
from hiro.vision.cards import card_summary, dialog_card_upgraded
from hiro.vision.deck import read_card_dialog, read_confirm_dialog, read_deck_list
from hiro.vision.drinks import read_drink_dialog
from hiro.vision.event import read_event_choices
from hiro.vision.items import read_pdrink_max
from hiro.vision.pages import page_fields
from hiro.vision.hif import read_hif_exam, read_hif_result
from hiro.vision.ocr import ocr
from hiro.vision.pitem_select import read_pitem_select
from hiro.vision.shop import read_consult
from hiro.vision.support import read_supports


def _lesson(img: MatLike) -> dict:
    return read_lesson(img).to_dict()


# 三选一弹窗底部按钮上方的剩余次数角标「あと N回」：左为カード除去，右为再抽選（720x1280）
DIALOG_COUNTERS = {"excludes_left": (40, 1038, 230, 1078), "rerolls_left": (500, 1038, 700, 1078)}


# 角标下方的按钮：次数用完时游戏把角标和按钮一起拿掉（第六局 trace 00043「カード除去」、00078 起「再抽選」消失）
DIALOG_BUTTONS = {"excludes_left": ((40, 1070, 240, 1120), "除去"), "rerolls_left": ((490, 1070, 700, 1120), "抽選")}


def _dialog_counter(img: MatLike, box, button: tuple | None = None) -> int | None:
    """「あと N回」角标 → N。角标读不到时看按钮：按钮也不在 → 0（次数用完）；按钮还在、角标读不出 → None（不猜）。
    以前一律给 None，reduce 保留旧值，决策器以为还剩 1 次（第六局 23:29 连刷三次「还剩 1」、23:27:44 点灰掉的カード除去）。"""
    x1, y1, x2, y2 = box
    crop = img[y1:y2, x1:x2]
    # 默认放大读不出数字时换 1 / 2 倍再读（RapidOCR 3 倍下「あと1回」→「あと回」，q6-00040）
    for scale in (None, 1, 2):
        m = re.search(r"あと\s*(\d+)\s*回", "".join(t for t, _ in (ocr(crop) if scale is None else ocr(crop, scale=scale))))
        if m:
            return int(m.group(1))
    if button is not None:
        (bx1, by1, bx2, by2), word = button
        if word not in "".join(t for t, _ in ocr(img[by1:by2, bx1:bx2])):
            return 0
    return None


def _select_card(img: MatLike) -> dict:
    from kaa.game_ui import badge
    from kaa.tasks import R
    from kaa.tasks.produce.new.page import CardSelectContext, ProducePage

    ctx = CardSelectContext(ProducePage(), None)
    cards = ctx.fetch_cards()
    # 「おすすめ」可以同时挂在多张卡下；按 KAA fetch_recommend_card 的规则配对，recommended 取第一张（与 KAA 一致）
    badges = [b.rect for b in R.InProduce.TextRecommend.find_all()] if cards else []
    flags = [m.badge is not None for m in badge.match([c.rect for c in cards], badges, "mb")] if badges else [False] * len(cards)
    options = []
    for i, o in enumerate(cards):
        upgraded, red = dialog_card_upgraded(img, o.rect)
        options.append({"slot": i, "box": list(o.rect.xywh), "upgrade_red_px": red, "recommended": flags[i],
                        **(card_summary(o.card, upgraded=upgraded) or {})})  # fmt: skip
    out = {"options": options, "recommended": next((i for i, f in enumerate(flags) if f), None)}
    if cards:
        out.update({k: _dialog_counter(img, box, DIALOG_BUTTONS[k]) for k, box in DIALOG_COUNTERS.items()})
    return out


def _action_select(img: MatLike) -> dict:
    from hiro.vision.items import read_bar_drinks

    return {**read_action_select(img).to_dict(), **read_bar_drinks(img)}


def _consult(img: MatLike) -> dict:
    from hiro.vision.items import read_bar_drinks
    from hiro.vision.pages import read_hud_resources

    # D43：相談页头部的体力 / P 点与选行动同一位置；续跑落在相談时 state.produce 没有 P，决策只能不买
    return {**read_consult(img), **read_bar_drinks(img), **read_hud_resources(img)}


def _select_drink(img: MatLike) -> dict:
    from hiro.vision.items import read_held_drinks

    return {"options": read_drink_dialog(img), **read_held_drinks(img)}


def _card_dialog(img: MatLike) -> dict:
    """删除 / 换卡第 2 步：卡格、选中项，以及盖在上面的确认弹窗（「スキルカード削除」等）。"""
    return {**read_card_dialog(img), "confirm": read_confirm_dialog(img)}


CARD_DIALOG_BUTTON_ROW = (1100, 1200)  # 删卡 / 强化对话框底部按钮行（720x1280）


def dialog_cancelable(img: MatLike) -> bool | None:
    """删卡 / 强化对话框能不能取消（D42）：底部有「キャンセル」（x≈160，右边是 x≈485 的「強化 / 削除」）→ True；
    只有居中的「強化 / 削除」（x≈350，P 道具等触发的必选强化 / 删除，第三十三局本战 #70 #81）→ False；两样都没读到 → None。"""
    from hiro.vision.ocr import ocr

    texts = [t.replace(" ", "") for t, _ in ocr(img[CARD_DIALOG_BUTTON_ROW[0] : CARD_DIALOG_BUTTON_ROW[1]], scale=2)]
    if any("キャンセル" in t for t in texts):
        return True
    if any("強化" in t or "削除" in t for t in texts):
        return False
    return None


def _removal_dialog(img: MatLike) -> dict:
    return {**_card_dialog(img), "cancelable": dialog_cancelable(img)}


from hiro.vision.deck import ENHANCE_GRID  # noqa: E402  强化页上方有两个详情框，卡格从 y≈698 起（删卡页 398 起）


def _enhance_dialog(img: MatLike) -> dict:
    """强化：卡格区域与删卡页不同（KAA 参考图 screenshot_skill_card_enhance_dialog*.png，12 格都在 ENHANCE_GRID 内）。"""
    from hiro.vision.ocr import ocr

    out = {**read_card_dialog(img, ENHANCE_GRID), "confirm": read_confirm_dialog(img), "cancelable": dialog_cancelable(img)}
    # 下方「强化后」详情框：选了满级的卡显示「強化できません」，底部提示「これ以上強化不可能なスキルカードです」（main10 相談強化）
    x1, y1, x2, y2 = ENHANCE_RESULT_BOX
    text = "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=2)).replace(" ", "")
    if "強化できません" in text or "強化不可能" in text:
        out["upgradable"] = False
    elif out.get("selected") is not None or out.get("selected_card"):
        out["upgradable"] = True
    else:
        out["upgradable"] = None
    return out


ENHANCE_RESULT_BOX = (40, 470, 700, 680)


EXTRACTORS = {
    "PRACTICE": _lesson,
    "EXAM": _lesson,
    "SELECT_CARD": _select_card,
    "SELECT_DRINK": _select_drink,
    "HIF_EXAM": read_hif_exam,
    "HIF_EXAM_RESULT": read_hif_result,
    "SELECT_PITEM": read_pitem_select,
    "PRODUCE_START_CONFIRM": read_supports,
    "CONSULT": _consult,
    "STUDY": read_event_choices,
    "OUTING": read_event_choices,
    "ACTION_SELECT": _action_select,
    "DECK_LIST": read_deck_list,
    "SKILL_CARD_REMOVAL": _removal_dialog,
    "SKILL_CARD_ENHANCE": _enhance_dialog,
    "SKILL_CARD_CHANGE_1": lambda img: __import__("hiro.vision.deck", fromlist=["read_change_candidates"]).read_change_candidates(img),
    "SKILL_CARD_CHANGE_2": _card_dialog,
    "SPECIAL_GUIDANCE": lambda img: __import__("hiro.vision.pages", fromlist=["page_fields"]).page_fields("SPECIAL_GUIDANCE", img),
    "HIF_ROUND_INTERVAL": lambda img: __import__("hiro.vision.pages", fromlist=["read_interval"]).read_interval(img),
    "PDRINK_MAX": lambda img: read_pdrink_max(img) or {"error": "没有读到「手持ちのドリンク」"},
    **{page: (lambda img, page=page: page_fields(page, img))
       for page in ("DIALOG", "ACQUIRE", "STAR_GAIN", "HIF_EXAM_BRIEFING", "PITEM_CUSTOMIZE", "PITEM_CUSTOMIZE_DONE",
                    "MENU", "UPGRADE_RESULT", "TRANSITION", "GIFT", "PRODUCE_LOG", "CUSTOMIZE_RESULT", "HIF_FINALS_RANKING", "HIF_FINALS_START")},
}


def extract(scene: str, img: MatLike) -> tuple[dict | None, float]:
    fn = EXTRACTORS.get(scene)
    if fn is None:
        return None, 0.0
    start = time.perf_counter()
    try:
        fields = fn(img)
    except Exception as e:
        fields = {"error": repr(e)}
    return fields, (time.perf_counter() - start) * 1000
