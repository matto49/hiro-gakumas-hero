"""相談「終了」要正面确认离开（实机帧）：删卡 / 强化后底部提示条（「〜を削除しました」）、支援卡横幅、强化演出盖着时，
以前「不是空闲相談页、没有取消框」第一帧就判成功（Mac 第 33 局选拔 0.062s / 0.035s；DMM 0.047s），实际还在相談。
现在：先等提示条退掉再点，点完要连续 2 帧看不到商店页文字（也没有确认框）才算离开；相談结束后的角色场景、loading 算离开。"""

from hiro.contracts import Action, ActionKind
from hiro.produce import shop

import pytest
from test_shop_exec import FakeConsult, frame, near

pytestmark = [pytest.mark.fixtures]


@pytest.mark.parametrize("name, shown", [("toast_delete", True), ("toast_upgrade", True), ("support_banner", True), ("toast_delete_dmm", True),
                                          ("idle", True), ("consult_end_scene", False), ("exam_loading", False), ("left", False)])  # fmt: skip
def test_shop_shown_markers(name, shown):
    from hiro.produce.ops import ocr_boxes

    assert shop.shop_shown(ocr_boxes(frame(name))) is shown


class ToastConsult(FakeConsult):
    """开局是提示条（toast_delete），toast_s 秒后自己退成空闲相談页；ignore=True 时点「終了」也不走。"""

    def __init__(self, toast="toast_delete", toast_s=2.0, ignore=False):
        super().__init__(start=toast)
        self.toast_s, self.ignore, self.end_taps_while = toast_s, ignore, []

    def shoot(self):
        if self.state.startswith("toast") and self.t >= self.toast_s:
            self.state = "idle"
        return super().shoot()

    def click(self, x, y):
        if near((x, y), shop.END_TAP):
            self.end_taps_while.append(self.state)
            if self.ignore:
                self.clicks.append((x, y))
                return
        super().click(x, y)


@pytest.mark.parametrize("toast", ["toast_delete", "toast_delete_dmm"])
def test_end_waits_for_toast_then_confirms_left(toast):
    fake = ToastConsult(toast=toast)
    res = shop.execute_shop(Action(ActionKind.SHOP_END, {}), {}, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep,
                            calib=False)  # fmt: skip
    assert res.ok and fake.state == "left"
    assert fake.end_taps_while == ["idle"]  # 没在提示条盖着的时候点


def test_end_not_left_is_failure_not_success():
    """点了「終了」画面没走（一直是空闲相談页）：重点两次后报失败，不再当成功。"""
    fake = ToastConsult(toast="toast_delete", toast_s=0.0, ignore=True)
    res = shop.execute_shop(Action(ActionKind.SHOP_END, {}), {}, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep,
                            calib=False)  # fmt: skip
    assert not res.ok and len(fake.end_taps_while) == 1 + shop.END_RETAPS
