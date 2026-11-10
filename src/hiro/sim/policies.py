# 来源：digital-hiro src/hiro/sim/policies.py（develop 864e037）
"""离线对局用的策略：决策层（规则/LLM）、快速启发式、确定化推演专家。

专家的每个「世界」只重排玩家看不到的信息：山札顺序、尚未公开的前段回合属性。牌堆组成、弃牌、
已安装效果都按公开状态保留，不读取真实牌序或种子。
"""

import random
import statistics
from dataclasses import dataclass, field

from hiro.brain.battle import BattleBrain
from hiro.brain.rules import Decision
from hiro.sim.env import SimGame, clone, fast_apply

ATTR_LESSON = {"Vo": "vocal", "Da": "dance", "Vi": "visual"}


# ---------- 决策层 ----------


class BrainPolicy:
    """实机同款决策层。每局开始时新建记忆。"""

    name = "brain"

    def __init__(self, brain: BattleBrain):
        self.brain = brain
        if brain.llm is not None:
            self.name = "brain-llm"
        else:
            self.name = "brain-rules"

    def start(self, game: SimGame) -> None:
        game.memory = self.brain.new_memory(game.exam)

    def act(self, game: SimGame) -> tuple[dict, Decision, object]:
        state = game.state()
        d = self.brain.decide(state, game.memory)
        action = game.to_action(d)
        return action, d, state

    def after(self, game: SimGame, state, d: Decision) -> None:
        self.brain.commit(state, game.memory, d)


class PreviewBrainPolicy(BrainPolicy):
    """决策层 + 动作预演：每个决策点给出全部合法动作的即时结果与期望终局分（hiro.sim.preview）。"""

    def __init__(self, brain: BattleBrain, meta: dict | None, worlds: int = 16):
        super().__init__(brain)
        self.meta = meta
        self.worlds = worlds
        self.name = self.name + "-preview"

    def act(self, game: SimGame):
        from hiro.sim.preview import preview_actions, prompt_block, slot_previews

        state = game.state()
        previews = preview_actions(game.env, self.meta, worlds=self.worlds, seed=game.env.turn * 1000 + game.env.play_count)
        state.action_previews = prompt_block(slot_previews(previews, game))
        game.memory.plan = []  # 每步都有新的预演，不沿用旧计划
        d = self.brain.decide(state, game.memory)
        return game.to_action(d), d, state


class ExamPolicyPolicy(BrainPolicy):
    """实机 --exam-policy 的同一条选择逻辑（hiro.produce.exam_policy.decide_with_policy）+ 实机同款预演参数（16 世界、每步 deadline），
    用来做专家模式的离线验收（reports/analysis/expert-preview-pilot.md §5）。step_seconds 记每步决策耗时（含预演）。"""

    def __init__(self, brain: BattleBrain, meta: dict | None, policy: str = "expert", worlds: int = 16, budget_s: float | None = None):
        super().__init__(brain)
        from hiro.produce.exam_policy import PREVIEW_BUDGET_S

        self.meta, self.policy, self.worlds = meta, policy, worlds
        self.budget_s = PREVIEW_BUDGET_S if budget_s is None else budget_s
        self.name = f"exam-{policy}"
        self.step_seconds: list[float] = []

    def act(self, game: SimGame):
        import time

        from hiro.produce.exam_policy import decide_with_policy
        from hiro.sim.preview import preview_actions, prompt_block, slot_previews

        t0 = time.monotonic()
        state = game.state()
        previews = preview_actions(game.env, self.meta, worlds=self.worlds, seed=game.env.turn * 1000 + game.env.play_count,
                                   deadline=t0 + self.budget_s if self.budget_s else None)  # fmt: skip
        state.action_previews = prompt_block(slot_previews(previews, game))
        d, _ = decide_with_policy(self.policy, state, game.memory, self.brain)
        self.step_seconds.append(time.monotonic() - t0)
        return game.to_action(d), d, state


