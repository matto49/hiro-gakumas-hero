"""hiro.master：技能卡 / 饮料主数据（不 import KAA）。用内存里的小库，不依赖真实 game.db。"""

import json
import sqlite3

from hiro import master
from hiro.state import catalog as cat

import pytest

COLS = ("id", "upgradeCount", "name", "assetId", "rarity", "planType", "category", "stamina", "forceStamina", "costType", "costValue",
        "playMovePositionType", "produceDescriptions", "playEffects")  # fmt: skip


@pytest.fixture
def db(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.execute(f"CREATE TABLE ProduceCard ({', '.join(COLS)})")
    con.execute("CREATE TABLE ProduceExamEffect (id, produceDescriptions)")
    con.execute("CREATE TABLE ProduceDrink (id, assetId, name)")
    effects = json.dumps([{"produceExamEffectId": "e1"}, {"produceExamEffectId": "e2"}, {"produceExamTriggerId": "t"}])
    con.executemany(f"INSERT INTO ProduceCard VALUES ({', '.join('?' * len(COLS))})", [
        ("p_card-a", 0, "アピールの基本", "img_a", "R", "Logic", "ActiveSkill", 4, 0, None, 0, "Grave", "[]", effects),
        ("p_card-a", 1, "アピールの基本+", "img_a", "R", "Logic", "ActiveSkill", 3, 0, None, 0, "Grave", "[]", "[]"),
    ])  # fmt: skip
    con.executemany(
        "INSERT INTO ProduceExamEffect VALUES (?, ?)",
        [("e1", json.dumps([{"text": "<nobr>パラメータ+9</nobr>"}])), ("e2", json.dumps([{"text": "元気+2"}]))],
    )
    con.execute("INSERT INTO ProduceDrink VALUES ('pdrink_1', 'img_d1', '初星水')")
    monkeypatch.setattr(cat, "catalog", lambda: cat.Catalog(con))
    monkeypatch.setattr(master, "catalog", lambda: cat.Catalog(con))
    monkeypatch.setattr(cat, "_sim_catalog", lambda: None)
    master.clear()
    yield con
    master.clear()


def test_card_lookups(db):
    c = master.card("p_card-a", 1)
    assert (c._id, c.upgrade_count, c.name, c.stamina, c.category) == (
        "p_card-a",
        1,
        "アピールの基本+",
        3,
        "ActiveSkill",
    )
    assert master.card("p_card-a") == master.cards()[0] and len(master.cards()) == 2
    assert master.card_by_asset("img_a").upgrade_count == 0  # 同图多个强化版本取第一行（同 KAA）
    assert master.card("p_card-x") is None and master.card_by_asset("img_x") is None


def test_effect_display_text_joins_play_effects(db):
    assert master.card("p_card-a").effect_display_text == "パラメータ+9\n元気+2"
    assert master.card("p_card-a", 1).effect_display_text == ""


def test_drinks_and_rows(db):
    assert master.drinks() == (master.Drink("pdrink_1", "img_d1", "初星水"),)
    assert master.drink_by_asset("img_d1").name == "初星水" and master.drink_by_asset("img_x") is None
    assert [r["name"] for r in master.rows("SELECT name FROM ProduceDrink WHERE id = ?", "pdrink_1")] == ["初星水"]


def test_card_falls_back_to_decoded_catalog_row(db, monkeypatch):
    """game.db 里没有的新卡：catalog（模拟器主数据退路）给的是解过 JSON 的 dict，JSON 列转回文本。"""
    row = dict(zip(COLS, ("p_card-new", 0, "新卡", "img_n", "SSR", "Sense", "MentalSkill", 0, 2, None, 0, None,
                          [{"text": "集中+3"}], [])))  # fmt: skip

    def one(self, table, key, variant=None):
        if key != "p_card-new":
            raise KeyError(key)
        return row

    monkeypatch.setattr(cat.Catalog, "one", one)
    c = master.card("p_card-new")
    assert c.name == "新卡" and json.loads(c.produce_descriptions) == [{"text": "集中+3"}] and c._play_effects == "[]"
    assert master.card("p_card-y") is None
