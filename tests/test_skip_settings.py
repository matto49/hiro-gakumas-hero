"""「演出スキップ設定」弹窗（第二十三局）与开始确认页判据：设置框盖在開始確認页上时不停机、开局过场里不停机。"""

import importlib.util as _iu
from pathlib import Path
from pathlib import Path as _P

from hiro.loop import skip_settings

import pytest


def _real(*rel):
    """用实机截图的用例：截图放在 tests/fixtures/…（开源仓库不带，用自己的截图），识别层（hiro.vision.ocr）也要装。"""
    have = (
        all((_P(__file__).parent / "fixtures" / r).exists() for r in rel)
        and _iu.find_spec("hiro.vision.ocr") is not None
    )
    return pytest.mark.skipif(not have, reason="需要实机截图 tests/fixtures/" + ", ".join(rel) + " 和识别层")


FIX = Path(__file__).parent / "fixtures/settings"


def _boxes(state: str):
    """skip-settings-off-m23.png 的 OCR 框（720×1280），四个开关显示 state。"""
    return [("【プロデュース】演出スキップ設定", 1.0, (72, 121, 569, 156)), ("サポートイベントの", 1.0, (65, 316, 349, 348)),
            ("コミュスキップ", 1.0, (67, 352, 288, 383)), (state, 1.0, (580, 339, 635, 363)),
            ("各プロデュース開始時の", 1.0, (63, 527, 415, 561)), ("説明コミュスキップ", 1.0, (63, 561, 353, 597)), (state, 1.0, (580, 548, 635, 573)),
            ("定期公演『初』の「気になる", 1.0, (63, 663, 480, 696)), ("様子」コミュスキップ", 1.0, (63, 697, 385, 732)), (state, 1.0, (580, 684, 635, 709)),
            ("親愛度コミュ内の", 1.0, (63, 799, 320, 832)), ("映像スキップ", 1.0, (63, 833, 257, 868)), (state, 1.0, (577, 820, 636, 844)),
            ("次回以降表示しない", 1.0, (257, 992, 481, 1016)), ("決定", 1.0, (353, 1145, 411, 1177))]  # fmt: skip


@_real("settings/skip-settings-off-m23.png")
def test_read_real_frame():
    from hiro.produce.ops import ocr_boxes

    import cv2

    img = cv2.imread(str(FIX / "skip-settings-off-m23.png"))
    r = skip_settings.read(ocr_boxes(img))
    assert r is not None and set(r["items"].values()) == {"OFF"} and len(r["off"]) == 4
    assert r["ok"] is not None and abs(r["ok"][0] - 381) < 20 and abs(r["ok"][1] - 1160) < 20
    assert skip_settings.read(ocr_boxes(cv2.imread(str(FIX / "start-confirm-m23.png")))) is None


def test_handle_turns_on_then_decides():
    clicks, frames = [], [_boxes("ON")]
    r = skip_settings.handle(_boxes("OFF"), shoot=lambda: "img", ocr=lambda img: frames.pop(0), click=lambda x, y: clicks.append((x, y)),
                             sleep=lambda s: None)  # fmt: skip
    assert r == {
        "kind": "演出スキップ設定",
        "items": dict.fromkeys(skip_settings.ITEMS, "ON"),
        "option": False,
        "clicked": 4,
        "ok": True,
    }
    assert clicks[:4] == [(607, 351), (607, 560), (607, 696), (606, 832)] and clicks[4] == (382, 1161)


def test_handle_already_on_only_decides():
    clicks = []
    r = skip_settings.handle(
        _boxes("ON"),
        shoot=lambda: None,
        ocr=lambda img: [],
        click=lambda x, y: clicks.append((x, y)),
        sleep=lambda s: None,
    )
    assert r["ok"] and r["clicked"] == 0 and clicks == [(382, 1161)]


