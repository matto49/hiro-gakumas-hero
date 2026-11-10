# 来源：digital-hiro src/hiro/sim/env.py（develop 864e037）
"""考试引擎（gakumas_exam）对局 ↔ 决策层 BattleState / Decision 的转换，以及搜索用的克隆与推进。

状态转换刻意对齐实机：卡牌文本优先用 KAA 卡面（定制过的卡改用引擎效果渲染），记忆（持续效果剩余、道具次数）
由决策层自己的 ExamMemory 推算，不直接读引擎内部。hide 参数可以把实机读不到的字段置为未知。
"""

import json
from functools import lru_cache

import hiro.sim  # noqa: F401  引擎路径
from hiro.sim.engine import Exam

from hiro.brain.cards import CardView, DrinkView, card_view, parse_tags
from hiro.brain.rules import Decision
from hiro.brain.state import BattleState, ExamMemory, IdolProfile

LIVE_HIDDEN = frozenset({"motivation", "impression", "score"})  # 当前实机感知还读不到的字段


def clone(env):
    """搜索用克隆：运行时状态复制，主数据与卡牌定义共享，日志清空（gakumas_exam 的 FidelityBattle / NativeBattle.clone）。"""
    return env.clone()


def fast_apply(env, action: dict) -> None:
    """不做原子回滚与审计记录的推进；调用方保证 action 合法。"""
    env._apply(action)


# ---------- 卡牌文本 ----------


@lru_cache(maxsize=None)
def _kaa_cards() -> dict:
    import hiro  # noqa: F401  KAA 兼容层
    import hiro.vision  # noqa: F401  开源版：让 KAA 读 HIRO_KAA_GAME_DATA（不在当前目录 resources/ 时）
    from kaa.db.skill_card import SkillCard

    return {(c._id, c.upgrade_count or 0): c for c in SkillCard.all()}


@lru_cache(maxsize=None)
def _kaa_drinks() -> dict:
    import hiro  # noqa: F401
    import hiro.vision  # noqa: F401
    from kaa.db.drink import Drink

    return {d.id: d for d in Drink.all()}


def _pct(permil: int) -> str:
    return f"{permil / 10:g}%"


def render_effects(effects: list) -> str:
    """引擎效果 → 与卡面同口径的日文短句（用于定制过的卡，parse_tags 可识别）。"""
    out = []
    for e in effects:
        op, v = e["op"], e.get("value", 0)
        if op == "score":
            out.append(f"パラメータ+{v}")
        elif op == "shield":
            out.append(f"元気+{v}")
        elif op == "motivation":
            out.append(f"やる気+{v}")
        elif op == "impression":
            out.append(f"好印象+{v}")
        elif op == "draw":
            out.append("スキルカードを引く" if v == 1 else f"スキルカードを{v}枚引く")
        elif op == "next_turn_draw":
            out.append(f"次のターン、スキルカードを{v}枚引く")
        elif op == "extra_action":
            out.append(f"スキルカード使用数追加+{v}")
        elif op == "extra_turn":
            out.append(f"ターン追加+{v}")
        elif op == "recover":
            out.append(f"体力回復{v}")
        elif op == "hp_loss":
            out.append(f"体力消費{v}")
        elif op == "shield_score":
            s = f"元気の{_pct(e['permil'])}分パラメータ上昇"
            if e.get("consume_permil") == 1000:
                s += "させ、元気を0にする"
            elif e.get("consume_permil"):
                s += f"させ、元気を{_pct(e['consume_permil'])}消費"
            out.append(s)
        elif op == "resource_score":
            name = "やる気" if e["resource"] == "motivation" else "好印象"
            out.append(f"{name}の{_pct(e['permil'])}分パラメータ上昇")
        elif op == "shield_tick":
            out.append(f"以降の{e['ticks']}ターンの間、ターン開始時、元気の{_pct(e['permil'])}分パラメータ上昇")
        elif op == "delayed_shield_score":
            out.append(f"{e['delay']}ターン後、元気の{_pct(e['permil'])}分パラメータ上昇")
        elif op == "conditional_tick":
            when = "レッスン終了まで" if e["ticks"] == -1 else f"以降の{e['ticks']}ターンの間"
            cond = "、体力が50%以下の場合" if e["requires"].get("hp_permil_max") == 500 else ""
            out.append(f"{when}、ターン開始時{cond}、" + "・".join(render_effects(e["effects"]).split(" / ")))
        elif op == "mental_motivation":
            out.append(f"レッスン終了まで、メンタルスキルカード使用時、やる気+{v}")
        elif op == "end_turn_motivation":
            out.append(f"レッスン終了まで、ターン終了時、やる気+{v}")
        elif op == "permanent_score_boost":
            out.append(f"パラメータ上昇量増加{_pct(e['permil'])}")
        elif op in ("score_boost", "timed_score_boost"):
            out.append(f"パラメータ上昇量増加{_pct(e['permil'])}（{e['turns']}ターン）")
        elif op == "create_deck_random":
            out.append("眠気を山札のランダムな位置に生成")
        elif op == "remove_random":
            out.append("山札か捨札の眠気をランダムに1枚除外")
        elif op == "play_interval":
            out.append(f"スキルカードを{e['interval']}枚使用するごとに、" + render_effects(e["effects"]))
        elif op == "conditional":
            out.append("条件を満たす場合、" + render_effects(e["effects"]))
        else:
            out.append(op)
    return " / ".join(out)


