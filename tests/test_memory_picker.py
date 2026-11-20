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
    assert json.loads(out.read_text(encoding="utf-8"))["entries"][0]["card"]["card_id"] == "p_card-02-act-1_037"


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

    def __init__(self, fling=None, slop=1.0, stuck=(), copies=1, dead_after_row=None):
        self.rows, self.base = list_rows() * copies, PAGES[0].copy()
        if copies > 1:  # 重复拼接时给每一份打上不同的色块，保证行各不相同
            for i in range(len(self.rows)):
                self.rows[i] = self.rows[i].copy()
                for c in range(3):  # 每格都打一块（压进缩略图区），重复的几份行也对不上
                    x0 = mp.COL_X[c] - 60
                    self.rows[i][30:90, x0 : x0 + 120] = ((i * 37 + c * 80) % 255, (i * 91) % 255, (i * 53 + c * 40) % 255)
        self.slop, self.stuck = slop, set(stuck)
        self.dead_after_row = dead_after_row  # 滚到这一行之后，从原起点 (360, SWIPE_FROM) 起滑列表不动（起点落在锁格上，11-16 账号 2 假到底）
        gap = self.base[575:580, 40:680].reshape(-1, 3).mean(0)
        self.base[mp.GRID_Y[0] : mp.GRID_Y[1] + 60, :] = gap.astype(np.uint8)
        self.offset, self.fling, self.swipes, self.read = 0.0, dict(fling or {}), 0, []
        self.selected = None  # 最后点过的格子 (行, 列)：画橙色选中框（实机：读完一页，选中框留在下一页的重叠行上）
        self.max_offset = (len(self.rows) - 2) * mp.ROW_PITCH

    def shoot(self):
        img = self.base.copy()
        for i, row in enumerate(self.rows):
            y = int(round(self.TOP + i * mp.ROW_PITCH - self.offset))
            y1, y2 = max(y, mp.GRID_Y[0] - 5), min(y + 118, mp.GRID_Y[1] + 60)
            if y2 > y1:
                img[y1:y2] = row[y1 - y : y2 - y]
        if self.selected is not None:
            r, c = self.selected
            y = int(round(self.TOP + r * mp.ROW_PITCH - self.offset))
            x = mp.COL_X[c] - mp.CELL_W // 2
            if mp.GRID_Y[0] <= y and y + 118 <= mp.GRID_Y[1] + 60:
                cv2.rectangle(img, (x + 6, y + 6), (x + mp.CELL_W - 7, y + 111), (0, 140, 255), 12)  # 选中框画在格子里（伸进缩略图区）
        img[1005:1060, 30:690] = (250, 250, 250)  # 浮在列表上的「編成詳細 / 筛选 / 排序」按钮
        return img

    def swipe(self, x1, y1, x2, y2):
        self.swipes += 1
        if self.swipes in self.stuck:
            return  # 这一次滑动没让列表动
        if (self.dead_after_row is not None and self.offset >= self.dead_after_row * mp.ROW_PITCH
                and (x1, y1) == (360, mp.SWIPE_FROM) and y1 > y2):  # fmt: skip
            return
        rows = (y1 - y2) * self.slop / mp.ROW_PITCH + (self.fling.pop(self.swipes, 0) if y1 > y2 else 0)
        self.offset = min(max(0.0, self.offset + rows * mp.ROW_PITCH), self.max_offset)

    def row_at(self, y):
        return int((y - self.TOP + self.offset) // mp.ROW_PITCH)


class ListPicker(mp.Picker):
    def __init__(self, lst):
        super().__init__(lst.shoot, lambda x, y: None, lst.swipe, ocr=lambda im: [], clock=lambda: 0.0, sleep=lambda s: None)
        self.lst = lst

    def select(self, cell):
        self.select_checked(cell)

    def select_checked(self, cell):
        rc = (self.lst.row_at(cell["center"][1]), cell["col"])
        self.lst.read.append(rc)
        self.lst.selected = rc
        return True, None

    def read_selected(self):
        r, c = self.lst.read[-1]
        return {"card": {"card_id": f"m{r}-{c}"}}  # 每格内容不同

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


@pytest.mark.fixtures
def test_scan_stops_at_gap_instead_of_rereading_old_rows(tmp_path, monkeypatch):
    """账号 2 11-15：对不上后往回滑了几行接着记，旧行被当成新行记了一遍又一遍（仕切り直し 记了 7 份），后面行号全偏。
    现在：找不到上一页最后一行就停，complete=False，记 aborted；已记的行号不重复。"""
    lst = ScrollList(fling={4: 5}, copies=3)  # 第 4 次滑动（前两次是回顶部的下滑）多滚 5 行：往回滑能找回来，照常扫完
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert inv["complete"] and not inv.get("aborted") and sorted(set(lst.read)) == all_cells(lst)
    lst = ScrollList(fling={4: 12}, copies=3)  # 多滚 12 行：往回滑 6 行也找不到
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert inv.get("aborted") and not inv["complete"] and inv["gaps"]
    slots = [e["slot"] for e in inv["entries"]]
    assert len(slots) == len(set(slots)) and len(lst.read) == len(set(lst.read))  # 没有重读、没有重号


class StalePicker(ListPicker):
    """点格子后详情栏不刷新：stale 里的格子第一次读到上一格的内容；always 里的格子一直读到上一格的内容。"""

    def __init__(self, lst, stale=(), always=()):
        super().__init__(lst)
        self.stale, self.always, self.seen = set(stale), set(always), {}

    def read_selected(self):
        rc = self.lst.read[-1]
        self.seen[rc] = self.seen.get(rc, 0) + 1
        prev = next((x for x in reversed(self.lst.read) if x != rc), rc)  # 上一个不同的格子（重读时同一格会再记一次）
        if rc in self.always or (rc in self.stale and self.seen[rc] == 1):
            return {"card": {"card_id": f"m{prev[0]}-{prev[1]}"}} if prev != rc else {"card": {"card_id": "x"}}
        return {"card": {"card_id": f"m{rc[0]}-{rc[1]}"}}


@pytest.mark.fixtures
def test_stale_detail_is_reread_or_marked_suspect(tmp_path, monkeypatch):
    """详情栏没刷新（读到上一格的内容、缩略图不同）：再点一次重读；重读还是一样就标 suspect=same_as_prev（formation_plan 不选）。"""
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    lst = ScrollList()
    inv = mp.scan(StalePicker(lst, stale={(1, 1)}, always={(2, 0)}), tmp_path / "inv.json")
    by = {tuple(e["cell"]): e for e in inv["entries"]}
    assert by[(1, 1)]["card"]["card_id"] == "m1-1" and "suspect" not in by[(1, 1)]  # 重读后对了
    assert by[(2, 0)]["suspect"] == "same_as_prev" and inv["suspects"] == 1


def test_mark_suspects_rules():
    es = [{"slot": 0, "thumb": "a", "card": {"card_id": "c1"}}, {"slot": 1, "thumb": "b", "card": {"card_id": "c1"}},
          {"slot": 2, "thumb": "b", "card": {"card_id": "c1"}}, {"slot": 3, "thumb": "c", "card": None},
          {"slot": 4, "thumb": "d", "card": {"card_id": "c4"}, "refreshed": False}]  # fmt: skip
    assert mp.mark_suspects(es) == 3
    # 同内容同缩略图：真的有两份一样的回忆（不是没刷新），不标
    assert [e.get("suspect") for e in es] == [None, "same_as_prev", None, "card_unread", "detail_not_refreshed"]



@pytest.mark.fixtures
def test_dead_swipe_start_is_not_the_end(tmp_path, monkeypatch):
    """账号 2 11-16：第 15 行之后原起点滑不动，扫描判成到底（48 份、complete=True，实际 100 多份）。换起点再滑，扫完整个列表。"""
    lst = ScrollList(copies=3, dead_after_row=5)
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst) and inv["complete"] and not inv.get("aborted")


