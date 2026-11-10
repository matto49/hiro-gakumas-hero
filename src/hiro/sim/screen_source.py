"""考试局面插件（ExamStateSource）：画面识别 + 模拟器跟踪，给决策附动作预演——不读考试存档。

用户 2026-11-08 决定开源模拟策略。主仓库的预演靠解密考试存档拿到精确局面（hiro.sim.live，不开源）；
这里改成从画面推：
1. 开考第一帧：用培育记账的牌组（state.produce.deck，R1 记在 finals_context）、画面上的饮料 / 体力、
   本战倍率（R1 结束记下的 / 考试里见过的 / 参考值）合成开局（hiro.sim.resource.setup_for，和轮间模拟同一套）；
2. 之后每一步：按实际执行的动作在模拟器里推进（hiro.sim.track.SimTracker），抽到哪几张按画面强制对齐，
   体力 / 元気 / やる気 / 好印象 / 分数有小误差时按画面修正；
3. 对不上（例如合成开局里的 P 道具 / 支援卡效果和实机不同，累积偏差太大）时，从最后一次对上的局面出发按画面重建手牌和数值；
   重建也不行就这一步不给预演（决策退回 LLM + 规则）。
隐藏信息只有山札顺序：预演在多个「世界」里重排它（hiro.sim.policies.determinize）。

选用（默认不用：画面推出的局面没有存档精确，先离线验证）：
    HIRO_EXAM_STATE_SOURCE=hiro.sim.screen_source:SimScreenExamStateSource
有预演时默认出招器（hiro.defaults.advisor）按预演期望 + 硬规则链选（exam_policy.expert_pick），选不出再退回 LLM。
"""

from __future__ import annotations

import copy
import logging
import os
import time
from typing import Any

from hiro.defaults.exam_state import ScreenExamStateSource

logger = logging.getLogger(__name__)

FINALS_LABELS = ("r1", "r2")
# 哪几轮给预演（逗号分隔，缺省 r1,r2）。Windows 第二十局：R1 有预演时 LLM 跟着预演早兑现，R1 只打 8.2 万（R2 反而受益），
# 先可以只在 R2 开：HIRO_EXAM_SIM_ROUNDS=r2
ROUNDS = tuple(x.strip().lower() for x in os.environ.get("HIRO_EXAM_SIM_ROUNDS", "r1,r2").split(",") if x.strip())
PREVIEW_WORLDS = int(os.environ.get("HIRO_EXAM_SIM_WORLDS", "16"))
PREVIEW_BUDGET_S = float(os.environ.get("HIRO_EXAM_SIM_BUDGET_S", "7.5"))


def _round_label(exam: str | None) -> str | None:
    """考试标签（r1 / r2 / r2#2 …）→ r1 / r2；选拔考试 None（没有合成模板）。"""
    if not exam:
        return None
    base = str(exam).split("#", 1)[0].lower()
    return base if base in FINALS_LABELS else None


