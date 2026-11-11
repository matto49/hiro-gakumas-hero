# 来源：digital-hiro tests/test_memory_picker.py（c3fc27f）
"""回忆选择页（hiro.produce.memory_picker）离线测试：总控上次扫描留下的半分辨率实拍帧（放大到 720x1280）。"""

import json
from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.produce import memory_picker as mp
from hiro.produce import ops

FIX = Path(__file__).parent / "fixtures" / "memory"


def frame(name):
    return cv2.resize(cv2.imread(str(FIX / name)), (720, 1280))


@pytest.mark.lowres_sample("回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_page_readers_on_real_frames():
    p1 = frame("picker-p1-daydreaming.jpg")
    b = ops.ocr_boxes(p1)
    assert mp.picker_open(b) and mp.detail_page(b) == 1
    assert mp.read_stats(b) == {"Vo": 139, "Da": 130, "Vi": 15, "hp": 0}
    card = mp.read_card_page(b)
    assert card["timing"] == "start" and card["card"]["card_id"] == "p_card-02-act-1_037" and card["card"]["upgrade"] == 1
    k = mp.read_card_page(ops.ocr_boxes(frame("picker-p1-kamifubuki.jpg")))
    assert k["timing"] == "mid" and k["card"]["card_id"] == "p_card-02-act-2_062"


@pytest.mark.lowres_sample("回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_ability_tiles_grouped():
    ab = mp.read_ability_page(ops.ocr_boxes(frame("picker-p2.jpg")))
    texts = [a["text"] for a in ab]
    assert any("初期Pポイント+30" in t for t in texts) and any("初期ビジュアル上昇+15" in t for t in texts)
    assert sum(a["disabled"] for a in ab) == 2  # 两张【センス専用】发動不可
    assert all("センス専用" in a["text"] for a in ab if a["disabled"])


@pytest.mark.parametrize("name", ["picker-p1-daydreaming.jpg", "picker-p1-kamifubuki.jpg", "picker-p2.jpg"])
def test_grid_three_full_rows_nine_cells(name):
    img = frame(name)
    rows = mp.grid_rows(img)
    assert len(rows) in (2, 3) and all(108 <= y2 - y1 <= 128 and y2 <= mp.ROW_BOTTOM_MAX for y1, y2 in rows)  # 压到浮动按钮的行不算
    cells = mp.grid_cells(img)
    assert len(cells) == 3 * len(rows) and len({c["hash"] for c in cells}) == len(cells)
    assert [c["center"][0] for c in cells[:3]] == list(mp.COL_X)


class FakeDevice:
    def __init__(self):
        self.frames = {"p1": frame("picker-p1-daydreaming.jpg"), "p2": frame("picker-p2.jpg"),
                       "closed": cv2.imread(str(Path(__file__).parent / "fixtures" / "shop" / "consult-r5.jpg"))}  # fmt: skip
        self.state, self.taps, self.swipes, self.t, self._ocr = "p1", [], 0, 0.0, {}

    def shoot(self):
        return self.frames[self.state]

    def click(self, x, y):
        self.taps.append((x, y))
        near = lambda p: abs(x - p[0]) <= 30 and abs(y - p[1]) <= 30  # noqa: E731
        if near(mp.PAGE_NEXT) and self.state == "p1":
            self.state = "p2"
        elif near(mp.PAGE_PREV) and self.state == "p2":
            self.state = "p1"
        elif near(mp.CANCEL_TAP):
            self.state = "closed"

    def swipe(self, *a):
        self.swipes += 1  # 画面不变 = 到底

    def ocr(self, im):
        key = next(k for k, v in self.frames.items() if v is im)
        if key not in self._ocr:
            self._ocr[key] = ops.ocr_boxes(im)
        return self._ocr[key]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.mark.lowres_sample("回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集")
@pytest.mark.fixtures
def test_scan_reads_each_cell_and_cancels(tmp_path):
    dev = FakeDevice()
    picker = mp.Picker(dev.shoot, dev.click, dev.swipe, ocr=dev.ocr, clock=dev.clock, sleep=dev.sleep)
    out = tmp_path / "inv.json"
    inv = mp.scan(picker, out)
    assert len(inv["entries"]) == 3 * len(mp.grid_rows(dev.frames["p1"]))  # 每个格子一条（同内容的回忆分开记，不再按内容去重）
    e = inv["entries"][0]
    assert e["card"]["name"] == "デイドリーミング" and e["timing"] == "start" and any("初期Pポイント" in a["text"] for a in e["abilities"])
    assert inv["complete"] and dev.state == "closed"  # 画面不动 = 到顶也到底
    assert (475, 1160) not in dev.taps  # 从不点「決定」
    assert json.loads(out.read_text())["entries"][0]["card"]["card_id"] == "p_card-02-act-1_037"


def test_scan_refuses_outside_picker(tmp_path):
    dev = FakeDevice()
    dev.state = "closed"
    picker = mp.Picker(dev.shoot, dev.click, dev.swipe, ocr=dev.ocr, clock=dev.clock, sleep=dev.sleep)
    with pytest.raises(RuntimeError):
        mp.scan(picker, tmp_path / "inv.json")
    assert dev.taps == []


# ---------------------------------------------------------------- 滚动核对（2026-11-01 实机：快滑带惯性，整行漏扫）

import numpy as np

PAGES = [cv2.imread(str(FIX / f"scan-{n}.jpg")) for n in ("0001-page000", "0002-page001", "0003-page002", "0004-page003")]


def list_rows():
    """4 张实拍页的完整行（实机核对过页与页之间没有重叠，行各不相同；压到浮动按钮的行不算）。"""
    out = []
    for img in PAGES:
        for y1, y2 in mp.grid_rows(img):
            out.append(img[y1 : y1 + 118, :].copy())
    return out


class ScrollList:
    """把实拍行按行距 137.5 排成一个长列表，按滚动位置重画回忆格区；fling = 第 k 次上滑多滚的行数。"""

    TOP = 600  # 第 0 行在滚动位置 0 时的上沿

    def __init__(self, fling=None, slop=1.0, stuck=(), copies=1):
        self.rows, self.base = list_rows() * copies, PAGES[0].copy()
        if copies > 1:  # 重复拼接时给每一份打上不同的色块，保证行各不相同
            for i in range(len(self.rows)):
                self.rows[i] = self.rows[i].copy()
                self.rows[i][40:80, 100:140] = ((i * 37) % 255, (i * 91) % 255, (i * 53) % 255)
        self.slop, self.stuck = slop, set(stuck)
        gap = self.base[575:580, 40:680].reshape(-1, 3).mean(0)
        self.base[mp.GRID_Y[0] : mp.GRID_Y[1] + 60, :] = gap.astype(np.uint8)
        self.offset, self.fling, self.swipes, self.read = 0.0, dict(fling or {}), 0, []
        self.max_offset = (len(self.rows) - 2) * mp.ROW_PITCH

    def shoot(self):
        img = self.base.copy()
        for i, row in enumerate(self.rows):
            y = int(round(self.TOP + i * mp.ROW_PITCH - self.offset))
            y1, y2 = max(y, mp.GRID_Y[0] - 5), min(y + 118, mp.GRID_Y[1] + 60)
            if y2 > y1:
                img[y1:y2] = row[y1 - y : y2 - y]
        img[1005:1060, 30:690] = (250, 250, 250)  # 浮在列表上的「編成詳細 / 筛选 / 排序」按钮
        return img

    def swipe(self, x1, y1, x2, y2):
        self.swipes += 1
        if self.swipes in self.stuck:
            return  # 这一次滑动没让列表动
        rows = (y1 - y2) * self.slop / mp.ROW_PITCH + (self.fling.pop(self.swipes, 0) if y1 > y2 else 0)
        self.offset = min(max(0.0, self.offset + rows * mp.ROW_PITCH), self.max_offset)

    def row_at(self, y):
        return int((y - self.TOP + self.offset) // mp.ROW_PITCH)


class ListPicker(mp.Picker):
    def __init__(self, lst):
        super().__init__(lst.shoot, lambda x, y: None, lst.swipe, ocr=lambda im: [], clock=lambda: 0.0, sleep=lambda s: None)
        self.lst = lst

    def select(self, cell):
        self.lst.read.append((self.lst.row_at(cell["center"][1]), cell["col"]))

    def read_selected(self):
        return {}

    def cancel(self):
        return True


def run_scan(tmp_path, lst, monkeypatch):
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    return mp.scan(ListPicker(lst), tmp_path / "inv.json")


@pytest.mark.fixtures
def test_scan_reads_every_row_once(tmp_path, monkeypatch):
    lst = ScrollList()
    lst.offset = 4 * mp.ROW_PITCH  # 从列表中间开始：先滑回顶部
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst) and len(lst.read) == len(all_cells(lst))
    assert inv["complete"] and inv["gaps"] == [] and [e["slot"] for e in inv["entries"]] == list(range(len(all_cells(lst))))


@pytest.mark.fixtures
def test_scan_backtracks_after_fling_overshoot(tmp_path, monkeypatch):
    lst = ScrollList(fling={2: 2})  # 第 2 次上滑多滚 2 行（实机快滑的惯性）
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst) and len(lst.read) == len(all_cells(lst))
    assert inv["gaps"] == []


def test_row_overlap_tolerates_band_shift():
    img = PAGES[0]
    rows = mp.page_rows(img)
    shifted = np.roll(img, -2, axis=0)  # 行带上下差 2 像素
    assert mp.row_overlap(rows, mp.page_rows(shifted)) == len(rows)
    assert mp.row_overlap(rows, mp.page_rows(PAGES[1])) == 0  # 实机：相邻两页没重叠（漏行）


def all_cells(lst):
    return [(r, c) for r in range(len(lst.rows)) for c in range(3)]


@pytest.mark.fixtures
def test_scan_with_slop_and_occluded_bottom_rows(tmp_path, monkeypatch):
    """第二次实机扫描：每次少滑一点（3%），几页后最下面一行跑到浮动按钮下面、和下一页对不上，最后被误判到底。"""
    lst = ScrollList(slop=0.97, copies=3)  # 36 行
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst) and len(lst.read) == len(all_cells(lst))
    assert inv["complete"] and inv["gaps"] == []


@pytest.mark.fixtures
def test_one_stuck_swipe_is_not_the_end(tmp_path, monkeypatch):
    lst = ScrollList(stuck={4}, copies=2)  # 中途有一次滑动没动（不是到底）
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst)
    assert inv["complete"]


@pytest.mark.fixtures
def test_occluded_row_is_not_counted():
    lst = ScrollList()
    lst.offset = 0.6 * mp.ROW_PITCH  # 第 3 个完整行的下沿压到按钮下面
    rows = mp.grid_rows(lst.shoot())
    assert rows and all(y2 <= mp.ROW_BOTTOM_MAX for _, y2 in rows)
