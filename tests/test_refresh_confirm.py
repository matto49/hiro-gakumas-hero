"""「リフレッシュ確認」（第十八局本战轮间，总控 2026-11-02）：刷新货架后弹出的确认框，挂了 30 秒没人处理。
帧：reports/live/m18-refresh/ 00124（弹框前）、00125（确认框）、00132（30 秒后仍在）。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.pages import classify_page, read_interval, read_refresh_confirm
from hiro.vision.quick import quick_page

import cv2
import pytest

# 「キャンセル」按钮宽约 270（中心 x≈215）：RapidOCR 给的文字框比 Vision 窄、中心 x≈240，点下去仍在按钮里
CANCEL_TOL_X = 40

FIX = Path(__file__).with_name("fixtures") / "interval"


def _img(n: str):
    return cv2.imread(str(FIX / f"refresh-main18-{n}.jpg"))


@pytest.mark.parametrize("n", ["00125", "00132"])
def test_refresh_confirm_read(n):
    """P 335 → 325（花 10P）、没有ジュエル、按钮「キャンセル」/「更新する」位置。"""
    c = read_refresh_confirm(_img(n), 658)
    assert (c["p_before"], c["p_after"], c["cost"], c["jewel"]) == (335, 325, 10, False)
    assert abs(c["ok"][0] - 505) <= 20 and abs(c["ok"][1] - 1160) <= 20
    assert abs(c["cancel"][0] - 215) <= CANCEL_TOL_X and abs(c["cancel"][1] - 1160) <= 20


@pytest.mark.parametrize("n", ["00125", "00132"])
def test_quick_page_cancels(n):
    """快速判页认成 DIALOG refresh_confirm（不再落成 UNKNOWN），点「キャンセル」：刷新动作自己会按更新，
    落到主循环的是没有刷新决策时挂着的框（总控 2026-11-02，约定同执行 f0f2d10）。"""
    q = quick_page(_img(n))
    assert (
        q["page"] == "DIALOG" and q["fields"]["kind"] == "refresh_confirm" and q["fields"]["confirm"]["p_after"] == 325
    )
    assert (
        q["tap"] == tuple(q["fields"]["confirm"]["cancel"])
        and abs(q["tap"][0] - 215) <= CANCEL_TOL_X
        and abs(q["tap"][1] - 1160) <= 20
    )


def test_interval_overlay_dialog():
    """轮间读数带上盖着的确认框：确认框挂着时 interval=None 但 dialog.kind=refresh_confirm（执行侧据此判「还在轮间」，不是已离开）。"""
    under = read_interval(_img("00125"))
    assert under["interval"] is None and under["dialog"]["kind"] == "refresh_confirm"
    assert under["dialog"]["confirm"]["p_before"] == 335
    before = read_interval(_img("00124"))
    assert before["dialog"] is None


def test_classify_dialog_refresh():
    page, f = classify_page(_img("00132"))
    assert page == "DIALOG" and f["kind"] == "refresh_confirm" and f["danger"] is False


def test_page_fields_refresh_confirm():
    from hiro.vision.pages import page_fields

    f = page_fields("DIALOG", _img("00125"))
    assert f["kind"] == "refresh_confirm" and f["confirm"]["cost"] == 10
