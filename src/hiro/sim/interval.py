# 来源：digital-hiro src/hiro/sim/interval.py（develop 864e037）
"""轮间（R1 与 R2 之间的商店）按计算选（协调 4a 第八局本战分析 B3，用户：「当前的策略太垃圾了，每个决策的影响都要能算出来」）。

做法：
1. 把能做的事列成选项：回体力到几、买哪瓶饮料、买哪张卡（用ワッペン加星星）、チェンジ 哪张换哪张、强化哪张；
2. 每个选项（以及预算内的组合）都从当前状态出发合成 R2 开局（r2_opening），用模拟器跑多个种子取期望分；
3. 星星对得分的影响按 multiplier.star_factor（社区边界：基准 800 星 = 2 倍、往上连续增长、3 倍封顶）乘上去；
   卡加进牌组的稀释、体力对 エウレカ（HP ≤ 50% 才触发）的影响都由模拟自然体现；
4. 选期望分最高的组合；各组合的期望分写进决策记录。

R2 开局的合成方式同 tools/q3bench/synth.py：以 campus-20261019/r2-opening（12 回合）为模板，换上实际的牌组、饮料、体力、三属性倍率；
P 道具 / 支援卡 / 开局状态沿用模板（各组合相同，只影响绝对值，不影响组合之间的比较）；定制不还原。
"""

from __future__ import annotations

import copy
import json
import logging
import math
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

import hiro.sim  # noqa: F401  引擎路径
from hiro.sim import FIXTURES

TEMPLATE = "campus-20261019/r2-opening"
STAR_BASELINE_R2 = 800  # 本战 R2 审查基准 [wiki]
WAPPEN_STAR = 15  # 轮间买一张卡用掉一枚ワッペン：星星 +15（第八局实况：两张 +30）


@lru_cache(maxsize=None)
def _template(name: str = TEMPLATE) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@dataclass
class R2Setup:
    """合成 R2 开局所需的状态。deck：[(card_id, upgrade)]；drinks：[drink_id]；permil：{Vo, Da, Vi} 本战倍率（千分比）。"""

    deck: list
    drinks: list
    hp: int
    max_hp: int
    permil: dict
    star: int | None = None
    template: str = TEMPLATE  # 开局模板：R2 用 r2-opening（12 回合），R1 可用 campus-20261019/r1-opening（9 回合）
    fill: bool = True  # R2 模板时按応援棒补到 22 张（借 R2 模板模拟选拔考试时关掉）

    def key(self) -> tuple:
        deck = tuple(sorted(tuple(tuple(x) if isinstance(x, list) else x for x in c) for c in self.deck))
        return (deck, tuple(sorted(self.drinks)), self.hp, self.max_hp, tuple(sorted(self.permil.items())), self.template, self.fill)


# H.I.F応援棒（R1 结束后获得）：R2 开考时山札不满 22 张，用名字含「基本」的随机卡补到 22 张 [wiki][もなみ][いおん ep07]。
# 模拟里按固定轮换补（共通 + Plan2 的基础卡，未强化），这样「轮间买卡 vs 不买」比较的是「买的卡 vs 基础卡」，不是「买的卡 vs 小牌组」。
OUENBOU_SIZE = 22
OUENBOU_BASICS = ("p_card-00-act-0_001", "p_card-02-men-0_039", "p_card-00-men-0_003", "p_card-02-act-0_032", "p_card-02-men-0_041",
                  "p_card-00-act-0_002", "p_card-02-men-0_011", "p_card-02-act-0_037", "p_card-02-men-0_012", "p_card-02-act-0_038")  # fmt: skip


def ouenbou_fill(deck: list) -> list:
    """R2 开考前的牌组 → 応援棒补完后的牌组（不足 22 张时按 OUENBOU_BASICS 轮换补基础卡）。"""
    out = list(deck)
    i = 0
    while len(out) < OUENBOU_SIZE:
        out.append((OUENBOU_BASICS[i % len(OUENBOU_BASICS)], 0))
        i += 1
    return out


def r2_opening(setup: R2Setup) -> dict:
    o = copy.deepcopy(_template(setup.template))
    tmpl = next(iter(o["cards"].values()))
    o["cards"], o["zones"] = {}, {k: [] for k in o["zones"]}
    deck = ouenbou_fill(setup.deck) if setup.template == TEMPLATE and setup.fill else setup.deck  # 只有 R2 有応援棒
    for entry in deck:
        cid, up = entry[0], entry[1]
        counts = list(entry[2]) if len(entry) > 2 else [0, 0, 0]  # 定制：按卡的 produceCardCustomizeIds 顺序的次数
        inst = "card-" + uuid.uuid5(uuid.NAMESPACE_OID, f"{cid}@{up}#{len(o['cards'])}").hex[:24]
        c = copy.deepcopy(tmpl)
        c.update(instance=inst, card_id=cid, effective_upgrade=up, customize_counts=counts)
        c["native"]["_baseUpgradeCount"] = up
        c["native"]["_playCount"] = 0
        o["cards"][inst] = c
        o["zones"]["deck"].append(inst)
    o["drinks"] = [{"_id": d} for d in setup.drinks]
    s = o["scalars"]
    s.update(stamina=min(setup.hp, setup.max_hp), maxStamina=setup.max_hp, vocalBonusPermil=setup.permil["Vo"],
             danceBonusPermil=setup.permil["Da"], visualBonusPermil=setup.permil["Vi"])  # fmt: skip
    o["source"] = {"synthetic": True, "template": TEMPLATE}
    return o


def star_factor(star: int | None) -> float:
    from gakumas_produce.multiplier import star_factor as f

    return f(star, STAR_BASELINE_R2) if star else 2.0


# ---------------------------------------------------------------- 评估


def _env(setup: R2Setup, seed: int):
    """setup 的一局：开考快照合成好（饮料、体力、倍率都写在快照里），考试引擎按 seed 开局。"""
    obs = _opening_cached(setup.key(), setup)
    return _episode(setup, _exam_cached(setup.key(), obs), seed)


_EPISODES: "OrderedDict" = None  # type: ignore[assignment]
EPISODE_CACHE = 512  # 每个进程缓存的开局数（同一组合 × 种子）


def _episode(setup: R2Setup, exam, seed: int):
    """开局按（组合, 种子）缓存，每次返回一份 clone。"""
    global _EPISODES
    from collections import OrderedDict

    from hiro.sim.env import clone

    if _EPISODES is None:
        _EPISODES = OrderedDict()
    k = (setup.key(), seed)
    env = _EPISODES.get(k)
    if env is None:
        env = _EPISODES[k] = exam.episode(seed)
        if len(_EPISODES) > EPISODE_CACHE:
            _EPISODES.popitem(last=False)
    else:
        _EPISODES.move_to_end(k)
    return clone(env)


def bare_exam(setup: R2Setup):
    """setup 的那场考试（开考配置缓存；resource.prewarm / note_exam 提前建好）。"""
    return _exam_cached(setup.key(), _opening_cached(setup.key(), setup))


def _couple(env, base_env, seed: int) -> None:
    """共同随机数：变体按基准牌组在同一种子下的「手牌 + 山札」顺序排，换掉的卡原位替换（强化 / チェンジ），新加的卡插到按种子
    确定的位置，去掉的卡直接拿掉。这样同一种子下各方案只差那几张卡，期望差的标准误小得多（不配对时 6 个种子标准误 2–6 万）。"""
    key = lambda e, i: e.cards[i].split(":", 1)[0]  # noqa: E731  定义键去掉定制哈希
    seq = [key(base_env, i) for i in base_env.zones["hand"] + base_env.zones["deck"]]
    pool: dict = {}
    for i in env.zones["hand"] + env.zones["deck"]:
        pool.setdefault(key(env, i), []).append(i)
    have = {k: len(v) for k, v in pool.items()}
    out, removed = [], []
    for k in seq:
        if have.get(k, 0) > 0:
            have[k] -= 1
            out.append(k)
        else:
            out.append(None)
            removed.append(k)
    added = [k for k, n in have.items() for _ in range(n)]
    # 去掉的位置优先给同一张卡的其他强化度（强化），再给其他新卡（チェンジ）；剩下的新卡按种子插入，剩下的空位删掉
    for pos, k in enumerate(out):
        if k is not None:
            continue
        old = seq[pos].split("@")[0]
        j = next((n for n, a in enumerate(added) if a.split("@")[0] == old), 0 if added else None)
        if j is not None:
            out[pos] = added.pop(j)
    rng = random.Random(seed * 7919 + 17)
    for k in added:
        out.insert(rng.randrange(len(out) + 1), k)
    out = [k for k in out if k is not None]
    h = len(env.zones["hand"])
    insts = [pool[k].pop() for k in out]
    env.zones["hand"], env.zones["deck"] = insts[:h], insts[h:]


DRINK_TARGETS = ("p_card-02-act-3_186", "p_card-02-act-1_037", "p_card-02-act-3_039", "p_card-02-act-2_062", "p_card-02-act-3_045")  # 复制饮料值得接的主动卡
CASH_OPS = ("shield_score",)


def drink_allowed(env, action: dict, rule: str, drunk_this_turn: int) -> bool:
    """饮料阶段预算（第十局选拔③ 第 1 回合连喝三瓶；bench 验证用，见 reports/analysis/preview-optimization-plan.md）。
    R0 不限；R1 剩余 >4 回合时每回合最多 1 瓶；R2 剩余 >4 回合时按用途：复制只接核心主动卡、追加行动 / 爆发只在手里有兑现牌时、
    抽牌每回合 1 瓶；R3 剩余 >4 回合时不喝。剩余 ≤4 回合都不限。"""
    if action.get("kind") != "drink" or rule in (None, "R0"):
        return True
    remaining = len(env.turns) - env.turn
    if remaining <= 4:
        return True
    if rule == "R1":
        return drunk_this_turn < 1
    if rule == "R3":
        return False
    ops = {e.get("op") for e in env.drink_definitions[env.drinks[action["id"]]]["effects"]}
    hand = [env.cards[i].split("@")[0] for i in env.zones["hand"]]
    playable = {a["id"] for a in env.legal_actions() if a.get("kind") == "play"}
    cash = any(any(e.get("op") in CASH_OPS for e in env.definition(i)["effects"]) for i in env.zones["hand"] if i in playable)
    if "repeat_card" in ops:
        return any(c in DRINK_TARGETS for c in hand) and env.actions > 0
    if "extra_action" in ops or "timed_score_boost" in ops and "draw" not in ops:
        return cash
    return drunk_this_turn < 1


