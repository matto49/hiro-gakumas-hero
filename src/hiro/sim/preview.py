# 来源：digital-hiro src/hiro/sim/preview.py（develop 864e037）
"""动作预演：每个合法动作先在模拟器里按规则执行一遍，给决策层明确的结果与分数激励。

对每个合法动作：
- **即时结果**：在若干确定化世界里执行这一步，比较执行前后的分数、元气、体力、干劲、好印象、剩余行动、手牌数。
  各世界一致的就是确定结果；依赖抽牌等随机性而不一致的，给出范围。抽到哪几张不透露。
  「结束回合」的即时结果包含下回合开始时的结算（エウレカ、持续得分、好印象、道具）。
- **期望终局分**：同一批世界里执行这一步后，用推演启发式打到考试结束，取平均；并给出相对最佳动作的配对差。

确定化世界只重排玩家看不到的信息（山札顺序、未公开的前段回合属性），见 policies.determinize。
"""

import random
import statistics
from dataclasses import asdict, dataclass, field

from hiro.sim.env import clone, fast_apply
from hiro.sim.policies import action_key, card_label, determinize, rollout

FIELDS = (("score", "分数"), ("shield", "元气"), ("hp", "体力"), ("motivation", "干劲"), ("impression", "好印象"))


def _snap(env) -> dict:
    return dict(score=env.score, shield=env.shield, hp=env.hp, motivation=env.motivation, impression=env.impression,
                actions=env.actions, hand=len(env.zones["hand"]), turn=env.turn, ended=env.ended)  # fmt: skip


LAST_STATS: dict = {}  # 最近一次 preview_actions 的统计（preview_ms / worlds_planned / worlds_done / candidates / depth），实机记进 trace


@dataclass
class Preview:
    key: str  # play:卡名 / drink:饮料名 / end_turn
    action: dict  # 引擎动作（第一个匹配实例）
    immediate: dict  # 字段 → 确定值或 [最小, 最大]
    turn_passed: bool
    expected: float
    spread: float
    vs_best: float = 0.0
    vs_best_se: float = 0.0
    samples: list = field(default_factory=list, repr=False)
    preview_ms: float | None = None
    worlds_planned: int | None = None
    worlds_done: int | None = None
    eliminated: str | None = None

    def to_prompt(self) -> dict:
        imm = {}
        for k, name in FIELDS:
            v = self.immediate[k]
            if v == 0:
                continue
            imm[name] = f"{v:+,}" if isinstance(v, int) else f"{v[0]:+,}~{v[1]:+,}（随机）"
        a = self.immediate["actions"]
        imm["之后剩余行动"] = a if isinstance(a, int) else f"{a[0]}~{a[1]}"
        d = self.immediate["hand_drawn"]
        if d != 0:
            imm["抽牌"] = d if isinstance(d, int) else f"{d[0]}~{d[1]}"
        if self.immediate.get("hand_replaced"):
            imm["手牌"] = "全部换掉：现有手牌进弃牌堆，再从山札抽同样张数（抽到什么随机，山札不够时弃牌堆洗回，可能抽回刚弃的牌）"
        label = {"end_turn": "结束回合（含下回合开始的结算）"}.get(self.key, self.key)
        return {
            "动作": label,
            "即时结果": imm or "无变化",
            "期望终局分": round(self.expected),
            "比最佳": round(self.vs_best) if self.vs_best else 0,
            "比最佳标准误": round(self.vs_best_se) if self.vs_best_se else 0,
        }


def _range(values: list):
    lo, hi = min(values), max(values)
    return lo if lo == hi else [lo, hi]


# 回合内搜索的共同随机数（用户 2026-11-03：同回合几张牌的出牌顺序差是确定的，不能被续打噪声淹没）：同一层的各个分支用同一个种子续打，
# 只差出牌顺序的分支之间的差就不再混进两次独立续打的随机差。改变所有预演数值，默认关，等 tools/preview_bench 验收后再开。
CRN_INTURN = False


# ---------- 回合内搜索剪枝（协调 2026-11-02 预演加速一期第 3 项）----------
BEAM = 2  # 回合内每层展开几个出牌（按 community_value 排序）；None = 全展开（现行）
MIN_ACTIONS_TO_SEARCH = 2  # 剩余行动少于这个数时只再看一层（选本回合最好的一张），不再往下穷举


