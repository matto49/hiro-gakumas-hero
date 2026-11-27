"""事件选项页（EVENT_CHOICE：随机事件、支援事件、开局二选一等多选项按钮页）。

用户 11-14（去 KAA 设计稿 §8 第 2 条）：不按攻略写死规则，按这一局培育整体（参数 / 体力 / 路线计划 / 后面的考试）来选，
走 算法 → LLM → 兜底（LLM 路径缺省开）。这里是「算法」那一层：把每个选项的效果文字解析成分量，

  {"Vo", "Da", "Vi", "hp", "p", "card", "drink", "upgrade", "delete", "trouble"}

某个选项在所有分量上都不差于其他每个选项、且至少一项更好（严格占优）时规则直接选；否则交 LLM（deciders.event_choice），
粗估分（score）只用来排候选顺序和兜底。效果文字解析不出的选项记 unparsed，LLM 不可用时不拿它兜底（停机等人）。
"""

from __future__ import annotations

import re
import unicodedata

KEYS = ("Vo", "Da", "Vi", "hp", "p", "card", "drink", "upgrade", "delete", "trouble")
ATTR = {"ボーカル": "Vo", "ダンス": "Da", "ビジュアル": "Vi", "vo": "Vo", "da": "Da", "vi": "Vi"}

_NUM = r"([+\-]\s*\d+)"
_PARAM = re.compile(r"(ボーカル|ダンス|ビジュアル|Vo|Da|Vi)[^\d+\-｜|、/]{0,6}" + _NUM, re.I)
_HP = re.compile(r"体力[^\d+\-｜|、/]{0,4}" + _NUM)
_P = re.compile(r"(?:Pポイント|Ppt|P)[^\d+\-｜|、/]{0,3}" + _NUM)
_FLAGS = (("card", re.compile(r"スキルカード.{0,12}(獲得|入手)|カード.{0,6}獲得")),
          ("drink", re.compile(r"(Pドリンク|ドリンク).{0,8}(獲得|入手)")),
          ("upgrade", re.compile(r"強化")),
          ("delete", re.compile(r"削除")),
          ("trouble", re.compile(r"トラブル(?!カード以外)|眠気")))  # fmt: skip
_HP_RECOVER = re.compile(r"体力(回復|を回復)")  # 「体力回復」不带数字：按 +1 记方向


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace("−", "-").replace("＋", "+")


def parse(choice: dict) -> tuple[dict, bool]:
    """一个选项 → (分量, 是否解析完整)。读 text / effect 两段文字和结构化代价（cost_hp / cost_p / trouble）。
    文字里有内容但一个分量都没认出 → 不完整（unparsed）。effect_unread（点开看了、说明框没读到）→ 效果未知，一律不完整，
    不当成「没有效果」（CodeRabbit #196：两项都没读到时原来会直接选第一项）。"""
    v = dict.fromkeys(KEYS, 0)
    text = _norm(" ".join(str(choice.get(k) or "") for k in ("text", "effect")))
    hit = False
    for m in _PARAM.finditer(text):
        v[ATTR.get(m.group(1).lower(), ATTR.get(m.group(1), m.group(1)))] += int(m.group(2).replace(" ", ""))
        hit = True
    rest = _PARAM.sub(" ", text)
    for m in _HP.finditer(rest):
        v["hp"] += int(m.group(1).replace(" ", ""))
        hit = True
    if not _HP.search(rest) and _HP_RECOVER.search(rest):
        v["hp"] += 1
        hit = True
    rest = _HP.sub(" ", rest)
    for m in _P.finditer(rest):
        v["p"] += int(m.group(1).replace(" ", ""))
        hit = True
    for key, pat in _FLAGS:
        if pat.search(rest):
            v[key] += 1
            hit = True
    if choice.get("cost_hp"):
        v["hp"] -= abs(int(choice["cost_hp"]))
        hit = True
    if choice.get("cost_p"):
        v["p"] -= abs(int(choice["cost_p"]))
        hit = True
    if choice.get("trouble"):
        v["trouble"] = max(v["trouble"], 1)
        hit = True
    blank = not text.strip() and not hit
    return v, (hit or blank) and not choice.get("effect_unread")


def _better(a: dict, b: dict) -> tuple[bool, bool]:
    """(a 每一项都不差于 b, a 至少一项比 b 好)。trouble 越少越好。"""
    ge, gt = True, False
    for k in KEYS:
        x, y = (-a[k], -b[k]) if k == "trouble" else (a[k], b[k])
        if x < y:
            ge = False
        if x > y:
            gt = True
    return ge, gt


def dominant(vecs: list[dict]) -> int | None:
    """严格占优的选项下标（对其他每个选项都不差、且至少对一个更好）；没有则 None。"""
    for i, a in enumerate(vecs):
        rel = [_better(a, b) for j, b in enumerate(vecs) if j != i]
        if all(ge for ge, _ in rel) and any(gt for _, gt in rel):
            return i
    return None


def score(
    v: dict, *, stamina: int | None, max_stamina: int | None, growth: dict | None, main_attr: str | None
) -> float:
    """粗估分（参数点当量），只用来排候选顺序和兜底，不当依据：
    参数按成长率加权（主属性再 ×1.2）；体力低于一半时每点 8、否则 3（回满之后的部分不算）；P 每点 1.5；
    获得卡 / 饮料 / 强化 30 / 25 / 30，删卡 20，眠気 −40。"""
    s = 0.0
    for a in ("Vo", "Da", "Vi"):
        w = (growth or {}).get(a, 1000) / 1000 * (1.2 if a == main_attr else 1.0)
        s += v[a] * w
    hp = v["hp"]
    if stamina is not None and max_stamina:
        if hp > 0:
            hp = min(hp, max_stamina - stamina)
        per = 8 if stamina < max_stamina / 2 else 3
        if hp < 0 and stamina + hp < 0:
            return float("-inf")  # 体力不够付
    else:
        per = 5
    s += (
        hp * per
        + v["p"] * 1.5
        + v["card"] * 30
        + v["drink"] * 25
        + v["upgrade"] * 30
        + v["delete"] * 20
        - v["trouble"] * 40
    )
    return s


def describe(v: dict) -> str:
    names = {
        "Vo": "Vo",
        "Da": "Da",
        "Vi": "Vi",
        "hp": "体力",
        "p": "P",
        "card": "获得卡",
        "drink": "获得饮料",
        "upgrade": "强化",
        "delete": "删卡",
        "trouble": "眠気",
    }
    parts = [
        f"{names[k]}{v[k]:+d}" if k in ("Vo", "Da", "Vi", "hp", "p") else f"{names[k]}×{v[k]}" for k in KEYS if v[k]
    ]
    return "、".join(parts) or "无变化"
