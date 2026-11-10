# 来源：digital-hiro src/hiro/vision/quick.py（develop c3fc27f）；开源版改：去掉活动 Logo 美术模板
"""快速判页（协调 2026-11-02：每局 4–7 次「页面认不出超过 30 秒」，一次卡 31–73 秒）。

主循环每轮先调 quick_page，命中就直接处理，不再走 KAA check_scene（这类页面上它要 1–2.5 秒、返回 None）
和整帧 OCR 补判链（又是几秒）。一连串「点一下就过」的弹窗每个都要等一整轮，加起来就超过 30 秒。

只用模板、像素和局部 OCR，目标每帧 ≤ 0.3 秒；认不出返回 None，照旧走原来的流程。返回 {page, tap, dispatch, fields, ms}：
- DECK_LIST（tap=「閉じる」）：牌组一览挂着，最先判（KAA 会把一览底部「Pアイテム」标签判成 SELECT_PITEM）；
- dispatch="SELECT_PITEM"：P 道具三选一 / 定制后三选一（「獲得するPアイテムを選択してください」「カスタム後を選択してください」），
  交给原来的 SELECT_PITEM 处理（KAA 判 IDLE / None，原来要等 30 秒后整帧 OCR 补判）；
- tap=(x, y)：点一下就过的页面：
  · ACQUIRE：获得演出（课程结束拿饮料、メモリー効果、差し入れ 等），名称栏局部 OCR 精确命中主数据才算；fields 是 read_acquire_popup 的读数；
  · GIFT：差し入れ礼物箱（左上「差し入れ」标签 + 没有选择面板），点发光的礼物袋；
    袋子打开后的「Pドリンク獲得」光效（TRANSITION kind=gift_effect）不点，等饮料三选一弹窗；
  · TRANSITION（kind=tap_hint）：底部「TAP」/「タップして次へ」（选拔奖励 / パラメータボーナス；考前说明页见下）；
  · 底部白色圆角气泡（没有确认框标题条、上方没有选择面板），再读气泡文字（read_bubble）：UPGRADE_RESULT / CUSTOMIZE_RESULT /
    DELETE_RESULT（「A と B を削除しました」：考试后自动处理基础卡、相談删卡）/ TRANSITION（kind=message，「Vo.が87上昇した!」等）。
  · commu：剧情底栏「SKIP」「AUTO」（白色字形）都对上，点 SKIP；
- DIALOG（kind=refresh_confirm）：「リフレッシュ確認」（标题白字模板）→ 读 P 前后 / ジュエル字样，点「キャンセル」
  （刷新动作自己会按更新；落到主循环的是没有刷新决策时挂着的框）；
- TRANSITION kind=login_bonus：日切换后的活动登录奖励弹窗（「開催期間」白字，共 4 屏），点底部空白 (360,1180)；
- LOADING（tap=None）：浅蓝花纹底的读取 / 支援卡过场 / 星星金字塔、右下「NOW LOADING」的读取，不点，等一下；
- TRANSITION：整屏青色闪屏（kind=flash，不点）；「カスタム選択」过场（custom_cutin，点 (12,12)，与 hiro.loop.advance 对认不出帧的连点一致）。
  第十七局选拔逐帧核对后补（总控 2026-11-02）。
- 第二十一局选拔补的可点演出（点 (12,12)）：TRANSITION kind=select_bonus（「選抜試験ボーナス」）/ toppa（「選抜試験 第N回 突破」）/
  custom_pitem（「カスタムPアイテム / アイテム獲得」）/ custom_pitem_get（「カスタムPアイテム獲得」）；STAR_GAIN（「スター性獲得」，读 gain）；
  选拔试验评价「TAPで次へ」点提示；P 道具效果说明浮条（TRANSITION kind=pitem_note，第二十三局）点 (360,250)。认出后 hiro.loop.advance 不再连点，所以这里自己点。
- HIF_EXAM_BRIEFING：考前说明页（「合格条件」/「優勝条件」条）出了「タップして次へ」= 倍率定格，整屏 OCR 读 read_exam_briefing
  （各属性倍率是本场真值，reduce → exam_session.brief_permil）再点提示；提示出来之前是ボーナス演出（TRANSITION kind=exam_bonus），不读不点。
点的位置：礼物袋 / TAP 提示 / SKIP 本身；其余点左上角 (12, 12)（与 KAA skip() 相同，这些页面实机上点它会前进）。
"""

