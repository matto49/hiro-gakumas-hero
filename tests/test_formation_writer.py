"""开局自动编成（produce.formation_writer）：修正计划、支援卡列表定位与改格流程、回忆按库存位置改格（假设备）。"""

from pathlib import Path

from hiro.produce import formation_writer as fw
from hiro.produce.formation_writer import FormationWriteError, fix_plan

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "support"


# ------------------------------------------------------------------ 修正计划


def test_fix_plan_keeps_present_cards_and_prefers_wrong_slots():
    assert fix_plan(["a", "b", "c"], ["a", "b", "c"]) == []
    assert fix_plan(["a", "x", "c"], ["c", "a", "b"]) == [(1, "b")]  # 只换不在方案里的那格，顺序不同不动
    assert fix_plan(["a", None, "x"], ["a", "b", "c"]) == [(2, "b"), (1, "c")]  # 先换认出来的错卡，再用认不出的格子
    with pytest.raises(FormationWriteError):
        fix_plan(["a", "b"], ["c", "d", "e"])


@pytest.mark.lowres_sample(
    "支援卡列表样例 list-01 是 540x960 截图；自动编成默认关，DMM 支援卡列表待编成库存扫描取实机帧补进回归集"
)
def test_find_support_tile_on_real_list_frame():
    from hiro.vision.support_screens import read_support_list

    r = read_support_list(cv2.imread(str(FIX / "list-01.png")))
    tile = fw.find_support_tile(r["cards"], "s_card-3-0021")  # さみだれ
    assert tile is not None and tile["center"] == [581, 648]
    assert fw.find_support_tile(r["cards"], "s_card-x") is None
    partial = read_support_list(cv2.imread(str(FIX / "list-03.png")))
    assert all(
        fw.find_support_tile(partial["cards"], c["support_card_id"]) is None
        for c in partial["cards"]
        if c.get("partial")
    )


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
        return {
            "view": "step2",
            "slots": [
                {"slot": i, "support_card_id": c, "center": SLOT_CENTERS[i], "name": c}
                for i, c in enumerate(self.slots)
            ],
        }

    def read_list(self, img):
        if img != "list":
            return None
        cards = [
            {"support_card_id": cid, "center": c, "partial": False, "selected": cid == self.selected}
            for cid, c in self._tiles()
        ]
        return {"page": self.page + 1, "pages": len(self.pages), "controls": CONTROLS, "cards": cards}

    def device(self):
        return fw.Device(shoot=self.shoot, click=self.click, swipe=self.swipe, sleep=lambda s: None)


SLOT_CENTERS = [[134, 774], [359, 772], [586, 774], [134, 906], [361, 906]]


def _plan(ids):
    return {"support": {"own": [{"support_card_id": i} for i in ids]}}


def test_fix_support_changes_only_wrong_slot_and_verifies():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q", "r"], ["c", "s", "t"]], [["u"]]])
    done = fw.fix_support(
        g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list
    )
    assert done == [{"slot": 2, "from": "x", "to": "c"}] and g.slots == ["a", "b", "c", "d", "e"]


def test_fix_support_retaps_dropped_slot_click():
    """11-21 DMM：点支援卡格的那一下丢了（画面还是编成页）：再点一次，不报「没打开支援卡选择列表」。"""
    g = FakeGame(["a", "b", "x", "d", "e"], [[["c"]]])
    real, dropped = g.click, []

    def flaky(x, y):
        if g.view == "step2" and not dropped:
            dropped.append((x, y))
            return
        real(x, y)

    g.click = flaky
    fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert g.slots[2] == "c" and dropped


def test_fix_support_finds_card_on_second_page():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q"], ["r", "s"]], [["c", "t"]]])
    fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert g.slots[2] == "c"


def test_fix_support_missing_card_cancels_and_raises():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["p", "q"], ["r", "s"]]])
    with pytest.raises(FormationWriteError, match="没找到"):
        fw.fix_support(
            g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list
        )
    assert g.view == "step2" and g.slots[2] == "x"  # 已经キャンセル回到第 2 步，编成没被改


