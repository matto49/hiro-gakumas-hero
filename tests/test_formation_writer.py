# 来源：digital-hiro tests/test_formation_writer.py（7744207）
"""开局自动编成（produce.formation_writer）：修正计划、支援卡列表定位与改格流程、回忆按库存位置改格（假设备）。"""

from pathlib import Path

import cv2
import pytest

from hiro.produce import formation_writer as fw
from hiro.produce.formation_writer import FormationWriteError, fix_plan

FIX = Path(__file__).with_name("fixtures") / "support"


# ------------------------------------------------------------------ 修正计划


def test_fix_plan_keeps_present_cards_and_prefers_wrong_slots():
    assert fix_plan(["a", "b", "c"], ["a", "b", "c"]) == []
    assert fix_plan(["a", "x", "c"], ["c", "a", "b"]) == [(1, "b")]  # 只换不在方案里的那格，顺序不同不动
    assert fix_plan(["a", None, "x"], ["a", "b", "c"]) == [(2, "b"), (1, "c")]  # 先换认出来的错卡，再用认不出的格子
    with pytest.raises(FormationWriteError):
        fix_plan(["a", "b"], ["c", "d", "e"])


@pytest.mark.lowres_sample("支援卡列表样例 list-01 是 540x960 截图；自动编成默认关，DMM 支援卡列表待编成库存扫描取实机帧补进回归集")
def test_find_support_tile_on_real_list_frame():
    from hiro.vision.support_screens import read_support_list

    r = read_support_list(cv2.imread(str(FIX / "list-01.png")))
    tile = fw.find_support_tile(r["cards"], "s_card-3-0021")  # さみだれ
    assert tile is not None and tile["center"] == [581, 648]
    assert fw.find_support_tile(r["cards"], "s_card-x") is None
    partial = read_support_list(cv2.imread(str(FIX / "list-03.png")))
    assert all(fw.find_support_tile(partial["cards"], c["support_card_id"]) is None for c in partial["cards"] if c.get("partial"))


# ------------------------------------------------------------------ 支援卡：假设备状态机


CONTROLS = {"cancel": [220, 1157], "decide": [497, 1157], "prev": [233, 533], "next": [485, 533]}


