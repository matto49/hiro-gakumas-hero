"""回忆选择页的「ソート・フィルタ」弹窗（设计 5.4 / D10；坐标和文字来自 11-26 实机，见 Task 3 findings §2）。"""

from hiro.produce import memory_filter as mf

import pytest

# 弹窗「フィルタ」页里的内容（绝对 y，随滚动整体上移）：标题和复选框。タグ 组里也有 センス / ロジック / アノマリー，不能点错。
# 语义（11-26 实机）：复选框灰勾 = 没选，橙色 = 选中；一组里一个都没选 = 不按这组筛
FILTER_ITEMS = [
    ("アイドル", 200), ("全解除", 202), ("プロデュース用スキルカード", 830), ("プラン", 890),
    ("共通", 953), ("センス", 953), ("ロジック", 1027), ("アノマリー", 1029), ("スキルカードタイプ", 1091),
    ("メモリーアビリティ", 1500), ("プロデュース", 2100), ("全解除P", 2100), ("定期公演『初』", 2167),
    ("NEXT IDOL AUDITION", 2168), ("FESTIVAL", 2254), ("保護済みメモリー", 2400), ("タグ", 3000),
    ("センス", 3300), ("ロジック", 3360), ("アノマリー", 3360),
]  # fmt: skip
X = {"共通": 124, "センス": 451, "ロジック": 147, "アノマリー": 473, "全解除": 475, "全解除P": 475,
     "定期公演『初』": 180, "NEXT IDOL AUDITION": 531, "FESTIVAL": 152}  # fmt: skip
CHECKABLE = {"共通", "センス", "ロジック", "アノマリー", "定期公演『初』", "NEXT IDOL AUDITION", "FESTIVAL"}


class FakePicker:
    def __init__(self, sort="プロデュースクリアランク"):
        self.sort, self.popup, self.tab, self.offset = sort, False, "filter", 0
        self.pending_sort = sort
        # 11-26 实机：重置后全是灰勾 = 都没选（不筛选）；点一下变橙 = 选中，只显示选中的
        self.checked = set()
        self.clicks = []

    def shoot(self):
        return _icon_frame(getattr(self, "dir", "desc"))  # 选择页的升降序按钮（缺省新的在前）

    def sleep(self, s):
        pass

    def _visible(self):
        out = []
        if not self.popup:
            return [
                (self.sort, (430, 1040, 540, 1064)),
                ("キャンセル", (160, 1150, 270, 1172)),
                ("決定", (490, 1150, 540, 1172)),
            ]
        out += [("ソート・フィルタ", (100, 100, 280, 126)), ("ソート", (180, 1020, 240, 1044)), ("フィルタ", (480, 1020, 550, 1044)),
                ("キャンセル", (160, 1150, 270, 1172)), ("決定", (490, 1150, 540, 1172))]  # fmt: skip
        if self.tab == "sort":
            out += [("プロデュースクリアランク", (120, 264, 300, 288)), ("入手", (410, 264, 445, 288)),
                    ("アイドル", (100, 338, 170, 362)), ("タグ", (410, 338, 445, 362))]  # fmt: skip
        else:
            out.append(("フィルタをリセット", (460, 940, 610, 962)))
            for t, y in FILTER_ITEMS:
                sy = y - self.offset
                if 150 <= sy <= 920:
                    x = X.get(t, 200)
                    out.append((t.rstrip("P"), (x - 40, sy - 12, x + 40, sy + 12)))
        return out

    def ocr(self, img):
        return [(t, 1.0, b) for t, b in self._visible()]

    def swipe(self, x1, y1, x2, y2):
        if self.popup and self.tab == "filter":
            self.offset = max(0, min(3000, self.offset + (y1 - y2)))

    def click(self, x, y):
        self.clicks.append((x, y))
        hit = next((t for t, (x1, y1, x2, y2) in self._visible() if x1 <= x <= x2 and y1 <= y <= y2), None)
        hit = hit.lstrip("•") if hit else hit
        if not self.popup:
            if hit == self.sort:
                self.popup = True
            return
        if hit == "決定":
            self.popup, self.sort = False, self.pending_sort
        elif hit == "キャンセル":
            self.popup = False
        elif hit in ("ソート", "フィルタ"):
            self.tab = "sort" if hit == "ソート" else "filter"
        elif self.tab == "sort" and hit in ("プロデュースクリアランク", "入手", "アイドル", "タグ"):
            self.pending_sort = hit
        elif hit == "フィルタをリセット":
            self.checked = set()
        elif hit == "全解除":
            sec = [it for it in FILTER_ITEMS if it[1] - self.offset > y - 20]  # 这一行往下同一组
            grp = {"プロデュース": ("定期公演『初』", "NEXT IDOL AUDITION", "FESTIVAL")}
            near = next((t for t, yy in FILTER_ITEMS if t == "プロデュース" and abs(yy - self.offset - y) < 20), None)
            if near:
                self.checked -= {(t, yy) for t, yy in FILTER_ITEMS if t in grp[near]}
            del sec
        elif hit in CHECKABLE:
            item = min((it for it in FILTER_ITEMS if it[0] == hit), key=lambda it: abs(it[1] - self.offset - y))
            self.checked ^= {item}