def test_fix_support_does_not_decide_when_tap_did_not_select():
    g = FakeGame(["a", "b", "x", "d", "e"], [[["c"]]], select_works=False)
    with pytest.raises(FormationWriteError, match="选中"):
        fw.fix_support(
            g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list
        )
    assert tuple(CONTROLS["decide"]) not in g.clicks and g.slots[2] == "x"


def test_fix_support_noop_when_already_matching():
    g = FakeGame(["a", "b", "c", "d", "e"], [[["c"]]])
    assert (
        fw.fix_support(
            g.device(), _plan(["e", "d", "c", "b", "a"]), read_formation=g.read_formation, read_list=g.read_list
        )
        == []
    )
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
    monkeypatch.setattr(
        memory_picker,
        "iter_rows",
        lambda p: ((g, [{"row": g, "col": c, "center": (0, 0)} for c in range(3)]) for g in range(mem.rows)),
    )  # 精确行号
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

    scan_entry = {
        "cell": [12, 2],
        "slot": 38,
        "stats": {"Vo": 10},
        "card": {"card_id": "p_card-02-act-3_039"},
        "timing": "start",
        "abilities": [],
    }
    pos = _inventory_position(scan_entry)
    assert pos["cell"] == [12, 2] and isinstance(pos["key"], str)
    assert (
        _inventory_position({"cell": [1, 2], "stats": [{"attr": "Vo"}]}) == {}
    )  # tools.memory_inventory：屏内行号，不用


def test_fix_support_waits_for_settled_slots():
    """刚翻到这个编成号时第 1 格还在动画里、读成认不出：等读数稳定后发现 5 格都对，不改任何格子（11-16 Mac 实机）。"""
    g = FakeGame(["a", "b", "c", "d", "e"], [[["p"]]])
    reads = iter([[None, "b", "c", "d", "e"]])
    real = g.read_formation

    def read_formation(img):
        page = real(img)
        nxt = next(reads, None)
        if page is not None and nxt is not None:
            page["slots"] = [{**s, "support_card_id": c} for s, c in zip(page["slots"], nxt)]
        return page

    done = fw.fix_support(
        g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=read_formation, read_list=g.read_list
    )
    assert done == [] and g.slots == ["a", "b", "c", "d", "e"]


def test_fix_memory_matches_core_when_ability_text_differs(monkeypatch):
    """同一份回忆两次读，能力文字 OCR 差一个字（11-16 Mac 实机：第 16 行附近试了 2 格都对不上 → 不开局）：卡 / 强化 / 时机 / 属性一致就算。"""
    import json

    from hiro.produce import memory_picker

    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"])
    card_of = lambda r, c: "act-3_039" if (r, c) == (7, 1) else f"other{r}{c}"  # noqa: E731
    monkeypatch.setattr(
        memory_picker,
        "iter_rows",
        lambda p: ((g, [{"row": g, "col": c, "center": (0, 0)} for c in range(3)]) for g in range(mem.rows)),
    )  # 精确行号
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)

    def key(e):
        r, c = mem.selected
        return json.dumps(
            [e["card"], 1, "start", 10, 20, 0, 0, [{"text": "初期ダンス上昇+2O" if (r, c) == (7, 1) else f"x{r}{c}"}]],
            ensure_ascii=False,
        )

    monkeypatch.setattr(fw, "memory_key", key)
    want = json.dumps(["act-3_039", 1, "start", 10, 20, 0, 0, [{"text": "初期ダンス上昇+20"}]], ensure_ascii=False)
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039", "cell": [7, 1], "key": want}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(card_of))
    assert mem.decided == [(7, 1)] and done and done[0]["to"] == "act-3_039"


