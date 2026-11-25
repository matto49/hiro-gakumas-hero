"""回忆签名（资料库扫描设计 5.2）：增量补扫时认出「资料库里已经有的回忆」。

签名 = 技能卡编号 + 强化等级 + 四项属性 + 所有能力里出现的数字（多重集）。
- 只用前三项时，11-26 那次扫描的 405 份里有 38 组撞车（同卡、同强化、同属性），所以要加能力。
- 能力文字的 OCR 噪声大：属性词、关键词本身会读坏（ポーカル / バーカル / ごジュアル、確率→唯率、集中→果甲、好調→対調、
  ダンス→ブンス），图标读成杂字，词序被打乱，两条能力有时被读成一行。两次扫描同一格的能力文字规整后只有 193 / 405 格一致；
  按关键词集合比也只有 285 / 405。数字基本都读对，所以只取数字，并且把所有能力的数字合成一个多重集（不管怎么切行）：
  11-25 / 11-26 两次扫描（各自扣掉编成上下文的常数偏移后）398 / 405 格签名一致，一次扫描内部没有撞车。
  剩下 7 格是多读 / 少读一个数字（图标「②」读成 2 之类）或强化等级读错：认「已有」时允许数字差 1 个（KnownSet）。
- 代价：只差属性词、数字一样的两条能力（「初期ビジュアル上昇+15」和「初期ボーカル上昇+15」）分不出来；同一份回忆靠卡、强化、属性区分，
  11-26 的 405 份里没有因此撞车的。
- 缩略图指纹跨次扫描不稳定（两次只有 1 / 405 格一致），不用。
属性要是回忆本身的（从全空的回忆编成进选择页读，见设计 5.3）；不同编成上下文读到的属性不能互相比。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

_NUM = re.compile(r"\d+(?:\.\d+)?")
_STATS = ("Vo", "Da", "Vi", "hp")


def _text(ability) -> str:
    return ability.get("text") or "" if isinstance(ability, dict) else str(ability or "")


def ability_numbers(abilities) -> list[str]:
    """所有能力文字里的数字，排好序（多重集）。"""
    return sorted(
        (n for a in abilities or [] for n in _NUM.findall(unicodedata.normalize("NFKC", _text(a)))),
        key=lambda n: (float(n), n),
    )


def _head(entry: dict) -> list:
    card = entry.get("card") or {}
    stats = entry.get("stats") or {}
    return [card.get("card_id") or entry.get("card_text"), card.get("upgrade"), [stats.get(k) for k in _STATS]]


def signature(entry: dict) -> str:
    """一份回忆的签名（精确）：卡（读不出时用卡名原文）、强化、属性、能力数字。"""
    return json.dumps([*_head(entry), ability_numbers(entry.get("abilities"))], ensure_ascii=False)


class KnownSet:
    """资料库里已有的回忆。take(entry)：在还没被认领的已有回忆里找同一份，找到就认领（同样的回忆有几份就能认几次）。
    同一份 = 卡、强化、属性完全一样，能力数字的多重集最多差 1 个（OCR 多读 / 少读一个数字）。精确一样的优先。"""

    def __init__(self, entries):
        self._left: dict[str, list[Counter]] = {}
        for e in entries:
            self._left.setdefault(json.dumps(_head(e), ensure_ascii=False), []).append(
                Counter(ability_numbers(e.get("abilities")))
            )

    def take(self, entry: dict) -> bool:
        return self._take(
            json.dumps(_head(entry), ensure_ascii=False), Counter(ability_numbers(entry.get("abilities")))
        )

    def take_signature(self, sig: str) -> bool:
        """同 take，按 signature() 的结果认（编成方案里只存了签名）。"""
        try:
            *head, nums = json.loads(sig)
        except (TypeError, ValueError):
            return False
        return self._take(json.dumps(head, ensure_ascii=False), Counter(nums))

    def _take(self, head: str, nums: Counter) -> bool:
        pool = self._left.get(head)
        if not pool:
            return False
        best = min(range(len(pool)), key=lambda i: _diff(pool[i], nums))
        if _diff(pool[best], nums) > 1:
            return False
        pool.pop(best)
        return True


def _diff(a: Counter, b: Counter) -> int:
    return sum(((a - b) + (b - a)).values())
