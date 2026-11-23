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
_SEEN_ERRORS: set[str] = set()  # 预演异常：每种只在第一次记调用栈

FINALS_LABELS = ("r1", "r2")
# 哪几轮给预演（逗号分隔，缺省 r1,r2）。Windows 第二十局：R1 有预演时 LLM 跟着预演早兑现，R1 只打 8.2 万（R2 反而受益），
# 先可以只在 R2 开：HIRO_EXAM_SIM_ROUNDS=r2
ROUNDS = tuple(x.strip().lower() for x in os.environ.get("HIRO_EXAM_SIM_ROUNDS", "r1,r2").split(",") if x.strip())
PREVIEW_WORLDS = int(os.environ.get("HIRO_EXAM_SIM_WORLDS", "16"))
PREVIEW_BUDGET_S = float(os.environ.get("HIRO_EXAM_SIM_BUDGET_S", "7.5"))
# 手牌里有、引擎各区都没有的卡（考试中生成的 眠気、账本漏记的支援事件卡）在当前局面上新建接住（Rust new_card），一场最多这么多张：
# 再多多半是认卡认错，宁可退回画面读数（DMM mainverify2 11-17）
NEW_CARD_LIMIT = 8
# R2 応援棒补卡按偶像流派的真实卡池（interval.ouenbou_pool）补，画面上露出的基础卡在引擎里没有时换掉一张还没露面的占位卡（不另新建）。
# 协调 11-18 mainz4 R2 复盘③：旧轮换表补的 アピール / 表現 / ファンサ の基本 広 的応援棒根本不会给，露出的 仕草 / アイコンタクト
# 只能临时新建，引擎山札一直多 2–3 张永远不会来的卡。缺省开（离线自打 400 局 +29k、约 2.5 个标准误，协调批准）；0 / off 关。
OUENBOU_POOL_ENV = "HIRO_SIM_OUENBOU_POOL"


# P 道具账本为空时（mainz4 整局 produce 没有 items、考试画面 pitems 也是空的），run_loadout 给空道具，合成开局把模板的道具全清掉：
# 引擎里连固有道具（広：えいえいおー HP≥50% 追加行动 / 失血 15%）都没有，搜索推演的主循环是错的。打开后只放偶像卡的固有道具。
# 1 = 主数据 IdolCard.beforeProduceItemId（未凸；模板 campus-20261019 里也是它）；也可以直接写道具 id（凸后 …-1）。
# 缺省开（建模错，协调 11-18 批准）；0 / off 关。
UNIQUE_ITEM_ENV = "HIRO_SIM_UNIQUE_ITEM"


def unique_item(idol_card_id: str | None) -> str | None:
    v = (os.environ.get(UNIQUE_ITEM_ENV) or "1").strip()
    if v.lower() in ("0", "off", "false", "no"):
        return None
    if v.startswith("pitem_"):
        return v
    try:
        from hiro.bench_bridge.tower import master

        idol = next(
            (
                r
                for r in master().rows("IdolCard")
                if r.get("id") == str(idol_card_id or "").replace("i_card-skin-", "i_card-")
            ),
            None,
        )
    except Exception:  # noqa: BLE001  没有主数据
        return None
    return (idol or {}).get("beforeProduceItemId") or None


def ouenbou_pool_on() -> bool:
    return (os.environ.get(OUENBOU_POOL_ENV) or "1").strip().lower() not in ("0", "off", "false", "no")


def _round_label(exam: str | None) -> str | None:
    """考试标签（r1 / r2 / r2#2 …）→ r1 / r2；选拔考试 None（没有合成模板）。"""
    if not exam:
        return None
    base = str(exam).split("#", 1)[0].lower()
    return base if base in FINALS_LABELS else None


