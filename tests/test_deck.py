"""牌组一览（DECK_LIST）与相談削除 / 换卡弹窗的卡格读取。

夹具：
- deck/list-kaa.png：KAA 参考截图「所持スキルカード」（16 张，一屏可见 12 张）
- deck/change2-kaa.png：KAA 参考截图 H.I.F 换卡第 2 步（含两张定制卡：绿色「1」徽章）
- shop/flow/00141-delete_deck.jpg、scenes/00144-SKILL_CARD_REMOVAL.jpg：实机相談削除（同一副 11 张）
真值按截图逐格目视（「+」被效果图标盖住一半的也算强化）。
"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision import deck

FIX = Path(__file__).with_name("fixtures")
DIALOG = (0, 380, 720, 1080)

LIST_TRUTH = [
    ("開花", 0), ("ラブリーウインク", 0), ("手拍子", 0), ("可愛い仕草", 0),
    ("アピールの基本", 0), ("アピールの基本", 0), ("ポーズの基本", 0), ("よそ見はダメ♪", 1),
    ("オトメゴコロ", 0), ("リスタート", 0), ("幸せのおまじない", 1), ("幸せのおまじない", 0),
]  # fmt: skip
REMOVAL_TRUTH = [
    ("アイコンタクトの基本", 0), ("仕草の基本", 0), ("デイドリーミング", 1), ("エウレカ！", 1),
    ("距離感の基本", 0), ("距離感の基本", 0), ("距離感の基本", 0), ("セルフケアの基本", 0),
    ("えいえいおー", 1), ("幸せのおまじない", 1), ("夢色リップ", 1),
]  # fmt: skip
CHANGE_TRUTH = [
    ("手拍子", 1, False), ("みんな大好き", 1, False), ("輝くキミへ", 1, False), ("キセキの魔法", 1, False),
    ("POW！", 1, False), ("セリフの基本", 0, False), ("幸せな時間", 1, True), ("やる気は満点", 0, False),
    ("止められない想い", 1, False), ("ゆめみごこち", 0, False), ("ファンシーチャーム", 0, False), ("オトメゴコロ", 1, True),
]  # fmt: skip


def _img(rel: str):
    return cv2.imread(str(FIX / rel))


def test_deck_list_page():
    img = _img("deck/list-kaa.png")
    assert deck.is_deck_list(img)
    r = deck.read_deck_list(img)
    assert r["deck_size"] == 16
    assert [(c["name"], c["upgrade"]) for c in r["cards"]] == LIST_TRUTH
    assert [(c["row"], c["col"]) for c in r["cards"]] == [(i // 4, i % 4) for i in range(12)]
    assert all(c["customized"] is False for c in r["cards"])
    # 第 4 行被标签栏压住，不认；12 / 16 张 → 不完整，deck 为 None
    assert r["complete"] is False and r["deck"] is None
    assert r["problems"] == ["只看到 12 / 16 张（要滑动再读）"]


@pytest.mark.parametrize("rel", ["shop/flow/00141-delete_deck.jpg", "scenes/00144-SKILL_CARD_REMOVAL.jpg"])
def test_removal_dialog_grid(rel):
    img = _img(rel)
    assert not deck.is_deck_list(img)
    cards = deck.read_card_grid(img, DIALOG)
    assert [(c["name"], c["upgrade"]) for c in cards] == REMOVAL_TRUTH  # 第 3 行第 4 格是背景货架的饮料，不算


def test_change_dialog_grid_custom_badge():
    cards = deck.read_card_grid(_img("deck/change2-kaa.png"), DIALOG)
    assert [(c["name"], c["upgrade"], c["customized"]) for c in cards] == CHANGE_TRUTH


@pytest.mark.parametrize(
    "rel",
    ["select_card/00283.jpg", "shop/consult-r5.jpg", "scenes/00041-ACTION_SELECT.jpg", "scenes/00144-SKILL_CARD_REMOVAL.jpg",
     "deck/change2-kaa.png", "hif_hud/01694.jpg"],
)  # fmt: skip
def test_not_deck_list(rel):
    assert not deck.is_deck_list(_img(rel))


def test_find_deck_button():
    """入口是礼盒图标（KAA OwnedCardsButton）；左边的文档图标是行动记录，qualify7 实机点错过。"""
    assert deck.find_deck_button(_img("select_card/00283.jpg")) == (654, 1200)
    assert deck.find_deck_button(_img("scenes/00041-ACTION_SELECT.jpg")) == (558, 1213)
    assert deck.find_deck_button(_img("shop/consult-r5.jpg")) == (558, 1213)
    assert deck.find_deck_button(_img("deck/q7-action-select-icons.png")) == (558, 1213)  # 实机选行动页（文档 461 / 礼盒 558 / 菜单 653）
    assert deck.find_deck_button(_img("deck/list-kaa.png")) is None


def _read(names: list[str], size: int, *, first_row: int = 0) -> dict:
    cards = [{"index": i, "row": i // 4, "col": i % 4, "card_id": n, "name": n, "upgrade": 0, "customized": False}
             for i, n in enumerate(names)]  # fmt: skip
    return {"deck_size": size, "cards": cards}


def test_merge_deck_pages():
    full = [f"c{i}" for i in range(18)]  # 5 行，最后一行 2 张
    top = _read(full[:12], 18)  # 第 1–3 行
    bottom = _read(full[8:], 18)  # 第 3–5 行
    m = deck.merge_deck_pages(top, bottom)
    assert m["complete"] and m["problems"] == []
    assert [c["card_id"] for c in m["deck"]] == full
    assert [c["index"] for c in m["cards"]] == list(range(18)) and m["cards"][-1]["row"] == 4

    # 重叠行读得不一样
    bad = _read(full[8:], 18)
    bad["cards"][0]["card_id"] = "x"
    m = deck.merge_deck_pages(top, bad)
    assert m["deck"] is None and m["problems"] == ["第 3 行两屏读得不一样"]

    # 26 张 7 行：两屏各 3 行，中间第 4 行没看到
    full = [f"c{i}" for i in range(26)]
    m = deck.merge_deck_pages(_read(full[:12], 26), _read(full[16:], 26))
    assert m["deck"] is None and "第 [4] 行两屏都没看到" in m["problems"]

    # 底屏没滑到底：最后一行张数与总张数对不上
    m = deck.merge_deck_pages(_read(full[:12], 26), _read(full[12:24], 26))
    assert m["deck"] is None and any("没滑到底" in p for p in m["problems"])


def test_deck_list_observation():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("DECK_LIST", _img("deck/list-kaa.png"))
    obs = observation(1, 0.0, "DECK_LIST", raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert f["deck"] is None and f["deck_size"] == 16 and f["complete"] is False and len(f["cards"]) == 12
    assert set(f["cards"][0]) == set(("index", "row", "col", "card_id", "name", "upgrade", "customized", "center", "dimmed"))
    assert obs.field_meta["complete"].note == "只看到 12 / 16 张（要滑动再读）"

    # 拼接后的完整牌组：deck 为 ProduceState.deck 格式
    full = [c["card_id"] for c in raw["cards"]] + [c["card_id"] for c in raw["cards"][:4]]
    merged = deck.merge_deck_pages(_read(full[:12], 16), _read(full[4:], 16))
    obs = observation(2, 0.0, "DECK_LIST", merged)
    assert obs.problems(strict=True) == [] and obs.fields["complete"] is True
    assert obs.fields["deck"][0] == {"card_id": raw["cards"][0]["card_id"], "upgrade": 0, "customized": False}
    assert obs.field_meta["deck"].method == "template"

    from hiro.state.produce import DeckCard

    assert [DeckCard(**c) for c in obs.fields["deck"]][7].card_id == raw["cards"][7]["card_id"]


@pytest.mark.parametrize(
    ("rel", "region", "scrollable", "n"),
    [("deck/enhance-kaa.png", deck.ENHANCE_GRID, True, 12),  # 强化对话框只露出 3 行，右侧有滑块
     ("deck/removal-kaa.png", deck.REMOVAL_GRID, False, 14),  # 删卡对话框 14 张一屏放得下
     ("shop/flow/00141-delete_deck.jpg", deck.REMOVAL_GRID, False, 11)],
)  # fmt: skip
def test_dialog_scroll_and_grid(rel, region, scrollable, n):
    """第九局复盘：相談强化 / 删除对话框的卡格（格式同 SKILL_CARD_REMOVAL.cards）；没有滑块 = 整副牌组都在这一屏。"""
    img = _img(rel)
    st = deck.dialog_scroll(img, region)
    assert st["scrollable"] is scrollable
    if scrollable:
        assert st["at_top"] is True
    cards = deck.read_card_dialog(img, region)["cards"]
    assert len(cards) == n and all(c["card_id"] for c in cards)


def test_merge_dialog_screens():
    """强化对话框一屏 3 行，滚动读 3 屏拼成 22 张（第 3 屏只多出 1 行，再滑一次没有新行 = 到底）。"""
    full = [f"c{i}" for i in range(22)]
    full[4:8] = ["base"] * 4  # 同一行重复的基础卡也要对得上

    def screen(first_row: int, n_rows: int) -> list[dict]:
        cells = [(r, c) for r in range(first_row, first_row + n_rows) for c in range(4) if r * 4 + c < len(full)]
        return [{"row": r - first_row, "col": c, "card_id": full[r * 4 + c], "name": None, "upgrade": 0, "customized": False} for r, c in cells]

    m = deck.merge_dialog_screens([screen(0, 3), screen(2, 3), screen(3, 3), screen(3, 3)])
    assert [c["card_id"] for c in m["cards"]] == full and [c["deck_index"] for c in m["cards"]] == list(range(22))
    assert m["new_rows"] == [3, 2, 1, 0] and m["problems"] == []

    # 滑过了头（第 2 屏从第 4 行开始，和第 1 屏没有重叠）
    m = deck.merge_dialog_screens([screen(0, 3), screen(4, 2)])
    assert any("没有重叠" in p for p in m["problems"]) and m["complete"] is False


def test_merge_q14_no_scroll_wildcards():
    """第十四局相談删除：18 张一屏放得下，滑了一次画面没变；两屏没认出的格不同（第 1 屏 4 格、第 2 屏 1 格）→ 旧版对不上、整屏接在后面，
    拼成 36 张、えいえいおー 3 张。现在没认出的格当通配，对上后用另一屏补全；new_rows 第 2 屏起为 0 = 到底。"""
    screens = [deck.read_card_dialog(_img(f"deck/removal-q14-0{i}.jpg"), deck.REMOVAL_GRID)["cards"] for i in (1, 2, 3)]
    m = deck.merge_dialog_screens(screens, expected=18)
    assert m["complete"] is True and m["new_rows"] == [5, 0, 0] and m["unknown"] == []
    names = [c["name"] for c in m["cards"]]
    assert len(names) == 18 and names.count("えいえいおー") == 2
    assert names[-2:] == ["夢色リップ", "眠気"] and [c["deck_index"] for c in m["cards"]] == list(range(18))
    # 记账张数差太多 → 不交决策
    bad = deck.merge_dialog_screens(screens, expected=30)
    assert bad["complete"] is False and any("差太多" in p for p in bad["problems"])


def test_merge_wildcard_needs_known_match():
    """全是没认出的格不能当对齐依据：两屏毫无重叠时 complete=False。"""
    unk = [{"row": 0, "col": c, "card_id": None, "name": None, "upgrade": None, "customized": None} for c in range(4)]
    known = [{"row": 0, "col": c, "card_id": f"x{c}", "name": None, "upgrade": 0, "customized": False} for c in range(4)]
    m = deck.merge_dialog_screens([unk, known])
    assert m["new_rows"] == [1, 1] and m["complete"] is False


# ------------------------------------------------------------ 一览页多屏（本战开局写 produce.deck，总控 2026-11-03）
def _list_screen(full, first_row, rows=3, size=None):
    """合成一屏 read_deck_list：显示全局第 first_row 行起的 rows 行（本屏行号从 0 起）。"""
    cards = []
    for r in range(first_row, min(first_row + rows, -(-len(full) // 4))):
        for c in range(4):
            i = r * 4 + c
            if i < len(full):
                cards.append({"index": len(cards), "row": r - first_row, "col": c, "card_id": full[i], "name": full[i], "upgrade": i % 2,
                              "customized": i == 5, "dimmed": False, "center": (100 + 160 * c, 450 + 150 * (r - first_row))})  # fmt: skip
    return {"deck_size": size if size is not None else len(full), "cards": cards}


def test_merge_deck_list_overlap():
    """22 张（6 行），每次滑约 2 行：三屏按重叠行拼成整副，deck 为 ProduceState.deck 格式、顺序 = 一览顺序。"""
    from hiro.vision.deck import merge_deck_list

    full = [f"c{i:02d}" for i in range(22)]
    r = merge_deck_list([_list_screen(full, 0), _list_screen(full, 2), _list_screen(full, 3)])
    assert r["complete"] is True and r["screens"] == 3 and len(r["deck"]) == 22
    assert [d["card_id"] for d in r["deck"]] == full and r["deck"][5]["customized"] is True and r["deck"][3]["upgrade"] == 1
    assert [c["deck_index"] for c in r["cards"]] == list(range(22))


def test_merge_deck_list_fallback_and_mismatch():
    """首屏和末屏没有重叠（22 张正好两屏盖满）→ 按行号拼；标签 25 张却只拼出 22 张 → deck=None。"""
    from hiro.vision.deck import merge_deck_list

    full = [f"c{i:02d}" for i in range(22)]
    r = merge_deck_list([_list_screen(full, 0), _list_screen(full, 3)])
    assert r["complete"] is True and [d["card_id"] for d in r["deck"]] == full and "按首屏 + 末屏行号拼" in r["problems"][0]
    bad = merge_deck_list([_list_screen(full, 0, size=25), _list_screen(full, 2, size=25), _list_screen(full, 3, size=25)])
    assert bad["deck"] is None and bad["complete"] is False and any("25" in p for p in bad["problems"])


def test_merge_deck_list_real_no_scroll():
    """KAA 一览参考图（16 张，一屏 12 张）：滑了画面没变 → 只拼出 12 张，不冒充整副。"""
    from hiro.vision.deck import merge_deck_list, read_deck_list

    img = cv2.imread(str(FIX / "deck" / "list-kaa.png"))
    r = merge_deck_list([img, img])  # 执行侧直接传截图
    assert r["deck"] is None and len(r["cards"]) == 12 and r["deck_size"] == 16
    assert merge_deck_list([read_deck_list(img)] * 2)["cards"] == r["cards"]  # 也接受已读好的结果


def test_merged_deck_list_into_produce_deck():
    """拼好的一览页作为 DECK_LIST 观测 → reduce 后 produce.deck 是整副（本战开局保护卡 / 必拿不再按空牌组算）。"""
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce
    from hiro.vision.deck import merge_deck_list
    from hiro.vision.observe import observation

    full = [f"c{i:02d}" for i in range(22)]
    raw = merge_deck_list([_list_screen(full, 0), _list_screen(full, 2), _list_screen(full, 3)])
    obs = observation(1, 0.0, "DECK_LIST", raw)
    st = reduce(GameState(produce={}), obs)
    assert [d["card_id"] for d in st.produce["deck"]] == full and st.produce["deck"][5] == {"card_id": "c05", "upgrade": 1, "customized": True}


def test_dialog_whole_deck_fields():
    """执行侧滑动读整副后：cards 换成 merge_dialog_screens 的整副（带全局 deck_index / dimmed / screen），另给 deck_complete / deck_screens。"""
    from hiro.vision.deck import merge_dialog_screens
    from hiro.vision.observe import observation

    full = [f"c{i:02d}" for i in range(22)]
    screens = [_list_screen(full, 0)["cards"], _list_screen(full, 2)["cards"], _list_screen(full, 3)["cards"]]
    m = merge_dialog_screens(screens, expected=22)
    obs = observation(1, 0.0, "SKILL_CARD_ENHANCE", {"cards": m["cards"], "selected": None, "selected_card": None, "agree": None,
                                                      "confirm": None, "deck_complete": m["complete"], "deck_screens": len(screens)})  # fmt: skip
    f = obs.fields
    assert f["deck_complete"] is True and f["deck_screens"] == 3 and len(f["cards"]) == 22
    assert [c["deck_index"] for c in f["cards"]] == list(range(22)) and f["cards"][21]["screen"] == 2 and f["cards"][0]["dimmed"] is False


def _main20_list():
    """第二十局开局实机一览页（540x960 截图，放大到 720x1280）：20 张，一屏整格可见 3 行 12 张，第 4 行被标签栏挡住一半。"""
    img = cv2.imread(str(FIX / "deck" / "list-main20-540.png"))
    return cv2.resize(img, (720, 1280), interpolation=cv2.INTER_CUBIC)


def test_deck_list_real_main20():
    """实机一览页：原来自动推断几何被误检字母（标签栏、卡面图案）带偏成 dialog，一屏只认出 4 张；固定 list 几何后 12 张全对。"""
    from hiro.vision.deck import is_deck_list, read_deck_list

    img = _main20_list()
    assert is_deck_list(img)
    r = read_deck_list(img)
    assert r["deck_size"] == 20 and r["deck"] is None
    assert [(c["name"], c["upgrade"], c["customized"]) for c in r["cards"]] == [
        ("届いて！", 1, True), ("あのときの約束", 0, False), ("せのびの魔法", 0, False), ("エウレカ！", 1, False),
        ("きらきら紙吹雪", 1, False), ("デイドリーミング", 1, True), ("私がスター", 1, False), ("夢色リップ", 0, False),
        ("ほかほか焼き芋", 1, False), ("ワクワクが止まらない", 0, False), ("ひなたぼっこ", 1, False), ("止められない想い", 1, False)]  # fmt: skip


def test_quick_page_closes_deck_list():
    """快速判页最先认一览页、点「閉じる」：KAA 会把一览底部「Pアイテム」标签判成 SELECT_PITEM（第二十局开局停机）。"""
    from hiro.vision.quick import quick_page

    q = quick_page(_main20_list())
    assert (q["page"], q["tap"], q["dispatch"]) == ("DECK_LIST", (360, 1157), None)


def test_merge_deck_list_real_main20r1():
    """第二十局续跑开局读整副的两屏实拍（滑 300px ≈ 2 行，重叠 1 行，第 2 屏到底）：原来拼出 8 张失败，现在 20 张整副。"""
    from hiro.vision.deck import merge_deck_list

    imgs = [cv2.imread(str(FIX / "deck" / f"list-main20r1-{n}.jpg")) for n in ("00", "01")]
    r = merge_deck_list(imgs)
    assert r["complete"] is True and r["problems"] == [] and len(r["deck"]) == 20
    names = [c["name"] for c in r["cards"]]
    assert names[:4] == ["届いて！", "あのときの約束", "せのびの魔法", "エウレカ！"]
    assert names[-8:] == ["ゆめみごこち", "成長痛", "えいえいおー", "えいえいおー", "えいえいおー", "思い出し笑い", "距離感の基本", "紅葉ランニング"]
    assert [(d["upgrade"]) for d in r["deck"][12:16]] == [1, 1, 1, 0]


def _cell_with_red(blobs, size=141):
    """合成卡格：白底，在「+」区域里按 blobs 画红色块 [(x, y, w, h)]（相对「+」区域左上角）。"""
    import numpy as np

    img = np.full((size, size, 3), 255, np.uint8)
    y1, x1 = int(round(deck.PLUS_BOX[0] * size)), int(round(deck.PLUS_BOX[2] * size))
    for x, y, w, h in blobs:
        img[y1 + y : y1 + y + h, x1 + x : x1 + x + w] = (0, 0, 230)  # BGR 纯红
    return img


@pytest.mark.synthetic
def test_plus_scattered_art_red_is_zero():
    """z5（11-19 DMM 1080x1920）：がむしゃら 的橙色爆炸插画在「+」区域里有 6–12 个零星红点（最大连通块 ≤ 8），落在「看不清」区间，
    牌组一览因此十来次拼接失败、相談删卡没做。零星红点（最大连通块不到阈值一半）判 0。"""
    dots = [(2 + 6 * i, 4 + (i % 3) * 5, 2, 2) for i in range(5)]  # 5 个 2x2 红点 = 20 px，最大块 4
    assert deck._plus(_cell_with_red(dots)) == 0


@pytest.mark.synthetic
def test_plus_compact_partial_red_stays_unsure():
    assert deck._plus(_cell_with_red([(4, 4, 6, 5)])) is None  # 30 px 成块：可能是没显示全的「+」，仍然不确定


def _z5(name):
    import cv2

    p = Path(__file__).parent / "fixtures" / "deck_list" / name
    if not p.exists():
        pytest.skip(f"没有 z5 牌组一览帧 {name}")
    return cv2.imread(str(p))


def test_deck_list_drops_half_visible_row():
    """z5（11-19 01:45 DMM 1080x1920）：第 1 屏最下一行被标签栏挡住，只认出最右的 眠気，当成「不满 4 张的末行」，
    第 2 屏就判「没有重叠的行」、拼出 25 张。一览每行从左往右排满：不是从第 1 列连续排起的行是露了一半，丢掉。"""
    r = deck.read_deck_list(_z5("dl-20261119-014525-00.jpg"))
    rows = {}
    for c in r["cards"]:
        rows.setdefault(c["row"], []).append(c["col"])
    assert all(cols == list(range(len(cols))) for cols in rows.values())
    assert [c["name"] for c in r["cards"]][-1] != "眠気" and len(r["cards"]) == 12


def test_deck_list_merge_z5_first_review():
    from hiro.produce.deck_review import merge_screens

    imgs = [_z5(f"dl-20261119-014525-0{i}.jpg") for i in range(2)]
    out = merge_screens(imgs)
    assert not any("没有重叠" in p for p in out.get("problems") or []), out.get("problems")