from __future__ import annotations

import re
import time
from functools import cache
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

TEMPLATES = Path(__file__).with_name("templates")
SKIP_TAP = (12, 12)
LOGIN_BONUS_TAP = (360, 1180)  # 活动登录奖励：底部空白（正中央是道具框，点它不翻页；执行 23e6965 实机验证）
DECK_LIST_CLOSE = (360, 1157)  # 牌组一览底部「閉じる」（同总控 controller.DECK_LIST_CLOSE）
DIALOG_HEADER_MAX_Y = 1100
GRAY_TPL = {  # 名称 → (搜索区域, 阈值)
    "quick_pitem_custom_title": ((100, 640, 620, 760), 0.85),  # 「カスタム後を選択してください」：正例 0.98–1.0，反例 ≤ 0.30
    "quick_gift_label": ((40, 20, 200, 90), 0.9),  # 左上「差し入れ」标签（选饮料 / 选卡页也有，要再看没有选择面板）
    "quick_drink_gain": ((150, 660, 560, 800), 0.8),  # 礼物袋打开后的「Pドリンク獲得」光效：正例 0.93–1.0（q15–q18 9 帧），反例 ≤ 0.52
    "quick_tap_en": ((300, 1180, 420, 1260), 0.85),  # 「TAP」：正例 0.95–1.0，反例 ≤ 0.63
    "quick_tap_ja": ((240, 1160, 490, 1240), 0.85),  # 「タップして次へ」：正例 0.92–1.0（动画中 0.74，下一帧再认）
    "quick_hud_label": ((500, 10, 690, 80), 0.8),  # 右上「審査基準」：培育画面才有（剧情对话框、事件过场、主页菜单没有）
    "quick_pass_label": ((20, 580, 220, 680), 0.9),  # 考前说明页「合格条件」条（选拔）：正例 0.96–1.0，反例 ≤ 0.75
    "quick_finals_label": ((20, 580, 220, 680), 0.8),
    "quick_ptecho_label": ((560, 660, 720, 760), 0.8),
}
WHITE_TPL = {  # 白色字形模板（背景会变）：名称 → (搜索区域, 阈值)
    "quick_commu_skip": ((150, 1160, 300, 1240), 0.6),  # 剧情底栏「▶SKIP」：剧情帧 0.78–1.0，其他 ≤ 0.26
    "quick_commu_auto": ((410, 1160, 570, 1240), 0.6),  # 「▶▶AUTO」：两个都对上才算剧情（不是剧情时 SKIP 的位置是饮料栏第 3 格）
    "quick_now_loading": ((400, 1150, 720, 1280), 0.75),  # 右下「NOW LOADING…」（HIF 蓝紫读取 / 选拔试验标题）：正例 0.82–1.0，反例 ≤ 0.10
    "quick_now_loading2": ((400, 1150, 720, 1280), 0.5),  # 「移動中」读取（字号略小）：正例 0.54–1.0，反例 ≤ 0.36
    "quick_custom_cutin": ((150, 740, 570, 870), 0.8),
    "quick_pdrink_max_title": ((40, 60, 560, 170), 0.8),
    "quick_event_period": ((40, 180, 500, 290), 0.85),  # 活动弹窗标志下「開催期間: …まで」白字：正例 1.0，fixtures 反例 ≤ 0.52
    # 第二十一局选拔可点过场（执行 23e6965 清单；得分：第二十一局 150 帧 + fixtures 313 张）
    "quick_select_bonus": ((100, 100, 620, 240), 0.8),  # 「選抜試験ボーナス」：正例 0.96–1.0，反例 ≤ 0.37
    "quick_star_gain": ((100, 340, 620, 470), 0.7),  # 「スター性獲得」：正例 0.89–1.0（淡入淡出 0.54–0.77），反例 ≤ 0.51
    "quick_toppa": ((200, 760, 520, 960), 0.85),  # 「選抜試験 第N回 突破」的「突破」：正例 1.0，反例 ≤ 0.63
    "quick_custom_pitem": ((60, 370, 660, 500), 0.8),  # 「カスタムPアイテム」（アイテム獲得 / カスタム選択）：正例 0.98–1.0，反例 ≤ 0.21
    "quick_custom_pitem_get": ((10, 190, 710, 330), 0.8),  # 「カスタムPアイテム獲得」：正例 1.0，反例 ≤ 0.49
    "quick_tap_de_next": ((200, 1100, 520, 1200), 0.8),  # 选拔试验评价页「TAPで次へ」：正例 1.0，反例 ≤ 0.26
    "quick_select_eval_title": ((60, 100, 660, 280), 0.8),  # 「選抜試験評価」标题：选拔 1.0，本战「最終プロデュース評価」0.40（那页 KAA 认作 PRODUCE_END，交给它）  # 「Pドリンク所持上限」标题白字：溢出页 1.0，其他 ≤ 0.22（17,595 帧）  # 「カスタム選択」过场（之后是定制后三选一）：正例 0.996–1.0，反例 ≤ 0.32
}
REFRESH_TITLE_MIN = 0.8  # 标题白字「リフレッシュ」（也覆盖「リフレッシュ実行確認」「相談リフレッシュ確認」）：正例 1.0（main18 39 帧），其他确认框 ≤ 0.39
FLASH_CYAN_MIN = 0.6  # 整屏青色闪屏（课程 / 事件之间的转场）：闪屏 0.83–1.0，其他 ≤ 0.18
LOADING_PALE_MIN = 0.5  # 浅蓝花纹底（读取 / 支援卡过场 / 星星金字塔）：读取画面 0.65–1.0，其他 ≤ 0.24
WHITE_X = (60, 660)
PANEL_Y = (650, 940)  # 选择面板 / 卡格面板所在：这里有 ≥ 40 行连续白色 = 是选择页，不是气泡 / 礼物箱
BUBBLE_Y = (940, 1135)  # 底部消息气泡
# 日程表覆盖层（スケジュール / 育成目標 / 編成）：点左上「選抜試験Nまで」框会打开。底部青色标签栏 + 下方白色圆形 ✕。
# DMM dmm2：过渡页点 (12,12) 落进了那个框（DMM 坐标有偏移），日程表的白色时间轴又被当成底部消息气泡，再点 (12,12)，转了 3 分钟停机
SCHEDULE_BAR_Y = (1078, 1168)
SCHEDULE_CLOSE = (360, 1200)
SCHEDULE_CLOSE_BOX = (335, 1180, 385, 1230)
ACQ_BAR_Y, ACQ_BAR_X, ACQ_BAR_ROWS = (840, 920), (160, 560), (16, 26)  # 获得演出名称栏：白色横条 20–21 行


