# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""kaa_patches 授業换卡补丁的离线测试：用 KAA 自带的 H.I.F 换卡参考截图（阶段 1 / 阶段 2）喂离线设备。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.loop import kaa_patches
from hiro.produce import card_ops
from hiro.produce import memory_select as ms
from hiro.vision.offline import load

import cv2
import kaa.tasks
import numpy as np
import pytest

REFS = (
    Path(kaa.tasks.__file__).parents[2] / "kotonebot-resource" / "sprites" / "jp" / "in_produce"
)  # 已安装的 KAA（vendor/kaa）
STAGE1 = REFS / "dialog_card_change_1.png"
STAGE2 = REFS / "dialog_card_change_2.png"

pytestmark = pytest.mark.skipif(not STAGE1.exists(), reason="找不到 KAA 资源里的换卡参考截图")


def _shift(img, dy):
    return cv2.warpAffine(img, np.float32([[1, 0, 0], [0, 1, dy]]), (720, 1280), borderMode=cv2.BORDER_REPLICATE)


class RecordingDevice:
    """截图转给离线 MockDevice，点击只记录（MockDevice 没有输入实现）。"""

    def __init__(self):
        from kotonebot.backend.context import device

        self._real = device
        self.clicks = []

    def screenshot(self):
        return self._real.screenshot()

    def click(self, *args):
        self.clicks.append(args[0] if len(args) == 1 else tuple(args))

    def swipe(self, *args, **kw):  # 换卡阶段 2 目标不在这一屏时翻屏找；离线画面不变（= 到底 / 到顶）
        self.swipes = getattr(self, "swipes", []) + [args]


@pytest.fixture
def dev(monkeypatch):
    rec = RecordingDevice()
    monkeypatch.setattr(card_ops, "device", rec)
    monkeypatch.setattr(card_ops, "CHANGE_STAGE_TIMEOUT", 0.6)
    monkeypatch.setattr(card_ops, "CHANGE_POLL", 0.1)
    monkeypatch.setattr(card_ops._change_run, "run", None, raising=False)
    from hiro.produce import ops

    monkeypatch.setattr(ops, "CALIB_DIR", Path(__file__).parent / ".calib-test")  # 不往 reports/ 写
    return rec


@pytest.mark.parametrize("dy", [-30, -15, -8, 0, 8, 15, 30])
def test_stage1_marks_found_despite_offset(dy):
    marks = card_ops.find_card_marks(
        _shift(cv2.imread(str(STAGE1)), dy), card_ops.CHANGE_LETTERS_BOX, card_ops.CHANGE_LETTER_THRESHOLD
    )
    assert len(marks) == 3
    assert [m.rect.x1 for m in marks] == sorted(m.rect.x1 for m in marks)


def test_stage1_clicks_leftmost_candidate(dev):
    img = _shift(cv2.imread(str(STAGE1)), 12)
    load(img)
    leftmost = card_ops.find_card_marks(img, card_ops.CHANGE_LETTERS_BOX, card_ops.CHANGE_LETTER_THRESHOLD)[0]
    assert card_ops.change_stage1(None, 0) is False  # 离线画面不会前进到阶段 2，超时返回而不是抛异常
    assert dev.clicks, "应当点了候选卡"
    assert set(dev.clicks) == {tuple(leftmost.center)}


def test_stage2_on_stage1_screen_returns_false_without_raising(dev):
    load(cv2.imread(str(STAGE1)))
    assert card_ops.change_stage2(None, 0) is False
    assert dev.clicks == []


@pytest.fixture
def no_confirm(monkeypatch, dev):
    """离线画面不会自己变：点了卡就当作阶段 2 已结束（标题消失），没有确认框。
    以前靠「什么都没认出 = 完成」放行，第二十局实机正是这样把晚弹出的确认框漏掉的。"""
    import hiro.vision.ui_templates as ui

    real_tap, real_tpl = ui.tap, ui.tpl
    monkeypatch.setattr(
        ui,
        "tap",
        lambda name, img, click: (
            False if name == "InProduce.DialogCardChange2.ButtonConfirm" else real_tap(name, img, click)
        ),
    )
    monkeypatch.setattr(card_ops, "confirm_change_dialog", lambda img, run=None: False)

    def tpl(name):
        t = real_tpl(name)
        if name != "InProduce.DialogCardChange2.Title":
            return t
        return type("T", (), {"exists": staticmethod(lambda img: not dev.clicks and t.exists(img))})()

    monkeypatch.setattr(ui, "tpl", tpl)


def test_stage2_clicks_first_unprotected_deck_card(dev, no_confirm):
    """参考图牌组左上第一张是 手拍子（不在保护名单）：点它。"""
    load(cv2.imread(str(STAGE2)))
    assert card_ops.change_stage2(None, 0) is True  # 点卡后阶段 2 标题连续两帧不在、也没有确认框 → 完成
    assert dev.clicks[0] == (140, 697)


