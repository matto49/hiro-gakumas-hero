"""判页（去 KAA 第 1 步）：classify(img) -> PageHit，纯函数，同一帧永远同样结果，不点、不读设备，可离线回放。

顺序和 hiro.vision.scene.classify_current（旧主循环用，带 KAA）一样，只是 KAA 的四样换成了自己的：
- KAA ProducePage.check_scene → template_scene（同一批模板，搬进 assets/pack/ui，见 tools/assets/ui_from_kaa.py）；
  KAA 判页时会顺手点（引导框点 ✓、开局二选一双击第一项），这里只判不点：引导框判 IDLE，二选一判 INITIAL_CHOICE。
- KAA at_home → at_home（首页按钮高亮模板）；KAA is_at_commu → at_commu（SKIP 按钮，阈值 0.6，原图 + 白色过滤各找一次）；
- KAA CardSelectContext.fetch_cards（选卡弹窗认不认得出卡）→ has_card_letters（卡类型角标 A / M）；
- KAA CommuEventButtonUI（授業 / おでかけ 有几个选项）→ option_buttons（同一套像素做法）。

source 取值沿用 PageObs.source：own 判出的页面记 template（这一层）/ signature / exam_ocr / pitem_ocr / … （见 _SOURCE）。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from hiro.vision.ui_templates import PACK, box, tpl


@dataclass(frozen=True)
class PageHit:
    """判页结果：name 是页面名（状态机 / Session 用的名字），area produce / outside / unknown，source 是判出它的那一层。"""

    name: str
    source: str | None = None
    area: str = "produce"
    fields: dict | None = None


UNKNOWN = PageHit("unknown", "none", "unknown")


# ---------------------------------------------------------------- KAA 那几样的自有实现
def loading(img) -> bool:
    """加载画面：上方 35% 二值化后只剩不超过 2 种颜色（同 KAA tasks.actions.loading）。"""
    top = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)[1][: int(img.shape[0] * 0.35)]
    b, g, r = cv2.split(top)
    return len(np.unique(b.astype(np.int64) + 1000 * (g.astype(np.int64) + 1) + 1000 * 1000 * (r.astype(np.int64) + 1))) <= 2


def at_home(img) -> bool:
    return tpl("Daily.ButtonHomeCurrent").exists(img)


WHITE_LOW, WHITE_HIGH = np.array((0, 0, 200)), np.array((180, 30, 255))  # 同 KAA game_ui.WhiteFilter


def _white(img) -> np.ndarray:
    return cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), WHITE_LOW, WHITE_HIGH)


def at_commu(img) -> bool:
    """剧情 / 交流页：SKIP 按钮（同 KAA is_at_commu：阈值 0.6 原图找，找不到再两边都只留白色部分找）。"""
    t = tpl("Common.ButtonCommuSkip")
    if t.exists(img, threshold=0.6):
        return True
    res = cv2.matchTemplate(_white(img), _white(cv2.imread(str(PACK / t.file))), cv2.TM_CCOEFF_NORMED)
    return float(res.max()) >= 0.6


def has_card_letters(img) -> bool:
    """选卡弹窗里有没有卡（卡类型角标 A / M，阈值 0.65，只在卡区找；同 KAA SkillCardSelect.fetch_cards 的第一步）。"""
    region = box("InProduce.SkillCardSelect.CardsBox").xywh
    return any(tpl(n).exists(img, threshold=0.65, region=region) for n in ("InProduce.A", "InProduce.M"))


def option_buttons(img) -> int:
    """授業 / おでかけ / 开局二选一的选项按钮数。"""
    return len(option_regions(img))


def option_regions(img) -> list:
    """授業 / おでかけ / 开局二选一的选项按钮框（整帧坐标的 Box，从上到下；同 KAA CommuEventButtonUI.all 的按钮判定，
    有选中项时最上面那块是描述、不算按钮）。"""
    from hiro.vision.ocr import Box

    x1, y1, x2, y2 = box("InProduce.BoxCommuEventButtonsArea")
    crop = img[y1:y2, x1:x2]
    edges = cv2.Canny(cv2.threshold(crop, 190, 255, cv2.THRESH_BINARY)[1], 200, 250)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.dilate(cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel), kernel, iterations=1)
    regions = []
    for c in cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        x, y, w, h = cv2.boundingRect(c)
        if not (350 < w < 660 and h > 60):
            continue
        is_button = 60 < h < 100
        white = (cv2.threshold(cv2.cvtColor(crop[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY), 190, 255, cv2.THRESH_BINARY)[1] == 255).mean()
        regions.append([y, is_button, is_button and white < 0.5, Box(x1 + x, y1 + y, x1 + x + w, y1 + y + h)])
    regions.sort(key=lambda r: r[0])
    if regions and any(r[2] for r in regions):
        regions[0][1] = False  # 有选中项时，最上面那块是描述（同 KAA）
    return [r[3] for r in regions if r[1]]


def _outing_options(img) -> int:
    """外出页的选项数：亮按钮（option_regions）+ 灰色选不了的（P 不够；初 Mac 11-17 外出三档两档是灰的，原来只数到 1 个判 IDLE，180 秒停）。"""
    from hiro.vision.event import grey_rows

    return option_buttons(img) + len(grey_rows(img))


# ---------------------------------------------------------------- KAA check_scene 的同一套模板判据（只判不点）
def _any(img, *names) -> bool:
    return any(tpl(n).exists(img) for n in names)


SELECT_TITLE_BOX = (230, 590, 370, 670)  # 三选一弹窗标题「スキルカード / Pアイテム / Pドリンク」的位置（KAA 模板原位 (247,613) 107x32）


def _title(img, name: str) -> bool:
    """三选一弹窗标题模板只认原位：支援能力横幅「Pアイテム獲得時、…」（y≈460）也会命中同一行字（初 PRO 11-18：中間后判成
    SELECT_PITEM，读不到候选重读 3 次停机）。"""
    m = tpl(name).find(img)
    if m is None:
        return False
    x1, y1, x2, y2 = SELECT_TITLE_BOX
    return x1 <= m.rect.center.x <= x2 and y1 <= m.rect.center.y <= y2


def template_scene(img) -> str | None:
    """KAA ProducePage.check_scene 的顺序和模板；返回 SceneType 名或 None。"""
    if loading(img):
        return "LOADING"
    if _any(img, "InProduce.TextOutingStaminaMax"):
        return "OUTING_STAMINA_MAX"
    if _any(img, "InProduce.TextPDrinkMax"):
        return "PDRINK_MAX"
    if _any(img, "InProduce.TextPDrinkMaxConfirmTitle"):
        return "PDRINK_MAX_CONFIRM"
    if _any(img, "InProduce.TextSkillCardSelectGuideDialogTitle"):
        return "IDLE"  # 引导框：KAA 判页时点 ✓，这里只判（中断层 / 处理器去点）
    if _any(img, "InProduce.ProduceScore.TitleText", "InProduce.End.ButtonGenerate"):
        return "PRODUCE_END"
    if _title(img, "InProduce.TextSkillCard"):
        return "SELECT_CARD"
    if _title(img, "InProduce.TextPItem"):
        return "SELECT_PITEM"
    if _title(img, "InProduce.TextPDrink"):
        return "SELECT_DRINK"
    if _any(img, "InProduce.IconTitleSkillCardEnhance"):
        return "SKILL_CARD_ENHANCE"
    if _any(img, "InProduce.IconTitleSkillCardRemoval"):
        return "SKILL_CARD_REMOVAL"
    if _any(img, "InProduce.DialogCardChange1.TipText"):
        return "SKILL_CARD_CHANGE_1"
    if _any(img, "InProduce.DialogCardChange2.Title"):
        return "SKILL_CARD_CHANGE_2"
    if _any(img, "InProduce.TextReviewCriteria"):
        if _any(img, "InProduce.TextPDiary", "InProduce.ButtonFinalPracticeVisual"):
            return "ACTION_SELECT"
        if _any(img, "InProduce.IconTitleStudy"):
            return "STUDY" if option_buttons(img) > 1 else "IDLE"
        if _any(img, "InProduce.TitleIconOuting"):
            return "OUTING" if _outing_options(img) > 1 else "IDLE"
        if _any(img, "InProduce.IconTitleConsult"):
            return "CONSULT"
        if _any(img, "InProduce.IconTitleAllowance"):
            return "ALLOWANCE"
        if _any(img, "InProduce.HifInterval.Title"):
            return "HIF_ROUND_INTERVAL"
        if option_buttons(img) > 1:
            return "INITIAL_CHOICE"  # 开局饮料 / 卡二选一：KAA 双击第一项，这里交给决策层
    elif _any(img, "InProduce.IconTitleConsult"):
        # N.I.A 右上不是「審査基準」（是投票数面板），上面这组按标题图标认的页一个都进不来：N.I.A 采样 11-17 相談页全判 unknown，
        # 支援能力横幅盖住时签名也认不出。相談标题图标只在培育的相談页出现，单独认
        return "CONSULT"
    elif _any(img, "InProduce.TitleIconOuting"):
        return "OUTING" if _outing_options(img) > 1 else "IDLE"
    if _any(img, "InProduce.TextClearUntil", "InProduce.TextPerfectUntil"):
        return "PRACTICE"
    if _any(img, "InProduce.TextExamRankSmallFirst", "InProduce.TextExamRankLargeFirst"):
        return "EXAM"
    return None


# ---------------------------------------------------------------- 整条判页链
# 授業（STUDY）与おでかけ（OUTING）是同一套选项事件界面，签名分不开，交给模板按标题图标区分
SIGNATURE_DEFER = {"OUTING", "STUDY"}
# 三选一的选卡 / 选饮料弹窗布局相同（受け取る、カード除去 椭圆按钮），SELECT_DRINK 签名是这些通用按钮，
# 选卡页会以 2/3 票判成 SELECT_DRINK（第四局 r3/r4 实机 3 帧）；而饮料满、已选中一瓶的差し入れ页模板认不出，只能靠签名。
# 所以签名给出这两页时，再按提示文字 / 卡角标定（_card_or_drink）
CARD_OR_DRINK = {"SELECT_CARD", "SELECT_DRINK"}
ACQUIRE_OVER = {"CONSULT"}  # 模板判出这些页面时，先看是否被获得演出盖住
DRINK_DIALOG_MIN_SCORE = 0.6  # 饮料弹窗三格最高分实测 0.74；选卡弹窗用不到这条（先按卡判）


def card_or_drink(img) -> str:
    """选卡 / 选饮料 / 换卡第 1 步弹窗（签名分不开）：提示文字「受け取るスキルカード / Pドリンクを選んでください」优先；
    选中一项后提示被详情替换时，看卡区有没有卡类型角标：有就是选卡，否则是选饮料。"""
    if _any(img, "InProduce.DialogCardChange1.TipText"):
        return "SKILL_CARD_CHANGE_1"  # 换卡第 1 步「チェンジするスキルカードを選択してください」，布局同三选一
    if _any(img, "InProduce.TextSkillCard"):
        return "SELECT_CARD"
    if _any(img, "InProduce.TextPDrink"):
        return "SELECT_DRINK"
    return "SELECT_CARD" if has_card_letters(img) else "SELECT_DRINK"


def selecting_dialog(img) -> str | None:
    """三选一弹窗里已选中某一项时，顶部提示换成了该项详情，模板判不出 SELECT_CARD / SELECT_DRINK（落到 IDLE / None）。
    底部「受け取る」按钮选中前后都在（全数据集只在这类弹窗出现），据此补判；卡还是饮料看卡角标 / 饮料图标分数。"""
    from hiro.vision.drinks import read_drink_dialog

    if not _any(img, "InProduce.AcquireBtnDisabled"):
        return None
    if has_card_letters(img):
        return "SELECT_CARD"
    if max(d["score"] for d in read_drink_dialog(img)) >= DRINK_DIALOG_MIN_SCORE:
        return "SELECT_DRINK"
    return None


def pixel_fallback(img) -> str | None:
    """模板判不出时的像素兜底。界面 Sprite（页面签名）是自建素材，开源默认没有：签名本来能认的几页在这里补上。
    - quick.fallback_page：选行动页（「P手帳」标签）、Pドリンク所持上限 列表；
    - 选饮料页饮料已满且选中了一瓶（提示文字被详情替换）：「Pドリンク所持上限」红标。"""
    from hiro.vision.items import drink_full_badge
    from hiro.vision.quick import fallback_page

    if img is None or img.shape[0] < img.shape[1]:
        return None  # 横屏（LIVE / 游戏被杀后的安卓桌面）：这些兜底都按竖屏坐标裁图，会裁出空图（GKMS 第五局 cvtColor 断言）
    fb = fallback_page(img)
    if fb is not None:
        return fb
    return "SELECT_DRINK" if drink_full_badge(img) else None


def classify_scene(img) -> PageHit:
    """不含快速判页的整条判页链（和旧的 scene.classify_current 同一顺序，KAA 部分换成自己的）。"""
    from hiro.vision.deck import is_deck_list
    from hiro.vision.hif import is_hif_exam, is_hif_result
    from hiro.vision.pages import acquire_over, classify_page
    from hiro.vision.pitem_select import is_select_pitem
    from hiro.vision.signature import match as signature_match
    from hiro.vision.support import is_start_confirm

    if img is None:
        return UNKNOWN
    if img.shape[0] < img.shape[1]:
        return PageHit("LIVE", "landscape")  # 横屏（LIVE 演出，或游戏被杀后的安卓桌面）：竖屏判据都不适用
    if is_hif_exam(img):
        return PageHit("HIF_EXAM", "exam_ocr")
    from hiro.vision.tower import is_tower_exam

    if is_tower_exam(img):
        return PageHit("TOWER_EXAM", "tower_ocr")  # 偶像之路考试页：先于 KAA 的 EXAM 模板（否则会当成 H.I.F 考试进 run_exam 的 H.I.F 流程）
    if is_hif_result(img):
        return PageHit("HIF_EXAM_RESULT", "exam_ocr")
    if is_select_pitem(img):
        return PageHit("SELECT_PITEM", "pitem_ocr")
    if is_start_confirm(img):
        return PageHit("PRODUCE_START_CONFIRM", "start_ocr", "outside")
    if is_deck_list(img):
        return PageHit("DECK_LIST", "deck_ocr")
    fast, _ = signature_match(img)
    if fast in CARD_OR_DRINK:
        fast = card_or_drink(img)
    if fast is not None and fast not in SIGNATURE_DEFER:
        return PageHit(fast, "signature")
    if at_home(img):
        return PageHit("home", "template", "outside")
    scene = template_scene(img)
    if scene == "SELECT_PITEM" and (hit := classify_page(img)) is not None and hit[0] == "PRODUCE_LOG":
        return PageHit("PRODUCE_LOG", "page_ocr")  # H.I.F 的 P 道具选择页上面已判过；到这里多半是行动记录（「Pアイテム…を獲得」）
    if scene in (None, "IDLE"):
        if (selecting := selecting_dialog(img)) is not None:
            return PageHit(selecting, "pixel")
        if (hit := classify_page(img)) is not None:  # 模板认不出的 H.I.F 页面（弹窗、获得演出、考前说明、定制、菜单…）
            return PageHit(hit[0], "page_ocr", "outside" if hit[0] == "MENU" and hit[1].get("kind") == "home" else "produce", hit[1])
    if scene in ACQUIRE_OVER and acquire_over(img) is not None:
        return PageHit("ACQUIRE", "page_ocr")  # 相談购买后的获得演出盖在货架上，模板仍判 CONSULT
    if scene is not None:
        return PageHit(scene, "template")
    if (fb := pixel_fallback(img)) is not None:
        return PageHit(fb, "pixel")
    if at_commu(img):
        return PageHit("commu", "template", "outside")
    return UNKNOWN


def classify(img) -> PageHit:
    """快速判页（hiro.vision.quick：模板 / 像素 / 局部 OCR，≤0.3 s，带字段）→ classify_scene。"""
    from hiro.vision.quick import quick_page

    if img is not None and img.shape[0] > img.shape[1] and (q := quick_page(img)) is not None:
        return PageHit(q["page"], "quick", fields={**q.get("fields", {}), "_quick": q})
    return classify_scene(img)
