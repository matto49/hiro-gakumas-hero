"""快速判页（协调 2026-11-02：每局 4–7 次「页面认不出超过 30 秒」）。帧取自第十五～十七局 trace，真值按截图目视。"""

import glob
from pathlib import Path
from types import SimpleNamespace as NS

import hiro  # noqa: F401
from hiro.vision.quick import quick_page, split_card_names

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures")


def _q(rel):
    return quick_page(cv2.imread(str(FIX / rel)))


@pytest.mark.parametrize(
    ("rel", "page", "tap", "dispatch"),
    [("quick/pitem-select-q15-153.jpg", "SELECT_PITEM", None, "SELECT_PITEM"),  # KAA 判 IDLE，原来 73 秒
     ("quick/pitem-custom-q16-286.jpg", "SELECT_PITEM", None, "SELECT_PITEM"),  # 「カスタム後を選択してください」
     ("quick/gift-q15-18.jpg", "GIFT", "bag", None),
     ("quick/acquire-drink-q15-75.jpg", "ACQUIRE", (12, 12), None),  # 课程结束拿饮料
     ("quick/acquire-memory-q15-147.jpg", "ACQUIRE", (12, 12), None),  # メモリー効果
     ("quick/bonus-tap-q16-144.jpg", "TRANSITION", (360, 1220), None),  # 选拔奖励「TAP」
     ("quick/briefing-tap-q17-123.jpg", "HIF_EXAM_BRIEFING", (365, 1200), None),  # 合格条件「タップして次へ」：先读倍率再点
     ("quick/param-bubble-q15-214.jpg", "TRANSITION", (12, 12), None),  # 「Vo.が87上昇した!」
     ("quick/auto-delete-q15-150.jpg", "DELETE_RESULT", (12, 12), None)],  # 考试后自动删基础卡
)  # fmt: skip
def test_quick_pages(rel, page, tap, dispatch):
    q = _q(rel)
    assert (q["page"], q["dispatch"]) == (page, dispatch)
    assert q["tap"] == tuple(q["fields"]["bag"]) if tap == "bag" else q["tap"] == tap


def test_bubble_events():
    """气泡文字：考试后自动处理基础卡 → DELETE_RESULT（事件 CARD_DELETE），卡名里的「と」按主数据切开。"""
    q = _q("quick/auto-delete-q17-159.jpg")
    assert [(c["name"], c["upgrade"]) for c in q["fields"]["cards"]] == [
        ("アイコンタクトの基本", 0),
        ("距離感の基本", 1),
    ]
    assert [c["name"] for c in split_card_names("ありがとうの言葉と距離感の基本")] == [
        "ありがとうの言葉",
        "距離感の基本",
    ]
    assert _q("pages/upgrade_result.jpg")["page"] == "UPGRADE_RESULT"
    assert _q("guidance/q10-after-exec.jpg")["page"] == "CUSTOMIZE_RESULT"


@pytest.mark.parametrize(
    ("rel", "kind", "tap"),
    [("quick/flash-q17-7.jpg", "flash", None),  # 整屏青色闪屏（选拔 #10 #46）
     ("quick/flash-wipe-q17-8.jpg", "flash", None),  # 闪屏擦入（上沿还露着教室）
     ("quick/custom-cutin-q17-473.jpg", "custom_cutin", (12, 12)),  # 「カスタム選択」过场（选拔 #475）
     ("quick/exam-bonus-q17-121.jpg", "exam_bonus", None),  # 考前说明页ボーナス演出中，提示还没出来（选拔 #125 #431）：不读不点
     ("quick/finals-briefing-anim-main17-89.jpg", "exam_bonus", None),  # 本战「優勝条件」说明页，倍率还没出来
     ("quick/loading-move-q17-390.jpg", None, None),  # 「移動中」读取（选拔 #395）
     ("quick/loading-hif-q17-424.jpg", None, None)],  # HIF 蓝紫读取（选拔 #431）
)  # fmt: skip
def test_wait_pages_q17(rel, kind, tap):
    """第十七局选拔逐帧核对补的过场：原来每轮 KAA + 整帧 OCR 要 8–10 秒才再看一次。读取 / 闪屏不点；
    「カスタム選択」点 (12,12)（认出后 advance 不再连点，所以这里自己点）；考前说明页演出中不点，等倍率定格。"""
    q = _q(rel)
    assert q["tap"] == tap and q["dispatch"] is None
    assert (q["page"], q["fields"].get("kind")) == (("TRANSITION", kind) if kind else ("LOADING", None))


