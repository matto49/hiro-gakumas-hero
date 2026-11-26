"""回忆选择页（hiro.produce.memory_picker）离线测试：总控上次扫描留下的半分辨率实拍帧（放大到 720x1280）。"""

import json
from pathlib import Path

import hiro  # noqa: F401
from hiro.produce import memory_picker as mp
from hiro.produce import ops

import cv2
import pytest

FIX = Path(__file__).parent / "fixtures" / "memory"


def frame(name):
    return cv2.resize(cv2.imread(str(FIX / name)), (720, 1280))


@pytest.mark.lowres_sample(
    "回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集"
)
def test_page_readers_on_real_frames():
    p1 = frame("picker-p1-daydreaming.jpg")
    b = ops.ocr_boxes(p1)
    assert mp.picker_open(b) and mp.detail_page(b) == 1
    assert mp.read_stats(b) == {"Vo": 139, "Da": 130, "Vi": 15, "hp": 0}
    card = mp.read_card_page(b)
    assert (
        card["timing"] == "start" and card["card"]["card_id"] == "p_card-02-act-1_037" and card["card"]["upgrade"] == 1
    )
    k = mp.read_card_page(ops.ocr_boxes(frame("picker-p1-kamifubuki.jpg")))
    assert k["timing"] == "mid" and k["card"]["card_id"] == "p_card-02-act-2_062"


@pytest.mark.lowres_sample(
    "回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集"
)
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
    assert len(rows) in (2, 3) and all(
        108 <= y2 - y1 <= 128 and y2 <= mp.ROW_BOTTOM_MAX for y1, y2 in rows
    )  # 压到浮动按钮的行不算
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
        # 翻页 / 读详情只 OCR 上半部分（im 是帧的切片，base 指向原帧）
        key = next(k for k, v in self.frames.items() if v is im or getattr(im, "base", None) is v)
        key = (key, im.shape[0])
        if key not in self._ocr:
            self._ocr[key] = ops.ocr_boxes(im)
        return self._ocr[key]

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.mark.lowres_sample(
    "回忆选择页样例 picker-*.jpg 是 360x640 截图放大，RapidOCR 读不出「開始時 / 後に獲得」；DMM 回忆页待编成库存扫描取实机帧补进回归集"
)
@pytest.mark.fixtures
def test_scan_reads_each_cell_and_cancels(tmp_path):
    dev = FakeDevice()
    picker = mp.Picker(dev.shoot, dev.click, dev.swipe, ocr=dev.ocr, clock=dev.clock, sleep=dev.sleep)
    out = tmp_path / "inv.json"
    inv = mp.scan(picker, out)
    assert len(inv["entries"]) == 3 * len(
        mp.grid_rows(dev.frames["p1"])
    )  # 每个格子一条（同内容的回忆分开记，不再按内容去重）
    e = inv["entries"][0]
    assert (
        e["card"]["name"] == "デイドリーミング"
        and e["timing"] == "start"
        and any("初期Pポイント" in a["text"] for a in e["abilities"])
    )
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

import numpy as np  # noqa: E402

PAGES = [
    cv2.imread(str(FIX / f"scan-{n}.jpg")) for n in ("0001-page000", "0002-page001", "0003-page002", "0004-page003")
]


def list_rows():
    """4 张实拍页的完整行（实机核对过页与页之间没有重叠，行各不相同；压到浮动按钮的行不算）。"""
    out = []
    for img in PAGES:
        for y1, _y2 in mp.grid_rows(img):
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
                    self.rows[i][30:90, x0 : x0 + 120] = (
                        (i * 37 + c * 80) % 255,
                        (i * 91) % 255,
                        (i * 53 + c * 40) % 255,
                    )
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
            y = int(round(self.TOP + i * mp.ROW_PITCH - self.offset))  # noqa: RUF046
            y1, y2 = max(y, mp.GRID_Y[0] - 5), min(y + 118, mp.GRID_Y[1] + 60)
            if y2 > y1:
                img[y1:y2] = row[y1 - y : y2 - y]
        if self.selected is not None:
            r, c = self.selected
            y = int(round(self.TOP + r * mp.ROW_PITCH - self.offset))  # noqa: RUF046
            x = mp.COL_X[c] - mp.CELL_W // 2
            if mp.GRID_Y[0] <= y and y + 118 <= mp.GRID_Y[1] + 60:
                cv2.rectangle(
                    img, (x + 6, y + 6), (x + mp.CELL_W - 7, y + 111), (0, 140, 255), 12
                )  # 选中框画在格子里（伸进缩略图区）
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
        super().__init__(
            lst.shoot, lambda x, y: None, lst.swipe, ocr=lambda im: [], clock=lambda: 0.0, sleep=lambda s: None
        )
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
    assert (
        inv["complete"]
        and inv["gaps"] == []
        and [e["slot"] for e in inv["entries"]] == list(range(len(all_cells(lst))))
    )


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
    assert mp.mark_suspects(es) == 2
    # 同内容同缩略图：真的有两份一样的回忆（不是没刷新），不标；
    # 详情栏像素没怎么变、但读到的内容和上一格不同：读对了（11-26 两次扫描都是同样 14 格误报），不标
    assert [e.get("suspect") for e in es] == [None, "same_as_prev", None, "card_unread", None]