def test_stage2_skips_protected_card(dev, no_confirm, monkeypatch):
    """第四局本战：牌组按 id 排序，第 0 张是核心卡时不能换——跳到下一张非保护卡。"""
    load(cv2.imread(str(STAGE2)))
    monkeypatch.setattr(
        card_ops, "protected_card_ids", lambda: {"p_card-02-act-1_003", "p_card-02-act-2_049"}
    )  # 手拍子、みんな大好き
    assert card_ops.change_stage2(None, 0) is True
    assert dev.clicks[0] == (286 + 147, 697)  # 第 3 张 輝くキミへ


def test_stage2_uses_decision_card_id(dev, no_confirm):
    load(cv2.imread(str(STAGE2)))
    assert card_ops.change_stage2(None, 0, card_id="p_card-02-men-2_051") is True  # やる気は満点
    assert dev.clicks[0] == (580, 845)


def test_stage2_cancels_when_decision_card_missing(dev, no_confirm):
    load(cv2.imread(str(STAGE2)))
    assert card_ops.change_stage2(None, 0, card_id="p_card-02-act-1_037") is False  # 牌组里没有 デイドリーミング
    assert dev.clicks == [
        card_ops.CHANGE_CANCEL_TAP
    ]  # 翻屏也没找到才取消（画面不变：往下 / 往上各滑一次就判到底 / 到顶）


def test_pick_change_target_rules():
    cards = [{"card_id": None, "name": None}, {"card_id": "a", "name": "A"}, {"card_id": "b", "name": "B"}]
    assert card_ops.pick_change_target(cards, 0, protected={"a"})[0]["card_id"] == "b"
    assert card_ops.pick_change_target(cards, 0, protected={"a", "b"})[0] is None
    assert card_ops.pick_change_target([{"card_id": None}], 0, protected=set())[0] is None  # 认不出：不换
    assert card_ops.pick_change_target(cards, 0, card_id="zz", protected=set())[0] is None


def test_protected_cards_come_from_knowledge():
    from hiro.state.knowledge import YARUKI_CARDS

    ids = card_ops.protected_card_ids()
    for name in ("届いて！", "せのびの魔法", "デイドリーミング", "きらきら紙吹雪"):
        assert any(YARUKI_CARDS[c]["name"] == name for c in ids if c in YARUKI_CARDS)


# ---------------------------------------------------------------- H.I.F P 道具背包三选一

PITEM = Path(__file__).parent / "fixtures" / "pitem"


def _pitem_frames():
    """(灰按钮帧, 亮按钮帧, 拿完后的帧)。亮按钮取标题页同款橙色按钮贴到按钮区。"""
    disabled = cv2.imread(str(PITEM / "hif_select_pitem_r2.jpg"))
    lit = disabled.copy()
    x, y, w, h = card_ops.PITEM_ACQUIRE_BTN
    lit[y : y + h, x : x + w] = cv2.resize(cv2.imread(str(PITEM / "lit_button_title_renkei.png")), (w, h))
    after = disabled.copy()
    after[y - 20 : y + h + 20, x - 20 : x + w + 20] = (120, 40, 20)  # 背包页关闭，按钮区换成别的画面
    return disabled, lit, after


class FakeScreen:
    """按点击推进画面：点背包后变亮（lights=False 时不变），点「受け取る」后换页（acquires=False 时不变）。"""

    def __init__(self, *, lights=True, acquires=True, start="disabled"):
        self.frames = dict(zip(("disabled", "lit", "after"), _pitem_frames()))
        self.state, self.lights, self.acquires = start, lights, acquires
        self.clicks = []
        self.t = 0.0

    def shoot(self):
        return self.frames[self.state]

    def click(self, x, y):
        self.clicks.append((x, y))
        bx, by, bw, bh = card_ops.PITEM_ACQUIRE_BTN
        on_button = bx <= x <= bx + bw and by <= y <= by + bh
        if on_button and self.state == "lit" and self.acquires:
            self.state = "after"
        elif not on_button and self.lights:
            self.state = "lit"

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _commit(screen, index):
    return card_ops.pitem_commit(
        None, index, shoot=screen.shoot, click=screen.click, clock=screen.clock, sleep=screen.sleep
    )


def test_pitem_lit_detection_on_real_frame():
    disabled, lit, _ = _pitem_frames()
    base = card_ops._button_sv(disabled)[1]
    assert not card_ops.acquire_lit(disabled, base)
    assert card_ops.acquire_lit(lit, base)
    assert card_ops.acquire_lit(lit)  # 没有基准时靠绝对阈值


def test_pitem_centers_inside_bag_frames():
    """实机帧上每个点击中心都落在对应背包的彩色内框里；KAA 原来的左点在红框外。"""
    img = cv2.imread(str(PITEM / "hif_select_pitem_r2.jpg"))
    band = cv2.cvtColor(img[740:1000], cv2.COLOR_BGR2HSV)
    masks = [cv2.inRange(band, (0, 90, 120), (10, 255, 255)) | cv2.inRange(band, (165, 90, 120), (180, 255, 255)),
             cv2.inRange(band, (22, 90, 120), (35, 255, 255)), cv2.inRange(band, (40, 90, 120), (75, 255, 255))]  # fmt: skip
    for (cx, cy), m in zip(card_ops.PITEM_CENTERS, masks):
        ys, xs = np.nonzero(m)
        x1, x2, y1, y2 = (
            np.percentile(xs, 0.5),
            np.percentile(xs, 99.5),
            np.percentile(ys, 0.5) + 740,
            np.percentile(ys, 99.5) + 740,
        )
        assert x1 + 20 < cx < x2 - 20 and y1 + 20 < cy < y2 - 20
    red_x2 = np.percentile(np.nonzero(masks[0])[1], 99.5)
    assert red_x2 < 157 + 64  # KAA PITEM_POSTIONS[0] 中心 x=221