def state_signature(w) -> int:
    """置换表用的局面签名（引擎提供）：标量、牌区（山札按序、其余按集合）、饮料、全部状态（含附魔计数、各卡成长与附魔）。"""
    return w.signature()


def _inturn_candidates(w, beam: int | None) -> list:
    """回合内展开哪些动作：出牌按 community_value 取前 beam 个；结束回合总保留；饮料只保留社区规则此刻会喝的（community_action 选中的那瓶）。"""
    from hiro.sim.policies import community_action, community_value

    legal = w.legal_actions()
    if beam is None:
        return legal
    plays = [a for a in legal if a["kind"] == "play"]
    plays.sort(key=lambda a: -community_value(w, a))
    keep = plays[:beam] + [a for a in legal if a["kind"] == "end_turn"]
    drinks = [a for a in legal if a["kind"] == "drink"]
    if drinks:
        pick = community_action(w, random.Random(0))
        if pick.get("kind") == "drink":
            keep.append(pick)
    return keep or legal[:1]


def best_in_turn_fast(w, rng, depth: int, tt: dict | None = None, beam: int | None = BEAM, min_actions: int = MIN_ACTIONS_TO_SEARCH) -> int:
    """best_in_turn 的剪枝版：置换表（同一世界里不同出牌顺序到同一局面只算一次）+ beam + 剩余行动少时不再穷举。"""
    turn = w.turn
    if w.ended:
        return w.score
    if depth == 0:
        return rollout(w, rng)
    if w.actions < min_actions:
        depth = min(depth, 1)  # 剩余行动少：只再看一层（例如只剩 1 次行动时选最好的一张），不往下穷举
    if tt is None:
        tt = {}
    sig = (state_signature(w), depth)
    if sig in tt:
        return tt[sig]
    best = None
    seen = set()
    for a in _inturn_candidates(w, beam):
        k = action_key(a, w)
        if k in seen:
            continue
        seen.add(k)
        x = clone(w)
        fast_apply(x, a)
        if x.ended or x.turn != turn:
            v = rollout(x, random.Random(rng.random()))
        else:
            v = best_in_turn_fast(x, random.Random(rng.random()), depth - 1, tt, beam, min_actions)
        best = v if best is None else max(best, v)
    tt[sig] = best
    return best


# 实机预演的世界数 / 回合内搜索深度（2026-11-04 世界数曲线，第二十六 / 三十 / 三十一局 R2 开局各 12 世界配对、专家打完整个 R2）：
# 深度 3 × 16 世界（现行）433,708；深度 3 × 64 世界 496,491；深度 1 × 64 世界 495,373（比现行 +61,665 ± 24,640，和深度 3 × 64 只差 −1,118 ± 21,879，
# 耗时约是深度 3 × 64 的 1/6，比现行还便宜）。152 局面 gold（7.5 s 截止）：16/3 regret 1,493、32/3 984、64/3 1,406、64/1 2,150
# （gold 本身是深度 3 × 64 世界慢算，对深度 1 有偏）。建议 32 / 3：整局 +41,329 ± 25,546、gold 最好、每步中位约 1 s。
# 行为变化：默认保持 16 / 3，由协调 / 总控决定
LIVE_WORLDS = 32  # 协调 2026-11-04：152 gold 32/3 regret 984 最好（16/3 1,493），R2 约 +4 万
LIVE_DEPTH = 3

# 关键步固定加预算（协调 2026-11-04「老师化」任务 4；sts-ironclad-agent 测过自适应不如固定加倍）：
# 关键步 = 手里有非 S0 饮料且剩余 ≥ KEY_STEP_DRINK_REM 回合（喝不喝 / 喝哪瓶影响整场）、最后一回合（兑现）。这些步世界数和时间预算都 × KEY_STEP_BUDGET。
# 行为变化，默认关（1 = 不加）；验收：gold dev regret、fresh 整局均值、耗时
KEY_STEP_BUDGET = 1
KEY_STEP_DRINK_REM = 8