def test_detail_not_refreshed_only_when_content_also_repeats():
    """11-26 本机复核：相邻两格详情栏长得像，像素变化没过阈值（refreshed=False），但读到的内容不同 → 不是没刷新。
    内容和上一格一样、缩略图也一样（真有两份一样的回忆、这次点了没变）才标 detail_not_refreshed。"""
    es = [{"slot": 0, "thumb": "a", "card": {"card_id": "c1"}, "abilities": ["x"]},
          {"slot": 1, "thumb": "b", "card": {"card_id": "c2"}, "abilities": ["y"], "refreshed": False},
          {"slot": 2, "thumb": "b", "card": {"card_id": "c2"}, "abilities": ["y"], "refreshed": False}]  # fmt: skip
    assert mp.mark_suspects(es) == 1
    assert [e.get("suspect") for e in es] == [None, None, "detail_not_refreshed"]


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

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "memory-picker-purple-locked-rows.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/memory-picker-purple-locked-rows.png")
    img = cv2.resize(cv2.imread(str(path)), (720, 1280))
    rows = mp.grid_rows(img)  # 上面被详情栏截一半、下面压到浮动按钮的不算
    # 11-25 起按「不是背景」的像素占比分行（浅色 / 锁格行、末行不满 3 格都要认出），行边沿可能差 2–3 像素
    assert len(rows) == 2 and all(abs(a - b) <= 3 for r, e in zip(rows, [(676, 794), (814, 933)]) for a, b in zip(r, e))
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
                  ("決定", 1, (0, 0, 1, 1)), ("編成詳細", 1, (0, 0, 1, 1))], clock=iter(range(10_000)).__next__, sleep=lambda s: None)  # fmt: skip
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
                  ("決定", 1, (0, 0, 1, 1)), ("編成詳細", 1, (0, 0, 1, 1))], clock=iter(range(10_000)).__next__, sleep=lambda s: None)  # fmt: skip
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
    p = mp.Picker(
        lambda: None,
        lambda x, y: None,
        lambda x1, y1, x2, y2: swipes.append((y1, y2)),
        ocr=lambda im: [],
        sleep=lambda s: None,
    )
    p.scroll(px=int(16 * mp.ROW_PITCH))
    assert sum(a - b for a, b in swipes) == int(16 * mp.ROW_PITCH) and len(swipes) >= 6
    assert all(
        abs(a - b) <= mp.SCROLL_STEP_MAX and mp.GRID_Y[0] <= min(a, b) and max(a, b) <= mp.GRID_Y[1] for a, b in swipes
    )
    swipes.clear()
    p.scroll(px=-int(5 * mp.ROW_PITCH))
    assert sum(b - a for a, b in swipes) == int(5 * mp.ROW_PITCH) and all(
        mp.GRID_Y[0] <= min(a, b) and max(a, b) <= mp.GRID_Y[1] for a, b in swipes
    )


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
    p = mp.Picker(
        lambda: None, lambda x, y: None, lambda *a: None, ocr=lambda im: calls.append(1) or [], sleep=lambda s: None
    )
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


@pytest.mark.fixtures
def test_scan_logs_progress_every_few_pages(tmp_path, monkeypatch, caplog):
    """初星助手的驾驶舱靠日志看进度：扫描过程中每 PROGRESS_EVERY 页打一行（11-23 账号 2 扫了半小时只看到「开始」）。"""
    monkeypatch.setattr(mp, "PROGRESS_EVERY", 1)
    caplog.set_level("INFO", logger=mp.logger.name)
    run_scan(tmp_path, ScrollList(), monkeypatch)
    assert any("回忆扫描：第 1 页" in r.getMessage() for r in caplog.records)


