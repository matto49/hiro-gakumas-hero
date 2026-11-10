"""凌晨日切换「日付変更」（hiro.loop.date_change）：识别、KAA Loop 回调打断、回首页流程。"""

import pytest

pytest.importorskip("kotonebot")  # 操作层：要装 KAA / kotonebot（pip install -e ".[play]"）

from pathlib import Path

import cv2
import numpy as np
import pytest

import hiro  # noqa: F401
from hiro.loop import date_change as dc
from hiro.produce import ops

import importlib.util as _iu
from pathlib import Path as _P


def _real(*rel):
    """用实机截图的用例：截图放在 tests/fixtures/…（开源仓库不带，用自己的截图），识别层（hiro.vision.ocr）也要装。"""
    have = all((_P(__file__).parent / "fixtures" / r).exists() for r in rel) and _iu.find_spec("hiro.vision.ocr") is not None
    return pytest.mark.skipif(not have, reason="需要实机截图 tests/fixtures/" + ", ".join(rel) + " 和识别层")


FIX = Path(__file__).parent / "fixtures" / "date_change"


def box(t, x, y):
    return (t, 1.0, (x - 40, y - 15, x + 40, y + 15))


@_real('date_change/date-change-20261030.png')
def test_real_frame_detected_with_strip_ocr():
    img = cv2.imread(str(FIX / "date-change-20261030.png"))
    pos = dc.date_change_button(dc.strip_boxes(img, ops.ocr_boxes))
    # 按钮宽约 220（中心 x≈379）：RapidOCR 文字框偏窄，中心 x≈354 仍在按钮内
    assert pos and abs(pos[0] - 379) < 40 and abs(pos[1] - 1159) < 15


def test_not_triggered_by_other_pages():
    assert dc.date_change_button([box("タイトルへ", 360, 1160)]) is None  # 没有「日付変更」标题
    assert dc.date_change_button([box("日付変更", 120, 970)]) is None  # 没有按钮


class FakeLoop:
    def __init__(self, img):
        self.screenshot = img


def test_loop_callback_raises_and_is_throttled():
    t = {"now": 0.0}
    calls = []

    def ocr(img):
        calls.append(1)
        return [box("日付変更", 120, 90), box("タイトルへ", 380, 280)]  # 横条内坐标

    cb = dc.loop_callback(ocr, every=5.0, clock=lambda: t["now"])
    with pytest.raises(dc.DateChanged):
        cb(FakeLoop(np.zeros((1280, 720, 3), np.uint8)))
    t["now"] = 2.0
    assert cb(FakeLoop(np.zeros((1280, 720, 3), np.uint8))) is False and len(calls) == 1  # 5 秒内不再 OCR


def test_loop_callback_quiet_on_normal_frames():
    cb = dc.loop_callback(lambda img: [box("次へ", 360, 200)], every=0.0, clock=lambda: 1.0)
    assert cb(FakeLoop(np.zeros((1280, 720, 3), np.uint8))) is False


class Screens:
    """日期切换 → 标题（TAP TO START）→ 登录奖励（点空白）→ 购买推荐框（只认閉じる）→ 首页。"""

    def __init__(self):
        self.seq = [
            ("date", [box("日付変更", 120, 973), box("日付が変わりました。", 176, 1057), box("タイトルへ", 379, 1159)]),
            ("title", [box("TAP TO START", 360, 1000)]),
            ("bonus", [box("ログインボーナス", 360, 300)]),
            ("promo", [box("ジュエルパック 購入", 360, 600), box("閉じる", 360, 1160)]),
            ("home", []),
        ]
        self.i, self.clicks = 0, []

    def shoot(self):
        return self.i

    def ocr(self, i):
        return self.seq[i][1]

    def click(self, x, y):
        self.clicks.append((self.seq[self.i][0], (x, y)))
        if self.seq[self.i][0] == "promo" and (x, y) != (360, 1160):
            raise AssertionError("购买框上只能点閉じる")
        self.i = min(self.i + 1, len(self.seq) - 1)


def test_recover_to_home_full_flow_never_taps_purchase():
    s = Screens()
    t = {"now": 0.0}
    ok = dc.recover_to_home(s.shoot, s.click, s.ocr, lambda i: s.seq[i][0] == "home",
                            clock=lambda: t["now"], sleep=lambda x: t.__setitem__("now", t["now"] + x))  # fmt: skip
    assert ok
    assert [c[0] for c in s.clicks] == ["date", "title", "bonus", "promo"]
    assert s.clicks[0][1] == (379, 1159) and s.clicks[2][1] == dc.BLANK_TAP and s.clicks[3][1] == (360, 1160)


def test_recover_waits_on_purchase_screen_without_close():
    clicks = []
    t = {"now": 0.0}
    ok = dc.recover_to_home(lambda: 0, lambda x, y: clicks.append((x, y)), lambda i: [box("ジュエル購入", 360, 600)], lambda i: False,
                            clock=lambda: t["now"], sleep=lambda x: t.__setitem__("now", t["now"] + x), timeout=10)  # fmt: skip
    assert not ok and clicks == []  # 有购买字样又没有閉じる：一下都不点


