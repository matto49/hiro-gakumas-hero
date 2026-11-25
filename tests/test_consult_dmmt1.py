"""DMM 单局测试 dmmt1 第 12 天相談（实机帧在私有 fixtures）：
1. 免费刷新真的换了货架（按钮也没了），新旧货架长得像，货架差 11.2 < 12，判成「没换」；
2. 强化 ゆめみごこち 后「ゆめみごこちを強化しました」消息框一直挂着（DMM 上不会自己退），相談循环 3 次读不出货架只好结束。"""

from hiro.contracts import Action, ActionKind
from hiro.produce import shop
from tests.test_interval import Fake

import pytest
from test_shop_exec import frame

# 经 test_shop_exec.frame 读私有截图样例（原来靠 test_interval 顶层 import kaa 整文件跳过）
pytestmark = pytest.mark.fixtures

REFRESH = (100, 1085)


def test_reroll_counts_badge_change_with_weak_shelf_diff():
    before, after = frame("reroll_before"), frame("reroll_after")
    assert shop.SHELF_CHANGED_WEAK <= shop.shelf_diff(before, after) < shop.SHELF_CHANGED_MIN
    fake = Fake({"idle": before, "confirm": frame("reroll_confirm"), "after": after},
                {("idle", REFRESH): "confirm", ("confirm", shop.REROLL_OK_TAP): "after"}, "idle")  # fmt: skip
    res = shop.execute_shop(Action(ActionKind.REROLL, {"scope": "consult"}), {"rerolls_left": 1}, shoot=fake.shoot, click=fake.click,
                            clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip
    assert res.ok, (res.error, fake.clicks)


def test_same_shelf_is_not_a_reroll():
    """按钮没变（确认框没点下去）时，单靠一点点货架差不算换了。"""
    before = frame("reroll_before")
    fake = Fake({"idle": before}, {}, "idle")
    res = shop.execute_shop(Action(ActionKind.REROLL, {"scope": "consult"}), {"rerolls_left": 1}, shoot=fake.shoot, click=fake.click,
                            clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False)  # fmt: skip
    assert not res.ok


def test_done_bubble_is_tapped_away():
    bubble, idle = frame("enhance_bubble"), frame("reroll_after")
    fake = Fake({"bubble": bubble, "idle": idle}, {("bubble", (300, 990)): "idle"}, "bubble")
    r = shop._Run(
        Action(ActionKind.SHOP_UPGRADE_CARD), fake.shoot, fake.click, fake.clock, fake.sleep, ocr=fake.ocr, calib=False
    )
    back, _ = shop.settle_after_op(r)
    assert back and fake.state == "idle" and len(fake.clicks) == 1, fake.clicks


def test_no_tap_when_no_bubble():
    fake = Fake({"idle": frame("reroll_after")}, {}, "idle")
    r = shop._Run(
        Action(ActionKind.SHOP_UPGRADE_CARD), fake.shoot, fake.click, fake.clock, fake.sleep, ocr=fake.ocr, calib=False
    )
    assert shop.settle_after_op(r)[0] and fake.clicks == []