@pytest.mark.fixtures
def test_scan_progress_says_elapsed_and_first_scan_note(tmp_path, monkeypatch, caplog):
    """G3（11-26 Windows 小白验收）：回忆多的账号第一次扫描 27 分钟才 86 页，界面只有「第 N 页、已读 M 格」，小白以为卡死。
    回忆列表不显示总数：进度写上已用几分钟，第一次全量扫描再说一句要多久、会自己停。"""
    monkeypatch.setattr(mp, "PROGRESS_EVERY", 1)
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    caplog.set_level("INFO", logger=mp.logger.name)
    p = ListPicker(ScrollList())
    t = iter(range(0, 10**8, 120))  # 每看一次钟过 2 分钟
    p.clock = lambda: next(t)
    mp.scan(p, tmp_path / "inv.json")
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("回忆扫描：第")]
    assert msgs and "已用" in msgs[-1] and "分钟" in msgs[-1], msgs
    assert "回忆多的账号要半小时以上" in msgs[-1], msgs


def _pale_row_grid(dip_rows: tuple[int, ...]) -> "np.ndarray":
    """合成回忆格区：3 行（y 586–704 / 723–842 / 861–979，行距同实机），行内饱和度 55；dip_rows 里的 y 行饱和度压到 30
    （浅色卡面：白衣服 / 浅色照片的一条横线，账号 3 11-25 实机里一行被切成好几段）。"""
    import numpy as np

    hsv = np.zeros((1280, 720, 3), np.uint8)
    hsv[:, :, 2] = 230
    for y1, y2 in ((586, 704), (723, 842), (861, 979)):
        for cx in mp.COL_X:
            hsv[y1:y2, cx - mp.CELL_W // 2 + 4 : cx + mp.CELL_W // 2 - 4, 1] = 55
            hsv[y1 + 20 : y1 + 40, cx - 40 : cx + 40, 0] = (cx // 4) % 180  # 每格不同色块：指纹各不相同
            hsv[y1 + 20 : y1 + 40, cx - 40 : cx + 40, 1] = 200
    for y in dip_rows:
        hsv[y, :, 1] = 30
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_pale_rows_with_thin_low_saturation_lines_are_whole_rows():
    """浅色卡面行里有 1–5 像素的低饱和横线：不能当成行间空隙把一行切碎（真空隙约 19 像素）。11-25 账号 3 回忆扫描只认出 1 行、
    翻 20 页后「对不上」中止。"""
    img = _pale_row_grid((587, 588, 615, 616, 617, 618, 619, 767))
    assert mp.grid_rows(img) == [(586, 704), (723, 842), (861, 979)]
    assert len(mp.grid_cells(img)) == 9


def test_pale_cells_are_not_taken_as_empty():
    """浅色格整体饱和度只有 41–50（实机），空格是 0：空格阈值不能和行间空隙共用 40。"""
    import numpy as np

    img = _pale_row_grid(())
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hsv[861:979, 250:687, 1] = 0  # 最后一行只有第 1 格
    pale = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    pale_hsv = cv2.cvtColor(pale, cv2.COLOR_BGR2HSV)
    pale_hsv[586:704, 35:250, 1] = np.minimum(pale_hsv[586:704, 35:250, 1], 36)  # 第 1 行第 1 格整体很淡
    pale = cv2.cvtColor(pale_hsv, cv2.COLOR_HSV2BGR)
    cells = mp.grid_cells(pale)
    assert [(c["row"], c["col"]) for c in cells] == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0)]


def test_pale_rows_real_frame():
    """11-25 账号 3 实机第一页：B+ / B 两行卡面偏淡，以前只认出最下面的 D 行。"""
    import os

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "memory-picker-pale-rows.jpg"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/memory-picker-pale-rows.jpg")
    img = cv2.imread(str(path))
    rows = mp.grid_rows(img)
    assert len(rows) == 3 and all(108 <= y2 - y1 <= 128 for y1, y2 in rows)
    cells = mp.grid_cells(img)
    assert len(cells) == 9 and len({c["hash"] for c in cells}) == 9