class PreviewExpert:
    """直接选动作预演里期望终局分最高的动作（本回合内穷举 + 回合后推演）。"""

    name = "expert-preview"

    def __init__(self, meta: dict | None, worlds: int = 16, depth: int = 3):
        self.meta, self.worlds, self.depth = meta, worlds, depth

    def start(self, game):
        pass

    def act(self, game):
        from hiro.sim.preview import preview_actions

        best = preview_actions(game.env, self.meta, worlds=self.worlds, seed=game.env.turn * 1000 + game.env.play_count, depth=self.depth)[0]
        return best.action, None, None

    def after(self, game, state, d):
        pass


# ---------- 快速启发式（推演基础策略，直接读引擎） ----------


def _clears_genki(defn) -> bool:
    return any(e["op"] == "shield_score" and e.get("consume_permil") == 1000 for e in defn["effects"])


def _pending_sustained(env) -> bool:
    """还会按元气结算的 N 回合持续效果（不含整场的条件附魔）。"""
    return any(t.get("kind") is None and t["remaining"] > 0 for t in env.ticks)


def _sustained(defn) -> bool:
    return any(e["op"] in ("shield_tick", "delayed_shield_score") for e in defn["effects"])


def _item_ready(env, card_id: str) -> bool:
    return any(h["phase"] == "card_play" and card_id in h["card_ids"] and (not h["limit"] or h["uses"] < h["limit"])
               and env.hp * 1000 >= h["requires"].get("hp_permil_min", 0) * env.max_hp for h in env.hooks)  # fmt: skip


def _value(env, defn) -> float:
    s = 0.0
    for e in defn["effects"]:
        op = e["op"]
        if op == "extra_action":
            s += 30
        elif op in ("draw", "next_turn_draw"):
            s += 8 * e.get("value", 1)
        elif op in ("shield_tick", "delayed_shield_score"):
            s += 10 + env.shield / 10
        elif op == "conditional_tick":
            s += 35
        elif op == "extra_turn":
            s += 40
        elif op in ("motivation", "mental_motivation"):
            s += 3 * e.get("value", 1)
        elif op == "shield":
            s += 1 + (e.get("value", 0) + env.motivation * e.get("motivation_scale", 0)) * 0.05
        elif op == "permanent_score_boost":
            s += 20  # 2026-10-30 试过按剩余回合加权（+20% 剩 10 回合约 +87），专家 R1/R2 均分反而降 8–9 万，回滚
    src = defn.get("provenance") or {}
    if _item_ready(env, src.get("card_id", "")):
        s += 40  # 高血时触发固有道具：追加行动 + 干劲
    return s


def heuristic_action(env, rng: random.Random) -> dict:
    legal = env.legal_actions()
    plays = [a for a in legal if a["kind"] == "play"]
    drinks = [a for a in legal if a["kind"] == "drink"]
    remaining = len(env.turns) - env.turn
    last = remaining <= 1
    real = [a for a in plays if not env.definition(a["id"]).get("trouble")]
    # 复制饮料：手里有可打的持续核心时先喝，接着打它
    for d in drinks:
        effects = env.drink_definitions[env.drinks[d["id"]]]["effects"]
        if any(e["op"] == "repeat_card" for e in effects):
            if env.actions >= 1 and any(_sustained(env.definition(a["id"])) for a in real):
                return d
    others = [d for d in drinks if not any(e["op"] == "repeat_card" for e in env.drink_definitions[env.drinks[d["id"]]]["effects"])]
    if others and (env.turn <= 2 or last):
        return rng.choice(others)
    if real:
        safe = [a for a in real if not _clears_genki(env.definition(a["id"]))]
        if last:
            pool = safe or real  # 末回合：先打别的，终结最后
        elif safe:
            pool = safe
        elif not _pending_sustained(env) and remaining <= 2 and rng.random() < 0.5:
            pool = real
        else:
            pool = []
        if pool:
            return max(pool, key=lambda a: _value(env, env.definition(a["id"])) + rng.random() * 5)
    trouble = [a for a in plays if env.definition(a["id"]).get("trouble")]
    if trouble and rng.random() < 0.5:
        return trouble[0]  # 打出眠気：消耗行动、除外，压缩牌堆
    return dict(kind="end_turn")


