"""「開催期間」白字：活动登录奖励点底部空白翻页；但限时扭蛋 / 礼包 / 公告也印着它，底部中间是「引く」「購入」，不能当登录奖励点。
续跑开局停在登录奖励上（DMM dmm2 -r4）：先翻掉再进培育，不当「已在培育内」。"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from hiro.vision import quick

FIX = Path(__file__).parent / "fixtures"
BONUS = ["date_change/event-login-bonus-jewel-dmm2.png", "home/g26-login-bonus.png", "quick/login-bonus-gakumas-wed.png",
         "quick/login-bonus-resume-dmm2-r4.jpg"]  # fmt: skip


def _img(rel):
    f = FIX / rel
    if not f.exists():
        pytest.skip(f"需要实机截图 {f}")
    return cv2.imread(str(f))


@pytest.mark.parametrize("rel", BONUS)
def test_real_login_bonus_still_tapped(rel):
    r = quick.quick_page(_img(rel))
    assert r["fields"]["kind"] == "login_bonus" and tuple(r["tap"]) == quick.LOGIN_BONUS_TAP


def test_purchase_words():
    assert quick.has_purchase_words(["10回引く", "ジュエル 2500"])
    assert quick.has_purchase_words(["購入する"]) and quick.has_purchase_words(["ガシャ"]) and quick.has_purchase_words(["¥610円"])
    assert not quick.has_purchase_words(["ジュエル", "250", "開催期間: 2026/11/18 04:59まで"])  # 登录奖励本身就送ジュエル


def test_event_page_with_draw_button_not_tapped(monkeypatch):
    """登录奖励帧 + 假装底部 OCR 读到「10回引く」：返回 event_purchase、不点。"""
    import hiro.vision.ocr as ocrmod

    img = _img(BONUS[0])
    monkeypatch.setattr(ocrmod, "ocr", lambda im, **kw: [("10回引く", 0.99)])
    r = quick.quick_page(img)
    assert r["fields"]["kind"] == "event_purchase" and not r.get("tap")


def test_home_and_shop_frames_do_not_hit_event_period():
    """主页 / 商店帧（有「ガシャ」「ショップ」字样）本来就不命中「開催期間」模板：排除只在模板命中后才看。"""
    for f in ["shop/consult-r5.jpg", "events/acquire-shop-00128.jpg"]:
        p = FIX / f
        if p.exists():
            assert not quick._white_hit(cv2.imread(str(p)), "quick_event_period")


def test_recover_to_home_waits_on_event_purchase():
    """回首页流程：「開催期間」+「引く」→ 不点（等「閉じる」）；超时也不点。"""
    from hiro.loop import date_change as dc

    img = _img(BONUS[0])
    clicks, t = [], {"now": 0.0}
    box = lambda s, x, y: (s, 1.0, (x - 30, y - 12, x + 30, y + 12))  # noqa: E731
    ok = dc.recover_to_home(lambda: img, lambda x, y: clicks.append((x, y)), lambda im: [box("10回引く", 360, 1180)],
                            lambda im: False, clock=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s), timeout=10)  # fmt: skip
    assert not ok and clicks == []


def test_resume_skips_login_bonus_pages():
    """续跑开局：两屏登录奖励，各点一次底部空白，第三屏不是奖励就停。"""
    from hiro.loop.resume import skip_login_bonus

    bonus = _img("quick/login-bonus-resume-dmm2-r4.jpg")
    other = np.zeros((1280, 720, 3), np.uint8)
    frames = iter([bonus, bonus, other])
    clicks = []
    assert skip_login_bonus(shoot=lambda: next(frames), click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None)
    assert clicks == [quick.LOGIN_BONUS_TAP] * 2