@pytest.mark.fixtures
def test_stuck_and_not_on_picker_is_aborted_not_complete(tmp_path, monkeypatch):
    lst = ScrollList(copies=2)
    state = {"open": True}
    monkeypatch.setattr(mp, "picker_open", lambda boxes: state["open"])
    picker = ListPicker(lst)
    real = lst.swipe

    def swipe(*a):
        if lst.offset >= 3 * mp.ROW_PITCH:  # 第 3 行之后弹了别的画面：列表滑不动，也不是选择页了
            state["open"] = False
            return
        real(*a)

    picker.swipe = swipe
    inv = mp.scan(picker, tmp_path / "inv.json")
    assert inv.get("aborted") and not inv["complete"]


def test_content_key_ignores_stats():
    """选择页顶部属性是「编成合计 ± 增减」，同一份回忆换个编成就不一样：判「没刷新」的内容键不含属性。"""
    a = {"card": {"card_id": "c", "upgrade": 1}, "timing": "mid", "abilities": [{"text": "x"}], "stats": {"Vo": 20}}
    b = {**a, "stats": {"Vo": 35}}
    assert mp.content_key(a) == mp.content_key(b)


def test_purple_locked_rows_are_detected():
    """紫色锁格（只有卡种徽记、「SS 開」、锁图标，没有偶像照片，账号 2 第 16 行起）：照样是完整行、每格一个指纹。"""
    import os

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "memory-picker-purple-locked-rows.png"
    if not path.exists():
        pytest.skip("需要实机截图 pages/memory-picker-purple-locked-rows.png")
    img = cv2.resize(cv2.imread(str(path)), (720, 1280))
    assert mp.grid_rows(img) == [(676, 794), (814, 933)]  # 上面被详情栏截一半、下面压到浮动按钮的不算
    cells = mp.grid_cells(img)
    assert len(cells) == 6 and len({c["hash"] for c in cells}) == 6