@cache
def _tpl(name: str) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(str(TEMPLATES / f"{name}.png")), cv2.COLOR_BGR2GRAY)


def _score(gray: np.ndarray, name: str) -> float:
    (x1, y1, x2, y2), _ = GRAY_TPL[name]
    return float(cv2.matchTemplate(gray[y1:y2, x1:x2], _tpl(name), cv2.TM_CCOEFF_NORMED).max())


def _hit(gray: np.ndarray, name: str) -> bool:
    return _score(gray, name) >= GRAY_TPL[name][1]


def _white(bgr: np.ndarray) -> np.ndarray:
    return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), (0, 0, 200), (180, 50, 255))


@cache
def _white_tpl(name: str) -> np.ndarray:
    return _white(cv2.imread(str(TEMPLATES / f"{name}.png")))


def _cyan(hsv: np.ndarray) -> float:
    hsv = hsv[::4, ::4]  # 隔 4 取 1 统计占比（阈值 0.6，正例 0.83–1.0、反例 ≤ 0.18，抽样不影响判定）
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return float(((h >= 88) & (h <= 100) & (s >= 200) & (v >= 180)).mean())


class _Frame:
    """当前这一帧的白色掩码 / 标题条（quick_page、fallback_page 开头 reset，各检查共用，不重复转色彩空间）。"""

    img = None
    white = None
    bands = None

    @classmethod
    def reset(cls, img) -> None:
        cls.img, cls.white, cls.bands = img, None, None

    @classmethod
    def white_mask(cls, img) -> np.ndarray | None:
        if img is not cls.img:
            return None
        if cls.white is None:
            cls.white = _white(img)
        return cls.white

    @classmethod
    def header_bands(cls, img) -> list[tuple[int, int]]:
        from hiro.vision.pages import _header_bands

        if img is not cls.img:
            return _header_bands(img)
        if cls.bands is None:
            cls.bands = _header_bands(img)
        return cls.bands