def key_step(env) -> str | None:
    """这一步是不是关键步：'drink' / 'cash' / None。"""
    rem = len(env.turns) - env.turn
    if rem <= 1:
        return "cash"
    if rem >= KEY_STEP_DRINK_REM and env.drinks:
        from hiro.state.knowledge import STAT_DRINK_CLASSES, drink_class

        if any(drink_class(d) not in STAT_DRINK_CLASSES for d in env.drinks.values()):
            return "drink"
    return None


def live_budget(env, worlds: int, deadline_s: float | None) -> tuple[int, float | None, str | None]:
    """实机每步的世界数 / 时间预算：关键步按 KEY_STEP_BUDGET 放大。返回 (worlds, deadline_s, 关键步原因)。"""
    why = key_step(env) if KEY_STEP_BUDGET > 1 else None
    if why is None:
        return worlds, deadline_s, None
    return worlds * KEY_STEP_BUDGET, (deadline_s * KEY_STEP_BUDGET if deadline_s else deadline_s), why


SEARCH = "full"  # full：现行 best_in_turn；fast：best_in_turn_fast（剪枝）。预演入口按这个选


def search_in_turn(w, rng, depth: int, drunk: int = 0) -> int:
    return best_in_turn_fast(w, rng, depth) if SEARCH == "fast" else best_in_turn(w, rng, depth, drunk=drunk)


# 回合内搜索的饮料分支（第三十局：R2 开局 4 瓶饮料时深度 3 每个世界 3.2 s，7.5 s 只算完 1–6 个世界；没有饮料时 0.1 s）。行为变化，默认关
INTURN_STAT_AUTO = True  # 协调 2026-11-04 D29 验收后开； S0 类（纯加数值）饮料在搜索里直接喝、不作分支（同回合先喝后喝结果相同，第三十局 R2 T1 96 世界配对 +0 ± 0）
INTURN_TT = True  # 协调 2026-11-04 D29 验收后开； 置换表：同一世界里不同出牌顺序到达同一局面（state_signature + 剩余深度）只算一次（不剪分支）
INTURN_DRINK_BUDGET = True  # 协调 2026-11-04 D29 验收后开；绑定：若 D8 撤掉实机 drink_budget，这个开关要跟着关； 搜索里每回合最多再喝 1 瓶非 S0 饮料（剩余 > 4 回合时；卡手时抽牌类不限），和实机专家的 drink_budget 一致


def _live_drink_budget() -> bool:
    """INTURN_DRINK_BUDGET 和实机专家的 drink_budget 绑定（协调 2026-11-04）：实机 exam_policy.EXPERT_DRINK_RULE 关掉（D8 撤 drink_budget）时，
    搜索里的饮料预算自动跟着关；R1d（抽牌类不限）时搜索里同样不限抽牌类（_drink_branch_ok 卡手判断之外再放开）。"""
    from hiro.produce import exam_policy

    return exam_policy.EXPERT_DRINK_RULE in ("R1", "R1d")


def _stat_drinks(w, legal) -> list:
    from hiro.sim.policies import _is_stat_drink

    return [a for a in legal if a["kind"] == "drink" and _is_stat_drink(w, a)]


def _drink_branch_ok(w, a, legal, drunk: int) -> bool:
    """INTURN_DRINK_BUDGET：这一路已经喝过非 S0 饮料、剩余 > 4 回合时不再展开饮料（卡手时抽牌类除外）。"""
    if a["kind"] != "drink" or drunk < 1 or len(w.turns) - w.turn <= 4:
        return True
    from hiro.state.knowledge import DRAW_CLASSES, drink_class

    from hiro.produce import exam_policy

    stuck = not any(x["kind"] == "play" and not w.definition(x["id"]).get("trouble") for x in legal)
    if exam_policy.STUCK_EMPTY_MAX_REM is not None and len(w.turns) - w.turn > exam_policy.STUCK_EMPTY_MAX_REM:
        stuck = False  # 只作数据的变体：空手豁免只在剩余回合 ≤ N 时放行（和实机 _budget_exempt 一致）
    k = drink_class(w.drinks[a["id"]])
    # D37：实机 STUCK_FETCH_EXEMPT 开时搜索里检索类同样豁免（STUCK_BY_PREVIEW 要预演值，搜索里没有，不跟）
    return stuck and (k in DRAW_CLASSES or (exam_policy.STUCK_FETCH_EXEMPT and k == "fetch"))


