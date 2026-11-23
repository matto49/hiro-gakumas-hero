"""卡格认卡：角色专属卡图变体按 base 归并（实机帧在私有 fixtures）。
DMM 的游戏数据里有新角色（秦谷美鈴，后缀 -hmsz）的卡图变体，KAA 的 strip_character_suffix 不认这个后缀：
同一张卡票数被拆开、卡图相关第一第二是同一张卡（领先差 0），本战相談 2 格卡认不出、整个货架判不完整，决策直接离开。"""

from hiro.vision import shop
from hiro.vision.deck import base_asset

from test_shop_exec import frame


def test_base_asset_strips_any_character_suffix():
    assert base_asset("img_general_skillcard_men-3_041-hmsz") == "img_general_skillcard_men-3_041"
    assert base_asset("img_general_skillcard_act-2_047-jsna") == "img_general_skillcard_act-2_047"
    assert base_asset("img_general_skillcard_ido-100_047") == "img_general_skillcard_ido-100_047"


def test_main_consult_shelf_complete():
    r = shop.read_consult(frame("main_consult_hmsz"))
    assert r["complete"] is True
    assert [c.get("card_id") for c in r["shelf"][:4]] == [
        "p_card-00-men-2_012",
        "p_card-00-men-3_003",
        "p_card-02-men-3_041",
        "p_card-02-act-1_028",
    ]
    assert [c["price"] for c in r["shelf"]] == [53, 70, 105, 70, 35, 100, 75, 100]


def test_card_db_merges_character_variants(monkeypatch):
    """卡面图库认卡（hiro.vision.card_db.match）：同一张卡的角色专属图（-hmsz 等新角色后缀）合并计票，不被拆成两张。"""
    from hiro.vision import card_db

    monkeypatch.setattr(card_db, "query", lambda img: [card_db.Hit("img_general_skillcard_men-3_041", 2, 900.0),
                                                       card_db.Hit("img_general_skillcard_men-3_041-hmsz", 2, 800.0),
                                                       card_db.Hit("img_general_skillcard_men-3_003", 3, 500.0)])  # fmt: skip
    assert card_db.match(None) == "img_general_skillcard_men-3_041"  # 2 + 2 票 > 3 票
    monkeypatch.setattr(card_db, "query", lambda img: [card_db.Hit("img_general_skillcard_men-3_041-hmsz", 2, 800.0)])
    assert card_db.match(None) is None  # 不到 3 票不认


def test_card_db_ivf_finds_library_card():
    """整库（约 47 万个描述子）走 IVF（nprobe 16）：图库里的卡图拿去查，第一名是它自己。以前全量搜索每张 1.4 秒，换卡弹窗翻屏要几分钟。"""
    from hiro.vision import card_db

    import cv2
    import pytest

    keys, _, index = card_db._db()
    if not keys:
        pytest.skip("没有卡图（KAA 公开数据 skill_cards/，HIRO_KAA_GAME_DATA）")
    if index.ntotal >= card_db.IVF_MIN:
        assert index.nprobe == card_db.NPROBE
    key = keys[len(keys) // 2]
    img = cv2.imread(str(card_db._card_dir() / f"{key}.png"), cv2.IMREAD_COLOR)
    assert card_db.query(img)[0].key == key