def _white_hit(img: MatLike, name: str) -> bool:
    (x1, y1, x2, y2), thr = WHITE_TPL[name]
    full = _Frame.white_mask(img)
    region = full[y1:y2, x1:x2] if full is not None else _white(img[y1:y2, x1:x2])  # 逐像素阈值：整帧掩码切片与区域单算完全一致
    return float(cv2.matchTemplate(region, _white_tpl(name), cv2.TM_CCOEFF_NORMED).max()) >= thr


def _pale(hsv: np.ndarray) -> float:
    hsv = hsv[::4, ::4]  # 隔 4 取 1（阈值 0.5，正例 0.65–1.0、反例 ≤ 0.24）
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return float(((h >= 88) & (h <= 108) & (s >= 15) & (s <= 90) & (v >= 200)).mean())


def _white_rows(hsv: np.ndarray) -> np.ndarray:
    x1, x2 = WHITE_X
    return ((hsv[:, x1:x2, 2] >= 235) & (hsv[:, x1:x2, 1] <= 25)).mean(axis=1) >= 0.85


def _runs(flags: np.ndarray, y1: int, y2: int, min_len: int) -> list[tuple[int, int]]:
    out, start = [], None
    for y in range(y1, y2):
        if flags[y] and start is None:
            start = y
        elif not flags[y] and start is not None:
            if y - start >= min_len:
                out.append((start, y))
            start = None
    if start is not None and y2 - start >= min_len:
        out.append((start, y2))
    return out


def _acquire_bar(hsv: np.ndarray) -> bool:
    x1, x2 = ACQ_BAR_X
    w = ((hsv[ACQ_BAR_Y[0] : ACQ_BAR_Y[1], x1:x2, 2] >= 230) & (hsv[ACQ_BAR_Y[0] : ACQ_BAR_Y[1], x1:x2, 1] <= 30)).mean(axis=1) >= 0.6
    return ACQ_BAR_ROWS[0] <= int(w.sum()) <= ACQ_BAR_ROWS[1]


PITEM_NOTE_Y = (380, 720)  # P 道具效果说明浮条出现的范围
PITEM_NOTE_H = (80, 200)
PITEM_NOTE_BORDER_X = ((20, 30), (690, 700))  # 左右白色竖边
PITEM_NOTE_TAP = (360, 250)  # 浮条上方空白：(12,12) 点不掉（执行 23e6965，第二十三局 SP 课后停 78 秒）