def _core_rig(monkeypatch, target_cell, ability_of):
    import json

    from hiro.produce import memory_picker

    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"], rows=40)
    card_of = lambda r, c: "act-3_039" if (r, c) == target_cell else f"other{r}{c}"  # noqa: E731
    monkeypatch.setattr(
        memory_picker,
        "iter_rows",
        lambda p: ((g, [{"row": g, "col": c, "center": (0, 0)} for c in range(3)]) for g in range(mem.rows)),
    )  # 精确行号
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)

    def key(e):
        r, c = mem.selected
        # 属性是「当前编成 + 这份回忆」的总加成：开局时读到的和扫描时不一样
        return json.dumps([e["card"], 1, "start", 145, 160, 15, 0, [{"text": ability_of(r, c)}]], ensure_ascii=False)

    monkeypatch.setattr(fw, "memory_key", key)
    want = json.dumps(["act-3_039", 1, "start", 10, 20, 0, 0, [{"text": "初期ダンス上昇+20"}]], ensure_ascii=False)
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039", "cell": [7, 1], "key": want}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    return mem, card_of, plan, dev


def test_fix_memory_ignores_stats_and_searches_rows_around(monkeypatch):
    """11-16 账号 2：属性随当前编成变、库存行号偏了几行。卡 / 强化 / 时机一致 + 能力文字相似就选，在目标行上下 SEARCH_ROWS 行里找。"""
    mem, card_of, plan, dev = _core_rig(
        monkeypatch, (13, 1), lambda r, c: "初期ダンス上昇+2O" if (r, c) == (13, 1) else f"x{r}{c}"
    )
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(card_of))
    assert mem.decided == [(13, 1)] and done[0]["to"] == "act-3_039"  # 库存写第 7 行，实际在第 13 行


def test_fix_memory_core_only_match_on_second_pass(monkeypatch):
    """窗口里没有能力文字也像的：第二遍选第一份卡 / 强化 / 时机一致的（写日志）。"""
    mem, card_of, plan, dev = _core_rig(monkeypatch, (9, 1), lambda r, c: "全然違う能力")
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(card_of))
    assert mem.decided == [(9, 1)] and done


def test_fix_memory_reads_inventory_cell_first(monkeypatch):
    """11-20 MuMu：每读一格详情约 20 秒，从窗口顶读起一个槽位要 4 分钟。库存记的那格就是目标时，只读这一格。"""
    mem, card_of, plan, dev = _core_rig(
        monkeypatch, (7, 1), lambda r, c: "初期ダンス上昇+20" if (r, c) == (7, 1) else f"x{r}{c}"
    )
    reads = []
    picker = mem.picker(card_of)
    orig = type(picker).read_selected
    monkeypatch.setattr(type(picker), "read_selected", lambda self: reads.append(mem.selected) or orig(self))
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: picker)
    assert mem.decided == [(7, 1)] and done and reads == [(7, 1)]


def test_core_match_unread_timing_is_unknown():
    """11-20 MuMu：重读时入手时机横幅没读出来（None），卡 / 强化一致、能力文字一样的那格要认。卡没读出来、卡或强化不同不认。"""
    want = ["p_card-00-men-3_005", 1, "mid"]
    assert fw._core_match(["p_card-00-men-3_005", 1, None], want)
    assert fw._core_match(["p_card-00-men-3_005", 1, "mid"], want)
    assert not fw._core_match(["p_card-00-men-3_005", 1, "start"], want)
    assert not fw._core_match(["p_card-00-men-3_005", 0, None], want)
    assert not fw._core_match([None, 1, "mid"], want) and not fw._core_match(None, want)


def test_pick_support_list_closed_after_tap_is_not_an_error(monkeypatch):
    """11-23 Mac 初 手毬：点了列表里的卡，游戏直接放进编成格、关了列表（不用按決定）。原来再读列表读不到选中就报错，
    还在编成页上点了一次 cancel 的坐标。列表已经关了：不点 cancel、不按決定，交给写完后的回读核对。"""
    clicks = []
    shots = {"n": 0}
    cards = [{"support_card_id": "s_card-3-0027", "center": (575, 730), "partial": False, "selected": False}]
    controls = {"cancel": (130, 1180), "decide": (590, 1180), "next": (680, 700)}

    def read_list(img):
        return {"cards": cards, "controls": controls, "pages": 1} if img == "list" else None  # 点完以后列表没了

    def shoot():
        shots["n"] += 1
        return "list" if shots["n"] == 1 else "formation"

    monkeypatch.setattr(fw, "_list_to_top", lambda dev, rl: {"controls": controls, "pages": 1})
    dev = fw.Device(shoot=shoot, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None, sleep=lambda s: None)
    fw.pick_support(dev, "s_card-3-0027", read_list=read_list)
    assert clicks == [(575, 730)]  # 只点了卡；没点 cancel / 決定