# ---------------------------------------------------------------- 增量补扫 / 预算 / 多片不退出（设计 5.4 / D6）
class CancelCounting(ListPicker):
    def __init__(self, lst, clock=lambda: 0.0):
        super().__init__(lst)
        self.clock, self.cancels = clock, 0

    def cancel(self):
        self.cancels += 1
        return True


@pytest.mark.fixtures
def test_incremental_scan_stops_after_three_known(tmp_path, monkeypatch):
    """前 2 格是新的，之后都是资料库里有的：读到第 3 格已知就停（读 5 格），new=2。"""
    from hiro.produce.memory_sig import KnownSet

    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    lst = ScrollList()
    known = KnownSet([{"card": {"card_id": f"m{r}-{c}"}} for r, c in all_cells(lst)[2:]])
    inv = mp.scan(CancelCounting(lst), tmp_path / "inv.json", known=known)
    assert lst.read == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]
    assert inv["new"] == 2 and inv["stopped"] == "known" and inv["complete"]
    assert [e.get("known", False) for e in inv["entries"]] == [False, False, True, True, True]


@pytest.mark.fixtures
def test_three_known_at_top_stop_immediately(tmp_path, monkeypatch):
    from hiro.produce.memory_sig import KnownSet

    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    lst = ScrollList()
    cells = all_cells(lst)
    known = KnownSet([{"card": {"card_id": f"m{r}-{c}"}} for r, c in cells if (r, c) != (1, 0)])
    inv = mp.scan(CancelCounting(lst), tmp_path / "inv.json", known=known)
    # 列表最上面 3 格都已知（最新的都扫过了）：读 3 格就停
    assert len(lst.read) == 3 and inv["new"] == 0


@pytest.mark.fixtures
def test_budget_stops_scan_incomplete_and_keeps_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    t = iter(range(0, 1000, 10))
    p = CancelCounting(ScrollList(), clock=lambda: next(t))
    inv = mp.scan(p, tmp_path / "inv.json", deadline=25)
    assert inv["stopped"] == "budget" and not inv["complete"] and 1 <= len(inv["entries"]) < len(all_cells(p.lst))


@pytest.mark.fixtures
def test_close_false_leaves_picker_open(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)
    p = CancelCounting(ScrollList())
    mp.scan(p, tmp_path / "inv.json", close=False)
    assert p.cancels == 0
    mp.scan(p, tmp_path / "inv2.json")
    assert p.cancels == 1


@pytest.mark.fixtures
def test_each_page_advances_two_rows(tmp_path, monkeypatch):
    """11-26 实机：每页只前进 1 行（读完把最后一行滑到 y≈605，第 3 行下沿刚过 ROW_BOTTOM_MAX 认不出）。
    把最后一行滑到 SCROLL_TOP_TARGET（≈585），每屏 3 行都完整，每页前进 2 行：36 行的列表滑动次数减半，每格仍只读一次。"""
    lst = ScrollList(copies=3)
    swipes = []
    orig = lst.swipe
    lst.swipe = lambda *a, **k: (swipes.append(a), orig(*a, **k))[1]
    inv = run_scan(tmp_path, lst, monkeypatch)
    assert sorted(set(lst.read)) == all_cells(lst) and len(lst.read) == len(all_cells(lst)) and inv["complete"]
    assert len(swipes) <= len(lst.rows) // 2 + 6, len(swipes)


def test_scroll_brings_last_read_row_to_top_target():
    """读完一页把最后一行滑到 SCROLL_TOP_TARGET（不是第一行原来的位置）：实机帧行上沿 605 / 743 时滑 158，
    之后 3 行上沿 585 / 722 / 860，下沿都在 ROW_BOTTOM_MAX 以内，每页前进 2 行（11-26 实机每页只前进 1 行）。"""
    rows = [[{"rect": (40, 605, 210, 118)}], [{"rect": (40, 743, 210, 118)}]]
    assert mp.page_scroll_px(rows) == 743 - mp.SCROLL_TOP_TARGET
    assert mp.SCROLL_TOP_TARGET + 2 * mp.ROW_PITCH + mp.ROW_H[1] <= mp.ROW_BOTTOM_MAX
    assert mp.page_scroll_px([[{"rect": (40, 605, 210, 118)}]]) == int(mp.ROW_PITCH)  # 只认出 1 行：照旧滑一行