def _pitem_note(hsv: np.ndarray) -> tuple[int, int] | None:
    """P 道具效果说明浮条（彩色渐变底、白色圆角边，横贯全宽，文字随道具变）：整行高亮的连续带 + 左右两道白色竖边 → (y1, y2)。"""
    y0, y3 = PITEM_NOTE_Y
    bright = (hsv[y0:y3, 60:660, 2] >= 215).mean(axis=1) >= 0.6
    runs, start = [], None
    for i, f in enumerate(list(bright) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            runs.append((start, i))
            start = None
    for a, b in runs:
        y1, y2 = y0 + a, y0 + b
        if not PITEM_NOTE_H[0] <= y2 - y1 <= PITEM_NOTE_H[1]:
            continue
        band = hsv[y1 + 5 : y2 - 5]
        white = (band[:, :, 1] < 35) & (band[:, :, 2] >= 235)
        if not all(white[:, x1:x2].mean(axis=0).max() >= 0.9 for x1, x2 in PITEM_NOTE_BORDER_X):
            continue
        # 孤立的一条：上下各 30 行都不是高亮（特別指導卡格 / 相談详情 / 选饮料弹窗的横带都和上下面板连成一片，全帧 20 帧误检因此排除）
        above = (hsv[max(0, y1 - 35) : y1 - 5, 60:660, 2] >= 215).mean(axis=1)
        below = (hsv[y2 + 5 : y2 + 35, 60:660, 2] >= 215).mean(axis=1)
        if above.size and below.size and above.max() < 0.3 and below.max() < 0.3:
            return y1, y2
    return None


RECEIVE_BTN_BOX = (250, 1050, 470, 1130)  # 选饮料弹窗底部「受け取る」橙色按钮
RECEIVE_BTN_MIN = 0.4  # 橙色占比：弹窗 0.85，礼物箱画面 ≤ 0.14（全部帧 265 张 GIFT）


def _receive_button(hsv: np.ndarray) -> bool:
    """差し入れ 的选饮料弹窗（浅灰底，达不到白色面板判定）：底部中间有「受け取る」橙色按钮。
    原来被认成 GIFT，read_gift 取最大亮色块正好是这颗按钮 → 没经过决策就收下了（第二十一局选拔 #47、r3 #4）。"""
    x1, y1, x2, y2 = RECEIVE_BTN_BOX
    b = hsv[y1:y2, x1:x2]
    return float(((b[:, :, 0] >= 3) & (b[:, :, 0] <= 20) & (b[:, :, 1] >= 100) & (b[:, :, 2] >= 150)).mean()) >= RECEIVE_BTN_MIN


def _refresh_dialog(img: MatLike) -> int | None:
    """「リフレッシュ確認」：带白字标题的橙黄标题条里对上标题模板 → 标题条中心 y；不是返回 None（不做 OCR，约 20 ms）。"""
    from hiro.vision.pages import _title_white

    tpl = _white_tpl("quick_refresh_title")
    for y1, y2 in reversed(_Frame.header_bands(img)):
        if not _title_white(img, y1, y2):
            continue
        reg = _white(img[max(0, y1 - 6) : y2 + 6, 40:420])
        if reg.shape[0] >= tpl.shape[0] and float(cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED).max()) >= REFRESH_TITLE_MIN:
            return (y1 + y2) // 2
    return None


def _dialog(img: MatLike) -> bool:
    """确认框 / 弹窗：橙黄标题条且左段有白色标题字（同 pages.read_dialog 的像素判据，不做 OCR）。
    课程画面底部的木地板、获得演出名称栏的彩色边框也会形成「标题条」，但没有白字。"""
    from hiro.vision.pages import TITLE_WHITE_MIN

    for y1, y2 in _Frame.header_bands(img):
        if y1 >= DIALOG_HEADER_MAX_Y:
            continue  # 画面最底部（课程画面的木地板 + 白色饮料栏图标）不会是确认框标题条
        hsv = cv2.cvtColor(img[y1:y2, 40:320], cv2.COLOR_BGR2HSV)
        if ((hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 235)).mean() >= TITLE_WHITE_MIN:
            return True
    return False