class SimScreenExamStateSource(ScreenExamStateSource):
    """画面读局面（同默认实现）+ 模拟器跟踪出预演。"""

    def __init__(self):
        super().__init__()
        self.label: str | None = None
        self.tracker = None
        self.game = None  # 这一步核对过的 SimGame（预演用）
        self.meta = None
        self.lost: str | None = None
        self.stats = {"tracked": 0, "rebuilt": 0, "lost": 0}
        self.screen_drinks: list = []  # 画面饮料栏（槽位 → drink_id），模拟器的饮料顺序可能不同

    # ---------------------------------------------------------------- 生命周期
    def begin(self, context: dict[str, Any]) -> None:
        super().begin(context)
        ctx = context or {}
        self.label = _round_label(ctx.get("exam"))
        if self.label is not None and self.label not in ROUNDS:
            self.label = None  # 这一轮不给预演（HIRO_EXAM_SIM_ROUNDS）
        self.tracker = self.game = self.meta = None
        self.lost = None
        self.stats = {"tracked": 0, "rebuilt": 0, "lost": 0}

    def end(self, result: dict[str, Any] | None) -> None:
        if self.label and self.session is not None:
            self.session.note(f"模拟器跟踪（{self.label}）：核对通过 {self.stats['tracked']} 步、按画面重建 {self.stats['rebuilt']} 步、"
                              f"给不出预演 {self.stats['lost']} 步")  # fmt: skip
        super().end(result)
        self.tracker = self.game = None

    def confidence(self) -> str:
        return "screen"  # 局面仍来自画面；预演是附加信息

    # ---------------------------------------------------------------- 读局面
    def read(self, frame: Any, memory: Any) -> tuple[Any | None, str]:
        state, why = super().read(frame, memory)
        self.game = None
        if state is not None:
            self.screen_drinks = [getattr(d, "drink_id", None) for d in state.drinks]
        if state is None or self.label is None:
            return state, why
        try:
            self._sync(state, memory)
        except Exception as e:  # noqa: BLE001  模拟器出错不能影响考试
            self._lose(f"sim_error：{type(e).__name__}: {e}")
        return state, why

    def _sync(self, state, memory) -> None:
        from hiro.sim.track import Screen

        hand = [(c.card_id, c.upgrade) for c in state.hand]
        if not hand or any(cid is None for cid, _ in hand):
            self._lose("hand_unread")
            return
        screen = Screen(remaining=state.remaining_turns, hp=state.hp, genki=state.genki, hand=hand, score=state.score,
                        motivation=state.motivation, impression=state.impression)  # fmt: skip
        if self.tracker is None and not self._build(state, memory):
            return
        r = self.tracker.sync(screen)
        if r.game is not None:
            self.game = r.game
            self.stats["tracked"] += 1
            return
        game, note = self._rebuild(state)
        if game is None and getattr(self, "_missing", None) and self._add_basic(self._missing):
            game, note = self._rebuild(state)
            note = f"补了応援棒基础卡；{note}"
        if game is None:
            self._lose(f"{r.reason}；重建失败：{note}")
            return
        self.tracker.game, self.tracker.pending = game, None
        self.game = game
        self.stats["rebuilt"] += 1
        logger.info("模拟器跟踪：%s 对不上（%s），按画面重建（%s）", self.label, r.reason, note)

    def _lose(self, why: str) -> None:
        self.game = None
        self.stats["lost"] += 1
        if why != self.lost:
            logger.info("模拟器跟踪（%s）这一步给不出预演：%s", self.label, why)
        self.lost = why

    def _build(self, state, memory) -> bool:
        """开考：合成开局（牌组 / 饮料 / 体力 / 倍率），建 SimTracker。"""
        from hiro.sim import interval as iv
        from hiro.sim import resource as rv
        from hiro.sim.env import SimGame
        from hiro.sim.track import SimTracker
        from hiro.state.finals_context import CONTEXT

        deck = self._deck()
        if not deck:
            self._lose("牌组没记账")
            return False
        drinks = [d.drink_id for d in state.drinks if getattr(d, "drink_id", None)]
        max_hp = state.max_hp or (self.profile.max_hp if self.profile is not None else None) or 33
        permil = {k: v for k, v in (getattr(memory, "attr_permil", None) or {}).items() if v} or dict(CONTEXT.get("permil") or {})
        setup, notes = rv.setup_for(self.label, deck=deck, drinks=drinks, hp=state.hp or max_hp, max_hp=max_hp, permil=permil)
        game = self._new_game(setup, copy.deepcopy(memory))
        self.setup = setup
        self.tracker = SimTracker(game, 0, self.meta)
        logger.info("模拟器跟踪：%s 合成开局（牌组 %d 张、饮料 %s、体力 %s/%s%s）", self.label, len(deck), drinks, state.hp, max_hp,
                    "；" + "、".join(notes) if notes else "")  # fmt: skip
        return True

    def _new_game(self, setup, memory):
        from hiro.sim import interval as iv
        from hiro.sim.env import SimGame

        exam = iv.bare_exam(setup)
        env = iv._env(setup, seed=0)
        game = SimGame(env, self.label, self.profile)
        game.memory = memory
        self.meta = getattr(exam, "meta", None)
        game.meta = self.meta
        return game

    def _add_basic(self, missing: list) -> bool:
        """R2 応援棒随机补的基础卡（名字含「基本」，实机随机、模拟器按固定轮换补）、考试里生成的基础类卡（成長痛 塞进山札的 眠気 等，
        考试引擎按客户端规则会生成）在合成开局里没有：把画面上这几张加进合成牌组（R2 応援棒少补几张，总数不变）重新编译，
        接着按画面重建。R1 / R2 都做；只对基础类卡（ID 含 -0_）做，一场最多加 6 张。"""
        from hiro.sim import interval as iv

        if self.label not in FINALS_LABELS or getattr(self, "setup", None) is None:
            return False
        basics = [(cid, up or 0) for cid, up in missing if cid and "-0_" in cid]
        self._added = getattr(self, "_added", 0)
        if not basics or len(basics) != len(missing) or self._added + len(basics) > 6:
            return False
        self._added += len(basics)
        self.setup = iv._with(self.setup, deck=list(self.setup.deck) + basics)
        memory = self.tracker.game.memory if self.tracker is not None else None
        self.tracker.game = self._new_game(self.setup, memory)
        self.tracker.pending = None
        logger.info("模拟器跟踪：%s 画面上的基础类卡 %s 不在合成牌组（応援棒 / 考试中生成），加进去重新编译", self.label, basics)
        return True

    def _deck(self) -> list:
        """这一场考试的牌组：培育记账（R2 含轮间买的）；没有时用 R1 记下的。"""
        rows = []
        if self.session is not None:
            rows = (getattr(self.session.state, "produce", None) or {}).get("deck") or []
        deck = [(c["card_id"], int(c.get("upgrade") or 0)) for c in rows if isinstance(c, dict) and c.get("card_id")]
        if deck:
            return deck
        from hiro.state.finals_context import CONTEXT

        return [tuple(x[:2]) for x in CONTEXT.get("deck") or []]

    def _rebuild(self, state):
        """从最后一次对上的局面出发：推进到画面的剩余回合，手牌完全按画面设（从模拟器各区取同卡 ID 的实例），数值按画面。"""
        from hiro.sim.env import SimGame, clone, fast_apply
        from hiro.sim.track import _order_hand, def_card_id, def_upgrade

        want = [(c.card_id, c.upgrade) for c in state.hand]
        self._missing = []
        env = clone(self.tracker.game.env)
        notes: list[str] = []
        guard = 0
        while not env.ended and state.remaining_turns is not None and self.tracker.screen_remaining(env) > state.remaining_turns and guard < 6:
            fast_apply(env, {"kind": "end_turn"})
            guard += 1
        if env.ended:
            return None, "ended"
        pools = {z: list(env.zones.get(z, [])) for z in ("hand", "deck", "grave", "lost")}
        new_hand = []
        for cid, up in want:
            pick = None
            for exact in (True, False):
                for z in ("hand", "deck", "grave", "lost"):
                    pick = next((i for i in pools[z] if def_card_id(env.cards[i]) == cid and (not exact or up is None or def_upgrade(env.cards[i]) == up)), None)
                    if pick is not None:
                        pools[z].remove(pick)
                        if z != "hand":
                            notes.append(f"{env.definition(pick)['name']} ← {z}")
                        break
                if pick is not None:
                    break
            if pick is None:
                self._missing.append((cid, up))
                continue
            new_hand.append(pick)
        if self._missing:
            return None, f"画面上的 {[c for c, _ in self._missing]} 在模拟器里找不到"
        env.zones["deck"] = pools["deck"] + pools["hand"]
        env.zones["grave"], env.zones["lost"], env.zones["hand"] = pools["grave"], pools["lost"], new_hand
        for attr, v in (("hp", state.hp), ("shield", state.genki), ("motivation", state.motivation), ("impression", state.impression),
                        ("score", state.score)):  # fmt: skip
            if v is not None:
                setattr(env, attr, v)
        if env.actions <= 0 and any(c.available for c in state.hand):
            env.actions = 1
        _order_hand(env, want, notes)
        game = SimGame(env, self.label, self.profile)
        game.memory, game.meta = self.tracker.game.memory, self.meta
        return game, "；".join(notes) or "数值按画面"

    # ---------------------------------------------------------------- 预演 / 记账
    def previews(self, state: Any) -> list | None:
        if self.game is None:
            return None
        from hiro.contracts import Preview
        from hiro.sim.preview import preview_actions, slot_previews

        t0 = time.monotonic()
        try:
            game = self.game
            game.memory = copy.deepcopy(game.memory) if game.memory is not None else None
            game.state()  # 设好槽位（_hand / _drinks）
            pv = preview_actions(game.env, self.meta, worlds=PREVIEW_WORLDS, seed=game.env.play_count,
                                 deadline=time.monotonic() + PREVIEW_BUDGET_S)  # fmt: skip
            slots = slot_previews(pv, game)
            # 饮料：模拟器槽位 → 画面槽位（按 drink_id；手牌已由 _order_hand 按画面排好）
            remap = {}
            for k, p in slots.items():
                if k.startswith("drink:"):
                    did = game.env.drinks[game._drinks[int(k.split(":")[1])]]
                    j = next((i for i, x in enumerate(self.screen_drinks) if x == did and f"drink:{i}" not in remap), None)
                    if j is not None:
                        remap[f"drink:{j}"] = p
                else:
                    remap[k] = p
            out = Preview.from_slots(remap)
        except Exception as e:  # noqa: BLE001
            logger.info("模拟器预演失败：%s: %s", type(e).__name__, e)
            return None
        legal = {a.legal_key() if hasattr(a, "legal_key") else a for a in (state.legal_actions() if hasattr(state, "legal_actions") else [])}
        if legal:
            out = [p for p in out if p.action.legal_key() in legal]
        logger.info("模拟器预演：%d 个动作，%.1fs", len(out), time.monotonic() - t0)
        return out or None

    def executed(self, action, result) -> None:
        if self.tracker is None or self.game is None:
            return
        try:
            key = action.legal_key()
            kind, _, slot = key.partition(":")
            env = self.game.env
            if kind == "play":
                act = {"kind": "play", "id": env.zones["hand"][int(slot)]}
            elif kind == "drink":
                did = self.screen_drinks[int(slot)]
                key_ = next((d for d in env.drinks if env.drinks[d] == did), None)
                if key_ is None:
                    raise KeyError(f"模拟器里没有饮料 {did}")
                act = {"kind": "drink", "id": key_}
            else:
                act = {"kind": "end_turn"}
            self.tracker.executed(act)
        except Exception as e:  # noqa: BLE001  记账失败：下一帧按画面重建
            logger.info("模拟器跟踪记动作失败：%s: %s", type(e).__name__, e)
            self.tracker.pending = None
