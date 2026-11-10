# 来源：digital-hiro src/hiro/sim/track.py（develop 864e037）
"""存档读不到时，用模拟器把最近一次读到的局面按实际执行的动作推下去（专家续推）。

起因：main8 本战 R1（reports/runs/20261101-055854）第 3 回合起存档每次都投影失败（Physical card occurs in multiple zones），
28 步里只有 3 步由专家出牌。存档只在出牌瞬间写、而且常常读不了，但模拟器里已经有完整局面：按执行过的动作推进即可，
隐藏的只有抽牌顺序，而抽到哪几张在画面上是公开的。

每一步先用画面核对，对得上才把它当作这一步的局面：
- 剩余回合：画面的大数字不含追加回合（追加回合单独显示「+1」，main8 R1 截图），= 模拟器剩余回合 − 追加回合数；
- 体力、元气（画面读到时）必须一致；
- 手牌（卡 ID 的多重集合）：模拟器里抽到的是随机的。不一致时把画面上新出现的牌放到山札顶，从上一步的局面重放这个动作
  （回合开始的「手札をすべてレッスン中強化」等按真实抽到的牌结算）；仍不一致（弃牌洗回、随机位置生成的眠気）时
  直接把多出来的手牌与山札 / 弃牌里画面上的那张互换。手牌顺序按画面排。
对不上就返回原因，调用方丢掉续推，等下一次存档读到再建。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hiro.sim.env import SimGame, clone, fast_apply


def def_card_id(key: str) -> str:
    """定义键 'p_card-xx@1:hash' → 卡 ID。"""
    return key.split("@", 1)[0]


def def_upgrade(key: str) -> int | None:
    try:
        return int(key.split("@", 1)[1].split(":", 1)[0])
    except (IndexError, ValueError):
        return None


@dataclass
class Screen:
    """画面读数（只用公开信息）。hand：每个槽位的 (卡 ID, 强化次数或 None)，顺序同画面。"""

    remaining: int | None
    hp: int | None
    genki: int | None
    hand: list[tuple[str | None, int | None]]
    score: int | None = None
    motivation: int | None = None  # やる気 / 好印象：识别读到时用来在同名卡的不同版本之间挑（读错时不丢续推）
    impression: int | None = None


@dataclass
class SyncResult:
    game: SimGame | None
    reason: str | None = None  # 对不上的原因：remaining / hp / genki / hand / hand_unread / ended / sim_error
    notes: list[str] = field(default_factory=list)  # 对上了但值得记下的（强化次数不同、分数不同、手牌靠互换补齐）


class SimTracker:
    """game：最近一次核对过的局面（这一步决策用的那一份）。extra_turns：建立时存档里的追加回合数（scalars.extraTurn）。"""

    def __init__(self, game: SimGame, extra_turns: int, meta: dict | None):
        self.game = game
        self.meta = meta
        self.extra0 = extra_turns - getattr(game.env, "extra_turns_added", 0)
        self.pending: dict | None = None  # 已执行、还没和画面核对的动作
        self.steps = 0  # 自上次读到存档以来续推了几步

    def executed(self, action: dict) -> None:
        self.pending = action

    def extra_turns(self, env) -> int:
        return self.extra0 + getattr(env, "extra_turns_added", 0)

    def screen_remaining(self, env) -> int:
        return len(env.turns) - env.turn - self.extra_turns(env)

    def sync(self, screen: Screen) -> SyncResult:
        if any(cid is None for cid, _ in screen.hand):
            return SyncResult(None, "hand_unread")
        base = self.game.env
        first = best = None
        for vbase, seq, vnote in self._candidates(base, self.pending):
            notes: list[str] = [vnote] if vnote else []
            try:
                env = self._advance(vbase, seq, screen.hand, notes)
            except Exception as e:  # 模拟器推进出错：不续推
                env, why, diffs, notes = None, "sim_error", {}, notes + [f"{type(e).__name__}: {e}"]
            else:
                why, diffs = ("hand", {}) if env is None else ("ended", {}) if env.ended else self._check(env, screen, notes)
            if why is not None:
                first = first or SyncResult(None, why, notes)
                continue
            off = {f: v for f, v in diffs.items() if f != "score"}  # 分数只按画面修正，不参与挑候选
            cost = (len(off), sum(abs(v - getattr(env, f)) for f, v in off.items()))
            if best is None or cost < best[0]:
                best = (cost, env, diffs, notes)
            if not off:
                break
        if best is None:
            return first
        _, env, diffs, notes = best
        for f, v in diffs.items():
            if f in ("motivation", "impression") and abs(v - getattr(env, f)) > SOFT_TOL:
                notes.append(f"{FIELD_NAMES[f]}：模拟器 {getattr(env, f)} ≠ 画面 {v}，差太多，按模拟器（可能是读错）")
                continue
            # 小误差（引擎取整、定制细节）与读数：采用画面值继续
            notes.append(f"{FIELD_NAMES[f]}：模拟器 {getattr(env, f)} → 画面 {v}")
            setattr(env, f, v)
        _order_hand(env, screen.hand, notes)
        game = SimGame(env, self.game.exam, self.game.profile)
        game.memory = self.game.memory
        self.game, self.pending = game, None
        self.steps += 1
        return SyncResult(game, None, notes)

    def _candidates(self, base, action):
        """(起点局面, 动作序列, 说明)。每个版本都试「只执行这个动作」与「再加回合结束」：行动用完时游戏自动结束回合，
        画面已经是下一回合，模拟器要显式 end_turn（main8 R2：夢色リップ 结算后行动 0，下一帧就是第 3 回合）。
        还没执行动作（刚读到存档）时同样试「不动」与「回合结束」。"""
        for vbase, act, vnote in self._variants(base, action):
            seq = [act] if act is not None else []
            probe = clone(vbase)
            for a in seq:
                fast_apply(probe, a)
            if probe.ended:
                yield vbase, seq, vnote
                continue
            auto = not [a for a in probe.legal_actions() if a["kind"] != "end_turn"]
            with_end = (seq + [END_TURN], "；".join(filter(None, [vnote, "行动用完，游戏自动结束回合"])))
            for s, n in ([with_end, (seq, vnote)] if auto else [(seq, vnote), with_end]):
                yield vbase, s, n

    def _variants(self, base, action):
        """先按原样推；出的牌在画面上分不出版本（同卡 ID、同强化，定制不同）时，再试把它换成山札 / 弃牌里的另一版本：
        抽牌时模拟器可能拿了另一份（离线回放种子 1：元气差 5）。"""
        yield base, action, None
        if action is None or action.get("kind") != "play":
            return
        key = base.cards[action["id"]]
        seen = {key}
        for zone in ("deck", "grave"):
            for inst in base.zones[zone]:
                k = base.cards[inst]
                if k in seen or def_card_id(k) != def_card_id(key) or def_upgrade(k) != def_upgrade(key):
                    continue
                seen.add(k)
                alt = clone(base)
                hand, pile = alt.zones["hand"], alt.zones[zone]
                hand[hand.index(action["id"])], pile[pile.index(inst)] = inst, action["id"]
                yield alt, dict(action, id=inst), f"按画面改用同名的另一版本：{k}"

    def _check(self, env, screen: Screen, notes: list[str]) -> tuple[str | None, dict]:
        """(对不上的原因或 None, 采用画面值的字段 {属性: 画面值})。剩余回合、手牌、体力差 >1、元气差超过 max(2, 3%) 算对不上；
        更小的差（引擎取整：main8 R2 实机第 5 回合开始元气 55 vs 54）与やる気 / 好印象 / 分数的差按画面值修正。"""
        diffs: dict = {}
        if screen.remaining is not None:
            expect = self.screen_remaining(env)
            # 最后的追加回合画面显示 0 还是 1 没有实测，两者都接受
            if screen.remaining != expect and not (expect <= 0 and screen.remaining in (0, 1)):
                notes.append(f"剩余回合：模拟器 {expect}（+{self.extra_turns(env)}）≠ 画面 {screen.remaining}")
                return "remaining", diffs
        if Counter(_ids(env, env.zones["hand"])) != Counter(cid for cid, _ in screen.hand):
            return "hand", diffs
        for f, attr, tol in (("hp", "hp", lambda v: 1), ("genki", "shield", lambda v: max(2, v * 3 // 100))):
            v = getattr(screen, f)
            if v is None or v == getattr(env, attr):
                continue
            if abs(v - getattr(env, attr)) > tol(v):
                notes.append(f"{FIELD_NAMES[attr]}：模拟器 {getattr(env, attr)} ≠ 画面 {v}")
                return f, diffs
            diffs[attr] = v
        for f in ("motivation", "impression"):
            v = getattr(screen, f)
            if v is not None and v != getattr(env, f):  # 都参与挑候选；差太多时多半是读错，不采用（见 sync）
                diffs[f] = v
        if screen.score is not None and screen.score != env.score:
            diffs["score"] = screen.score
        return None, diffs

    def _advance(self, base, actions: list[dict], screen_hand: list, notes: list[str]):
        """按画面推进一步：先原样推；手牌不同时把画面上新出现的牌放到山札顶重放；回合开始「随机强化 1 张」之类的随机效果
        再换几个随机种子重放，取手牌与强化次数都和画面一致的那次。都不完全一致时取卡 ID 一致的第一次，强化次数按画面改。"""
        from gakumas_exam.rng import NativeRandom

        want = [cid for cid, _ in screen_hand]
        tries = []
        env = clone(base)
        _apply_all(env, actions)
        tries.append(env)
        if actions and _match(env, screen_hand) < 2:
            before = set(base.zones["hand"])
            kept = Counter(_ids(env, [i for i in env.zones["hand"] if i in before]))
            need = Counter(want) - kept
            top, deck = [], list(base.zones["deck"])
            for cid, n in need.items():
                for inst in [i for i in deck if def_card_id(base.cards[i]) == cid][:n]:
                    top.append(inst)
                    deck.remove(inst)
            for k in range(RESEED_TRIES + 1):
                replay = clone(base)
                replay.zones["deck"] = top + deck
                if k:
                    replay.rng = NativeRandom((base.rng.state + k * 0x9E3779B9) % 2**32)
                _apply_all(replay, actions)
                tries.append(replay)
                if _match(replay, screen_hand) == 2:
                    break
        best = max(tries, key=lambda e: _match(e, screen_hand))  # 并列取最早的（原样推进优先）
        score = _match(best, screen_hand)
        if best is not tries[0]:
            notes.append(f"按画面重放抽牌 / 随机效果（第 {tries.index(best)} 次）")
        if score >= 1:
            return best
        # 仍不同：多出来的手牌与山札 / 弃牌里画面上的那张互换（近似：不重放抽牌触发）
        return _swap_in(clone(best), want, notes)


END_TURN = {"kind": "end_turn"}
SOFT_TOL = 3  # やる気 / 好印象 与画面差这么多以内时采用画面值
FIELD_NAMES = {"hp": "体力", "shield": "元气", "motivation": "やる気", "impression": "好印象", "score": "分数"}
RESEED_TRIES = 12  # 随机效果重放的种子数（每次约 1 ms 量级）


def _match(env, screen_hand) -> int:
    """0 = 手牌卡 ID 不同；1 = 卡 ID 一致、强化次数有不同；2 = 都一致（画面读不到强化的槽位不比）。"""
    hand = [env.cards[i] for i in env.zones["hand"]]
    if Counter(def_card_id(k) for k in hand) != Counter(cid for cid, _ in screen_hand):
        return 0
    have = Counter((def_card_id(k), def_upgrade(k)) for k in hand)
    for cid, up in screen_hand:
        if up is None:
            continue
        if have[(cid, up)] <= 0:
            return 1
        have[(cid, up)] -= 1
    return 2


def _apply_all(env, actions) -> None:
    for a in actions:
        fast_apply(env, a)


def _ids(env, insts) -> list[str]:
    return [def_card_id(env.cards[i]) for i in insts]


def _swap_in(env, want: list[str], notes: list[str]):
    hand = env.zones["hand"]
    extra = Counter(_ids(env, hand)) - Counter(want)
    missing = Counter(want) - Counter(_ids(env, hand))
    out = []
    for i in hand:
        if extra[def_card_id(env.cards[i])] > 0:
            out.append(i)
            extra[def_card_id(env.cards[i])] -= 1
    swaps = []
    for cid, n in missing.items():
        for _ in range(n):
            src = next((z for z in ("deck", "grave") if any(def_card_id(env.cards[i]) == cid for i in env.zones[z])), None)
            if src is None or not out:
                notes.append(f"手牌补不齐：画面上的 {cid} 在模拟器的山札 / 弃牌里找不到")
                return None
            inst = next(i for i in env.zones[src] if def_card_id(env.cards[i]) == cid)
            old = out.pop()
            env.zones[src][env.zones[src].index(inst)] = old
            hand[hand.index(old)] = inst
            swaps.append(f"{cid}←{src}")
    notes.append(f"手牌互换补齐：{swaps}")
    return env


def _order_hand(env, screen_hand, notes: list[str]) -> None:
    """模拟器手牌按画面顺序排（决策的槽位与点击一致）；强化次数与画面不同时记一笔（画面读不到强化时不比）。"""
    left = list(env.zones["hand"])
    ordered = []
    for cid, up in screen_hand:
        cands = [i for i in left if def_card_id(env.cards[i]) == cid]
        exact = [i for i in cands if up is None or def_upgrade(env.cards[i]) == up]
        inst = (exact or cands)[0]
        if not exact:
            # 随机强化重放也没对上：按画面改这张的强化次数（定义库里有这个版本时）
            keys = [k for k in env.definitions if def_card_id(k) == cid and def_upgrade(k) == up]
            if keys:
                notes.append(f"强化次数按画面改：{env.cards[inst]} → {keys[0]}")
                env.cards[inst] = keys[0]
            else:
                notes.append(f"强化次数：模拟器 {env.cards[inst]} ≠ 画面 +{up}（定义库里没有这个版本，未改）")
        ordered.append(inst)
        left.remove(inst)
    env.zones["hand"] = ordered + left
