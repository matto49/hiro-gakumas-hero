# 来源：digital-hiro src/hiro/vision/pages.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""H.I.F 培育里 KAA 认不出的页面（页面地图 docs/pages-hif.md）。

只在 KAA 给 None / IDLE 时调用（hiro.vision.scene），所以不影响已有页面的速度。顺序：
1. 弹窗（DIALOG）：橙黄渐变标题条（numpy 找行带，约 3 ms）+ 标题 OCR → kind。
2. 其余页面：整帧 OCR 一次（约 0.3–0.8 s，只发生在认不出的帧上），按文字锚点判定。
页面名与字段见 docs/pages-hif.md；过渡帧（倒计时、考试标题卡、课程结果动画等）统一给 TRANSITION（kind 区分），应当按转场处理。
"""

import re
from functools import cache

import cv2
import numpy as np
from cv2.typing import MatLike

# ---------------------------------------------------------------- 弹窗
HEADER_X = (30, 690)
HEADER_MIN_FRAC = 0.55  # 行内橙黄像素占比（标题条 0.67–0.76，其余画面没有连续 25 行以上达到）
HEADER_H = (25, 70)
HEADER_KEEP_FRAC = 0.45  # 标题条开始要 ≥ HEADER_MIN_FRAC，之后 ≥ 这个值就算同一条（滞回）：
# 「プロデュース再開」标题条右侧有白色「リタイア」按钮，几行橙色只占 0.54，原来标题条被切断、整个框认不出（第二十局 r1 #207）
TITLE_WHITE_MIN = 0.03  # 标题条左段白字像素占比：弹窗 0.046–0.126；获得演出名称栏、橙色背景 ≤ 0.009
# 标题关键字 → kind（按顺序匹配；OCR 偶有错字，用关键字而不是全文）
DIALOG_KINDS = (
    ("ターン終了", "turn_end"),  # 考试：还有可打的牌时跳过回合
    ("使用確認", "drink_use_confirm"),  # 考试：Pドリンク使用確認（效果对象不存在）
    ("ドリンク詳細", "drink_detail"),  # 考试：点饮料后的详情（使う / 捨てる）
    ("相談終了", "consult_end"),
    ("カスタマイズ確認", "customize_confirm"),
    ("カスタマイズを実行", "customize_confirm"),
    ("特別指導終了", "guidance_end"),  # 还有可定制次数时点「終了」：「特別指導終了確認」→「終了する」(500,1160)
    ("カスタマイズ", "customize_confirm"),  # 特別指導「カスタマイズ実行確認」（OCR 常把「実」读成「美」）→「実行する」(500,1160)
    ("交換確認", "exchange"),  # 相談购买确认（hiro.vision.deck.read_confirm_dialog）
    ("削除", "delete"),  # スキルカード削除确认
    ("リタイア", "retire"),  # プロデュース・リタイア確認：绝不能点确认
    ("破棄", "data_discard"),  # プロデュースデータの破棄（开局时其他端末有进行中的培育）：绝不能点确认
    ("所持上限", "pdrink_max"),
    ("報酬スキップ", "reward_skip"),
    ("リフレッシュ", "refresh_confirm"),
    ("通信エラー", "comm_error"),  # 「通信エラー」：confirm = read_comm_error（リトライ可选 / 时间太久只能閉じる回标题）
    ("通信工ラー", "comm_error"),  # OCR 常把「エ」读成「工」
    ("再挑戦", "retry_confirm"),  # 本战敗退后「再挑戦確認」：confirm = read_retry_confirm（免费 / 券 / ジュエル），决定 / キャンセル
    ("再開", "produce_resume"),  # 首页「プロデュース再開」：标题条右侧有「リタイア」按钮，不算危险框（点「再開する」）  # 轮间 / 相談刷新货架「リフレッシュ確認」（P 10 → 更新する；confirm 见 read_refresh_confirm）
)

DANGER_KINDS = {"retire", "data_discard"}
CONFIRM_DIALOG_KINDS = {"exchange", "delete"}  # 这两种弹窗另读 confirm（hiro.vision.deck.read_confirm_dialog）
REFRESH_CANCEL, REFRESH_OK = (215, 1160), (505, 1160)  # 「リフレッシュ確認」按钮（第十八局本战轮间实测）；OCR 读到时用读到的位置
DANGER_WORDS = ("リタイア", "破棄")
DIALOG_BODY_KINDS = (("リタイア", "retire"), ("破棄", "data_discard"), ("削除しますか", "delete"), ("交換しますか", "exchange"))


def _bands(frac: np.ndarray, keep: float) -> list[tuple[int, int]]:
    out, y = [], 0
    while y < len(frac):
        if frac[y] >= HEADER_MIN_FRAC:
            start = y
            while y < len(frac) and frac[y] >= keep:
                y += 1
            if HEADER_H[0] <= y - start <= HEADER_H[1]:
                out.append((start, y))
        y += 1
    return out


def _header_bands(img: MatLike) -> list[tuple[int, int]]:
    hsv = cv2.cvtColor(img[:, HEADER_X[0] : HEADER_X[1]], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    frac = ((h >= 8) & (h <= 30) & (s >= 110) & (v >= 190)).mean(axis=1)
    out = _bands(frac, HEADER_MIN_FRAC)
    if not any(_title_white(img, y1, y2) for y1, y2 in out):
        # 一条带白字标题的都没有时才放宽（滞回）：开始仍要 ≥ HEADER_MIN_FRAC，之后 ≥ HEADER_KEEP_FRAC 就算同一条。
        # 原算法认得出的框结果不变（全帧放宽会把标题条和下方橙色区域连成一片、超高被丢，61 帧回退）
        out = [(y1, y2) for y1, y2 in _bands(frac, HEADER_KEEP_FRAC) if _white_body(img, y2)]  # 放宽后还要求下方是白色框体（砖墙背景的误检没有）
    return out


def _white_body(img: MatLike, y2: int) -> bool:
    """标题条正下方是确认框的白色正文：y2+10 起 50 行、x 60–660 里近白像素占比 ≥ 0.6。"""
    crop = img[y2 + 10 : min(img.shape[0], y2 + 60), 60:660]
    if crop.shape[0] < 10:
        return False  # 标题条贴着画面底边，下面没有框体
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 225)).mean()) >= 0.6


def _title_white(img: MatLike, y1: int, y2: int) -> bool:
    hsv = cv2.cvtColor(img[y1:y2, 40:320], cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 235)).mean() >= TITLE_WHITE_MIN


def dialog_header(img: MatLike) -> tuple[int, int] | None:
    """只用像素判断有没有弹窗（约 20ms，不做 OCR）：最下面一条带白色标题字的橙黄标题条 (y1, y2)，没有返回 None。
    与 read_dialog 同一判据；考试执行用它决定要不要 OCR 弹窗区。"""
    return next(((y1, y2) for y1, y2 in reversed(_header_bands(img)) if _title_white(img, y1, y2)), None)


def read_dialog(img: MatLike) -> dict | None:
    """橙黄标题条的弹窗 → {kind, title, danger, y}。danger：リタイア / 培育数据破棄 这类绝不能确认的弹窗（标题或正文含关键字）。有多个（详情框上叠确认框）时取最下面那个（后弹出的在上层、位置靠下）。
    标题认不出关键字时 kind="other"、title 为 OCR 原文；没有标题条返回 None。"""
    from hiro.vision.ocr import ocr

    for y1, y2 in reversed(_header_bands(img)):
        if not _title_white(img, y1, y2):
            continue  # 没有白色标题字：获得演出的金色名称栏、橙色背景的场景
        title = "".join(t for t, _ in ocr(img[max(0, y1 - 4) : y2 + 4, 20:560])).replace(" ", "")
        if not title:
            continue
        kind = next((k for key, k in DIALOG_KINDS if key in title), "other")
        # 正文总要读：标题 OCR 偶尔乱码（实测「プロデュース・リタイア確認」→「ーメリダイノ唯談」），
        # 危险弹窗不能只靠标题。danger=True 时执行侧一律只点キャンセル（不依赖 kind 判没判对）
        body = "".join(t for t, _ in ocr(img[y2 : min(img.shape[0], y2 + 450), 20:700])).replace(" ", "")
        if kind == "other":
            kind = next((k for key, k in DIALOG_BODY_KINDS if key in body), "other")
        danger = kind in DANGER_KINDS or (kind != "produce_resume" and any(w in title or w in body for w in DANGER_WORDS))
        return {"kind": kind, "title": title, "danger": danger, "y": int((y1 + y2) // 2)}
    return None


# ---------------------------------------------------------------- 整帧文字锚点
def _texts(img: MatLike) -> list[tuple[str, tuple[int, int, int, int]]]:
    from hiro.vision.ocr import ocr_boxes

    return [(t.replace(" ", ""), b) for t, _, b in ocr_boxes(img)]


def _has(boxes, word: str, y_range: tuple[int, int] = (0, 1280)) -> tuple | None:
    from hiro.vision.ocr import engine

    loose = len(word) >= 4 and engine() != "vision"  # RapidOCR 等常错一个字（「スター性獲得」→「ネター性獲得」）
    if loose:
        # 长音「ー」常读成「一」，再错一个字就超出容错（DMM qualifydmm1-00340「ス夕一性獲得」）：先统一长音再比
        word = word.translate(_DASHES)
        boxes = [(t.translate(_DASHES), b) for t, b in boxes]
    return next((b for t, b in boxes if (word in t or (loose and _contains_near(t, word))) and y_range[0] <= (b[1] + b[3]) // 2 <= y_range[1]), None)


_DASHES = str.maketrans({"一": "ー", "－": "ー", "-": "ー", "―": "ー", "ｰ": "ー"})


def _contains_near(text: str, word: str) -> bool:
    """text 里有一段与 word 等长、只差一个字；≥6 字的 word 掉了任意一个字也算
    （RapidOCR「カスタマイズメニュ」丢了末尾长音，「ス一性獲得」丢了中间的「タ」）。"""
    n = len(word)
    if n >= 6 and any(word[:i] + word[i + 1 :] in text for i in range(n)):
        return True
    return any(sum(a != b for a, b in zip(text[i : i + n], word, strict=True)) <= 1 for i in range(len(text) - n + 1))


_last: tuple[bytes, tuple[str, dict] | None] | None = None


def classify_page(img: MatLike) -> tuple[str, dict] | None:
    """KAA 认不出的帧 → (页面名, 识别字段) 或 None。同一帧（缩略图相同）连续调用直接复用（scene 判页后 extract 再取字段）。"""
    global _last
    key = cv2.resize(img, (45, 80), interpolation=cv2.INTER_AREA).tobytes()
    if _last is not None and _last[0] == key:
        return _last[1]
    result = _classify_page(img)
    _last = (key, result)
    return result


def acquire_over(img: MatLike) -> dict | None:
    """KAA 已判成某页（相談）但上面盖着获得演出时用：只读名称栏（约 80 ms），不做整帧 OCR。"""
    from hiro.vision.deck import read_acquire_popup

    return read_acquire_popup(img)


def page_fields(page: str, img: MatLike) -> dict:
    """extract 用：本模块页面的识别字段（与 classify_page 判出的同一页面才有）。"""
    if page == "ACQUIRE":
        acquired = acquire_over(img)
        if acquired is not None:
            return acquired
    hit = classify_page(img)
    if hit is None or hit[0] != page:
        return {"error": f"画面不再是 {page}"}
    if page == "DIALOG" and hit[1].get("kind") in CONFIRM_DIALOG_KINDS:
        from hiro.vision.deck import read_confirm_dialog

        return {**hit[1], "confirm": read_confirm_dialog(img)}  # 交換確認 / 削除确认：买 / 删的是哪件、价格（事件 SHOP_BUY / CARD_DELETE）
    if page == "DIALOG" and hit[1].get("kind") == "comm_error":
        return {**hit[1], "confirm": read_comm_error(img, hit[1].get("y"))}  # 通信エラー：按钮位置 / 能不能リトライ
    if page == "DIALOG" and hit[1].get("kind") == "retry_confirm":
        return {**hit[1], "confirm": read_retry_confirm(img, hit[1].get("y"))}  # 再挑戦確認：免费 / 券 / ジュエル / 剩余次数
    if page == "DIALOG" and hit[1].get("kind") == "refresh_confirm":
        return {**hit[1], "confirm": read_refresh_confirm(img, hit[1].get("y"))}  # 刷新确认：P 前后 / ジュエル / 按钮
    return hit[1]


def _classify_page(img: MatLike) -> tuple[str, dict] | None:
    dialog = read_dialog(img)
    if dialog is not None:
        return "DIALOG", dialog
    boxes = _texts(img)
    texts = [t for t, _ in boxes]

    # 获得演出：名称栏居中、精确命中卡 / 饮料 / P 道具表（hiro.vision.deck.read_acquire_popup 同一规则）
    from hiro.vision.deck import ACQUIRE_BAR, read_acquire_popup

    if any(ACQUIRE_BAR[1] <= (b[1] + b[3]) // 2 <= ACQUIRE_BAR[3] for _, b in boxes):
        acquired = read_acquire_popup(img)
        if acquired is not None:
            return "ACQUIRE", acquired
    if _has(boxes, "開始確認", (0, 90)) and _has(boxes, "選抜試験メモリー") and _has(boxes, "本戦へ"):
        return "HIF_FINALS_START", read_finals_start(boxes)  # 本战开始确认：当前选中的选拔回忆
    if _has(boxes, "現在順位", (100, 300)):
        return "HIF_FINALS_RANKING", read_finals_ranking_page(img, boxes)  # 本战开考前 / 各轮之后的名次页（识别-05）
    if _has(boxes, "スター性獲得", (280, 520)):
        return "STAR_GAIN", read_star_gain(img)
    if (_has(boxes, "突破ライン") and _has(boxes, "合格条件")) or _has(boxes, "優勝条件"):
        return "HIF_EXAM_BRIEFING", read_exam_briefing(img, boxes)
    if _has(boxes, "カスタマイズ完了"):
        return "PITEM_CUSTOMIZE_DONE", {}
    if any(("カスタム" in t and "選択して" in t) for t in texts):
        return "PITEM_CUSTOMIZE", {}
    if _has(boxes, "完了", (40, 140)) and _has(boxes, "プロデュース", (0, 120)) and _has(boxes, "ファン数"):
        return "PRODUCE_END", {}
    if _has(boxes, "リタイア", (900, 1200)) and (_has(boxes, "P手帳", (900, 1200)) or _has(boxes, "中断", (900, 1200))):
        return "MENU", {"kind": "produce"}  # 培育中右下「≡」菜单（中断 / P手帳 / リタイア…）：关掉即可，绝不点リタイア
    if any(re.search(r"\d+日目$", t) and b[3] <= LOG_HEADER_MAX_Y for t, b in boxes) and _log_close(img):
        return "PRODUCE_LOG", {"close": list(LOG_CLOSE)}  # 行动记录（底栏文档图标打开）：点 ✕ 关闭；KAA 会因「Pアイテム…を獲得」判成 SELECT_PITEM
    from hiro.vision.customize import is_special_guidance, read_customize_result, read_special_guidance

    done = "".join(texts)
    if "カスタマイズしまし" in done:
        line = next((t for t in texts if "をカスタマイズ" in t), done)
        return "CUSTOMIZE_RESULT", read_customize_result(line)  # 「〜をカスタマイズしました」：点一下回到定制菜单（左上标签还在，先判这个）
    if is_special_guidance(boxes):
        return "SPECIAL_GUIDANCE", read_special_guidance(img, boxes)
    if _has(boxes, "プロフィール") and _has(boxes, "名刺"):
        return "MENU", {"kind": "home"}
    # 箱子名牌上的偶像名是大字（OCR 实测只读到「篠澤」，「さん」常读不出）
    if _has(boxes, "差し入れ", (0, 100)) and any(b[3] - b[1] >= 50 and 800 <= (b[1] + b[3]) // 2 <= 1000 for _, b in boxes):
        return "GIFT", read_gift(img)  # 差し入れ礼物箱：点发光的礼物袋开箱（第四局 r4 在此停了 63 s）
    upgraded = next((t for t in texts if t.endswith("を強化しました")), None)
    if upgraded is not None:
        return "UPGRADE_RESULT", read_upgrade_result(upgraded)
    kind = _transition_kind(boxes)
    if kind is not None:
        return "TRANSITION", {"kind": kind}
    return None


def _transition_kind(boxes) -> str | None:
    texts = [t for t, _ in boxes]
    if any("NOWLOADING" in t.upper().replace(".", "") for t in texts):
        return "loading"
    if _has(boxes, "パラメータボーナス") or _has(boxes, "親愛度ボーナス"):
        return "bonus_animation"
    # 换日：「選抜試験1まで 5日」大字居中（选行动页左上角的同样文字很小、在 y<80）。要求同一框里有「試験」和「まで」：
    # 台词「いつまでも続けばいいのに」也含「まで」（数据确认 00016 误判）
    if any("試験" in t and "まで" in t and 90 <= (b[1] + b[3]) // 2 <= 200 for t, b in boxes) and not _has(boxes, "審査基準"):
        return "day_countdown"
    if _has(boxes, "選抜試験", (650, 820)) and any(re.fullmatch(r"第\d回", t) for t in texts):
        return "exam_title"
    if any(re.fullmatch(r"選抜試験\d", t) and b[3] - b[1] >= 40 and 560 <= (b[1] + b[3]) // 2 <= 720 for t, b in boxes):
        return "exam_title"  # 考场入场的「選抜試験1」
    if any(t in ("突破", "敗退") and b[3] - b[1] >= 60 and 820 <= (b[1] + b[3]) // 2 <= 1000 for t, b in boxes):
        return "exam_result_anim"  # 结算前的「突破」大字动画
    return None


# ---------------------------------------------------------------- 页面字段
STAR_GAIN_BOX = (160, 560, 520, 880)  # 金字塔区域：左侧「+N」气泡、下方累计星数


def read_star_gain(img: MatLike) -> dict:
    """スター性獲得：{gain: 本次 +N, star: None, star_shown: 金字塔下的数字}。
    - gain 取金字塔左侧气泡里的「+N」（x 180–330）；背景里饮料说明的「+5」在更左边，不取（数据确认 00319/00320 曾误读成 5）。
    - 累计星数单独裁数字读（不含左侧星形图标）；淡出中的半透明数字读不成整串数字时给 None。
    - 累计星数会从旧值滚动到新值（00320 读到 150→165 途中的 159），单帧分不出是不是最终值：star 一律 None，
      画面上看到的数放在 star_shown（只供核对）；星数以选行动页的 star 为准。"""
    from hiro.vision.ocr import ocr_boxes

    x1, y1, x2, y2 = STAR_GAIN_BOX
    boxes = [(t.replace(" ", ""), (a + x1, b + y1, c + x1, d + y1)) for t, _, (a, b, c, d) in ocr_boxes(img[y1:y2, x1:x2])]
    gains = [(b[3] - b[1], int(m.group(1))) for t, b in boxes
             if (m := re.fullmatch(r"[+＋](\d+)", t)) and 180 <= (b[0] + b[2]) // 2 <= 330 and 590 <= (b[1] + b[3]) // 2 <= 740]  # fmt: skip
    # 累计星数单独裁数字那一块读：左边的橙色星形图标会被认成「1」（90→190），右边的光圈弧线会被认成「-1」
    from hiro.vision.ocr import ocr

    shown = None
    for scale in (None, 2):  # 默认放大读不出时（RapidOCR 在 00320 上什么都没读到）换 2 倍再读一次
        text = "".join(t for t, _ in (ocr(img[775:840, 352:520]) if scale is None else ocr(img[775:840, 352:520], scale=scale)))
        if m := re.fullmatch(r"(\d{1,4})[)）]?", text.replace(" ", "")):
            shown = int(m.group(1))
            break
    return {"gain": max(gains)[1] if gains else None, "star": None, "star_shown": shown}


BRIEF_ROWS = {"Vo": 930, "Da": 1010, "Vi": 1090}  # 属性行中心 y（720x1280）


def _brief_pct(img: MatLike, digits: str, b) -> int | None:
    """说明页倍率 = 考试里各属性倍率的初值（reduce exam.attr_permil），读错会影响整场：OCR 与 ddddocr 一致才给。
    ddddocr 读「1413%」时末尾的「%」偶尔读成数字，所以只比前 len(OCR) 位。
    OCR 偶尔把左边的「>>」箭头读成一位数字粘在前面（q17 #431：「23029%」，ddddocr 3029）：去掉首位再比一次。"""
    from hiro.vision.digits import read_dddd

    x1, y1, x2, y2 = b
    dddd = read_dddd(img, (max(0, x1 - 4), max(0, y1 - 4), x2 + 4, y2 + 4))
    if dddd is None:
        return None
    # 反过来也会发生：RapidOCR 给的框更宽、把「>>」框进去了，ddddocr 读出的首位是箭头（2026-11-06：「1962%」→ 71962）
    reads = (str(dddd), str(dddd)[1:])
    return next((int(c) for c in (digits, digits[1:]) if len(c) >= 2 and any(r.startswith(c) for r in reads)), None)