def test_fix_support_waits_while_list_is_still_opening():
    """Mac 11-24 16:15（初星助手写编成）：第 1 格改完，点第 2 格后画面在过场（既不是编成页也不是列表），
    原来直接往下走、读一次列表读不到就报「没打开支援卡选择列表」。过场时接着等，列表出来再选。"""
    g = FakeGame(["a", "b", "x", "y", "e"], [[["c", "d"]]])
    real_click, real_shoot = g.click, g.shoot
    left = {"n": 0}

    def click(x, y):
        was = g.view
        real_click(x, y)
        if was == "step2" and g.view == "list":
            left["n"] = 9  # 列表要过 9 张截图（约 4 秒）才出来，比原来等的 6 张长

    def shoot():
        if left["n"] > 0:
            left["n"] -= 1
            return "loading"
        return real_shoot()

    g.click, g.shoot = click, shoot
    fw.fix_support(g.device(), _plan(["a", "b", "c", "d", "e"]), read_formation=g.read_formation, read_list=g.read_list)
    assert g.slots == ["a", "b", "c", "d", "e"]


def test_fix_memory_applies_slice_filter_then_restores(monkeypatch):
    """D15：分片库存里的回忆，行号是片内的（这一片的筛选 + 入手排序下）。写编成前先套同样的筛选，全部写完再开一次选择页恢复排序、
    重置筛选、退出。"""
    from hiro.produce import memory_filter, memory_picker

    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"])
    card_of = lambda r, c: "act-3_039" if (r, c) == (2, 0) else f"other{r}{c}"  # noqa: E731
    log = []
    monkeypatch.setattr(memory_filter, "read_state", lambda p: (log.append("read"), {"sort": "アイドル"})[1])
    monkeypatch.setattr(memory_filter, "apply_slice", lambda p, k: log.append(f"apply {k}"))
    monkeypatch.setattr(memory_filter, "restore", lambda p, st, **kw: (log.append(f"restore {st['sort']}"), True)[1])
    monkeypatch.setattr(
        memory_picker,
        "iter_rows",
        lambda p: (
            (log.append("walk") or True)
            and ((g, [{"row": g, "col": c, "center": (0, 0)} for c in range(3)]) for g in range(mem.rows))
        ),
    )
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)
    monkeypatch.setattr(fw, "memory_key", lambda e: e["key_override"])
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039", "cell": [2, 0], "key": "key20", "slice": "sense:hif"}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    done = fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(card_of))
    assert done == [{"slot": 3, "from": "men-1_035", "to": "act-3_039"}]
    assert log[:3] == ["read", "apply sense:hif", "walk"] and log[-1] == "restore アイドル"
    assert mem.view == "step3"  # 恢复后退出了选择页


def test_plan_records_slice_and_signature():
    from hiro.brain import formation
    from hiro.produce import memory_sig

    raw = {"cell": [3, 1], "slot": 10, "stats": {"Vo": 0, "Da": 52, "Vi": 64, "hp": 0}, "slice": "common:nia",
           "card": {"card_id": "p_card-x", "upgrade": 0}, "abilities": [{"text": "初期ビジュアル上昇+15"}]}  # fmt: skip
    pos = formation._inventory_position(raw)
    assert pos["cell"] == [3, 1] and pos["slice"] == "common:nia" and pos["signature"] == memory_sig.signature(raw)
    del raw["slice"]
    assert "slice" not in formation._inventory_position(raw)


