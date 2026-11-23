"""相談「リフレッシュ」次数用完：按钮和「あと N回」整块消失（实机帧在私有 fixtures）。
DMM 第 12 天相談刷新一次后 rerolls_left 仍按旧值 1，决策再刷、执行盲点 REROLL_TAP 点到空地。
识别：货架完整、左下没有按钮 → rerolls_left=0；执行：相談画面上没有按钮就不点。"""

from hiro.contracts import Action, ActionKind
from hiro.produce import shop
from hiro.vision import shop as vshop
from tests.test_interval import Fake

from test_shop_exec import frame


def test_read_consult_counts_missing_button_as_zero():
    assert vshop.read_consult(frame("no_reroll_left"))["rerolls_left"] == 0
    assert vshop.read_consult(frame("reroll_left1"))["rerolls_left"] == 1


def _reroll(fake, left):
    return shop.execute_shop(Action(ActionKind.REROLL, {"scope": "consult"}), {"rerolls_left": left}, shoot=fake.shoot, click=fake.click,
                             clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip


def test_executor_does_not_tap_without_button():
    for stale in (1, None):  # 字段过期 / 没读到，都以画面为准
        fake = Fake({"idle": frame("no_reroll_left")}, {}, "idle")
        res = _reroll(fake, stale)
        assert not res.ok and "リフレッシュ" in res.error and fake.clicks == [], (stale, res.error, fake.clicks)


def test_executor_taps_visible_button():
    fake = Fake({"idle": frame("reroll_left1")}, {}, "idle")
    res = _reroll(fake, 1)
    assert fake.clicks and abs(fake.clicks[0][0] - 120) <= 70 and abs(fake.clicks[0][1] - 1085) <= 20, fake.clicks
    assert not res.ok and "货架没换" in res.error  # 替身画面不会变，只验证点了按钮