# ---------- 社区打法续打（预演优化 P1，reports/analysis/preview-optimization-plan.md §2）----------


def _drink_ops(env, d) -> set:
    return {e["op"] for e in env.drink_definitions[env.drinks[d["id"]]]["effects"]}


def _ops(defn) -> dict:
    return {e["op"]: e for e in defn["effects"]}


def _mults(env) -> list:
    return [t.get("bonus_permil") or 0 for t in env.turns[env.turn:]]


def _cash_value(env, defn, mult: int) -> float:
    """这张牌现在打的即时兑现（千分比算到分）：元気 × 比例 × 本回合倍率 + やる気兑现。"""
    v = 0.0
    for e in defn["effects"]:
        if e["op"] == "shield_score":
            v += env.shield * e["permil"] / 1000 * mult / 1000
        elif e["op"] == "resource_score" and e.get("resource") == "motivation":
            v += env.motivation * e["permil"] / 1000 * mult / 1000
    return v


def _eureka_on(env) -> bool:
    """エウレカ（回合开始 HP ≤ 50% 时按元気得分）已装：引擎里是 kind=conditional 的持续效果。"""
    return any(t.get("kind") == "conditional" for t in env.ticks)


def community_value(env, a: dict) -> float:
    """一张可打的牌的价值（社区打法）：回合倍率、せのび 结算落点、デイドリ 装备时机、终结末回合、エウレカ 控血。"""
    defn = env.definition(a["id"])
    ops = _ops(defn)
    rem = len(env.turns) - env.turn
    mults = _mults(env)
    now, best = (mults[0] if mults else 0), max(mults or [0])
    if "delayed_shield_score" in ops:  # せのび：结算落在剩 1–2 回合（倍率高、元気多），太晚结算不到
        delay = ops["delayed_shield_score"].get("delay", 4)
        settle = rem - delay
        if settle < 1:
            return 0.0
        return 90.0 if settle <= 2 else (40.0 if settle == 3 else 10.0)
    if "shield_tick" in ops:  # デイドリ：触发次数 × 元気；元気低、离末段远时晚点装
        ticks = ops["shield_tick"].get("ticks", 3)
        n = min(ticks, max(0, rem - 1))
        if env.shield < 60 and rem > ticks + 2:
            return 6.0
        return 15.0 + n * env.shield * ops["shield_tick"]["permil"] / 1000 / 20
    if "shield_score" in ops:
        cash = _cash_value(env, defn, now)
        if ops["shield_score"].get("consume_permil", 0) >= 1000:  # 终结：末回合打
            last_mult = mults[-1] if mults else now
            # 剩 2 回合：留着回合末就进弃牌堆，最后一回合几乎抽不回来（第十一局 R2 214k 那次：剩 2 回合 届いて 在手没打，
            # 最后一回合没抽到，元気 843 浪费）。v2 起剩 2 回合就打；v1 只在本回合倍率不低于最后一回合时打
            if rem <= 1 or (rem == 2 and not _pending_sustained(env) and (FINISHER_AT_2 or now >= last_mult)):
                return 200.0 + cash / 1000
            if CYCLE_RULES and rem >= 2 and not _pending_sustained(env):
                # 牌库循环（第十八局选拔③ 剩 4：届いて 不打就进弃牌堆，山札 8 张抽完才重洗，最早最后一回合回来、概率约 1/9）：
                # 估回收概率 P，「P × 元気增长 × 之后最高倍率」不如现在的倍率就现在兑现
                p_back = finisher_return_prob(env)
                if p_back * FINISHER_GENKI_GROWTH * max(mults[1:] or [now]) < now:
                    return 200.0 + cash / 1000
            return -1.0
        # 不清空的兑现（紙吹雪 / 約束）：倍率接近剩余回合最高、或末段才打；否则留着让元気再涨
        if rem <= 3 or now >= best * 0.95:
            return 60.0 + cash / 1000
        return 4.0 + cash / 5000
    v = _value(env, defn)
    if _eureka_on(env) and env.hp * 2 > env.max_hp and (defn.get("cost") or {}).get("hp"):
        v += 15  # エウレカ：HP 压到半血以下才触发
    if CYCLE_RULES and "create_deck_random" in ops:
        v -= sleep_insert_cost(env)  # 成長痛 / 気まぐれハート：往山札随机位置塞眠気，按局面权衡（用户：可以抉择，不一刀切）
    return v


