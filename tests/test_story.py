"""看初星コミュ（hiro.daily.story）：解析话列表 / 章列表 / 对话框。数据是 11-25 账号 3 实机画面的 RapidOCR 结果原样抄下。"""

from types import SimpleNamespace as NS

from hiro.daily import story

AFTER_SKIP = [  # 看完第 9 話回到 1 章话列表：第 10 話 NEW，第 11 話锁着
    ("丰初星コミュ", 0.9, (36, 38, 200, 77)), ("1章", 0.9, (287, 106, 439, 186)),
    ("はじめまして初星学園", 0.9, (236, 201, 487, 229)), ("11話", 0.9, (81, 335, 178, 376)),
    ("直前のコミュをみることで解放", 0.9, (91, 383, 372, 406)), ("NEW", 0.9, (43, 424, 99, 445)),
    ("第10話", 0.9, (43, 467, 177, 506)), ("第9話", 0.9, (43, 599, 177, 636)), ("第8話", 0.9, (43, 729, 177, 767)),
    ("第 7 話", 0.9, (43, 861, 177, 897)), ("第6話", 0.9, (43, 991, 177, 1027)), ("第5話", 0.9, (43, 1122, 177, 1159)),
]  # fmt: skip
CHAPTERS = [
    ("三初星コミュ", 0.9, (36, 38, 204, 77)), ("4章", 0.9, (322, 454, 380, 487)),
    ("直前のコミュをみることで解放", 0.9, (91, 558, 372, 582)), ("3章", 0.9, (324, 621, 380, 655)),
    ("直前のコミュをみることで解放", 0.9, (91, 725, 372, 753)), ("2章", 0.9, (324, 789, 380, 822)),
    ("直前のコミュをみることで解放", 0.9, (91, 894, 372, 926)), ("1章", 0.9, (324, 958, 378, 990)),
    ("はじめまして初星学園", 0.9, (326, 999, 566, 1025)),
]  # fmt: skip
PLAY_CONFIRM = [
    ("第9話", 0.9, (52, 473, 182, 510)), ("再生確認", 0.9, (58, 550, 188, 585)), ("• 縦画面", 0.9, (71, 884, 180, 912)),
    ("ボイスあり（5.54 MB）", 0.9, (108, 1025, 318, 1049)), ("• 横画面", 0.9, (387, 885, 495, 912)),
    ("• ボイスなし（2.33MB）", 0.9, (385, 1025, 633, 1053)), ("✕キャンセル", 0.9, (117, 1146, 311, 1176)),
    ("再生する", 0.9, (467, 1145, 579, 1177)),
]  # fmt: skip


def test_episodes_after_skip():
    rows = story.episodes(AFTER_SKIP)
    assert [(r["n"], r["new"], r["locked"]) for r in rows] == [
        (11, False, True), (10, True, False), (9, False, False), (8, False, False), (7, False, False),
        (6, False, False), (5, False, False),
    ]  # fmt: skip
    assert story.in_chapter(AFTER_SKIP, 1) and not story.in_chapter(AFTER_SKIP, 2)
    assert story.next_episode(rows, until=14)["n"] == 10
    assert story.next_episode(rows, until=9) is None  # 只看到第 9 話为止


def test_chapter_row_skips_locked_banner():
    assert story.chapter_row(CHAPTERS, 1) == (351, 974)
    assert not story.in_chapter(CHAPTERS, 1)  # 章列表不是话列表


def test_play_confirm_buttons():
    assert story.find(PLAY_CONFIRM, "再生確認")
    assert story.find(PLAY_CONFIRM, "縦画面") == (125, 898)
    assert story.find(PLAY_CONFIRM, "ボイスなし") == (509, 1039)
    assert story.find(PLAY_CONFIRM, "再生する") == (523, 1161)


class FakeRt:
    """按点击推进的假 runtime：drive 每拍用当前画面的 boxes 调 step。"""

    def __init__(self, screens, on_tap):
        self.screens, self.on_tap, self.taps = list(screens), on_tap, []
        self.device = NS(tap=self._tap)

    def _tap(self, x, y):
        self.taps.append((x, y))
        nxt = self.on_tap(self.taps, (x, y))
        if nxt is not None:
            self.screens = [nxt]

    def sleep(self, s):
        pass

    def drive(self, step, timeout, what, every=0.5):
        for _ in range(50):
            r = step(NS(boxes=self.screens[0], img=None))
            if r is not None:
                return r
        raise AssertionError(f"{what}：没走完")


def test_watch_does_not_return_before_player_shows():
    """点了「再生する」后头几帧还是话列表：不能当成已经看完（要先见过播放画面 / 跳过确认）。"""
    playing = [("1章9話", 0.9, (35, 39, 143, 73))]
    skip_confirm = [("スキップ確認", 0.9, (59, 922, 251, 957)), ("決定", 0.9, (495, 1146, 553, 1176))]
    stages = {"list_again": 0}

    def on_tap(taps, p):
        if p == (360, 618):
            return PLAY_CONFIRM
        if p == (523, 1161):  # 再生する：先回一帧列表（播放器还没起来），下一次点击前才变成播放画面
            stages["list_again"] += 1
            return [b for b in AFTER_SKIP if b[0] != "NEW"]
        if p == story.SKIP_BUTTON:
            return skip_confirm
        if p == (524, 1161):
            return AFTER_SKIP
        return None

    rt = FakeRt([AFTER_SKIP], on_tap)
    first = {"n": 9, "y": 618}
    # 列表帧出现后没有播放画面：drive 会一直等；这里让第一次等列表时切到播放画面
    orig = rt.drive

    def drive(step, timeout, what, every=0.5):
        if what.startswith("跳过"):
            r = step(NS(boxes=rt.screens[0], img=None))
            assert r is None  # 头一帧是列表，不能返回
            rt.screens = [playing]
        return orig(step, timeout, what, every)

    rt.drive = drive
    story.watch(rt, 1, first)
    assert story.SKIP_BUTTON in rt.taps and (524, 1161) in rt.taps