class ClosingPicker(ListPicker):
    """读到某一格时选择页关了（实机：点成了「決定」，回到编成菜单）。"""

    def __init__(self, lst, close_at):
        super().__init__(lst)
        self.close_at, self.closed, self.clicks = close_at, False, []
        self.click = lambda x, y: self.clicks.append((x, y))

    def select_checked(self, cell):
        if self.closed:
            raise mp.PickerClosed("选择页不见了")
        out = super().select_checked(cell)
        if self.lst.read[-1] == self.close_at:
            self.closed = True
        return out


@pytest.mark.fixtures
def test_picker_closed_mid_scan_stops_and_flags_formation(tmp_path, monkeypatch):
    """11-16 账号 2：扫描中选择页关了、编成第 1 格被换掉，扫描还接着点了下一格、最后又点了キャンセル。
    现在：立即停，记 aborted + formation_may_have_changed，不再点格子、不点キャンセル。"""
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    lst = ScrollList()
    picker = ClosingPicker(lst, close_at=(2, 1))
    cancels = []
    picker.cancel = lambda: cancels.append(1) or True
    inv = mp.scan(picker, tmp_path / "inv.json")
    assert inv.get("formation_may_have_changed") and "人工核对" in inv["aborted"] and not inv["complete"]
    assert lst.read[-1] == (2, 1) and cancels == []  # 关了之后没再点别的格子，也没点キャンセル