def best_in_turn(w, rng, depth: int, cache: dict | None = None, drunk: int = 0) -> int:
    """本回合剩余出牌由玩家自己选：在本回合内穷举后续动作（按卡名去重），回合结束后交给推演启发式。

    每个确定化世界各自搜索（PIMC）：同一世界里抽到的牌对后续选择可见，有轻微乐观偏差，但比把本回合的选择
    交给粗糙启发式准确得多（例如「先成長痛再终结」这类同回合组合）。
    drunk：这一路本回合已经喝过的非 S0 饮料数（INTURN_DRINK_BUDGET 用）。"""
    turn = w.turn
    if w.ended:
        return w.score
    if depth == 0:
        return rollout(w, rng)
    if INTURN_TT:
        if cache is None:
            cache = {}
        sig = (state_signature(w), depth, drunk)
        if sig in cache:
            return cache[sig]
    legal = w.legal_actions()
    if INTURN_STAT_AUTO:
        st = _stat_drinks(w, legal)
        if st:
            x = clone(w)
            fast_apply(x, st[0])  # 不耗深度：S0 类饮料什么时候喝结果都一样
            return rollout(x, rng) if (x.ended or x.turn != turn) else best_in_turn(x, rng, depth, cache, drunk)
    best = None
    seen = set()
    shared = rng.random() if CRN_INTURN else None
    for a in legal:
        k = action_key(a, w)
        if k in seen:
            continue
        seen.add(k)
        if INTURN_DRINK_BUDGET and _live_drink_budget() and not _drink_branch_ok(w, a, legal, drunk):
            continue
        x = clone(w)
        fast_apply(x, a)
        s = shared if shared is not None else rng.random()
        if x.ended or x.turn != turn:
            v = rollout(x, random.Random(s))
        else:
            d2 = drunk + (a["kind"] == "drink" and not (INTURN_STAT_AUTO and _stat_drinks(w, [a])))
            v = best_in_turn(x, random.Random(s), depth - 1, cache, drunk=d2)
        best = v if best is None else max(best, v)
    if INTURN_TT:
        cache[sig] = best
    return best


