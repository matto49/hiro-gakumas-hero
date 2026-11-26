"""N50（G3 第 3 轮 11-26 Windows）：结算停在选回忆照片页 / 保存确认框时，收尾和回首页都要能走完；确认框整句常被 OCR 断行读错。"""

import types

from hiro.runtime import nav, photo_pick
from hiro.vision.ocr import Box, Text

import pytest


def _t(text, x1, y1, x2, y2):
    return Text(text, Box(x1, y1, x2, y2), 1.0)


PHOTO = [_t("メモリーにするフォトを選んでください", 145, 432, 575, 455), _t("自動撮影", 163, 493, 230, 513),
         _t("次へ", 325, 1140, 391, 1177)]  # fmt: skip
CONFIRM = [_t("確認", 300, 380, 420, 410), _t("選択したフォトを", 100, 520, 360, 550), _t("メモリーとして保存しますか？", 100, 560, 520, 590),
           _t("※保存していないフォトは削除されます", 100, 620, 600, 650), _t("キャンセル", 120, 1140, 300, 1180),
           _t("決定", 450, 1140, 560, 1180)]  # fmt: skip


@pytest.fixture(autouse=True)
def _auto(monkeypatch):
    monkeypatch.delenv("HIRO_PHOTO_PICK", raising=False)
    monkeypatch.delenv("HIRO_LIVE", raising=False)


def test_confirm_detected_when_ocr_splits_or_misreads():
    assert photo_pick.is_photo_confirm([t.text for t in CONFIRM])
    assert photo_pick.is_photo_confirm(["※保存していないフォトは削除されます"])  # 只读到第二行也算
    assert photo_pick.is_photo_page(["メモリーにするフォトを選んで"])  # 截断也算


def test_goto_home_walks_photo_page_and_confirm():
    f_photo, f_confirm = types.SimpleNamespace(texts=PHOTO), types.SimpleNamespace(texts=CONFIRM)
    assert nav._photo_step(f_photo) == (358, 1158)
    assert nav._photo_step(f_confirm) == (505, 1160)


def test_goto_home_leaves_photo_to_player_when_they_pick(monkeypatch):
    monkeypatch.setenv("HIRO_PHOTO_PICK", "stop")
    assert nav._photo_step(types.SimpleNamespace(texts=PHOTO)) is None