YARUKI_FIRST = True  # 续打回合内顺序：先叠 やる気 再打加元気的牌（用户 2026-11-03）


def _gives_genki(defn) -> bool:
    sh = _ops(defn).get("shield")
    return bool(sh and sh.get("motivation_scale"))


def _pure_yaruki(defn) -> bool:
    ops = _ops(defn)
    return "motivation" in ops and "shield" not in ops and "shield_score" not in ops


_STAT_OF: dict = {}


def _is_stat_drink(env, d) -> bool:
    did = env.drinks[d["id"]]
    if did not in _STAT_OF:
        from hiro.state.knowledge import STAT_DRINK_CLASSES, drink_class

        _STAT_OF[did] = drink_class(did) in STAT_DRINK_CLASSES
    return _STAT_OF[did]


# ---- 牌库循环（协调 2026-11-02 第十八局选拔③ 案例；用户：塞眠気按局面权衡）----
CYCLE_RULES = False  # 行为变化，默认关，验收后由总控 / 用户决定（2026-11-04）
FINISHER_GENKI_GROWTH = 1.3  # 终结牌留到后面时元気的大致增长（やる気 回合继续叠）
DRAWS_PER_TURN = 3
SLEEP_COST = 30.0  # 塞一张眠気顶掉一次关键抽牌的代价（community_value 刻度；好牌约 10–60）


def _key_card(defn) -> bool:
    ops = _ops(defn)
    return "shield_score" in ops or "delayed_shield_score" in ops


def finisher_return_prob(env) -> float:
    """手里这张终结牌这回合不打（回合末进弃牌堆）的话，最后一回合之前能再抽回来的概率（粗估）：
    山札 D 张、之后还能抽 3 ×（剩余回合 − 1）张；抽不完山札就不会重洗 → 0；否则重洗后剩 k 次抽牌，从「弃牌 + 手牌 + 山札」重洗出的牌里抽到它的概率 ≈ k / 牌数。"""
    rem = len(env.turns) - env.turn
    d = len(env.zones.get("deck", []))
    draws = DRAWS_PER_TURN * max(0, rem - 1)
    k = draws - d
    if k <= 0:
        return 0.0
    pool = len(env.zones.get("grave", [])) + len(env.zones.get("hand", [])) + d
    return min(1.0, k / max(1, pool))


def sleep_insert_cost(env) -> float:
    """往山札随机位置塞一张眠気的代价：关键牌（终结 / 兑现）还在山札或弃牌堆里要抽时，眠気插在之后会被抽到的位置就顶掉一次抽牌。
    插到会被抽到的位置的概率 ≈ min(1, 剩余抽牌数 /（山札张数 + 1）)；山札越小、离要抽关键牌越近，代价越大。关键牌都在手里 / 已打出时代价为 0。"""
    rem = len(env.turns) - env.turn
    waiting = any(_key_card(env.definition(i)) for z in ("deck", "grave") for i in env.zones.get(z, []))
    if not waiting:
        return 0.0
    d = len(env.zones.get("deck", []))
    draws = DRAWS_PER_TURN * max(0, rem - 1)
    return SLEEP_COST * min(1.0, draws / (d + 1))


