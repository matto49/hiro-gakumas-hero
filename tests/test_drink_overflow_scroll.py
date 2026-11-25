"""饮料溢出页：要丢的已有饮料被对话框底边切掉时，往上滑再找（实机帧在私有 fixtures）。
DMM 单局测试 dmmt2 选拔：新饮料 センブリソーダ 说明四行，已有饮料只完整露出 2 瓶，决策要丢第 [2] 瓶（初星スペシャル青汁），
「只看到 2 瓶」失败两次后停机。滑动后的画面没有实拍：用第一次溢出页（三瓶都露出）代替，它的第 2 瓶取消勾选后的帧是实拍。"""

from hiro.contracts import Action, ActionKind
from hiro.produce import drink_swap
from tests.test_interval import Fake

import pytest
from test_shop_exec import frame

# 经 test_shop_exec.frame 读私有截图样例（原来靠 test_interval 顶层 import kaa 整文件跳过）
pytestmark = pytest.mark.fixtures


def _new_checked(img):
    """切掉的那页，把新饮料（y≈481）的勾选框画成勾上（从已有第 0 瓶 y≈756 抄过来）。"""
    out = img.copy()
    x1, x2 = drink_swap.CHECK_X
    out[481 - 17 : 481 + 17, x1:x2] = img[756 - 17 : 756 + 17, x1:x2]
    return out


def test_scrolls_to_cut_off_owned_row():
    cut = frame("overflow_cut")
    frames = {"cut": cut, "scrolled": frame("overflow_3rows"), "scrolled_off2": frame("overflow_3rows_off2"),
              "back": cut, "back_new": _new_checked(cut), "after": frame("overflow_after")}  # fmt: skip
    fake = Fake(
        frames,
        {
            ("scrolled", (630, 946)): "scrolled_off2",
            ("back", (630, 481)): "back_new",
            ("back_new", (381, 1160)): "after",
        },
        "cut",
    )
    swipes = []

    def swipe(x1, y1, x2, y2):
        swipes.append((y1, y2))
        if y1 > y2 and fake.state == "cut":  # 往上滑：露出第三瓶
            fake.state = "scrolled"
        elif y1 < y2 and fake.state == "scrolled_off2":  # 滑回去：新饮料那一行回到画面里
            fake.state = "back"

    res = drink_swap.resolve_overflow([2], action=Action(ActionKind.DISCARD_DRINK, {"discard": [2], "receive": True}), shoot=fake.shoot,
                                      click=fake.click, clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False, swipe=swipe)  # fmt: skip
    assert res.ok, (res.error, fake.clicks, swipes)
    assert swipes[0][0] > swipes[0][1] and swipes[-1][0] < swipes[-1][1]  # 先往上滑，最后滑回来
    assert fake.state == "after"


def test_visible_rows_need_no_scroll():
    """三瓶都看得到时不滑。"""
    fake = Fake(
        {"open": frame("overflow_3rows"), "off2": frame("overflow_3rows_off2")}, {("open", (630, 946)): "off2"}, "open"
    )
    swipes = []
    drink_swap.resolve_overflow([2], shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep, ocr=fake.ocr, calib=False,
                                swipe=lambda *a: swipes.append(a))  # fmt: skip
    assert swipes == []