def test_select_checked_never_taps_same_cell_twice():
    """详情栏没变也不再点同一格（再点已选中的格子可能被当成「決定」）；点之前核对还在选择页。"""
    still = np.zeros((1280, 720, 3), np.uint8)
    taps = []
    p = mp.Picker(lambda: still, lambda x, y: taps.append((x, y)), lambda *a: None, ocr=lambda im: [("キャンセル", 1, (0, 0, 1, 1)),
                  ("決定", 1, (0, 0, 1, 1)), ("編成詳細", 1, (0, 0, 1, 1))], clock=iter(range(0, 10_000)).__next__, sleep=lambda s: None)  # fmt: skip
    changed, _ = p.select_checked({"center": [360, 700]})
    assert not changed and taps == [(360, 700)]
    p.ocr = lambda im: []  # 选择页不见了
    with pytest.raises(mp.PickerClosed):
        p.select_checked({"center": [140, 700]})
    assert taps == [(360, 700)]  # 不在选择页就不点


def _bracketed(rect):
    """灰底画一个格子，四角外侧画橙色 L 形选中框（同实机：框在格子外沿外侧）。"""
    img = np.full((1280, 720, 3), 90, np.uint8)
    x, y, w, h = rect
    cv2.rectangle(img, (x, y), (x + w, y + h), (200, 200, 200), -1)
    orange = (40, 150, 255)  # BGR
    for cx, cy, sx, sy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        cv2.rectangle(img, (cx - sx * 12, cy - sy * 12), (cx + sx * 40, cy - sy * 3), orange, -1)
        cv2.rectangle(img, (cx - sx * 12, cy - sy * 12), (cx - sx * 3, cy + sy * 40), orange, -1)
    return img


def test_cell_selected_reads_outer_corner_brackets():
    rect = [35, 585, 210, 120]
    assert mp.cell_selected(_bracketed(rect), {"rect": rect})
    assert not mp.cell_selected(_bracketed(rect), {"rect": [475, 585, 210, 120]})
    plain = np.full((1280, 720, 3), 90, np.uint8)
    cv2.rectangle(plain, (35, 585), (245, 705), (40, 150, 255), -1)  # 卡面本身是橙色：框外没有橙色就不算选中
    assert not mp.cell_selected(plain, {"rect": rect})


def test_select_checked_does_not_tap_preselected_cell():
    """11-19 账号 2：从空格子打开选择页，第一格默认选中；扫描先点它，被当成「決定」关了选择页。已选中的格子只读不点。"""
    rect = [35, 585, 210, 120]
    img = _bracketed(rect)
    taps = []
    p = mp.Picker(lambda: img, lambda x, y: taps.append((x, y)), lambda *a: None, ocr=lambda im: [("キャンセル", 1, (0, 0, 1, 1)),
                  ("決定", 1, (0, 0, 1, 1)), ("編成詳細", 1, (0, 0, 1, 1))], clock=iter(range(0, 10_000)).__next__, sleep=lambda s: None)  # fmt: skip
    changed, got = p.select_checked({"rect": rect, "center": [140, 645], "row": 0})
    assert changed and got is img and taps == []
    p.select_checked({"rect": [255, 585, 210, 120], "center": [360, 645], "row": 0})
    assert taps == [(360, 645)]


@pytest.mark.master
def test_fuzzy_card_recovers_dropped_or_wrong_kanji():
    """11-19 账号 2：「アイドル言」（漏「宣」）写编成时认不出；「嬌しい誤算＋」（嬉→嬌）扫描时认不出。分不开的不猜。"""
    assert mp.fuzzy_card("アイドル言")["card_id"].endswith("men-2_012")
    got = mp.fuzzy_card("嬌しい誤算＋")
    assert got["name"] == "嬉しい誤算" and got["upgrade"] == 1
    assert mp.fuzzy_card("精神統")["name"] == "精神統一"
    assert mp.fuzzy_card("ア") is None and mp.fuzzy_card("全然ちがうなまえです") is None


