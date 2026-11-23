"""偶像之路页面（hiro.vision.tower + pages / classify 接入）：Mac 实机采样 12 张（11-14，协调）。"""

from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "tower" / "pages"


def _hit(name):
    from hiro.vision.classify import classify

    import cv2

    path = FIX / name
    if not path.exists():
        pytest.skip(f"需要实机截图 tests/fixtures/tower/pages/{name}")
    h = classify(cv2.imread(str(path)))
    return h.name, {k: v for k, v in (h.fields or {}).items() if k != "_quick"}


def test_list_layers_info():
    name, f = _hit("03_list.png")
    assert name == "TOWER_LIST" and [(i["stage"], i["name"], i["progress"]) for i in f["idols"][:3]] == [
        (22, "花海咲季", "62/81"), (14, "月村手毬", "37/81"), (25, "藤田ことね", "75/81")]  # fmt: skip
    name, f = _hit("04_idol_layers.png")
    assert (
        name == "TOWER_LAYERS" and f["idol"] == "月村手毬" and f["stage"] == 15 and f["challenge"] and not f["locked"]
    )
    # 层号取居中放大那层的大数字（左栏「STAGE 15 報酬」滑动后不变）；星数是那层的三颗星；最前沿锁着时挑戦する是灰的
    for frame, stage, stars, locked in (("21_layers_after_clear.png", 15, 1, False), ("22_layers_scroll_down1.png", 13, 2, False),
                                        ("23_layers_scroll_down2.png", 10, 3, False), ("26_layers_frontier.png", 16, 0, True)):  # fmt: skip
        name, f = _hit(frame)
        assert (name, f["stage"], f["stars"], f["locked"]) == ("TOWER_LAYERS", stage, stars, locked), frame
    name, f = _hit("05_layer_info.png")
    assert name == "TOWER_LAYER_INFO" and [(c["star"], c["text"]) for c in f["conditions"]] == [
        (3, "1位でクリア"), (2, "2位以上でクリア"), (1, "3位以上でクリア")]  # fmt: skip


def test_formation_reads_loadout_fields():
    """编成页给 HIRO_TOWER_LOADOUT：回合数、三属性倍率千分比、体力上限（8a3a1e7 的 tower_context 读这个格式）。"""
    name, f = _hit("06_challenge.png")
    assert name == "TOWER_FORMATION"
    assert (f["layer"], f["turns"], f["score_bonus_permils"], f["max_stamina"]) == (15, 15, [23090, 11980, 2600], 46)
    assert f["challenge"] and f["omakase"]  # おまかせ 只记位置，处理器绝不点
    assert f["memory_ratings"] == [12075, 10773, 9978]  # 主 / サブ1 / サブ2 回忆评价值：编成签名用


def test_memory_select_result_retry_exam():
    assert _hit("07_memory_select.png")[0] == "TOWER_MEMORY_SELECT"
    name, f = _hit("09_result.png")
    assert (
        name == "TOWER_RESULT" and (f["cleared"], f["layer"], f["stars"]) == (True, 15, 1) and f["back"] and f["retry"]
    )
    name, f = _hit("10_retry_confirm.png")
    assert name == "TOWER_RETRY_CONFIRM" and f["cost"] is None and f["same"] and f["ok"]  # 没有消耗字样
    assert _hit("11_retry_started.png")[0] == "TOWER_EXAM"  # 先于 KAA 的 EXAM 模板


def test_formation_page2_reads_deck():
    """编成页第 2/2 页：亮着的卡进牌组（「重複」「制限」灰掉的跳过），右上角红「+」是强化；认不出的记 unread。"""
    name, f = _hit("38_formation_page2.png")
    assert name == "TOWER_FORMATION" and f["page"] == 2 and f["memory_ratings"] == [12075, 10773, 9978]
    d = f["deck"]
    assert [sum(c["dim"] for c in r) for r in d["rows"]] == [1, 3, 4]
    assert (
        "p_card-01-ido-2_017+1" in d["cards"]
        and "p_card-00-sup-3_027" in d["cards"]
        and len(d["cards"]) + d["unread"] == 10
    )
    assert (
        "p_card-01-act-2_032+1" in d["cards"] and d["unread"] == 0
    )  # スタンドプレー+：带等级 / 角标，卡格规则没把握，两票对一票认出


def test_ranking_page_after_exam():
    """考完的名次页（第一次实机 r1：判成 unknown，180 秒停机）：左上名次徽章、各行分数、「次へ」。"""
    name, f = _hit("40_after_r1_stop.png")
    assert name == "TOWER_RANKING" and f["my_rank"] == 3 and f["next"]
    assert [(r["rank"], r["name"]) for r in f["rows"][:3]] == [
        (1, "白草四音"),
        (2, "暁美みずき"),
        (3, "月村手毬"),
    ] and f["rows"][2]["score"] == 54009
    name, f = _hit("06_challenge.png")
    assert (f["idol_name"], f["idol_card_title"]) == ("月村手毬", "一匹狼")


def test_failed_ranking_page():
    """没过关的名次页（左上「不合格」）：没有「次へ」，再挑戦 / 一覧に戻る 在这一页；自己那行的 Pt 读成「P1」也要收。"""
    name, f = _hit("60_failed_ranking.png")
    assert name == "TOWER_RANKING" and f["failed"] and f["retry"] and f["back"] and f["next"] is None
    assert [(r["rank"], r["name"], r["score"]) for r in f["rows"][:5]] == [
        (1, "白草四音", 105438), (2, "暁美みずき", 76661), (3, "鳴海ひかり", 48593), (4, "月村手毬", 45231), (5, "高円寺レイ", 24508)]  # fmt: skip


def test_exam_over_on_ranking_not_in_exam():
    from hiro.runtime.tower_play import exam_over

    import cv2

    for name in ("60_failed_ranking.png", "40_after_r1_stop.png", "09_result.png"):
        if not (FIX / name).exists():
            pytest.skip("需要实机截图")
        assert exam_over(cv2.imread(str(FIX / name))), name
    assert not exam_over(cv2.imread(str(FIX / "11_retry_started.png")))  # 考试中


def test_perfect_result_page():
    """三星（1 位）的结果页标题是「PERFECT」不是「CLEAR」（Mac 手毬 14 层 11-15，原来判成 unknown 卡住）：照样是 TOWER_RESULT，星数 3。"""
    name, f = _hit("100_perfect_result.png")
    assert (
        name == "TOWER_RESULT" and (f["cleared"], f["layer"], f["stars"]) == (True, 14, 3) and f["back"] and f["retry"]
    )


def test_list_card_found_when_stage_sparkles():
    """g5b 11-17：手毬那张「STAGE 15」上有闪光动画，OCR 读成「STA」「¥15」，原来按 STAGE 定位整张卡漏掉 →「列表里没找到月村手毬」。
    现在按进度「x/81」定位：stage 读不到记 None，名字 / 进度 / 点击位置照样有。"""
    name, f = _hit("list-stage-sparkle-g5b.jpg")
    assert name == "TOWER_LIST"
    tm = next(i for i in f["idols"] if i["name"] == "月村手毬")
    assert tm["stage"] is None and tm["progress"] == "41/81" and 800 < tm["center"][1] < 950