class FakeGame:
    """第 2 步编成页 + 支援卡选择列表。列表两页、每页若干屏；点卡 = 选中，決定 = 写进打开列表的那一格。"""

    def __init__(self, slots, pages, *, select_works=True):
        self.slots = list(slots)
        self.pages = pages  # [[屏1卡ids, 屏2卡ids, ...], ...]
        self.view, self.open_slot, self.page, self.screen, self.selected = "step2", None, 0, 0, None
        self.select_works = select_works
        self.clicks = []

    # 设备
    def shoot(self):
        return self.view

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.view == "step2":
            self.open_slot = next(i for i, c in enumerate(SLOT_CENTERS) if tuple(c) == (x, y))
            self.view, self.page, self.screen, self.selected = "list", 0, 0, None
        elif [x, y] == CONTROLS["decide"]:
            if self.selected is not None:
                self.slots[self.open_slot] = self.selected
            self.view = "step2"
        elif [x, y] == CONTROLS["cancel"]:
            self.view = "step2"
        elif [x, y] == CONTROLS["next"]:
            self.page, self.screen = min(self.page + 1, len(self.pages) - 1), 0
        elif [x, y] == CONTROLS["prev"]:
            self.page, self.screen = max(self.page - 1, 0), 0
        else:
            hit = next((cid for cid, c in self._tiles() if c == [x, y]), None)
            if hit and self.select_works:
                self.selected = hit

    def swipe(self, x1, y1, x2, y2, dur=None):
        if y1 > y2:
            self.screen = min(self.screen + 1, len(self.pages[self.page]) - 1)
        else:
            self.screen = 0

    def _tiles(self):
        ids = self.pages[self.page][self.screen]
        return [(cid, [138 + 222 * (i % 3), 650 + 137 * (i // 3)]) for i, cid in enumerate(ids)]

    # 读屏
    def read_formation(self, img):
        if img != "step2":
            return None
        return {"view": "step2", "slots": [{"slot": i, "support_card_id": c, "center": SLOT_CENTERS[i], "name": c} for i, c in enumerate(self.slots)]}

    def read_list(self, img):
        if img != "list":
            return None
        cards = [{"support_card_id": cid, "center": c, "partial": False, "selected": cid == self.selected} for cid, c in self._tiles()]
        return {"page": self.page + 1, "pages": len(self.pages), "controls": CONTROLS, "cards": cards}

    def device(self):
        return fw.Device(shoot=self.shoot, click=self.click, swipe=self.swipe, sleep=lambda s: None)


SLOT_CENTERS = [[134, 774], [359, 772], [586, 774], [134, 906], [361, 906]]


def _plan(ids):
    return {"support": {"own": [{"support_card_id": i} for i in ids]}}


def test_fix_support_changes_only_wrong_slot_and_verifies():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q", "r"], ["c", "s", "t"]], [["u"]]])
    done = fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert done == [{"slot": 2, "from": "x", "to": "c"}] and g.slots == ["a", "b", "c", "d", "e"]


def test_fix_support_finds_card_on_second_page():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q"], ["r", "s"]], [["c", "t"]]])
    fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert g.slots[2] == "c"


def test_fix_support_missing_card_cancels_and_raises():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q"], ["r", "s"]]])
    with pytest.raises(FormationWriteError, match="没找到"):
        fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert g.view == "step2" and g.slots[2] == "x"  # 已经キャンセル回到第 2 步，编成没被改


def test_fix_support_does_not_decide_when_tap_did_not_select():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["c"]]], select_works=False)
    with pytest.raises(FormationWriteError, match="选中"):
        fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert tuple(CONTROLS["decide"]) not in g.clicks and g.slots[2] == "x"


def test_fix_support_noop_when_already_matching():
    g = FakeGame(["a", "b", "c", "d", "e"], [[["c"]]])
    assert fw.fix_support(g.device(), _plan(["e", "d", "c", "b", "a"]), read_formation=g.read_formation, read_list=g.read_list) == []
    assert g.clicks == []


# ------------------------------------------------------------------ 回忆


class FakeMemory:
    """第 3 步四格 + 回忆选择页（假 picker）：列表按行排，每行 3 份；cell=[行, 列] 的回忆内容键 = key{行}{列}。"""

    def __init__(self, slots, rows=20):
        self.slots = list(slots)  # 各格卡键
        self.rows = rows
        self.view, self.open_slot, self.top_row, self.selected = "step3", None, 0, None
        self.decided = []

    def shoot(self):
        return self.view

    def click(self, x, y):
        if self.view == "step3":
            self.open_slot = fw.STEP3_SLOTS.index((x, y))
            self.view, self.top_row = "picker", 0

    def read_icons(self, img):
        return [{"card": c, "known": True, "slot": i} for i, c in enumerate(self.slots)]

    def picker(self, card_of):
        mem = self

        class P:
            def shoot(self):
                return "picker"

            def ocr(self, img):
                return [("キャンセル", 0, (0, 0, 1, 1)), ("決定", 0, (0, 0, 1, 1)), ("編成詳細", 0, (0, 0, 1, 1))]

            def scroll(self, rows=0, px=None):
                mem.top_row += round((px or 0) / 137.5)
                return "picker"

            def select(self, cell):
                mem.selected = (cell["row"], cell["col"])

            def read_selected(self):
                r, c = mem.selected
                return {"key_override": f"key{r}{c}", "card": card_of(r, c)}

            def click(self, x, y):
                mem.decided.append(mem.selected)
                mem.slots[mem.open_slot] = card_of(*mem.selected)
                mem.view = "step3"

            def sleep(self, s):
                pass

            def cancel(self):
                mem.view = "step3"
                return True

        return P()


def test_fix_memory_picks_by_inventory_position(monkeypatch):
    from hiro.produce import memory_picker

    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"])
    card_of = lambda r, c: "act-3_039" if (r, c) == (7, 1) else f"other{r}{c}"  # noqa: E731
    monkeypatch.setattr(memory_picker, "to_top", lambda p: (setattr(mem, "top_row", 0), "picker")[1])
    monkeypatch.setattr(memory_picker, "page_rows", lambda img: [[{"row": mem.top_row + i, "col": c, "center": (0, 0)} for c in range(3)] for i in range(5)])
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)
    monkeypatch.setattr(fw, "memory_key", lambda e: e["key_override"])
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039", "cell": [7, 1], "key": "key71"}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(card_of))
    assert done == [{"slot": 3, "from": "men-1_035", "to": "act-3_039"}] and mem.decided == [(7, 1)]


def test_fix_memory_without_inventory_position_refuses():
    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"])
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039"}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    with pytest.raises(FormationWriteError, match="重扫"):
        fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: None)
    assert mem.view == "step3"


def test_inventory_position_only_for_memory_scan_entries():
    from hiro.brain.formation import _inventory_position

    scan_entry = {"cell": [12, 2], "slot": 38, "stats": {"Vo": 10}, "card": {"card_id": "p_card-02-act-3_039"}, "timing": "start", "abilities": []}
    pos = _inventory_position(scan_entry)
    assert pos["cell"] == [12, 2] and isinstance(pos["key"], str)
    assert _inventory_position({"cell": [1, 2], "stats": [{"attr": "Vo"}]}) == {}  # tools.memory_inventory：屏内行号，不用


def test_autofix_switch_default_off(monkeypatch):
    from hiro import kaa_env
    from hiro.loop import kaa_patches

    monkeypatch.setattr(kaa_env, "ACTIVE_PLAN", {"support": {"own": [{"support_card_id": "a"}]}})
    monkeypatch.delenv("HIRO_FORMATION_AUTOFIX", raising=False)
    assert kaa_patches._autofix_on() is False
    monkeypatch.setenv("HIRO_FORMATION_AUTOFIX", "1")
    assert kaa_patches._autofix_on() is True
    monkeypatch.setattr(kaa_env, "ACTIVE_PLAN", None)
    assert kaa_patches._autofix_on() is False