@pytest.mark.fixtures
@pytest.mark.parametrize("index", [0, 1, 2])
def test_pitem_commit_clicks_bag_then_acquire(index):
    screen = FakeScreen()
    assert _commit(screen, index) is True
    bx, by, bw, bh = card_ops.PITEM_ACQUIRE_BTN
    assert screen.clicks == [card_ops.PITEM_CENTERS[index], (bx + bw // 2, by + bh // 2)]


@pytest.mark.fixtures
def test_pitem_commit_already_lit_skips_bag():
    screen = FakeScreen(start="lit")
    assert _commit(screen, 1) is True
    assert len(screen.clicks) == 1  # 只点了「受け取る」


@pytest.mark.fixtures
def test_pitem_commit_gives_up_when_button_never_lights():
    screen = FakeScreen(lights=False)
    assert _commit(screen, 2) is False
    assert screen.clicks == [card_ops.PITEM_CENTERS[2]] * card_ops.PITEM_TRIES
    assert screen.t <= card_ops.PITEM_TRIES * (card_ops.PITEM_LIT_TIMEOUT + 1)


@pytest.mark.fixtures
def test_pitem_commit_gives_up_when_acquire_has_no_effect():
    screen = FakeScreen(acquires=False)
    assert _commit(screen, 0) is False
    bx, by, bw, bh = card_ops.PITEM_ACQUIRE_BTN
    assert screen.clicks == [card_ops.PITEM_CENTERS[0]] + [(bx + bw // 2, by + bh // 2)] * card_ops.PITEM_TRIES


@pytest.mark.fixtures
def test_pitem_commit_rejects_bad_index():
    with pytest.raises(ValueError):
        _commit(FakeScreen(), 3)


def test_pitem_patch_installed():
    from kaa.tasks.produce.new.page import PItemSelectContext

    kaa_patches.apply()
    assert PItemSelectContext.commit is card_ops.pitem_commit


def test_discard_dialog_never_confirmed(monkeypatch):
    """KAA do_produce 的「プロデュースデータの破棄」确认被替换：点キャンセル 并抛 ResumeBlocked，绝不点開始する。"""
    import hiro.loop.kaa_patches as kp
    from hiro.loop.resume import CANCEL_BUTTON, ResumeBlocked

    import pytest

    clicks = []
    monkeypatch.setattr(kp, "device", type("D", (), {"click": staticmethod(lambda *a: clicks.append(a))}))
    kp.apply()
    from kaa.tasks import R

    assert R.Produce.BreakProduceDialog.ButtonConfirm is kp.NeverConfirmDiscard
    with pytest.raises(ResumeBlocked):
        R.Produce.BreakProduceDialog.ButtonConfirm.try_click()
    assert clicks == [CANCEL_BUTTON]


def test_blind_double_click_is_blocked():
    """KAA 判页时对剧情二选一 double_click(buttons[0])：被拦下，判 UNKNOWN 并置 BLIND_CHOICE，不点击。"""
    import importlib

    pg = importlib.import_module("kaa.tasks.produce.new.page")
    import hiro.loop.kaa_patches as kp

    from kaa.tasks.produce.new.consts import SceneType

    real = pg.device
    guarded = kp._guard_action_like(lambda self: pg.device.double_click((1, 2)))
    kp.BLIND_CHOICE["pending"] = False
    scene = guarded(object())
    assert scene.type is SceneType.UNKNOWN and kp.BLIND_CHOICE["pending"] and pg.device is real
    kp.BLIND_CHOICE["pending"] = False
    kp.apply()
    assert getattr(pg.ProducePage._check_action_like, "_hiro_guard", False)


# ---------------------------------------------------------------- 删卡 / 强化页（游戏执行-06）

EVENTS = Path(__file__).parent / "fixtures" / "events"
REMOVE_REF = REFS / "screenshot_remove_skill_card.png"
ENHANCE_REF = REFS / "screenshot_skill_card_enhance_dialog.png"
ENHANCE_SEL_REF = REFS / "screenshot_skill_card_enhance_dialog_selected.png"
KYORIKAN = "p_card-02-men-0_039"  # 距離感の基本：第 146 帧里选中的那张


class CardPage:
    """按点击推进的画面：transitions {(状态, 目标点): 新状态}，点在 40px 内命中。OCR 按状态缓存。"""

    def __init__(self, frames, transitions, start):
        self.frames, self.transitions, self.state = frames, transitions, start
        self.clicks, self.t, self._ocr = [], 0.0, {}

    def shoot(self):
        return self.frames[self.state]

    def click(self, x, y):
        self.clicks.append((x, y))
        for (s, (tx, ty)), nxt in self.transitions.items():
            if s == self.state and abs(x - tx) <= 40 and abs(y - ty) <= 40:
                self.state = nxt
                return

    def ocr(self, im):
        from hiro.produce.ops import ocr_boxes

        key = next(k for k, v in self.frames.items() if v is im)
        if key not in self._ocr:
            self._ocr[key] = ocr_boxes(im)
        return self._ocr[key]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def kw(self):
        return {
            "shoot": self.shoot,
            "click": self.click,
            "clock": self.clock,
            "sleep": self.sleep,
            "ocr": self.ocr,
            "calib": False,
            "swipe": self.swipe,
        }

    def swipe(self, *a):
        self.swipes = getattr(self, "swipes", 0) + 1


def consult_delete_frames():
    from hiro.vision.deck import read_card_dialog

    f = {n: cv2.imread(str(EVENTS / p)) for n, p in (("none", "removal-none-00141.jpg"), ("sel6", "removal-sel6-00146.jpg"),
                                                      ("confirm", "delete-confirm-00148.jpg"), ("done", "delete-done-00149.jpg"))}  # fmt: skip
    cards = read_card_dialog(f["none"], card_ops.CARD_OP_REGION["remove"])["cards"]
    target = next(c for c in cards if c["card_id"] == KYORIKAN)
    return f, tuple(target["center"])


def test_remove_card_consult_page_full_flow():
    f, center = consult_delete_frames()
    page = CardPage(
        f, {("none", center): "sel6", ("sel6", (520, 1161)): "confirm", ("confirm", (518, 1161)): "done"}, "none"
    )
    assert card_ops.remove_card(None, KYORIKAN, **page.kw()) is True
    assert page.clicks[0] == center and page.state == "done"


def test_remove_card_without_id_cancels_on_consult_page():
    f, _ = consult_delete_frames()
    page = CardPage(f, {}, "none")
    assert card_ops.remove_card(None, None, **page.kw()) is False
    assert len(page.clicks) == 1 and page.clicks[0][0] < 360  # 只点了左下「キャンセル」，没点任何卡


def test_remove_card_missing_id_cancels():
    f, _ = consult_delete_frames()
    page = CardPage(f, {}, "none")
    assert card_ops.remove_card(None, "p_card-02-act-3_039", **page.kw()) is False  # 牌组里没有 届いて！
    assert len(page.clicks) == 1 and page.clicks[0][0] < 360


def test_remove_card_wrong_selection_cancels():
    """点了卡但选中的是别的（第 142 帧选中第 0 张）：不按「削除」，取消。"""
    f, center = consult_delete_frames()
    f["sel0"] = cv2.imread(str(EVENTS / "removal-sel0-00142.jpg"))
    page = CardPage(f, {("none", center): "sel0"}, "none")
    assert card_ops.remove_card(None, KYORIKAN, **page.kw()) is False
    assert page.clicks[0] == center and page.clicks[-1][0] < 360 and (520, 1161) not in page.clicks


@pytest.mark.skipif(not REMOVE_REF.exists(), reason="找不到 KAA 删卡参考图")
def test_remove_card_event_page_has_no_cancel_and_taps_nothing():
    page = CardPage({"deck": cv2.imread(str(REMOVE_REF))}, {}, "deck")
    assert card_ops.remove_card(None, None, **page.kw()) is False
    assert page.clicks == []


@pytest.mark.skipif(not ENHANCE_REF.exists(), reason="找不到 KAA 强化参考图")
def test_enhance_card_flow_on_reference():
    from hiro.vision.deck import read_card_dialog

    cid = "p_card-02-act-0_009"  # 可愛い仕草（参考图选中的那张）
    f = {"deck": cv2.imread(str(ENHANCE_REF)), "sel": cv2.imread(str(ENHANCE_SEL_REF)),
         "after": cv2.imread(str(Path(__file__).parent / "fixtures" / "interval" / "round2-transition-q5.jpg"))}  # fmt: skip
    cards = read_card_dialog(f["sel"], card_ops.CARD_OP_REGION["enhance"])["cards"]
    center = tuple(next(c for c in cards if c["card_id"] == cid)["center"])
    # 两张参考图是不同牌组：从「已选中」那张开始（点卡后画面不变、选中项核对通过）
    page = CardPage(f, {("sel", (380, 1161)): "after"}, "sel")
    assert card_ops.enhance_card(None, cid, **page.kw()) is True
    assert page.clicks[0] == center and page.state == "after"


@pytest.mark.skipif(not ENHANCE_REF.exists(), reason="找不到 KAA 强化参考图")
def test_enhance_card_without_id_taps_nothing():
    page = CardPage({"deck": cv2.imread(str(ENHANCE_REF))}, {}, "deck")
    assert card_ops.enhance_card(None, None, **page.kw()) is False and page.clicks == []


def test_stage1_mark_by_column():
    from kotonebot.primitives import Rect

    class M:
        def __init__(self, x):
            self.rect = Rect(x, 946, 20, 20)

    full = [M(491), M(213), M(352)]
    assert card_ops._stage1_mark(full, 0).rect.x1 == 213 and card_ops._stage1_mark(full, 2).rect.x1 == 491
    missing_left = [M(352), M(491)]  # 第 0 张角标漏检：第 1 张仍是中间那列，不能错位成第 0 张
    assert card_ops._stage1_mark(missing_left, 1).rect.x1 == 352 and card_ops._stage1_mark(missing_left, 0) is None


def test_select_memory_finds_by_params():
    import hiro.loop.kaa_patches as kp

    headers = {ms.MEMORY_CELLS[4]: ["1498", "2066", "944"]}
    state = {"cur": None}
    clicks = []

    def click(x, y):
        clicks.append((x, y))
        state["cur"] = (x, y)

    ok = kp.select_memory(("1498", "2066", "944"), shoot=lambda: None, click=click, swipe=lambda *a: None,
                          texts_of=lambda img: headers.get(state["cur"], ["1697", "1936", "815"]), sleep=lambda s: None)  # fmt: skip
    assert (
        ok
        and clicks[0] == ms.MEMORY_CHANGE_BTN
        and clicks[1] == ms.MEMORY_SORT_BTN
        and clicks[-1] == ms.MEMORY_OK_BTN
        and ms.MEMORY_CELLS[4] in clicks
    )


def test_card_op_scrolls_to_collect_deck_and_back_to_target(monkeypatch):
    """牌组一屏放不下：先往下滑读完整副交给决策，再往回滑到目标那一屏点它。用识别的 merge_dialog_screens 拼接。"""
    from hiro.vision import deck

    screens = [[{"row": r, "col": c, "card_id": f"c{s_}{r}{c}" if not (s_ and r == 0) else f"c{s_ - 1}2{c}", "upgrade": 0, "customized": False,
                 "name": f"卡{s_}{r}{c}", "center": [140 + 147 * c, 760 + 147 * r]} for r in range(3) for c in range(4)] for s_ in range(3)]  # fmt: skip
    state = {"screen": 0, "taps": [], "chosen": None}
    img = cv2.imread(str(ENHANCE_REF)) if ENHANCE_REF.exists() else np.zeros((1280, 720, 3), np.uint8)

    monkeypatch.setattr(
        deck, "dialog_scroll", lambda im, region: {"scrollable": True, "thumb": None, "at_top": state["screen"] == 0}
    )

    def fake_read(im, region):
        cs = screens[state["screen"]]
        sel = next((i for i, c in enumerate(cs) if state["chosen"] and c["center"] == list(state["chosen"])), None)
        return {
            "cards": cs,
            "selected": sel,
            "selected_card": cs[sel] if sel is not None else None,
            "detail": None,
            "agree": None,
        }

    monkeypatch.setattr(deck, "read_card_dialog", fake_read)

    def swipe(x1, y1, x2, y2):
        state["screen"] = min(2, state["screen"] + 1) if y2 < y1 else max(0, state["screen"] - 1)

    def click(x, y):
        state["taps"].append((x, y))
        hit = next((c for c in screens[state["screen"]] if c["center"] == [x, y]), None)
        if hit and hit["card_id"] == "c121":  # 目标卡（在当前屏上点中才算）
            state["chosen"] = (x, y)

    seen = {}

    def choose(cards):
        seen["n"] = len(cards)
        return "c121"

    page_done = {"t": 0.0}
    ok = card_ops._card_op("enhance", None, shoot=lambda: img, click=click, clock=lambda: page_done["t"],  # noqa: F841
                              sleep=lambda s: page_done.__setitem__("t", page_done["t"] + s), ocr=lambda im: [], calib=False,
                              choose=choose, swipe=swipe)  # fmt: skip
    assert seen["n"] == 3 * 4 + 2 * 4 * 2  # 第 1 屏 3 行 + 后两屏各 2 行新行（每屏首行与上一屏末行重叠）
    assert state["chosen"] == state["taps"][0]  # 第一下就点中目标（它也出现在最后一屏的首行，不必往回滑）


def test_card_op_scrolls_back_when_target_is_above(monkeypatch):
    from hiro.vision import deck

    screens = [[{"row": r, "col": c, "card_id": f"c{s_}{r}{c}" if not (s_ and r == 0) else f"c{s_ - 1}2{c}", "upgrade": 0, "customized": False,
                 "name": "", "center": [140 + 147 * c, 760 + 147 * r]} for r in range(3) for c in range(4)] for s_ in range(3)]  # fmt: skip
    state = {"screen": 0, "chosen": None, "back": 0}
    monkeypatch.setattr(deck, "dialog_scroll", lambda im, region: {"scrollable": True, "thumb": None, "at_top": False})

    def fake_read(im, region):
        cs = screens[state["screen"]]
        sel = next((i for i, c in enumerate(cs) if state["chosen"] and c["center"] == list(state["chosen"])), None)
        return {
            "cards": cs,
            "selected": sel,
            "selected_card": cs[sel] if sel is not None else None,
            "detail": None,
            "agree": None,
        }

    monkeypatch.setattr(deck, "read_card_dialog", fake_read)

    def swipe(x1, y1, x2, y2):
        if y2 > y1:
            state["back"] += 1
        state["screen"] = min(2, state["screen"] + 1) if y2 < y1 else max(0, state["screen"] - 1)

    def click(x, y):
        hit = next((c for c in screens[state["screen"]] if c["center"] == [x, y]), None)
        if hit and hit["card_id"] == "c011":
            state["chosen"] = (x, y)

    t = {"t": 0.0}
    state["screen"] = 2  # 打开时停在最后一屏（续跑等情况）：往下没有新行，再往回滑
    card_ops._card_op("enhance", "c011", shoot=lambda: np.zeros((1280, 720, 3), np.uint8), click=click, clock=lambda: t["t"],
                         sleep=lambda s: t.__setitem__("t", t["t"] + s), ocr=lambda im: [], calib=False, swipe=swipe)  # fmt: skip
    assert state["chosen"] is not None and state["back"] == 2  # 第 1 屏才有：从第 3 屏往回滑两次


def _given_id_env(monkeypatch, screens, start=0):
    from hiro.vision import deck

    state = {"screen": start, "swipes": [], "taps": []}
    monkeypatch.setattr(
        deck, "dialog_scroll", lambda im, region: {"scrollable": True, "thumb": None, "at_top": state["screen"] == 0}
    )
    monkeypatch.setattr(
        deck,
        "read_card_dialog",
        lambda im, region: {"cards": screens[state["screen"]], "selected": None, "selected_card": None},
    )

    def swipe(x1, y1, x2, y2):
        state["swipes"].append("down" if y2 < y1 else "up")
        state["screen"] = min(len(screens) - 1, state["screen"] + 1) if y2 < y1 else max(0, state["screen"] - 1)

    t = {"t": 0.0}
    kw = {"shoot": lambda: np.zeros((1280, 720, 3), np.uint8), "click": lambda x, y: state["taps"].append((x, y)), "clock": lambda: t["t"],
              "sleep": lambda s: t.__setitem__("t", t["t"] + s), "ocr": lambda im: [], "calib": False, "swipe": swipe}  # fmt: skip
    return state, kw


def test_card_op_given_id_on_first_screen_taps_without_scanning(monkeypatch):
    """第十七局本战轮间：决策给了 card_id、目标就在第一屏，不再先滑完整副（拼接「滑过了头」会被判不可信而取消）。"""
    screens = [[{"row": i // 4, "col": i % 4, "card_id": f"c{s_}{i}", "upgrade": 0, "name": "", "center": [140 + 147 * (i % 4), 760 + 147 * (i // 4)]} for i in range(12)]
               for s_ in range(3)]  # fmt: skip
    state, kw = _given_id_env(monkeypatch, screens)
    card_ops._card_op("enhance", "c05", **kw)
    assert state["swipes"] == [] and state["taps"][0] == (287, 907)


def test_card_op_given_id_scrolls_down_until_found(monkeypatch):
    """目标在第 3 屏：一屏屏往下找；屏与屏之间没有重叠（滑过了头）也照样找。"""
    screens = [[{"row": i // 4, "col": i % 4, "card_id": f"c{s_}{i}", "upgrade": 0, "name": "", "center": [140 + 147 * (i % 4), 760 + 147 * (i // 4)]} for i in range(12)]
               for s_ in range(3)]  # fmt: skip
    state, kw = _given_id_env(monkeypatch, screens)
    card_ops._card_op("enhance", "c21", **kw)
    assert state["swipes"] == ["down", "down"] and state["taps"][0] == (287, 760)


def test_card_op_given_id_on_real_interval_frames():
    """第十七局本战 R1→R2 轮间实拍（calib 20261102-172438-强化）：きらきら紙吹雪（p_card-02-act-2_062）在第一屏第 1 行第 2 列。
    以前先往下滑读整副，第 2 屏和第 1 屏没有重叠的行 → 拼接不可信 → 点キャンセル。现在第一下就点它，不滑。"""
    top = cv2.imread(str(Path(__file__).parent / "fixtures/interval_enhance/main17-r1-deck-top.jpg"))
    bottom = cv2.imread(str(Path(__file__).parent / "fixtures/interval_enhance/main17-r1-deck-bottom.jpg"))
    state = {"img": top, "taps": [], "swipes": 0}
    t = {"t": 0.0}

    def swipe(*a):
        state["swipes"] += 1
        state["img"] = bottom

    card_ops._card_op("enhance", "p_card-02-act-2_062", shoot=lambda: state["img"], click=lambda x, y: state["taps"].append((x, y)),
                         clock=lambda: t["t"], sleep=lambda s: t.__setitem__("t", t["t"] + s), ocr=lambda im: [], calib=False, swipe=swipe)  # fmt: skip
    assert (
        state["swipes"] == 0
        and state["taps"]
        and abs(state["taps"][0][0] - 287) <= 10
        and abs(state["taps"][0][1] - 757) <= 10
    )


def test_select_memory_matches_prefixed_and_confused_ocr():
    """第十八局本战：列表上方属性读成「+1434」，按块精确相等找不到；拼接后按值找（允许 1 位常见混淆），且不跨块拼出假数。"""
    import hiro.loop.kaa_patches as kp

    headers = {ms.MEMORY_CELLS[0]: ["スター性", "ボーカル", "+1434", "ダンス", "2296", "1179"],
               ms.MEMORY_CELLS[1]: ["ボーカル", "1510", "ダンス", "2360", "1029"]}  # fmt: skip
    state = {"cur": None}

    def click(x, y):
        state["cur"] = (x, y)

    picked = []
    ok = kp.select_memory(("1434", "2296", "1179"), shoot=lambda: None, click=lambda x, y: (click(x, y), picked.append((x, y))),
                          swipe=lambda *a: None, texts_of=lambda img: headers.get(state["cur"], ["1"]), sleep=lambda s: None)  # fmt: skip
    assert ok and ms.MEMORY_CELLS[0] in picked
    assert not kp.memory_value_seen("1936", "1697|1936x".replace("1936x", "19|36"))  # 不跨块拼数


# ---------------------------------------------------------------- 第二十局 03:21：点「チェンジ」后确认框晚弹出
CHANGE_CONFIRM = Path(__file__).parent / "fixtures" / "change_confirm"


def test_late_change_confirm_dialog_is_confirmed(dev):
    """「スキルカードチェンジ確認」（calib 20261103-032052/05）：认出来点「決定」，不点キャンセル。"""
    img = cv2.imread(str(CHANGE_CONFIRM / "m20-late-confirm.jpg"))
    load(img)
    assert card_ops.confirm_change_dialog(img)
    pts = [
        (c.center.x, c.center.y)
        if hasattr(c, "center")
        else (c.rect.center.x, c.rect.center.y)
        if hasattr(c, "rect")
        else c
        for c in dev.clicks
    ]
    assert pts and all(x >= 360 and y >= 1100 for x, y in pts)  # 右下「決定」，不是左下キャンセル


def test_stage2_screen_is_not_a_confirm_dialog(dev):
    """点了「チェンジ」、确认框还没弹出来的阶段 2 画面（calib …/04）：不是确认框，也不该被当成「换完了」。"""
    img = cv2.imread(str(CHANGE_CONFIRM / "m20-after-change-tap.jpg"))
    load(img)
    assert not card_ops.confirm_change_dialog(img) and dev.clicks == []
    from hiro.vision.ui_templates import tpl

    assert tpl("InProduce.DialogCardChange2.Title").exists(img)  # 还在阶段 2：等待循环会继续等，而不是判完成


def test_select_memory_sort_by_ocr_and_fast_scan(monkeypatch):
    """第二十二局：排序选项按固定坐标点、列表按「タグ」排，翻完 12 页 5 分半没找到。现在按 OCR 找「入手」点，每格等 0.6s。"""
    import hiro.loop.kaa_patches as kp

    import numpy as np

    monkeypatch.setattr(ms, "_memory_snap", lambda img, tag: None)
    clicks, t = [], [0.0]
    sort_dialog = [("ソート・フィルタ", 1.0, (200, 60, 520, 100)), ("タグ", 1.0, (380, 260, 440, 286)), ("入手", 1.0, (180, 380, 240, 406)),
                   ("決定", 1.0, (470, 1145, 530, 1175))]  # fmt: skip
    img = np.zeros((1280, 720, 3), np.uint8)
    ok = kp.select_memory(("1", "2", "3"), shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None,
                          texts_of=lambda im: ["9999"], sleep=lambda s: t.__setitem__(0, t[0] + s), boxes_of=lambda im: sort_dialog)  # fmt: skip
    assert not ok
    assert (
        clicks[2] == (210, 393) and ms.MEMORY_SORT_NEWEST not in clicks
    )  # 点的是 OCR 找到的「入手」，不是固定坐标（也不是「タグ」）
    assert (500, 1160) in clicks  # 「決定」
    assert t[0] < 120  # 翻满 12 页 × 9 格的等待合计 < 2 分钟（原来约 5 分钟）


def test_enhance_pick_verify_survives_slow_reads():
    """第二十三局：录像时读一次卡格要数秒，点「成長痛」后 5 秒里只看了点完立刻截的一帧（还是上一张 ワクワク 选中），
    连续 3 次「选中不符」停机。现在点后先等一下、超时前至少看 3 帧：第二帧起就是选中的 成長痛，核对通过并点「強化」。"""
    fx = Path(__file__).parent / "fixtures"
    f = {"prev": cv2.imread(str(fx / "enhance/g23-prev-selected.jpg")), "target": cv2.imread(str(fx / "enhance/g23-target-selected.jpg")),
         "after": cv2.imread(str(fx / "interval/round2-transition-q5.jpg"))}  # fmt: skip
    cid = "p_card-02-men-2_111"  # 成長痛

    class Slow(CardPage):
        """每截一张图耗 6 秒（比 CARD_OP_TIMEOUT 还长）；点了目标后第一张截图还是旧选中。"""

        lag = 0

        def shoot(self):
            self.t += 6.0
            if self.lag:
                self.lag -= 1
                return self.frames["prev"]
            return self.frames[self.state]

        def click(self, x, y):
            before = self.state
            super().click(x, y)
            if before == "prev" and self.state == "target":
                self.lag = 1

    page = Slow(f, {("prev", (141, 905)): "target", ("target", (364, 1160)): "after"}, "prev")
    assert card_ops.enhance_card(None, cid, **page.kw()) is True
    assert page.clicks[0] == (141, 905) and page.state == "after"


DECK_DIALOG = Path(__file__).parent / "fixtures" / "deck_dialog"


@pytest.mark.skipif(
    not (DECK_DIALOG / "dmm-remove-art-loading.jpg").is_file(), reason="需要实机截图 tests/fixtures/deck_dialog/"
)
def test_read_loaded_waits_for_card_art():
    """DMM 版 dmm1m 相談删 眠気：对话框刚打开时下面几行卡面是白色占位（19 格认不出 18 格），拼接对不上；
    read_loaded 隔一会儿重读，拿到卡图加载好的那一帧（20 格全认出）。"""
    from hiro.loop import kaa_patches as kp  # noqa: F401

    import cv2

    loading = cv2.imread(str(DECK_DIALOG / "dmm-remove-art-loading.jpg"))
    loaded = cv2.imread(str(DECK_DIALOG / "dmm-remove-art-loaded.jpg"))
    frames = iter([loading, loading, loaded])
    t = [0.0]
    img, d = card_ops.read_loaded(
        lambda: next(frames),
        card_ops.CARD_OP_REGION["remove"],
        sleep=lambda s: t.__setitem__(0, t[0] + s),
        clock=lambda: t[0],
    )
    assert len(d["cards"]) == 20 and all(c.get("card_id") for c in d["cards"])
    assert img is loaded and t[0] <= 1.0


def test_read_loaded_gives_up_when_no_progress():
    """真认不出的卡（素材没有）：连续几次没变好就停，不等满 timeout。"""
    from hiro.loop import kaa_patches as kp  # noqa: F401

    import numpy as np

    blank = np.zeros((1280, 720, 3), np.uint8)
    calls = {"n": 0}

    def shoot():
        calls["n"] += 1
        return blank

    def fake_read(img, region):
        return {"cards": [{"card_id": None}, {"card_id": "x"}]}

    import hiro.vision.deck as deck

    orig = deck.read_card_dialog
    deck.read_card_dialog = fake_read
    try:
        t = [0.0]
        card_ops.read_loaded(shoot, (0, 0, 1, 1), sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    finally:
        deck.read_card_dialog = orig
    assert calls["n"] == 1 + card_ops.CARD_ART_STALL


def test_step_indicator_threshold_relaxed():
    """11-21 DMM 541x961：第 2 步步骤条只有 0.77 分，阈值放到 0.7（别的步骤条同一帧最高 0.56）。"""
    from hiro.loop import kaa_patches

    from kaa.tasks import R

    kaa_patches._relax_step_indicators(R)
    assert all(
        getattr(R.Produce, n).threshold == kaa_patches.STEP_INDICATOR_THRESHOLD for n in kaa_patches.STEP_INDICATORS
    )


def test_opening_dry_run_waits_then_refuses_click():
    """HIRO_OPENING_DRY_RUN：等到第 4 步开始按钮（证明前面走通），click 不点、抛 OpeningDryRunDone（ResumeBlocked）。"""
    from hiro.loop import kaa_patches
    from hiro.loop.errors import ResumeBlocked

    calls = []

    class Orig:
        @staticmethod
        def wait(*a, **k):
            calls.append("wait")
            return "match"

    btn = kaa_patches._dry_run_start_button(Orig)
    got = btn.wait()
    assert calls == ["wait"]
    with pytest.raises(ResumeBlocked):
        got.click()


def test_go_home_on_opening_step_taps_toolbar_home_not_next():
    """11-21：开局停在第 2 步时「先回首页」点了灰色的次へ。开局准备页认出来，只点工具栏首页。"""
    from hiro.loop.resume import is_opening_step

    assert is_opening_step(["2サポート選択", "3", "4"]) and is_opening_step(["サポートカードを選択してください"])
    assert not is_opening_step(["プロデュース", "ホーム", "コンテスト"])


def test_start_button_click_is_tracked():
    """点「プロデュース開始」时记 START_CLICKED；run_hif 据此区分开局准备里出错（停下）和培育中出错（转 resume）。"""
    from hiro.loop import kaa_patches

    clicked = []

    class Found:
        def click(self):
            clicked.append(1)

    class Orig:
        @staticmethod
        def wait(*a, **k):
            return Found()

    btn = kaa_patches._track_start_button(Orig)
    kaa_patches.START_CLICKED["clicked"] = False
    m = btn.wait()
    assert kaa_patches.START_CLICKED["clicked"] is False  # 只等到按钮不算
    m.click()
    assert kaa_patches.START_CLICKED["clicked"] is True and clicked == [1]


def test_step_indicator_threshold_also_in_hiro_templates():
    """新主循环（nav.find → ui_templates.tpl）也要放宽：11-22 DMM 新主循环同样卡在「等第 2 步」。"""
    from hiro.vision.ui_templates import tpl

    assert all(tpl(n).threshold == 0.7 for n in ("Produce.TextStepIndicator1", "Produce.TextStepIndicator2", "Produce.TextStepIndicator3",
                                                  "Produce.TextStepIndicator4Or2"))  # fmt: skip
