"""考试局面 → UIObs v1（gakumas-bench 的公开观测，也是 GKMSUINet 的输入）。

格式只照公开 schema（GKMSUINet gkms_uinet/data/uiobs.v1.schema.json）：
  exam   produce / step / plan / idol / turn / limit / types（每回合属性 1 Vo 2 Da 3 Vi）/ permil（三属性倍率）/ crit（審査基準）
  num    score / stamina / max_stamina / block（元気）/ playable（本回合还能出几张）/ plays（本回合已出几张）
  stance [指針, 段数]；status [种类, 数值, 剩余回合]；ench [附魔 id, 剩余次数, 剩余回合]（-1 = 无限）
  hand（画面顺序，带 p 能否打出）/ deck / grave / hold / lost（无序）/ played（本回合打出的，按顺序）/ drinks / legal

两种来源：
- from_engine：考试引擎的局面（gakumas_exam 的导出格式，Python / Rust 后端相同；模拟器跟踪时有）——信息全。
- from_screen：只有画面读数（hiro BattleState）+ 培育牌组记账——见该函数说明里缺什么。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SCHEMA = "uiobs.v1"
INF = -1
STANCES = {1: "Concentration", 2: "Preservation", 3: "FullPower", 4: "OverPreservation"}  # ExamIdolStatusType
ATTR_TYPE = {"Vo": 1, "Da": 2, "Vi": 3}
STEPS = ("AUDITION_MID1", "AUDITION_MID2", "AUDITION_FINAL")

# 引擎状态类 → UIObs 状态种类。引擎按游戏的合并方式存：
#   单值（やる気、集中…）数量在 value；单回合（好印象、好調、各种限时状态）数量 / 剩余回合在 turn；
#   按回合分层（倍率、获得量增加…）每层一个实例，数值在 value、剩余回合在 turn。
VALUE_KIND = {  # 数量在 value，不限回合
    "LessonBuffStatusEffect": "lesson_buff",
    "AggressiveStatusEffect": "aggressive",
    "FullPowerPointStatusEffect": "full_power_point",
    "EnthusiasticStatusEffect": "enthusiastic",
    "AntiDebuffStatusEffect": "anti_debuff",
    "StaminaConsumptionDownFixStatusEffect": "stamina_consumption_down_fix",
    "BlockAddDownFixStatusEffect": "block_add_down_fix",
    "StaminaRecoverAddStatusEffect": "stamina_recover_add",
}
TURN_AMOUNT_KIND = {  # 数量就是 turn（好印象 n = 剩 n）
    "ReviewStatusEffect": "review",
}
FLAG_KIND = {  # 有没有 + 剩余回合：[种类, 1, 剩余回合]
    "ParameterBuffMultiplePerTurnStatusEffect": "parameter_buff_multiple",
    "StanceLockStatusEffect": "stance_lock",
    "StanceLockConcentrationEffect": "stance_lock",
    "StanceLockFullPowerEffect": "stance_lock",
    "StanceLockPreservationEffect": "stance_lock",
    "StaminaConsumptionDownStatusEffect": "stamina_consumption_down",
    "StaminaConsumptionAddStatusEffect": "stamina_consumption_add",
    "BlockRestrictionStatusEffect": "block_restriction",
    "BlockAddDownStatusEffect": "block_add_down",
    "StaminaRecoverRestrictionStatusEffect": "stamina_recover_restriction",
    "BuffConsumptionAddStatusEffect": "buff_consumption_add",
    "SlumpStatusEffect": "slump",
    "ParameterDebuffStatusEffect": "gimmick_parameter_debuff",
    "PanicStatusEffect": "panic",
    "LessonParameterMultipleDependReviewOrAggressiveStatusEffect": "lesson_parameter_depend",
}
LAYER_KIND = {  # 每层 [种类, 数值, 剩余回合]
    "LessonParameterMultipleStatusEffect": "lesson_parameter_multiple",
    "LessonParameterMultipleDownStatusEffect": "lesson_parameter_multiple_down",
    "LessonBuffMultipleStatusEffect": "lesson_buff_multiple",
    "ReviewMultipleStatusEffect": "review_multiple",
    "ReviewCountAddStatusEffect": "review_count_add",
    "FullPowerLessonMultipleAdditiveStatusEffect": "full_power_lesson_multiple_add",
    "ConcentrationLessonMultipleAdditiveStatusEffect": "concentration_lesson_multiple_add",
    "FullPowerPointAdditiveStatusEffect": "full_power_point_add",
    "EnthusiasticAdditiveStatusEffect": "enthusiastic_add",
    "StaminaConsumptionAddFixStatusEffect": "stamina_consumption_add_fix",
    "LessonDebuffStatusEffect": "lesson_debuff",
    "LessonBuffAdditiveStatusEffect": "lesson_buff_gain_multiple",
    "ParameterBuffAdditiveStatusEffect": "parameter_buff_gain_multiple",
    "AggressiveAdditiveStatusEffect": "aggressive_gain_multiple",
    "ReviewAdditiveStatusEffect": "review_gain_multiple",
    "LessonBuffAdditiveFixStatusEffect": "lesson_buff_gain_fix",
    "AggressiveAdditiveFixStatusEffect": "aggressive_gain_fix",
    "ParameterBuffAdditiveFixStatusEffect": "parameter_buff_gain_fix",
    "EnthusiasticMultipleStatusEffect": "enthusiastic_gain_permil",
    "ReviewAdditiveFixStatusEffect": "timed_gain",
    "FullPowerPointAdditiveFixStatusEffect": "timed_gain",
    "GrowEffectLessonAddAdditiveStatusEffect": "timed_gain",
}
# 数值按千分比存、UIObs 写小数的种类（倍率类）。照 GKMS UIObs 的实际输出核过（bench 逐步记录，2026-11-14）：
# lesson_buff_multiple 0.05 / 0.25、lesson_parameter_multiple 0.1 / 0.3 这类是小数；enthusiastic_add（熱意獲得量増加）是绝对量
# （GKMS 6 / 16 / 22…，主数据 effectValue1 = 1 / 2 / 3），不能除；enthusiastic_gain_permil 原样千分比（100 / 1000 / 3000）。
# full_power_point_add：主数据是千分比（250 / 400 / 500），这里写成小数；GKMS 记录里只见过 0.0（像是读错了槽），先不跟。
PERMIL_KINDS = {
    "lesson_parameter_multiple",
    "lesson_parameter_multiple_down",
    "lesson_buff_multiple",
    "review_multiple",
    "full_power_lesson_multiple_add",
    "concentration_lesson_multiple_add",
    "lesson_buff_gain_multiple",
    "parameter_buff_gain_multiple",
    "aggressive_gain_multiple",
    "review_gain_multiple",
    "full_power_point_add",
}


def _turns(s: dict) -> int:
    """画面上的剩余回合。引擎的 turn 在没有 passing 标记时比画面少 1（和 GKMS 对照台逐步核对得出：
    passing 为真时两边相同，为假时画面 = turn + 1）。"""
    if not s.get("limited"):
        return INF
    return int(s.get("turn", -1)) + (0 if s.get("passing") else 1)


def _value(kind: str, v: Any) -> float:
    v = float(v or 0)
    return v / 1000.0 if kind in PERMIL_KINDS else v


def statuses(state: dict) -> list:
    """引擎导出的 status.effects → UIObs status。"""
    out = []
    for s in (state.get("status") or {}).get("effects") or ():
        cls = s.get("cls")
        if cls in VALUE_KIND:
            v = s.get("value") or (s.get("extra") or {}).get("_count") or 0
            if v:
                out.append([VALUE_KIND[cls], float(v), INF])
        elif cls in TURN_AMOUNT_KIND:
            if int(s.get("turn") or 0) > 0:
                out.append([TURN_AMOUNT_KIND[cls], float(s["turn"]), INF])
        elif cls == "ParameterBuffStatusEffect":
            t = int(s.get("turn") or 0)
            if t > 0:
                out.append(["parameter_buff", float(t), t])
        elif cls in FLAG_KIND:
            t = _turns(s)
            if t == INF or t > 0:
                out.append([FLAG_KIND[cls], 1.0, t])
        elif cls in LAYER_KIND:
            t = _turns(s)
            if t == INF or t > 0:
                kind = LAYER_KIND[cls]
                out.append([kind, _value(kind, s.get("value")), t])
        elif cls == "StartTurnCardDrawDownStatusEffect":
            if s.get("value"):
                out.append(["card_draw_down", float(s["value"]), 1])
        elif cls == "SearchPlayCardLimitStatusEffect":
            out.append(["play_card_limit", 1.0, _turns(s)])
    if int(state.get("extra_turn") or 0):
        out.append(["extra_turn", float(state["extra_turn"]), INF])
    return out


def _extra_actions(st: dict) -> int:
    """追加行动（PlayableValueAddStatusEffect）：画面上的「还能出几张」包含它。"""
    return sum(
        int(s.get("value") or 0) for s in st.get("effects") or () if s.get("cls") == "PlayableValueAddStatusEffect"
    )


def enchants(state: dict) -> list:
    out = []
    for s in (state.get("status") or {}).get("effects") or ():
        trig = s.get("trig")
        if not trig:
            continue
        left = int(trig.get("limit_count", -1))
        if left == 0:
            continue  # 次数用完：图标消失
        out.append(
            [
                trig.get("enchant_id") or (trig.get("trigger") or {}).get("id") or "",
                INF if left < 0 else left,
                _turns(s),
            ]
        )
    return out


def card_spec(c: dict, customize_ids=None, playable: bool | None = None) -> dict:
    """引擎导出的卡 → UIObs 卡 {id, u, c?, g?, p?}。customize_ids(card_id, upgrade) 给主数据的可定制列表。"""
    u = int(c.get("base_upgrade") or 0) + int(c.get("tmp_upgrade") or 0) + len(c.get("support_upgrades") or ())
    d: dict = {"id": c["card_id"], "u": u}
    if c.get("custom_override"):
        d["c"] = [[i, int(n)] for i, n in c["custom_override"]]
    elif any(c.get("customize_counts") or ()):
        ids = customize_ids(c["card_id"], u) if customize_ids else []
        cz = [[i, int(n)] for i, n in zip(ids, c["customize_counts"]) if n]
        if cz:
            d["c"] = cz
    g = [x.get("id") for x in c.get("grow_after") or () if x.get("id")]
    if g:
        d["g"] = g
    if playable is not None:
        d["p"] = int(bool(playable))
    return d


def _sort_key(c: dict):
    return (c["id"], c["u"], str(c.get("c")), str(c.get("g")))


def legal_list(legal: Iterable) -> list:
    """引擎 / hiro 的合法动作 → UIObs legal：["play", 手牌下标] / ["drink", 饮料下标] / ["end"]。"""
    out = []
    for a in legal:
        a = tuple(a)
        if a[0] in ("play", "drink"):
            out.append([a[0], int(a[1])])
        elif a[0] in ("end", "skip", "end_turn"):
            out.append(["end"])
    return out


def from_engine(state: dict, exam: dict, legal: Iterable, *, customize_ids=None, played: Iterable[dict] = ()) -> dict:
    """引擎局面（gakumas_exam.native.export_state / Rust export_json 的格式）→ UIObs。

    exam：这一轮固定的部分 {produce, step, plan, idol, crit}；turn / limit / types / permil 从局面取。
    legal：引擎的合法动作（("play", i) / ("drink", i) / ("end",)）。
    """
    cards = state["cards"]
    zones = state["zones"]
    legal = legal_list(legal)
    playable = {a[1] for a in legal if a[0] == "play"}

    def zone(name):
        return sorted((card_spec(cards[i], customize_ids) for i in zones.get(name) or ()), key=_sort_key)

    permil = {int(k): int(v) for k, v in state.get("bonus_permil") or ()}
    st = state.get("status") or {}
    stance = STANCES.get(int(st.get("idol_type") or 0))
    turn = int(state.get("current_turn") or 1)
    plays = int(state.get("turn_card_play_count") or 0)
    log = state.get("play_log") or []
    return {
        "v": SCHEMA,
        "exam": {
            **{k: exam.get(k) for k in ("produce", "step", "plan", "idol")},
            "turn": turn,
            "limit": int(state.get("limit_turn") or 0) + int(state.get("extra_turn") or 0),
            "types": [int(t) for t in state.get("turn_types") or ()],
            "permil": [permil.get(k, 0) for k in (1, 2, 3)],
            "crit": list(exam.get("crit") or [0, 0, 0]),
        },
        "num": {
            "score": int(state.get("score") or 0),
            "stamina": int(state.get("stamina") or 0),
            "max_stamina": int(state.get("max_stamina") or 0),
            "block": int(state.get("block") or 0),
            "playable": int(state.get("remaining_actions") or 0) + _extra_actions(st),
            "plays": plays,
        },
        "stance": [stance, int(st.get("idol_step") or 0) if stance else 0],
        "status": statuses(state),
        "ench": enchants(state),
        "hand": [card_spec(cards[i], customize_ids, k in playable) for k, i in enumerate(zones.get("hand") or ())],
        "deck": zone("deck"),
        "grave": zone("grave"),
        "hold": zone("hold"),
        "lost": zone("lost"),
        "played": list(played)
        if played
        else [card_spec(cards[i], customize_ids) for i in (log[-plays:] if plays else [])],
        "drinks": list(state.get("drinks") or ()),
        "legal": legal,
    }
