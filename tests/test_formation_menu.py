"""编成菜单写入（hiro.produce.formation_menu）：菜单页读取（实机帧在私有 fixtures）、导航（假设备）、写入后回读核对。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.produce import formation_menu as fm
from hiro.produce.formation_writer import Device, FormationWriteError

import numpy as np
import pytest

FIX = Path(__file__).parent / "fixtures" / "formation_menu"


def _frame(name):
    import cv2

    p = FIX / f"{name}.jpg"
    if not p.exists():
        pytest.skip(f"需要样例 {p}")
    return cv2.imread(str(p))


def _ocr(img):
    from hiro.vision.ocr import screen_texts

    return [(t.text, 1.0, tuple(int(v) for v in t.rect)) for t in screen_texts(img)]


@pytest.mark.parametrize("name, want", [
    ("hajime-support1", {"mode": "hajime", "tab": "support", "number": 1}),
    ("hif-memory1", {"mode": "hif", "tab": "memory", "number": 1}),
    ("hif-support20", {"mode": "hif", "tab": "support", "number": 20}),  # 编号 OCR 是「三 20/20」
    ("hif-memory20", {"mode": "hif", "tab": "memory", "number": 20}),
    ("switch-dialog", None),  # 切替框盖着
    ("hif-memory-picker", None),  # 回忆选择页
    ("home-popup", None),
])  # fmt: skip
def test_read_menu_on_device_frames(name, want):
    img = _frame(name)
    m = fm.read_menu(img, fm._texts(img, _ocr))
    if want is None:
        assert m is None
    else:
        assert {k: m[k] for k in want} == want and m["buttons"]["switch"] is not None


def test_read_memory_icons_big_row():
    """上排大图标：協調 11-17 写好的 メモリー編成 20 = 方案的四张卡；H.I.F 1 号第 4 格分数刚过线、靠领先认出。"""
    assert [r["card"] for r in fm.read_memory_icons(_frame("hif-memory20"))] == [
        "men-2_004",
        "act-1_037",
        "act-3_039",
        "men-3_002",
    ]
    reads = fm.read_memory_icons(_frame("hif-memory1"))
    assert all(r["known"] for r in reads) and reads[3]["card"] == "act-3_049"


def test_read_mode_labels():
    t = lambda s: [(s, (63, 45, 250, 70))]  # noqa: E731
    assert fm.read_mode(t("定期公演『初』")) == "hajime"
    assert fm.read_mode(t("HatsuboshiIDOLFESTIVAL")) == "hif"
    assert fm.read_mode(t("NEXTIDOLAUDITION")) == "nia"
    assert fm.read_mode([("定期公演『初』", (63, 300, 250, 330))]) is None  # 不在左上角


# ---------------------------------------------------------------- 导航（假设备）
class FakeGame:
    """菜单状态机：shoot 给当前状态，click 按坐标改状态；read_menu / _texts 换成读状态。"""

    def __init__(self, mode="hajime", tab="support", number=1, where="menu"):
        self.mode, self.tab, self.number, self.where = mode, tab, number, where
        self.pick = None
        self.clicks = []

    def shoot(self):
        return dict(vars(self))

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.where == "home":
            if (x, y) == fm.IDOL_TAB:
                self.where = "idol_panel"
        elif self.where == "idol_panel":
            if (x, y) == (243, 832):
                self.where = "submenu"  # 「プロデュース編成・スキルカード」→ 弹出子菜单
            else:
                raise AssertionError(f"点了不该点的地方 {(x, y)}（{self.where}）")
        elif self.where == "submenu":
            if (x, y) == (198, 579):
                self.where = "menu"
            else:
                raise AssertionError(f"点了不该点的地方 {(x, y)}（{self.where}）")
        elif self.where == "dialog":
            banner = next((k for k, v in fm.MODE_BANNERS.items() if v == (x, y)), None)
            if banner:
                self.pick = banner
            elif (x, y) == (522, 1161):
                self.mode, self.where = self.pick or self.mode, "menu"
        elif (x, y) == (569, 58):
            self.where = "dialog"
        elif (x, y) == (128, 1092):
            self.tab = "support"
        elif (x, y) == (333, 1093):
            self.tab = "memory"
        elif (x, y) == fm.MEMORY_TILES[0] and self.tab == "memory":
            self.where = "picker"
        elif (x, y) == fm.ARROWS["next"]:
            self.number = min(20, self.number + 1)
        elif (x, y) == fm.ARROWS["prev"]:
            self.number = max(1, self.number - 1)
        else:
            raise AssertionError(f"点了不该点的地方 {(x, y)}（{self.where}）")


def _menu_texts(img):
    if img["where"] == "home":
        return [("ホーム", (330, 1180, 390, 1200)), ("コンテスト", (430, 1180, 520, 1200))]
    if img["where"] == "idol_panel":  # 11-17 Mac 实机：アイドル 面板上是「プロデュース編成・スキルカード」
        return [("プロデュース編成・スキルカード", (110, 818, 376, 846)), ("ホーム", (330, 1180, 390, 1200))]
    if img["where"] == "submenu":  # 弹出的子菜单（实机帧 01-menu-wait-fail：「プロデュース編成」(198,579)）
        return [("編成", (100, 520, 156, 542)), ("プロデュース編成", (140, 567, 256, 591)), ("スキルカード", (130, 630, 220, 654)),
                ("プロデュース編成・スキルカード", (110, 818, 376, 846))]  # fmt: skip
    if img["where"] == "dialog":
        return [("キャンセル", (154, 1144, 311, 1178)), ("決定", (493, 1146, 551, 1176))]
    return [("menu", (0, 0, 1, 1))]


def _fake_read_menu(img, texts):
    if img["where"] != "menu":
        return None
    return {"mode": img["mode"], "tab": img["tab"], "number": img["number"],
            "buttons": {"switch": (569, 58), "support_tab": (128, 1092), "memory_tab": (333, 1093)}}  # fmt: skip


@pytest.fixture
def game(monkeypatch):
    monkeypatch.setattr(fm, "read_menu", _fake_read_menu)
    g = FakeGame()
    dev = Device(shoot=g.shoot, click=g.click, swipe=lambda *a: None, ocr=lambda img: [(t, 1.0, b) for t, b in _menu_texts(img)],
                 sleep=lambda s: None)  # fmt: skip
    return g, dev


def test_menu_from_home_switch_mode_tab_and_set(game):
    g, dev = game
    g.where = "home"
    menu = fm.Menu(dev)
    menu.open()
    assert g.where == "menu"
    assert menu.ensure_mode("hif")["mode"] == "hif"
    assert menu.ensure_tab("memory")["tab"] == "memory"
    assert menu.ensure_set(5)["number"] == 5
    assert menu.ensure_set(3)["number"] == 3
    assert g.clicks.count(fm.ARROWS["next"]) == 4 and g.clicks.count(fm.ARROWS["prev"]) == 2


def test_menu_stops_off_page(game):
    g, dev = game
    g.where = "somewhere"
    with pytest.raises(FormationWriteError, match="不在首页"):
        fm.Menu(dev).open()


def test_ensure_mode_noop_when_already_there(game):
    g, dev = game
    fm.Menu(dev).ensure_mode("hajime")
    assert g.clicks == []  # 已经是这个模式：一下都不点


def test_ensure_set_stuck_arrow_stops(game, monkeypatch):
    """点了箭头编号不变（翻不动 / 点没生效）：等一会儿就停，不乱点。"""
    _g, dev = game
    monkeypatch.setattr(fm, "WAIT_TRIES", 2)
    dev.click = lambda x, y: None
    with pytest.raises(FormationWriteError, match="编成号从 1 变"):
        fm.Menu(dev).ensure_set(2)
    with pytest.raises(ValueError):
        fm.Menu(dev).ensure_set(21)


def test_ensure_set_retaps_dropped_arrow(game, monkeypatch):
    """11-21 DMM：翻号时有一下点击丢了：编号没变就再点（最多 ARROW_RETRIES 次）。"""
    g, dev = game
    monkeypatch.setattr(fm, "WAIT_TRIES", 2)
    real, dropped = g.click, []

    def flaky(x, y):
        if (x, y) == fm.ARROWS["next"] and g.number == 2 and not dropped:
            dropped.append(1)
            return
        real(x, y)

    dev.click = flaky
    assert fm.Menu(dev).ensure_set(4)["number"] == 4 and dropped == [1]


# ---------------------------------------------------------------- 写入 + 回读
PLAN = {"memory": {"set": 20, "chosen": [{"card_id": f"p_card-02-men-2_00{i}", "cell": [i, 0], "key": "k"} for i in range(1, 5)]},
        "support": {"set": 20, "own": [{"support_card_id": f"s_card-{i}"} for i in range(5)]}}  # fmt: skip


def _reads(cards):
    return [{"slot": i, "card": c, "known": c is not None} for i, c in enumerate(cards)]


def test_write_memory_only_changes_wrong_slots_and_reads_back(monkeypatch):
    seq = [_reads(["men-2_001", "act-9_999", "men-2_003", None])] * 2 + [
        _reads(["men-2_001", "men-2_002", "men-2_003", "men-2_004"])
    ] * 2
    it = iter(seq)
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: next(it))
    calls = []
    monkeypatch.setattr(
        fm, "fix_memory", lambda dev, plan, **kw: calls.append(kw["slots"]) or [{"slot": 1}, {"slot": 3}]
    )
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    assert fm.write_memory(dev, PLAN, make_picker=None) == [{"slot": 1}, {"slot": 3}]
    assert calls == [fm.MEMORY_TILES]


def test_write_memory_read_back_mismatch_stops(monkeypatch):
    it = iter(
        [_reads(["men-2_001", None, "men-2_003", "men-2_004"])] * 2
        + [_reads(["men-2_001", "act-1_001", "men-2_003", "men-2_004"])] * 2
    )
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: next(it))
    monkeypatch.setattr(fm, "fix_memory", lambda dev, plan, **kw: [{"slot": 1}])
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    with pytest.raises(FormationWriteError, match="回读还差"):
        fm.write_memory(dev, PLAN, make_picker=None)


def test_write_memory_dry_run_touches_nothing(monkeypatch):
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: _reads(["men-2_001", "x-1_1", "men-2_003", "men-2_004"]))
    monkeypatch.setattr(fm, "fix_memory", lambda *a, **k: pytest.fail("dry-run 不该写"))
    dev = Device(shoot=lambda: None, click=lambda *a: pytest.fail("dry-run 不该点"), swipe=None, sleep=lambda s: None)
    assert fm.write_memory(dev, PLAN, make_picker=None, dry_run=True) == [
        {"slot": 1, "to": "men-2_002", "dry_run": True}
    ]


def test_write_support_uses_formation_view_and_reads_back(monkeypatch):
    pages = iter([{"view": "formation", "slots": [{"support_card_id": f"s_card-{i}"} for i in (0, 1, 9, 3, 4)]}] * 2
                 + [{"view": "formation", "slots": [{"support_card_id": f"s_card-{i}"} for i in range(5)]}] * 2)  # fmt: skip
    seen = []
    monkeypatch.setattr(fm, "fix_support", lambda dev, plan, **kw: seen.append(kw["view"]) or [{"slot": 2}])
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    assert fm.write_support(dev, PLAN, read_formation=lambda img: next(pages), read_list=None) == [{"slot": 2}]
    assert seen == ["formation"]


def test_run_order_and_sets(game, monkeypatch):
    g, dev = game
    done = []
    monkeypatch.setattr(
        fm, "write_support", lambda dev, plan, **kw: done.append(("support", g.mode, g.tab, g.number)) or []
    )
    monkeypatch.setattr(
        fm, "write_memory", lambda dev, plan, mp, **kw: done.append(("memory", g.mode, g.tab, g.number)) or []
    )
    out = fm.run(dev, {"support": {"set": 4}, "memory": {"set": 6}}, "nia")
    assert done == [("support", "nia", "support", 4), ("memory", "nia", "memory", 6)] and out == {
        "support": [],
        "memory": [],
    }
    with pytest.raises(FormationWriteError, match="memory.set"):  # noqa: RUF043
        fm.run(dev, {"support": {"set": 4}, "memory": {}}, "nia", what=("memory",))


def test_menu_requires_ocr():
    with pytest.raises(ValueError):
        fm.Menu(NS(ocr=None))


def test_open_taps_submenu_item_on_device_frame(monkeypatch):
    """11-17 Mac：アイドル 面板弹出的子菜单（实机帧）。要点子菜单里的「プロデュース編成」(198,579)，
    不是面板按钮「プロデュース編成・スキルカード」(243,832)。"""
    img = _frame("idol-submenu")
    clicks = []
    state = {"menu": False}
    monkeypatch.setattr(
        fm,
        "read_menu",
        lambda im, texts: {"mode": "hajime", "tab": "support", "number": 1, "buttons": {}} if state["menu"] else None,
    )

    def click(x, y):
        clicks.append((x, y))
        state["menu"] = True

    dev = Device(shoot=lambda: img, click=click, swipe=lambda *a: None, ocr=_ocr, sleep=lambda s: None)
    fm.Menu(dev).open()
    assert len(clicks) == 1 and abs(clicks[0][0] - 198) < 20 and abs(clicks[0][1] - 579) < 20


def test_set_number_reread_when_digit_dropped(monkeypatch):
    """11-20 Windows MuMu：整帧 OCR 把「9/20」读成「/20」，认不出编成页、翻号卡住。裁下来按 2 倍重读能认出编号
    （不手动放大：review #11，RapidOCR 4 倍以上会多读杂字）。"""
    import hiro.vision.ocr as ocr

    seen = []

    def fake(img, scale=3.0, **kw):
        seen.append((img.shape, scale))
        return [("9/20", 0.9, (0, 0, 1, 1))]

    monkeypatch.setattr(ocr, "ocr_boxes", fake)
    img = np.zeros((1280, 720, 3), np.uint8)
    texts = [("/20", (82, 948, 139, 975)), ("おまかせ", (573, 950, 655, 973))]
    assert fm._set_number(img, texts) == 9
    ((shape, scale),) = seen
    assert (
        shape[1] == 139 + 10 - (82 - fm.SET_NUMBER_LEFT) and scale == 2
    )  # 只裁「/20」左边一小块，原尺寸交给 OCR 按 2 倍读
    assert fm._set_number(img, [("8/20", (60, 948, 139, 975))]) == 8 and len(seen) == 1  # 读全了不重读
    monkeypatch.setattr(ocr, "ocr_boxes", lambda img, scale=3.0, **kw: [("/20", 0.9, (0, 0, 1, 1))])
    assert fm._set_number(img, texts) is None  # 重读也没有编号：不猜


