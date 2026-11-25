"""初星課題页（hiro.daily.main_task）：解析 OCR 框。数据是 11-25 账号 3 实机课题页（第 37 步）的 RapidOCR 结果原样抄下。"""

from hiro.daily import main_task

PAGE37 = [
    ("巨課題", 0.9, (13, 37, 121, 75)),
    ("39", 0.9, (285, 424, 327, 454)),
    ("で初星コミュ 1章「第9話」解放", 0.9, (346, 424, 696, 452)),
    ("NEXT", 0.9, (44, 503, 110, 526)),
    ("プロデュースに挑戦して評価C+以上を獲得", 0.9, (61, 553, 448, 573)),
    ("しよう", 0.9, (63, 577, 117, 595)),
    ("アイドル", 0.9, (60, 625, 138, 644)),
    ("【条件】プラン：ロジックのプロデュース", 0.9, (67, 601, 437, 621)),
    ("0/1", 0.9, (234, 649, 285, 673)),
    ("EXP", 0.9, (621, 577, 651, 592)),
    ("150", 0.9, (638, 597, 668, 614)),
    ("50", 0.9, (553, 653, 586, 675)),
    ("36", 0.9, (119, 731, 160, 757)),
    ("-37", 0.9, (291, 725, 390, 763)),
    ("38", 0.9, (562, 731, 603, 757)),
    ("1,000", 0.9, (117, 986, 177, 1007)),
    ("50", 0.9, (365, 986, 394, 1003)),
    ("初星課題", 0.9, (91, 1090, 188, 1115)),
    ("課題挑戦", 0.9, (324, 1086, 445, 1119)),
    ("40で獲得", 0.9, (512, 1055, 614, 1081)),
    ("進捗", 0.9, (513, 1111, 556, 1133)),
    ("36/ 60", 0.9, (586, 1113, 673, 1133)),
]


def test_parse_page37():
    r = main_task.parse(PAGE37)
    assert r == {
        "number": 37,
        "text": "プロデュースに挑戦して評価C+以上を獲得しよう【条件】プラン：ロジックのプロデュースアイドル",
        "progress": [0, 1],
        "done": 36,
        "total": 60,
    }
    assert main_task.at_task_page(PAGE37)
    assert main_task.cleared_popup(PAGE37) is None  # 课题页本身不是完成弹窗


def test_number_falls_back_to_done_plus_one():
    boxes = [b for b in PAGE37 if b[0] != "-37"]
    assert main_task.parse(boxes)["number"] == 37


def test_missing_fields_are_none():
    r = main_task.parse([("巨課題", 0.9, (13, 37, 121, 75))])
    assert r == {"number": None, "text": "", "progress": None, "done": None, "total": None}
    assert not main_task.at_task_page([("巨課題", 0.9, (13, 37, 121, 75))])


CLEAR40 = [  # 11-25 实机：第 37 局打完回主页，「課題CLEAR」弹窗（第 40 步）
    ("課題CLEAR", 0.9, (184, 322, 548, 396)), ("40", 0.9, (63, 508, 117, 543)),
    ("プロデュースアイドル1人の特訓段", 0.9, (140, 491, 577, 523)), ("階を2にしよう", 0.9, (140, 528, 324, 556)),
    ("以下のアイテムを獲得しました", 0.9, (160, 755, 564, 787)), ("プロデュース", 0.9, (264, 981, 458, 1022)),
    ("報酬は自動で受け取られます", 0.9, (220, 1071, 500, 1096)),
]  # fmt: skip
REWARD = [
    ("報酬獲得", 0.9, (227, 294, 493, 365)), ("500", 0.9, (372, 560, 420, 581)), ("ジュエル", 0.9, (303, 618, 419, 650)),
    ("報酬は自動で受け取られます", 0.9, (220, 1071, 500, 1096)),
]  # fmt: skip


def test_cleared_popup():
    assert main_task.cleared_popup(CLEAR40) == {
        "kind": "clear",
        "number": 40,
        "text": "プロデュースアイドル1人の特訓段階を2にしよう",
    }
    assert main_task.cleared_popup(REWARD) == {"kind": "reward", "number": None, "text": ""}
    assert main_task.cleared_popup(PAGE37) is None


def test_close_popups_taps_x_until_gone(monkeypatch):
    from types import SimpleNamespace as NS

    from hiro.daily import common

    screens = [CLEAR40, REWARD, PAGE37]
    taps = []
    rt = NS(device=NS(tap=lambda x, y: (taps.append((x, y)), screens.pop(0))), sleep=lambda s: None)
    monkeypatch.setattr(common, "frame", lambda rt: NS(boxes=screens[0]))
    closed = main_task.close_popups(rt)
    assert [c["kind"] for c in closed] == ["clear", "reward"] and taps == [main_task.POPUP_CLOSE] * 2


def test_progress_with_thousands_separator():
    """第 48 步「コンテストのユニット総合力を25,000以上」：本步进度是「20,640/25,000」这类带逗号的数（11-25 实机读成 None）。"""
    boxes = [b for b in PAGE37 if b[0] != "0/1"] + [("20,640/25,000", 0.9, (180, 649, 340, 673))]
    assert main_task.parse(boxes)["progress"] == [20640, 25000]


def test_enter_closes_popup_that_appears_late(monkeypatch):
    """11-25 实机：回到首页后「課題CLEAR」晚一拍才弹出，close_popups 已经跑过，enter 只等不关 → 30 秒超时。"""
    from types import SimpleNamespace as NS

    from hiro.runtime import nav

    clear48 = [("課題CLEAR", 0.9, (184, 322, 548, 396)), ("48", 0.9, (63, 508, 117, 543)),
               ("コンテストのユニット総合力を", 0.9, (141, 491, 532, 519)), ("25,000以上にしよう", 0.9, (137, 526, 400, 564)),
               ("報酬は自動で受け取られます", 0.9, (220, 1071, 500, 1096))]  # fmt: skip
    home = [("ホーム", 0.9, (329, 1185, 389, 1204))]
    screens = [clear48]
    taps = []

    def tap(x, y):
        taps.append((x, y))
        screens[0] = home if (x, y) == main_task.POPUP_CLOSE else PAGE37

    def drive(step, timeout, what, every=0.5):
        for _ in range(10):
            if (r := step(NS(boxes=screens[0]))) is not None:
                return r
        raise AssertionError(what)

    monkeypatch.setattr(nav, "goto_home", lambda rt: None)
    monkeypatch.setattr(nav, "at_home", lambda f: f.boxes is home)
    closed = []
    rt = NS(device=NS(tap=tap), sleep=lambda s: None, drive=drive)
    f = main_task.enter(rt, closed=closed)
    assert main_task.at_task_page(f.boxes)
    assert taps == [main_task.POPUP_CLOSE, main_task.HOME_BANNER] and closed[0]["number"] == 48


def test_run_returns_home_after_reading(monkeypatch):
    """11-25 实机：main-task 读完 NEXT 停在课题页，接着开的 scan-memories「不在主页也不在プロデュース編成页」退出。读完回主页。"""
    from types import SimpleNamespace as NS

    from hiro.runtime import nav

    calls = []
    monkeypatch.setattr(main_task, "close_popups", lambda rt: [])
    monkeypatch.setattr(nav, "goto_home", lambda rt: calls.append("home"))
    monkeypatch.setattr(main_task, "enter", lambda rt, closed=None: calls.append("enter") or NS(boxes=PAGE37))
    out = main_task.run(NS())
    assert out["next"]["number"] == 37
    assert calls[-1] == "home" and calls.index("enter") < len(calls) - 1