# 核心兑现牌最早在剩几回合时打（剩余回合按含追加回合算）：せのび 4 回合后结算，剩 5 打正好落在最后一回合；
# 届いて 清空元気；約束 按元気 / やる気兑现。第十局本战 R1 剩 9 回合、元気 0 时打出 せのび+，R1 只有 46,570
CASH_HOLD = {"p_card-02-act-3_186": 5, "p_card-02-act-3_039": 2, "p_card-02-act-3_045": 3}


def card_allowed(env, action: dict, rule: dict | None) -> bool:
    if not rule or action.get("kind") != "play":
        return True
    cid = env.cards[action["id"]].split("@")[0]
    limit = rule.get(cid)
    return limit is None or len(env.turns) - env.turn <= limit


def play(setup: R2Setup, seed: int, policy: str = "community", worlds: int = 4, depth: int = 2, base: R2Setup | None = None,
         drink_rule: str | None = None, card_rule: dict | None = None, drink_se: bool = False) -> int:  # fmt: skip
    """在合成的 R2 开局上打一局，返回终局分。community：社区打法续打（快，默认）；heuristic：旧启发式（只作对照，用户 2026-11-02：
    前 2 回合 / 末回合随机喝饮料，扭曲饮料 / 体力 / 强化的价值，不再用在实机路径上）；expert：每步预演选期望最高（实机专家同款，慢）。
    base 给出时与基准方案配对（_couple）。"""
    from hiro.sim.env import fast_apply
    from hiro.sim.policies import heuristic_action

    env = _env(setup, seed)
    if base is not None and base.key() != setup.key():
        _couple(env, _env(base, seed), seed)
    exam = _exam_cached(setup.key(), None)
    rng = random.Random(seed)
    if policy in ("heuristic", "community"):
        from hiro.sim.policies import community_action

        act = community_action if policy == "community" else heuristic_action
        while not env.ended:
            fast_apply(env, act(env, rng))
        return env.score
    from hiro.sim.preview import preview_actions

    turn, drunk = env.turn, 0
    while not env.ended:
        if env.turn != turn:
            turn, drunk = env.turn, 0
        legal = env.legal_actions()
        if len(legal) == 1:
            fast_apply(env, legal[0])
            continue
        # 固定计算量、不设时间预算：同一局面两次打出来完全一样，方案之间才能按同一种子配对（带 deadline 时机器负载会改变结果）
        ranked = preview_actions(env, exam.meta, worlds=worlds, seed=seed * 1000 + env.play_count, depth=depth)
        allowed = [r for r in ranked if drink_allowed(env, r.action, drink_rule, drunk) and card_allowed(env, r.action, card_rule)] or ranked[:1]
        if drink_se and allowed[0].action.get("kind") == "drink":
            alt = next((r for r in allowed if r.action.get("kind") != "drink"), None)
            if alt is not None and allowed[0].expected - alt.expected < alt.vs_best_se:
                allowed = [alt]  # 饮料只比最好的非饮料动作多不到 1 个标准误：不喝
        best = allowed[0]
        drunk += best.action.get("kind") == "drink"
        fast_apply(env, best.action)
    return env.score


_OPENINGS: dict = {}
_EXAMS: dict = {}


def _opening_cached(key, setup):
    if key not in _OPENINGS:
        _OPENINGS[key] = r2_opening(setup)
    return _OPENINGS[key]


def _exam_cached(key, obs):
    from hiro.sim.engine import Exam

    if key not in _EXAMS and obs is not None:
        _EXAMS[key] = Exam(obs)
    return _EXAMS[key]


def _play_task(args):
    setup, seed, policy, kw = args
    return play(setup, seed, policy, **kw)


_POOL = None
_POOL_N = 0


def _pool(workers: int):
    """常驻进程池（协调 2026-11-03：选拔饮料模拟每次 10–47 秒，其中每次新开进程、每个进程重新编译考试约 5.7 秒）。
    进程里的考试编译缓存（_exam_cached）跨调用复用；池坏了（BrokenProcessPool）由调用方重建。"""
    global _POOL, _POOL_N
    if _POOL is None or _POOL_N != workers:
        from concurrent.futures import ProcessPoolExecutor

        if _POOL is not None:
            _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL, _POOL_N = ProcessPoolExecutor(workers), workers
    return _POOL


def _reset_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False, cancel_futures=True)
    _POOL = None


def sim_workers(asked: int) -> int:
    """进程池大小上限（HIRO_SIM_WORKERS；0 = 不限）。没设时用平台缺省（hiro.compat.default_sim_workers：Windows 1，其他不限）。"""
    import os

    from hiro.compat import default_sim_workers

    cap = os.environ.get("HIRO_SIM_WORKERS")
    cap = int(cap) if cap and cap.isdigit() else default_sim_workers()
    return max(1, min(asked, cap)) if cap else asked


