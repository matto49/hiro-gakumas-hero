"""第三十三局：本地设置被重置后，开始确认页点「プロデュース開始」依次弹 4 个首次设置框（总控实机截图 tests/fixtures/first_run/）。
- ボイス再生確認：「決定」左边的对勾被 OCR 读成「く決定」，以前按全等找「決定」找不到，不点（设置框会一直盖着）；
- コミュ早送り設定 / 演出スキップ設定：skip_settings 照常处理；
- サポートイベント設定：hiro.runtime.scenes.first_run_settings（原 HiroController._first_run_settings）选「強化済みで獲得する」→「決定」（这一下就开始培育）。"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.loop import skip_settings as ss

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures" / "first_run"  # 实机帧（不随仓库发布）
BOXES = json.loads(
    (Path(__file__).parent / "data" / "g33-ocr-boxes.json").read_text(encoding="utf-8")
)  # 同一批帧的 OCR 框（随仓库发布）


def _boxes(name):
    """第三十三局实机帧的 OCR 框（tests/data，合成用例用）：[(text, conf, (x1, y1, x2, y2))]。"""
    return [(t, c, tuple(b)) for t, c, b in BOXES[f"first_run/g33-{name}"]]


def _frame(name):
    """实机帧本身（只有截图样例在时才有），缺哪张就跳过并写明。"""
    p = FIX / f"g33-{name}.jpg"
    if not p.exists():
        pytest.skip(f"缺样例帧 tests/fixtures/first_run/{p.name}")
    return cv2.resize(cv2.imread(str(p)), (720, 1280))


def test_ok_text_tolerates_check_icon():
    assert ss.is_ok_text("決定") and ss.is_ok_text("く決定") and ss.is_ok_text(" 決 定")
    assert not ss.is_ok_text("決定しますか") and not ss.is_ok_text("キャンセル") and not ss.is_ok_text("未決定")


@pytest.mark.synthetic
@pytest.mark.parametrize(("name", "kind", "option_y"), [
    ("1-voice", "ボイス再生確認", 818),  # ボイスありで再生（不是「なし」那行 y≈894）
    ("2-commu-ff", "早送り設定", 818),  # コミュを早送りする（不是「しない」那行 y≈742）
    ("3-produce-skip", "演出スキップ設定", None),
])  # fmt: skip
def test_real_frames_read(name, kind, option_y):
    r = ss.read(_boxes(name))
    assert r is not None and r["kind"] == kind
    assert r["ok"] is not None and abs(r["ok"][1] - 1160) <= 30, r
    if option_y is None:
        assert r["option"] is None
    else:
        assert r["option"] is not None and abs(r["option"][1] - option_y) <= 20, r


@pytest.mark.synthetic
def test_voice_dialog_clicks_option_then_ok():
    boxes = _boxes("1-voice")
    clicks = []
    r = ss.handle(
        boxes, shoot=lambda: None, ocr=lambda img: boxes, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None
    )
    assert r["ok"] and r["option"]
    assert abs(clicks[0][1] - 818) <= 20 and clicks[-1][0] > 400 and abs(clicks[-1][1] - 1160) <= 30, (
        clicks
    )  # 右边的「決定」，不是キャンセル


@pytest.mark.synthetic
def test_support_event_dialog_real_frame(monkeypatch):
    from hiro.runtime import scenes
    from hiro.vision.ocr import Box, Text

    import numpy as np

    raw = _boxes("4-support-event")
    assert ss.read(raw) is None  # 不归 skip_settings，归 first_run_settings
    boxes = [Text(t, Box(*b)) for t, _, b in raw]
    taps = []
    img = np.zeros((1280, 720, 3), np.uint8)
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), screen=NS(shot=lambda: NS(img=img)),
            sleep=lambda s: None, session=None)  # fmt: skip
    monkeypatch.setattr(scenes, "screen_texts", lambda *a, **k: boxes)
    assert scenes.first_run_settings(rt, NS(img=img, texts=boxes))
    assert len(taps) == 2 and abs(taps[0][1] - 892) <= 20 and abs(taps[1][1] - 1160) <= 30, (
        taps
    )  # 強化済み（不是未強化 y≈966）→ 決定


def test_quick_page_leaves_first_run_dialogs_alone():
    from hiro.vision.quick import quick_page

    for name in ("1-voice", "2-commu-ff", "3-produce-skip", "4-support-event"):
        assert quick_page(_frame(name)) is None, name