def test_submenu_item_tolerates_ocr_misread():
    """11-21 DMM：子菜单「プロデュース編成」被读成「プロデュース綿成」。"""
    from hiro.produce.formation_menu import _is_panel, _is_submenu_item

    assert _is_submenu_item("プロデュース編成") and _is_submenu_item("プロデュース綿成")
    assert not _is_submenu_item("プロデュース編成・スキルカード") and not _is_submenu_item("プロデュース")
    assert (
        _is_panel("プロデュース編成・スキルカード")
        and _is_panel("プロデュース綿成・スキルカード")
        and not _is_panel("プロデュース綿成")
    )


def test_find_tolerates_hen_misread():
    """11-21 DMM 小窗口：「編成」读成「綿成 / 縞成」（textnorm 容错，不再列易混字）。"""
    from hiro.produce.formation_menu import _find

    texts = [
        ("サポート編成", (70, 1080, 189, 1104)),
        ("メモリー綿成", (276, 1081, 394, 1103)),
        ("縞成詳細", (566, 1078, 665, 1107)),
    ]
    assert _find(texts, "メモリー編成", y_min=1040) == (276, 1081, 394, 1103)
    assert _find(texts, "サポート編成", y_min=1040) == (70, 1080, 189, 1104)  # 不会认成「メモリー綿成」