def evaluate(setups: list, seeds, policy: str = "expert", workers: int = 4, **kw) -> list[dict]:
    """多个开局 × 同一批种子（共同随机数，组合之间的差更稳）并行打完，返回每个开局的期望（已乘星星系数）。"""
    seeds = list(seeds)
    workers = sim_workers(workers)
    # 按种子排：同一种子的各方案落在同一批任务、同一个进程里，开局只生成一次（_episode 缓存）
    tasks = [(st, s, policy, dict(kw, base=setups[0])) for s in seeds for st in setups]
    if workers > 1 and len(tasks) > 1:
        from concurrent.futures.process import BrokenProcessPool

        chunk = len(setups) * max(1, len(seeds) // (workers * 2))
        try:
            scores = list(_pool(workers).map(_play_task, tasks, chunksize=chunk))
        except BrokenProcessPool:
            _reset_pool()
            scores = list(_pool(workers).map(_play_task, tasks, chunksize=chunk))
    else:
        scores = [_play_task(t) for t in tasks]
    m = len(setups)
    scores = [scores[j * m + i] for i in range(m) for j in range(len(seeds))]  # 回到「方案 × 种子」顺序
    out, n = [], len(seeds)
    for i, st in enumerate(setups):
        xs = [x * star_factor(st.star) / 2.0 for x in scores[i * n:(i + 1) * n]]  # 模板倍率按「正好基准 = 2 倍」算；星星按社区曲线放大
        se = statistics.stdev(xs) / math.sqrt(n) if n > 1 else 0.0
        out.append({"mean": round(statistics.fmean(xs)), "se": round(se), "star_factor": round(star_factor(st.star), 4), "n": n, "scores": xs})
    if out:  # 与第一个（基准）配对的差：同一种子相减
        b = out[0]["scores"]
        for r in out:
            d = [x - y for x, y in zip(r["scores"], b)]
            r["gain"] = round(statistics.fmean(d))
            r["gain_se"] = round(statistics.stdev(d) / math.sqrt(n)) if n > 1 else 0
    return out


def expected(setup: R2Setup, seeds, policy: str = "community", **kw) -> dict:
    return evaluate([setup], list(seeds), policy, workers=1, **kw)[0]


# ---------------------------------------------------------------- 选项与组合


@dataclass
class Option:
    """一个可执行的轮间选项。actions：按顺序执行的契约动作参数 [(kind, args)]。"""

    key: str
    label: str
    cost: int
    apply: object  # Callable[[R2Setup], R2Setup]
    actions: list = field(default_factory=list)
    group: str = ""  # 同组互斥（回体力的不同档位）
    card_id: str | None = None  # 买卡 / 强化：哪张卡

    def __hash__(self):
        return hash(self.key)


def recover_cost(hp_from: int, hp_to: int, per2: int = 10) -> int:
    return per2 * math.ceil(max(0, hp_to - hp_from) / 2)


def options(shelf: dict, setup: R2Setup, p: int, *, wappen_left: int = 2, junk: list | None = None, upgrade_target=None,
            per2: int = 10) -> list[Option]:  # fmt: skip
    """货架 + 当前状态 → 选项。shelf 格式同 page_fields.interval（cards / changes / drinks / upgrade / recover）。
    junk：チェンジ 时换出的牌（按价值从低到高的 [(card_id, upgrade)]）。"""
    out = []
    hp, mx = setup.hp, setup.max_hp
    targets = sorted({min(mx, t) for t in (mx // 2 + 1, mx // 2 + 3, (mx * 3) // 4, mx)} - {hp})
    for t in targets:
        if t <= hp:
            continue
        times = math.ceil((t - hp) / 2)
        out.append(Option(f"hp{t}", f"回体力到 {t}/{mx}", recover_cost(hp, t, per2),
                          lambda s, t=t: _with(s, hp=t), [("IntervalBuy", {"service": "recover"})] * times, group="hp"))  # fmt: skip
    for it in shelf.get("drinks") or []:
        if it.get("purchased") or not it.get("drink_id"):
            continue
        out.append(Option(f"drink{it['index']}", f"买饮料 {it.get('name')}", it["price"],
                          lambda s, d=it["drink_id"]: _with(s, drinks=s.drinks + [d]), [("IntervalBuy", {"index": it["index"]})]))  # fmt: skip
    for it in shelf.get("cards") or []:
        if it.get("purchased") or not it.get("card_id"):
            continue
        # 用户 2026-11-01（第九局复盘）：ワッペン / 星星不能单独作为买卡理由。评估买卡时不算星星加成，只算这张卡进牌组的效果
        out.append(Option(f"card{it['index']}", f"买卡 {it.get('name')}", it["price"],
                          lambda s, c=(it["card_id"], it.get("upgrade") or 0): _with(s, deck=s.deck + [c]),
                          [("IntervalBuy", {"index": it["index"]})], group="wappen", card_id=it["card_id"]))  # fmt: skip
    for it in shelf.get("changes") or []:
        if it.get("purchased") or not it.get("card_id") or not junk:
            continue
        old = junk[0]
        new = (it["card_id"], 0 if old[0] == "p_card-00-acc-0_002" else old[1])  # 眠気 チェンジ 后必为未强化 [wiki]；其余继承强化
        out.append(Option(f"change{it['index']}", f"チェンジ {old[0]} → {it.get('name')}", it["price"],
                          lambda s, o=old, n=new: _with(s, deck=_replace(s.deck, o, n)), [("IntervalBuy", {"index": it["index"], "change": old[0]})]))  # fmt: skip
    up = shelf.get("upgrade") or {}
    if upgrade_target and up.get("available", True) and (up.get("left") is None or up["left"] > 0):
        cid, u = upgrade_target
        out.append(Option("upgrade", f"强化 {cid}+{u}→+{u + 1}", up.get("price") or 100,
                          lambda s: _with(s, deck=_replace(s.deck, (cid, u), (cid, u + 1))), [("IntervalUpgradeCard", {"card_id": cid})],
                          card_id=cid))  # fmt: skip
    return [o for o in out if o.cost <= p]


def _with(s: R2Setup, **kw) -> R2Setup:
    d = dict(deck=list(s.deck), drinks=list(s.drinks), hp=s.hp, max_hp=s.max_hp, permil=dict(s.permil), star=s.star, template=s.template, fill=s.fill)
    d.update(kw)
    return R2Setup(**d)


def _replace(deck: list, old, new) -> list:
    out = list(deck)
    out[out.index(old)] = new
    return out


def apply_all(setup: R2Setup, opts) -> R2Setup:
    for o in opts:
        setup = o.apply(setup)
    return setup


SIGNIFICANT = 1.5  # 配对差超过几个标准误才算「确实更好」
CARD_SIGNIFICANT = 2.0  # 买卡要求更严（第九局：+1.29 万 ± 0.78 万的 ありがとうの言葉 被买进来）


def _sig(o, g, r) -> bool:
    return g > (CARD_SIGNIFICANT if o.group == "wappen" else SIGNIFICANT) * r["gain_se"]


def search(setup: R2Setup, opts: list[Option], p: int, *, drink_capacity: int = 4, wappen_left: int = 2, seeds=range(6),
           policy: str = "expert", workers: int = 4, combos_top: int = 5, log=None, **kw) -> dict:  # fmt: skip
    """1）基准与每个选项单独评估（同一批种子），得到边际收益；2）收益为正的选项在预算 / 饮料栏 / ワッペン次数 / 回体力档位互斥
    的约束下组合，按「边际收益之和」取前 combos_top 个；3）这些组合再实际评估，选期望最高。所有评估结果都返回（可审计）。"""
    from itertools import combinations

    t0 = time.monotonic()
    seeds = list(seeds)
    rs = evaluate([setup] + [o.apply(setup) for o in opts], seeds, policy, workers, **kw)
    base, single = rs[0], [(o, r["gain"], r) for o, r in zip(opts, rs[1:])]
    if log:
        for o, g, r in single:
            log(f"{o.label}：{g:+,.0f} ± {r['gain_se']:,}（{o.cost}P）")

    def feasible(combo) -> bool:
        return (sum(o.cost for o in combo) <= p and sum(o.group == "hp" for o in combo) <= 1
                and len(setup.drinks) + sum(o.key.startswith("drink") for o in combo) <= drink_capacity
                and sum(o.group == "wappen" for o in combo) <= max(wappen_left, 0))  # fmt: skip

    gain = {o.key: g for o, g, _ in single}
    # 只组合「明显为正」的选项（配对差 > SIGNIFICANT 个标准误）：噪声里的正值不花 P 点
    good = [o for o, g, r in sorted(single, key=lambda x: -x[1]) if _sig(o, g, r)][:10]
    cands = [c for k in range(1, len(good) + 1) for c in combinations(good, k) if feasible(c)]
    cands.sort(key=lambda c: -sum(gain[o.key] for o in c))
    multi = [c for c in cands if len(c) > 1][:combos_top]
    rows = [{"options": [], "labels": ["什么都不做（直接结束）"], "cost": 0, **base}]
    rows += [{"options": [o.key], "labels": [o.label], "cost": o.cost, **r} for o, _, r in single]
    if multi:
        for c, r in zip(multi, evaluate([setup] + [apply_all(setup, c) for c in multi], seeds, policy, workers, **kw)[1:]):
            rows.append({"options": [o.key for o in c], "labels": [o.label for o in c], "cost": sum(o.cost for o in c),
                         "additive_est": round(base["mean"] + sum(gain[o.key] for o in c)), **r})  # fmt: skip
    rows = [r for r in rows if feasible([o for o in opts if o.key in r["options"]])]
    for r in rows:
        r.pop("scores", None)
    rows.sort(key=lambda x: -x["gain"])
    return {"base": rows[-1] if not rows else next(r for r in rows if not r["options"]), "combos": rows, "best": rows[0],
            "policy": policy, "seeds": seeds, "seconds": round(time.monotonic() - t0, 1)}  # fmt: skip


def plan_actions(best: dict, opts: list[Option]) -> list:
    """最佳组合 → 执行顺序：先回体力（不受后面买东西影响），再饮料、卡、チェンジ、强化。"""
    order = {"hp": 0, "drink": 1, "card": 2, "change": 3, "upgrade": 4}
    chosen = sorted((o for o in opts if o.key in best["options"]), key=lambda o: order.get(o.group or o.key.rstrip("0123456789"), 5))
    return [a for o in chosen for a in o.actions]


# ---------------------------------------------------------------- 实机接线

# 开源版：本战上下文的记账在 hiro.state.finals_context（考试循环调用它的 note_exam），这里是同一个对象
from hiro.state.finals_context import CONTEXT, CONTEXT_FILE  # noqa: E402


def note_exam(label: str, *, deck=None, drinks=None, permil=None, trace_dir=None) -> None:
    """同 hiro.state.finals_context.note_exam（记账、落盘），另外记下轮间缓存目录、记下倍率时预热 R2。"""
    from hiro.state import finals_context

    finals_context.note_exam(label, deck=deck, drinks=drinks, permil=permil, trace_dir=trace_dir)
    on_context(label, permil=permil, trace_dir=trace_dir)


def on_context(label: str, *, permil=None, trace_dir=None) -> None:
    """finals_context.note_exam 之后调用：轮间缓存落在 trace 目录；R1 记下倍率时让进程池先编译 R2。"""
    global STATE_DIR
    if label != "r1":
        return
    if trace_dir is not None:
        STATE_DIR = trace_dir
    if permil:
        _prewarm_r2()


def _prewarm_r2(workers: int = 4) -> None:
    """R1 结束（note_exam 记下倍率）时，让常驻进程池的每个进程先把 R2 的考试编译好（R1 牌组、无饮料），进轮间时方案 / 增益表不用等编译。不阻塞。"""
    deck = [tuple(x) for x in CONTEXT.get("deck") or []]
    permil = {a: v for a, v in (CONTEXT.get("permil") or {}).items() if v}
    if not deck or not permil:
        return
    for a in ("Vo", "Da", "Vi"):
        permil.setdefault(a, min(permil.values()))
    if sim_workers(workers) <= 1:
        return  # 不开进程池（Windows 默认）：没有常驻子进程可预热，用到时在主进程里编译
    try:
        setup = R2Setup(deck=deck, drinks=[], hp=33, max_hp=33, permil=permil)
        pool = _pool(workers)
        for _ in range(workers):
            pool.submit(bare_exam, setup)
    except Exception as e:  # noqa: BLE001
        logger.debug("R2 预热失败：%s", e)


def load_context(trace_dir) -> dict:
    global STATE_DIR
    from hiro.state import finals_context

    finals_context.load_context(trace_dir)
    STATE_DIR = trace_dir
    return CONTEXT


def upgrade_target(deck: list) -> tuple | None:
    """R1 牌组里最值得强化的未强化卡（强化顺序表 → 必拿 → S / A），要有 + 版。"""
    from hiro.state.context import catalog
    from hiro.state.knowledge import UPGRADE_ORDER, YARUKI_CARDS

    owned = {c[0] for c in deck if c[1] == 0}
    order = [c for c in UPGRADE_ORDER if c in owned]
    order += [c for c, k in YARUKI_CARDS.items() if c in owned and c not in order and (k.get("must") or k.get("tier") in ("S", "A"))]
    for cid in order:
        try:
            catalog().card(cid, 1)
            return (cid, 0)
        except Exception:
            continue
    return None


def greedy_final(rows: list, opts: list, p: int, drinks_held: int, drink_capacity: int) -> list:
    """最后一个轮间（R1→R2，之后 P 点没用）：单项期望 ≥ 0 的选项按期望从高到低买，直到 P 不够（总控 2026-11-01）。
    回体力只取一档（期望最高的那档）；饮料不超过空位；买卡排除小牌组不该拿的牌（用户：不为星星买废卡，买卡评估本来就不算星星）。"""
    from hiro.brain.deciders import unwanted

    by_key = {o.key: o for o in opts}
    singles = sorted((r for r in rows if len(r["options"]) == 1 and r["gain"] >= 0), key=lambda r: -r["gain"])
    chosen, left, hp_done, free = [], p, False, drink_capacity - drinks_held
    for r in singles:
        o = by_key.get(r["options"][0])
        if o is None or o.cost > left:
            continue
        if o.group == "hp":
            if hp_done:
                continue
            hp_done = True
        elif o.key.startswith("drink"):
            if free <= 0:
                continue
            free -= 1
        elif o.group == "wappen" and unwanted(o.card_id):
            continue
        chosen.append((o, r))
        left -= o.cost
    return chosen


def plan(shelf: dict, *, hp: int, max_hp: int, p: int, star: int | None, drink_capacity: int = 4, wappen_left: int = 2,
         junk: list | None = None, seeds=range(200), policy: str = "community", workers: int = 4, log=None, final: bool = True,
         **kw) -> dict | None:  # fmt: skip
    """轮间方案：CONTEXT 里有 R1 牌组时返回 {target_hp, buy: [货架 index], labels, gain, rows（各组合期望分）}；否则 None。
    当前只执行 回体力 / 买饮料 / 买卡（チェンジ、强化 只评估、写进记录，执行器支持后再放开）。"""
    deck = [tuple(x) for x in CONTEXT.get("deck") or []]
    if not deck:
        return None
    permil = {a: v for a, v in (CONTEXT.get("permil") or {}).items() if v}
    if not permil:
        return None
    for a in ("Vo", "Da", "Vi"):
        # 缺的属性用已知的最小值补（第十局续跑：R1 画面没读到 Vi 回合，permil 只有 Vo / Da，轮间没跑模拟）；只影响绝对分，方案之间照样可比
        permil.setdefault(a, min(permil.values()))
    setup = R2Setup(deck=deck, drinks=list(CONTEXT.get("drinks") or []), hp=hp, max_hp=max_hp, permil=permil, star=star)
    per2 = (shelf.get("recover") or {}).get("price_per_2hp") or 10
    opts = options(shelf, setup, p, wappen_left=wappen_left, junk=junk, per2=per2, upgrade_target=upgrade_target(deck))
    # 评估策略：社区打法 200 个种子配对（用户 2026-11-02 起；原来是启发式，它会随机喝饮料）。专家每局约 2 s、单局波动约 11 万，
    # 24 个种子标准误仍有 2–3 万，分不出 1–5% 的差（reports/analysis/interval-sim-main8.md）；续打打法确定，同一种子配对后标准误小
    run = search(setup, opts, p, drink_capacity=drink_capacity, wappen_left=wappen_left, seeds=seeds, policy=policy, workers=workers,
                 log=log, **kw)  # fmt: skip
    if final:
        # 最后一个轮间：P 点之后没用，期望 ≥ 0 就买（不要求显著），按期望从高到低（总控 2026-11-01，第十二局剩 389P 没花）
        exe = [o for o in opts if o.key.startswith(("hp", "drink", "card", "upgrade"))]
        chosen_rows = greedy_final(run["combos"], exe, p, len(setup.drinks), drink_capacity)
        chosen = [o for o, _ in chosen_rows]
        target = max([hp] + [int(o.key[2:]) for o in chosen if o.group == "hp"])
        buy = [a[1]["index"] for o in chosen if o.group != "hp" for a in o.actions if "index" in a[1]]
        up = next((o.card_id for o in chosen if o.key == "upgrade"), None)
        return {"target_hp": target, "buy": buy, "upgrade": up, "labels": [o.label for o in chosen],
                "gain": sum(r["gain"] for _, r in chosen_rows), "gain_se": max([r["gain_se"] for _, r in chosen_rows] or [0]),
                "cost": sum(o.cost for o in chosen), "significant": bool(chosen), "final": True,
                "rows": run["combos"], "seconds": run["seconds"], "policy": run["policy"], "seeds": run["seeds"]}  # fmt: skip
    executable = [r for r in run["combos"] if all(k.startswith(("hp", "drink", "card")) for k in r["options"])
                  and (not r["options"] or r["gain"] > SIGNIFICANT * r["gain_se"])]  # fmt: skip
    best = executable[0] if executable else {"options": [], "labels": [], "gain": 0, "gain_se": 0, "cost": 0}
    chosen = [o for o in opts if o.key in best["options"]]
    target = max([hp] + [int(o.key[2:]) for o in chosen if o.group == "hp"])
    buy = [a[1]["index"] for o in chosen if o.group != "hp" for a in o.actions if "index" in a[1]]
    return {"target_hp": target, "buy": buy, "labels": best["labels"], "gain": best["gain"], "gain_se": best["gain_se"], "cost": best["cost"],
            "significant": bool(best["options"]),
            "rows": run["combos"], "seconds": run["seconds"], "policy": run["policy"], "seeds": run["seeds"]}  # fmt: skip


# ---------------------------------------------------------------- 社区默认方案 + 模拟偏离（用户 2026-11-02 第十六局复盘）
HP_ABOVE_TARGET_TEST = True  # 回体力超过角色目标那段单独过 2 SE（用户「控制在半血以上就好」，协调 2026-11-04 定默认开）
DEVIATE_SE = 2.0  # 模拟方案比社区默认方案好过几个标准误才偏离（用户更正：建议 > 2 个标准误，跨档也可以）


def _card_rule_key(card_id: str, owned: set) -> tuple:
    """轮间补卡的社区默认顺序（大的先）：缺的必拿卡 > 一次性的再动 / 加使用数卡（いおん ep08：优先用一次性再动卡补张数）> 社区评级 / 卡牌价值。"""
    from hiro.brain.cards import description_text
    from hiro.brain.deciders import _card_priority
    from hiro.state.knowledge import is_once
    from hiro.state.context import catalog
    from hiro.state.knowledge import YARUKI_CARDS

    know = YARUKI_CARDS.get(card_id) or {}
    try:
        text = description_text(catalog().card(card_id, 0)["produceDescriptions"])
    except Exception:
        text = ""
    once_action = bool(is_once(card_id)) and ("使用数追加" in text or "追加行動" in text)
    return (bool(know.get("must")) and card_id not in owned, once_action, _card_priority(card_id))


def rule_plan(shelf: dict, setup: R2Setup, opts: list[Option], p: int, *, target_hp: int, drink_capacity: int = 4,
              wappen_left: int | None = None) -> tuple[list[Option], list[str]]:  # fmt: skip
    """社区默认的轮间方案（いおん ep07 / もなみ / playbook，用户 2026-11-02）：
    1. 补到 22 张：只能靠买卡（セレクトチェンジ 不加张数），按 _card_rule_key 选；小牌组不该拿的牌（冲突 / 好印象）不买，交给応援棒补基础卡；
    2. 买 R2 饮料：按 hiro.brain.drinks 的持有档位（R2：两瓶爆发 + 两瓶抽牌，复制优先），一次加一瓶、档位变好才买；
       优先档（复制 / 必要的爆发）可以用掉回体力的钱，其余饮料先给回体力留出到目标体力的 P；
    3. 回体力到角色目标（広 刚过半血）；
    4. 还有 P：强化核心卡。体力不超过角色目标（用户 2026-11-01：広 有回血、エウレカ 要半血以下才触发；
       第十六局轮间专家对比：回满 33 比 B3 的 24 低 −2.2 万 ± 1.6 万），剩下的 P 不为回体力而花。
    返回 (选项列表, 每步说明)。"""
    from hiro.brain import drinks as dk
    from hiro.brain.deciders import unwanted

    by_key = {o.key: o for o in opts}
    chosen, notes, left = [], [], p
    owned = {c[0] for c in setup.deck}
    # 本战瘦牌组（用户 2026-11-02）：不再为凑 22 张买卡——不满 22 张由応援棒在 R2 开考时补「基本」卡（模拟里按 OUENBOU_BASICS 补）；
    # 只买核心卡（缺的必拿 / S·A / 核心角色）。依据：第十六局轮间（24 张牌组）买 イメトレ+ / トキメキ+ / 本番前夜+ / ゆるふわ+
    # 比不买（応援棒补基础卡）−1.3 万 ~ −3.1 万；牌组越大，せのび / 届いて 越难在关键回合抽到
    from hiro.brain.deciders import _is_core_card

    from hiro.brain.deciders import wants_copy

    count = lambda cid: sum(1 for c in setup.deck if c[0] == cid)  # noqa: E731
    cards = [o for o in opts if o.group == "wappen" and o.card_id and not unwanted(o.card_id) and _is_core_card(o.card_id) and wants_copy(o.card_id, count(o.card_id))]
    cards.sort(key=lambda o: _card_rule_key(o.card_id, owned), reverse=True)
    limit = len(cards) if wappen_left is None else min(len(cards), max(wappen_left, 0))
    for o in cards[:limit]:
        if o.cost <= left:
            chosen.append(o)
            left -= o.cost
    notes.append(f"牌组 {len(setup.deck)} 张：不为凑 22 张买卡（不满由応援棒补基础卡）；核心卡 {'买 ' + '、'.join(o.label for o in chosen) if chosen else '货架上没有'}")
    hp_opts = sorted((o for o in opts if o.group == "hp"), key=lambda o: int(o.key[2:]))
    to_target = next((o for o in hp_opts if int(o.key[2:]) >= target_hp), None) if setup.hp < target_hp else None
    reserve = to_target.cost if to_target else 0
    held = list(setup.drinks)
    drink_opts = [o for o in opts if o.key.startswith("drink")]
    bought = []
    while len(held) < drink_capacity:
        cur = dk.holding_key(held, "r2", drink_capacity)
        best = None
        for o in drink_opts:
            if o in bought or o.cost > left:
                continue
            new = held + [o.apply(_with(setup, drinks=[])).drinks[-1]]
            key = dk.holding_key(new, "r2", drink_capacity)
            if key >= cur:
                continue
            urgent = dk.tag(new, "r2")[-1][0] == 0  # 这瓶在新持有里是优先档（复制 / 必要的爆发）
            if not urgent and o.cost > left - reserve:
                continue
            if best is None or key < best[0]:
                best = (key, o)
        if best is None:
            break
        o = best[1]
        bought.append(o)
        chosen.append(o)
        held = held + [o.apply(_with(setup, drinks=[])).drinks[-1]]
        left -= o.cost
    notes.append(f"R2 饮料：买 {'、'.join(o.label for o in bought) or '无'}，持有 {dk.describe(held, 'r2')}")
    hp_pick = None
    if to_target and to_target.cost <= left:
        hp_pick = to_target
    elif setup.hp < target_hp:  # 回不到目标：能回多少回多少
        hp_pick = next((o for o in reversed(hp_opts) if int(o.key[2:]) < target_hp and o.cost <= left), None)
    if hp_pick:
        left -= hp_pick.cost
    up = by_key.get("upgrade")
    if up and up.cost <= left:
        chosen.append(up)
        left -= up.cost
        notes.append(f"P 还有剩：{up.label}")
    if hp_pick:
        chosen.append(hp_pick)
        notes.append(f"回体力：{hp_pick.label}（角色目标 {target_hp}）")
    elif setup.hp < target_hp:
        notes.append(f"P 不够回体力（目标 {target_hp}）")
    return chosen, notes


SCREEN_SEEDS = 48  # 轮间方案初筛（search）的局数
PAIR_BATCHES = (32, 64, 128, 200)  # 社区默认 vs 模拟方案的配对检验：分批，显著或外推无望就停


def _pair_adaptive(setups: list, seeds: list, policy: str, workers: int, **kw) -> tuple[list[dict], int]:
    """[基准, 社区默认, (模拟方案)] 分批配对评估。只有两个时（方案一致）跑第一批就停。判据和原来一样：
    d = 模拟 − 默认，se = √(se_模拟² + se_默认²)（保守）；d > DEVIATE_SE × se（≥ 32 局）→ 停；外推到最后一批也到不了 0.75 × DEVIATE_SE → 停。"""
    batches = [b for b in PAIR_BATCHES if b <= len(seeds)] or [len(seeds)]
    scores = [[] for _ in setups]
    done, out = 0, []
    for n in batches:
        res = evaluate(setups, seeds[done:n], policy, workers, **kw)
        for i, r in enumerate(res):
            scores[i].extend(r["scores"])
        done = n
        base = scores[0]
        out = []
        for st, xs in zip(setups, scores):
            d = [x - y for x, y in zip(xs, base)]
            m = len(d)
            out.append({"mean": round(statistics.fmean(xs)), "se": round(statistics.stdev(xs) / math.sqrt(m)) if m > 1 else 0,
                        "star_factor": round(star_factor(st.star), 4), "n": n, "gain": round(statistics.fmean(d)),
                        "gain_se": round(statistics.stdev(d) / math.sqrt(m)) if m > 1 else 0})  # fmt: skip
        if len(setups) < 3:
            break
        dd = out[2]["gain"] - out[1]["gain"]
        se = max(math.sqrt(out[2]["gain_se"] ** 2 + out[1]["gain_se"] ** 2), 1)
        t = dd / se
        if (t > DEVIATE_SE and n >= 32) or t * math.sqrt(batches[-1] / n) < 0.75 * DEVIATE_SE:
            break
    return out, done


def plan_tiered(shelf: dict, *, hp: int, max_hp: int, p: int, star: int | None, target_hp: int, drink_capacity: int = 4,
                wappen_left: int | None = None, seeds=range(200), policy: str = "community", workers: int = 4, log=None,
                extra_drinks=(), extra_cards=(), upgraded=(), **kw) -> dict | None:  # fmt: skip
    """轮间：社区默认方案（rule_plan）为默认；模拟方案（B3：逐项评估 + greedy_final）比它好 > DEVIATE_SE 个标准误时用模拟方案，
    记偏离。返回格式同 plan()，另带 rule_labels / rule_notes / deviation。CONTEXT 没有 R1 牌组时 None。"""
    deck = [tuple(x) for x in CONTEXT.get("deck") or []]
    if not deck:
        return None
    permil = {a: v for a, v in (CONTEXT.get("permil") or {}).items() if v}
    if not permil:
        return None
    for a in ("Vo", "Da", "Vi"):
        permil.setdefault(a, min(permil.values()))
    t0 = time.monotonic()
    # 本次轮间已经成交的部分（中途重算时）：买过的卡进牌组、买过的饮料进持有、强化过的卡按 +1
    deck = deck + [tuple(c) for c in extra_cards]
    for cid in upgraded:
        hit = next((c for c in deck if c[0] == cid and c[1] == 0), None)
        if hit is not None:
            deck = _replace(deck, hit, (cid, 1))
    setup = R2Setup(deck=deck, drinks=list(CONTEXT.get("drinks") or []) + list(extra_drinks), hp=hp, max_hp=max_hp, permil=permil, star=star)
    per2 = (shelf.get("recover") or {}).get("price_per_2hp") or 10
    opts = options(shelf, setup, p, per2=per2, upgrade_target=upgrade_target(deck))
    # 「不回满」是硬约束（用户：控制在半血以上就好、不回满；协调 2026-11-02：社区续打对体力估值偏高，第十七局 B3 选回满 33，专家核对只是噪声）。
    # 续打第 2、3 步改好复测前，回满这一档不进模拟候选，模拟显著也不偏离到回满
    opts = [o for o in opts if not (o.group == "hp" and int(o.key[2:]) >= max_hp)]
    # 瘦牌组：非核心卡不进模拟候选（模拟显著也不偏离到买废卡）
    from hiro.brain.deciders import _is_core_card

    from hiro.brain.deciders import wants_copy

    opts = [o for o in opts if not (o.group == "wappen" and not (_is_core_card(o.card_id) and wants_copy(o.card_id, sum(1 for c in deck if c[0] == o.card_id))))]
    exe = [o for o in opts if o.key.startswith(("hp", "drink", "card", "upgrade"))]
    rule, notes = rule_plan(shelf, setup, exe, p, target_hp=target_hp, drink_capacity=drink_capacity, wappen_left=wappen_left)
    # 初筛只用来挑候选方案（偏离要过下面的配对检验），48 局够用（协调 2026-11-03 轮间提速：原来 200 局，第二十二局一次方案 105s）
    run = search(setup, opts, p, drink_capacity=drink_capacity, wappen_left=OUENBOU_SIZE if wappen_left is None else wappen_left,
                 seeds=list(seeds)[:SCREEN_SEEDS], policy=policy, workers=workers, log=log, **kw)  # fmt: skip
    sim = [o for o, _ in greedy_final(run["combos"], exe, p, len(setup.drinks), drink_capacity)]
    # 体力档：社区规则（半血以上，広 刚过半血 = 角色目标）为默认；回得比目标多要和其他项一样模拟显著（> DEVIATE_SE）才偏离。
    # 原来「中间回到几由 B3 模拟定」不经检验（第十八、十九局都回到 24）；用户 2026-11-03「回体力的必要性也待考证」，
    # 第十九局 R2 开局快照 64 局配对：17 比 24 −0.9 万 ± 1.3 万（不显著），16（不回）−3.5 万 ± 1.2 万，12 −4.3 万 ± 1.2 万（决策-17 §5）
    same = {o.key for o in sim} == {o.key for o in rule}
    pair, n_used = _pair_adaptive([setup, apply_all(setup, rule)] + ([] if same else [apply_all(setup, sim)]), list(seeds), policy, workers, **kw)
    r_rule = pair[1]
    pick, deviation = rule, None
    if not same:
        r_sim = pair[2]
        d = r_sim["gain"] - r_rule["gain"]
        # 两个方案对同一基准的配对差相减：标准误取两者平方和开方（保守，不算协方差）
        se = math.sqrt(r_sim["gain_se"] ** 2 + r_rule["gain_se"] ** 2)
        if d > DEVIATE_SE * max(se, 1):
            pick = sim
            deviation = {"where": "HIF_ROUND_INTERVAL", "rule": [o.label for o in rule], "pick": [o.label for o in sim], "gain": round(d),
                         "gain_se": round(se), "policy": policy, "seeds": n_used,
                         "has_hp": any(o.group == "hp" for o in sim) and {o.key for o in sim if o.group == "hp"} != {o.key for o in rule if o.group == "hp"}}  # fmt: skip
        notes.append(f"模拟方案 {'、'.join(o.label for o in sim) or '不买'} 比社区默认 {d:+,.0f} ± {se:,.0f}"
                     + ("（> 2 个标准误：偏离社区默认）" if deviation else "（不到 2 个标准误：按社区默认）"))
    else:
        notes.append("模拟方案与社区默认一致")
    r_pick = pair[1] if pick is rule else pair[2]
    gain, gain_se = r_pick["gain"], r_pick["gain_se"]
    if pick is not rule and any(o.key == "upgrade" for o in pick):
        # 强化单独过检验（协调 2026-11-04，第二十五局：「回体力 + 强化」合并估 +7.2 万，强化单独约 +0.6 万）：
        # 同一批种子配对「方案 − 方案去掉强化」，> DEVIATE_SE 个标准误才把强化留在模拟方案里；否则去掉，强化回到社区默认的「剩下的 P 再强化」
        rest = [o for o in pick if o.key != "upgrade"]
        ru = evaluate([apply_all(setup, rest), apply_all(setup, pick)], list(seeds)[:max(n_used, 32)], policy, workers, **kw)
        du = [b - a for a, b in zip(ru[0]["scores"], ru[1]["scores"])]
        gu, su = statistics.fmean(du), (statistics.stdev(du) / math.sqrt(len(du)) if len(du) > 1 else 0.0)
        if gu > DEVIATE_SE * max(su, 1):
            notes.append(f"强化单独 {gu:+,.0f} ± {su:,.0f}（> 2 个标准误：留在模拟方案里）")
        else:
            notes.append(f"强化单独 {gu:+,.0f} ± {su:,.0f}（不到 2 个标准误：不进模拟方案，剩下的 P 按社区默认再强化）")
            gain, gain_se = gain - round(gu), gain_se
            pick = rest
            if {o.key for o in pick} | {"upgrade"} == {o.key for o in rule} | {"upgrade"}:
                pick, deviation = rule, None
                gain, gain_se = pair[1]["gain"], pair[1]["gain_se"]
            elif deviation is not None:
                deviation = dict(deviation, pick=[o.label for o in pick], gain=round(deviation["gain"] - gu))
    over = [o for o in pick if o.group == "hp" and int(o.key[2:]) > max(target_hp, hp)]
    if HP_ABOVE_TARGET_TEST and pick is not rule and over:
        # 回体力超过角色目标那一段单独过检验（用户「控制在半血以上就好」，协调 2026-11-04）：同一批种子配对「方案 − 方案只回到目标」，
        # > DEVIATE_SE 个标准误才回到更高；否则只回到目标（省下的 P 交给 drink_step 刷新找目标饮料）
        base_hp = next((o for o in opts if o.key == f"hp{target_hp}"), None) if target_hp > hp else None
        alt = [o for o in pick if o.group != "hp"] + ([base_hp] if base_hp else [])
        rh = evaluate([apply_all(setup, alt), apply_all(setup, pick)], list(seeds)[:max(n_used, 32)], policy, workers, **kw)
        dh = [b - a for a, b in zip(rh[0]["scores"], rh[1]["scores"])]
        gh, sh = statistics.fmean(dh), (statistics.stdev(dh) / math.sqrt(len(dh)) if len(dh) > 1 else 0.0)
        if gh > DEVIATE_SE * max(sh, 1):
            notes.append(f"回体力到 {over[0].key[2:]}（比只回到 {target_hp} 多）单独 {gh:+,.0f} ± {sh:,.0f}（> 2 个标准误：保留）")
        else:
            notes.append(f"回体力到 {over[0].key[2:]}（比只回到 {target_hp} 多）单独 {gh:+,.0f} ± {sh:,.0f}（不到 2 个标准误：只回到目标 {target_hp}）")
            gain = gain - round(gh)
            pick = alt
            if {o.key for o in pick} == {o.key for o in rule}:
                pick, deviation = rule, None
                gain, gain_se = pair[1]["gain"], pair[1]["gain_se"]
            elif deviation is not None:
                deviation = dict(deviation, pick=[o.label for o in pick], gain=round(deviation["gain"] - gh))
    target = max([hp] + [int(o.key[2:]) for o in pick if o.group == "hp"])
    buy = [a[1]["index"] for o in pick if o.group != "hp" for a in o.actions if "index" in a[1]]
    # 强化永远最后执行（饮料停了才用剩下的钱，deciders._interval_drink_step）；模拟方案没留强化时用社区默认的强化目标
    up = next((o.card_id for o in pick if o.key == "upgrade"), None) or next((o.card_id for o in rule if o.key == "upgrade"), None)
    return {"target_hp": target, "buy": buy, "upgrade": up, "labels": [o.label for o in pick], "gain": gain, "gain_se": gain_se,
            "cost": sum(o.cost for o in pick), "significant": True, "final": True, "rule_labels": [o.label for o in rule], "rule_notes": notes,
            "deviation": deviation, "rows": run["combos"], "seconds": round(time.monotonic() - t0, 1), "policy": policy, "seeds": list(seeds)[:n_used]}  # fmt: skip


# ---------------------------------------------------------------- 轮间刷新货架（用户 2026-11-02：第十六局 R2 只有 漢方、第十七局只有 ハーブティー）
# 商品池：客户端 master 没有相談 / 轮间的商品表（服务器决定）。依据实机观测：全部 trace 里相談货架 42 次 144 瓶，16 种 共通 + Plan2 饮料都出现、
# 频率接近均匀（每种 4–14 次）；轮间货架 20 次 29 瓶，种类是它的子集。所以假设：刷新后每个饮料格从这 16 种里等概率抽，两格独立。
# 价格（wiki：轮间 Pドリンク交換 2 枠，和相談同价）：R 50 / SR 75 / SSR 100。
SHOP_DRINK_POOL = ("pdrink_00-1-001", "pdrink_00-1-004", "pdrink_00-2-001", "pdrink_00-2-003", "pdrink_00-2-004", "pdrink_00-2-008", "pdrink_00-2-009",
                   "pdrink_00-2-010", "pdrink_00-3-001", "pdrink_00-3-005", "pdrink_02-1-006", "pdrink_02-1-008", "pdrink_02-2-006", "pdrink_02-3-006",
                   "pdrink_02-3-007", "pdrink_02-3-010")  # fmt: skip
INTERVAL_DRINK_SLOTS = 2
DRINK_PRICE = {"ProduceDrinkRarity_R": 50, "ProduceDrinkRarity_Sr": 75, "ProduceDrinkRarity_Ssr": 100}
GAIN_SIG = 1.5  # 一瓶饮料的增益要超过几个标准误才算「值得买」（噪声里的正值不买）
# 余 P 的机会成本（R2 分 / P）：轮间计划内的体力 / 强化已经预留，剩下的 P 之后只换得到少量东西（knowledge.FINALS_P_VALUE 的「需要量以外」档）。
# 第十八局本战：ルイボスティー R2 增益 +120 ± 1（SE 极小，过了 1.5 SE）花了 50P——加上「增益 > 价格 × 每 P 机会成本」
SPARE_P_VALUE = 20


@lru_cache(maxsize=None)
def drink_price(drink_id: str) -> int:
    from hiro.state.context import catalog

    try:
        return DRINK_PRICE.get(catalog().by_id("ProduceDrink", drink_id)[0]["rarity"], 75)
    except Exception:
        return 75


GAIN_BATCHES = (32, 64, 100)  # 增益表分批：每瓶「肯定值得买 / 肯定不值得」就不再加局（协调 2026-11-03 轮间提速）


def drink_gains(setup: R2Setup, pool=SHOP_DRINK_POOL, seeds=range(100), policy: str = "community", workers: int = 4) -> dict:
    """见 drink_gains_table（测试里 conftest 会把这个入口换掉，不跑续打）。"""
    return drink_gains_table(setup, pool=pool, seeds=seeds, policy=policy, workers=workers)


def drink_gains_table(setup: R2Setup, pool=SHOP_DRINK_POOL, seeds=range(100), policy: str = "community", workers: int = 4) -> dict:
    """持有 setup.drinks 时，再加池里每一瓶的 R2 增益 {drink_id: (gain, se)}（社区续打配对，按种类和阶段用饮料）。
    分批（GAIN_BATCHES，最多 len(seeds)）：一瓶的「增益 − 2 × se」已经高过买它的门槛（GAIN_SIG × se 和 价格 × SPARE_P_VALUE），
    或「增益 + 2 × se」低于价格 × SPARE_P_VALUE，就定了；没定的继续加局。和一次跑满相比只是少跑定了的那些。"""
    seeds = list(seeds)
    batches = [b for b in GAIN_BATCHES if b <= len(seeds)] or [len(seeds)]
    pool = list(pool)
    base_scores: dict = {}
    scores = {d: {} for d in pool}
    open_ = list(pool)
    done = 0
    for n in batches:
        batch = seeds[done:n]
        rs = evaluate([setup] + [_with(setup, drinks=setup.drinks + [d]) for d in open_], batch, policy, workers=workers)
        for s, x in zip(batch, rs[0]["scores"]):
            base_scores[s] = x
        for d, r in zip(open_, rs[1:]):
            for s, x in zip(batch, r["scores"]):
                scores[d][s] = x
        done = n
        still = []
        for d in open_:
            g, se = _gain_of(scores[d], base_scores)
            line = SPARE_P_VALUE * drink_price(d)
            if not (g - 2 * se > max(GAIN_SIG * se, line) or g + 2 * se < line):
                still.append(d)
        open_ = still
        if not open_:
            break
    return {d: _gain_of(scores[d], base_scores) for d in pool}


def _gain_of(xs: dict, base: dict) -> tuple[int, int]:
    d = [x - base[s] for s, x in xs.items()]
    if not d:
        return 0, 0
    return round(statistics.fmean(d)), round(statistics.stdev(d) / math.sqrt(len(d))) if len(d) > 1 else 0


_GAINS_BG: dict = {}  # 后台增益表：key → {"thread", "result", "error"}


def _gains_key(setup: R2Setup) -> tuple:
    return _with(setup, hp=setup.max_hp).key()


def start_gains(setup: R2Setup) -> None:
    """后台线程先算增益表（协调 2026-11-03：「进轮间时直接用」）。轮间方案一算出来就按「现在持有 + 方案里要买的饮料」提交；
    同一组合只算一次。线程里用常驻进程池，不阻塞决策。"""
    import threading

    k = _gains_key(setup)
    if k in _GAINS_BG:
        return
    slot: dict = {"result": None, "error": None}

    def run():
        try:
            slot["result"] = drink_gains(setup)
        except Exception as e:  # noqa: BLE001
            slot["error"] = e

    slot["thread"] = threading.Thread(target=run, daemon=True)
    _GAINS_BG[k] = slot
    slot["thread"].start()


def take_gains(setup: R2Setup, wait_s: float = 60.0) -> dict | None:
    """后台算好的增益表（持有组合一致才用，体力不影响 key）；还在算就最多等 wait_s 秒；没有 / 出错返回 None（调用方自己算）。"""
    slot = _GAINS_BG.get(_gains_key(setup))
    if slot is None:
        return None
    slot["thread"].join(wait_s)
    return slot["result"]


INTERVAL_CACHE_FILE = "interval_cache.pkl"
STATE_DIR = None  # 本战上下文所在的 trace 目录（note_exam / load_context 设置）：轮间缓存落在这里，续跑读回


def save_interval_cache(obj: dict) -> None:
    """轮间方案 / 账本 / 增益表落盘（进程重启后读回，协调 2026-11-03：第二十二局通信エラー 重启后整套重算 71s + 78s）。"""
    import pickle

    if STATE_DIR is None:
        return
    try:
        path = Path(STATE_DIR) / INTERVAL_CACHE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(obj, protocol=4))
        tmp.replace(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("轮间缓存落盘失败：%s", e)


def load_interval_cache() -> dict | None:
    import pickle

    if STATE_DIR is None:
        return None
    try:
        return pickle.loads((Path(STATE_DIR) / INTERVAL_CACHE_FILE).read_bytes())
    except Exception:  # noqa: BLE001
        return None


def shelf_value(shelf_drinks: list, gains: dict, free: int, spare: int) -> tuple[float, list]:
    """货架上这些饮料 [(key, drink_id, price)] 在 free 个空位、可花 spare P 内的最优买法：值得买（增益 > GAIN_SIG 个标准误）的里取增益和最大的。
    返回 (价值, [key…])。增益按单瓶相加（近似，忽略两瓶之间的交互）。"""
    from itertools import combinations

    good = [(k, d, pr, gains[d][0] - SPARE_P_VALUE * pr) for k, d, pr in shelf_drinks
            if d in gains and gains[d][0] > GAIN_SIG * max(gains[d][1], 1) and gains[d][0] > SPARE_P_VALUE * pr]  # fmt: skip
    best = (0.0, [])
    for n in range(1, min(free, len(good)) + 1):
        for combo in combinations(good, n):
            if sum(x[2] for x in combo) <= spare:
                v = sum(x[3] for x in combo)
                if v > best[0]:
                    best = (v, [x[0] for x in combo])
    return best


# ---------------------------------------------------------------- 轮间买饮料 / 刷新：栏位机会成本 + 社区先验（第二十五局，用户 2026-11-04「瞎买饮料」）
# 用户经协调：4 格有限，买一瓶弱饮料等于放弃后续刷新可能刷到的更好饮料；R2 目标组合 复制 / 爆发 / 抽牌 优先，好印象 / 回复 类只在
# 刷新预算用尽、栏位还空时作为最后填充；偏离先验要模拟 > 2 SE 并记录。
REFRESH_STEP = 10  # 刷新价每刷一次 +10 估（实测 10 → 10 → 20，按偏贵估，继续刷新的价值偏保守）
TARGET_MIN_PRICE = 75  # 最便宜的目标饮料（ブースト / センブリ 75P）：社区「刷新止损」——P 不够「刷新 + 一瓶目标」就不刷


def _good(gains: dict, d: str) -> bool:
    return d in gains and gains[d][0] > GAIN_SIG * max(gains[d][1], 1) and gains[d][0] > SPARE_P_VALUE * drink_price(d)


# 第 17–26 局轮间 / 相談货架实测（45 个货架、117 瓶，.temp/simcov/shelf_freq.py；协调 2026-11-04 第二十六局：等概率假设高估了刷到目标的机会）。
# 每格按（次数 + 1）加权（拉普拉斯平滑，没见过的也留一点概率）。抽牌 センブリ 只见过 2 次（1.7%），爆发 ブースト 7 次（6.0%）。
SHELF_COUNTS = {"pdrink_02-2-006": 16, "pdrink_00-3-001": 12, "pdrink_02-3-010": 11, "pdrink_00-2-004": 10, "pdrink_02-3-006": 10, "pdrink_02-3-007": 9, "pdrink_00-2-003": 8, "pdrink_00-3-005": 8, "pdrink_00-2-008": 7, "pdrink_00-2-009": 6, "pdrink_00-1-004": 5, "pdrink_02-1-008": 5, "pdrink_02-1-006": 3, "pdrink_00-2-001": 3, "pdrink_00-1-001": 2, "pdrink_00-2-010": 2}
SHELF_COUNTS_PRIOR = 1


def shelf_weight(d: str) -> float:
    return SHELF_COUNTS.get(d, 0) + SHELF_COUNTS_PRIOR


class RefreshDP:
    """刷新货架的最优停止（精确，商品池 16 种 × 2 格独立，每格按实测频率 shelf_weight = 256 种货架）。只按模拟增益表：
    W(f, s, c)：刚看到一个新货架（f 个空位、可花 s P、下次刷新价 c）的期望价值；C(f, s, c)：现在刷新（或停）的价值 = max(0, −c × 每 P 机会成本 + E W)。
    价值 = 买到的饮料「增益 − 价格 × SPARE_P_VALUE」之和（余 P 按 SPARE_P_VALUE 计，刷新价同样计成本）。增益按单瓶相加（忽略瓶间交互）。"""

    def __init__(self, gains: dict, pool=SHOP_DRINK_POOL, buyable=None):
        """buyable(drink_id) → 刷新预算没用尽时会不会买（drink_step 的规则：弱饮料只在最后补位，不算进继续刷新的价值；
        第二十六局：把 ハーブティー / 烏龍茶 等也算进去，继续刷新的价值一直为正，连刷 5 次 110P 没刷到目标）。"""
        from collections import Counter

        good = {d: (gains[d][0] - SPARE_P_VALUE * drink_price(d), drink_price(d)) for d in pool if _good(gains, d) and (buyable is None or buyable(d))}
        cnt = Counter()
        w = {d: shelf_weight(d) for d in pool}
        total = sum(w.values()) ** 2
        for a in pool:
            for b in pool:
                cnt[tuple(sorted(x for x in (a, b) if x in good))] += w[a] * w[b]
        self.good = good
        self.shelves = [([good[d] for d in k], n / total) for k, n in cnt.items()]
        self._w: dict = {}

    def C(self, f: int, s: int, c: int) -> float:
        if f <= 0 or s < c:
            return 0.0
        return max(0.0, -SPARE_P_VALUE * c + self.W(f, s - c, c + REFRESH_STEP))

    def W(self, f: int, s: int, c: int) -> float:
        key = (f, s, c)
        if key in self._w:
            return self._w[key]
        total = 0.0
        for items, pr in self.shelves:
            best = self.C(f, s, c)
            for i, (v1, p1) in enumerate(items):
                if f >= 1 and p1 <= s:
                    best = max(best, v1 + self.C(f - 1, s - p1, c))
                    for v2, p2 in items[i + 1:]:
                        if f >= 2 and p1 + p2 <= s:
                            best = max(best, v1 + v2 + self.C(f - 2, s - p1 - p2, c))
            total += pr * best
        self._w[key] = total
        return total


REROLL_COMMUNITY_CAP = 3  # 社区默认一次轮间最多刷几次（手册刷新止损：历史反例刷 5 次花 120P）
REROLL_HARD_CAP = True  # 超过 REROLL_COMMUNITY_CAP 一律不刷（不看模拟）。协调 2026-11-04 定默认开：手册「不为单瓶目标无限刷新」是社区先验，
# 增益表里 S0 类（やる気 / 元気）的绝对值还没用专家续打复核；复核可信后再考虑放开成「超过 3 次要专家续打 > 2 SE」


# 目标饮料增益校准（2026-11-04 增益表复核，第二十六局 R2 开局，持有 ホエイ + コーヒー 再加一瓶）：社区续打（增益表口径）低估目标饮料，
# 专家续打 / 社区续打 = 特製 8.42 / 3.74 万、センブリ 8.97 / 5.55 万、ブースト 4.26 / 2.97 万；差距分解（48 世界）里空位补一瓶
# ブースト +7.2 万 ± 2.2 万、ビネガー→特製 +6.3 万 ± 2.6 万，都远高于增益表。开时把这三类的增益（均值和标准误）按比例放大。
TARGET_GAIN_SCALE = {"copy": 2.25, "draw": 1.62, "burst": 1.43}
TARGET_CALIBRATION = True  # 协调 2026-11-04 定默认开（手册 + 用户依据 + 增益复核 / 差距分解数据）
# 硬上限之后：还缺目标饮料、有空位、P 够「刷新 + 一瓶目标」时继续刷新找目标（不拿这笔钱去强化）。第二十七局：3 次刷完剩 170P、空 2 格，
# 100P 强化了 紙吹雪（差距分解 紙吹雪+ +1.5 万 ± 1.6 万，不显著），而一瓶目标约 +6–7 万。协调 2026-11-04 定默认开
REROLL_FOR_TARGETS = True


# 第二版校准（2026-11-04 饮料反事实：第三十 / 三十一局 R2 开局，96 世界配对的单瓶边际 vs 轮间增益表原始值）：
# 特製 实测 +11.2 / +8.5 万 vs 表 1.9 / 1.1 万，ブースト +6.9 / +5.4 万 vs 1.4 / 1.6 万；连同第二十六局复核，三个测点实测 / 原始比值几何平均
# 复制 ≈ ×4.5、爆发 ≈ ×2.9；抽牌只有一个测点，暂留 ×1.62；加行动（第二瓶 ホエイ 实测 +4.4 / +2.8 万 vs 表 3.2 / 2.1 万）≈ ×1.3。
# 行为变化，默认关，验收后由协调 / 总控打开
TARGET_GAIN_SCALE_V2 = {"copy": 4.5, "draw": 1.62, "burst": 2.9, "action": 1.3}
TARGET_SCALE_V2 = True  # 协调 2026-11-04 验收后开（饮料反事实：第 26–31 局轮间重放只变两步，实测边际 +5.3k / +8.9k）


# 第二版的上限（第三十二局：增益表原始值 特製 52,491、ブースト 39,385，乘 ×4.5 / ×2.9 后 236,210 / 114,216，远超实测边际，
# 继续刷新的价值算成 +358,132，刷了 7 次）。倍率只用来把偏低的原始值往上拉，最多拉到实测边际的水平（第三十 / 三十一局反事实：
# 特製 +8.5–11.2 万、ブースト +5.4–6.9 万、第二瓶 ホエイ +2.8–4.4 万；センブリ 第二十六局复核专家口径 +9.0 万）；原始值本来就更高时保留原始值
TARGET_GAIN_CAP_V2 = {"copy": 100_000, "burst": 70_000, "draw": 90_000, "action": 40_000}
TARGET_CAP_V2 = True  # 协调 2026-11-04 开； D35：第二版倍率按实测绝对值封顶。开关默认关，验收后由协调 / 总控打开
# D35 刷新止损：空位 ≥ 2 时，P 不够「刷新 + 一瓶目标 + 一瓶便宜饮料」就不再刷新、先把栏位填满（第三十二局刷 7 次后留 1 格空、剩 50P）。默认关
REROLL_FILL_GUARD = False
CHEAP_DRINK_PRICE = 50


# 价值表校准（协调 2026-11-04「老师化」任务 3）：倍率和上限不再手调，改用价值表专家层 / 社区层的按类比值（tools/value_table/finals.py calibrate，
# 落盘 data/value_tables/drink_scale.json：{"by_class": {类: {"scale", "cap"}}}）。开时取代 TARGET_GAIN_SCALE_V2 / TARGET_GAIN_CAP_V2；
# 表里没有的类不改。行为变化，默认关，fresh 块验收后由协调 / 总控打开
VALUE_TABLE_SCALE = True  # 协调 2026-11-04 价值表验收后开
VALUE_TABLE_FILE = Path(__file__).resolve().parents[3] / "data" / "value_tables" / "drink_scale.json"


@lru_cache(maxsize=1)
def value_table_scale() -> dict:
    try:
        return json.loads(VALUE_TABLE_FILE.read_text(encoding="utf-8")).get("by_class", {})
    except (OSError, ValueError) as e:
        # 开源版：价值表随仓库在 data/value_tables/。缺了它增益没有校准、刷新估值偏高（Windows 第十四局后测试），要大声说
        logger.warning("价值表读不到（%s：%s），轮间饮料增益不校准；检查 data/value_tables/", VALUE_TABLE_FILE, e)
        return {}


def _scale_and_cap(k: str) -> tuple[float, int | None, int | None]:
    """(倍率, 上限, 下限)。下限只有价值表给：社区续打看不到价值的类（回复），按专家实测 −2 SE 兜底。"""
    if VALUE_TABLE_SCALE:
        row = value_table_scale().get(k)
        if row:
            return float(row.get("scale", 1.0)), row.get("cap"), row.get("floor")
    scale = TARGET_GAIN_SCALE_V2 if TARGET_SCALE_V2 else TARGET_GAIN_SCALE
    return scale.get(k, 1.0), (TARGET_GAIN_CAP_V2.get(k) if (TARGET_SCALE_V2 and TARGET_CAP_V2) else None), None


# 轮间回体力目标按价值表（协调 2026-11-04，用户在考虑）：价值表专家层里体力 17 → 19 / 24 / 33 的 R2 增益，按「每 P 回报」逐段往上回，
# 直到下一段低于 HP_VALUE_LINE（分 / P，介于目标饮料 ブースト 约 510 与 やる気 / 回复 类约 200–250 之间）。第 26–31 局：17→19 约 530、
# 19→24 约 590、24→33 约 250 分 / P → 回到 24。只在最大体力和价值表一致（33）时用；比角色目标低时取角色目标。行为变化，默认关
HP_TARGET_VALUE = False
HP_VALUE_LINE = 400
VALUE_TABLE_EXPERT = VALUE_TABLE_FILE.with_name("finals_expert.json")


@lru_cache(maxsize=1)
def _hp_table() -> tuple[int, int, dict] | None:
    """(基准体力, 最大体力, {体力: R2 增益})，读价值表专家层；没有时 None。"""
    try:
        t = json.loads(VALUE_TABLE_EXPERT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    srcs = list(t.get("sources", {}).values())
    if not srcs:
        return None
    base, mx = srcs[0]["hp"], srcs[0]["max_hp"]
    levels = {base: 0.0}
    for name, row in t.get("summary", {}).items():
        if name.startswith("hp:"):
            levels[int(name.split(":")[1])] = float(row["d_mean"])
    return base, mx, levels


def value_hp_target(max_hp: int, default: int, per2: int = 10) -> int:
    if not HP_TARGET_VALUE:
        return default
    tab = _hp_table()
    if tab is None or tab[1] != max_hp:
        return default
    base, _, levels = tab
    cur = base
    for nxt in sorted(h for h in levels if h > base):
        cost = recover_cost(cur, nxt, per2)
        if cost <= 0 or (levels[nxt] - levels[cur]) / cost < HP_VALUE_LINE:
            break
        cur = nxt
    return max(default, cur)


def calibrated_gains(gains: dict) -> dict:
    from hiro.state.knowledge import drink_class

    if not (TARGET_CALIBRATION or VALUE_TABLE_SCALE):
        return gains
    out = {}
    for d, (g, se) in gains.items():
        k = drink_class(d)
        f, cap, floor = _scale_and_cap(k)
        if floor is not None and g < floor:
            out[d] = (floor, max(se, round(floor * 0.2)))
            continue
        if g <= 0 or f == 1.0:
            out[d] = (g, se)
            continue
        target = g * f if cap is None or f < 1.0 else min(g * f, max(g, cap))
        r = target / g
        out[d] = (round(target), round(se * r))
    return out


def drink_step(shelf: list, gains: dict, holding: list, free: int, spare: int, price: int | None, dp: RefreshDP | None = None,
               rerolls: int = 0) -> dict:
    """当前货架 [(index, drink_id, price)] 上下一步：买哪瓶 / 刷新 / 停。
    adv = 这瓶净值 + 买后继续刷新的价值 − 不买继续刷新的价值（栏位和 P 的机会成本，RefreshDP）。
    - 目标饮料（社区 R2 目标组合还缺的：第一瓶复制、两瓶爆发、两瓶抽牌、一瓶加行动）：看到就买，除非模拟显著不划算（adv + 2 SE < 0，记偏离）；
    - 档位 2（やる気 等）：adv > 0 才买；
    - 档位 3–4（回复 / 元気 / 平加 / 好印象…）：刷新预算用尽才补位；预算没用尽时 adv > 2 SE 才买（记偏离）。
    刷新：模拟说继续刷新的价值 > 0，或社区默认（R2 目标没齐、P 够「刷新 + 一瓶目标」）。返回 {kind: buy|reroll|stop, index?, why, deviation?, value}"""
    from hiro.state.knowledge import BURST_MUST, DRAW_CAP, DRAW_CLASSES, DRINK_CLASSES, drink_class

    gains = calibrated_gains(gains)
    dp = dp or RefreshDP(gains, buyable=lambda d: DRINK_CLASSES.get(drink_class(d), DRINK_CLASSES["other"])["tier"] <= 2)
    cls = [drink_class(d) for d in holding if d]
    missing = ("copy" not in cls) or cls.count("burst") < BURST_MUST["r2"] or sum(c in DRAW_CLASSES for c in cls) < DRAW_CAP["r2"]
    TARGET_COUNT = {"copy": 1, "burst": BURST_MUST["r2"], "draw": DRAW_CAP["r2"], "action": 1}
    can_refresh = price is not None and free > 0 and spare >= price
    c_now = dp.C(free, spare, price) if can_refresh else 0.0
    budget_left = can_refresh and spare >= price + TARGET_MIN_PRICE  # 社区刷新止损：P 还够「刷新 + 一瓶目标」
    if REROLL_FILL_GUARD and free >= 2:
        budget_left = budget_left and spare >= price + TARGET_MIN_PRICE + CHEAP_DRINK_PRICE  # 还要留一瓶便宜饮料的钱填空位
    # 继续刷新价值的误差：按会被买的那些饮料增益的平均标准误粗估（同 reroll_ev）
    se_c = statistics.fmean([max(gains[d][1], 1) for d in dp.good]) if dp.good and c_now > 0 else 0.0
    # 社区刷新止损（手册「不为单瓶目标无限刷新」，历史反例刷 5 次 120P；第二十六局刷 5 次 110P 没刷到目标）：
    # 社区默认最多刷 REROLL_COMMUNITY_CAP 次；超过之后只有模拟说继续刷新的价值 > 2 SE 才刷（记偏离）
    capped = rerolls >= REROLL_COMMUNITY_CAP
    community_refresh = budget_left and missing and (not capped or REROLL_FOR_TARGETS)
    refresh_ok = can_refresh and (community_refresh or (c_now > 2 * se_c and not REROLL_HARD_CAP if capped else c_now > 0))
    cands, skipped = [], []
    for idx, d, pr in shelf:
        if free <= 0 or pr > spare or d not in gains:
            continue
        g, se = gains[d]
        se = max(se, 1)
        net = g - SPARE_P_VALUE * pr
        after = dp.C(free - 1, spare - pr, price) if can_refresh else 0.0
        adv = net + after - c_now
        k = drink_class(d)
        tier = DRINK_CLASSES.get(k, DRINK_CLASSES["other"])["tier"]
        # 社区 R2 目标组合：第一瓶复制、两瓶爆发、两瓶抽牌（DRAW_CAP）、加行动 1 瓶；超出的同类按档位 2 的规则（比继续刷新划算才买）
        have = sum(c in DRAW_CLASSES for c in cls) if k in DRAW_CLASSES else cls.count(k)
        target = tier <= 1 and have < TARGET_COUNT.get("draw" if k in DRAW_CLASSES else k, 1)
        name = DRINK_CLASSES.get(k, DRINK_CLASSES["other"])["label"]
        row = {"index": idx, "drink_id": d, "price": pr, "gain": g, "se": se, "adv": round(adv), "class": name, "tier": tier, "after": round(after)}
        if target:
            # 复制不按模拟跳过：社区续打只在手里有大额兑现时喝复制，增益表低估它（第十九局 R2 专家续打 第 3 瓶 特製 +6.6 万 ± 1.8 万，
            # 第二十五局社区续打增益表 +0.1 万 ± 0.6 万）
            sd = math.sqrt(se ** 2 + se_c ** 2)
            if k != "copy" and adv + 2 * sd < 0:
                skipped.append(f"{name} {d} 模拟显著不划算（栏位机会成本后 {adv:+,.0f} ± {sd:,.0f}）")
                continue
            cands.append((0, -g, row, None))
        elif tier <= 2 or k == "unknown":  # 档位 1 超出目标数的同类也按这条
            if _good(gains, d) and adv > 0:
                cands.append((1, -adv, row, None))
        elif _good(gains, d):
            sd = math.sqrt(se ** 2 + se_c ** 2)
            if not budget_left or not refresh_ok:
                cands.append((2, -net, row, None))  # 刷新预算用尽：补位
            elif adv > 2 * sd:
                cands.append((1, -adv, row, f"弱饮料（{name}·{tier}档）刷新预算没用尽就买：栏位机会成本后 {adv:+,.0f} ± {sd:,.0f} > 2 SE"))
    if cands:
        pri, _, row, dev = min(cands, key=lambda x: (x[0], x[1]))
        how = {0: "社区目标饮料（复制 / 爆发 / 抽牌 / 加行动）", 1: "比继续刷新划算", 2: "刷新预算用尽，补位"}[pri]
        # 第二十七局：只写「继续刷新的价值 50,260」像是拿它和 +12,176 比；两侧都写出来（买一瓶只占一格，剩下的格照样能刷）
        why = (f"买 {row['class']}·{row['tier']}档（{how}；R2 增益 {row['gain']:+,} ± {row['se']:,}"
               + ("（目标饮料按专家口径校准）" if TARGET_CALIBRATION and row["tier"] <= 1 else "")
               + f"；买：净值 {row['gain'] - SPARE_P_VALUE * row['price']:+,} + 剩 {free - 1} 格继续刷新 {row['after']:+,}"
               f" 对 不买：剩 {free} 格继续刷新 {c_now:+,.0f}，差 {row['adv']:+,}）")  # fmt: skip
        return {"kind": "buy", "index": row["index"], "why": why, "deviation": dev, "value": c_now, "skipped": skipped}
    if refresh_ok:
        dev = None if community_refresh else (f"已刷 {rerolls} 次（社区止损 {REROLL_COMMUNITY_CAP} 次），模拟继续刷新的价值 {c_now:+,.0f} > 2 SE（{se_c:,.0f}）" if capped
                                              else f"R2 目标已齐 / P 不够目标饮料，按模拟继续刷新（价值 {c_now:+,.0f}）")
        why = (f"刷新估值：继续刷新的价值 {c_now:+,.0f}（剩 {free} 格、可花 {spare}P（不为强化预留）、刷新 {price}P）"
               + ("；社区默认：R2 目标没齐、P 够刷新 + 一瓶目标" if community_refresh else ""))
        return {"kind": "reroll", "why": why, "deviation": dev, "value": c_now, "skipped": skipped}
    why = "饮料栏满" if free <= 0 else (f"刷新不划算（价值 {c_now:+,.0f}），货架上没有值得补位的饮料" if can_refresh else "P 不够刷新，货架上没有值得补位的饮料")
    return {"kind": "stop", "why": why, "deviation": None, "value": c_now, "skipped": skipped}


def reroll_ev(gains: dict, free: int, spare: int, price: int, current_value: float, *, pool=SHOP_DRINK_POOL, n: int = 4000, seed: int = 0) -> dict:
    """刷新一次的价值 = 新货架（每格从商品池等概率抽）的期望最优买法价值 − 当前货架最优价值。刷新价格从 spare 里扣（spare 已扣掉轮间需要量，
    剩下的 P 在最后一个轮间之后没用，所以价格本身不再计价）。返回 {ev, se, p_good}（p_good：新货架至少有一瓶值得买的概率）。"""
    import random as _r

    if free <= 0 or spare < price:
        return {"ev": float("-inf"), "se": 0.0, "p_good": 0.0}
    rng = _r.Random(seed)
    vals = []
    for _ in range(n):
        shelf = [(i, d, drink_price(d)) for i, d in enumerate(rng.choice(pool) for _ in range(INTERVAL_DRINK_SLOTS))]
        vals.append(shelf_value(shelf, gains, free, spare - price)[0])
    mean = statistics.fmean(vals)
    # 误差主要来自增益估计：按「被选中的增益」的标准误粗估（单瓶增益的平均标准误 × 选中概率）
    sel = [d for d in pool if gains[d][0] > GAIN_SIG * max(gains[d][1], 1) and gains[d][0] > SPARE_P_VALUE * drink_price(d)]
    se = statistics.fmean([gains[d][1] for d in sel]) if sel else 0.0
    return {"ev": mean - current_value, "se": se, "p_good": sum(v > 0 for v in vals) / n}
