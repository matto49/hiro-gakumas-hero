"""收尾 / 回首页 / 日切换恢复阶段的认不出画面处理（hiro.loop.assist）：快速判页 → 卡住 ≥12s 交 LLM（同主循环、同拦截）。"""

from hiro.brain.llm import MockLLM as _Mock

import pytest

NEED_IMAGE = pytest.mark.skipif(
    not hasattr(_Mock, "ask_json_image"), reason="开源 hiro.brain.llm 还没有看图接口 ask_json_image，等策略层同步"
)

from hiro.brain.llm import MockLLM  # noqa: E402
from hiro.loop import rescue  # noqa: E402
from hiro.loop.assist import Assist  # noqa: E402

import numpy as np  # noqa: E402

BONUS = [("毎週水曜日は学マスの日", 1.0, (407, 95, 582, 115)), ("開催期間：2026/11/11 04:59まで", 1.0, (173, 218, 549, 246)),
         ("学マ水曜日ガシャチケット", 1.0, (216, 763, 504, 787))]  # fmt: skip


def _img(v=40):
    return np.full((1280, 720, 3), v, np.uint8)


def test_quick_page_tap_first():
    clicks = []
    a = Assist(
        "回首页", click=lambda x, y: clicks.append((x, y)), quick=lambda img: {"page": "GIFT", "tap": (360, 1180)}
    )
    assert a.step(_img(), BONUS) is True and clicks == [(360, 1180)]


@NEED_IMAGE
def test_stuck_screen_goes_to_llm_after_threshold(monkeypatch):
    t = [0.0]
    clicks = []
    img = _img()
    r = rescue.Rescuer(MockLLM(replies=[{"page": "登录奖励", "reason": "无按钮", "action": "tap_blank"}]), ocr=lambda im: BONUS,
                       shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None, clock=lambda: t[0],
                       out_dir=None, enabled=True)  # fmt: skip
    monkeypatch.setattr(rescue, "LLM_UNKNOWN", True)
    a = Assist("回首页", click=lambda x, y: None, quick=lambda im: None, rescuer=r, clock=lambda: t[0])
    assert a.step(img, BONUS) is False  # 刚看到这个画面
    t[0] += rescue.RESCUE_AFTER
    assert a.step(img, BONUS) is False  # 交给 LLM（后台）
    r._job["future"].result()
    assert a.step(img, BONUS) is True and clicks  # 回复到了：点空白


@NEED_IMAGE
def test_llm_stop_gives_up_to_caller(monkeypatch):
    t = [0.0]
    img = _img()
    r = rescue.Rescuer(MockLLM(replies=[{"action": "stop"}]), ocr=lambda im: BONUS, shoot=lambda: img, click=lambda x, y: None,
                       sleep=lambda s: None, clock=lambda: t[0], out_dir=None, enabled=True)  # fmt: skip
    monkeypatch.setattr(rescue, "LLM_UNKNOWN", True)
    a = Assist("回首页", click=lambda x, y: None, quick=lambda im: None, rescuer=r, clock=lambda: t[0])
    a.step(img, BONUS)
    t[0] += rescue.RESCUE_AFTER
    a.step(img, BONUS)
    r._job["future"].result()
    assert a.step(img, BONUS) is False  # 放手：调用方照旧点自己的兜底位置
    t[0] += 30
    assert a.step(img, BONUS) is False and not r.pending  # 同一画面不再问


@NEED_IMAGE
def test_purchase_screen_never_tapped(monkeypatch):
    t = [0.0]
    img = _img()
    clicks = []
    shop = [("ジュエル購入", 1.0, (200, 600, 500, 640)), ("閉じる", 1.0, (300, 1150, 420, 1190))]
    r = rescue.Rescuer(MockLLM(replies=[{"action": "tap_box", "box": 1}]), ocr=lambda im: shop, shoot=lambda: img,
                       click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None, clock=lambda: t[0], out_dir=None, enabled=True)  # fmt: skip
    monkeypatch.setattr(rescue, "LLM_UNKNOWN", True)
    a = Assist("回首页", click=lambda x, y: clicks.append((x, y)), quick=lambda im: None, rescuer=r, clock=lambda: t[0])
    a.step(img, shop)
    t[0] += rescue.RESCUE_AFTER
    a.step(img, shop)
    r._job["future"].result()
    assert a.step(img, shop) is False and clicks == []
