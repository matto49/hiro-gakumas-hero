"""本战开始确认页在回忆列表里按属性选回忆（hiro.produce.memory_select.select_memory，主循环开局用）。
原来经旧主循环的包装 hiro.loop.kaa_patches.select_memory 测（没给设备时去拿 kotonebot 的，单独跑报 ContextNotInitializedError），
2026-11-24 包装随旧主循环删了，改成直接测。"""

from hiro.produce import memory_select as ms

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _no_snap(monkeypatch):
    monkeypatch.setattr(ms, "_memory_snap", lambda img, tag: None)  # 不往 calib/ 存帧


def test_select_memory_finds_by_params():
    headers = {ms.MEMORY_CELLS[4]: ["1498", "2066", "944"]}
    state = {"cur": None}
    clicks = []

    def click(x, y):
        clicks.append((x, y))
        state["cur"] = (x, y)

    ok = ms.select_memory(("1498", "2066", "944"), shoot=lambda: None, click=click, swipe=lambda *a: None,
                          texts_of=lambda img: headers.get(state["cur"], ["1697", "1936", "815"]), sleep=lambda s: None)  # fmt: skip
    assert (
        ok
        and clicks[0] == ms.MEMORY_CHANGE_BTN
        and clicks[1] == ms.MEMORY_SORT_BTN
        and clicks[-1] == ms.MEMORY_OK_BTN
        and ms.MEMORY_CELLS[4] in clicks
    )


def test_select_memory_matches_prefixed_and_confused_ocr():
    """第十八局本战：列表上方属性读成「+1434」，按块精确相等找不到；拼接后按值找（允许 1 位常见混淆），且不跨块拼出假数。"""
    headers = {ms.MEMORY_CELLS[0]: ["スター性", "ボーカル", "+1434", "ダンス", "2296", "1179"],
               ms.MEMORY_CELLS[1]: ["ボーカル", "1510", "ダンス", "2360", "1029"]}  # fmt: skip
    state = {"cur": None}
    picked = []

    def click(x, y):
        state["cur"] = (x, y)
        picked.append((x, y))

    ok = ms.select_memory(("1434", "2296", "1179"), shoot=lambda: None, click=click, swipe=lambda *a: None,
                          texts_of=lambda img: headers.get(state["cur"], ["1"]), sleep=lambda s: None)  # fmt: skip
    assert ok and ms.MEMORY_CELLS[0] in picked
    assert not ms.memory_value_seen("1936", "1697|19|36")  # 不跨块拼数


def test_select_memory_sort_by_ocr_and_fast_scan():
    """第二十二局：排序选项按固定坐标点、列表按「タグ」排，翻完 12 页 5 分半没找到。现在按 OCR 找「入手」点，每格等 0.6s。"""
    clicks, t = [], [0.0]
    sort_dialog = [("ソート・フィルタ", 1.0, (200, 60, 520, 100)), ("タグ", 1.0, (380, 260, 440, 286)), ("入手", 1.0, (180, 380, 240, 406)),
                   ("決定", 1.0, (470, 1145, 530, 1175))]  # fmt: skip
    img = np.zeros((1280, 720, 3), np.uint8)
    ok = ms.select_memory(("1", "2", "3"), shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None,
                          texts_of=lambda im: ["9999"], sleep=lambda s: t.__setitem__(0, t[0] + s), boxes_of=lambda im: sort_dialog)  # fmt: skip
    assert not ok
    assert (
        clicks[2] == (210, 393) and ms.MEMORY_SORT_NEWEST not in clicks
    )  # 点的是 OCR 找到的「入手」，不是固定坐标（也不是「タグ」）
    assert (500, 1160) in clicks  # 「決定」
    assert t[0] < 120  # 翻满 12 页 × 9 格的等待合计 < 2 分钟（原来约 5 分钟）