def test_fix_memory_picker_closed_skips_filter_restore(monkeypatch):
    """#116 审查严重 2：找回忆途中选择页关了（PickerClosed，编成可能被改）：停下交给人，finally 不再开选择页恢复筛选。"""
    from hiro.produce import memory_filter, memory_picker

    mem = FakeMemory(["act-2_062", "men-3_012", "act-3_050", "men-1_035"])
    log = []
    monkeypatch.setattr(memory_filter, "read_state", lambda p: {"sort": "アイドル", "dir": "desc"})
    monkeypatch.setattr(memory_filter, "apply_slice", lambda p, k: log.append("apply"))
    monkeypatch.setattr(memory_filter, "restore", lambda p, st, **kw: (log.append("restore"), True)[1])
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)

    def closed(picker, target, **kw):
        raise memory_picker.PickerClosed("选择页不见了")

    monkeypatch.setattr(fw, "pick_memory", closed)
    plan = {"memory": {"chosen": [{"card_id": "p_card-02-act-2_062"}, {"card_id": "p_card-00-men-3_012"}, {"card_id": "p_card-02-act-3_050"},
                                  {"card_id": "p_card-02-act-3_039", "cell": [2, 0], "key": "key20", "slice": "sense:hif"}]}}  # fmt: skip
    dev = fw.Device(shoot=mem.shoot, click=mem.click, swipe=lambda *a: None, sleep=lambda s: None)
    with pytest.raises(fw.FormationWriteError, match="选择页关了"):
        fw.fix_memory(dev, plan, read_icons=mem.read_icons, make_picker=lambda: mem.picker(lambda r, c: "x"))
    assert "restore" not in log


def test_restore_filter_uses_open_picker_instead_of_tapping_tile(monkeypatch):
    """严重 2 另一半：选择页还开着时（apply_slice 后判断方向出错），恢复不能再点回忆格的位置（落在格子区、可能被当成「決定」）。"""
    from hiro.produce import memory_filter, memory_picker

    clicks = []
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: True)
    monkeypatch.setattr(memory_filter, "restore", lambda p, st, **kw: True)

    class Pk:
        def shoot(self):
            return None

        def ocr(self, img):
            return []

        def cancel(self):
            clicks.append("cancel")
            return True

    dev = fw.Device(
        shoot=lambda: None, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None, sleep=lambda s: None
    )
    fw._restore_filter(dev, Pk, fw.STEP3_SLOTS[0], {"sort": "アイドル"})
    assert clicks == ["cancel"]


def test_restore_filter_does_not_tap_when_page_unknown(monkeypatch):
    """#116 第二轮审查（第一轮 2 的另一半）：选择页没开着时，要确认在编成页（认得出回忆格的图标）才点格子去开选择页；
    认不出（弹窗挡着、别的画面）就不点，提示去游戏里看。弹窗还开着先关弹窗。"""
    from hiro.produce import memory_filter, memory_picker

    log = []
    monkeypatch.setattr(memory_picker, "picker_open", lambda boxes: False)
    monkeypatch.setattr(memory_filter, "close_popup", lambda p: (log.append("close_popup"), True)[1])
    monkeypatch.setattr(memory_filter, "restore", lambda p, st, **kw: (log.append("restore"), True)[1])

    class Pk:
        def shoot(self):
            return None

        def ocr(self, img):
            return []

        def cancel(self):
            log.append("cancel")
            return True

    dev = fw.Device(
        shoot=lambda: None, click=lambda x, y: log.append(("click", x, y)), swipe=lambda *a: None, sleep=lambda s: None
    )
    fw._restore_filter(dev, Pk, fw.STEP3_SLOTS[0], {"sort": "アイドル"}, read_icons=lambda img: [{"known": False}] * 4)
    assert log == ["close_popup"]  # 先关弹窗；认不出编成页：不点格子、不恢复
    log.clear()
    fw._restore_filter(dev, Pk, fw.STEP3_SLOTS[0], {"sort": "アイドル"}, read_icons=lambda img: [{"known": True}] * 4)
    assert ("click", *fw.STEP3_SLOTS[0]) in log