@pytest.mark.parametrize(("rel", "page", "tap"), [("quick/not-commu-181.png", "commu", (225, 1200)), ("quick/not-cutin-184.png", "LOADING", None),
                                                  ("quick/not-home-menu.png", None, None)])  # fmt: skip
def test_not_bubble_without_produce_hud(rel, page, tap):
    """剧情对话框、支援事件过场、主页菜单下方也有白块：没有右上「審査基準」就不当消息气泡点。
    剧情按底栏 SKIP + AUTO 白字认出、点 SKIP；浅蓝花纹底的过场认成 LOADING（不点，等）；主页菜单不碰。"""
    q = _q(rel)
    assert (q["page"], q["tap"]) == (page, tap) if page else q is None


def test_decision_pages_untouched():
    """决策页（选行动 / 三选一 / 考试 / 相談 / 确认框 / 特別指導 / 删卡对话框…）一律 None，交给原流程。"""
    hits = []
    for pat in ("scenes/*.jpg", "select_card/*.jpg", "hif_hud/*.jpg", "shop/consult*.jpg", "pages/dialog-*.jpg", "guidance/q10-enter.jpg",
                "action/*.jpg", "finals/exam-*.jpg", "deck/removal-q14-0*.jpg"):  # fmt: skip
        for f in sorted(glob.glob(str(FIX / pat))):
            img = cv2.imread(f)
            if img is None or img.shape[:2] != (1280, 720):
                continue
            q = quick_page(img)
            if q is not None and q["page"] not in ("ACQUIRE", "UPGRADE_RESULT", "DELETE_RESULT"):
                hits.append((Path(f).name, q["page"]))
    assert hits == []


def _rt():
    taps = []
    return NS(device=NS(tap=lambda x, y: taps.append((x, y))), _quick_wait=None), taps


def test_runtime_quick_tap(monkeypatch):
    """新主循环（hiro.runtime.play._quick_tap，原 HiroController._quick_page）：点一下就过的页面直接点；
    P 道具页交给 SELECT_PITEM；认不出返回 None。"""
    from hiro.runtime import play

    rt, taps = _rt()
    q = quick_page(cv2.imread(str(FIX / "quick/gift-q15-18.jpg")))
    assert q is not None and q["page"] == "GIFT" and play._quick_tap(rt, q, 0.0) and len(taps) == 1
    q = quick_page(cv2.imread(str(FIX / "quick/pitem-custom-q16-286.jpg")))
    assert q is not None and q.get("dispatch") == "SELECT_PITEM"
    assert quick_page(cv2.imread(str(FIX / "scenes/00041-ACTION_SELECT.jpg"))) is None


def test_battle_drinks_unknown_or_empty_slots_give_nothing(monkeypatch):
    """考试中饮料栏全空或有拿不准的格（unknown）：都只给认出的饮料，没有就是空（原来 unknown 时退回 KAA，KAA 认不出 H.I.F 饮料，已删）。"""
    from hiro.exec import exam as battle  # 考试执行原语（主仓库 hiro.produce.battle 的开源部分）

    empty = [{"slot": i, "state": "empty", "asset": None, "center": [62 + 85 * i, 1200]} for i in range(4)]
    monkeypatch.setattr("hiro.vision.items.read_drink_slots", lambda img, cache=None: empty)
    assert battle._drinks(None) == []
    monkeypatch.setattr(
        "hiro.vision.items.read_drink_slots", lambda img, cache=None: [dict(empty[0], state="unknown")] + empty[1:]
    )
    assert battle._drinks(None) == []


@pytest.mark.parametrize(
    ("rel", "exam_no", "pct"),
    [("quick/briefing-tap-q17-123.jpg", 1, {"Vo": 1413, "Da": 1467, "Vi": 860}),  # 选拔 #125
     ("quick/briefing-tap-q17-427.jpg", 3, {"Vo": 2408, "Da": 3029, "Vi": 1616})],  # 选拔 #431：OCR 把「>>」读成首位「23029%」
)  # fmt: skip
def test_exam_briefing_truth(rel, exam_no, pct):
    """考前说明页倍率 = 本场考试真值（用户 2026-11-02「读那个最准」）：「タップして次へ」出来（定格）时先读再点提示。"""
    from hiro.vision.observe import observation

    q = _q(rel)
    assert (q["page"], q["tap"]) == ("HIF_EXAM_BRIEFING", (365, 1200))
    f = q["fields"]
    assert f["complete"] is True and f["finals"] is False and f["exam_no"] == exam_no and f["multiplier_pct"] == pct
    obs = observation(1, 0.0, "HIF_EXAM_BRIEFING", f)
    assert obs.fields["multiplier_pct"] == pct