def sim_card_view(slot: int, definition: dict, *, available: bool) -> CardView:
    src = definition.get("provenance") or {}
    card_id, upgrade = src.get("card_id"), src.get("upgrade", 0)
    customized = bool((src.get("customization") or {}).get("customizations"))
    kaa = _kaa_cards().get((card_id, upgrade))
    if kaa is not None and not customized:
        v = card_view(slot, kaa, available=available)
    else:
        name = definition["name"] + ("（定制）" if customized else "")
        text = render_effects(definition["effects"]) + (" / レッスン中1回" if definition.get("once") else "")
        cost = definition.get("cost", {})
        v = CardView(slot, name, card_id, upgrade, available, definition.get("category", "unknown"),
                     cost.get("shieldable", 0), cost.get("hp", 0), None, text,
                     parse_tags(definition["name"], text, once=definition.get("once", False)))  # fmt: skip
    if definition.get("requires"):
        req = ", ".join(f"{k}≥{x}" for k, x in definition["requires"].items())
        v.text += f"（使用条件：{req}）"
    return v


def sim_drink_view(slot: int, definition: dict) -> DrinkView:
    src = definition.get("provenance") or {}
    did = src.get("drink_id")
    from hiro.brain.cards import drink_view

    kaa = _kaa_drinks().get(did)
    if kaa is not None:
        return drink_view(slot, kaa)
    return DrinkView(slot, definition["name"], did, render_effects(definition["effects"]))


# ---------- 对局 ----------


class SimGame:
    """一局离线考试。持有引擎对局与决策层的记忆；按槽位把决策映射回引擎动作。"""

    def __init__(self, env, exam_label: str, profile: IdolProfile | None, *, hide=frozenset(), public_piles=True):
        self.env = env
        self.exam = exam_label
        self.profile = profile
        self.hide = set(hide)
        self.public_piles = public_piles
        self.memory: ExamMemory | None = None

    @property
    def ended(self) -> bool:
        return self.env.ended

    @property
    def score(self) -> int:
        return self.env.score

    def _legal(self):
        return self.env.legal_actions()

    def state(self) -> BattleState:
        env = self.env
        assert self.memory is not None
        remaining = len(env.turns) - env.turn
        self.memory.observe_turn(remaining)
        legal_plays = {a["id"] for a in self._legal() if a["kind"] == "play"}
        legal_drinks = {a["id"] for a in self._legal() if a["kind"] == "drink"}
        self._hand = list(env.zones["hand"])
        self._drinks = [d for d in env.drinks if d in legal_drinks]
        hand = [sim_card_view(i, env.definition(c), available=c in legal_plays) for i, c in enumerate(self._hand)]
        drinks = [sim_drink_view(i, env.drink_definitions[env.drinks[d]]) for i, d in enumerate(self._drinks)]
        observed = dict(
            remaining_turns=remaining,
            hp=env.hp,
            genki=env.shield,
            motivation=env.motivation,
            impression=env.impression,
            score=env.score,
        )
        for k in self.hide:
            observed[k] = None
        info = piles = None
        if self.public_piles:
            deck = sorted(env.definition(c)["name"] for c in env.zones["deck"])
            grave = sorted(env.definition(c)["name"] for c in env.zones["grave"])
            lesson = env.turns[env.turn].get("lesson") if env.turn < len(env.turns) else None
            draw = env.turns[env.turn + 1]["draw"] if env.turn + 1 < len(env.turns) else 0
            info = f"本回合属性 {lesson}；山札 {len(deck)} 张（无序）：{'、'.join(deck)}；弃牌 {len(grave)} 张：{'、'.join(grave)}；下回合正常抽 {draw} 张"
            once = sorted({env.definition(c)["name"] for z in ("deck", "grave") for c in env.zones[z] if env.definition(c).get("once")})
            piles = {"deck": deck, "grave": grave, "once": once,
                     "draw": draw or (env.turns[env.turn]["draw"] if env.turn < len(env.turns) else 3)}  # fmt: skip
        perm = dict(now_turn_permil=env.turns[env.turn].get("bonus_permil") if env.turn < len(env.turns) else None,
                    final_turn_permil=env.turns[-1].get("bonus_permil") if env.turns else None)  # fmt: skip
        return BattleState.build(self.memory, self.profile, hand=hand, drinks=drinks, public_info=info, piles=piles, **perm, **observed)

    def to_action(self, d: Decision) -> dict:
        if d.kind == "play":
            return dict(kind="play", id=self._hand[d.slot])
        if d.kind == "drink":
            return dict(kind="drink", id=self._drinks[d.slot])
        return dict(kind="end_turn")

    def step(self, action: dict) -> None:
        fast_apply(self.env, action)


class SimExam:
    """一份开考配置（实机快照）上的对局工厂。"""

    def __init__(self, fixture: str, exam_label: str, profile: IdolProfile | None, **kw):
        from hiro.sim import FIXTURES

        path = FIXTURES / f"{fixture}.json"
        self.observation = json.loads(path.read_text())
        self.exam = Exam(self.observation, **kw)
        self.label = exam_label
        self.profile = profile

    def episode(self, seed: int, **kw) -> SimGame:
        """从开考快照按 seed 重新开局。"""
        return SimGame(self.exam.episode(seed), self.label, self.profile, **kw)

    def restore(self, snapshot: str, seed: int, **kw) -> SimGame:
        from hiro.sim import FIXTURES

        obs = json.loads((FIXTURES / f"{snapshot}.json").read_text())
        return SimGame(self.exam.restore(obs, seed), self.label, self.profile, **kw)
