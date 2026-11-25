"""回忆选择页的「ソート・フィルタ」弹窗（设计 5.4 / D10；坐标和文字来自 11-26 实机，见 Task 3 findings §2）。"""

from hiro.produce import memory_filter as mf

import pytest

# 弹窗「フィルタ」页里的内容（绝对 y，随滚动整体上移）：标题和复选框。タグ 组里也有 センス / ロジック / アノマリー，不能点错
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
        self.checked = {(t, y) for t, y in FILTER_ITEMS if t in CHECKABLE}
        self.clicks = []

    def shoot(self):
        return None

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
            self.checked = {(t, yy) for t, yy in FILTER_ITEMS if t in CHECKABLE}
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
    assert before == {"sort": "プロデュースクリアランク"}
    mf.apply_slice(p, "sense:hif")
    assert p.sort == "入手" and not p.popup
    assert checked_in(p, 900, 1090) == ["センス"]
    assert checked_in(p, 2150, 2300) == ["FESTIVAL"]
    assert checked_in(p, 3200, 3400) == ["アノマリー", "センス", "ロジック"]  # タグ 组没动


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
    assert len(p.checked) == sum(t in CHECKABLE for t, _ in FILTER_ITEMS)


def test_missing_popup_raises_instead_of_clicking_blind(monkeypatch):
    p = FakePicker()
    monkeypatch.setattr(p, "click", lambda x, y: p.clicks.append((x, y)))  # 点了没反应：弹窗一直不出来
    with pytest.raises(mf.MemoryFilterError):
        mf.apply_slice(p, "sense:hif")
    assert len(p.clicks) <= mf.OPEN_TRIES