def test_runtime_briefing_waits_then_taps():
    """考前说明页演出中不点（等定格），停太久（QUICK_WAIT_TAP_S）才点 (12,12) 兜底；定格帧点「タップして次へ」。
    原来测 HiroController._quick_page，2026-11-24 改测新主循环 hiro.runtime.play._quick_tap（倍率读数见 test_exam_briefing_truth）。"""
    from hiro.runtime import play

    rt, taps = _rt()
    q = quick_page(cv2.imread(str(FIX / "quick/exam-bonus-q17-121.jpg")))
    assert q is not None and q.get("tap") is None
    assert not play._quick_tap(rt, q, 0.0) and not play._quick_tap(rt, q, play.QUICK_WAIT_TAP_S - 1) and taps == []
    assert play._quick_tap(rt, q, play.QUICK_WAIT_TAP_S + 0.1) and taps == [(12, 12)]
    q = quick_page(cv2.imread(str(FIX / "quick/briefing-tap-q17-123.jpg")))
    assert play._quick_tap(rt, q, play.QUICK_WAIT_TAP_S + 1) and taps[-1] == (365, 1200)


@pytest.mark.parametrize(("rel", "page", "tap"), [("quick/gift-drink-gain-q18-21.jpg", "TRANSITION", None),  # 金色光效（第十八局 #21）
                                                  ("quick/gift-drink-gain-q16-16.jpg", "TRANSITION", None),  # 彩色光效
                                                  ("quick/gift-open-q18-20.jpg", "GIFT", "bag")])  # 袋子还没打开：照常点  # fmt: skip
def test_gift_drink_gain_not_tapped(rel, page, tap):
    """礼物袋打开后的「Pドリンク獲得」光效不当礼物袋点（原来点到淡入中的饮料三选一弹窗说明文字上，执行 23e6965 报）。"""
    q = _q(rel)
    assert q["page"] == page and (q["tap"] == tuple(q["fields"]["bag"]) if tap == "bag" else q["tap"] is None)
    if page == "TRANSITION":
        assert q["fields"]["kind"] == "gift_effect"


def test_landscape_frame_skipped():
    """LIVE 横屏帧（宽 > 高）直接 None，不抛异常（第十八局 LIVE 期间每帧 cvtColor !_src.empty()）。"""
    import numpy as np

    live = cv2.imread(str(FIX / "quick/live-landscape-q17-511.jpg"))
    assert live.shape[:2] == (720, 1280) and quick_page(live) is None  # 实机 LIVE 帧
    assert quick_page(np.zeros((720, 1280, 3), np.uint8)) is None
    assert quick_page(cv2.rotate(cv2.imread(str(FIX / "quick/gift-q15-18.jpg")), cv2.ROTATE_90_CLOCKWISE)) is None


@pytest.mark.parametrize(
    ("rel", "page"),
    [("quick/fb-action-bubble-q21-103.jpg", "ACTION_SELECT"),  # 顶部提示气泡「Pポイントがありますね…」，KAA 判 UNKNOWN
     ("quick/fb-action-fadein-q21r2-31.jpg", "ACTION_SELECT"),  # 行动按钮淡入中
     ("quick/fb-pdrink-max-q21r2-87.jpg", "PDRINK_MAX"),  # 「Pドリンク所持上限」列表完整显示仍判 unknown
     ("quick/gift-q15-18.jpg", None), ("quick/briefing-tap-q17-123.jpg", None), ("quick/exam-bonus-q17-121.jpg", None)],
)  # fmt: skip
def test_fallback_page_q21(rel, page):
    """KAA 判 UNKNOWN 之后的像素兜底（第二十一局选拔，执行 23e6965 第 9 / 10 类）：认出交给主循环原来的处理。"""
    from hiro.vision.quick import fallback_page

    assert fallback_page(cv2.imread(str(FIX / rel))) == page