def test_handle_stuck_off_still_decides_after_rounds():
    clicks = []
    r = skip_settings.handle(_boxes("OFF"), shoot=lambda: "img", ocr=lambda img: _boxes("OFF"), click=lambda x, y: clicks.append((x, y)),
                             sleep=lambda s: None)  # fmt: skip
    assert r["ok"] and r["clicked"] == 4 * skip_settings.ROUNDS and clicks[-1] == (382, 1161)


def test_not_this_dialog():
    assert skip_settings.handle([("Pドリンク使用確認", 1.0, (0, 0, 10, 10))], shoot=None, ocr=None, click=None) is None


def _ff_boxes(unread: str):
    """「コミュ早送り設定」（第二十五局，没存帧：按总控描述的文字和演出スキップ設定同一版式造）。"""
    return [("コミュ早送り設定", 1.0, (72, 121, 400, 156)), ("コミュを早送りしない", 1.0, (120, 330, 420, 365)),
            ("コミュを早送りする", 1.0, (120, 440, 400, 475)), ("未読コミュでも早送りする", 1.0, (63, 600, 420, 635)),
            (unread, 1.0, (580, 605, 635, 630)), ("次回以降表示しない", 1.0, (257, 992, 481, 1016)), ("決定", 1.0, (353, 1145, 411, 1177))]  # fmt: skip


def test_comm_fast_forward_picks_on_and_unread():
    """コミュ早送り設定：选「コミュを早送りする」（不是「しない」、不是「未読…」那行），「未読コミュでも早送りする」打开，再点決定。"""
    clicks, frames = [], [_ff_boxes("ON")]
    r = skip_settings.handle(_ff_boxes("OFF"), shoot=lambda: "img", ocr=lambda img: frames.pop(0), click=lambda x, y: clicks.append((x, y)),
                             sleep=lambda s: None)  # fmt: skip
    assert (
        r["kind"] == "早送り設定"
        and r["option"]
        and r["clicked"] == 1
        and r["ok"]
        and r["items"] == {"unread_ff": "ON"}
    )
    assert clicks == [(260, 457), (607, 617), (382, 1161)]


def test_voice_keeps_voice_on():
    """ボイス再生確認：点「ボイスありで再生」（不是「なしで再生」），再点決定。"""
    boxes = [("ボイス再生確認", 1.0, (72, 121, 330, 156)), ("ボイスありで再生", 1.0, (120, 400, 400, 435)),
             ("ボイスなしで再生", 1.0, (120, 520, 400, 555)), ("次回以降表示しない", 1.0, (257, 992, 481, 1016)),
             ("キャンセル", 1.0, (120, 1145, 300, 1177)), ("決定", 1.0, (440, 1145, 500, 1177))]  # fmt: skip
    clicks = []
    r = skip_settings.handle(
        boxes, shoot=lambda: None, ocr=lambda img: [], click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None
    )
    assert r["kind"] == "ボイス再生確認" and r["ok"] and clicks == [(260, 417), (470, 1161)]


@_real("settings/acquire-guide-acct2.png")
def test_acquire_guide_picks_hide_then_decides():
    """獲得ガイド表示設定（新账号第一次拿技能卡，选卡三选一中途弹）：选「獲得ガイドを表示しない」，再点決定（协调 11-14，Mac 账号 2）。"""
    from hiro.produce.ops import ocr_boxes

    import cv2

    boxes = ocr_boxes(cv2.imread(str(FIX / "acquire-guide-acct2.png")))
    clicks = []
    r = skip_settings.handle(
        boxes, shoot=lambda: None, ocr=lambda img: [], click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None
    )
    assert r["kind"] == "獲得ガイド表示設定" and r["option"] and r["ok"] and len(clicks) == 2
    (ox, oy), (kx, ky) = clicks
    assert 940 < oy < 990 and ox < 400  # 「表示しない」那行（「表示する」在 y≈890）
    assert abs(ky - 1160) < 15 and 300 < kx < 460  # 決定