def read_exam_briefing(img: MatLike, boxes) -> dict:
    """考前说明页：{exam_no, rank_line, participants, star, star_ok, params{Vo,Da,Vi}, multiplier_pct{Vo,Da,Vi}, finals}。
    选拔是「合格条件 / 突破ライン」；本战是「優勝条件 / ラウンド1,2合計スコア 1位」（finals=True，exam_no / rank_line 为 None），版式相同。
    倍率是考试里各属性回合的得分倍率（与考试画面左上的 % 一致）。突破线 OCR 常把「64」读成「04」：
    参加人数必须是突破线的 2 倍（64/128、32/64），对不上时 rank_line 为 None。"""
    line = next((t for t, _ in boxes if "突破ライン" in t), "")
    joined = " ".join(t for t, b in boxes if abs((b[1] + b[3]) // 2 - 635) <= 25)
    exam = re.search(r"試験(\d)", line)
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*人", joined)
    rank_line = participants = None
    if m:
        a, n = int(m.group(1)), int(m.group(2))
        participants = n
        rank_line = a if a * 2 == n else None
    params, mult = {}, {}
    for attr, cy in BRIEF_ROWS.items():
        row = [(t, b) for t, b in boxes if abs((b[1] + b[3]) // 2 - cy) <= 30]
        p = next((int(t) for t, b in row if re.fullmatch(r"\d{2,4}", t) and b[0] < 230), None)
        # 「%」可能被漏认（RapidOCR：「1413%」→「1413」）；倍率列靠横坐标 260–340 限定，和左边的属性值（< 230）分得开
        hit = next(((mm.group(1), b) for t, b in row if (mm := re.match(r"(\d{2,6})%?$", t.strip())) and 260 <= b[0] <= 340), None)
        params[attr], mult[attr] = p, _brief_pct(img, *hit) if hit else None
    star = next((int(t) for t, b in boxes if re.fullmatch(r"\d{1,4}", t) and abs((b[1] + b[3]) // 2 - 852) <= 25 and b[0] < 230), None)
    # 「親愛度ボーナス」演出中倍率在往上跳（314% → 471% → 1413%），这时的数不是最终值
    animating = _has(boxes, "ボーナス") is not None
    if animating:
        mult = {k: None for k in mult}
    return {"exam_no": int(exam.group(1)) if exam else None, "rank_line": rank_line, "participants": participants,
            "star": star, "star_ok": True if _has(boxes, "達成", (820, 880)) else None, "params": params, "multiplier_pct": mult,
            "complete": not animating, "finals": _has(boxes, "優勝条件") is not None}  # fmt: skip


def read_upgrade_result(text: str) -> dict:
    """「えいえいおーを強化しました」→ 强化前的卡名、card_id 与强化后的 upgrade（= 卡名里的「+」数 + 1）。卡名认不出时都给 None。"""
    from hiro.vision.deck import card_from_name

    name = text.removesuffix("を強化しました")
    card = card_from_name(name)
    if card:  # 认出了就用主数据卡名（RapidOCR 长音读成「一」：「えいえいお一」），保留 OCR 读到的「+」
        name = card["name"] + "+" * card["upgrade"]
    return {"name": name, "card_id": card["card_id"] if card else None, "upgrade": card["upgrade"] + 1 if card else None, "text": text}


LOG_HEADER_MAX_Y = 220  # 行动记录顶部「1日目」标题条（实测 y 137–166）
LOG_CLOSE = (360, 1160)  # 底部白色圆形 ✕
LOG_CLOSE_PROBE = (335, 1178, 385, 1196)  # ✕ 圆的下半截（白色面板之外）


def _log_close(img: MatLike) -> bool:
    x1, y1, x2, y2 = LOG_CLOSE_PROBE
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return float(((hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 220)).mean()) >= 0.6


GIFT_REGION = (40, 520, 680, 1100)  # 礼物箱区域（720x1280）
GIFT_MIN_AREA = 1500


def read_gift(img: MatLike) -> dict:
    """差し入れ礼物箱：{bag: [x, y] 发光礼物袋（彩虹色）的中心 | None}。取高饱和、高亮的最大连通块。"""
    x1, y1, x2, y2 = GIFT_REGION
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 200)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, st, cent = cv2.connectedComponentsWithStats(mask)
    best = max(range(1, n), key=lambda i: st[i][cv2.CC_STAT_AREA], default=None)
    if best is None or st[best][cv2.CC_STAT_AREA] < GIFT_MIN_AREA:
        return {"bag": None}
    cx, cy = cent[best]
    return {"bag": [int(cx + x1), int(cy + y1)]}


def read_finals_ranking_page(img: MatLike, boxes) -> dict:
    """本战名次页「現在順位」：已演出的人按名次从上到下（大字 1st / 2nd + 右下「399,645Pt」），玩家卡是青色底；
    还没演出时玩家卡在「NEXT」下面、没有分数。
    {entries: [{rank, score, me}], next: 有「NEXT」, my_score, rank（玩家已演出才有）, finals_round: 1 / 2 / None}。
    finals_round 按 NPC 目标分落在主数据哪一轮的区间（hif.finals_round_from_npc）。"""
    from hiro.vision.hif import _result_score, finals_round_from_npc, read_retry

    title = next((t for t, b in boxes if "ラウンド" in t and "結果" in t), None)
    if title is not None or _has(boxes, "ログ") is not None:
        return _read_round_result(img, boxes, title)  # 演出后的结果页（轮次结果 / R2 后的最终页）：名次数字 + 分数，玩家行带「ログ」
    rows = sorted(((b[1] + b[3]) // 2, int(re.sub(r"\D", "", m.group(1)))) for t, b in boxes
                  if (m := re.search(r"([\d,]{3,})\s*[pP][tT]", t)))  # fmt: skip
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    entries = []
    for i, (y, score) in enumerate(rows):
        card = hsv[max(0, y - 150) : max(1, y - 20), 40:200]  # 分数在卡的右下角，卡左上是大字名次
        cyan = ((card[:, :, 0] >= 85) & (card[:, :, 0] <= 105) & (card[:, :, 1] >= 120) & (card[:, :, 2] >= 150)).mean()
        entries.append({"rank": i + 1, "name": None, "score": score, "me": bool(cyan >= 0.3)})
    me = next((e for e in entries if e["me"]), None)
    npc = [e["score"] for e in entries if not e["me"]]
    return {"kind": "standings", "score": me["score"] if me else None, "round_score": None,
            "entries": entries, "next": _has(boxes, "NEXT") is not None,
            "my_score": me["score"] if me else None, "rank": me["rank"] if me else None,
            "finals_round": finals_round_from_npc(npc), "round_result": None, "retry": None,
            "my_gain": None, "final": False, "outcome": None, "passed": None}  # fmt: skip


GAIN_PLUS_W = 14  # 加分前的「+」号宽（ddddocr 从它右边开始读）


def _gain_cut(img: MatLike, box, n_total: int, n_gain: int) -> int:
    """「总分 +加分」框里加分（蓝色小字）开始的 x：框宽 35% 往右第一列蓝色像素；找不到按字数比例估。"""
    x1, y1, x2, y2 = box
    start = x1 + (x2 - x1) * 35 // 100
    hsv = cv2.cvtColor(img[y1 + 8 : y2 - 8, start:x2], cv2.COLOR_BGR2HSV)
    blue = ((hsv[:, :, 0] >= 90) & (hsv[:, :, 0] <= 115) & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 150)).sum(axis=0) >= 3
    cols = np.where(blue)[0]
    return start + int(cols[0]) - 2 if len(cols) else x1 + round((x2 - x1) * n_total / (n_total + n_gain)) + 4


def _merged_gain(img: MatLike, listed: str, box, n_total: int, n_gain: int) -> int | None:
    """加分（蓝色小字）三票取多数：整框 OCR 的「+」后段、只裁加分段放大 OCR、ddddocr（跳过「+」号，否则「+」常读成 4）。
    单次 OCR 常错一位（第二十一局重读：373,312→573,312、236,937→256,937、356,088→350,088）。
    ddddocr 位数与整框读数一致时直接用它：另两票都是 Vision OCR，会一起把蓝色小字的 6 读成 0（第二十九局 R2：402,477 两票 vs 462,477），
    人工核对的 7 例分歧里 ddddocr 都对（265,312 / 430,255 / 356,088 / 462,477 …）；位数对不上才退回多数票。"""
    from collections import Counter

    from hiro.vision.digits import read_dddd
    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = box
    cut = _gain_cut(img, box, n_total, n_gain)
    crop = img[max(0, y1 - 6) : y2 + 6, cut : min(img.shape[1], x2 + 6)]
    again = re.sub(r"\D", "", "".join(t for t, _ in ocr(cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC), scale=1)))
    dddd = read_dddd(img, (cut + GAIN_PLUS_W, max(0, y1 - 4), min(img.shape[1], x2 + 4), y2 + 4))
    d = str(dddd) if dddd is not None else None
    if d and listed and len(d) == len(listed) + 1 and d[0] == "4":
        d = d[1:]  # 「+」读成 4
    if d and listed and len(d) == len(listed):
        return int(d)  # ddddocr 优先：另两票同为 Vision OCR、会一起错（第二十九局 R2：两票 402,477、ddddocr 462,477 才对）
    votes = [listed or None, again or None, d]
    best, n = Counter(v for v in votes if v).most_common(1)[0] if any(votes) else (None, 0)
    return int(best) if best and n >= 2 else None


def _merged_total(img: MatLike, listed: str, box, n_total: int, n_gain: int) -> int | None:
    """「1,078,090+598,516」并在一个框里时的总分：整框 OCR 的总分段、只裁总分段放大 OCR（取「+」前）、ddddocr 三票取多数。"""
    from collections import Counter

    from hiro.vision.digits import read_dddd
    from hiro.vision.ocr import ocr

    x1, y1, x2, y2 = box
    # 加分是蓝色小字、总分是白字：切在第一列蓝色像素之前（按字数比例估会切掉总分末位）
    # 只看框中间几行、从框宽 35% 往右找（框边缘有进度条等蓝色，总分至少占框的三分之一）
    cut = _gain_cut(img, box, n_total, n_gain)
    crop = img[max(0, y1 - 6) : y2 + 6, max(0, x1 - 6) : cut]
    text = "".join(t for t, _ in ocr(cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC), scale=1))
    again = re.sub(r"\D", "", re.split(r"[+＋]", text)[0])
    dddd = read_dddd(img, (max(0, x1 - 4), max(0, y1 - 4), cut, y2 + 4))
    votes = [listed or None, again or None, str(dddd) if dddd is not None else None]
    best, n = Counter(v for v in votes if v).most_common(1)[0] if any(votes) else (None, 0)
    return int(best) if best and n >= 2 else None


def _row_name(boxes, score_box) -> str | None:
    """名次页一行的名字：分数正上方（约 55 像素）、左缘对齐的文字（「十王 星南」）。"""
    cy = (score_box[1] + score_box[3]) // 2
    hits = [(t, b) for t, b in boxes if 200 <= b[0] <= 420 and 25 <= cy - (b[1] + b[3]) // 2 <= 90
            and not re.search(r"\d{2}", t) and len(t) >= 2]  # fmt: skip
    return _idol_name(min(hits, key=lambda h: abs(cy - (h[1][1] + h[1][3]) // 2 - 55))[0]) if hits else None


@cache
def _idol_names() -> tuple[str, ...]:
    from kaa.db.sqlite import select_many

    return tuple(r["lastName"] + r["firstName"] for r in select_many("SELECT lastName, firstName FROM Character"))


def _idol_name(text: str) -> str | None:
    """OCR 名字 → 主数据角色名（「+王星南」→「十王星南」）：相似度 ≥ 0.6 的最像的一个，没有就 None（不猜）。"""
    from difflib import SequenceMatcher

    text = text.replace(" ", "")
    best = max(_idol_names(), key=lambda n: SequenceMatcher(None, text, n).ratio(), default=None)
    return best if best and SequenceMatcher(None, text, best).ratio() >= 0.6 else None


RESULT_SCORE_MAX = 2_999_999  # 本战两轮合计的上限（历史最高 1,303,847；NPC 约 108 万），超过一定是读错
RESULT_ROW_Y = (324, 503, 682)  # 结算页三行分数框的中心 y（10 张实机图 ±3 像素）


def _rank_by_row_y(cy: int) -> int | None:
    """名次大字没读到时按分数行的位置定名次。按行序数会在漏读一行时错位（Windows 第三局 r2 00228：倉本行没读到，我方第 3 被当成第 2）。"""
    return next((i + 1 for i, y in enumerate(RESULT_ROW_Y) if abs(cy - y) <= 40), None)


def _split_gain_box(boxes, b) -> tuple[str | None, tuple]:
    """最终页一行的总分和加分被 OCR 拆成两个框（Windows 第七局 r2 00312：「873,015」与「1274,711」，「+」读成「1」）：
    找同一行紧挨在总分框右边的数字框当加分，去掉读错的「+」（1 / 十 / +），返回 (加分文字, 合并后的框)。没有就 (None, b)。"""
    cy = (b[1] + b[3]) / 2
    for t, b2 in boxes:
        if b2 is b or not (-25 <= b2[0] - b[2] <= 30 and b2[2] > b[2] and abs((b2[1] + b2[3]) / 2 - cy) <= 15):
            continue
        g = re.fullmatch(r"[+＋十1]?\s*(\d{1,3}(?:[,、]\d{3})+)", t.replace(" ", ""))
        if g:
            return g.group(1).replace("、", ","), (b[0], min(b[1], b2[1]), b2[2], max(b[3], b2[3]))
    return None, b


def _read_round_result(img: MatLike, boxes, title: str | None) -> dict:
    """本战演出后的名次页（结算页布局）：左侧名次数字、名字、分数（带逗号、无 Pt），玩家行白框 + 右侧「ログ」，左下再挑戦。
    - 轮次结果「H.I.F本戦 ラウンド1結果 現在順位」：每行一个分数；
    - R2 后的最终页「敗退（突破…）/ 現在順位」：每行「总分 +本轮加分」（OCR 常并成一个框「1,078,090+598,516」），final=True。
    分数（总分部分）三次读数取多数（hiro.vision.hif._result_score）；玩家行 = 与「ログ」同一行。"""
    from hiro.vision.hif import _result_score, finals_round_from_npc, read_retry

    num = r"\d{1,3}(?:,\d{3})+"
    m = re.search(r"ラウンド\s*(\d)", title or "")
    log = next((b for t, b in boxes if t.startswith("ログ")), None)
    rows = []
    final_page = m is None  # 没有「ラウンドN」：R2 后的最终页，每行「总分 +加分」
    for t, b in boxes:
        t = re.sub(r"[、，.]", ",", t)  # 加分的逗号常读成「、」（「820,463+455、281」整行没对上，名次也跟着错位）
        # 加分的逗号 OCR 常漏（第二十一局 R2 我方行「1,045,084+504068」整行没对上 → 我方第 None 名）：加分部分允许没有逗号
        # 「+」常读错：「:」（main15 #280）、「#」（Windows 第三局 r2 00228，那一行整行丢了、名次跟着错）——分隔符收任意 1–2 个非数字字符
        mm = re.fullmatch(rf"({num})(?:\s*[^\d,\s]{{1,2}}\s*(\d{{1,3}}(?:,\d{{3}})+|\d{{1,7}}))?", t)
        if mm and 200 <= b[0] <= 420:
            gain = mm.group(2)
            if gain is None and final_page:
                gain, b = _split_gain_box(boxes, b)
            rows.append((b, mm.group(1), gain))
    rows.sort(key=lambda r: r[0][1])
    # 名次读左侧大字（1 / 2 / 3，与分数同一行），读不到再按行序
    digits = [(int(t), (b[1] + b[3]) // 2) for t, b in boxes if re.fullmatch(r"[1-9]", t) and b[2] <= 140]
    entries = []
    for i, (b, total, gain) in enumerate(rows):
        cy = (b[1] + b[3]) // 2
        rank = next((d for d, y in digits if abs(y - cy) <= 60), None) or _rank_by_row_y(cy) or i + 1
        if gain:
            score = _merged_total(img, re.sub(r"\D", "", total), b, len(total), len(gain) + 1)
        else:
            score, _ = _result_score(img, re.sub(r"\D", "", total), b)
        if score is not None and score > RESULT_SCORE_MAX:
            score = None  # Windows 第四局 r2 00219：十王 1,079,363 读成 36350791（Mac 同一帧读对，复现不了），名次照用
        me = log is not None and abs((log[1] + log[3]) // 2 - (b[1] + b[3]) // 2) <= 50
        g = _merged_gain(img, re.sub(r"\D", "", gain), b, len(total), len(gain) + 1) if gain else None
        entries.append({"rank": rank, "name": _row_name(boxes, b), "score": score, "gain": g, "me": me,
                        "round_score": g if g is not None else (score if m and m.group(1) == "1" else None)})  # fmt: skip
    me = next((e for e in entries if e["me"]), None)
    npc = [e["score"] for e in entries if not e["me"]]
    final = m is None
    outcome = next((t for t, b in boxes if t in ("敗退", "突破", "優勝", "準優勝") and b[1] < 160), None)
    rnd = int(m.group(1)) if m else None
    return {"kind": "round_result" if rnd else "final", "score": me["score"] if me else None, "round_score": me["round_score"] if me else None,
            "entries": entries, "next": False, "my_score": me["score"] if me else None, "rank": me["rank"] if me else None,
            "my_gain": me["gain"] if me else None, "finals_round": rnd if rnd else (2 if final else finals_round_from_npc(npc)),  # 最终页在 R2 之后
            "round_result": rnd, "final": final, "outcome": outcome, "retry": read_retry(img),
            "passed": {"敗退": False, "突破": True, "優勝": True, "準優勝": True}.get(outcome) if outcome else None}  # fmt: skip


def read_hud_resources(img: MatLike) -> dict:
    """培育页头部的体力 / P 点（选行动、轮间、特別指導同一位置）→ {stamina, max_stamina, produce_point}。
    三种读法（OCR / 游戏字体 / ddddocr）至少两票一致才用（游戏执行 fcff374：单次 OCR 偶有 340→540、320→525）；
    体力的 OCR 一票用 action.read_stamina（斜杠读成 1 / 0 时也能拆）。"""
    from collections import Counter

    from hiro.vision.action import POINTS_BOX, STAMINA_BOX, read_stamina
    from hiro.vision.digits import read_dddd, read_text
    from hiro.vision.ocr import ocr

    def vote(*values) -> int | None:
        votes = [v for v in values if v is not None]
        if not votes:
            return None
        best, n = Counter(votes).most_common(1)[0]
        return best if n >= 2 or len(votes) == 1 and len(values) == 1 else None

    def font(box) -> int | None:
        t, _ = read_text(img, box)  # 「不确定」多半是斜杠 / 图标，数字本身仍可用作一票
        return int(t) if t.isdigit() else None

    x1, y1, x2, y2 = POINTS_BOX
    pt = re.search(r"\d+", "".join(t for t, _ in ocr(img[y1:y2, x1:x2], scale=4, languages=("en-US",))))
    st = read_stamina(img)
    sx1, sy1, sx2, sy2 = STAMINA_BOX
    hp_box, max_box = (sx1, sy1, 380, sy2), (382, sy1, sx2, sy2)
    return {"stamina": vote(st[0] if st else None, font(hp_box), read_dddd(img, hp_box)),
            "max_stamina": vote(st[1] if st else None, read_dddd(img, max_box)),
            "produce_point": vote(int(pt.group()) if pt else None, font(POINTS_BOX), read_dddd(img, POINTS_BOX))}  # fmt: skip


def read_interval(img: MatLike) -> dict:
    """HIF_ROUND_INTERVAL（本战 R1 与 R2 之间）：头部体力 / P 点（read_hud_resources）+ 货架
    （hiro.vision.shop.read_interval_shelf，格式见 docs/llm-context.md）。货架没完整出现（入场过场、确认框盖住）时 interval=None、complete=False。
    dialog：盖在轮间上的确认框（read_dialog；「リフレッシュ確認」带 confirm = read_refresh_confirm），没有为 None。
    第十八局本战：刷新后弹「リフレッシュ確認」没人处理，轮间读数全 None 被当成「已不在轮间」（总控 2026-11-02）。"""
    from hiro.vision.shop import read_interval_shelf

    shelf = read_interval_shelf(img)
    return {**read_hud_resources(img), "interval": shelf["interval"] if shelf["complete"] else None, "complete": shelf["complete"],
            "dialog": overlay_dialog(img)}  # fmt: skip


def overlay_dialog(img: MatLike) -> dict | None:
    """页面上盖着的确认框：先像素判标题条（约 20 ms，没有就不 OCR），有再 read_dialog；refresh_confirm 另读 confirm。"""
    if dialog_header(img) is None:
        return None
    d = read_dialog(img)
    if d is not None and d["kind"] == "refresh_confirm":
        d = {**d, "confirm": read_refresh_confirm(img, d["y"])}
    return d


def read_comm_error(img: MatLike, header_y: int | None = None) -> dict:
    """「通信エラー」→ {retry, title, close, forced_title, text}：按钮中心（没有为 None）。
    - 「通信中にエラーが発生しました」：「タイトルへ」/「リトライ」→ 点リトライ（用户规则：绝不点タイトルへ）；
    - 「時間が経っているのでタイトルに戻します」：只有「閉じる」→ forced_title=True，点閉じる后走 resume（标题 → 首页 → プロデュース再開）。
    第二十二局本战轮间（12:26）：前一种挂了 195 秒没人处理（总控 2026-11-03）。"""
    from hiro.vision.ocr import ocr_boxes

    y0 = max(0, (header_y if header_y is not None else 970) + 30)
    boxes = [(t.replace(" ", ""), (b[0], b[1] + y0, b[2], b[3] + y0)) for t, _, b in ocr_boxes(img[y0 : min(img.shape[0], y0 + 330)])]

    def button(*words: str) -> list[int] | None:
        b = max((b for t, b in boxes if any(w in t for w in words)), key=lambda b: b[1], default=None)
        return [int((b[0] + b[2]) // 2), int((b[1] + b[3]) // 2)] if b else None

    text = "".join(t for t, _ in boxes)
    retry, close = button("リトライ"), button("閉じる")
    # 「へ」和片假名「ヘ」字形相同，RapidOCR 会读成后者（2026-11-06）
    return {"retry": retry, "title": button("タイトルへ", "タイトルヘ"), "close": close, "text": text,
            "forced_title": retry is None and (close is not None or "タイトルに戻" in text)}  # fmt: skip


RETRY_CANCEL, RETRY_OK = (215, 1160), (505, 1160)  # 「再挑戦確認」按钮（第二十局实测）


def read_retry_confirm(img: MatLike, header_y: int | None = None) -> dict:
    """「再挑戦確認」→ {free, jewel, tickets_before, tickets_after, left, total, cancel, ok}。
    free：「本日あと1回無料」；jewel：正文出现「ジュエル」（用户规则：消耗ジュエル的再挑戦一律キャンセル）；
    tickets：「所持 267 → 267」（免费时不减）；left / total：「残り再挑戦回数 3/3」。"""
    from hiro.vision.ocr import ocr_boxes

    y0 = max(0, (header_y if header_y is not None else 570) + 30)
    boxes = [(t.replace(" ", ""), (b[0], b[1] + y0, b[2], b[3] + y0)) for t, _, b in ocr_boxes(img[y0 : min(img.shape[0], y0 + 650)])]
    texts = [t for t, _ in boxes]
    own = next((b for t, b in boxes if "所持" in t), None)
    nums = sorted((b[0], int(t)) for t, b in boxes if re.fullmatch(r"\d{1,5}", t) and own and abs((b[1] + b[3]) // 2 - (own[1] + own[3]) // 2) <= 25)
    frac = next((re.search(r"(\d+)/(\d+)", t) for t in texts if re.search(r"^\d+/\d+$", t)), None)

    def button(word: str, default: tuple[int, int]) -> list[int]:
        b = max((b for t, b in boxes if t == word or t.endswith(word)), key=lambda b: b[1], default=None)
        return [int((b[0] + b[2]) // 2), int((b[1] + b[3]) // 2)] if b else list(default)

    return {"free": any("無料" in t for t in texts), "jewel": any("ジュエル" in t for t in texts),
            "tickets_before": nums[0][1] if len(nums) >= 2 else None, "tickets_after": nums[-1][1] if len(nums) >= 2 else None,
            "left": int(frac.group(1)) if frac else None, "total": int(frac.group(2)) if frac else None,
            "cancel": button("キャンセル", RETRY_CANCEL), "ok": button("決定", RETRY_OK)}  # fmt: skip


def read_refresh_confirm(img: MatLike, header_y: int | None = None) -> dict:
    """「リフレッシュ確認」→ {p_before, p_after, cost, jewel, cancel, ok}。
    jewel：正文出现「ジュエル」（要花ジュエル的刷新，执行侧只能点キャンセル）；cancel / ok：「キャンセル」「更新する」按钮中心。"""
    from hiro.vision.ocr import ocr_boxes

    y0 = max(0, (header_y if header_y is not None else 658) + 30)
    boxes = [(t.replace(" ", ""), (b[0], b[1] + y0, b[2], b[3] + y0)) for t, _, b in ocr_boxes(img[y0 : min(img.shape[0], y0 + 600)])]
    nums = sorted(((b[0], int(t)) for t, b in boxes if re.fullmatch(r"\d{1,5}", t)), key=lambda x: x[0])
    before, after = (nums[0][1], nums[-1][1]) if len(nums) >= 2 else (None, None)

    def button(word: str, default: tuple[int, int]) -> list[int]:
        b = max((b for t, b in boxes if word in t), key=lambda b: b[1], default=None)  # 按钮在最下面（正文也有「更新をします」）
        return [int((b[0] + b[2]) // 2), int((b[1] + b[3]) // 2)] if b else list(default)

    return {"p_before": before, "p_after": after, "cost": before - after if before is not None and after is not None else None,
            "jewel": any("ジュエル" in t for t, _ in boxes), "cancel": button("キャンセル", REFRESH_CANCEL), "ok": button("更新する", REFRESH_OK)}  # fmt: skip


def _growth_pct(text: str) -> float | None:
    """成长率「45.5%」（总是一位小数）。RapidOCR 会把小数点读成「:」「,」或读丢（DMM start-confirm-dmm1「ヘ455%」「〜44:4%」）。"""
    if m := re.search(r"(\d{1,2})\s*[.:,，．]\s*(\d)(?!\d)", text):
        return float(f"{m.group(1)}.{m.group(2)}")
    if m := re.search(r"(?<!\d)(\d{2})(\d)\s*%", text):
        return float(f"{m.group(1)}.{m.group(2)}")
    return None


def read_finals_start(boxes) -> dict:
    """本战「開始確認」页（H.I.F本戦へ 之前）：当前选中的那份选拔回忆的概要。
    {selected_memory: {idol, star, params{Vo,Da,Vi}, growth_pct{Vo,Da,Vi}, deck: None, pitems: None}, qualify_memories: None}。
    - 星星在三角徽章旁（y≈447）、属性一行（y≈496，从左到右 Vo / Da / Vi）、成长率一行（y≈524）；
    - 牌组网格的卡缩略图很小、现有样本只有 540x960 截图，deck / pitems 暂不读；
    - 可在多份回忆间切换的列表 / 详情页尚无样本，qualify_memories（docs/llm-context.md「QUALIFY_MEMORY」）为 None，已列入采样清单。"""

    def row(y_lo: int, y_hi: int, pattern: str) -> list[tuple[int, str]]:
        return sorted(((b[0] + b[2]) // 2, t) for t, b in boxes if y_lo <= (b[1] + b[3]) // 2 <= y_hi and re.search(pattern, t))

    nums = [int(re.sub(r"\D", "", t)) for _, t in row(482, 512, r"^\D?\d{2,4}$")]
    pcts = [v for _, t in row(514, 540, r"\d") if (v := _growth_pct(t)) is not None]
    star = next((int(t) for x, t in row(430, 465, r"^\d{2,4}$") if 440 <= x <= 600), None)
    idol = next((t for t, b in boxes if b[0] >= 480 and 150 <= (b[1] + b[3]) // 2 <= 230 and not re.search(r"\d", t)), None)
    return {"selected_memory": {"idol": idol, "star": star,
                                "params": dict(zip(("Vo", "Da", "Vi"), nums)) if len(nums) == 3 else None,
                                "growth_pct": dict(zip(("Vo", "Da", "Vi"), pcts)) if len(pcts) == 3 else None,
                                "deck": None, "pitems": None},
            "qualify_memories": None}  # fmt: skip