def checked_in(p, lo, hi):
    return sorted(t for t, y in p.checked if lo <= y <= hi)


def test_apply_slice_sets_sort_plan_and_produce():
    p = FakePicker()
    before = mf.read_state(p)
    assert before == {"sort": "プロデュースクリアランク", "dir": "desc"}
    mf.apply_slice(p, "sense:hif")
    assert p.sort == "入手" and not p.popup
    assert checked_in(p, 900, 1090) == ["センス"]
    assert checked_in(p, 2150, 2300) == ["FESTIVAL"]
    assert checked_in(p, 3200, 3400) == []  # タグ 组没动（没选 = 不筛选）


def test_apply_common_nia():
    p = FakePicker()
    mf.apply_slice(p, "common:nia")
    assert checked_in(p, 900, 1090) == ["共通"] and checked_in(p, 2150, 2300) == ["NEXT IDOL AUDITION"]


def test_restore_puts_sort_back_and_resets_filter():
    p = FakePicker(sort="アイドル")
    st = mf.read_state(p)
    mf.apply_slice(p, "logic:hajime")
    assert mf.restore(p, st)
    assert p.sort == "アイドル" and not p.popup
    assert p.checked == set()  # 重置 = 都没选


def test_missing_popup_raises_instead_of_clicking_blind(monkeypatch):
    p = FakePicker()
    monkeypatch.setattr(p, "click", lambda x, y: p.clicks.append((x, y)))  # 点了没反应：弹窗一直不出来
    with pytest.raises(mf.MemoryFilterError):
        mf.apply_slice(p, "sense:hif")
    assert len(p.clicks) <= mf.OPEN_TRIES


def test_confirm_refuses_when_popup_already_closed():
    """终审 I5：弹窗的「決定」和选择页的「決定」几乎同一位置；弹窗已经不在时点「決定」会把选中的回忆装进编成。先确认弹窗还开着。"""
    p = FakePicker()
    assert not p.popup
    with pytest.raises(mf.MemoryFilterError):
        mf._confirm(p)
    assert p.clicks == []


def test_close_popup_cancels_only_when_open():
    """终审 I6：恢复失败时弹窗可能还开着，退出选择页前先关弹窗（点弹窗的キャンセル），不然那一下キャンセル关的是弹窗。"""
    p = FakePicker()
    p.popup = True
    assert mf.close_popup(p) and not p.popup
    n = len(p.clicks)
    assert mf.close_popup(p) and len(p.clicks) == n  # 没开就不点


def test_real_device_labels_with_radio_dots_and_icon_noise():
    """11-26 实机：排序选项前带单选圆点（OCR「•入手」「• プロデュースクリアランク」），按钮上的排序名带图标杂字
    （「TE プロデュースク…」「7入手」）：都要认得出。"""
    assert mf._sort_of(mf.fold("TE プロデュースク…")) == "プロデュースクリアランク"
    assert mf._sort_of(mf.fold("7入手")) == "入手"
    assert mf._find([(mf.fold("•入手"), (410, 264, 445, 288))], "入手", y_max=600) is not None


def test_apply_slice_with_radio_dot_labels():
    p = FakePicker()
    orig = p._visible

    def dotted():
        return [("•" + t if t in ("入手", "プロデュースクリアランク", "アイドル", "タグ") and p.popup and p.tab == "sort" else t, b)
                for t, b in orig()]  # fmt: skip

    p._visible = dotted
    mf.apply_slice(p, "sense:hif")
    assert p.sort == "•入手".lstrip("•")


def _icon_frame(direction):
    """画一张带升降序按钮的帧：箭头竖线 + 箭头尖（↓ 尖在下 = 新的在前；↑ 尖在上 = 旧的在前）。11-26 实机图标就是这样。"""
    import numpy as np

    img = np.zeros((1280, 720, 3), np.uint8)
    x0, y0 = mf.DIR_ICON[0], mf.DIR_ICON[1]
    img[y0 + 6 : y0 + 36, x0 + 10 : x0 + 12] = 255  # 竖线
    tip = range(y0 + 30, y0 + 37) if direction == "desc" else range(y0 + 5, y0 + 12)
    for i, y in enumerate(tip):
        w = (len(tip) - i) if direction == "desc" else (i + 1)
        img[y, x0 + 11 - w : x0 + 12 + w] = 255
    return img