def test_read_selected_starts_on_current_page_and_never_flips_back(monkeypatch):
    """11-26 实机：选另一格后详情栏停在原来那一页。读完不翻回第 1 页，下一格从当前页读起：每格只翻一次页。"""
    monkeypatch.setattr(mp, "read_stats", lambda boxes: {"Vo": 1})
    monkeypatch.setattr(mp, "read_card_page", lambda boxes: {"card": {"card_id": "c"}})
    monkeypatch.setattr(mp, "read_ability_page", lambda boxes: ["a"])
    monkeypatch.setattr(mp, "picker_open", lambda boxes: True)  # 读完核对选择页还开着

    class Pk(mp.Picker):
        def __init__(self):
            super().__init__(lambda: None, lambda x, y: None, lambda *a: None, ocr=lambda im: [], sleep=lambda s: None)
            self.pages = []

        def to_page(self, page):
            self.pages.append(page)
            self._page = page
            return None, []

    p = Pk()
    e1, e2, e3 = p.read_selected(), p.read_selected(), p.read_selected()
    assert p.pages == [1, 2, 2, 1, 1, 2]
    assert e1 == e2 == e3 == {"stats": {"Vo": 1}, "card": {"card_id": "c"}, "abilities": ["a"]}


def test_to_page_ocrs_only_detail_area():
    """翻页 / 读详情只 OCR 画面上半部分（详情栏和页码都在 DETAIL_OCR_BOTTOM 以上）：整屏 OCR 0.38 秒，每格省两次。"""
    import numpy as np

    heights = []

    def ocr(img):
        heights.append(img.shape[0])
        return [("1/2", 1.0, (330, 525, 390, 545))]

    p = mp.Picker(lambda: np.zeros((1280, 720, 3), np.uint8), lambda x, y: None, lambda *a: None, ocr=ocr,
                  sleep=lambda s: None, clock=lambda: 0.0)  # fmt: skip
    p.to_page(1)
    assert heights and all(h <= mp.DETAIL_OCR_BOTTOM for h in heights) and p._page == 1


@pytest.mark.fixtures
@pytest.mark.parametrize(
    ("name", "want"), [("picker-vo-zero-hif-1126.png", (0, 62, 64)), ("picker-vo-zero-hif-1126-b.png", (0, 67, 49))]
)
def test_stats_fall_back_to_full_frame_when_a_digit_is_missed(name, want):
    """11-26 实机：顶栏 Vo 只有一个「0」，只 OCR 上半部分时读不出来（整屏能读出）：有属性没读出时退回整屏再读。"""
    img = frame(name)
    boxes = ops.ocr_boxes(img[: mp.DETAIL_OCR_BOTTOM])
    s = mp.stats_with_fallback(img, boxes, ops.ocr_boxes)
    assert (s["Vo"], s["Da"], s["Vi"]) == want


def test_read_selected_checks_picker_still_open(monkeypatch):
    """#116 第二轮审查重要 1：读完一格要核对选择页还开着（点格子被当成「決定」时选择页会关）。
    提速时只 OCR 上半部分、去掉了这层核对，一页最后一格 / 片尾 / 预算到时关了就发现不了。"""
    monkeypatch.setattr(mp, "read_stats", lambda boxes: {"Vo": 1, "Da": 1, "Vi": 1, "hp": 0})
    monkeypatch.setattr(mp, "read_card_page", lambda boxes: {"card": {"card_id": "c"}})
    monkeypatch.setattr(mp, "read_ability_page", lambda boxes: [])

    class Pk(mp.Picker):
        def __init__(self):
            super().__init__(lambda: None, lambda x, y: None, lambda *a: None, ocr=lambda im: [], sleep=lambda s: None)

        def to_page(self, page):
            self._page = page
            return None, []

    with pytest.raises(mp.PickerClosed):
        Pk().read_selected()


def test_to_page_checks_picker_before_flipping(monkeypatch):
    """#116 第三轮：点翻页箭头前要对同一帧核对选择页还开着；选择页已经关了、编成页上的字被读成页码时，不能在编成页上点。"""
    import numpy as np

    clicks, t = [], iter(range(10**6))
    p = mp.Picker(lambda: np.zeros((1280, 720, 3), np.uint8), lambda x, y: clicks.append((x, y)), lambda *a: None,
                  ocr=lambda im: [("1/2", 1.0, (330, 525, 390, 545))], sleep=lambda s: None, clock=lambda: next(t))  # fmt: skip
    with pytest.raises(mp.PickerClosed):
        p.to_page(2)  # 整屏没有キャンセル / 決定：选择页不在了
    assert clicks == []
