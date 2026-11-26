"""偶像之路出牌：Rust GNRPA 搜索（用户 11-14：「偶像之塔这里我们用 gnrpa 去搜索吧，不要强制性使用 uinet」）。

bench Algorithm 接口（hiro.bench_bridge 的 tower 场次按 HIRO_TOWER_ALGO 加载，也能直接在 gakumas-bench 上跑）：
  begin(ctx)：ctx.loadout（整份偶像之路编成）→ 我们的引擎开局（hiro.search.tower.tower_env）；目标 = 三星（名次按对手分数：
              ctx.loadout["rivals"] / 画面读到的优先，没有用主数据区间上限），26 / 27 层带通关条件。
  decide(ctx)：引擎局面按 ctx.obs（UIObs，画面读数）对齐——手牌（按画面顺序）、山札 / 弃牌 / 除外（按卡 id 对实例，同区优先）、
              分数 / 体力 / 元気 / やる気 / 好印象 / 饮料 / 回合；ctx.known_draws（用户 11-14：同一编成重试可以用上一次画面上看到的
              抽牌顺序；只用第一轮，洗牌后会分叉）有就把还没抽到的那段排在山札最前、设成已知前缀；然后 gakumas_exam_rs.search（三星目标）→ 动作；
              这个动作也在我们的引擎里执行，状态 / 附魔由引擎演化。
不读种子、不读存档；ctx.worlds（GKMS 世界）不用。出错就抛异常，由调用方退回 uinet（写明原因）；Rust 后端没有 search / plan_many 时构造就抛（加载失败 → 退回 uinet）。
"""

from __future__ import annotations

import collections
import json
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger("hiro.search.tower")


def _status_maps() -> tuple[dict, dict]:
    """UIObs 状态名 → (引擎状态类, 数量在 turn?)：synced = 画面上没有就删（数值类 / 好印象 / 好調，画面读数可靠）；
    flags = 带剩余回合的开关（画面给了有限回合才改，不删）。名字重复（stance_lock 对应几个类）的不收。"""
    from hiro.bench_bridge import uiobs as U

    synced = {k: (c, False) for c, k in U.VALUE_KIND.items()}
    synced.update({k: (c, True) for c, k in U.TURN_AMOUNT_KIND.items()})
    synced["parameter_buff"] = ("ParameterBuffStatusEffect", True)
    names = list(U.FLAG_KIND.values())
    flags = {k: (c, True) for c, k in U.FLAG_KIND.items() if names.count(k) == 1}
    return synced, flags


def sync_statuses(rx, status: list) -> None:
    """画面状态（UIObs status：[名字, 数值, 剩余回合]）覆盖进引擎（PyExam.override_status）。只同步「一种状态一条」的
    数值类 / 回合类：手毬 16 层集中（lesson_buff）画面 52、引擎还是 7，好調引擎里没有，搜索按错的状态打（协调 11-17）。"""
    synced, flags = _status_maps()
    shown = {s[0]: s for s in status}
    for name, (cls, by_turn) in synced.items():
        s = shown.get(name)
        n = int(round(float(s[1]))) if s else 0  # noqa: RUF046
        try:  # noqa: SIM105
            rx.override_status(cls, by_turn, n)
        except KeyError:
            pass
    for name, (cls, by_turn) in flags.items():  # noqa: B007
        s = shown.get(name)
        if s and len(s) > 2 and 0 < int(s[2]) < 99:
            try:  # noqa: SIM105
                rx.override_status(cls, True, int(s[2]))
            except KeyError:
                pass


def allocate_turns(weights: dict, n: int) -> list[int]:
    """n 个回合按三属性权重按比例分配（最大余数法），返回属性列表（1 Vo / 2 Da / 3 Vi，同属性挨着，搜索里每个世界再打乱）。"""
    tot = sum(float(v) for v in weights.values() if v) or 0.0
    if n <= 0 or tot <= 0:
        return []
    raw = {a: n * float(v) / tot for a, v in weights.items()}
    cnt = {a: int(x) for a, x in raw.items()}
    for a in sorted(raw, key=lambda a: -(raw[a] - cnt[a]))[: n - sum(cnt.values())]:
        cnt[a] += 1
    return [a for a in sorted(cnt) for _ in range(cnt[a])]


NEWER_OBJECTIVE = {"pass_rank", "score_cap", "clear_score"}  # 按场次的目标（11-17 加）：旧 Rust 包没有