def test_open_memory_picker_from_empty_set_and_report_original(game, monkeypatch):
    """11-26 实机：回忆选择页顶栏 = 这份回忆 + 编成里其余 3 格。从全空的编成进去，读到的就是回忆本身的属性（设计 5.3）。
    返回进去前的编成号，扫完切回去。"""
    g, dev = game
    g.mode, g.tab, g.number = "hif", "memory", 20
    # 终审 I4：不只信资料库的编成快照，切过去后现场看 4 格是不是都空
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: [{"empty": img["number"] == 19}] * 4)
    got = fm.open_picker(
        dev,
        "memory",
        ready=lambda img: img["where"] == "picker",
        use_set=lambda mode: ("hif", 19) if mode == "hif" else None,
    )
    assert g.where == "picker" and g.number == 19
    assert got == {"mode": "hif", "set": 20, "used": 19, "used_mode": "hif", "empty": True}


def test_open_memory_picker_without_empty_set_stays(game, monkeypatch):
    g, dev = game
    g.mode, g.tab, g.number = "hif", "memory", 20
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: [{"empty": img["number"] == 19}] * 4)
    got = fm.open_picker(dev, "memory", ready=lambda img: img["where"] == "picker", use_set=lambda mode: None)
    assert g.number == 20 and got == {"mode": "hif", "set": 20, "used": None, "used_mode": None, "empty": False}


