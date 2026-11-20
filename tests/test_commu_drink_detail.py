"""协调 11-18 初 MASTER 20:31：快速判页认剧情、隔 0.36 秒点了两次 SKIP (225,1200)；第一下已经跳过，第二下落在刚出来的选行动页
饮料栏第 3 格，点开「Pドリンク詳細」（捨てる / キャンセル / 使う）盖住选行动页，选行动读不到选项停机。
① 剧情 SKIP：点过 COMMU_RETAP_S 秒内不再点，点之前重截核对底栏；② 中断层见到「Pドリンク詳細」只点キャンセル。"""

from types import SimpleNamespace as NS

import pytest

from hiro.runtime import interrupts, play
from hiro.runtime.frame import Frame
from test_runtime import frame as fake_frame
from test_runtime import runtime


def _real(name):
    import cv2

    p = __import__("pathlib").Path(__file__).with_name("fixtures") / "pages" / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


COMMU = "commu-ending-before-action-select.jpg"  # 第二下 SKIP 点下去时截的帧：还是剧情
ACTION = "action-select-oikomi-after-commu.jpg"  # 0.5 秒后：选行动页（追い込み），(225,1200) 是饮料栏第 3 格
DETAIL = "pdrink-detail-action-select.png"


def test_commu_bar_real_frames():
    from hiro.vision.quick import commu_bar, quick_page

    assert commu_bar(_real(COMMU)) and quick_page(_real(COMMU))["page"] == "commu"
    assert not commu_bar(_real(ACTION))


class Rt:
    def __init__(self, shots):
        self.t, self.taps, self.shots = 0.0, [], list(shots)
        self.device = NS(tap=lambda x, y: self.taps.append((x, y)))
        self.screen = NS(shot=lambda: NS(img=self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]))
        self.session = None
        self.clock = lambda: self.t


def test_commu_skip_not_retapped_within_gap(monkeypatch):
    monkeypatch.setattr(play, "_flag_deck_after_commu", lambda rt: None)
    commu = _real(COMMU)
    rt = Rt([commu])
    q = {"page": "commu", "tap": (225, 1200)}
    assert play._quick_tap(rt, q, 0.0) and rt.taps == [(225, 1200)]
    rt.t = 0.36  # 实机第二下的间隔
    assert not play._quick_tap(rt, q, 0.36) and len(rt.taps) == 1
    rt.t = play.COMMU_RETAP_S + 0.1  # 过了间隔剧情还在（比如 スキップ確認 没出来）：照点
    assert play._quick_tap(rt, q, rt.t) and len(rt.taps) == 2


def test_commu_skip_rechecks_fresh_frame(monkeypatch):
    monkeypatch.setattr(play, "_flag_deck_after_commu", lambda rt: None)
    rt = Rt([_real(ACTION)])  # 判页那帧是剧情，点之前重截已经是选行动页
    assert not play._quick_tap(rt, {"page": "commu", "tap": (225, 1200)}, 0.0) and rt.taps == []


def test_drink_detail_taps_cancel_only():
    rt, _ = runtime([fake_frame()])
    msg = interrupts.check(Frame(_real(DETAIL), 0.0), rt)
    assert msg and "Pドリンク詳細" in msg
    (x, y), = rt.raw_device.taps
    assert x < 360 and abs(y - 1160) < 20  # 左下キャンセル；捨てる 在 (605,889)、使う 在 (516,1160)


def test_drink_detail_waits_after_tap_and_fallback_position():
    rt, _ = runtime([fake_frame()])
    f = fake_frame(texts=[], dialog={"title": "Pドリンク詳細"})
    assert "キャンセル" in interrupts.drink_detail(f, rt)
    assert "等框关" in interrupts.drink_detail(f, rt)  # 刚点过：不连点
    assert rt.raw_device.taps == [interrupts.DRINK_DETAIL_CANCEL]


# ---------------------------------------------------------------- 底部「TAPで次へ」（协调 11-18 初 MASTER 最終后「1位」证书页）
CERT = "hajime-final-rank1-certificate.png"


def _rt_img(img):
    rt = Rt([img])
    return rt


def test_rank_certificate_taps_tap_next():
    """白色证书框、没有弹窗标题条，判 unknown；连点被 BLOCK_WORDS 的「次へ」拦住 180 秒停机。现在点「TAPで次へ」那行字。"""
    img = _real(CERT)
    rt = _rt_img(img)
    f = Frame(img, 0.0)
    assert play.advance(rt, f, NS(name="unknown", fields={}))
    (x, y), = rt.taps
    assert abs(x - 360) < 40 and abs(y - 1147) < 20
    rt.t = 0.5
    assert play.advance(rt, Frame(img, 0.5), NS(name="unknown", fields={})) and len(rt.taps) == 1  # 刚点过：等翻页，不连点


def test_tap_next_blocked_by_button_words():
    from hiro.runtime import scenes
    from hiro.vision.ocr import Box, Text

    def T(text, x, y):
        return Text(text, Box(x - 20, y - 10, x + 20, y + 10))

    import numpy as np

    img = np.zeros((1280, 720, 3), np.uint8)
    for extra in ("購入する", "キャンセル", "ジュエル"):
        rt = _rt_img(img)
        f = NS(img=img, texts=[T("TAPで次へ", 360, 1147), T(extra, 300, 600)])
        assert not scenes.tap_next(rt, f) and rt.taps == []
    rt = _rt_img(img)
    assert not scenes.tap_next(rt, NS(img=img, texts=[T("TAPで次へ", 360, 500)]))  # 不在底部