def test_landscape_frame_skipped_no_crash():
    """q14：LIVE 横屏帧（720x1280 宽 > 高）切空图 OCR 抛 cv2.error，主循环连崩。横屏直接跳过。"""
    import numpy as np

    from hiro.loop import date_change as dc

    called = []
    img = np.zeros((720, 1280, 3), np.uint8)
    assert dc.strip_boxes(img, lambda im: called.append(im) or []) == [] and called == []

    def boom(_):
        raise RuntimeError("ocr failed")

    cb = dc.loop_callback(boom, every=0, clock=lambda: 0.0)
    assert cb(type("L", (), {"screenshot": np.zeros((1280, 720, 3), np.uint8)})()) is False


@_real('date_change/data-update-win4-206.jpg')
def test_data_update_dialog_detected():
    """Windows 第四局 R2 最后一回合：游戏弹「データ更新 / データ更新があります。」只有「タイトルへ」，看门狗空转 183s 停机。
    和日期切换走同一条路：认出来、点タイトルへ、回首页续跑。"""
    img = cv2.imread(str(FIX / "data-update-win4-206.jpg"))
    pos = dc.date_change_button(dc.strip_boxes(img, ops.ocr_boxes))
    assert pos and abs(pos[0] - 360) < 50 and abs(pos[1] - 1157) < 20
    cb = dc.loop_callback(ops.ocr_boxes, every=0, clock=lambda: 0.0)
    with pytest.raises(dc.DateChanged, match="データ更新"):
        cb(FakeLoop(img))


def test_recover_accepts_download_and_never_cancels():
    """回标题后要下载数据：有「ダウンロード」字样时点 OK / ダウンロード，绝不点キャンセル；下载中只等不点空白。"""
    seq = [
        [box("データ更新", 360, 970), box("タイトルへ", 360, 1157)],
        [box("追加データをダウンロードします", 360, 600), box("キャンセル", 220, 1000), box("OK", 500, 1000)],
        [box("ダウンロード中", 360, 1100), box("45%", 360, 1150)],
        [box("TAP TO START", 360, 1100)],
        "home",
    ]
    i = {"n": 0}
    clicks = []

    def shoot():
        n = i["n"]
        i["n"] = min(n + 1, len(seq) - 1)
        return n

    t = {"now": 0.0}
    ok = dc.recover_to_home(shoot, lambda x, y: clicks.append((x, y)), lambda n: seq[n], lambda n: seq[n] == "home",
                            clock=lambda: t["now"], sleep=lambda x: t.__setitem__("now", t["now"] + x))  # fmt: skip
    assert ok and clicks == [(360, 1157), (500, 1000), (360, 1100)]


def test_recover_download_dialog_kettei():
    """「データダウンロード / ゲームデータのダウンロードを行います 214.6 MB」按钮是 キャンセル / 決定：点決定（Windows 第十七局只认 OK，等了 15 分钟）。"""
    seq = [
        [box("データダウンロード", 200, 820), box("ゲームデータのダウンロードを行います", 280, 900), box("キャンセル", 240, 1160), box("決定", 520, 1160)],
        "home",
    ]
    i = {"n": 0}
    clicks = []

    def shoot():
        n = i["n"]
        i["n"] = min(n + 1, len(seq) - 1)
        return n

    t = {"now": 0.0}
    ok = dc.recover_to_home(shoot, lambda x, y: clicks.append((x, y)), lambda n: seq[n], lambda n: seq[n] == "home",
                            clock=lambda: t["now"], sleep=lambda x: t.__setitem__("now", t["now"] + x))  # fmt: skip
    assert ok and clicks == [(520, 1160)]


@_real('date_change/data-download-win17.png')
def test_real_download_dialog_button():
    img = cv2.imread(str(FIX / "data-download-win17.png"))
    texts = [(dc._norm(t), b) for t, _, b in ops.ocr_boxes(img)]
    assert any("ダウンロード" in t for t, _ in texts)
    assert next((dc._center(b) for t, b in texts if t in dc.DOWNLOAD_OK and dc._center(b)[1] > 700), None) is not None


def test_event_login_bonus_with_jewel_reward_is_tapped():
    """DMM dmm2 日切换：「学マス水曜日」登录奖励送 250 ジュエル，「ジュエル」命中购买字样 → 以前一直等「閉じる」240s 停机。
    「開催期間」白字认出是活动登录奖励，点底部空白；没有「開催期間」的购买画面照旧不点。"""
    f = Path(__file__).parent / "fixtures/date_change/event-login-bonus-jewel-dmm2.png"
    if not f.exists():
        pytest.skip(f"需要实机截图 {f}")
    from hiro.vision.quick import LOGIN_BONUS_TAP

    img = cv2.imread(str(f))
    clicks, t = [], {"now": 0.0}
    seq = iter([img, img, "home"])
    cur = {"v": None}

    def shoot():
        cur["v"] = next(seq, "home")
        return cur["v"]

    ok = dc.recover_to_home(shoot, lambda x, y: clicks.append((x, y)), lambda im: [] if isinstance(im, str) else [box("ジュエル", 260, 760), box("250", 300, 820)],
                            lambda im: isinstance(im, str), clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s))  # fmt: skip
    assert ok and clicks and all(c == LOGIN_BONUS_TAP for c in clicks)
