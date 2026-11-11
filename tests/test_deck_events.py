# 来源：digital-hiro tests/test_deck_events.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""改变牌组的事件页面：删除 / 换卡页的选中项、确认弹窗（交換確認 / スキルカード削除）、获得弹窗。

夹具 tests/fixtures/events/*.jpg 取自 datasets/20261029-224103-hif-qualify 与 traces/<run>-qualify2-*-r2，真值按截图目视。
"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision import deck

FIX = Path(__file__).with_name("fixtures") / "events"

KYORI = {"card_id": "p_card-02-men-0_039", "name": "距離感の基本", "upgrade": 0}


def _img(name: str):
    return cv2.imread(str(FIX / f"{name}.jpg"))


@pytest.mark.parametrize(
    ("name", "selected", "card"),
    [
        ("removal-none-00141", None, None),
        ("removal-sel0-00142", 0, {"card_id": "p_card-02-act-0_037", "name": "アイコンタクトの基本", "upgrade": 0}),
        ("removal-sel6-00146", 6, KYORI),
    ],
)
def test_card_dialog_selected(name, selected, card):
    r = deck.read_card_dialog(_img(name))
    assert len(r["cards"]) == 11
    assert r["selected"] == selected and r["selected_card"] == card
    assert r["agree"] is (None if selected is None else True)


def test_card_dialog_disagreement_gives_none(monkeypatch):
    """角框选中的格子和详情栏卡名对不上时不猜。"""
    monkeypatch.setattr(deck, "card_from_name", lambda text, candidates=None: {"card_id": "x", "name": "x", "upgrade": 0} if "基本" in text else None)
    r = deck.read_card_dialog(_img("removal-sel6-00146"))
    assert r["selected"] == 6 and r["agree"] is False and r["selected_card"] is None


@pytest.mark.parametrize(
    ("name", "expect"),
    [
        ("delete-confirm-00148", {"kind": "delete", "item": {"kind": "card", **KYORI}, "p_before": 116, "p_after": 16, "price": 100, "sale": False}),
        ("exchange-confirm-00135", {"kind": "exchange", "item": {"kind": "card", "card_id": "p_card-02-men-3_040", "name": "夢色リップ", "upgrade": 1},
                                    "p_before": 221, "p_after": 116, "price": 105, "sale": False}),
    ],
)  # fmt: skip
def test_confirm_dialog(name, expect):
    assert deck.read_confirm_dialog(_img(name)) == expect


def test_confirm_dialog_sale():
    r = deck.read_confirm_dialog(cv2.imread(str(FIX.parent / "shop" / "flow" / "00127-confirm.jpg")))
    assert (r["kind"], r["item"]["name"], r["item"]["upgrade"], r["price"], r["sale"]) == ("exchange", "デイドリーミング", 1, 49, True)


@pytest.mark.parametrize("name", ["removal-sel6-00146", "acquire-memory-00028", "not-acquire-select-drink"])
def test_not_confirm_dialog(name):
    assert deck.read_confirm_dialog(_img(name)) is None


@pytest.mark.parametrize(
    ("name", "kind", "item_id", "upgrade"),
    [
        ("acquire-memory-00028", "card", "p_card-02-men-1_035", 1),  # メモリー効果：幸せのおまじない+
        ("acquire-shop-00128", "card", "p_card-02-act-1_037", 1),  # 相談购买后的获得演出：デイドリーミング+
        ("acquire-study-00224", "card", "p_card-02-men-2_101", 0),  # 授業：冒険心
        ("acquire-upgraded-00303", "card", "p_card-02-men-2_111", 1),  # 成長痛+
        ("acquire-sleepy-00263", "card", "p_card-00-acc-0_002", 0),  # 外出トラブル：眠気
        ("acquire-drink-00270", "drink", "pdrink_02-3-010", None),  # 特製ハツボシエキス
        ("acquire-pitem-00166", "pitem", "pitem_00-3-010-0", None),  # 天川ラーメン巡り（名称前图标被切成单字框）
        ("acquire-wappen-00034", "pitem", "pitem_00-3-265-0", None),  # H.I.Fワッペン
    ],
)
def test_acquire_popup(name, kind, item_id, upgrade):
    r = deck.read_acquire_popup(_img(name))
    assert r["kind"] == kind and r[f"{kind}_id"] == item_id
    if kind == "card":
        assert r["upgrade"] == upgrade and r["agree"] is True


@pytest.mark.parametrize("name", ["not-acquire-select-drink", "not-acquire-drink-detail-01824", "removal-sel6-00146", "delete-confirm-00148"])
def test_not_acquire_popup(name):
    """选饮料页瓶身标签「初星水」、考试「Pドリンク詳細」的饮料名都在同一高度，不能当成获得弹窗。"""
    assert deck.read_acquire_popup(_img(name)) is None


def test_card_dialog_observation():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("SKILL_CARD_REMOVAL", _img("delete-confirm-00148"))
    obs = observation(1, 0.0, "SKILL_CARD_REMOVAL", raw)
    assert obs.problems(strict=True) == []
    assert obs.fields["confirm"]["price"] == 100 and obs.fields["confirm"]["item"]["card_id"] == KYORI["card_id"]

    raw, _ = extract("SKILL_CARD_REMOVAL", _img("removal-sel6-00146"))
    obs = observation(2, 0.0, "SKILL_CARD_REMOVAL", raw)
    assert obs.fields["selected"] == 6 and obs.fields["selected_card"] == KYORI and obs.fields["confirm"] is None
    assert set(obs.fields["cards"][0]) == {"index", "row", "col", "card_id", "name", "upgrade", "customized", "center", "dimmed"}


def test_change1_candidates():
    """识别-07：换卡第 1 步（换进哪张）候选，格式同 SELECT_CARD（另带 index）。KAA 参考图，尚无实机样本。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX.parent / "deck" / "change1-kaa.png")))
    scene = classify_current().scene
    assert scene == "SKILL_CARD_CHANGE_1"
    raw, _ = extract(scene, img)
    obs = observation(1, 0.0, scene, raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert [(c["index"], c["name"], c["upgrade"], c["recommended"]) for c in f["candidates"]] == [
        (0, "200%スマイル", 0, True), (1, "パステル気分", 0, False), (2, "リスタート", 0, True)]
    assert (f["recommended"], f["rerolls_left"], f["excludes_left"], f["complete"]) == (0, 2, 1, True)


@pytest.mark.parametrize(("name", "selected", "card"), [("enhance-kaa", None, None), ("enhance-selected-kaa", 1, "可愛い仕草")])
def test_enhance_grid_region(name, selected, card):
    """识别-07：强化页卡格从 y≈698 起（删卡页 398 起），用删卡页区域只能读到 8 格。"""
    from hiro.vision.extract import extract
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX.parent / "deck" / f"{name}.png")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    assert scene == "SKILL_CARD_ENHANCE" and len(raw["cards"]) == 12
    assert raw["selected"] == selected and (raw["selected_card"] or {}).get("name") == card


def test_plus_not_fooled_by_illustration():
    """偶像插画卡（红伞狐面）的红色曾被当成「+」→ upgrade=1；红色块太高、不像「+」时给 None（数据确认 enhance-kaa 第 7 格）。"""
    from hiro.vision.extract import extract
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX.parent / "deck" / "enhance-kaa.png")))
    raw, _ = extract(classify_current().scene, img)
    lamune = raw["cards"][7]
    assert lamune["name"] == "はじめてのラムネ" and lamune["upgrade"] is None
    assert all(c["upgrade"] == 0 for i, c in enumerate(raw["cards"]) if i != 7)


def test_change1_icon_not_a_letter():
    """qualify6-r2 #29（授業チェンジ，无除去 / 再抽選按钮）：第 2 张卡左侧效果图标被认成「M」，同一张卡曾在两个 slot 出现。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX.parent / "deck" / "change1-q6r2-00029.jpg")))
    scene = classify_current().scene
    assert scene == "SKILL_CARD_CHANGE_1"
    raw, _ = extract(scene, img)
    f = observation(1, 0.0, scene, raw).fields
    assert [(c["index"], c["name"], c["upgrade"], c["recommended"]) for c in f["candidates"]] == [
        (0, "ひなたぼっこ", 0, True), (1, "気まぐれハート", 0, False), (2, "せのびの魔法", 0, False)]
    assert [c["center"][0] for c in f["candidates"]] == [223, 362, 500]
    assert (f["recommended"], f["rerolls_left"], f["excludes_left"], f["complete"]) == (0, None, None, True)


@pytest.mark.parametrize("name", ["enhance-dim-main10-01", "enhance-dim-main10-03"])
def test_enhance_dialog_dimmed_upgraded_cards(name):
    """main10 相談強化：已满级（+）的卡在强化对话框里压暗，「+」跟着变暗，upgrade 全读成 0，决策选了 届いて！+（「これ以上強化不可能」）。
    现在压暗格先提亮再判「+」/ 定制，dimmed=True；选中满级卡时详情框「強化できません」→ upgradable=False。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("SKILL_CARD_ENHANCE", cv2.imread(str(FIX.parent / "deck" / f"{name}.jpg")))
    obs = observation(1, 0.0, "SKILL_CARD_ENHANCE", raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    got = [(c["name"], c["upgrade"], c["customized"], c["dimmed"]) for c in f["cards"]]
    assert got == [("えいえいおー", 0, False, False), ("えいえいおー", 0, False, False), ("ゆめみごこち", 0, False, False), ("気まぐれハート", 0, False, False),
                   ("夢色リップ", 0, False, False), ("トキメキ", 0, False, False), ("デイドリーミング", 1, True, True), ("ありがとうの言葉", 1, False, True),
                   ("きらきら紙吹雪", 1, True, True), ("届いて！", 1, True, True), ("あのときの約束", 1, False, True), ("せのびの魔法", 1, False, True)]  # fmt: skip
    assert f["upgradable"] is False and f["selected_card"]["name"] == "届いて！"


def test_enhance_dialog_upgradable_selected():
    """KAA 参考图：选中可强化的卡 → upgradable=True。"""
    from hiro.vision.extract import extract

    raw, _ = extract("SKILL_CARD_ENHANCE", cv2.imread(str(FIX.parent / "deck" / "enhance-selected-kaa.png")))
    assert raw["selected"] == 1 and raw["upgradable"] is True


def test_card_from_name_dash_normalized():
    """RapidOCR 把长音「ー」读成「一」（DMM dmm2 特別指導 sg-dmm2-2c）：统一长音后仍命中主数据。"""
    exact = deck.card_from_name("えいえいおー+")
    assert exact is not None and exact["upgrade"] == 1
    got = deck.card_from_name("えいえいお一+")
    assert got == exact
    assert deck.card_from_name("えいえいお一一+") is None