# 续打里的饮料使用时机（协调 2026-11-02，用户：「不同饮料有不同的最佳使用时期」）。按 knowledge.drink_class 分类，规则来自
# tools/drinktime/curves.py 的时机曲线（每瓶单独持有、社区出牌、只在指定时机喝，64 局配对；选拔③ / R2 / R1 三个考题）+ 用户经验 + 社区：
#   爆发    剩 ≤3 喝（曲线峰在剩 3：+3.3 / +3.6 / +2.7 万；旧规则「配兑现」只有 +1.1 / +0.5 / +1.0 万）[用户：加分药留到最后几回合]
#   抽牌    剩 ≤6 喝，或卡手（没有好牌）且剩 ≤8（曲线剩 5–10 平台 +6.4 / +8.9 / +4.4 万；第 1 回合喝最差；旧规则第 1 回合喝 +2.1 / +4.4 / +2.0 万）
#           [用户：手头没有可打的牌时用——卡手时喝 +4.3 / +6.9 / +3.5 万，比中盘略低]
#   换手    卡手时喝
#   复制    剩 ≤6 且手里有值得复制的主动卡；剩 ≤2 有主动卡就喝（曲线峰剩 4–6：+3.2 / +4.5 / +3.5 万；旧 +1.3 / +1.0 / +2.7 万）
#   加行动  好牌比行动多且剩 ≤8（曲线峰剩 7–8，噪声大）
#   やる気 / 平加 / 纯好印象   开局就喝（曲线单调递减：ブレンド 第 1 回合 +1.6 / +1.7 / +1.1 万；旧规则从不喝）[用户：纯加属性的药开局用]
#   元気（烏龍茶 / ハーブティー）  剩 ≤8 喝（剩 9–2 平台 +1.3–1.5 万）
#   检索    剩 ≥8 喝（前期；噪声大）
#   强化手牌 剩 ≥10 或能兑现时
#   回复 / 生成卡 / 其他       末回合（旧规则；回复前中期喝为负：体力高 エウレカ 不触发）
DRINK_TIMING = "class"  # class：按类别时机（本表）；community：旧规则。96 局验收（2026-11-04，三个考题合并 +2.6 万 ± 0.9 万）后开，有用户三条经验依据（D10–D12）
_CLASS_OF: dict = {}


def _drink_cls(env, d) -> str:
    did = env.drinks[d["id"]]
    if did not in _CLASS_OF:
        from hiro.state.knowledge import drink_class

        k = drink_class(did)
        if k == "other":  # master 里查不到（合成开局的饮料 id 也是 master id，一般不会走到这里）：按效果粗分
            ops = {e["op"] for e in env.drink_definitions[did]["effects"]}
            k = "copy" if "repeat_card" in ops else "action" if "extra_action" in ops else "draw" if "draw" in ops else "burst" if "timed_score_boost" in ops else "other"
        _CLASS_OF[did] = k
    return _CLASS_OF[did]


def drink_timing_ok(env, d, rem: int, good: list, targets: list, cash_now: bool, real: list) -> bool:
    k = _drink_cls(env, d)
    acts = env.actions
    if k == "burst":
        return rem <= 3 and acts >= 1
    if k == "draw":
        return acts >= 1 and (rem <= 6 or (not good and rem <= 8))
    if k == "swap":
        return acts >= 1 and not good
    if k == "copy":
        actives = [a for a in real if env.definition(a["id"]).get("category") in ("active", "ProduceCardCategory_ActiveSkill") or "shield_score" in _ops(env.definition(a["id"]))]
        return acts >= 1 and ((rem <= 6 and bool(targets)) or (rem <= 2 and bool(actives or targets)))
    if k == "action":
        return acts <= len(good) - 1 and rem <= 8
    if k in ("yaruki", "param", "impression", "genki"):
        return True  # 纯加数值（やる気 / 平加 / 好印象 / 元気）开局就喝：用户 2026-11-03「这种数值越早叠，总是越正向的」（元気 原来按曲线平台剩 ≤8）
    if k == "fetch":
        return rem >= 8
    if k == "upgrade":
        return rem >= 10 or cash_now
    return rem <= 1