# 跨步热启动强度（协调 11-15 批）：bench 同题同种子每步 5 秒，26 层三星 2/8 → 5/8、均分 38.2 万 → 51.4 万（8 局逐局都高），
# 27 层三星 2/8 → 2/8、均分 101.8 万 → 104.0 万（不降）。0 = 关
WARM = 2.0
STEP_SECONDS = 5.0  # 每步搜索秒数（用户 11-14 定 5 秒）；ctx.budget 有就用它


@dataclass
class Decision:
    action: tuple
    meta: dict = field(default_factory=dict)


def known_prefix(known: list[str], deck_ids: list[str], start: int = 0) -> tuple[int, list[str]]:
    """已知抽牌顺序 known（编成牌组的卡 id，按抽到先后）和现在的山札 deck_ids（画面读到的，可能有生成卡）对位：
    找最小的 k ≥ start，使 known[k:] 开头那一段（最多山札里编成牌的张数）都在山札里、并且 known 没到头时正好就是山札里的编成牌
    → (k, 那一段)。用山札内容对位，不靠数手牌：生成卡进手牌、抽到和刚打出的同名卡都不会数错（known 由调用方截到第一轮，洗牌后不当已知）。对不上 / 已知的抽完了返回 (start, [])。"""
    ids = set(known)
    left = collections.Counter(c for c in deck_ids if c in ids)  # 山札里的编成牌（生成卡不算）
    n = sum(left.values())
    for k in range(max(0, start), len(known)):
        seg = known[k : k + n]
        got = collections.Counter(seg)
        if got - left:
            continue
        if len(seg) == n and got != left:
            continue
        return k, seg
    return start, []


