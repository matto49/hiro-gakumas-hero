"""竞赛编队（hiro.daily.contest_unit）：页面解析。数据是 11-25 账号 3 实机画面的 RapidOCR / Vision 结果原样抄下。"""

from types import SimpleNamespace as NS

from hiro.daily import contest_unit as cu

UNIT = [
    ("黒ユニット編成", 0.9, (17, 43, 234, 76)), ("ステージ1", 0.9, (72, 133, 166, 154)),
    ("総合力合計 20640", 0.9, (207, 218, 450, 249)), ("センス", 0.9, (216, 305, 287, 329)), ("総合力", 0.9, (458, 303, 521, 326)),
    ("8010", 0.9, (553, 303, 633, 329)), ("リハーサル開始", 0.9, (47, 1096, 184, 1116)), ("リセット", 0.9, (270, 1097, 348, 1116)),
    ("おまかせ", 0.9, (393, 1094, 473, 1115)), ("マイユニット一覧", 0.9, (519, 1096, 675, 1116)),
]  # fmt: skip
MAIN = [
    ("コンテスト", 0.9, (65, 44, 205, 73)), ("総合力合計", 0.9, (63, 526, 166, 549)), ("20640", 0.9, (184, 523, 309, 556)),
    ("リハーサル", 0.9, (389, 528, 486, 547)), ("ユニット編成", 0.9, (534, 527, 655, 550)),
    ("総合力合計", 0.9, (86, 605, 188, 629)), ("20652", 0.9, (74, 643, 218, 680)),
]  # fmt: skip
ENTRY = [
    ("GRADE", 0.9, (331, 936, 389, 953)), ("レートPt", 0.9, (443, 839, 527, 858)), ("コンテスト", 0.9, (456, 880, 610, 912)),
    ("CONTEST", 0.9, (283, 1010, 440, 1055)), ("コンテスト", 0.9, (446, 1155, 549, 1201)),
]  # fmt: skip
CONFIRM = [("おまかせ編成", 0.9, (60, 923, 251, 956)), ("よろしいですか？", 0.9, (61, 1044, 251, 1072)),
           ("キャンセル", 0.9, (151, 1148, 311, 1176)), ("決定", 0.9, (493, 1146, 553, 1176))]  # fmt: skip
DONE = [("おまかせ確認", 0.9, (59, 96, 251, 130)), ("おまかせでメモリーが編成されました", 0.9, (56, 182, 461, 207)),
        ("閉じる", 0.9, (342, 1148, 424, 1174))]  # fmt: skip


def test_total_power_same_box_and_right_box():
    assert cu.total_power(UNIT) == 20640
    assert cu.total_power(MAIN) == 20640  # 主页：数字在右边的框；下面对手的「総合力合計」不算（y 超出顶部）


def test_pages():
    assert cu.at_unit_page(UNIT) and not cu.at_unit_page(MAIN)
    assert cu.find(MAIN, "ユニット編成", y_min=480, y_max=600, x_min=450) == (594, 538)
    assert cu.find(ENTRY, "コンテスト", y_min=800, y_max=1000, x_min=400) == (533, 896)


def test_omakase_flow_taps_in_order():
    """编队页 → 点おまかせ → 确认框 決定 → 结果框 閉じる → 回编队页结束。"""
    screens = {"s": UNIT}
    taps = []
    nxt = {(433, 1104): CONFIRM, (523, 1161): DONE, (383, 1161): UNIT}

    def tap(x, y):
        taps.append((x, y))
        screens["s"] = nxt.get((x, y), screens["s"])

    def drive(step, timeout, what, every=0.5):
        for _ in range(20):
            if (r := step(NS(boxes=screens["s"]))) is not None:
                return r
        raise AssertionError(what)

    rt = NS(device=NS(tap=tap), sleep=lambda s: None, drive=drive)
    cu.omakase(rt)
    assert taps == [(433, 1104), (523, 1161), (383, 1161)]


def test_home_button_on_dark_toolbar():
    """竞赛「ユニット編成」页左下是深色工具栏，Common.ButtonToolbarHome 只有 0.79（阈值 0.8），goto_home 60 秒回不去。"""
    import os
    from pathlib import Path

    from hiro.runtime import nav
    from hiro.runtime.frame import Frame

    import cv2
    import pytest

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "contest-unit-dark-toolbar.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/contest-unit-dark-toolbar.png")
    m = nav.home_button(Frame(cv2.imread(str(path)), 0.0))
    assert m is not None and abs(m.rect.center.x - 85) < 15 and abs(m.rect.center.y - 1211) < 15