def community_action(env, rng: random.Random, allow_drinks: bool = True) -> dict:
    legal = env.legal_actions()
    plays = [a for a in legal if a["kind"] == "play"]
    drinks = [a for a in legal if a["kind"] == "drink"] if allow_drinks else []  # allow_drinks=False：时机曲线工具（tools/drinktime）自己决定何时喝
    rem = len(env.turns) - env.turn
    real = [a for a in plays if not env.definition(a["id"]).get("trouble")]
    vals = {a["id"]: community_value(env, a) for a in real}
    good = [a for a in real if vals[a["id"]] > 5]
    cash_now = any("shield_score" in _ops(env.definition(a["id"])) and vals[a["id"]] >= 60 for a in real)
    targets = [a for a in real if vals[a["id"]] >= 60 and ("delayed_shield_score" in _ops(env.definition(a["id"])) or "shield_tick" in _ops(env.definition(a["id"]))
                                                          or "shield_score" in _ops(env.definition(a["id"])))]  # fmt: skip
    if DRINK_TIMING == "class":
        for d in drinks:
            if drink_timing_ok(env, d, rem, good, targets, cash_now, real):
                return d
        drinks = []  # 按类别时机都不到：这一步不喝（下面的旧规则不再用）
    for d in drinks:
        ops = _drink_ops(env, d)
        if _is_stat_drink(env, d):  # 纯加数值（やる気 / 元気 / 好印象 / 平加）开局就喝（用户 2026-11-03：越早叠越正向）
            return d
        if "repeat_card" in ops:  # 复制：留给 せのび / デイドリ / 大额兑现
            if env.actions >= 1 and targets:
                return d
        elif "extra_action" in ops:  # 追加行动：好牌比行动多时（兑现回合）
            if env.actions <= len(good) - 1 and (cash_now or rem <= 3):
                return d
        elif "timed_score_boost" in ops and "draw" not in ops:  # 爆发：配大额兑现
            if cash_now and env.actions >= 1:
                return d
        elif "draw" in ops:  # 抽牌：前期压缩、末段连打
            if env.turn <= 1 or rem <= 2 or not good:
                return d
        elif rem <= 1:
            return d
    if rem <= 1 and drinks and not real:
        return drinks[0]
    if real:
        best = max(real, key=lambda a: vals[a["id"]] + rng.random() * 3)
        if vals[best["id"]] > 0:
            if YARUKI_FIRST and _gives_genki(env.definition(best["id"])):
                # 先叠 やる気 再打加元気的牌（用户 2026-11-03）：只叠 やる気 的牌不耗行动、或还有 2 次以上行动时先打它
                first = [a for a in real if a is not best and vals[a["id"]] > 0 and _pure_yaruki(env.definition(a["id"]))
                         and ("extra_action" in _ops(env.definition(a["id"])) or env.actions >= 2)]  # fmt: skip
                if first:
                    return max(first, key=lambda a: vals[a["id"]])
            return best
    trouble = [a for a in plays if env.definition(a["id"]).get("trouble")]
    if trouble and rng.random() < 0.5:
        return trouble[0]
    return dict(kind="end_turn")


FINISHER_AT_2 = False  # v1（剩 2 回合只在本回合倍率不低于最后一回合时打，贴近用户规则）；v2（剩 2 就打）bench 与 v1 无显著差
# 续打策略：一律社区打法（用户 2026-11-02：「旧启发式明显有问题，参考社区打法」——heuristic_action 前 2 回合 / 末回合随机喝饮料）。
# 不再读环境变量回退；离线对照工具要比 heuristic 时直接改这个属性（tools/annot/bench_q11.py 的 rollout 参数）
ROLLOUT = "community"


def rollout(env, rng: random.Random, max_steps: int = 400) -> int:
    act = community_action if ROLLOUT == "community" else heuristic_action
    for _ in range(max_steps):
        if env.ended:
            break
        fast_apply(env, act(env, rng))
    return env.score


class HeuristicPolicy:
    name = "heuristic"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def start(self, game):
        pass

    def act(self, game):
        return heuristic_action(game.env, self.rng), None, None

    def after(self, game, state, d):
        pass


# ---------- 确定化推演专家 ----------