def test_sort_direction_from_icon():
    assert mf.sort_direction(_icon_frame("desc")) == "desc"
    assert mf.sort_direction(_icon_frame("asc")) == "asc"
    import numpy as np

    assert mf.sort_direction(np.zeros((1280, 720, 3), np.uint8)) is None


def test_apply_slice_flips_to_newest_first():
    """入手排序要新的在前（增量补扫靠它：反了会读到旧回忆就停）。方向不对就点一下按钮，再核对。"""
    p = FakePicker()
    p.dir = "asc"
    p.shoot = lambda: _icon_frame(p.dir)
    orig_click = p.click

    def click(x, y):
        if (x, y) == mf.DIR_BTN and not p.popup:
            p.dir = "desc" if p.dir == "asc" else "asc"
            p.clicks.append((x, y))
            return
        orig_click(x, y)

    p.click = click
    mf.apply_slice(p, "sense:hif")
    assert p.dir == "desc"


def test_unknown_direction_stops():
    import numpy as np

    p = FakePicker()
    p.shoot = lambda: np.zeros((1280, 720, 3), np.uint8)
    with pytest.raises(mf.MemoryFilterError):
        mf.apply_slice(p, "sense:hif")


def test_restore_puts_direction_back():
    """扫描时改成了新的在前；玩家原来是旧的在前，恢复时切回去。"""
    p = FakePicker()
    p.dir = "asc"
    orig_click = p.click

    def click(x, y):
        if (x, y) == mf.DIR_BTN and not p.popup:
            p.dir = "desc" if p.dir == "asc" else "asc"
            p.clicks.append((x, y))
            return
        orig_click(x, y)

    p.click = click
    st = mf.read_state(p)
    assert st["dir"] == "asc"
    mf.apply_slice(p, "sense:hif")
    assert p.dir == "desc"
    assert mf.restore(p, st) and p.dir == "asc"


def test_empty_slice_raises_empty_slice():
    """11-26 实机：这一片没有回忆时点「決定」，游戏提示「条件に当てはまるメモリーがありません」、弹窗不关。"""
    p = FakePicker()
    orig_click, orig_visible = p.click, p._visible

    def click(x, y):
        hit = next((t for t, (x1, y1, x2, y2) in p._visible() if x1 <= x <= x2 and y1 <= y <= y2), None)
        if hit == "決定" and p.popup:
            p.toast = True
            return
        orig_click(x, y)

    p.toast = False
    p.click = click
    p._visible = lambda: (
        orig_visible() + ([("条件に当てはまるメモリーがありません", (200, 600, 520, 625))] if p.toast else [])
    )
    with pytest.raises(mf.EmptySlice):
        mf.apply_slice(p, "common:hif")
    assert p.popup  # 弹窗还开着，下一片接着设


def test_restore_reports_false_when_original_state_unknown():
    """#116 审查重要 4：扫描前没认出排序 / 升降序，就没法确认恢复了：返回 False（调用方提示去游戏里看一下），不能谎报恢复成功。"""
    import numpy as np

    p = FakePicker()
    assert mf.restore(p, {"sort": None, "dir": "desc"}) is False
    p = FakePicker()
    assert mf.restore(p, {"sort": "プロデュースクリアランク", "dir": None}) is False
    p = FakePicker()
    assert mf.restore(p, {"sort": "プロデュースクリアランク", "dir": "desc"}) is True
    del np


class SelectedTabUnreadable(FakePicker):
    """G2 11-26（Mac rc.7，RapidOCR）：选中的页签是橙字白底，RapidOCR 认不出；「ソート」页上看得到「表示順」。"""

    def _visible(self):
        out = super()._visible()
        if self.popup:
            cur = "ソート" if self.tab == "sort" else "フィルタ"
            out = [(t, b) for t, b in out if t != cur]
            if self.tab == "sort":
                out.append(("表示順", (40, 190, 140, 214)))
        return out

    def click(self, x, y):
        # 页签按位置点（TABS）时认得出来
        if self.popup:
            for name, (tx, ty) in mf.TABS.items():
                if abs(x - tx) < 60 and abs(y - ty) < 20:
                    self.clicks.append((x, y))
                    self.tab = "sort" if name == "ソート" else "filter"
                    return
        super().click(x, y)


def test_selected_tab_unreadable_still_switches():
    p = SelectedTabUnreadable()
    p.tab = "sort"  # 弹窗打开时停在「ソート」页（实机）
    mf.apply_slice(p, "logic:nia")
    assert p.sort == "入手" and checked_in(p, 890, 1091) == ["ロジック"]