def quick_page(img: MatLike) -> dict | None:
    """→ {page, tap, dispatch, fields, ms} 或 None（见模块说明）。"""
    from hiro.vision.pitem_select import is_select_pitem

    if img is None or img.shape[0] < img.shape[1]:
        _Frame.reset(None)
        return None  # LIVE 横屏帧（1280x720）：区域都按竖屏 720x1280 定，裁出来是空图（第十八局每帧抛 cvtColor !_src.empty()）
    _Frame.reset(img)
    t0 = time.perf_counter()

    def done(page: str, *, tap=None, dispatch=None, fields=None) -> dict:
        return {"page": page, "tap": tap, "dispatch": dispatch, "fields": fields or {}, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    from hiro.vision.deck import is_deck_list

    if is_deck_list(img):
        # 牌组一览（「所持スキルカード」）挂在画面上：排在 P 道具三选一之前——KAA 的 TextPItem 会命中一览底部「Pアイテム」标签
        #（第二十局开局停机，总控 2026-11-03）。主循环不在这里读牌（读整副由执行的 deck_review 自己开 / 滑 / 关），点「閉じる」。
        return done("DECK_LIST", tap=DECK_LIST_CLOSE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if is_select_pitem(img) or _hit(gray, "quick_pitem_custom_title"):
        return done("SELECT_PITEM", dispatch="SELECT_PITEM")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    if _pale(hsv) >= LOADING_PALE_MIN:
        return done("LOADING")  # 读取 / 过场：不点，等一下（原来每轮 KAA + 整帧 OCR 3–6 秒，过场结束后要多等一轮）
    if _white_hit(img, "quick_event_period"):  # 开源版不带活动 Logo 美术模板，只靠「開催期間」白字（全帧验证可单独认出）
        # 日切换（04:00）后的活动登录奖励弹窗（每周三「学マス水曜日」：道具图标 + 数量，没有按钮）：点空白就过。
        # 第二十六局开局前收尾阶段卡 180 秒（协调 2026-11-04）；收尾 / 回首页阶段由执行接推进器调 quick_page
        return done("TRANSITION", tap=LOGIN_BONUS_TAP, fields={"kind": "login_bonus"})
    if _white_hit(img, "quick_now_loading") or _white_hit(img, "quick_now_loading2"):
        return done("LOADING")  # 不是浅蓝底的读取画面（HIF 蓝紫 / 「移動中」）：第十七局选拔 #395 #431
    if _cyan(hsv) >= FLASH_CYAN_MIN:
        return done("TRANSITION", fields={"kind": "flash"})  # 整屏青色闪屏：不点
    if _white_hit(img, "quick_custom_cutin"):
        return done("TRANSITION", tap=SKIP_TAP, fields={"kind": "custom_cutin"})  # 「カスタムPアイテム / カスタム選択」过场，接着是定制后三选一
    if _white_hit(img, "quick_commu_skip") and _white_hit(img, "quick_commu_auto"):
        (x1, y1, x2, y2), _ = WHITE_TPL["quick_commu_skip"]
        return done("commu", tap=((x1 + x2) // 2, (y1 + y2) // 2))  # 剧情：点 SKIP（之后的「スキップ確認」框交给原流程）
    if _acquire_bar(hsv):  # 名称栏的彩色边框会被当成「标题条」，所以在确认框判断之前
        from hiro.vision.deck import read_acquire_popup

        acquired = read_acquire_popup(img, art=False)
        if acquired is not None:
            return done("ACQUIRE", tap=SKIP_TAP, fields=acquired)
    if (ry := _refresh_dialog(img)) is not None:
        # 刷新货架的「リフレッシュ確認」：刷新动作（shop.reroll_dialog）自己会处理并按「更新する」；落到主循环这里的都是
        # 没有刷新决策时挂着的框 → 一律キャンセル（不花 P、不换掉已规划的货架；决策要刷会再给 Reroll）。
        # 第十八局本战轮间它挂了 30 秒没人处理（总控 2026-11-02，约定同执行 f0f2d10）。
        from hiro.vision.pages import read_refresh_confirm

        confirm = read_refresh_confirm(img, ry)
        return done("DIALOG", tap=tuple(confirm["cancel"]),
                    fields={"kind": "refresh_confirm", "title": "リフレッシュ確認", "danger": False, "confirm": confirm})  # fmt: skip
    if _dialog(img):
        return None  # 确认框 / 弹窗：原流程处理
    white = _white_rows(hsv)
    hint = next((name for name in ("quick_tap_en", "quick_tap_ja") if _hit(gray, name)), None)
    hint_xy = None
    if hint is not None:
        (x1, y1, x2, y2), _ = GRAY_TPL[hint]
        hint_xy = ((x1 + x2) // 2, (y1 + y2) // 2)
    if _hit(gray, "quick_pass_label") or _hit(gray, "quick_finals_label"):
        # 考前说明页（选拔「合格条件」/ 本战「優勝条件」）：各属性倍率是本场考试的真值（用户 2026-11-02「读那个最准」）。
        # 「タップして次へ」出来 = 倍率定格：整屏 OCR 读一次（约 1.5 秒）再点提示；之前是ボーナス演出（倍率在跳），不读不点。
        if hint_xy is not None:
            from hiro.vision.pages import _texts, read_exam_briefing

            brief = read_exam_briefing(img, _texts(img))
            if brief["complete"]:
                return done("HIF_EXAM_BRIEFING", tap=hint_xy, fields=brief)
        return done("TRANSITION", fields={"kind": "exam_bonus"})
    if hint_xy is not None:
        return done("TRANSITION", tap=hint_xy, fields={"kind": "tap_hint"})
    if _white_hit(img, "quick_tap_de_next") and _white_hit(img, "quick_select_eval_title"):
        (x1, y1, x2, y2), _ = WHITE_TPL["quick_tap_de_next"]
        return done("TRANSITION", tap=((x1 + x2) // 2, (y1 + y2) // 2), fields={"kind": "tap_hint"})  # 选拔试验评价「TAPで次へ」（本战最终评价不碰）
    if _white_hit(img, "quick_star_gain"):
        from hiro.vision.pages import read_star_gain

        return done("STAR_GAIN", tap=SKIP_TAP, fields=read_star_gain(img))  # スター性獲得：读本次 +N（约 0.5 秒），点 (12,12)
    for name, kind in (("quick_select_bonus", "select_bonus"), ("quick_toppa", "toppa"), ("quick_custom_pitem_get", "custom_pitem_get"),
                       ("quick_custom_pitem", "custom_pitem")):  # fmt: skip
        if _white_hit(img, name):
            return done("TRANSITION", tap=SKIP_TAP, fields={"kind": kind})  # 第二十一局选拔：点 (12,12) 就过的演出
    if _hit(gray, "quick_hud_label") and not _hit(gray, "quick_gift_label") and _pitem_note(hsv) is not None:
        # 「差し入れ」标签在 = 礼物 / 选饮料流程（浮条叠在选饮料弹窗上时点上方可能点到弹窗外），交给原流程
        return done("TRANSITION", tap=PITEM_NOTE_TAP, fields={"kind": "pitem_note"})  # P 道具效果说明浮条：点上方空白（叠在相談上时点的是顶部说明面板，无副作用）
    if _hit(gray, "quick_gift_label") and _hit(gray, "quick_drink_gain"):
        # 礼物袋已打开、「Pドリンク獲得」光效：接着淡入饮料三选一弹窗。光效是最大的高亮色块，原来被当礼物袋点，
        # 点击落在淡入中的弹窗说明文字上（第十八局 #21 #22，执行 23e6965 报）。不点，等弹窗。
        return done("TRANSITION", fields={"kind": "gift_effect"})
    if _hit(gray, "quick_gift_label") and not _runs(white, *PANEL_Y, 40) and not _receive_button(hsv):
        from hiro.vision.pages import read_gift

        bag = read_gift(img)["bag"]
        if bag is not None:
            return done("GIFT", tap=tuple(bag), fields={"bag": bag})
    if _schedule_overlay(hsv):
        return done("TRANSITION", tap=SCHEDULE_CLOSE, fields={"kind": "schedule"})  # 日程表覆盖层：点底部 ✕ 关掉
    runs = _runs(white, *BUBBLE_Y, 40)
    if runs and not _runs(white, *PANEL_Y, 40) and _hit(gray, "quick_hud_label"):  # 剧情对话框 / 事件过场 / 主页菜单下方也有白块
        # 有字的行白色占比不到 0.85：从白色段往上扩到占比 < 0.5，才是气泡上沿（第一行字在上面）
        x1, x2 = WHITE_X
        frac = ((hsv[:, x1:x2, 2] >= 235) & (hsv[:, x1:x2, 1] <= 25)).mean(axis=1)
        top = runs[0][0]
        while top > PANEL_Y[0] and frac[top - 1] >= 0.5:
            top -= 1
        page, fields = read_bubble(img, top, runs[-1][1])
        return done(page, tap=SKIP_TAP, fields=fields)
    return None


def _schedule_overlay(hsv) -> bool:
    """日程表覆盖层：标签栏那几十行每行青色占比 ≥ 0.3（渐变青→蓝，只数青的一段），且底部 ✕ 圆钮那块大半是白色。"""
    y1, y2 = SCHEDULE_BAR_Y
    bar = hsv[y1:y2:6, 20:700]
    cyan = ((bar[..., 0] >= 85) & (bar[..., 0] <= 100) & (bar[..., 1] >= 120) & (bar[..., 2] >= 150)).mean(axis=1)
    if (cyan >= 0.3).mean() < 0.9:
        return False
    x1, cy1, x2, cy2 = SCHEDULE_CLOSE_BOX
    btn = hsv[cy1:cy2, x1:x2]
    return float(((btn[..., 2] >= 235) & (btn[..., 1] <= 25)).mean()) >= 0.5


def split_card_names(text: str) -> list[dict] | None:
    """「仕草の基本と距離感の基本+」→ 两张卡。卡名里也可能有「と」（ありがとうの言葉），按每个「と」试切，两边都精确命中卡名才算。"""
    from hiro.vision.deck import card_from_name

    text = text.replace(" ", "").replace("＋", "+")
    if (one := card_from_name(text)) is not None:
        return [one]
    for i, ch in enumerate(text):
        if ch == "と" and 0 < i < len(text) - 1:
            left, right = card_from_name(text[:i]), split_card_names(text[i + 1 :])
            if left is not None and right:
                return [left, *right]
    return None


def read_bubble(img: MatLike, y1: int, y2: int) -> tuple[str, dict]:
    """底部消息气泡的文字（局部 OCR，约 50 ms）→ (页面名, 字段)：
    「〜を強化しました」→ UPGRADE_RESULT；「〜をカスタマイズしました」→ CUSTOMIZE_RESULT；
    「A と B を削除しました」→ DELETE_RESULT {cards[{card_id, name, upgrade}], text}（考试后自动处理基础卡 / 相談删卡；认不全时 cards=None）；
    其余 → TRANSITION {kind: message, text}。"""
    from hiro.vision.ocr import ocr
    from hiro.vision.pages import read_upgrade_result

    text = "".join(t for t, _ in ocr(img[max(0, y1 - 10) : y2 + 10, 30:690], scale=2)).replace(" ", "")
    if "を強化しました" in text:
        return "UPGRADE_RESULT", read_upgrade_result(text[: text.index("を強化しました") + len("を強化しました")])
    if "をカスタマイズしまし" in text:
        from hiro.vision.customize import read_customize_result

        return "CUSTOMIZE_RESULT", read_customize_result(text)
    if m := re.search(r"を削.しまし", text):  # OCR 偶尔把「除」读成「味」
        return "DELETE_RESULT", {"cards": split_card_names(text[: m.start()]), "text": text}
    return "TRANSITION", {"kind": "message", "text": text}


def fallback_page(img: MatLike) -> str | None:
    """KAA check_scene 判 UNKNOWN 之后的兜底（只看模板 / 像素，约 20 ms）：认出交给主循环原来的处理 → SceneType 名，认不出 None。
    不放进 quick_page：那里排在 KAA 之前，会改掉 KAA 本来认得出的帧的处理路径。
    - ACTION_SELECT：右侧「P手帳」标签（第二十一局选拔 #91 #103：顶部有提示气泡 / 淡入时 KAA 判不出）；
    - PDRINK_MAX：「Pドリンク所持上限」标题（第二十一局选拔 r2 #87 等：列表完整显示仍判 unknown）。"""
    if img is None or img.shape[0] < img.shape[1]:
        return None
    _Frame.reset(img)
    if _white_hit(img, "quick_pdrink_max_title") and _dialog(img):
        return "PDRINK_MAX"
    if _hit(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), "quick_ptecho_label") and not _dialog(img):
        return "ACTION_SELECT"
    return None