def test_snapshot_says_empty_but_set_is_not(game, monkeypatch):
    """快照说 19 号空，现场有回忆（快照之后被写过）：empty=False，属性带偏移，入库时不当作本身属性。"""
    g, dev = game
    g.mode, g.tab, g.number = "hif", "memory", 20
    monkeypatch.setattr(
        fm, "read_memory_icons", lambda img: [{"empty": True}, {"empty": False}, {"empty": True}, {"empty": True}]
    )
    got = fm.open_picker(dev, "memory", ready=lambda img: img["where"] == "picker", use_set=lambda mode: ("hif", 19))
    assert got["used"] == 19 and got["empty"] is False


def test_open_picker_switches_mode_for_empty_set_and_back(game, monkeypatch):
    """当前模式（初）没有空编成：切到 H.I.F 的 19 号扫，扫完（back_to）切回初的 10 号。"""
    g, dev = game
    g.mode, g.tab, g.number = "hajime", "memory", 10
    monkeypatch.setattr(
        fm, "read_memory_icons", lambda img: [{"empty": img["mode"] == "hif" and img["number"] == 19}] * 4
    )
    got = fm.open_picker(dev, "memory", ready=lambda img: img["where"] == "picker", use_set=lambda mode: ("hif", 19))
    assert (
        g.mode == "hif"
        and g.number == 19
        and got == {"mode": "hajime", "set": 10, "used": 19, "used_mode": "hif", "empty": True}
    )
    g.where = "menu"  # 扫完点キャンセル回到编成页
    fm.back_to(dev, got)
    assert (g.mode, g.number) == ("hajime", 10)


