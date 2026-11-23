"""q13：重新登录后开局弹「サポートイベント設定」，控制器不认，188s 停机。现在选「強化済みで獲得する」→「決定」。"""

import sys
from types import SimpleNamespace as NS

from hiro.loop.controller import HiroController
from hiro.vision.ocr import Box, Text


def _box(text, x, y):
    return Text(text, Box(x - 5, y - 5, x + 5, y + 5))  # 中心 (x, y)


def _fake(monkeypatch, clicks, boxes):
    """controller 里 from kotonebot import device（函数内导入）+ 模块级 screen_texts。"""
    monkeypatch.setitem(
        sys.modules, "kotonebot", NS(device=NS(click=lambda *r: clicks.append(r), screenshot=lambda: None))
    )
    monkeypatch.setattr("hiro.loop.controller.screen_texts", lambda *a, **k: boxes)


def test_support_event_setting_picks_upgraded_and_confirms(monkeypatch):
    boxes = [_box("サポートイベント設定", 100, 700), _box("強化済みで獲得する", 200, 890), _box("未強化で獲得する", 200, 965),
             _box("決定", 360, 1160)]  # fmt: skip
    clicks = []
    _fake(monkeypatch, clicks, boxes)
    monkeypatch.setattr("hiro.loop.controller.time.sleep", lambda s: None)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in boxes]
    assert c._first_run_settings()
    assert clicks == [(200, 890), (360, 1160)]


def test_other_pages_untouched(monkeypatch):
    c = HiroController.__new__(HiroController)
    c._texts = ["審査基準", "レッスン"]
    assert not c._first_run_settings()


def test_commu_skip(monkeypatch):
    boxes = [
        _box("プロデュースコミュ", 20, 40),
        _box("あさり先生", 60, 900),
        _box("SKIP", 220, 1200),
        _box("ログ", 360, 1200),
        _box("早送り", 640, 1200),
    ]
    clicks = []
    _fake(monkeypatch, clicks, boxes)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in boxes]
    assert c._skip_commu() and clicks == [(220, 1200)]
    c._texts = ["審査基準", "SKIP"]
    assert not c._skip_commu()
    # 支援事件剧情：没有「プロデュースコミュ」标题、SKIP 读成 MSkIP
    boxes2 = [
        _box("そろそろ焼けたかな?", 60, 100),
        _box("MSkIP", 222, 1198),
        _box("レDAuT0", 500, 1200),
        _box("恥早送り", 640, 1200),
    ]
    _fake(monkeypatch, clicks, boxes2)
    c._texts = [b.text for b in boxes2]
    assert c._skip_commu() and clicks[-1] == (222, 1198)


def test_commu_skip_needs_real_skip_box(monkeypatch):
    """q14：判据误中非剧情页时不再点固定位置（那里是饮料栏第 3 格）。"""
    boxes = [_box("早送り", 640, 1200), _box("ログ", 360, 1200)]
    clicks = []
    _fake(monkeypatch, clicks, boxes)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in boxes]
    assert not c._skip_commu() and clicks == []


def test_drink_detail_outside_exam_cancels(monkeypatch):
    """q14：考试外误开「Pドリンク詳細」，只点キャンセル，不点使う。"""
    import hiro.exec.exam  # noqa: F401  先导入真模块，再替换 kotonebot

    boxes = [_box("Pドリンク詳細", 60, 860), _box("レッスン・試験・オーディション以外では使用できません", 80, 640),
             _box("キャンセル", 200, 1160), _box("使う", 500, 1160)]  # fmt: skip
    clicks = []
    _fake(monkeypatch, clicks, boxes)
    assert HiroController._clear_hif_drink_dialog()
    assert clicks == [(200, 1160)]


def test_skip_confirm_unread_commu(monkeypatch):
    boxes = [
        _box("スキップ確認", 100, 700),
        _box("未読のコミュです", 120, 800),
        _box("✕キャンセル", 113, 1140),
        _box("決定", 486, 1144),
    ]
    clicks = []
    _fake(monkeypatch, clicks, boxes)
    monkeypatch.setattr("hiro.loop.controller.time.sleep", lambda s: None)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in boxes]
    assert c._first_run_settings() and clicks == [(486, 1144)]


def test_commu_skip_rechecks_fresh_frame(monkeypatch):
    """q15：缓存帧是剧情、点之前画面已切回培育页（没有「早送り」）→ 不点。"""
    stale = [_box("早送り", 640, 1200), _box("SKIP", 220, 1200)]
    fresh = [_box("審査基準", 500, 40), _box("Da.公開レッスン", 60, 40)]
    clicks = []
    _fake(monkeypatch, clicks, fresh)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in stale]
    assert not c._skip_commu() and clicks == []