def test_scroll_splits_big_distance_inside_list_area():
    """11-17 开局第 3 步：一次滑约 16 行，终点 y 成负数，kotonebot swipe 报 argument out of range。拆成每次 ≤ 3 行、起止点都在列表区。"""
    swipes = []
    p = mp.Picker(lambda: None, lambda x, y: None, lambda x1, y1, x2, y2: swipes.append((y1, y2)), ocr=lambda im: [], sleep=lambda s: None)
    p.scroll(px=int(16 * mp.ROW_PITCH))
    assert sum(a - b for a, b in swipes) == int(16 * mp.ROW_PITCH) and len(swipes) >= 6
    assert all(abs(a - b) <= mp.SCROLL_STEP_MAX and mp.GRID_Y[0] <= min(a, b) and max(a, b) <= mp.GRID_Y[1] for a, b in swipes)
    swipes.clear()
    p.scroll(px=-int(5 * mp.ROW_PITCH))
    assert sum(b - a for a, b in swipes) == int(5 * mp.ROW_PITCH) and all(mp.GRID_Y[0] <= min(a, b) and max(a, b) <= mp.GRID_Y[1] for a, b in swipes)


def test_cancel_finds_button_by_ocr():
    """开局第 3 步的选择页多了 所持メモリー / レンタル 页签，キャンセル 在 (220,1160) 附近：按 OCR 找，不点死坐标。"""
    taps, state = [], {"open": True}
    boxes = lambda im: ([("キャンセル", 1.0, (180, 1145, 260, 1175)), ("決定", 1.0, (440, 1145, 510, 1175)), ("編成詳細", 1.0, (80, 1040, 200, 1060))]  # noqa: E731
                        if state["open"] else [])  # fmt: skip

    def click(x, y):
        taps.append((x, y))
        state["open"] = False

    p = mp.Picker(lambda: None, click, lambda *a: None, ocr=boxes, sleep=lambda s: None)
    assert p.cancel() is True and taps == [(220, 1160)]


@pytest.mark.fixtures
@pytest.mark.parametrize("kw", [{}, {"slop": 0.97, "copies": 3}])
def test_iter_rows_counts_rows_exactly(kw):
    """按页与页重叠精确数行号（不按滑过的像素估算）：每一行产出的行号就是列表里的真实行号。"""
    lst = ScrollList(**kw)
    picker = ListPicker(lst)
    seen = []
    for g, r in mp.iter_rows(picker):
        seen.append((g, lst.row_at(r[0]["center"][1])))
    assert [g for g, _ in seen] == list(range(len(lst.rows))) and all(g == real for g, real in seen)


def test_one_bad_frame_does_not_count_as_picker_closed():
    """11-19 DMM：截到一帧黑帧 / 动画中间的帧，选择页其实还开着。单帧不在不算关，重截核对通过就继续。"""
    open_boxes = [("キャンセル", 1, (0, 0, 1, 1)), ("決定", 1, (0, 0, 1, 1)), ("編成詳細", 1, (0, 0, 1, 1))]
    reads = iter([[], open_boxes])  # 第一次读到黑帧，重截后正常
    sleeps = []
    p = mp.Picker(lambda: None, lambda x, y: None, lambda *a: None, ocr=lambda im: next(reads), sleep=sleeps.append)
    assert p.ensure_open() == open_boxes
    assert sleeps == [mp.CLOSED_RECHECK_S]


def test_picker_really_closed_after_repeated_checks():
    calls = []
    p = mp.Picker(lambda: None, lambda x, y: None, lambda *a: None, ocr=lambda im: calls.append(1) or [], sleep=lambda s: None)
    with pytest.raises(mp.PickerClosed):
        p.ensure_open()
    assert len(calls) == 1 + mp.CLOSED_CONFIRM



def test_detail_page_when_slash_is_dropped():
    """11-19 Windows MuMu：详情栏页码「2/2」被读成「22」，翻不回第 1 页，整次扫描卡名全空。"""
    box = (326, 517, 394, 549)
    assert mp.detail_page([("22", 1, box)]) == 2
    assert mp.detail_page([("12", 1, box)]) == 1
    assert mp.detail_page([("1/2", 1, box)]) == 1 and mp.detail_page([("212", 1, box)]) == 2
    assert mp.detail_page([("22", 1, (40, 517, 90, 549))]) is None  # 不在居中位置（别处的数字）不算