def test_open_picker_switches_back_when_picker_never_opens(game, monkeypatch):
    """#116 第二轮审查次要：切到空编成后选择页打不开（报错停下），要把模式和编成号切回去。"""
    g, dev = game
    g.mode, g.tab, g.number = "hajime", "memory", 10
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: [{"empty": True}] * 4)
    from hiro.produce.formation_writer import FormationWriteError

    orig = g.click
    g.click = lambda x, y: (
        None if (x, y) == fm.MEMORY_TILES[0] else orig(x, y)
    )  # 点回忆格没反应：选择页打不开、还在编成页
    dev.click = g.click
    with pytest.raises(FormationWriteError):
        fm.open_picker(dev, "memory", ready=lambda img: False, use_set=lambda mode: ("hif", 19))
    assert (g.mode, g.number) == ("hajime", 10)


# ---------------------------------------------------------------- blank_only（#134 review：开局前自动准备不覆盖玩家后来填的编成位）
def test_write_memory_blank_only_stops_when_slot_has_cards(monkeypatch):
    reads = [dict(r, empty=r["card"] is None) for r in _reads(["act-9_999", None, None, None])]
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: reads)
    monkeypatch.setattr(fm, "fix_memory", lambda *a, **k: pytest.fail("有卡时不该写"))
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    with pytest.raises(FormationWriteError, match="已经有卡"):
        fm.write_memory(dev, PLAN, make_picker=None, blank_only=True)


def test_write_memory_blank_only_writes_into_empty_slot(monkeypatch):
    empty = [{"slot": i, "card": None, "known": False, "empty": True} for i in range(4)]
    it = iter([empty] * 2 + [_reads(["men-2_001", "men-2_002", "men-2_003", "men-2_004"])] * 2)
    monkeypatch.setattr(fm, "read_memory_icons", lambda img: next(it))
    monkeypatch.setattr(fm, "fix_memory", lambda dev, plan, **kw: [{"slot": i} for i in range(4)])
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    assert len(fm.write_memory(dev, PLAN, make_picker=None, blank_only=True)) == 4


def test_write_support_blank_only_stops_when_slot_has_cards(monkeypatch):
    page = {
        "view": "formation",
        # 认不出的卡：support_card_id 是 None，但读得到等级（同编成位调查的口径），也算有卡
        "slots": [{"support_card_id": None, "level": 40}] + [{"support_card_id": None, "level": None}] * 4,
    }
    monkeypatch.setattr(fm, "fix_support", lambda *a, **k: pytest.fail("有卡时不该写"))
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    with pytest.raises(FormationWriteError, match="已经有卡"):
        fm.write_support(dev, PLAN, read_formation=lambda img: page, read_list=None, blank_only=True)


def test_write_support_blank_only_writes_into_empty_slot(monkeypatch):
    """支援卡格没有 empty 字段：全空（认不出卡、也读不到等级）时照常写（#134 review）。"""
    empty = {"view": "formation", "slots": [{"support_card_id": None, "level": None}] * 5}
    full = {"view": "formation", "slots": [{"support_card_id": f"s_card-{i}", "level": 40} for i in range(5)]}
    pages = iter([empty] * 2 + [full] * 2)
    monkeypatch.setattr(fm, "fix_support", lambda dev, plan, **kw: [{"slot": i} for i in range(5)])
    dev = Device(shoot=lambda: None, click=None, swipe=None, sleep=lambda s: None)
    done = fm.write_support(dev, PLAN, read_formation=lambda img: next(pages), read_list=None, blank_only=True)
    assert len(done) == 5
