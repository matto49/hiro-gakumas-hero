"""事件台词（页头 + 名字条 + 台词框、没有 SKIP 底栏）判成 commu、点台词框推进（协调 11-18：N.I.A 第二局选相談后星南独白判 unknown，
24 秒停）。初 / N.I.A / H.I.F 通用。实机帧在私有 fixtures pages/nia-event-dialogue-noskip.jpg。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _img(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.offline import load

    return load(cv2.imread(str(p)))


def test_event_dialogue_classified_commu_with_tap():
    from hiro.vision.classify import classify

    h = classify(_img("nia-event-dialogue-noskip.jpg"))
    assert h.name == "commu" and h.fields["kind"] == "event_dialogue" and h.fields["speaker"] == "星南"
    assert h.fields["_quick"]["tap"] == [360, 1030] and h.fields["_quick"]["dispatch"] is None


def _b(t, x, y, w=120):
    return (t, (x, y - 12, x + w, y + 12))


def test_rules():
    from hiro.vision.pages import event_dialogue

    base = [_b("星南", 61, 927, 50), _b("（お世話をする仕事だと伝えたら、", 91, 997, 400)]
    assert event_dialogue(base) is not None
    assert event_dialogue(base + [_b("SKIP", 230, 1201, 40)]) is None  # 有 SKIP 底栏：commu_skip 管
    assert event_dialogue(base + [_b("キャンセル", 200, 1160)]) is None  # 有按钮：弹窗 / 选项
    assert event_dialogue([base[1]]) is None  # 没有名字条
    assert event_dialogue([base[0]]) is None  # 没有台词
    assert event_dialogue(base + [_b("受け取る", 294, 1110)]) is None  # 选饮料弹窗淡入中（scenes/00265）：不点


def test_advance_taps_dialogue_box(monkeypatch):
    """主循环 advance（play.advance）和 N.I.A 采样的 advance 都按 _quick 的 tap 点台词框。"""
    import numpy as np

    from hiro.runtime import play
    from hiro.vision.classify import PageHit

    taps = []
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), _quick_wait=None)
    q = {"page": "commu", "tap": [360, 1030], "dispatch": None, "fields": {"kind": "event_dialogue"}}
    hit = PageHit("commu", "page_ocr", "produce", {"kind": "event_dialogue", "_quick": q})
    monkeypatch.setattr(play, "_flag_deck_after_commu", lambda rt: None)
    frame = NS(img=np.zeros((1280, 720, 3), np.uint8), t=0.0, texts=[])
    assert play.advance(rt, frame, hit) is True and taps == [(360, 1030)]
