"""编成菜单从别的画面进（G0b，11-26）：游戏停在支援卡选择列表、弹窗这类画面时，不再报「请先回主页再运行」让玩家自己去点，
而是先点不改东西的按钮（キャンセル / 戻る / 閉じる / 底栏ホーム）退回来；永远不点「決定」。退几次还认不出才停。"""

from hiro.produce import formation_menu as fm
from hiro.produce.formation_writer import FormationWriteError

import pytest

PICKER = [("編成詳細", (40, 1080, 200, 1110)), ("キャンセル", (90, 1160, 260, 1200)), ("決定", (480, 1160, 600, 1200))]
DIALOG = [("確認", (300, 400, 420, 440)), ("閉じる", (280, 900, 440, 940))]
HOME = [("ホーム", (40, 1200, 120, 1230)), ("コンテスト", (500, 1200, 640, 1230)), ("アイドル", (220, 1200, 330, 1230))]


class Dev:
    def __init__(self, screens):
        self.screens, self.i, self.clicks = screens, -1, []

    def shoot(self):
        self.i = min(self.i + 1, len(self.screens) - 1)
        return self.i

    def ocr(self, img):
        return [(t, 1.0, b) for t, b in self.screens[img]]

    def click(self, x, y):
        self.clicks.append((x, y))
        self.i = min(self.i, len(self.screens) - 2)  # 点了以后下一张图换成下一屏

    def sleep(self, s):
        pass

    def snap(self, img, tag):
        pass


@pytest.fixture(autouse=True)
def _menu_on_marker(monkeypatch):
    # 测试里「编成页」用一个只有 MENU 字样的画面表示
    monkeypatch.setattr(
        fm, "read_menu", lambda img, texts: {"mode": "hajime"} if any(t == "MENU" for t, _ in texts) else None
    )


def test_picker_is_cancelled_then_menu():
    dev = Dev([PICKER, [("MENU", (0, 0, 10, 10))]])
    m = fm.Menu(dev).open()
    assert m == {"mode": "hajime"}
    assert dev.clicks == [(175, 1180)]  # 点的是キャンセル，不是決定


def test_dialog_closed_then_home_flow_starts():
    dev = Dev([DIALOG, HOME, [("MENU", (0, 0, 10, 10))]])
    fm.Menu(dev).open()
    assert dev.clicks[0] == (360, 920)  # 先关弹窗
    assert (540, 1180) not in dev.clicks


def test_unknown_screen_without_safe_button_stops_with_plain_hint():
    dev = Dev([[("なにか", (100, 100, 200, 140))]])
    with pytest.raises(FormationWriteError, match="首页"):
        fm.Menu(dev).open()
    assert dev.clicks == []