def test_fallback_page_not_on_decision_pages():
    """其他决策页 / 考试页不会被兜底认成选行动或饮料溢出（全部 17,595 帧：选行动以外的页面「P手帳」≤ 0.47）。"""
    from hiro.vision.quick import fallback_page

    hits = []
    for pat in (
        "scenes/*.jpg",
        "select_card/*.jpg",
        "hif_hud/*.jpg",
        "shop/consult*.jpg",
        "pages/dialog-*.jpg",
        "finals/*.jpg",
    ):
        for f in sorted(glob.glob(str(FIX / pat))):
            img = cv2.imread(f)
            if img is None or img.shape[:2] != (1280, 720):
                continue
            fb = fallback_page(img)
            if fb is not None and not ("ACTION_SELECT" in Path(f).name and fb == "ACTION_SELECT"):
                hits.append((Path(f).name, fb))
    assert hits == []


@pytest.mark.parametrize(
    ("rel", "page", "kind", "tap"),
    [("quick/q21-select-bonus.jpg", "TRANSITION", "select_bonus", (12, 12)),  # 「選抜試験ボーナス / パラメータボーナス」
     ("quick/q21-toppa.jpg", "TRANSITION", "toppa", (12, 12)),  # 「選抜試験 第1回 突破」
     ("quick/q21-custom-pitem.jpg", "TRANSITION", "custom_pitem", (12, 12)),  # 「カスタムPアイテム / アイテム獲得」
     ("quick/q21-custom-pitem-get.jpg", "TRANSITION", "custom_pitem_get", (12, 12)),  # 「カスタムPアイテム獲得」
     ("quick/q21-tap-de-next.jpg", "TRANSITION", "tap_hint", (360, 1150)),  # 选拔试验评价「TAPで次へ」
     ("quick/q21-star-gain.jpg", "STAR_GAIN", None, (12, 12))],  # 「スター性獲得」：读本次 +15
)  # fmt: skip
def test_q21_tap_pages(rel, page, kind, tap):
    """第二十一局选拔 unknown 帧里点一下就过的演出（执行 23e6965 清单 1 / 2 / 5 + 评价页、スター性獲得）。"""
    q = _q(rel)
    assert (q["page"], q["fields"].get("kind"), q["tap"], q["dispatch"]) == (page, kind, tap, None)
    if page == "STAR_GAIN":
        assert q["fields"]["gain"] == 15


def test_final_produce_eval_left_to_kaa():
    """本战「最終プロデュース評価」（同样有「TAPで次へ」）KAA 认作 PRODUCE_END、走它的结束流程：快速判页不截走（全帧对比发现 9 帧）。"""
    assert _q("quick/not-final-eval-main15-284.jpg") is None


@pytest.mark.parametrize("rel", ["quick/pitem-note-main23-a.jpg", "quick/pitem-note-main23-b.jpg"])
def test_pitem_note(rel):
    """P 道具效果说明浮条（第二十三局 SP 课后，(12,12) 点不掉、停 78 秒，执行 23e6965）：点上方空白 (360,250)。"""
    q = _q(rel)
    assert (q["page"], q["fields"].get("kind"), q["tap"]) == ("TRANSITION", "pitem_note", (360, 250))


@pytest.mark.parametrize(
    "rel", ["quick/not-gift-drink-popup-q21-47.jpg", "quick/not-gift-drink-popup-note-q21r3-4.jpg"]
)
def test_drink_popup_not_gift(rel):
    """差し入れ 选饮料弹窗（浅灰底 + 底部「受け取る」）：原来被认成 GIFT、点了「受け取る」(360,1077)，没经过决策就收下饮料。
    现在不碰（第二张还叠着 P 道具效果说明浮条，也不点），交给原来的选饮料流程。"""
    assert _q(rel) is None


def test_login_bonus_gakumas_wed():
    """日切换后的活动登录奖励「学マス水曜日」（道具图标 + 数量，没有按钮）：第二十六局开局前收尾阶段卡 180 秒。
    点底部空白 (360,1180)（正中央是道具框，点它不翻页）。"""
    q = _q("quick/login-bonus-gakumas-wed.png")
    assert (q["page"], q["fields"].get("kind"), q["tap"]) == ("TRANSITION", "login_bonus", (360, 1180))
