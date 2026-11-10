"""特別指導入口：DMM 页面切换慢，两下各等 2.5s 就判「没进去」（dmm2 选拔第 19 天，其实已在定制页，之后空等停机）。
两下之后不再点，多等一个 ENTER_TIMEOUT 看按钮下方的「特別指導」标签是否消失。"""

import importlib.util as _iu

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(_iu.find_spec("kotonebot") is None or _iu.find_spec("kaa") is None, reason="要装 KAA / kotonebot")


def _enter(gone_at: float):
    from hiro.produce import special_guidance as sg

    t = [0.0]
    taps = []
    x, y = sg.OPTION_CENTER
    label_box = ("特別指導", 1.0, (x - 40, y + 40, x + 40, y + 70))

    def ocr(img):
        return [] if t[0] >= gone_at else [label_box]

    r = sg.enter(None, shoot=lambda: np.zeros((1280, 720, 3), np.uint8), click=lambda a, b: taps.append((a, b)),
                 clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s), ocr=ocr, calib=False)  # fmt: skip
    return r, taps


def test_slow_transition_counts_as_entered():
    r, taps = _enter(gone_at=6.0)  # 两下各等 2.5s 之后才切过去
    assert r.ok and len(taps) == 2


def test_fast_transition_unchanged():
    r, taps = _enter(gone_at=1.0)
    assert r.ok and len(taps) == 1


def test_never_enters_still_fails_without_extra_taps():
    r, taps = _enter(gone_at=1e9)
    assert not r.ok and len(taps) == 2


def test_name_in_normalizes_long_vowel():
    from hiro.produce import special_guidance as sg

    boxes = [("えいえいお一+", 1.0, (200, 170, 360, 200))]  # RapidOCR 把「ー」读成「一」
    assert sg._name_in(boxes, "えいえいおー", (150, 150, 720, 520))
    assert not sg._name_in(boxes, "がむしゃら", (150, 150, 720, 520))
    assert not sg._name_in(boxes, "えいえいおー", (400, 150, 720, 520))  # 区域外不算


def test_leave_retaps_end_when_first_tap_missed():
    """DMM dmm2：第一下点在切换中的旧位置（菜单页 (653,977)），画面切回一览后点成了选中卡；按当前画面重新找「終了」再点。"""
    from hiro.produce import special_guidance as sg

    t = [0.0]
    taps = []
    grid = [("カスタマイズ可能スキルカード あと2枚", 1.0, (170, 1000, 550, 1030)), ("終了", 1.0, (620, 1070, 690, 1100))]
    menu = [("カスタマイズメニュー", 1.0, (250, 760, 470, 790)), ("一覧に戻る", 1.0, (230, 1070, 400, 1100)), ("終了", 1.0, (620, 965, 690, 990))]
    state = {"page": "menu"}

    def ocr(img):
        if state["page"] == "menu":
            state["page"] = "grid"  # 第一次读到的还是菜单页，之后切回一览
            return menu
        return [] if (655, 1085) in taps else grid  # 点中一览的「終了」才离开

    r = sg.leave(shoot=lambda: np.zeros((1280, 720, 3), np.uint8), click=lambda a, b: taps.append((a, b)),
                 clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s), ocr=ocr, calib=False)  # fmt: skip
    assert r.ok and taps[0] == (655, 977) and (655, 1085) in taps


def test_name_in_keeps_real_kanji_one():
    """卡名本来就有汉字「一」（一番星 / 一歩）：两边同样统一，照样命中；也不会和不同的卡混淆。"""
    from hiro.produce import special_guidance as sg

    region = (150, 150, 720, 520)
    assert sg._name_in([("一番星を目指して+", 1.0, (200, 170, 400, 200))], "一番星を目指して", region)
    assert sg._name_in([("ー番星を目指して", 1.0, (200, 170, 400, 200))], "一番星を目指して", region)  # OCR 反过来读错也能对上
    assert not sg._name_in([("一番星を目指して", 1.0, (200, 170, 400, 200))], "二番星を目指して", region)


def test_selected_not_confirmed_gets_one_more_tap():
    """两下里丢了一下：画面停在已选中、浮出说明（「…カスタマイズできます」）→ 再点一下确认。"""
    from hiro.produce import special_guidance as sg

    t = [0.0]
    taps = []
    x, y = sg.OPTION_CENTER
    label = ("特別指導", 1.0, (x - 40, y + 40, x + 40, y + 70))
    hint = ("Pポイントを消費し、強化済みのスキルカードをカスタマイズできます。", 1.0, (130, 820, 600, 850))

    def ocr(img):
        if len(taps) >= 3:
            return []  # 第三下（确认）之后进了定制页
        return [label, hint] if taps else [label]

    r = sg.enter(None, shoot=lambda: np.zeros((1280, 720, 3), np.uint8), click=lambda a, b: taps.append((a, b)),
                 clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s), ocr=ocr, calib=False)  # fmt: skip
    assert r.ok and len(taps) == 3


FIX = __import__("pathlib").Path(__file__).parent / "fixtures/special_guidance_dmm"


def _real(name):
    import cv2

    f = FIX / name
    if not f.exists():
        pytest.skip(f"需要实机截图 {f}")
    from hiro.produce.ops import ocr_boxes

    img = cv2.imread(str(f))
    return img, ocr_boxes(img)


def test_real_frame_selected_hint_and_label():
    """DMM dmm2 现场帧（calib 20261111-031025 02-entered）：选中说明在，按钮下方标签也在 → 旧逻辑判「没进去」，新逻辑补点确认。"""
    from hiro.produce import special_guidance as sg
    from hiro.produce.ops import find_text

    _, boxes = _real("1b-selected-not-confirmed.jpg")
    x, y = sg.OPTION_CENTER
    assert find_text(boxes, sg.SELECTED_HINT) is not None
    assert find_text(boxes, sg.TITLE, region=(x - 100, y + 20, x + 100, y + 110)) is not None


def test_real_frame_menu_card_name():
    """现场帧（calib 20261111-031943 03-menu）：定制菜单页是「えいえいおー+」，统一长音后能对上。"""
    from hiro.produce import special_guidance as sg

    _, boxes = _real("2c-menu-eieio-plus.jpg")
    assert sg.on_menu(boxes) and sg._name_in(boxes, "えいえいおー", (150, 150, 720, 520))


def test_real_frame_end_button_on_grid():
    """现场帧（calib 20261111-031947 01-leave）：切回一览后「終了」在 (653,1083) 一带，重新找能找到、不在菜单页位置。"""
    from hiro.produce import special_guidance as sg
    from hiro.produce.ops import find_text

    _, boxes = _real("3b-after-end-tap-selected-gamushara.jpg")
    end = find_text(boxes, ("終了",), region=(560, 940, 720, 1120))
    assert sg.on_grid(boxes) and end is not None and end[1] > 1040