class TowerGnrpa:
    name = "hiro-tower-gnrpa"
    title = "hiro 偶像之路 GNRPA（Rust）"
    privileged = False
    needs_gkms = False
    sees_seed = False

    def __init__(
        self,
        seconds: float | None = None,
        worlds: int = 8,
        threads: int = 0,
        warm: float = WARM,
        greedy: float = 0.0,
        sync_status: bool = False,
        **params,
    ):
        try:
            import gakumas_exam_rs as rs
        except ImportError as e:
            raise RuntimeError(f"没装 Rust 后端 gakumas_exam_rs（{e}）") from None
        if getattr(rs, "__file__", None) is None and hasattr(
            rs, "__path__"
        ):  # 命名空间包：只有 __path__，没有 __file__
            # N45（G3 第 3 轮 11-26 Windows）：没编译成时 sys.path 上的 vendor/gakumas-player 让 import 拿到 Rust 源码目录
            # （空的命名空间包），原来报成「是旧版本」，其实是没装
            raise RuntimeError("没装 Rust 后端 gakumas_exam_rs（只找到源码目录，没有编译好的包）")
        missing = [f for f in ("search", "plan_many") if not hasattr(rs, f)]
        if missing:
            raise RuntimeError(f"Rust 后端 gakumas_exam_rs 是旧版本，没有 {' / '.join(missing)}（要带搜索的新包）")
        # 统计计数：begin 每场重置；构造时先建一份，begin 中途失败（没编成 / 主数据缺失）后执行层回调 undo_last 也不会报错
        self.stats = collections.Counter()
        self.seconds = None if seconds is None else float(seconds)
        self.worlds, self.threads = int(worlds), int(threads)
        # warm：跨步热启动强度（上一步各世界最好路线的权重 × warm 作这一步的初始策略；0 = 关）；greedy：眼前得分先验（0 = 关）
        self.warm, self.greedy = float(warm), float(greedy)
        # sync_status：每步把画面上的数值类 / 回合类状态（集中、好調等）覆盖进引擎。缺省关：手毬 16 层实机画面的集中读数
        # 18 → 1 → 2 → 30 跳动、消耗增减方向和引擎相反，读数待平台对帧核实（协调 11-17）；规则本身和 GKMS 逐步一致
        self.sync_status = str(sync_status).lower() not in ("0", "false", "off", "no", "")
        self.params = dict(
            params,
            seconds=seconds,
            worlds=worlds,
            threads=threads,
            warm=warm,
            greedy=greedy,
            sync_status=self.sync_status,
        )
        self.rx = None

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params, "privileged": False, "sees_seed": False}

    # ---------------------------------------------------------------- 一局
    def begin(self, ctx):
        from hiro.search.tower import complete_loadout, objective, tower_env

        self.lo = complete_loadout(dict(ctx.loadout))
        self.obj = objective(self.lo, self.lo.get("rivals"))
        seed = random.Random(json.dumps(self.lo.get("produce_cards"), sort_keys=True)).randrange(1, 2**31)
        self.rx = tower_env(self.lo, seed).rx
        self.rx.advance()
        self.prev = None  # 上一步对齐后、执行动作前的局面（引擎跑到画面前面时退回用）
        self.seen_types, self.free, self._turn_weights = {}, None, None  # 每局重新记（bench 同一个实例连打多局）
        self.policy: dict = {}  # 上一步搜索返回的权重（热启动用）
        # 只用第一轮（牌组张数以内）：洗牌后的顺序和当时弃牌堆的内容有关，打法不同就分叉（平台模块 11-14 回放 r1 / r3：前 12 张一样、
        # 第 13 张起不同），洗牌后交给搜索当随机
        known = [(d["id"], int(d.get("u") or 0)) for d in (getattr(ctx, "known_draws", None) or [])]
        self.known = known[: len(self.lo.get("produce_cards") or [])]
        self.drawn = 0  # known_draws 里已经抽过的张数（每步按山札内容重新对位）
        self.stats = collections.Counter()

    # ---------------------------------------------------------------- 对齐
    def _align(self, obs: dict) -> None:
        rx = self.rx
        guard = 0
        while not rx.ended and rx.current_turn < int(obs["exam"]["turn"]) and guard < 4:
            rx.step(("end",))
            rx.advance()
            guard += 1
        pools = {z: collections.defaultdict(list) for z in ("hand", "deck", "grave", "lost")}
        for z in pools:
            for g in rx.zone(z):
                pools[z][rx.card_info(g)[0]].append(g)

        def take(cid, zone, u=0):
            for z in [zone] + [x for x in pools if x != zone]:
                if pools[z].get(cid):
                    return pools[z][cid].pop(0)
            # 引擎牌组里没有：眠気这类机关 / 效果生成的卡（或编成没读到的牌），按画面新建一张接住
            if hasattr(rx, "new_card"):
                try:
                    g = rx.new_card(cid, int(u or 0))
                except KeyError:
                    return None
                self.stats["new_card"] += 1
                return g
            return None

        hand = [take(c["id"], "hand", c.get("u")) for c in obs["hand"]]
        grave = [take(c["id"], "grave", c.get("u")) for c in obs.get("grave") or []]
        lost = [take(c["id"], "lost", c.get("u")) for c in obs.get("lost") or []]
        ups = {c["id"]: c.get("u") for c in obs.get("deck") or []}
        deck_ids = [c["id"] for c in obs.get("deck") or []]
        # 已知抽牌顺序（ctx.known_draws：编成牌组里的牌按抽到先后、含开局起手；生成卡（眠気等）不在里面，8a3a1e7 11-14）
        if self.known:
            self.drawn, prefix = known_prefix([c for c, _ in self.known], deck_ids, self.drawn)
        else:
            prefix = []
        deck = [take(c, "deck", ups.get(c)) for c in prefix] + [
            take(c, "deck", ups.get(c))
            for c in (collections.Counter(deck_ids) - collections.Counter(prefix)).elements()
        ]
        if None in hand:
            raise RuntimeError(f"手牌对不上引擎牌组：{[c['id'] for c, g in zip(obs['hand'], hand) if g is None]}")
        rx.set_zone("hand", hand)
        rx.set_zone("grave", [g for g in grave if g])
        rx.set_zone("lost", [g for g in lost if g])
        rx.set_zone("deck", [g for g in deck if g])
        num = obs["num"]
        rx.score, rx.stamina, rx.block = int(num["score"]), int(num["stamina"]), int(num["block"])
        st = {s[0]: s[1] for s in obs.get("status") or []}
        if getattr(self, "sync_status", False) and hasattr(rx, "override_status"):
            sync_statuses(rx, obs.get("status") or [])
        else:  # 缺省：只同步 やる気 / 好印象（其余状态由引擎推演，规则和 GKMS 逐步一致；画面状态读数见 sync_statuses）
            if "aggressive" in st:
                rx.aggressive = int(st["aggressive"])
            if "review" in st:
                rx.review = int(st["review"])
        drinks = [d if isinstance(d, str) else d[0] for d in obs.get("drinks") or []]
        if drinks or rx.drinks:
            rx.drinks = drinks
        # 回合属性：画面只看得到已经到过的回合（ScreenTracker.types 把没见过的回合用当前回合的属性填满，那是猜的）。
        # 只收见过的；之后的回合按这一层战斗配置的三属性权重按比例分配，交给搜索每个世界打乱（free）。
        # 以前整列照收、还把 free 设空：手毬 16 层 T1 搜索以为 10 回合全是 Vo（倍率 23,090），预测终局高了约 4 倍（协调 11-17）
        cur, n = int(obs["exam"]["turn"]), int(rx.limit_turn)
        types = obs["exam"].get("types") or []
        seen = self.__dict__.setdefault("seen_types", {})
        if len(types) >= cur >= 1:
            seen[cur] = int(types[cur - 1])
        tt = [int(t) for t in rx.turn_types]
        for t, a in seen.items():
            if 1 <= t <= len(tt):
                tt[t - 1] = a
        future = list(range(min(cur, n), n))
        w = self.__dict__.get("_turn_weights")
        if w is None:
            from hiro.search.exam_env import turn_weights

            w = self.__dict__["_turn_weights"] = turn_weights(self.lo) or {}
        for i, a in zip(future, allocate_turns(w, len(future))) if w else ():
            tt[i] = a
        rx.turn_types = tt
        self.free = future
        self.prefix = len(prefix)

    # ---------------------------------------------------------------- 一步
    def decide(self, ctx) -> Decision:
        import gakumas_exam_rs as rs

        obs = ctx.obs
        t0 = time.monotonic()
        # 引擎比画面快（上一步我们选的动作实际没执行：退回了规则 / 画面上没生效）：回到上一步对齐后的局面再对齐
        if self.prev is not None and (self.rx.ended or int(self.rx.current_turn) > int(obs["exam"]["turn"])):
            self.rx = self.prev
            self.stats["rewind"] += 1
        self._align(obs)
        self.prev = self.rx.clone()
        secs = self.seconds if self.seconds is not None else float(getattr(ctx, "budget", None) or STEP_SECONDS)
        legal = {tuple(a) for a in obs.get("legal") or []}
        free = getattr(self, "free", None)
        if free is None:
            free = list(range(int(self.rx.current_turn), int(self.rx.limit_turn)))
        extra = {}
        if self.greedy:
            extra["greedy"] = self.greedy
        if self.warm and self.policy:
            extra["prior"] = {k: v * self.warm for k, v in self.policy.items()}
        kw = dict(
            worlds=self.worlds,
            seconds=max(0.2, secs - 0.3),
            threads=self.threads,
            seed=int(time.time()) & 0xFFFFFFFF,
            **self.obj,
        )
        try:
            r = rs.search(self.rx.clone(), self.prefix, free, **kw, **extra)
        except TypeError as e:  # 旧 Rust 包不认 prior / greedy 或按场次的目标（pass_rank / score_cap / clear_score）：这一局去掉，照常搜
            newer = sorted(set(extra) | (set(self.obj) & NEWER_OBJECTIVE))
            if not newer:
                raise
            logger.warning("Rust 后端不认 %s（%s），这一局去掉这些参数（热启动 / 按场次的目标）", newer, e)
            self.warm = self.greedy = 0.0
            self.obj = {k: v for k, v in self.obj.items() if k not in NEWER_OBJECTIVE}
            self.stats["no_newer_kwargs"] += 1
            kw = {k: v for k, v in kw.items() if k not in NEWER_OBJECTIVE}
            r = rs.search(self.rx.clone(), self.prefix, free, **kw)
        self.policy = r.get("policy") or {}
        best = tuple(r["best"]) if r.get("best") else ("end",)
        if legal and best not in legal:
            ok = [tuple(v[0]) for v in r.get("votes") or [] if tuple(v[0]) in legal]
            best = ok[0] if ok else (("end",) if ("end",) in legal else sorted(legal)[0])
            self.stats["illegal"] += 1
        self.rx.step(best)
        self.rx.advance()
        ws = r.get("worlds") or []
        raw = [w[4] for w in ws if len(w) > 4]
        stars = [w[5] for w in ws if len(w) > 5]
        meta = {"algo": self.name, "seconds": round(time.monotonic() - t0, 2), "worlds": len(ws),
                "votes": [[list(a), n, m] for a, n, m in (r.get("votes") or [])[:4]],
                "known_prefix": self.prefix,
                "score_quantiles": {"50%": int(sorted(raw)[len(raw) // 2])} if raw else {},
                "stars": collections.Counter(stars).most_common(3)}  # fmt: skip
        return Decision(best, meta)