def _track_all() -> bool:
    """出牌交给 bench 算法（hiro.bench_bridge，默认开）时，本战 R1 / R2 都跟踪，给出牌插件提供引擎局面（预演仍按 HIRO_EXAM_SIM_ROUNDS）。
    R1 以前不跟踪是因为 LLM 跟着 R1 预演过早兑现（Windows 第二十局）；bench 出牌不看预演，这个理由不成立了。"""
    try:
        from hiro.bench_bridge.advisor import enabled

        return enabled() and os.environ.get("HIRO_EXAM_SIM_TRACK_ALL", "1") != "0"
    except Exception:  # noqa: BLE001
        return False


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
        self.template: str | None = None  # 合成开局用的模板（r1 / r2 / selection）
        self.preview_on = True

    # ---------------------------------------------------------------- 生命周期
    def begin(self, context: dict[str, Any]) -> None:
        super().begin(context)
        ctx = context or {}
        self.label = _round_label(ctx.get("exam"))
        self.template = self.label
        self.preview_on = self.label in ROUNDS
        # 选拔考试不跟踪：没有自己的合成模板，借本战模板会带进本战的 P 道具 / 附魔，画面上又常有合成牌组里没有的卡
        # （2026-11-12 qualifydmmw1 离线重放 81 步丢 51 步），出牌插件走画面读数路径
        if self.label is not None and not self.preview_on and not _track_all():
            self.label = None  # 这一轮不跟踪、不给预演（HIRO_EXAM_SIM_ROUNDS）
        self.tracker = self.game = self.meta = None
        self.setup = None
        self._created = 0  # 按场算，同 _added
        self._added = 0  # 「一场最多加 6 张」按场算：以前不清零，同一局里 R1、R2 和几次再挑戦累计到 6 张后，后面的考试都加不进、整场给不出预演
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
        from hiro.sim.env import SimGame  # noqa: F401
        from hiro.sim.track import SimTracker
        from hiro.state.finals_context import CONTEXT

        deck = self._deck()
        if not deck:
            self._lose("牌组没记账")
            return False
        drinks = [d.drink_id for d in state.drinks if getattr(d, "drink_id", None)]
        max_hp = state.max_hp or getattr(memory, "max_hp", None)  # 开考时从培育页读到的（hiro.exec.exam.seed_max_hp）
        if not max_hp:
            self._lose("最大体力没读到（培育页体力上限），不拿猜的数值合成开局")
            return False
        permil = {k: v for k, v in (getattr(memory, "attr_permil", None) or {}).items() if v} or dict(
            CONTEXT.get("permil") or {}
        )
        setup, notes = rv.setup_for(
            self.template or self.label, deck=deck, drinks=drinks, hp=state.hp or max_hp, max_hp=max_hp, permil=permil
        )
        # 任务 ②：P 道具 / 支援卡 / 回忆附魔换成这一局的（原来是模板那一局的，任务 ① 查到搜出来的未来是错的）
        run, run_notes = self._run_loadout()
        setup = iv._with(setup, **run)
        notes = list(notes) + run_notes
        self._pool_ids = set()
        if ouenbou_pool_on() and setup.fill and len(deck) < iv.OUENBOU_SIZE:
            pool = iv.ouenbou_pool(getattr(self.profile, "idol_id", None))
            if pool:
                setup = iv._with(setup, ouenbou_pool=pool)
                self._pool_ids = {cid for cid, _ in pool}
                notes.append(f"応援棒按真实卡池补 {iv.OUENBOU_SIZE - len(deck)} 张")
        game = self._new_game(setup, copy.deepcopy(memory))
        self.setup = setup
        self.tracker = SimTracker(game, 0, self.meta)
        logger.info("模拟器跟踪：%s 合成开局（牌组 %d 张、饮料 %s、体力 %s/%s%s）", self.label, len(deck), drinks, state.hp, max_hp,
                    "；" + "、".join(notes) if notes else "")  # fmt: skip
        return True

    def _run_loadout(self) -> tuple[dict, list]:
        """这一局的编成（hiro.search.loadout）：P 道具来自培育记账，支援卡 / 回忆附魔来自开局编成方案（HIRO_FORMATION_PLAN=off 时没有）。"""
        import os

        from hiro.search.loadout import run_loadout

        produce = (getattr(self.session.state, "produce", None) or {}) if self.session is not None else {}
        plan = None
        if os.environ.get("HIRO_FORMATION_PLAN", "").lower() != "off":
            try:
                from hiro.brain.formation_plan import load

                plan = load(os.environ.get("HIRO_FORMATION_PLAN") or None)
            except Exception as e:  # noqa: BLE001
                logger.info("开局编成方案读不了：%s", e)
        run, notes = run_loadout(produce, plan)
        if not run.get("items") and (unique := unique_item(getattr(self.profile, "idol_id", None))):
            run = {**run, "items": (unique,)}
            notes = [n for n in notes if "P 道具没记账" not in n] + [
                f"P 道具没记账：只放固有道具 {unique}（{UNIQUE_ITEM_ENV}）"
            ]
        return run, notes

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

    def _new_card(self, env, cid: str, up) -> Any:
        """在当前局面上新建一张画面上有、引擎里没有的卡（不进任何区，由重建放进手牌）。不从开局重新编译：考试中引擎自己生成的卡
        （成長痛 塞进山札的 眠気）留在局面里（DMM mainverify2 R1：引擎里已有一张 眠気、画面两张，_add_basic 加一张重编译后
        只剩加的那张，「补了応援棒基础卡；画面上的 眠気 在模拟器里找不到」）。旧 Rust 包 / Python 后端没有 new_card：返回 None，走 _add_basic。
        新建的卡不带定制（customize_counts 为空）：账本漏记的若是特別指導定制过的卡，搜索按没定制算（3940a9：影响不大）。"""
        rx = getattr(env, "rx", None)
        if rx is None or not hasattr(rx, "new_card") or getattr(self, "_created", 0) >= NEW_CARD_LIMIT:
            return None
        try:
            g = rx.new_card(cid, int(up or 0))
        except (KeyError, ValueError):
            return None
        self._created = getattr(self, "_created", 0) + 1
        return g

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
        logger.info(
            "模拟器跟踪：%s 画面上的基础类卡 %s 不在合成牌组（応援棒 / 考试中生成），加进去重新编译", self.label, basics
        )
        return True

    def _deck(self) -> list:
        """这一场考试的牌组：培育记账（R2 含轮间买的）；没有时用 R1 记下的。"""
        rows = []
        if self.session is not None:
            rows = (getattr(self.session.state, "produce", None) or {}).get("deck") or []
        from hiro.search.loadout import customize_counts

        deck = []
        for c in rows:
            if not (isinstance(c, dict) and c.get("card_id")):
                continue
            up = int(c.get("upgrade") or 0)
            counts = customize_counts(c["card_id"], up, c.get("customize"))  # 特別指導记下的定制（任务 ②）
            deck.append((c["card_id"], up, counts) if counts else (c["card_id"], up))
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
        # 先执行上一步已经做了、但没和画面核对上的动作，再按画面修牌区和数值：原来从出牌之前的局面重建，这一步出牌的效果
        # （卡上挂的附魔、移到除外的卡、状态）整个丢了（任务 ② 验证 2：附魔完全一致的步只有 40%）
        pending = getattr(self.tracker, "pending", None)
        if pending:
            try:
                fast_apply(env, pending)
                notes.append("先执行上一步的动作")
            except Exception as e:  # noqa: BLE001  这一步在旧局面上执行不了：照旧从旧局面按画面重建
                env = clone(self.tracker.game.env)
                notes.append(f"上一步的动作执行不了（{type(e).__name__}），从旧局面重建")
        guard = 0
        while (
            not env.ended
            and state.remaining_turns is not None
            and self.tracker.screen_remaining(env) > state.remaining_turns
            and guard < 6
        ):
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
                    pick = next(
                        (
                            i
                            for i in pools[z]
                            if def_card_id(env.cards[i]) == cid
                            and (not exact or up is None or def_upgrade(env.cards[i]) == up)
                        ),
                        None,
                    )
                    if pick is not None:
                        pools[z].remove(pick)
                        if z != "hand":
                            notes.append(f"{env.definition(pick)['name']} ← {z}")
                        break
                if pick is not None:
                    break
            if pick is None and cid in (getattr(self, "_pool_ids", None) or ()):
                # 応援棒卡池里的卡露面了、引擎里没有这张：换掉一张还没露面（还在山札）的别的占位卡，牌组总数不变
                ph = next(
                    (
                        i
                        for i in pools["deck"]
                        if def_card_id(env.cards[i]) in self._pool_ids and def_card_id(env.cards[i]) != cid
                    ),
                    None,
                )
                if ph is not None:
                    pools["deck"].remove(ph)
                    pick = self._new_card(env, cid, up)
                    if pick is None:
                        pools["deck"].append(ph)
                    else:
                        notes.append(f"{cid} ← 换掉一张没露面的応援棒占位卡 {def_card_id(env.cards[ph])}")
            if pick is None and (pick := self._new_card(env, cid, up)) is not None:
                notes.append(f"{cid} ← 新建（考试中生成 / 牌组账本没有）")
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
        # 饮料栏按画面（画面每格都认出时）：重建从上一次对上的局面出发，中间喝掉的那瓶原来会一直留在引擎里（任务 ② 验证 2）
        screen_drinks = [getattr(d, "drink_id", None) for d in state.drinks or []]
        if all(screen_drinks) and sorted(env.drinks.values()) != sorted(screen_drinks):
            rx = getattr(env, "rx", None)
            if rx is not None and hasattr(type(rx), "drinks") and getattr(type(rx).drinks, "__set__", None):
                notes.append(f"饮料按画面：{sorted(env.drinks.values())} → {screen_drinks}")
                rx.drinks = screen_drinks
        _order_hand(env, want, notes)
        game = SimGame(env, self.label, self.profile)
        game.memory, game.meta = self.tracker.game.memory, self.meta
        return game, "；".join(notes) or "数值按画面"

    # ---------------------------------------------------------------- 预演 / 记账
    def previews(self, state: Any) -> list | None:
        if self.game is None:
            return None
        try:
            state.sim_game = self.game  # 对齐过画面的引擎局面：给出招插件（hiro.bench_bridge）转 UIObs 用
            state.sim_exact_exam = (
                self.template in FINALS_LABELS
            )  # 合成模板的回合数 / 属性表是这场考试的（选拔考试借的模板不是）
        except AttributeError:
            pass
        if not getattr(self, "preview_on", True):
            return None  # 只跟踪不预演（这一轮不在 HIRO_EXAM_SIM_ROUNDS）
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
                    j = next(
                        (i for i, x in enumerate(self.screen_drinks) if x == did and f"drink:{i}" not in remap), None
                    )
                    if j is not None:
                        remap[f"drink:{j}"] = p
                else:
                    remap[k] = p
            out = Preview.from_slots(remap)
        except Exception as e:  # noqa: BLE001
            # 同一种异常第一次出现时带调用栈（DMM 本战 R2 预演 24 次 KeyError '…@3' 只有一句文字，查不到在哪一步查表）
            sig = f"{type(e).__name__}: {e}"
            first = sig not in _SEEN_ERRORS
            _SEEN_ERRORS.add(sig)
            logger.info("模拟器预演失败：%s", sig, exc_info=first)
            return None
        legal = {
            a.legal_key() if hasattr(a, "legal_key") else a
            for a in (state.legal_actions() if hasattr(state, "legal_actions") else [])
        }
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