def preview_actions(env, meta: dict | None, *, worlds: int = 16, seed: int = 0, depth: int = 3, deadline: float | None = None) -> list[Preview]:
    """depth：第一步之后，本回合内继续穷举的步数（0 表示直接交给推演启发式）。
    deadline：time.monotonic() 的截止时刻。按「世界」一轮一轮算（每轮把所有动作都算一遍）；第一轮总要每个动作都有一个样本，
    但截止时间到了以后，第一轮剩下的动作只用续打估（不做回合内穷举）；之后每个动作前都看截止，
    过了就丢掉没算完的那一轮、停在已算完的轮数；None 时算满 worlds 轮，结果与不分轮完全相同（每个动作 × 世界用独立的 Random(world_seed)）。"""
    import time

    t_start = time.monotonic()
    rng = random.Random(seed)
    world_seeds = [rng.randrange(2**31) for _ in range(worlds)]
    uniq = {}
    for a in env.legal_actions():
        uniq.setdefault(action_key(a, env), a)
    before = _snap(env)
    acc = {key: {"finals": [], "deltas": {k: [] for k, _ in FIELDS}, "acts": [], "drawn": [], "replaced": False, "passed": False} for key in uniq}
    done = 0  # 完整算完的轮数
    for n, ws in enumerate(world_seeds):
        if deadline is not None and n > 0 and time.monotonic() > deadline:
            break
        cut = False
        for key, action in uniq.items():
            if deadline is not None and n > 0 and time.monotonic() > deadline:
                cut = True  # 这一轮没算完：丢掉这一轮已算的部分，各动作样本数保持一致（配对差需要）
                break
            a = acc[key]
            r = random.Random(ws)
            w = determinize(env, meta, r)
            kept = set(w.zones["hand"]) - ({action["id"]} if action["kind"] == "play" else set())
            fast_apply(w, action)
            after = _snap(w)
            for k, _ in FIELDS:
                a["deltas"][k].append(after[k] - before[k])
            a["acts"].append(after["actions"])
            # 打出的牌离手算 -1，其余手牌增量就是抽到/检索到的张数
            hand_after = list(w.zones["hand"]) if after["turn"] == before["turn"] and not after["ended"] else []
            if kept and hand_after and not kept & set(hand_after):
                # 手札を全部入れ替える（ミックススムージー）：手牌数不变，但原来的手牌一张不剩；抽到的就是现在整手
                a["replaced"] = True
                a["drawn"].append(len(hand_after))
            else:
                a["drawn"].append(after["hand"] - before["hand"] + (1 if action["kind"] == "play" else 0))
            a["passed"] = a["passed"] or after["turn"] != before["turn"] or after["ended"]
            moved = after["turn"] != before["turn"] or after["ended"]
            if moved or depth <= 0:
                a["finals"].append(rollout(w, r))
            elif n == 0 and deadline is not None and time.monotonic() > deadline:
                # 第一轮超时（第十八局 -r1 第 273 步：候选多，第一轮算了 19.4 秒）：剩下的候选只用续打估，不做回合内穷举
                a["finals"].append(rollout(w, r))
                a["first_round_cut"] = True
            else:
                root_drink = action["kind"] == "drink" and not _stat_drinks(env, [action])
                a["finals"].append(search_in_turn(w, r, depth, drunk=int(root_drink)))
        if cut:
            for a in acc.values():
                for lst in (a["finals"], a["acts"], a["drawn"], *a["deltas"].values()):
                    del lst[done:]
            break
        done += 1
    out = []
    for key, action in uniq.items():
        a = acc[key]
        imm = {k: _range(v) for k, v in a["deltas"].items()}
        imm["actions"] = _range(a["acts"])
        imm["hand_drawn"] = 0 if a["passed"] else _range(a["drawn"])
        imm["hand_replaced"] = a["replaced"]
        finals = a["finals"]
        p = Preview(key, action, imm, a["passed"], statistics.fmean(finals), statistics.pstdev(finals), samples=finals)
        if a.get("first_round_cut"):
            p.first_round_cut = True  # 第一轮超时：只用续打估（没做回合内穷举）
        out.append(p)
    best = max(out, key=lambda p: p.expected)
    ms = round((time.monotonic() - t_start) * 1000, 1)
    for p in out:
        diffs = [a - b for a, b in zip(p.samples, best.samples)]
        p.vs_best = statistics.fmean(diffs)
        p.vs_best_se = statistics.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else 0.0
        p.preview_ms, p.worlds_planned, p.worlds_done = ms, worlds, done  # 可观测：这一步总耗时、计划 / 实际算完的世界数
    LAST_STATS.update(preview_ms=ms, worlds_planned=worlds, worlds_done=done, candidates=len(out), depth=depth)
    return sorted(out, key=lambda p: -p.expected)


def slot_previews(previews: list[Preview], game) -> dict:
    """引擎动作键 → 决策层合法动作字符串（play:槽位 / drink:槽位 / skip），同名卡的每个槽位共享同一预演。"""
    env = game.env
    by_key = {p.key: p for p in previews}
    result = {}
    for slot, inst in enumerate(game._hand):
        k = "play:" + card_label(env.definition(inst))
        if k in by_key:
            result[f"play:{slot}"] = by_key[k]
    for slot, d in enumerate(game._drinks):
        k = "drink:" + env.drink_definitions[env.drinks[d]]["name"]
        if k in by_key:
            result[f"drink:{slot}"] = by_key[k]
    if "end_turn" in by_key:
        result["skip"] = by_key["end_turn"]
    return result


def prompt_block(slots: dict) -> list[dict]:
    rows = []
    for legal, p in sorted(slots.items(), key=lambda kv: -kv[1].expected):
        row = p.to_prompt()
        row["legal_action"] = legal
        rows.append(row)
    return rows


__all__ = ["Preview", "preview_actions", "slot_previews", "prompt_block", "asdict"]
