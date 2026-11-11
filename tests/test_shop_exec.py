# 来源：digital-hiro tests/test_shop_exec.py（c3fc27f）
"""相談商店执行器（hiro.produce.shop）离线测试：选拔赛数据集里手动走过一遍相談的实机帧，按点击推进画面。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.contracts import Action, ActionKind
from hiro.produce import ops, shop

shop_ocr = ops.ocr_boxes

FLOW = Path(__file__).parent / "fixtures" / "shop" / "flow"


def frame(name: str):
    return cv2.imread(str(next(FLOW.glob(f"*-{name}.jpg"))))


SHELF = [{"index": i, "kind": "card" if i < 4 else "drink", "price": 50, "purchased": False,
          "center": [65 + 155 * (i % 4) + 62, (482 if i < 4 else 668) + 62]} for i in range(8)]  # fmt: skip
FIELDS = {"shelf": SHELF, "services": {"delete": {"price": 100, "available": True}, "upgrade": {"price": 100, "available": True}}}


def near(xy, target, r=40):
    return abs(xy[0] - target[0]) <= r and abs(xy[1] - target[1]) <= r


def in_cancel_pill(xy) -> bool:
    """点在左下白色「キャンセル」按钮里（按 OCR 文字框中心点：Vision 框窄、RapidOCR 框宽，中心差 60 像素，都在按钮里）。"""
    x, y, w, h = shop.CANCEL_PILL
    return near(xy, shop.CANCEL_TAP, 60) or (x - 20 <= xy[0] <= x + w + 20 and y - 10 <= xy[1] <= y + h + 10)


class FakeConsult:
    """状态机：idle →(点第 0 格) selected0 →(交換する) confirm →(交換) acquire_anim →(跳过) star_overlay →(跳过) purchased01。
    删卡：idle →(削除) delete_deck →(キャンセル) idle。终了：→ left。stuck 里的状态点什么都不变。"""

    def __init__(self, start="idle", stuck=(), final="purchased01"):
        self.state, self.stuck, self.final = start, set(stuck), final
        self.frames = {}
        self.clicks = []
        self.t = 0.0

    def shoot(self):
        if self.state not in self.frames:
            self.frames[self.state] = frame(self.state)
        return self.frames[self.state]

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.state in self.stuck:
            return
        s, xy = self.state, (x, y)
        if s in ("idle", "purchased01") and near(xy, shop.END_TAP):
            self.state = "left"
        elif s == "idle" and near(xy, SHELF[0]["center"]):
            self.state = "selected0"
        elif s == "idle" and near(xy, shop.SERVICE_TAP["delete"], 60):
            self.state = "delete_deck"
        elif s == "delete_deck" and in_cancel_pill(xy):
            self.state = "idle"
        elif s == "selected0" and near(xy, shop.EXCHANGE_TAP):
            self.state = "confirm"
        elif s == "confirm" and near(xy, shop.CONFIRM_TAP):
            self.state = "acquire_anim"
        elif s == "acquire_anim" and near(xy, shop.SKIP_TAP):
            self.state = "star_overlay"
        elif s == "star_overlay" and near(xy, shop.SKIP_TAP):
            self.state = self.final

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(fake, action, fields=FIELDS, purchased=None):
    purchased = purchased or (lambda img, center: fake.state == "purchased01" and center == SHELF[0]["center"])
    return shop.execute_shop(action, fields, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep, purchased=purchased,
                             calib=False)


# ---------------------------------------------------------------- 画面判据（实机帧）


@pytest.mark.parametrize("name, idle", [("idle", True), ("purchased01", True), ("selected0", False), ("confirm", False),
                                        ("acquire_anim", False), ("star_overlay", False), ("delete_deck", False), ("left", False)])  # fmt: skip
def test_consult_idle(name, idle):
    assert shop.consult_idle(frame(name)) is idle


def test_dialog_and_deck_page_show_cancel():
    assert shop.cancel_visible(frame("confirm")) and shop.cancel_visible(frame("delete_deck"))
    assert not shop.cancel_visible(frame("idle"))


def test_selected_bracket_and_exchange_lit():
    sel, idle = frame("selected0"), frame("idle")
    assert shop.selected(sel, SHELF[0]["center"]) and not shop.selected(sel, SHELF[1]["center"])
    assert not shop.selected(idle, SHELF[0]["center"])
    assert shop.orange_lit(sel, shop.EXCHANGE_BTN) and not shop.orange_lit(idle, shop.EXCHANGE_BTN)
    assert shop.orange_lit(frame("confirm"), shop.CONFIRM_BTN)


def test_ocr_purchased_on_real_frame():
    img = frame("purchased01")
    assert shop.ocr_purchased(img, SHELF[0]["center"]) and shop.ocr_purchased(img, SHELF[1]["center"])
    assert not shop.ocr_purchased(img, SHELF[2]["center"])
    assert not shop.ocr_purchased(frame("idle"), SHELF[0]["center"])


# ---------------------------------------------------------------- 流程


@pytest.mark.fixtures
def test_buy_full_flow():
    fake = FakeConsult()
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}))
    assert res.ok, res.error
    assert fake.state == "purchased01"
    assert res.taps[:3] == [SHELF[0]["center"], list(shop.EXCHANGE_TAP), list(shop.CONFIRM_TAP)]
    assert all(t == list(shop.SKIP_TAP) for t in res.taps[3:]) and len(res.taps) >= 4  # 动画两层各点一次跳过


@pytest.mark.fixtures
def test_buy_already_purchased_does_nothing():
    fake = FakeConsult()
    fields = {**FIELDS, "shelf": [{**SHELF[0], "purchased": True}]}
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}), fields)
    assert not res.ok and "已经换过" in res.error and fake.clicks == []


@pytest.mark.fixtures
def test_buy_when_exchange_never_lights():
    fake = FakeConsult(stuck={"idle"})
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}))
    assert not res.ok and "没有被选中" in res.error
    assert fake.clicks == [tuple(SHELF[0]["center"])]
    assert fake.t <= shop.LIT_TIMEOUT + 0.5


@pytest.mark.fixtures
def test_buy_when_confirm_dialog_missing():
    fake = FakeConsult(stuck={"selected0"})
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}))
    assert not res.ok and "交換確認" in res.error


@pytest.mark.fixtures
def test_buy_stuck_in_animation_times_out():
    fake = FakeConsult(stuck={"acquire_anim"})
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}))
    assert not res.ok and "没回到相談" in res.error and res.page_after is None
    assert fake.t <= shop.LIT_TIMEOUT + shop.DIALOG_TIMEOUT + shop.SETTLE_TIMEOUT + 1


@pytest.mark.fixtures
def test_buy_back_on_consult_but_not_marked():
    """回到相談但什么都没变（P 点、格子都和购买前一样）：判失败。"""
    fake = FakeConsult(final="idle")
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}), purchased=lambda img, c: False)
    assert not res.ok and "交換済" in res.error and "P 点没减少" in res.error


@pytest.mark.fixtures
def test_buy_counts_p_drop_and_cell_change_without_mark():
    """第四局 17:12：饮料格「交換済」OCR 没读到但其实买到了。P 点减少 / 格子大变 也算买到。"""
    fake = FakeConsult()
    res = run(fake, Action(ActionKind.SHOP_BUY, {"index": 0}), purchased=lambda img, c: False)
    assert res.ok, res.error


def test_read_p_on_real_frames():
    assert shop.read_p(frame("idle"), shop_ocr) == 270 and shop.read_p(frame("purchased01"), shop_ocr) == 116
    assert shop.read_p(cv2.imread(str(FLOW.parent / "consult-r5.jpg")), shop_ocr) == 370


@pytest.mark.fixtures
def test_end_answers_end_confirm_dialog():
    """还有没换的东西时「終了」会弹「相談終了確認」（第四局 17:12）：点「決定」再离开。用交換確認帧代替（同一种弹窗）。"""
    fake = FakeConsult()
    steps = {("idle", shop.END_TAP): "confirm", ("confirm", shop.CONFIRM_TAP): "left"}

    def click(x, y):
        fake.clicks.append((x, y))
        fake.state = next((nxt for (s, t), nxt in steps.items() if s == fake.state and near((x, y), t, 60)), fake.state)

    fake.click = click
    res = run(fake, Action(ActionKind.SHOP_END))
    assert res.ok, res.error
    assert res.taps[0] == list(shop.END_TAP) and fake.state == "left" and len(res.taps) == 2


@pytest.mark.fixtures
def test_delete_service_without_choice_cancels():
    """没有 card_id、也没有 choose_card：打开选卡页后取消，退回相談。"""
    fake = FakeConsult()
    res = run(fake, Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": 3}))
    assert not res.ok and "没做成" in res.error
    assert fake.state == "idle" and fake.clicks[0] == shop.SERVICE_TAP["delete"] and fake.clicks[-1][0] < 360  # 最后一下是左下キャンセル


EV = FLOW.parent.parent / "events"
KYORIKAN = "p_card-02-men-0_039"  # 距離感の基本（数据集第 146 帧选中的那张）


class ConsultDelete:
    """相談删卡实拍帧：idle →削除→ deck(141) →点卡→ sel6(146) →削除→ confirm(148) →削除→ toast(149) →3s→ idle。"""

    def __init__(self):
        from hiro.vision.deck import read_card_dialog

        self.frames = {"idle": frame("idle"), "deck": cv2.imread(str(EV / "removal-none-00141.jpg")),
                       "sel6": cv2.imread(str(EV / "removal-sel6-00146.jpg")), "confirm": cv2.imread(str(EV / "delete-confirm-00148.jpg")),
                       "toast": cv2.imread(str(EV / "delete-done-00149.jpg"))}  # fmt: skip
        cards = read_card_dialog(self.frames["deck"], (0, 380, 720, 1080))["cards"]
        self.center = tuple(next(c for c in cards if c["card_id"] == KYORIKAN)["center"])
        self.state, self.t, self.entered, self.clicks, self._ocr = "idle", 0.0, 0.0, [], {}
        self.steps = {("idle", shop.SERVICE_TAP["delete"]): "deck", ("deck", self.center): "sel6", ("sel6", (520, 1161)): "confirm",
                      ("confirm", (518, 1161)): "toast", ("deck", (236, 1161)): "idle", ("sel6", (236, 1161)): "idle"}  # fmt: skip

    def shoot(self):
        if self.state == "toast" and self.t - self.entered >= 3.0:
            self.state = "idle"
        return self.frames[self.state]

    def click(self, x, y):
        self.clicks.append((x, y))
        nxt = next((n for (st, t), n in self.steps.items() if st == self.state and near((x, y), t, 45)), None)
        if nxt:
            self.state, self.entered = nxt, self.t

    def ocr(self, im):
        key = next(k for k, v in self.frames.items() if v is im)
        if key not in self._ocr:
            self._ocr[key] = ops.ocr_boxes(im)
        return self._ocr[key]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def consult_delete(fake, action, choose_card=None):
    return shop.execute_shop(action, FIELDS, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr,
                             calib=False, choose_card=choose_card)  # fmt: skip


@pytest.mark.fixtures
def test_delete_service_decision_picks_from_dialog_cards():
    """第九局复盘：相談读不到牌组，440P / 400P 没花。现在打开选卡页后把卡格交给决策选，按 card_id 点选、确认。"""
    fake = ConsultDelete()
    seen = {}

    def choose(kind, cards):
        seen["kind"], seen["names"] = kind, [c["name"] for c in cards]
        return KYORIKAN

    res = consult_delete(fake, Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": 6}), choose)
    assert res.ok, res.error
    assert seen["kind"] == "remove" and "距離感の基本" in seen["names"] and "デイドリーミング" in seen["names"]
    assert fake.center in fake.clicks and fake.state == "idle" and res.page_after == shop.PAGE


@pytest.mark.fixtures
def test_delete_service_card_id_in_action():
    fake = ConsultDelete()
    res = consult_delete(fake, Action(ActionKind.SHOP_BUY, {"service": "delete", "card_id": KYORIKAN}))
    assert res.ok, res.error


@pytest.mark.fixtures
def test_delete_service_decision_declines_cancels():
    fake = ConsultDelete()
    res = consult_delete(fake, Action(ActionKind.SHOP_BUY, {"service": "delete"}), lambda kind, cards: None)
    assert not res.ok and fake.state == "idle" and fake.center not in fake.clicks


@pytest.mark.fixtures
def test_service_unavailable_is_not_tapped():
    fake = FakeConsult()
    fields = {**FIELDS, "services": {"upgrade": {"price": 100, "available": False}}}
    res = run(fake, Action(ActionKind.SHOP_UPGRADE_CARD, {"card_index": 0}), fields)
    assert not res.ok and "不可用" in res.error and fake.clicks == []


@pytest.mark.fixtures
def test_end_leaves_consult():
    fake = FakeConsult()
    res = run(fake, Action(ActionKind.SHOP_END))
    assert res.ok and fake.state == "left" and res.taps == [list(shop.END_TAP)]


@pytest.mark.fixtures
def test_end_no_effect():
    fake = FakeConsult(stuck={"idle"})
    res = run(fake, Action(ActionKind.SHOP_END))
    assert not res.ok and "終了" in res.error


@pytest.mark.fixtures
def test_unknown_index_and_kind():
    assert not run(FakeConsult(), Action(ActionKind.SHOP_BUY, {"index": 9})).ok
    assert "不支持" in run(FakeConsult(), Action(ActionKind.SKIP)).error


def test_consult_idle_on_other_run_fixture():
    """另一局（r5）的相談页：「交換する」饱和度 136，比数据集那局高，仍判为主画面。"""
    assert shop.consult_idle(cv2.imread(str(FLOW.parent / "consult-r5.jpg")))


# ---------------------------------------------------------------- q12 第 12 天：强化用过后又去点

Q12 = FLOW.parent / "q12"


def test_used_upgrade_service_is_not_tapped():
    """「強化」用过一次后按钮上盖「交換済」（q12 calib 171155）：不点、直接返回「本次相談已用过」。"""
    img = cv2.imread(str(Q12 / "upgrade-used-picked.jpg"))
    clicks = []
    res = shop.execute_shop(Action(ActionKind.SHOP_UPGRADE_CARD), FIELDS, shoot=lambda: img, click=lambda x, y: clicks.append((x, y)),
                            clock=lambda: 0.0, sleep=lambda s: None, calib=False)  # fmt: skip
    assert not res.ok and shop.SERVICE_USED in res.error and clicks == []


def test_confirm_dialog_mismatch_on_q12_frame():
    """q12：货架第 0 格识别成 アイドル宣言（4P），实际是 励まし+（SALE 49）。确认框核对能读出真实商品。"""
    d = shop._read_dialog(cv2.imread(str(Q12 / "confirm-hagemashi.jpg")))
    assert d and d["item"]["name"].startswith("励まし")
    assert shop.dialog_mismatch({"card_id": "p_card-00-men-2_012", "name": "アイドル宣言"}, d)
