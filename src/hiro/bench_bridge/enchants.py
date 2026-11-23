"""主数据里的附魔：打出一张卡 / 带着一个 P 道具，会挂上哪些持续效果（UIObs ench 的 [附魔 id, 次数, 回合]）。

画面上持续效果只有来源卡名，没有 ID：只看画面时，出牌插件按「自己打出了哪张卡」从这里查它挂的附魔（ScreenTracker 记账），
开考时加上偶像自带 P 道具的附魔。次数画面上看不到触发，按初始值记。
"""

from __future__ import annotations

from functools import cache, lru_cache  # noqa: F401

INF = -1
CARD_ENCHANT_TYPES = ("ProduceExamEffectType_ExamStatusEnchant", "ProduceExamEffectType_ExamStatusEnchantEncore")


def _n(v) -> int:
    v = int(v or 0)
    return INF if v <= 0 else v


PLAYABLE_ADD = "ProduceExamEffectType_ExamPlayableValueAdd"
CREATE_TO_DECK = (
    "ProduceCardMovePositionType_DeckRandom",
    "ProduceCardMovePositionType_DeckFirst",
    "ProduceCardMovePositionType_DeckLast",
)


class Enchants:
    """画面跟踪要用的主数据事实（引擎 catalog）：打出 / 喝下挂的附魔、打出时往山札塞的卡、回合结束除外的卡、卡名 → 卡；查不到时返回空。"""

    def __init__(self, catalog=None):
        if catalog is None:
            from gakumas_exam.catalog import Catalog

            catalog = Catalog()
        self.cat = catalog

    def _get(self, table, key, variant=None):
        try:
            return self.cat.one(table, key, variant)
        except Exception:  # noqa: BLE001
            return None

    @cache  # noqa: B019
    def _row(self, card_id: str, upgrade: int):
        """卡的主数据行；强化超过最高等级时按最高等级。"""
        for u in range(int(upgrade or 0), -1, -1):
            row = self._get("ProduceCard", card_id, u)
            if row is not None:
                return row
        return None

    @cache  # noqa: B019  一局里同一张卡反复查
    def card(self, card_id: str, upgrade: int) -> tuple:
        """打出这张卡挂上的附魔：((附魔 id, 次数, 回合, 只第一次打出时), …)。"""
        row = self._row(card_id, upgrade)
        out = []
        for pe in (row or {}).get("playEffects") or ():
            e = self._get("ProduceExamEffect", pe.get("produceExamEffectId"))
            if e and e.get("effectType") in CARD_ENCHANT_TYPES and e.get("produceExamStatusEnchantId"):
                out.append(
                    (
                        e["produceExamStatusEnchantId"],
                        _n(e.get("effectCount")),
                        _n(e.get("effectTurn")),
                        bool(pe.get("isOncePlayEffect")),
                    )
                )
        return tuple(out)

    @cache  # noqa: B019
    def creates(self, card_id: str, upgrade: int) -> tuple:
        """打出这张卡时往山札塞的卡：((卡 id, 强化, 张数), …)（ExamCardCreateId → DeckRandom / DeckFirst，例：气まぐれハート → 眠気）。"""
        out = []
        for pe in (self._row(card_id, upgrade) or {}).get("playEffects") or ():
            e = self._get("ProduceExamEffect", pe.get("produceExamEffectId"))
            if (
                e
                and e.get("effectType") == "ProduceExamEffectType_ExamCardCreateId"
                and e.get("targetProduceCardId")
                and e.get("movePositionType") in CREATE_TO_DECK
            ):
                out.append(
                    (
                        e["targetProduceCardId"],
                        int(e.get("targetUpgradeCount") or 0),
                        max(1, int(e.get("effectValue1") or 0)),
                    )
                )
        return tuple(out)

    @cache  # noqa: B019
    def plays_added(self, card_id: str, upgrade: int) -> tuple:
        """打出这张卡确定会加的出牌次数（スキルカード使用数追加）：((几回合后, 次数), …)。0 = 本回合（ExamPlayableValueAdd，
        次数在 effectCount）；ExamEffectTimer 链着它的是 effectValue1 回合后（「次のターン、」= 1）。带触发条件的不算（画面上判断不了）。"""
        out = []
        for pe in (self._row(card_id, upgrade) or {}).get("playEffects") or ():
            if pe.get("produceExamTriggerId"):
                continue
            e = self._get("ProduceExamEffect", pe.get("produceExamEffectId")) or {}
            if e.get("effectType") == PLAYABLE_ADD:
                out.append((0, max(1, int(e.get("effectCount") or 0))))
            elif e.get("effectType") == "ProduceExamEffectType_ExamEffectTimer":
                for cid in e.get("chainProduceExamEffectIds") or ():
                    c = self._get("ProduceExamEffect", cid) or {}
                    if c.get("effectType") == PLAYABLE_ADD:
                        out.append((max(1, int(e.get("effectValue1") or 0)), max(1, int(c.get("effectCount") or 0))))
        return tuple(out)

    @cache  # noqa: B019
    def end_turn_lost(self, card_id: str, upgrade: int) -> bool:
        """回合结束时还在手里就除外（isEndTurnLost，例：眠気）。"""
        return bool((self._row(card_id, upgrade) or {}).get("isEndTurnLost"))

    @cache  # noqa: B019
    def lost_on_play(self, card_id: str, upgrade: int) -> bool:
        """打出后除外（playMovePositionType = Lost：レッスン中1回、眠気…）。"""
        return (self._row(card_id, upgrade) or {}).get("playMovePositionType") == "ProduceCardMovePositionType_Lost"

    @cache  # noqa: B019
    def drink(self, drink_id: str) -> tuple:
        """喝下这瓶饮料挂上的附魔：((附魔 id, 次数, 回合), …)。"""
        row = self._get("ProduceDrink", drink_id) or {}
        out = []
        for eid in row.get("produceDrinkEffectIds") or ():
            de = self._get("ProduceDrinkEffect", eid) or {}
            e = self._get("ProduceExamEffect", de.get("produceExamEffectId")) if de.get("produceExamEffectId") else de
            if e and e.get("produceExamStatusEnchantId") and "Enchant" in str(e.get("effectType")):
                out.append((e["produceExamStatusEnchantId"], _n(e.get("effectCount")), _n(e.get("effectTurn"))))
        return tuple(out)

    def enchant_card(self, name: str | None) -> str | None:
        """状态栏持续效果的名字（来源卡名，可能带 +）→ 卡 id；查不到 None。"""
        if not name:
            return None
        if not hasattr(self, "_by_name"):
            self._by_name = {}
            try:
                for row in self.cat.rows("ProduceCard"):
                    self._by_name.setdefault(str(row.get("name") or "").rstrip("+"), row["id"])
            except Exception:  # noqa: BLE001
                pass
        return self._by_name.get(str(name).rstrip("+"))

    @cache  # noqa: B019
    def item(self, item_id: str) -> tuple:
        """P 道具带的附魔：((附魔 id, 次数, 回合), …)。"""
        row = self._get("ProduceItem", item_id) or {}
        out = []
        for eid in row.get("produceItemEffectIds") or ():
            e = self._get("ProduceItemEffect", eid)
            if (
                e
                and e.get("effectType") == "ProduceItemEffectType_ExamStatusEnchant"
                and e.get("produceExamStatusEnchantId")
            ):
                out.append((e["produceExamStatusEnchantId"], _n(e.get("effectCount")), _n(e.get("effectTurn"))))
        return tuple(out)

    def idol_items(self, idol: str | None) -> list[str]:
        row = self._get("IdolCard", idol) if idol else None
        it = (row or {}).get("beforeProduceItemId")
        return [it] if it else []
