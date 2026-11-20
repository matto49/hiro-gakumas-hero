"""考试出招插件：Rust GNRPA 搜索（任务 ② 第 4 部分；设计稿 docs/ryugi/specs/2026-11-12-rust-exam-search-design.md）。

每一步：模拟器跟踪、对齐过画面的引擎局面（hiro.sim.screen_source 给的 state.sim_game，开局是这一局的编成）
→ 和画面核对（手牌卡 id、能打出的牌、饮料数）→ gakumas_exam_rs.search（W 个世界并行 GNRPA，按第一手投票）→ 动作映射回画面槽位。

退回顺序（每次写明原因）：没有对齐的引擎局面 / 和画面核不上 / 搜索出错或没结果 → bench 中转层（UINet）→ 规则。
一场里连续 3 步核不上，这场剩下的步都退回（日志写明）。

缺省出招插件（用户 11-14：「我们允许切换」）；换回 bench 中转层用插件选择 HIRO_EXAM_ADVISOR=hiro.bench_bridge.advisor:BenchAdvisor。
每步 15 秒、8 个世界、CPU 核数 − 1 个线程（BUDGET，不设开关）。
只在有合成模板的本战 R1 / R2 搜（选拔考试没有跟踪，走 UINet）。不读种子、不读存档的隐藏信息。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from hiro.bench_bridge.advisor import BenchAdvisor
from hiro.contracts import Action, Candidate, Decision

logger = logging.getLogger("hiro.search")

NO_RS = "Rust 后端没装或是不带 search 的旧版本"
MISS_LIMIT = 3  # 一场里连续这么多步和画面核不上：剩下的步不再搜


BUDGET = {"seconds": 15.0, "worlds": 8, "threads": 0}  # 每步搜索预算（threads 0 = CPU 核数 − 1）


def budget() -> dict:
    return dict(BUDGET)


def _rs():
    try:
        import gakumas_exam_rs as rs
    except ImportError:
        return None
    return rs if hasattr(rs, "search") else None


def check(rx, state, legal: list[Action]) -> str | None:
    """引擎局面和画面核对：对不上返回原因。核引擎算出来、画面也看得到的：手牌（卡 id、顺序）、能打出的牌（引擎不能比画面松）、饮料数。"""
    hand = list(state.hand or [])
    eng = [rx.card_info(g)[0] for g in rx.zone("hand")]
    scr = [c.card_id for c in hand]
    if eng != scr:
        return f"手牌对不上：引擎 {eng} / 画面 {scr}"
    c = rx.clone()
    c.advance()
    if c.ended:
        return "引擎局面已经结束"
    eng_play = {int(a[1]) for a in c.legal_actions() if a[0] == "play"}
    keys = [a if isinstance(a, str) else a.legal_key() for a in legal]  # Action 或 legal_key 字符串（BattleState.legal_actions）
    scr_play = {int(k.split(":")[1]) for k in keys if k and k.startswith("play:")}
    # 引擎比画面严（画面判可打、引擎判不可打）不算对不上：好印象 / やる気等消耗不够时游戏不让打、卡面却不变灰，引擎是对的
    # （DMM mainverify2 R2：ゆめみごこち 好印象不够，退回 UINet 后选了它、连点 3 次停在选中态）；搜索只在引擎可打的牌里选，画面上都能打。
    # 引擎比画面松（引擎判可打、画面灰着）才退回：搜索可能选到打不出的牌（mainverify2 R1：元気 0 读成 10，「届いて！」）
    # 代价（3940a9）：引擎状态本身偏了（引擎里好印象比实际少等）时会漏掉一张其实能打的牌，只影响打得好不好，不会卡住。
    if eng_play - scr_play:
        return f"能打出的牌对不上：引擎 {sorted(eng_play)} / 画面 {sorted(scr_play)}"
    scr_drinks = [d for d in state.drinks or [] if getattr(d, "drink_id", None)]
    if len(rx.drinks) != len(scr_drinks):
        return f"饮料数对不上：引擎 {len(rx.drinks)} / 画面 {len(scr_drinks)}"
    return None


def free_turns(rx) -> list[int]:
    """属性还没看到的回合（turn_types 下标）：下一回合起到末三回合之前（末三回合按配额固定，同 hiro.sim.engine）。"""
    start = int(rx.current_turn)  # 下标 current_turn = 下一回合
    return list(range(start, max(start, int(rx.limit_turn) - 3)))


# 审查日程已知时按真实回合属性搜（协调 11-18 mainz4 R2 复盘②：第 3 次把两瓶ブースト喝在 T8 Vi 1916%，T9 其实是 Da 2851%）。
# 平台读到审查日程就写进考试局面 exam.turn_types（按回合从 1 起的 ["Vo","Da",…]，长度 = 总回合数，读不全整表 None）。缺省关。
KNOWN_TURNS_ENV = "HIRO_SEARCH_KNOWN_TURNS"
ATTR_TYPE = {"Vo": 1, "Da": 2, "Vi": 3}


def known_turns_on() -> bool:
    return os.environ.get(KNOWN_TURNS_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def apply_schedule(rx, schedule) -> tuple[Any, list[int]]:
    """已知审查日程 → (改了回合属性的 rx 副本, 还没知道的回合下标)。日程比引擎回合少（追加回合）时多出来的回合照旧按配额随机；
    日程认不出的项（None / 别的写法）那一回合也照旧。"""
    types = [ATTR_TYPE.get(str(a)) if a is not None else None for a in schedule]
    c = rx.clone()
    tt = [int(t) for t in c.turn_types]
    known = set()
    for i, t in enumerate(types[: len(tt)]):
        if t:
            tt[i] = t
            known.add(i)
    c.turn_types = tt
    return c, [i for i in free_turns(rx) if i not in known]


class SearchAdvisor(BenchAdvisor):
    """缺省出招插件：能搜就搜，不能搜照 bench 中转层（UINet → 规则）。"""

    def __init__(self, profile=None, llm=None):
        super().__init__(profile, llm)
        self.rs = _rs()
        self.misses = 0
        self.off_why: str | None = None
        self.search_stats = {"search": 0, "fallback": 0}
        if self.rs is None:
            logger.warning("考试出牌搜索不可用（%s），用 bench 中转层", NO_RS)

    def on_round_start(self, round_no: int, context: dict[str, Any]) -> None:
        super().on_round_start(round_no, context)
        self.misses, self.off_why = 0, None
        self.search_stats = {"search": 0, "fallback": 0}
        self.round_no = round_no

    def on_round_end(self, result: dict[str, Any] | None) -> None:
        logger.info("[%s] 考试出牌搜索：搜索 %d 步，退回 %d 步%s", self.label, self.search_stats["search"], self.search_stats["fallback"],
                    f"（{self.off_why}）" if self.off_why else "")  # fmt: skip
        super().on_round_end(result)

    def _back(self, state, memory, previews, legal, why: str) -> Decision:
        self.search_stats["fallback"] += 1
        logger.info("[%s] 这一步不搜：%s", self.label, why)
        d = super().decide(state, memory, previews, legal)
        d.warnings = list(d.warnings) + [f"搜索没用上，退回 bench 中转层：{why}"]
        return d

    def decide(self, state: Any, memory: Any, previews, legal: list[Action]) -> Decision:
        if self.rs is None:
            return self._back(state, memory, previews, legal, NO_RS)
        if self.off_why:
            return self._back(state, memory, previews, legal, self.off_why)
        game = getattr(state, "sim_game", None)
        rx = getattr(getattr(game, "env", None), "rx", None)
        if rx is None or not getattr(state, "sim_exact_exam", False):
            return self._back(state, memory, previews, legal, "没有对齐过画面的引擎局面（选拔考试 / 跟踪丢了）")
        why = check(rx, state, legal)
        if why:
            self.misses += 1
            if self.misses >= MISS_LIMIT:
                self.off_why = f"连续 {self.misses} 步和画面核不上，这场剩下的步不搜（最后一次：{why}）"
            return self._back(state, memory, previews, legal, why)
        self.misses = 0
        b = budget()
        t0 = time.monotonic()
        try:
            seed = hash((self.label, getattr(self, "round_no", 0), int(rx.current_turn), int(rx.turn_card_play_count))) & 0xFFFFFFFF
            sched = self._schedule()
            start, free = apply_schedule(rx, sched) if sched else (rx.clone(), free_turns(rx))
            r = self.rs.search(start, 0, free, worlds=b["worlds"], seconds=b["seconds"], threads=b["threads"], seed=seed)
        except Exception as e:  # noqa: BLE001
            return self._back(state, memory, previews, legal, f"搜索出错：{type(e).__name__}: {e}")
        best = r.get("best")
        if best is None:
            return self._back(state, memory, previews, legal, "搜索没有结果（时间内一个世界都没跑完）")
        action, what = self._to_hiro(best, rx, state)
        if action is None or action.legal_key() not in {a.legal_key() for a in legal}:
            return self._back(state, memory, previews, legal, f"搜索给的 {best} 映射不到画面上的合法动作")
        if action.legal_key().startswith("play:"):
            card = next((c for c in state.hand if c.slot == action.slot), None)
            if card is not None:
                self.tracker.played_card(card)
        elif action.legal_key().startswith("drink:"):
            dv = next((x for x in state.drinks if x.slot == action.slot), None)
            if dv is not None:
                self.tracker.drank(getattr(dv, "drink_id", None))
        self.search_stats["search"] += 1
        cands = []
        for a, n, mean in r.get("votes") or []:
            ca, w = self._to_hiro(a, rx, state)
            if ca is not None:
                cands.append(Candidate(ca, note=f"{w}：{n} 票，平均终局 {mean:,.0f}", expected=mean))
        ws = r.get("worlds") or []
        reason = (f"GNRPA 搜索（{len(ws)} 个世界、{sum(w[2] for w in ws):,} 局续打、{r.get('seconds', 0):.1f}s）：{what}，"
                  f"{r['votes'][0][1]}/{len(ws)} 票，平均终局 {r['votes'][0][2]:,.0f}")  # fmt: skip
        return Decision(action=action, reason=reason, source="search", confidence=r["votes"][0][1] / max(1, len(ws)), candidates=cands,
                        seconds=time.monotonic() - t0, decider="battle")  # fmt: skip

    def _schedule(self) -> list | None:
        """平台读到的审查日程（考试局面 exam.turn_types）；开关没开 / 没读到 None。"""
        if not known_turns_on():
            return None
        ex = getattr(getattr(getattr(self, "_session", None), "state", None), "exam", None)
        tt = ex.get("turn_types") if isinstance(ex, dict) else getattr(ex, "turn_types", None)
        return list(tt) if tt else None

    @staticmethod
    def _to_hiro(a, rx, state) -> tuple[Action | None, str]:
        a = tuple(a)
        if a[0] == "play":
            i = int(a[1])
            card = next((c for c in state.hand if c.slot == i), None)
            return Action.play_card(i), (card.name if card is not None else f"手牌 {i}")
        if a[0] == "drink":
            i = int(a[1])
            if i >= len(rx.drinks):
                return None, ""
            did = rx.drinks[i]
            dv = next((x for x in state.drinks or [] if getattr(x, "drink_id", None) == did), None)
            return (Action.use_drink(dv.slot), dv.name or did) if dv is not None else (None, "")
        return Action.skip(), "结束回合"
