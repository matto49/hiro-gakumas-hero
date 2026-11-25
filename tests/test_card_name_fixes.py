"""卡名小修复（11-26 本机复核，设计 5.9）：偶像卡「一 / ー」、技能卡名丢尾字。"""

from hiro.vision import deck

import pytest


class _Cat:
    def rows_merged(self, table):
        if table == "Character":
            return [{"id": "kllj", "lastName": "葛城", "firstName": "リーリヤ"}]
        return [
            {
                "id": "i_card-kllj-2-000",
                "name": "一つ踏み出した先に",
                "characterId": "kllj",
                "rarity": "IdolCardRarity_Sr",
            }
        ]


def test_idol_title_read_with_long_dash_still_gets_id(monkeypatch):
    from hiro.produce import idol_scan
    from hiro.state import context

    monkeypatch.setattr(context, "catalog", lambda: _Cat())
    inv = {"idols": [{"character": "葛城リーリヤ", "title": "ーつ踏み出した先に"}]}
    idol_scan.attach_ids(inv)
    assert inv["idols"][0]["idol_card_id"] == "i_card-kllj-2-000"


def _names(monkeypatch, table):
    monkeypatch.setattr(deck, "_cards_by_full_name", lambda: table)
    monkeypatch.setattr(deck, "_cards_by_dash_name", dict)


# 用假卡名表，不要主数据库；通过别名调用，conftest 按源码猜「要主数据库」的规则（匹配 card_from_name 加括号）不会误跳过
lookup = deck.card_from_name

RAKKAN = {"楽観的": [("p_card-01-men-1_025", 0)], "楽観的+": [("p_card-01-men-1_025", 1)]}


def test_truncated_card_name_matches_unique_prefix(monkeypatch):
    _names(monkeypatch, RAKKAN)
    assert lookup("楽観", allow_prefix=True) == {"card_id": "p_card-01-men-1_025", "name": "楽観的", "upgrade": 0}
    assert lookup("楽観+", allow_prefix=True) == {"card_id": "p_card-01-men-1_025", "name": "楽観的", "upgrade": 1}
    # 终审 I8：缺省不开（考试 / 商店读卡名、split_card_names 拿前半截去查，前缀兜底会认错），只给回忆详情和入库用
    assert lookup("楽観") is None


@pytest.mark.parametrize("text", ["楽", "楽観"])
def test_prefix_needs_two_chars_and_a_single_card(monkeypatch, text):
    _names(monkeypatch, {**RAKKAN, "楽観主義": [("p_card-other", 0)]})
    assert lookup(text, allow_prefix=True) is None