def determinize(env, meta: dict | None, rng: random.Random):
    """复制对局并重排隐藏信息：山札顺序、未公开的前段回合属性。"""
    w = clone(env)
    # 世界自己的随机数也是未知信息：山札不够时弃牌堆洗回（ミックススムージー 的整手牌重抽、回合末弃牌后抽牌等）用的是对局 rng，
    # clone 出来的世界共享同一个状态，会让所有世界洗出同一个顺序（没有抽样，且和真实顺序无关）。每个世界换成自己的状态。
    w.determinize_hidden(rng)  # 漢方等检索后，山札顶部若干张是公开已知的顺序：引擎保留它，只重排其后的部分
    attrs = getattr(w, "turn_attributes", None)
    if meta and attrs:
        prefix = sum(meta["allocation"]["random_prefix_counts"].values())
        # 配额前段按整场考试的绝对回合数算；中途存档恢复出来的 env 从相对下标 0 开始（turn_offset = 存档回合数 - 1），
        # 末三回合的固定属性不能当隐藏前段重排。推演里打出追加回合会让 turns 比 attrs 长（追加回合沿用最后属性），
        # 所以下标以 attrs 为界。
        offset = getattr(w, "turn_offset", 0)
        hidden = [i for i in range(w.turn + 1, min(len(attrs), len(w.turns))) if offset + i < prefix]
        pool = [attrs[i] for i in hidden]
        rng.shuffle(pool)
        for i, a in zip(hidden, pool):
            w.turns[i] = dict(w.turns[i], bonus_permil=meta["bonus"][a], lesson=ATTR_LESSON[a])
    return w


def card_label(defn) -> str:
    """卡名；定制过的加「（定制）」，与决策层看到的名字一致，也避免同名不同定制的实例混淆。"""
    custom = (defn.get("provenance") or {}).get("customization") or {}
    return defn["name"] + ("（定制）" if custom.get("customizations") else "")


def _key(action: dict, env) -> str:
    if action["kind"] == "play":
        return "play:" + card_label(env.definition(action["id"]))
    if action["kind"] == "drink":
        return "drink:" + env.drink_definitions[env.drinks[action["id"]]]["name"]
    return "end_turn"


@dataclass
class ExpertResult:
    best: dict
    values: dict = field(default_factory=dict)  # 动作键 → 平均终局分
    spread: dict = field(default_factory=dict)  # 动作键 → 标准差
    samples: dict = field(default_factory=dict)  # 动作键 → 各世界终局分（世界与其他动作配对）

    def regret(self, chosen: str) -> tuple[float, float]:
        """专家最优相对所选动作的期望分差，及其配对标准误。"""
        best = max(self.values, key=self.values.get)
        if chosen not in self.samples or chosen == best:
            return 0.0, 0.0
        diffs = [b - c for b, c in zip(self.samples[best], self.samples[chosen])]
        se = statistics.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else 0.0
        return statistics.fmean(diffs), se


class RolloutExpert:
    """对每个合法动作，在 worlds 个确定化世界里推进一步再用启发式推演到终局，取平均分。"""

    name = "expert"

    def __init__(self, worlds: int = 16, seed: int = 0, meta: dict | None = None):
        self.worlds = worlds
        self.seed = seed
        self.meta = meta
        self.rng = random.Random(seed)

    def evaluate(self, env) -> ExpertResult:
        legal = env.legal_actions()
        # 同名卡的不同实例等价，只评估一次
        uniq = {}
        for a in legal:
            uniq.setdefault(_key(a, env), a)
        world_seeds = [self.rng.randrange(2**31) for _ in range(self.worlds)]
        values, spread, samples = {}, {}, {}
        for k, a in uniq.items():
            scores = []
            for ws in world_seeds:
                r = random.Random(ws)
                w = determinize(env, self.meta, r)
                fast_apply(w, a)
                scores.append(rollout(w, r))
            values[k] = statistics.fmean(scores)
            spread[k] = statistics.pstdev(scores)
            samples[k] = scores
        best = max(values, key=values.get)
        return ExpertResult(uniq[best], values, spread, samples)

    def start(self, game):
        pass

    def act(self, game):
        res = self.evaluate(game.env)
        return res.best, None, res

    def after(self, game, state, d):
        pass


def action_key(action: dict, env) -> str:
    return _key(action, env)
