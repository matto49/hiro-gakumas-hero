"""主数据里的技能卡、饮料，以及识别层要的几张表：不 import KAA，经 hiro.state.catalog（AssetSource.master_db()）读。

Card 的属性名和 KAA 的 SkillCard 一致（包括 `_id`）：去 KAA 期间，考试手牌（KAA locate_cards）、选卡页（KAA fetch_cards）
给的还是 KAA 的卡对象，和这里的 Card 进同一批函数（card_view / card_summary …）。那些入口换成自有实现后（去 KAA 第 7 步），
`_id` 统一改名 `id`。

列表查询（cards / drinks）按主数据整表缓存；换了主数据（测试、更新 game.db）调 clear()。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from functools import cache, cached_property

from hiro.state.catalog import catalog

_CARD_COLUMNS = ('id, upgradeCount, name, rarity, planType, category, stamina, forceStamina, costType, costValue, '
                 'playMovePositionType, produceDescriptions, playEffects')  # fmt: skip


def rows(sql: str, *args) -> list[sqlite3.Row]:
    """主数据原样查询（列按名取：row["name"]）。没有主数据时抛 hiro.state.catalog.CatalogUnavailable。"""
    return catalog().db.execute(sql, args).fetchall()


@dataclass(frozen=True)
class Card:
    """技能卡的一个强化版本（ProduceCard 一行）。"""

    _id: str
    upgrade_count: int
    name: str
    rarity: str | None = None
    plan_type: str | None = None
    category: str | None = None
    stamina: int | None = None
    force_stamina: int | None = None
    cost_type: str | None = None
    cost_value: int | None = None
    play_move_position_type: str | None = None
    produce_descriptions: str | None = None  # 卡面完整描述（JSON 文本），完整文本见 hiro.vision.cards.full_text
    _play_effects: str | None = field(default=None, repr=False)

    @cached_property
    def effect_display_text(self) -> str:
        """各出牌效果的说明拼起来（ProduceExamEffect.produceDescriptions）；produce_descriptions 为空时的退路。"""
        ids = [e.get("produceExamEffectId") for e in json.loads(self._play_effects or "[]") if isinstance(e, dict)]
        texts = []
        for eid in filter(None, ids):
            row = next(iter(rows("SELECT produceDescriptions FROM ProduceExamEffect WHERE id = ?", eid)), None)
            if row is not None and row[0]:
                texts.append("".join(d.get("text", "") for d in json.loads(row[0])))
        return "\n".join(texts).replace("</nobr>", "").replace("<nobr>", "").strip()


def _text(v) -> str | None:
    return v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _card(r) -> Card:
    """r：ProduceCard 一行（sqlite3.Row，或 catalog 解过 JSON 的 dict）。"""
    return Card(r["id"], r["upgradeCount"] or 0, r["name"], r["rarity"], r["planType"], r["category"], r["stamina"], r["forceStamina"],
                r["costType"], r["costValue"], r["playMovePositionType"], _text(r["produceDescriptions"]), _text(r["playEffects"]))  # fmt: skip


@cache
def cards() -> tuple[Card, ...]:
    """全部技能卡（每个强化版本一项）。"""
    return tuple(_card(r) for r in rows(f"SELECT {_CARD_COLUMNS} FROM ProduceCard"))


def card(card_id: str, upgrade: int = 0) -> Card | None:
    """(卡 id, 强化次数) → 卡；game.db 里没有的新卡退回模拟器主数据（hiro.state.catalog 的退路），都没有给 None。"""
    r = next(
        iter(rows(f"SELECT {_CARD_COLUMNS} FROM ProduceCard WHERE id = ? AND upgradeCount = ?", card_id, upgrade)), None
    )
    if r is not None:
        return _card(r)
    try:
        return _card(catalog().one("ProduceCard", card_id, upgrade))
    except (KeyError, IndexError):
        return None


def card_by_asset(asset_id: str) -> Card | None:
    """卡图资源名（assetId）→ 卡；同一张图有多个强化版本时取第一行（同 KAA SkillCard.from_asset_id）。"""
    r = next(iter(rows(f"SELECT {_CARD_COLUMNS} FROM ProduceCard WHERE assetId = ?", asset_id)), None)
    return _card(r) if r is not None else None


@dataclass(frozen=True)
class Drink:
    id: str
    asset_id: str
    name: str


@cache
def drinks() -> tuple[Drink, ...]:
    return tuple(Drink(r["id"], r["assetId"], r["name"]) for r in rows("SELECT id, assetId, name FROM ProduceDrink"))


def drink_by_asset(asset_id: str) -> Drink | None:
    return next((d for d in drinks() if d.asset_id == asset_id), None)


def clear() -> None:
    cards.cache_clear()
    drinks.cache_clear()
