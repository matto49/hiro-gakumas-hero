"""培育非考试环节的决策器：输入 hiro.contracts.GameState，输出 hiro.contracts.Decision。

    deciders = ProduceDeciders(profile, llm)          # llm：hiro.brain.llm.from_spec(...) 或 None（只用规则）
    d = deciders.decide("ACTION_SELECT", state)       # 或直接调 action_select / select_card / ...

每个决策器同一套流程：
1. 从 GameState 取 ProduceState（state.produce_state()，缺 idol_card_id 时补 profile 的）与本页字段（state.page_fields）；
2. 用 hiro.state.context 算出全部候选的确定性预演 → Decision.candidates；
3. 规则能定的直接给（source=rule）；需要判断的问 LLM（source=llm），LLM 不可用或输出非法时用社区攻略的兜底规则（source=fallback）。
判断依据以社区攻略为准（hiro.state.knowledge、brain/prompts/hif_produce.md）。

识别还没提供的页面字段（相談货架、授業 / 外出选项、轮间可买项）缺失时，决策器给出保守动作并写 warnings，不臆测。
"""

import json
import logging
import math
import os
import time
from dataclasses import replace
from functools import cache, lru_cache  # noqa: F401

from hiro.brain.llm import LLMAdapter, load_prompt, prompt_version
from hiro.brain.state import IdolProfile
from hiro.contracts import Action, ActionKind, Candidate, Decision, GameState, llm_trace_from_call
from hiro.state.knowledge import (
    CONFLICT_CARDS,
    EFFECT_JA,
    LOW_PICK_NO_DELETE,
    OUTING_LAYOUT,
    STUDY_LAYOUT,
    UPGRADE_ORDER,
    YARUKI_CARDS,
    is_conflict,  # fmt: skip
    is_once,
    template_option,
)
from hiro.state.produce import ATTRS, ProduceState

logger = logging.getLogger(__name__)
TIER_RANK = {"S": 3, "A": 2, "B": 1}
PAGES = (
    "TOWER_FORMATION",
    "TOWER_RETRY",
    "ACTION_SELECT",
    "SELECT_CARD",
    "SELECT_DRINK",
    "CONSULT",
    "STUDY",
    "OUTING",
    "HIF_ROUND_INTERVAL",
    "SELECT_PITEM",
    "PDRINK_MAX",
    "SPECIAL_GUIDANCE",
    "FORMATION",
    "QUALIFY_MEMORY",
    "SKILL_CARD_CHANGE_1",
    "SKILL_CARD_CHANGE_2",
    "SKILL_CARD_REMOVAL",
    "SKILL_CARD_ENHANCE",
    "PDRINK_MAX_CONFIRM",
    "EVENT_CHOICE",
    "EXAM_CARD_MOVE",
)


# 决策点补全（总控 2026-11-13）：规则 / 模拟拿不准的局面交 LLM，LLM 不可用或输出非法再兜底。用户 11-14：默认全开（「我们先默认都模型兜底」）。
# HIRO_PRODUCE_LLM 不设 / all = 全开；off = 全关；change_remove,remove_card,… = 只开这几条。选行动 / 抓卡三选一原来就问 LLM，不受这个开关管。
LLM_PATHS = (
    "change_remove",
    "remove_card",
    "select_drink",
    "consult",
    "interval",
    "drink_overflow",
    "select_pitem",
    "special_guidance",
    "event_choice",
    "card_move",
    "tower_formation",
)


def llm_path_on(name: str) -> bool:
    parts = {x.strip().lower() for x in os.environ.get("HIRO_PRODUCE_LLM", "").split(",") if x.strip()}
    if not parts or "all" in parts:
        return True
    if parts & {"off", "0", "none"}:
        return False
    return name in parts


def action_key(a: Action) -> str:
    """动作的简写（黄金用例的 accept / forbid 用）：training:Da:sp / class:Vo / outing / pick:1 / exclude:0 / reroll / event:2 /
    buy:3 / buy:delete:15 / upgrade:12 / ShopEnd / ibuy:4 / ibuy:recover / iupgrade:12 / IntervalEnd /
    remove:<card_id> / remove:cancel / enhance:<card_id> / change:<card_id> / dialog:reward_skip:yes。"""
    if a.kind is ActionKind.PRODUCE_NEXT:
        parts = [a.args["option"]] + ([a.args["attr"]] if a.args.get("attr") else [])
        if a.args.get("sp"):
            parts.append("sp")
        return ":".join(parts)
    if a.kind is ActionKind.ACTIVATE_EFFECT:
        return "pick:" + ",".join(map(str, a.args["pick_indexes"]))
    if a.kind is ActionKind.EXCLUDE_CARD:
        return f"exclude:{a.args['pick_index']}"
    if a.kind is ActionKind.EVENT_SELECT:
        return f"event:{a.args['select']}"
    if a.kind is ActionKind.DISCARD_DRINK:
        return f"discard:{a.args['discard'][0]}" if a.args.get("discard") else "keep"
    if a.kind in (ActionKind.SHOP_BUY, ActionKind.INTERVAL_BUY):
        head = "buy" if a.kind is ActionKind.SHOP_BUY else "ibuy"
        if a.args.get("service"):
            return ":".join([head, a.args["service"]] + ([str(a.args["card_index"])] if "card_index" in a.args else []))
        return f"{head}:{a.args['index']}"
    if a.kind in (ActionKind.REMOVE_CARD, ActionKind.ENHANCE_CARD, ActionKind.CHANGE_CARD):
        head = {ActionKind.REMOVE_CARD: "remove", ActionKind.ENHANCE_CARD: "enhance", ActionKind.CHANGE_CARD: "change"}[
            a.kind
        ]
        if a.args.get("cancel"):
            return f"{head}:cancel"
        return f"{head}:{a.args.get('card_id') or a.args.get('remove_card_id') or a.args.get('pick_index')}"
    if a.kind is ActionKind.DIALOG:
        return f"dialog:{a.args.get('kind')}:{'yes' if a.args.get('confirm') else 'no'}"
    if a.kind in (ActionKind.SHOP_UPGRADE_CARD, ActionKind.INTERVAL_UPGRADE_CARD):
        return f"{'upgrade' if a.kind is ActionKind.SHOP_UPGRADE_CARD else 'iupgrade'}:{a.args.get('card_index', a.args.get('card_id'))}"
    return a.kind.value if a.kind is not ActionKind.REROLL else "reroll"


def _tower_idol(tower_id: str, layer: int) -> str | None:
    """编成页没给 idol_card_id 时：用驱动刚写的这一层编成（HIRO_TOWER_LOADOUT，同塔同层才算）里的。编成评估要它
    （引擎按偶像定 plan 和开局基本卡；11-14 Mac 实机缺了它，评估报 KeyError IdolCard:None）。"""
    try:
        from hiro.bench_bridge.advisor import _tower_loadout

        lo = _tower_loadout({}) or {}
    except Exception:  # noqa: BLE001
        return None
    if lo.get("tower_id") == tower_id and int(lo.get("layer") or -1) == int(layer):
        return lo.get("idol_card_id")
    return None


class ProduceDeciders:
    def __init__(self, profile: IdolProfile | None, llm: LLMAdapter | None = None, *, main_attr: str | None = None,
                 unavailable_options: set[str] | frozenset = frozenset(), change_controlled: bool = False):  # fmt: skip
        """main_attr：运行级指定的主属性（Vo/Da/Vi，例如 run_hif --main-attr Da）；识别读出支援卡编成后可不传。
        unavailable_options：执行器还做不了的选行动选项（例如 special_guidance），决策时排除，
        避免选了执行不了、被 KAA 默认退回休息（2026-10-31 第 3 局第 19 天）。"""
        self.profile = profile
        self.llm = llm
        self.main_attr = main_attr
        self.unavailable_options = frozenset(unavailable_options)
        # 授業セレクトチェンジ 的换入 / 换出是否由决策器指定（执行侧按 change_pick / change_remove 的结果点击）。
        # False（默认）：换入 / 换出没有接 change_pick / choose_card_to_change（96c7acc 起执行侧只保护核心卡、认不出就取消），
        # 授業 遇到换卡档时改选眠気档；lead 接好两步后传 True。
        self.change_controlled = change_controlled
        self.change_target: str | None = None  # 阶段 1 选中的换入卡 card_id（阶段 2 比较价值用）
        self.consult_used: set[str] = (
            set()
        )  # 初 / N.I.A：这一次相談里已经用过的服务（删除 / 强化各一次，回到选行动页清空）
        self.picked_musts: set[str] = set()  # 本局决策器自己拿到的必拿卡（牌组没识别时用来判断还缺什么）
        self.consult_delete_lean = False  # 选拔相談打开「削除」对话框时：只删 qualify_consult_deletable 的牌（用户 2026-11-03 顺序），没有就取消
        self._page_spend: dict = {}  # 选卡页（page.since_seq）→ 本页已用的刷新 / 禁用次数
        self._last_reroll: dict = {}  # 选卡页 → 最近一次刷新前的候选（刷新后没变 = 没生效）
        self._last_left: dict = {}  # 选卡页 → 上一帧读到的剩余次数
        self._zero_waited: set = set()  # 已经为「次数突然变 0」等过一帧的页
        self.playbook = [f"idols/{profile.playbook}.md"] if profile and profile.playbook else []

    # ---------------------------------------------------------------- 入口
    def decide(self, page: str, state: GameState, *, n_options: int | None = None, **kw) -> Decision:
        """页面 → Decision。n_options：画面上实际的选项数（授業 / 外出 / 三选一），给出时校验序号。
        决策器抛异常、输出越界、或不能跳过的页输出了跳过时，改走本页的社区模板兜底（_page_fallback，source="fallback"、带 warnings），
        不回退到第 0 项（决策-08，用户：所有页面由我们的逻辑处理，不能落到 KAA 默认的 commit(0)）。"""
        fn = {"ACTION_SELECT": self.action_select, "SELECT_CARD": self.select_card, "SELECT_DRINK": self.select_drink,
              "CONSULT": self.consult, "STUDY": self.study, "OUTING": self.outing,
              "HIF_ROUND_INTERVAL": self.interval, "SELECT_PITEM": self.select_pitem, "EVENT_CHOICE": self.event_choice, "EXAM_CARD_MOVE": self.card_move, "PDRINK_MAX": self.drink_overflow,
              "SPECIAL_GUIDANCE": self.special_guidance, "FORMATION": self.formation, "TOWER_FORMATION": self.tower_formation, "TOWER_RETRY": self.tower_retry,
              "QUALIFY_MEMORY": self.qualify_memory, "SKILL_CARD_CHANGE_1": self.change_pick,
              "SKILL_CARD_CHANGE_2": self.change_remove, "SKILL_CARD_REMOVAL": self.remove_card,
              "SKILL_CARD_ENHANCE": self.enhance_card, "PDRINK_MAX_CONFIRM": self.drink_overflow_confirm,
              "NIA_SALES_PLACE": self.sales_place, "NIA_SALES_EVENT": self.sales_event, "NIA_AUDITION_SELECT": self.audition_select,
              "EXAM_RETRY": self.exam_retry}[page]  # fmt: skip
        t0 = time.monotonic()
        # 2026-11-25 插件架构：strategy.json 给这一页配了育成插件时先问它；它不管 / 不合格就走下面的内置逻辑
        from hiro.brain import plugin_gate

        pd, plugin_why = plugin_gate.produce_decide(
            page, state, n_options, kw, context=lambda: self._plugin_context(state), invalid=_invalid_decision
        )
        if pd is not None:
            pd.seconds = round(time.monotonic() - t0, 3)
            self._prewarm(state)
            return pd
        try:
            d = fn(state, **kw)
            why = _invalid_decision(page, d, n_options, kw)
        except Exception as e:  # noqa: BLE001  决策器不能把异常抛给主循环
            logger.exception("决策器 %s 异常", page)
            d, why = None, f"决策器异常 {type(e).__name__}: {e}"
        if why is not None:
            d = self._page_fallback(page, state, n_options, why, prior=d, **kw)
        if plugin_why is not None and d is not None:
            d.warnings.append(plugin_why)
        d.seconds = round(time.monotonic() - t0, 3)
        self._prewarm(state)
        return d

    def _plugin_context(self, state: GameState) -> dict:
        """育成插件 begin 拿到的本局上下文（spec 4.5）：培育 id / 家族 / 难度、偶像、主属性、开局编成方案（副本）、攻略包概况、trace 目录。"""
        import copy

        from hiro import formation_env
        from hiro.brain import guides
        from hiro.plugin_record import RECORD
        from hiro.state import modes

        mode = (state.produce or {}).get("mode")
        info = modes.MODES.get(mode or "")
        return {
            "mode": mode,
            "family": info.family if info else None,
            "difficulty": info.difficulty if info else None,
            "idol": getattr(self.profile, "idol_id", None),
            "profile": self.profile,
            "main_attr": self.main_attr,
            "formation_plan": copy.deepcopy(
                formation_env.ACTIVE_PLAN
            ),  # 开局编成方案（没用方案时 None）；插件改它不影响本局
            "guides": guides.pack_info(
                info.family if info else None
            ),  # 偶像卡攻略包概况；条目用 hiro.brain.guides.entries / lookup 取
            "trace_dir": getattr(RECORD, "trace_dir", None),
        }

    def _prewarm(self, state: GameState) -> None:
        """选拔：牌组变了就在后台把下一场考试提前编译（resource.prewarm，不阻塞），之后的饮料模拟不用等编译（协调 2026-11-03）。"""
        from hiro.brain import drinks as dk

        if not dk.SIM or not _sim_ready():
            return
        try:
            from hiro.state.produce import FINALS

            ps = self.produce_state(state)
            if (
                ps is None or ps.mode == FINALS or not ps.deck or _is_hajime_nia(ps.mode)
            ):  # 初 / N.I.A 不按 H.I.F 选拔考试预热
                return
            from hiro.sim import resource as rv

            if not ps.max_stamina:
                return  # 最大体力还没从画面读到：不预热（不猜数值）
            rv.prewarm("selection", deck=[(c.card_id, c.upgrade) for c in ps.deck], max_hp=ps.max_stamina)
        except Exception as e:  # noqa: BLE001  预热失败不影响决策
            logger.debug("饮料模拟预热失败：%s", e)

    def _page_fallback(
        self, page: str, state: GameState, n_options: int | None, why: str, *, prior: Decision | None = None, **kw
    ) -> Decision:
        """规则失败时的社区模板兜底（不选第 0 项）。认不出、没有模板的页面输出 Wait / 取消，由主循环记 recover。"""
        warn = [f"规则失败（{why}），改用社区模板兜底"] + (list(prior.warnings) if prior else [])
        n = n_options

        def fit(i: int | None) -> int | None:
            return i if i is not None and (n is None or 0 <= i < n) else None

        if page == "STUDY":
            _, slot = self._study_effect()
            i = fit(STUDY_LAYOUT.index(slot))
            if i is not None:
                return Decision(Action.event_select(i), f"授業兜底：本人おすすめ効果档（{slot}，knowledge.STUDY_LAYOUT 第 {i + 1} 项）", "fallback", 0.4,
                                warnings=warn, decider="study")  # fmt: skip
        if page == "OUTING":
            ps = self.produce_state(state)
            key = (
                "trouble"
                if ps and ps.mode == "produce-007" and ps.step == 5
                else ("paid" if ps and ps.produce_point >= 150 else "free")
            )
            i = fit(OUTING_LAYOUT.index(key))
            if i is not None:
                return Decision(Action.event_select(i), f"外出兜底：社区模板选「{key}」档（knowledge.OUTING_LAYOUT 第 {i + 1} 项）", "fallback", 0.4,
                                warnings=warn, decider="outing")  # fmt: skip
        if page == "SELECT_DRINK":
            raw = state.page_fields.get("candidates") or []
            best = _best_drink(raw[:n] if n else raw)
            if best is not None:
                return Decision(Action.activate_effect(best), f"饮料兜底：按饮料类别默认档位 / 稀有度选第 {best + 1} 瓶", "fallback", 0.4,
                                warnings=warn, decider="select_drink")  # fmt: skip
        if page == "SELECT_CARD":
            raw = state.page_fields.get("candidates") or []
            known = [i for i, c in enumerate(raw) if c.get("card_id") and (n is None or i < n)]
            if known:
                i, reason = self._card_fallback(raw, known, state.page_fields.get("recommended"))
                return Decision(
                    Action.activate_effect(i), reason, "fallback", 0.4, warnings=warn, decider="select_card"
                )
        if page == "SELECT_PITEM":
            items = state.page_fields.get("pitems") or []
            order = {"yellow": 0, "green": 1, "red": 2}
            colored = [
                (order[it["color"]], i)
                for i, it in enumerate(items)
                if it.get("color") in order and not it.get("custom") and (n is None or i < n)
            ]
            if colored:
                i = min(colored)[1]
                return Decision(Action.activate_effect(items[i].get("index", i)), "P 道具兜底：按背包颜色 黄 > 緑 > 赤", "fallback", 0.4,
                                warnings=warn, decider="select_pitem")  # fmt: skip
        if page in ("SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE"):
            kind = ActionKind.REMOVE_CARD if page == "SKILL_CARD_REMOVAL" else ActionKind.ENHANCE_CARD
            return Decision(
                Action(kind, {"cancel": True}),
                f"{page} 兜底：取消，不盲选",
                "fallback",
                0.2,
                warnings=warn,
                decider=page.lower(),
            )
        if page == "PDRINK_MAX_CONFIRM":
            return Decision(Action(ActionKind.DIALOG, {"kind": "reward_skip", "confirm": False}), "兜底：不确认跳过奖励", "fallback", 0.2, warnings=warn,
                            decider="drink_overflow")  # fmt: skip
        if prior is not None and prior.action.kind in (
            ActionKind.WAIT,
            ActionKind.SHOP_END,
            ActionKind.INTERVAL_END,
            ActionKind.CUSTOMIZE_END,
        ):
            prior.warnings = warn
            return prior
        return Decision(Action.ui(ActionKind.WAIT, why=why), f"{page} 没有可用的兜底模板：停下等主循环处理（不选第 0 项）", "fallback", 0.1,
                        warnings=warn, decider=page.lower())  # fmt: skip

    @staticmethod
    def unknown_fields(state: GameState, ps: ProduceState) -> set[str]:
        """未观测的 ProduceState 字段（会被默认值补上，不能当事实）。三选一页面观测到的禁用 / 刷新次数（page_fields）覆盖默认值。"""
        from dataclasses import fields

        unknown = set(state.unobserved_produce_fields()) if state.produce else {f.name for f in fields(ProduceState)}
        for key in ("rerolls_left", "excludes_left"):
            value = state.page_fields.get(key)
            if value is not None:
                setattr(ps, key, int(value))
                unknown.discard(key)
        return unknown

    def produce_state(self, state: GameState) -> ProduceState | None:
        produce = dict(state.produce or {})
        # 页面上「Pドリンク所持上限」（相談的「交換する」上方红标）：饮料栏已满，容量就是现在持有的瓶数。p15 第 12 天账号 2 只有 3 格，
        # 识别把容量读成 4，相談连点 3 次买饮料「交換する」都没亮（协调 11-17）
        if (state.page_fields or {}).get("drinks_full") and produce.get("drinks") is not None:
            produce["drink_capacity"] = len(produce["drinks"])
        if self.profile and not produce.get("idol_card_id"):
            produce["idol_card_id"] = self.profile.idol_id
        if self.main_attr and not produce.get("main_attr"):
            produce["main_attr"] = self.main_attr
        if HAJIME_NIA_STATE and _is_hajime_nia(produce.get("mode")):  # noqa: SIM102
            # 初 / N.I.A：没有星星（用户 11-17「完全不用处理」，star 留 None，GameState.produce_state() 在这两个模式不要求它）；
            # step 没读到补 0，unknown_fields 按原始局面仍当未观测。原来缺 step 时 produce_state() 是 None，选饮料 / 相談 / 特別指導 / 饮料溢出都在走兜底
            if produce.get("step") is None:
                produce["step"] = 0
        return replace(state, produce=produce).produce_state()

    # ---------------------------------------------------------------- 选行动
    def action_select(self, state: GameState) -> Decision:
        from hiro.state.context import preview_action_select
        from hiro.state.modes import HAJIME, NIA, family

        if family((state.produce or {}).get("mode")) in (HAJIME, NIA):
            return self._action_select_hajime_nia(state)
        ps = self.produce_state(state)
        available = state.page_fields.get("options") or None
        if available is not None and self.unavailable_options:
            available = [o for o in available if o.get("option") not in self.unavailable_options] or None
        if ps is None:
            return self._action_select_without_state(available, state.produce)
        # 日程那一步和页面选项对不上（DMM own 测试局：页头「選抜試験1まで」读偏 → step 停在前一天，用旧 step 的日程找课程 KeyError、
        # 重读 3 次后停机）：在附近几步里找和页面选项一致的那一步预览；找不到就不用日程预览，按页面选项兜底。都记 fallback，不抛异常
        stale = None
        if available:
            fit = _step_for_options(ps, available)
            if fit is None:
                return self._stale_step_fallback(
                    available, state.produce, f"日程第 {ps.step} 步的选项和页面对不上，附近也找不到对得上的一步"
                )
            if fit != ps.step:
                stale = f"日程第 {ps.step} 步的选项和页面对不上，按第 {fit} 步预览（页头可能读偏）"
                ps = replace(ps, step=fit)
        unknown = self.unknown_fields(state, ps)
        try:
            rows = [
                r for r in preview_action_select(ps, available, unknown) if r["option"] not in self.unavailable_options
            ]
        except (KeyError, IndexError, StopIteration, TypeError) as e:
            return self._stale_step_fallback(available, state.produce, f"选行动预览失败（{type(e).__name__}: {e}）")
        if stale:
            d = self._action_select_decide(ps, state, available, unknown, rows)
            return replace(
                d, source="fallback" if d.source in ("rule", "llm") else d.source, warnings=[*d.warnings, stale]
            )
        return self._action_select_decide(ps, state, available, unknown, rows)

    def _action_select_hajime_nia(self, state: GameState) -> Decision:
        """初 / N.I.A：按社区规则的策略表选（hiro.brain.hajime_nia，设计稿第四节），不走 H.I.F 的日程预演和もりし模板。"""
        from hiro.brain.hajime_nia import choose_action

        self.consult_used = set()  # 回到选行动页：上一次相談结束了

        prod = dict(state.produce or {})
        # 没有 option 名的项（识别没认出是什么按钮）跳过，不让整个决策器抛异常。
        # page_fields.declined_options：这一周游戏弹过警告、已经取消掉的选项（相談開始確認「Pポイントの所持数が少ないです」→ shop，
        # 平台点キャンセル后回到选行动页时给），这次不再选（全被排除时照旧全部可选）
        declined = set(state.page_fields.get("declined_options") or ())
        all_opts = [
            o
            for o in (state.page_fields.get("options") or [])
            if o.get("option") and o.get("option") not in self.unavailable_options
        ]
        opts = [o for o in all_opts if o["option"] not in declined] or all_opts
        cands = [Candidate(Action.produce_next(o["option"], attr=o.get("attr"), sp=o.get("sp"))) for o in opts]
        if not opts:
            return Decision(
                Action.ui(ActionKind.WAIT, why="没有可用行动"),
                "没有可选的行动",
                "fallback",
                0.1,
                cands,
                decider="action_select",
            )
        # step（第几周）N.I.A 画面上读不到，规则也只有初 MASTER「第 6 周前不能休息」用它：不算状态不全（N.I.A 实机 11-17 选行动 25 次全记成 fallback）
        missing = [k for k in ("stamina", "max_stamina") if prod.get(k) is None]
        st = self._hajime_nia_status(state)
        i, reason, _rules = choose_action(st, opts)
        o = opts[i]
        warn = [f"状态缺 {missing}，按默认值选"] if missing else []
        return Decision(Action.produce_next(o["option"], attr=o.get("attr"), sp=o.get("sp")), reason, "fallback" if missing else "rule",
                        0.5 if missing else 0.8, cands, warnings=warn, decider="action_select")  # fmt: skip

    def _loose_hajime_nia_state(self, state: GameState) -> ProduceState | None:
        """初 / N.I.A 的 ProduceState：实机局面没有 step、star（11-17 trace），produce_state() 构造不出、选卡退回了 H.I.F 的占位局面
        （mode=produce-007、牌组为空）。初 / N.I.A 没有星星，不补；其余没读到的补占位值，unknown_fields 仍按原始局面把它们当未观测。
        只给选卡用；别的模式返回 None。"""
        from hiro.state.modes import HAJIME, NIA, family

        raw = dict(state.produce or {})
        if family(raw.get("mode")) not in (HAJIME, NIA):
            return None
        # 续跑没读到页头的帧只有 mode / 牌组（11-17 trace）：选卡用不到体力 / P 点 / 属性，补占位值，unknown_fields 仍当未观测
        for k, v in (
            ("step", 0),
            ("stamina", 0),
            ("max_stamina", 0),
            ("produce_point", 0),
            ("params", {}),
            ("growth_permil", {}),
        ):
            if raw.get(k) is None:
                raw[k] = v
        return self.produce_state(replace(state, produce=raw))

    def _hajime_nia_status(self, state: GameState):
        from hiro.brain.hajime_nia import Status

        prod = dict(state.produce or {})
        from hiro.brain.hajime_nia import exam_criteria

        pf = state.page_fields or {}
        criteria = pf.get("criteria")
        if not criteria and pf.get("exam_next"):  # 页头「中間まで / 最終まで」：按主数据查下一场的审查基准
            criteria = exam_criteria(
                prod["mode"],
                prod.get("idol_card_id") or (self.profile.idol_id if self.profile else None),
                pf["exam_next"],
            )
        return Status(mode=prod["mode"], step=int(prod.get("step") or 0), stamina=int(prod.get("stamina") or 0),
                      max_stamina=int(prod.get("max_stamina") or prod.get("stamina") or 1), produce_point=int(prod.get("produce_point") or 0),
                      params=dict(prod.get("params") or {}), rest_left=prod.get("rest_left"), main_attr=prod.get("main_attr") or self.main_attr,
                      criteria=criteria, stage=pf.get("exam_next"), idol=prod.get("idol_card_id") or (self.profile.idol_id if self.profile else None),
                      business_vote_permil=int(prod.get("business_vote_permil") or 0),
                      consult_price=prod.get("consult_delete_price"))  # fmt: skip

    def _hajime_nia_choice(self, state: GameState, decider: str) -> Decision | None:
        """初 / N.I.A 的外出 / 授業（hiro.brain.hajime_nia）；别的模式返回 None，照 H.I.F 原逻辑。没有结构化 choices 时选中间那项兜底。"""
        from hiro.brain.hajime_nia import choose_outing, choose_study
        from hiro.state.modes import HAJIME, NIA, family

        if family((state.produce or {}).get("mode")) not in (HAJIME, NIA):
            return None
        choices = [c for c in state.page_fields.get("choices") or [] if "index" in c]
        if not choices:
            return Decision(Action.event_select(1), f"{decider}：识别没给选项，选中间那项", "fallback", 0.3, warnings=["没有 page_fields.choices"],
                            decider=decider)  # fmt: skip
        i, reason, _ = (choose_outing if decider == "outing" else choose_study)(self._hajime_nia_status(state), choices)
        return Decision(Action.event_select(i), reason, "rule", 0.7, decider=decider)

    EXAM_RETRY_MAX = 3  # 同一场考试 / 试镜最多再挑戦几次（协调 11-17，同 H.I.F 的 MAX_RETRIES_PER_EXAM）

    def exam_retry(self, state: GameState) -> Decision:
        """初 / N.I.A 考试 / 试镜失败后的再挑戦（协调 11-17：只用免费次数和已有的再挑戦チケット，每场最多 3 次；不用マニー买券、
        绝不花ジュエル）。page_fields：stage = result（结果页的「再挑戦」按钮）/ confirm（确认框），attempts（这一场已用次数），
        retries_left?（画面上「あと N回」），cost?（confirm：free / ticket / jewel，读不出不给）。
        输出 Dialog {kind: exam_retry, confirm, reselect?}；N.I.A 确认后回选档页降档（reselect，选档页的 failed 由平台带）。"""
        from hiro.state.modes import NIA, family

        pf = state.page_fields
        stage, n = pf.get("stage") or "result", int(pf.get("attempts") or 0)
        nia = family((state.produce or {}).get("mode")) == NIA

        def out(confirm: bool, why: str, conf: float = 0.9) -> Decision:
            args = {"kind": "exam_retry", "confirm": bool(confirm)}
            if nia and confirm:
                args["reselect"] = True
                why += "；N.I.A 回选档页降一档再打"
            return Decision(Action(ActionKind.DIALOG, args), why, "rule", conf, decider="exam_retry")

        if n >= self.EXAM_RETRY_MAX:
            return out(False, f"这一场已经再挑戦 {n} 次（上限 {self.EXAM_RETRY_MAX}），不再挑戦")
        if pf.get("retries_left") is not None and int(pf["retries_left"]) <= 0:
            return out(False, "画面上没有剩余的再挑戦次数")
        if stage == "result":
            return out(True, f"没过，第 {n + 1} 次再挑戦（先看确认框花什么）")
        cost = pf.get("cost")
        if cost in ("free", "ticket"):
            return out(
                True, f"再挑戦花的是{'免费次数' if cost == 'free' else '已有的再挑戦チケット'}，确认（第 {n + 1} 次）"
            )
        return out(
            False,
            "确认框要花ジュエル，取消" if cost == "jewel" else "确认框读不出花什么，取消（不冒险花ジュエル）",
            0.7,
        )

    # ---------------------------------------------------------------- N.I.A 营业 / 试镜选档（hiro.brain.hajime_nia，设计稿第三、六节）
    def sales_place(self, state: GameState) -> Decision:
        from hiro.brain.hajime_nia import choose_sales_place

        places = [p for p in state.page_fields.get("places") or [] if "index" in p]
        if not places:
            return Decision(
                Action.ui(ActionKind.WAIT, why="营业场所没读到"),
                "营业场所没读到",
                "fallback",
                0.1,
                decider="sales_place",
            )
        i, reason, _ = choose_sales_place(self._hajime_nia_status(state), places, (state.produce or {}).get("drinks"))
        return Decision(Action.event_select(i), reason, "rule", 0.7, decider="sales_place")

    def sales_event(self, state: GameState) -> Decision:
        from hiro.brain.hajime_nia import choose_sales_event

        choices = [c for c in state.page_fields.get("choices") or [] if "index" in c]
        if not choices:
            return Decision(
                Action.ui(ActionKind.WAIT, why="营业选项没读到"),
                "营业选项没读到",
                "fallback",
                0.1,
                decider="sales_event",
            )
        i, reason, _ = choose_sales_event(self._hajime_nia_status(state), choices)
        return Decision(Action.event_select(i), reason, "rule", 0.7, decider="sales_event")

    def audition_select(self, state: GameState) -> Decision:
        """N.I.A 试镜选档：动作带 audition_number（中转层按它查这场的战斗配置，8a3a1e7 11-17）。"""
        from hiro.brain.hajime_nia import choose_audition

        pf = state.page_fields
        tiers = [t for t in pf.get("tiers") or [] if "index" in t]
        if not tiers:
            return Decision(
                Action.ui(ActionKind.WAIT, why="试镜档位没读到"),
                "试镜档位没读到",
                "fallback",
                0.1,
                decider="audition_select",
            )
        votes = pf.get("votes") if pf.get("votes") is not None else (state.produce or {}).get("votes")
        i, number, reason, _ = choose_audition(
            self._hajime_nia_status(state), votes, tiers, failed=int(pf.get("failed") or 0)
        )
        return Decision(
            Action(ActionKind.EVENT_SELECT, {"select": int(i), "audition_number": number}),
            reason,
            "rule",
            0.7,
            decider="audition_select",
        )

    def _stale_step_fallback(self, available: list[dict] | None, produce: dict | None, why: str) -> Decision:
        """日程对不上时的兜底：不按（可能读错的）step 套日程模板，只按页面上的选项选。"""
        prod = {k: v for k, v in (produce or {}).items() if k != "step"}
        d = self._action_select_without_state(available, prod)
        return replace(d, source="fallback", warnings=[*d.warnings, why])

    def _action_select_decide(
        self, ps: ProduceState, state: GameState, available, unknown, rows: list[dict]
    ) -> Decision:
        from hiro.state.context import payload, to_previews

        for i, r in enumerate(rows):
            r["index"] = i
        previews = to_previews(rows)
        cands = [Candidate(p.action, preview=p.summary) for p in previews]
        legal = [i for i, r in enumerate(rows) if r.get("delta", {}).get("stamina", 0) + ps.stamina >= 0]
        if len(legal) > 1:
            legal = [i for i in legal if not _rest_blocked(ps, rows, i)] or legal
        if not rows:
            return Decision(
                Action.ui(ActionKind.WAIT, why="没有可用行动"),
                "没有可选的行动",
                "fallback",
                0.1,
                cands,
                decider="action_select",
            )
        if len(legal) == 1:
            blocked = [_rest_blocked(ps, rows, i) for i in range(len(rows)) if _rest_blocked(ps, rows, i)]
            why = f"只剩一个可选行动（休息被拦：{blocked[0]}）" if blocked else "只有一个体力够的行动"
            return Decision(previews[legal[0]].action, why, "rule", 1.0, cands, decider="action_select")
        template = template_option(ps.mode, ps.step, ps.stamina)
        if (hit := self._action_select_rule(ps, rows, legal, template)) is not None:
            return Decision(previews[hit[0]].action, hit[1], "rule", 0.9, cands, decider="action_select")
        fb = self._action_select_fallback(ps, rows, legal, template)
        if self.llm is None:
            return self._fallback(previews[fb[0]].action, fb[1], cands, "action_select", [])
        user = payload(ps, "action_select", available=available, unknown=unknown)
        user["社区模板"] = {
            "今天": template,
            "说明": "もりし的选拔/本战日程模板；第 8、14 天按考后体力是否 ≥21 在差し入れ和外出之间选",
        }
        data, calls = self._ask(["action_select.md", "hif_produce.md", *self.playbook], user)
        choice = _int(data, "choice")
        if choice not in legal:
            blocked = choice is not None and 0 <= choice < len(rows) and _rest_blocked(ps, rows, choice)
            why = (f"LLM 选了休息但被规则拦下（{_rest_blocked(ps, rows, choice)}），{fb[1]}" if blocked
                   else f"LLM 输出无效（{data or calls[-1].get('error')}），{fb[1]}")  # fmt: skip
            return self._fallback(previews[fb[0]].action, why, cands, "action_select", calls)
        return Decision(previews[choice].action, str(data.get("reason", "")), "llm", None, cands, line=str(data.get("line", "")),
                        llm=[llm_trace_from_call(c) for c in calls], decider="action_select")  # fmt: skip

    RULE_TEMPLATE_OPTIONS = (
        "gift",
        "outing",
        "shop",
        "special_guidance",
    )  # 模板直接决定、不用 LLM 的日程（授業 选哪个属性仍交给 LLM）
    LESSON_MIN_STAMINA = 8  # 只剩课程可选时，体力不低于它才按规则上主属性课程；更低时交给 LLM 权衡休息

    def _action_select_rule(
        self, ps: ProduceState, rows: list[dict], legal: list[int], template: str | None
    ) -> tuple[int, str] | None:
        """规则能明确决定的选行动，不调 LLM（协调 2026-11-03 选拔提速：第二十一局选拔 LLM 4.9 分钟 / 29 次，选行动 14 次里多数是这两类）：
        1. 当天有社区模板、模板动作可选，且是 差し入れ / 外出 / 相談 / 特別指導（第 8、14 天的 差し入れ / 外出 已按考后体力 ≥ 21 分好）；
        2. 选拔里能选的只有课程（加休息）、体力 ≥ LESSON_MIN_STAMINA：只上主属性课程，有 SP 选 SP [もりし]。
        其余（授業 补哪个属性、体力低时要不要休息、本战的课程）照旧交给 LLM。
        本战课程不走规则 2：主属性在选拔里早已 ◎，本战两次课程要补 R1 评定 △ / × 的属性（手册「主属性达到安全线后再补缺口属性」）。
        第二十三 / 二十四局本战课程误走规则 2、两次都上 Da，Vi 停在 × 附近，R1 倍率 Vi 15,620（第二十二局 LLM 补 Vi 后 22,500）。"""
        from hiro.state.context import main_attr_basis
        from hiro.state.produce import FINALS

        if template in self.RULE_TEMPLATE_OPTIONS:
            hit = [i for i in legal if rows[i]["option"] == template]
            if len(hit) == 1:
                return hit[0], f"社区模板：第 {ps.step} 天 {rows[hit[0]]['选项']}（规则直接定，不调 LLM）"
        opts = {rows[i]["option"] for i in legal}
        if (
            ps.mode != FINALS
            and opts <= {"training", "rest"}
            and "training" in opts
            and ps.stamina >= self.LESSON_MIN_STAMINA
        ):
            main, basis = main_attr_basis(ps)
            mains = [i for i in legal if rows[i]["option"] == "training" and rows[i].get("attr") == main]
            if mains:
                i = max(mains, key=lambda k: bool(rows[k].get("sp")))
                return (
                    i,
                    f"只能上课程：只上主属性 {main}（{basis}）{'，SP' if rows[i].get('sp') else ''} [もりし]（规则直接定，不调 LLM）",
                )
        return None

    def _action_select_fallback(
        self, ps: ProduceState, rows: list[dict], legal: list[int], template: str | None
    ) -> tuple[int, str]:
        """社区规则兜底：按模板的行动类型；课程上成长率最高的属性并优先 SP；授業补离本战 R1 基准最远的非主属性。"""
        from hiro.state.context import main_attr
        from hiro.state.produce import FINALS

        main = main_attr(ps)
        from hiro.state.context import ExamRequirementsUnavailable

        try:
            r1 = _r1_requirements(ps)
            weakest = min((a for a in ATTRS if a != main), key=lambda a: ps.params[a] / r1[a])
        except ExamRequirementsUnavailable:  # 没有考试要求数据：补数值最低的非主属性
            weakest = min((a for a in ATTRS if a != main), key=lambda a: ps.params.get(a) or 0)
        # 本战：课程 / 授業按 R1 倍率增量选（手册「主属性达到安全线后再补缺口属性」，协调 2026-11-04 与审计共用 context.r1_bonus_gain）
        gain = {i: rows[i]["R1倍率增量"] for i in legal if ps.mode == FINALS and rows[i].get("R1倍率增量") is not None}
        top = max(gain.values(), default=0) or 1

        def score(i: int) -> float:
            r = rows[i]
            s = 10.0 if r["option"] == template else 0.0
            if i in gain:
                s += 3 * gain[i] / top
            elif r["option"] == "training":
                s += 3 * (r.get("attr") == main) + 1.5 * bool(r.get("sp"))
            elif r["option"] == "class":
                s += 2 * (r.get("attr") == weakest)
            elif r["option"] in ("shop", "special_guidance") and template in ("shop", "special_guidance"):
                s += 8  # 模板的花钱日：相談 / 特別指導 互为替代（执行不了其中一个时用另一个）
            elif r["option"] == "rest" and _rest_blocked(ps, rows, i):
                s -= 20
            elif r["option"] == "rest":
                s += 4 if ps.stamina < 8 else -5
            return s

        best = max(legal, key=score)
        r = rows[best]
        how = f"本战按 R1 倍率增量 {r.get('R1倍率增量')}" if best in gain else f"主属性 {main}，授業补 {weakest}"
        return best, f"兜底（社区模板：{template or '无'}；{how}）→ {r['选项']}"

    def _action_select_without_state(self, available: list[dict] | None, produce: dict | None = None) -> Decision:
        """状态不全（识别没读到体力 / 成长率等）时：mode / step 已知就按日程模板选（第七局续跑后第 1 天按固定顺序选了 相談，模板是 差し入れ）；
        否则按「课程 > 授業 > 外出 > 差し入れ > 相談 > 休息」。"""
        order = ["training", "class", "outing", "gift", "shop", "rest"]
        opts = available or []
        cands = [Candidate(Action.produce_next(o["option"], attr=o.get("attr"))) for o in opts]
        if not opts:
            return Decision(
                Action.ui(ActionKind.WAIT, why="没有可用行动"),
                "没有可选的行动",
                "fallback",
                0.1,
                cands,
                decider="action_select",
            )
        produce = produce or {}
        if produce.get("mode") and produce.get("step"):
            want = template_option(produce["mode"], produce["step"], produce.get("stamina") or 33)
            hit = next((o for o in opts if o["option"] == want and want not in ("training", "class")), None)
            if hit is not None:
                return Decision(Action.produce_next(hit["option"]), f"状态不全，按日程模板第 {produce['step']} 天选 {want}", "fallback", 0.5, cands,
                                warnings=["ProduceState 字段不全，没有预演"], decider="action_select")  # fmt: skip
        o = min(
            opts, key=lambda o: (order.index(o["option"]) if o["option"] in order else 99, -(o.get("hp_cost") or 0))
        )
        sp = {8: True, 6: False}.get(o.get("hp_cost")) if o["option"] == "training" else None
        return Decision(Action.produce_next(o["option"], attr=o.get("attr"), sp=sp), "状态不全，按固定顺序兜底", "fallback", 0.3, cands,
                        warnings=["ProduceState 字段不全，没有预演"], decider="action_select")  # fmt: skip

    def _idol(self, state: GameState, ps: ProduceState | None = None) -> str | None:
        """当前偶像卡 id（去掉 skin 前缀）：ProduceState > state.produce > profile。"""
        idol = (
            (ps.idol_card_id if ps is not None else None)
            or (state.produce or {}).get("idol_card_id")
            or (self.profile.idol_id if self.profile else None)
        )
        return idol.replace("i_card-skin-", "i_card-") if idol else None

    def _upgrade_guide(self, state: GameState) -> dict:
        """偶像卡攻略 card_upgrade 条目 + 流派循环件 → {card_id: 分}（条目的 weight / 100，统计条目用排行榜里的占比；循环件至少 0.5）。"""
        from hiro.brain import flows, guides
        from hiro.brain.idol_know import lookup

        idol, out = self._idol(state), {}
        for it in guides.merge(lookup((state.produce or {}).get("mode"), idol, "card_upgrade")):
            if it.side == "prefer":
                f = it.fields
                out[it.id] = (
                    float(f["weight"]) / 100
                    if f.get("weight") is not None
                    else float((f.get("stat") or {}).get("share") or 0)
                )
        for cid in flows.loop_parts(idol):
            out[cid] = max(out.get(cid, 0.0), 0.5)
        return out

    def _hif_other(self, state: GameState, ps: ProduceState | None = None) -> bool:
        """H.I.F、开了 HIRO_HIF_GUIDES、偶像不是广（idol_know.hif_other）。"""
        from hiro.brain.idol_know import hif_other

        mode = (ps.mode if ps is not None else None) or (state.produce or {}).get("mode")
        return hif_other(mode, self._idol(state, ps))

    def _cull(self, state: GameState, cards: list[dict], op: str, **kw) -> list[dict]:
        """换 / 删的顺序：广照 cull_pool；H.I.F 里别的偶像（_hif_other）按自己的流派 + 攻略的删卡条目（_flow_delete_pool，同初 / N.I.A），
        换卡时去掉トラブル（游戏不让换）。"""
        if not self._hif_other(state):
            return cull_pool(cards, op, **kw)
        pool = _flow_delete_pool(cards, self._idol(state), (state.produce or {}).get("mode"))
        return [c for c in pool if c["flow_tier"] > 0] if op == "change" else pool

    # ---------------------------------------------------------------- 三选一
    def select_card(self, state: GameState, *, change: bool = False) -> Decision:
        from hiro.state.context import for_llm, preview_card_pick, situation, to_previews

        raw = list(state.page_fields.get("candidates") or [])
        known = [i for i, c in enumerate(raw) if c.get("card_id")]
        has_state = self.produce_state(state) is not None
        ps = (
            self.produce_state(state)
            or (self._loose_hajime_nia_state(state) if HAJIME_NIA_CARD_PICK else None)
            or _empty_state(self.profile)
        )
        unknown = self.unknown_fields(state, ps)
        cands_in = [
            {"card_id": c["card_id"], "upgrade": c.get("upgrade") or 0} if c.get("card_id") else None for c in raw
        ]
        pick = preview_card_pick(ps, [c or {"card_id": raw[known[0]]["card_id"]} for c in cands_in], change=change,
                                 unknown=unknown) if known else None  # fmt: skip
        rows = pick["候选"] if pick else []
        cands = [
            Candidate(p.action, preview=p.summary)
            for p in to_previews([r for i, r in enumerate(rows) if i in known], "card_pick")
        ]
        recommended = state.page_fields.get("recommended")
        if not known:
            if isinstance(recommended, int):
                return Decision(
                    Action.activate_effect(recommended),
                    "候选都没识别出来，跟随游戏推荐",
                    "fallback",
                    0.2,
                    cands,
                    decider="select_card",
                )
            return Decision(Action.ui(ActionKind.WAIT, why="选卡候选未识别"), "候选和推荐都没识别出来：停下等重新识别（不选第 0 张）", "fallback", 0.1,
                            cands, decider="select_card")  # fmt: skip
        if (why := self._sudden_zero(state, ps, unknown)) is not None:
            return Decision(Action.ui(ActionKind.WAIT, why=why), f"剩余次数突然变 0（{why}），按钮可能还没画出来：等一帧再定", "rule", 0.6, cands,
                            decider="select_card")  # fmt: skip
        budget = self._pick_budget(state, ps, unknown, raw)

        def out(d: Decision) -> Decision:
            self._note_spend(state, d)
            return d

        # 牌组没识别时（第十九局本战开局 produce.deck 为空）用换卡 / 删卡 / 强化对话框里见过的整副牌组（seen_deck）
        seen = getattr(self, "seen_deck", {}) or {}
        owned = {c.card_id for c in ps.deck} | self.picked_musts | set(seen)
        copies = {}
        for c in ps.deck:
            copies[c.card_id] = copies.get(c.card_id, 0) + 1
        if not ps.deck:
            copies = dict(seen)
        bad = {i for i in known if unwanted(raw[i]["card_id"], copies.get(raw[i]["card_id"], 0))}
        counted = (
            bool(ps.deck) and "deck" not in unknown
        )  # 张数确认过（状态机记账）才放开「第二张」；只见过对话框一屏时不放
        missing = lambda cid: cid not in owned or (counted and wants_copy(cid, copies.get(cid, 0)))  # noqa: E731
        from hiro.state.modes import HAJIME, NIA
        from hiro.state.modes import family as _family

        if HAJIME_NIA_CARD_PICK and not change and _family(ps.mode) in (HAJIME, NIA):
            # 初 / N.I.A 不按 H.I.F 的必拿表刷新 / 禁用、不进 H.I.F 提示词的 LLM（11-17 实机：为 せのびの魔法 把每页刷新 / 禁用用光）
            return out(self._select_card_hajime_nia(ps, raw, known, copies if counted or seen else None, budget, cands))
        if self._hif_other(state, ps):
            # H.I.F 里不是广的偶像（HIRO_HIF_GUIDES=1，用户 11-21）：按自己的流派 + 偶像卡攻略选，不按广的必拿表刷新 / 禁用
            return out(self._select_card_hajime_nia(ps, raw, known, copies if counted or seen else None, budget, cands))
        for i in known:
            know = YARUKI_CARDS.get(raw[i]["card_id"])
            if know and know.get("must") and missing(raw[i]["card_id"]):
                self.picked_musts.add(raw[i]["card_id"])
                return Decision(
                    Action.activate_effect(i),
                    f"社区必拿：{know['name']}（{know['note']}）",
                    "rule",
                    1.0,
                    cands,
                    decider="select_card",
                )
        from hiro.state.produce import FINALS as _FINALS

        blind_finals = (
            ps.mode == _FINALS and not ps.deck
        )  # 本战牌组没记账（对话框只见过一屏也算）：不按「必拿还缺」刷新 / 禁用
        # 本战牌组没识别、也没在对话框里见过时，不按「必拿还缺」刷新 / 禁用（第十九局 00:02：牌组里有 せのび，却按缺 せのび 用光了刷新和禁用）
        if not blind_finals and (d := self._fish_for_musts(raw, known, budget, owned, cands, bad)):
            return out(d)
        if d := self._avoid_conflict(raw, known, budget, cands, bad):
            return out(d)
        good = [
            i
            for i in known
            if _tier_rank(raw[i]["card_id"]) >= TIER_RANK["A"] and raw[i]["card_id"] not in owned and i not in bad
        ]
        if len(good) == 1 and all(_tier_rank(raw[i]["card_id"]) == 0 for i in known if i != good[0]):
            # 只有一张 S / A / protect，其余都是社区没提到的卡：直接拿（第 6 局第 3 天 がむしゃら 被刷掉、LLM 回放也没选它）
            k = YARUKI_CARDS.get(raw[good[0]]["card_id"]) or {}
            return out(Decision(Action.activate_effect(good[0]), f"候选里只有 {k.get('name')} 是高价值卡（{k.get('tier') or '实机核心'}），其余社区没提到：拿它",
                                "rule", 0.9, cands, decider="select_card"))  # fmt: skip
        from hiro.state.produce import FINALS

        deck_known = (bool(ps.deck) and "deck" not in unknown) or bool(seen)
        core = [i for i in known if missing(raw[i]["card_id"])
                and _is_core_card(raw[i]["card_id"], copies.get(raw[i]["card_id"], 0) if deck_known else None)] if ps.mode == FINALS else []  # fmt: skip
        if ps.mode == FINALS and core:
            # 本战不进 LLM（第十九局本战 00:03：LLM 理由说要 えいえいおー+、实际选了 手書きのメッセージ）：有核心卡按社区优先级直接拿
            i = max(core, key=lambda k: (_card_priority(raw[k]["card_id"]), -k))
            return out(
                Decision(
                    Action.activate_effect(i),
                    f"本战：拿核心卡 {_card_label(raw[i])}（社区优先级）",
                    "rule",
                    0.9,
                    cands,
                    decider="select_card",
                )
            )
        lean_sel = False
        if SELECTION_LEAN_PICK and ps.mode != FINALS and ps.deck and "deck" not in unknown:
            # 选拔（协调 2026-11-03 提速：选卡 12 次 LLM，其中「必拿已齐、候选无 S/A」6 次，3 次输出无效）：必拿都齐了、候选里没有核心卡时，
            # 和本战同样按瘦牌组规则（先刷新、否则拿最不污染的），不调 LLM
            owned_ids = {c.card_id for c in ps.deck}
            musts_done = all(cid in owned_ids for cid, k in YARUKI_CARDS.items() if k.get("must"))
            sel_core = [
                i
                for i in known
                if raw[i]["card_id"] not in owned_ids
                and _is_core_card(raw[i]["card_id"], copies.get(raw[i]["card_id"], 0))
            ]
            lean_sel = musts_done and not sel_core
        musts_done = (
            bool(ps.deck)
            and "deck" not in unknown
            and all(cid in {c.card_id for c in ps.deck} for cid, k in YARUKI_CARDS.items() if k.get("must"))
        )

        def lean_pick(why: str) -> Decision:
            # D40：选卡页没有「不拿」按钮（第三十三局选拔 #339 差し入れ页只有「受け取る」，课程页另有「カード除去」/ 再抽選）。
            # 「不想拿」= 有一次性 / 不污染的候选就直接拿它（协调 2026-11-04：同 test_small_deck，不刷新）；
            # 候选全部会污染牌组（冲突 / 好印象系 / 可复用的非核心）时才先刷新，刷新用完再拿最不污染的。不取游戏推荐
            def polluting(k: int) -> bool:
                cid = raw[k]["card_id"]
                return k in bad or bool(conflict_like(cid)) or is_once(cid) is not True

            clean = [k for k in known if not polluting(k)]
            if not clean and _budget_ok(budget, "reroll"):
                return out(
                    Decision(
                        Action(ActionKind.REROLL),
                        f"{why}：候选都会污染牌组，先刷新",
                        "rule",
                        0.8,
                        cands,
                        decider="select_card",
                    )
                )
            i = min(clean or known, key=lambda k: (_pollution_key(raw[k]["card_id"], k in bad), k != recommended))
            return out(Decision(Action.activate_effect(i), f"{why}：这一页不能不拿，拿最不污染的 {_card_label(raw[i])}"
                                f"（{'一次性' if is_once(raw[i]['card_id']) else '可复用'}{'、会生成眠気' if _makes_sleep(raw[i]['card_id']) else ''}）",
                                "rule", 0.75, cands, decider="select_card"))  # fmt: skip

        unrated = all(not YARUKI_CARDS.get(raw[i]["card_id"]) for i in known)
        if (ps.mode == FINALS and not core) or lean_sel:
            # 本战瘦牌组（用户 2026-11-02：「本战第二轮烂的原因是牌库太大了」；小牌组是游戏重点支持的方向）：
            # 候选里没有必拿 / S·A / 核心卡 → 先刷新（本页预算内），否则拿最不污染的（一次性 > 不生成眠気 > 不冲突），不走 LLM / 游戏推荐兜底
            # （三选一页面不能不拿）
            if _budget_ok(budget, "reroll"):
                return out(Decision(Action(ActionKind.REROLL), f"{'选拔' if lean_sel else '本战'}瘦牌组：候选里没有必拿 / S·A / 核心卡，先刷新", "rule", 0.85, cands,
                                    decider="select_card"))  # fmt: skip
            i = min(known, key=lambda k: _pollution_key(raw[k]["card_id"], k in bad))
            return out(Decision(Action.activate_effect(i), f"{'选拔' if lean_sel else '本战'}瘦牌组：候选里没有必拿 / S·A / 核心卡、刷新用完，拿最不污染的 {_card_label(raw[i])}"
                                f"（{'一次性' if is_once(raw[i]['card_id']) else '可复用'}{'、会生成眠気' if _makes_sleep(raw[i]['card_id']) else ''}）",
                                "rule", 0.8, cands, decider="select_card"))  # fmt: skip
        fb = self._card_fallback(raw, known, recommended, bad)
        if self.llm is None or len(known) == 1:
            if SELECT_NONE_LEAN and len(known) > 1 and musts_done and unrated:
                return lean_pick("兜底：必拿已齐、候选都是社区没提到的卡")
            return self._fallback(
                Action.activate_effect(fb[0]),
                fb[1],
                cands,
                "select_card",
                [],
                source="rule" if len(known) == 1 else "fallback",
            )
        user = {"局面": situation(ps, unknown) if has_state else "识别未提供完整局面",
                "选卡": pick | {"候选": [r for i, r in enumerate(for_llm(rows)) if i in known], "本页预算": _budget_text(budget)}}  # fmt: skip
        if SELECT_NONE_LEAN:
            user["选卡"]["不想拿时"] = (
                '这一页没有「不拿」按钮。三张都不想拿时输出 {"action": "skip"}：系统先刷新（有次数时），否则拿最不污染牌组的一张（一次性优先）'
            )
        data, calls = self._ask(["produce_card_pick.md", "hif_produce.md", *self.playbook], user)
        act, idx = (data or {}).get("action"), _int(data, "index")
        if SELECT_NONE_LEAN and (
            act in ("skip", "none", "no_pick", "pass") or (act == "pick" and idx is not None and idx < 0)
        ):
            return replace(
                lean_pick(f"LLM：不想拿（{str((data or {}).get('reason', ''))[:60]}）"),
                llm=[llm_trace_from_call(c) for c in calls],
            )
        action = None
        if act == "pick" and idx in known and idx in bad and any(i not in bad for i in known):
            idx = None  # 决策-07 / 小牌组：有别的候选时不拿冲突牌、可复用的非核心牌，退回兜底（兜底把它们排在最后）
        if act == "pick" and idx in known:
            action = Action.activate_effect(idx)
        elif act == "exclude" and idx in known and _budget_ok(budget, "exclude"):
            action = Action.exclude_card(idx)
        elif act == "reroll" and _budget_ok(budget, "reroll"):
            action = Action(ActionKind.REROLL)
        if action is None:
            why = f"LLM 输出无效（{data or calls[-1].get('error')}），{fb[1]}"
            if SELECT_NONE_LEAN and musts_done and unrated:
                return replace(
                    lean_pick(f"LLM 输出无效（{data or calls[-1].get('error')}）；必拿已齐、候选都是社区没提到的卡"),
                    llm=[llm_trace_from_call(c) for c in calls],
                )
            return self._fallback(Action.activate_effect(fb[0]), why, cands, "select_card", calls)
        return out(Decision(action, str(data.get("reason", "")), "llm", None, cands, line=str(data.get("line", "")),
                            llm=[llm_trace_from_call(c) for c in calls], decider="select_card"))  # fmt: skip

    # ---------------------------------------------------------------- 刷新 / 禁用预算
    def _pick_budget(self, state: GameState, ps: ProduceState, unknown: set[str], raw: list[dict]) -> dict:
        """本页还能用几次刷新 / 禁用（第 6 局选拔第 1 天两页用掉刷新 6 次、禁用 3 次）。社区依据：
        - 刷新 / 禁用次数整局不回复，剩余次数随选拔回忆带进本战 [wiki「選抜メモリーに引き継がれる」]；
        - 第 5 天外出的确定强化卡「再抽選を使い切ってでも」拿主力火力牌；其余时候只刷掉明显弱的卡 [もなみ]。
        规则：候选全是强化卡（确定强化页）时可以用光；其余每页刷新、禁用各最多 1 次，并留出预留：本战 1 次，选拔第 5 天前再多留 1 次。
        次数未识别时：每页最多 1 次（不按 0 也不按无限）。"""
        page = state.page.since_seq if state.page else None
        used = self._page_spend.get(page, {"reroll": 0, "exclude": 0})
        ids = [c.get("card_id") for c in raw]
        stuck = (
            self._last_reroll.get(page) == ids
        )  # 刷新后候选完全没变：上次刷新没生效（次数其实用完了，第 6 局 23:29:33 / 41）
        high = any(c.get("card_id") for c in raw) and all((c.get("upgrade") or 0) >= 1 for c in raw if c.get("card_id"))
        from hiro.state.produce import FINALS

        finals = ps.mode == FINALS
        reserve = 0 if (high or finals) else 1 + (1 if ps.mode == "produce-007" and ps.step < 5 else 0)
        caps, reserves = {"reroll": 1, "exclude": 1}, {"reroll": reserve, "exclude": reserve}
        from hiro.state.modes import HAJIME, NIA
        from hiro.state.modes import family as _family

        other = _family(ps.mode) in (HAJIME, NIA)  # 初 / N.I.A：没有 せのびの魔法 倾斜，也没有第 5 天确定强化 / 本战
        if not finals and not other and self._hif_other(state, ps):
            other = True  # H.I.F 里不是广的偶像（HIRO_HIF_GUIDES=1）：不为 せのびの魔法 倾斜刷新
        if not finals and not other and SENOBI not in ({c.card_id for c in ps.deck} | self.picked_musts):
            # 总控 2026-11-02（决策-11）：本战牌组来自选拔回忆，せのびの魔法 必须在选拔里拿到（缺它 R1 专家 −11.9 万）。
            # 还没拿到时刷新向它倾斜：每页最多 2 次、不留预留。禁用次数少（整局 3 次），不跟着倾斜：仍每页 1 次、照常留预留，
            # 别在前几页用光（第十五局第 3 页时只剩 1 次）
            caps["reroll"], reserves["reroll"] = (
                2,
                (1 if ps.mode == "produce-007" and ps.step < 5 else 0),
            )  # 第 5 天确定强化页仍留 1 次
        cap = caps["reroll"]
        out = {"high": high, "reserve": reserve, "used": dict(used), "stuck": stuck, "cap": cap, "other": other}
        for kind, key in (("reroll", "rerolls_left"), ("exclude", "excludes_left")):
            left = None if key in unknown else getattr(ps, key)
            if high:  # 确定强化页：剩多少用多少（次数未识别时最多 3 次）
                allowed = left if left is not None else 3 - used[kind]
            else:
                allowed = (
                    caps[kind] - used[kind] if left is None else min(caps[kind] - used[kind], left - reserves[kind])
                )
            out[kind] = max(0, allowed)
            out[kind + "_left"] = left
        if stuck:
            out["reroll"] = 0
        return out

    def _sudden_zero(self, state: GameState, ps: ProduceState, unknown: set[str]) -> str | None:
        """同一页上一帧次数 > 0、这一帧突然 0，而我们没用过：多半是弹窗刚出现、按钮还没画出来（数据确认 github-2c）。等一帧再定，只等一次。"""
        page = state.page.since_seq if state.page else None
        now = {k: None if k in unknown else getattr(ps, k) for k in ("rerolls_left", "excludes_left")}
        before = self._last_left.get(page)
        self._last_left[page] = now
        if before is None or page in self._zero_waited:
            return None
        spent = self._page_spend.get(page, {"reroll": 0, "exclude": 0})
        for key, kind in (("rerolls_left", "reroll"), ("excludes_left", "exclude")):
            if now[key] == 0 and (before.get(key) or 0) > 0 and spent[kind] < before[key]:
                self._zero_waited.add(page)
                return f"{key} 上一帧 {before[key]}、这一帧 0"
        return None

    def _note_spend(self, state: GameState, d: Decision) -> None:
        kind = {ActionKind.REROLL: "reroll", ActionKind.EXCLUDE_CARD: "exclude"}.get(d.action.kind)
        if kind is None:
            return
        page = state.page.since_seq if state.page else None
        self._page_spend.setdefault(page, {"reroll": 0, "exclude": 0})[kind] += 1
        if kind == "reroll":
            self._last_reroll[page] = [c.get("card_id") for c in state.page_fields.get("candidates") or []]

    def _select_card_hajime_nia(self, ps: ProduceState, raw: list[dict], known: list[int], copies: dict | None, budget: dict,
                                cands: list[Candidate]) -> Decision:  # fmt: skip
        """初 / N.I.A 三选一选卡（hajime_nia_rules）：
        1. 不拿：冲突牌 / 纯好印象系（conflict_like）、トラブル；可复用的非循环卡、循环卡已够张数（N.I.A [g8-NIAM] 只留几种可复用的循环卡；
           初 [g8-PRO] 牌组精简）。
        2. 拿：一次性的卡（「レッスン中1回」[g8-NIAM]）、还缺的循环卡（SMALL_DECK_CORE：きらきら紙吹雪 / ゆめみごこち 等，game8 やる気 的循环件）。
           同档里：社区 S / A（含 H.I.F 必拿，只是不专门去刷）> 还缺的循环卡（えいえいおー 是固有 P 道具的触发牌）> 能上偶像拿手 buff（广：やる気 [g8-試験]）> N.I.A 牌组里已有的一次性卡 [ww-NIA] > 稀有度 > 卡牌价值。
        3. 三张都是「不拿」：有刷新次数先刷新（本页预算同 _pick_budget），否则拿最不污染的。copies=None：牌组张数没确认，不按已有张数判。"""
        from hiro.brain import hajime_nia_rules as hr
        from hiro.state.knowledge import SMALL_DECK_CORE, core_keep
        from hiro.state.modes import NIA
        from hiro.state.modes import family as _family

        fam = _family(ps.mode)
        diff = None
        try:
            from hiro.state.modes import mode as _mode

            diff = _mode(ps.mode).difficulty
        except KeyError:
            pass
        buff = _signature_buff(ps.idol_card_id)
        used: list[str] = []

        def use(rid: str) -> None:
            if rid not in used and hr.applies(rid, fam, diff):
                used.append(rid)

        from hiro.brain import flows

        idol_flow = flows.flow(ps.idol_card_id)
        other_flow = idol_flow not in (
            None,
            "yaruki",
        )  # 广（やる気）照原来的卡表；别的偶像按自己流派（协调 11-19 多偶像）
        loops = flows.loop_parts(ps.idol_card_id) if other_flow else ()
        # 偶像卡攻略（hiro.brain.guides，协调 11-20）：有条目时，广（HIRO_GUIDES_HIRO=1）从条目读卡表，别的偶像在流派规则上补充
        from hiro.brain import guides as _guides
        from hiro.brain.idol_know import card_know

        know = card_know(ps.mode, ps.idol_card_id, ctx=_guides.Ctx(deck=[c.card_id for c in ps.deck] if copies is not None else None,
                                                                    p_point=ps.produce_point, stamina=ps.stamina))  # fmt: skip

        def info_flow(i: int) -> tuple[tuple, str]:
            """广以外的偶像：冲突 = 主打同プラン另一种 buff（flows.conflicts）；循环件 = g8-NIAM 这个流派的；社区评级 = 流派优先表（いおん）。"""
            cid, up = raw[i]["card_id"], raw[i].get("upgrade") or 0
            n = (copies or {}).get(cid, 0)
            once = is_once(cid) is True
            pr = flows.priority(ps.idol_card_id, cid) or {}
            weight = max(int(pr.get("weight") or 0), know.weight(cid) if know else 0)
            # 攻略级别高于 flows 的卡面规则（协调 11-20）：条目推荐的卡不按 flows 判冲突，条目避开的卡算冲突
            recommended = bool(know and cid in know.prefer)
            why_bad = (
                (None if recommended else flows.conflicts(cid, buff))
                or ("トラブル" if _is_trouble_card(cid) else None)
                or (f"攻略里避开（{know.source(cid)}）" if know and know.avoided(cid) else None)
            )
            if why_bad:
                return (0, int(once), weight), why_bad
            kk = (know.keep(cid) if know else None) or (1 if cid in loops else None)
            loop_need = kk is not None and (copies is None or n < kk)
            if not once and not loop_need:
                return (1, -_makes_sleep(cid), weight), (
                    "循环卡已经有了" if kk is not None else "可复用、不是这个流派的循环卡"
                )
            strong = weight >= flows.STRONG_WEIGHT or bool(know and know.strong(cid))
            main = bool(buff) and flows.buff_main(
                cid, buff
            )  # 主打拿手 buff（手拍子「好印象の150%分」对ことね）也算合拍
            gives = bool(buff) and (_gives_buff(cid, buff) or main)
            dup = fam == NIA and once and n > 0
            label = (
                f"攻略 {know.source(cid)}"
                if know and know.source(cid)
                else f"社区优先表 {weight}（{'、'.join(pr.get('sources') or [])}）"
            )
            tags = [t for t, on in ((label, strong), ("一次性", once), ("还缺的循环卡", loop_need and not once),
                                    (f"主打{buff}" if main else f"加{buff}", gives), (f"牌组里已有 {n} 张", dup)) if on]  # fmt: skip
            return (2, strong, loop_need, gives, dup, _card_rarity(cid), weight, up), "、".join(tags)

        def info(i: int) -> tuple[tuple, str]:
            if other_flow:
                return info_flow(i)
            cid, up = raw[i]["card_id"], raw[i].get("upgrade") or 0
            n = (copies or {}).get(cid, 0)
            once = is_once(cid) is True
            # 打法匹配先于「一次性」（协调 11-18 N.I.A 第二局拿了 キラメキ / キセキの魔法 / ファンシーチャーム）：
            # 主要得分 / 使用条件挂在好印象上的卡，对不是好印象流派的偶像一律算冲突；同是冲突时，不主打好印象的、一次性的污染小
            reliant = buff != "好印象" and _impression_main(cid)
            # 卡表：HIRO_GUIDES_HIRO=1 且有条目时从攻略读（hiro.brain.guides_hiro 迁过去的同一份），否则手写表；两者等价
            conflict = know.conflict(cid) if know else conflict_like(cid)
            if reliant or conflict or _is_trouble_card(cid):
                return (0, int(not reliant), int(once), card_value(cid, up)), (
                    "主打好印象" if reliant else "冲突 / 好印象系"
                )
            kk = know.keep(cid) if know else (core_keep(cid) if cid in SMALL_DECK_CORE else None)
            loop_need = kk is not None and (copies is None or n < kk)
            if not once and not loop_need:
                return (1, -_makes_sleep(cid), card_value(cid, up)), (
                    "循环卡已够张数" if kk is not None else "可复用、不是循环卡"
                )
            strong = know.strong(cid) if know else _tier_rank(cid) >= TIER_RANK["A"]
            tier = (know.tier(cid) if know else (YARUKI_CARDS.get(cid) or {}).get("tier")) or "必拿"
            gives = bool(buff) and _gives_buff(cid, buff)
            dup = fam == NIA and once and n > 0
            tags = [t for t, on in (("社区 " + str(tier), strong), ("一次性", once), ("还缺的循环卡", loop_need and not once),
                                    (f"加{buff}", gives), (f"牌组里已有 {n} 张", dup)) if on]  # fmt: skip
            # 同档再看稀有度、价值分：がむしゃら 只因换卡保留进了卡表（value 2），卡效要 トラブル 2 张以上（第七局复盘：几乎是废牌），不该排在 SSR 前
            return (2, strong, loop_need, gives, dup, _card_rarity(cid), card_value(cid, up), up), "、".join(tags)

        scored = {i: info(i) for i in known}
        best = max(known, key=lambda i: (scored[i][0], -i))
        key, why = scored[best]
        use("nia.card_once_first")
        use("nia.card_loop_three")
        use("hajime.card_slim")
        if key[0] < 2 and _budget_ok(budget, "reroll"):
            cites = "；".join(hr.cite(r) for r in used)
            return Decision(Action(ActionKind.REROLL), f"选卡：候选里没有一次性的卡或还缺的循环卡，先刷新（{_budget_text(budget)}；{cites}）", "rule", 0.8, cands,
                            decider="select_card")  # fmt: skip
        if key[0] < 2 and _budget_ok(budget, "exclude") and len(known) > 1:
            # 刷新用完：先禁用最差的那张换一张新的（协调 11-18：ファンシーチャーム 那页禁用还剩 2 次没用）
            worst = min(known, key=lambda i: (scored[i][0], i))
            cites = "；".join(hr.cite(r) for r in used)
            return Decision(Action.exclude_card(worst), f"选卡：没有一次性的卡或还缺的循环卡、刷新用完，禁用 {_card_label(raw[worst])}（{scored[worst][1]}）换一张"
                            f"（{_budget_text(budget)}；{cites}）", "rule", 0.75, cands, decider="select_card")  # fmt: skip
        if key[0] == 2:
            if other_flow and key[2]:
                use("nia.card_loop_parts")
            if key[3]:
                use("hajime.card_signature_buff")
            if key[4]:
                use("nia.card_dup_once")
        cites = "；".join(hr.cite(r) for r in used)
        lead = "选卡" if key[0] == 2 else "选卡：三张都不理想、刷新和禁用用完，拿最不污染的"
        return Decision(Action.activate_effect(best), f"{lead} {_card_label(raw[best])}（{why}）（{cites}）", "rule", 0.85 if key[0] == 2 else 0.6, cands,
                        decider="select_card")  # fmt: skip

    @staticmethod
    def _fish_for_musts(raw: list[dict], known: list[int], budget: dict, owned: set[str],
                        cands: list[Candidate], bad: set = frozenset()) -> Decision | None:  # fmt: skip
        """必拿卡还缺、候选里没有时主动换候选（第 3 局复盘 §4.1-3：27 个候选位没见到せのびの魔法 / 届いて！，刷新 4、禁用 3 一次没用）。
        - 候选里有攻略没提到的卡、还有禁用次数：禁用最差那张（只换一张、保留其余，且这张以后不再出现）；
        - 候选里没有 S / A 级、本页预算还有刷新：刷新。次数未识别时不硬做，交给 LLM。预算见 _pick_budget。"""
        missing = [v["name"] for k, v in YARUKI_CARDS.items() if v.get("must") and k not in owned]
        if not missing:
            return None
        tiers = {i: _tier_rank(raw[i]["card_id"]) for i in known}
        # 只禁用真正没用 / 冲突的牌（总控 2026-11-02，第十五局为找 せのび 禁用了 イメトレ）：冲突 / 好印象系、眠気 等トラブル、
        # 小牌组不该拿的（unwanted）。「攻略没提到」不算没用：イメトレ、えいえいおー、ワクワク 等在用户标注里是要打的叠元気牌（R1）
        junk = [i for i in known if i in bad or conflict_like(raw[i]["card_id"]) or _is_trouble_card(raw[i]["card_id"])]
        # 禁用哪张：和换 / 删同一个顺序，最该清掉的先禁（用户 2026-11-03：ハイテンション「这张卡不该进牌组，优先清掉」）
        junk.sort(key=lambda i: cull_key(raw[i]["card_id"], raw[i].get("upgrade") or 0), reverse=True)
        if budget.get("exclude_left") is None and budget.get("reroll_left") is None:
            return None  # 次数都没识别：不硬做，交给 LLM（每页最多 1 次）
        excludes, rerolls = budget["exclude"], budget["reroll"]
        tail = f"（{_budget_text(budget)}）"
        # 先刷新、后禁用：禁用整局只有约 3 次，只在本页有值得留的牌（S / A 级）时用来换掉一张不该拿的（总控 2026-11-02 第十五局：前几页就用到只剩 1 次）
        # 候选里有 S / A 级卡时不刷新（第 6 局第 1 天：为找必拿把 ワクワクが止まらない+、成長痛 两张 A 级刷掉了）
        if rerolls and max(tiers.values(), default=0) < TIER_RANK["A"]:
            return Decision(Action(ActionKind.REROLL), f"必拿还缺 {'、'.join(missing)}，候选里没有 S / A 级卡：刷新{tail}", "rule", 0.8,
                            cands, decider="select_card")  # fmt: skip
        if excludes and junk and len(junk) < len(known):
            i = junk[-1]
            return Decision(Action.exclude_card(i), f"必拿还缺 {'、'.join(missing)}：禁用不该拿的 {raw[i].get('name') or raw[i]['card_id']}，"
                            f"保留其余候选、换一张新的{tail}", "rule", 0.8, cands, decider="select_card")  # fmt: skip
        if excludes and junk:
            i = junk[-1]
            return Decision(Action.exclude_card(i), f"必拿还缺 {'、'.join(missing)}、本页刷新预算用完：禁用 {raw[i].get('name') or raw[i]['card_id']} 换一张{tail}",
                            "rule", 0.7, cands, decider="select_card")  # fmt: skip
        return None

    @staticmethod
    def _avoid_conflict(
        raw: list[dict], known: list[int], budget: dict, cands: list[Candidate], bad: set = frozenset()
    ) -> Decision | None:
        """候选全是冲突牌（ハイテンション、好印象系）时：本页预算内有禁用就禁用一张换新的，有刷新就刷新；都没有才交给兜底（决策-07）。"""
        if not known or not all(i in bad for i in known):
            return None
        excludes = budget["exclude"] if budget.get("exclude_left") is not None else None
        rerolls = budget["reroll"] if budget.get("reroll_left") is not None else None
        names = "、".join(raw[i].get("name") or raw[i]["card_id"] for i in known)
        worst = min(
            known, key=lambda i: cull_key(raw[i]["card_id"], raw[i].get("upgrade") or 0)
        )  # 和换 / 删同序：最该清掉的先禁
        if excludes:
            return Decision(Action.exclude_card(worst), f"候选全是不该拿的牌（{names}；小牌组：冲突牌、可复用的非核心牌不拿）：禁用一张换新的（禁用还剩 {excludes}）",
                            "rule", 0.8, cands, decider="select_card")  # fmt: skip
        if rerolls:
            return Decision(
                Action(ActionKind.REROLL),
                f"候选全是不该拿的牌（{names}）：刷新（还剩 {rerolls}）",
                "rule",
                0.8,
                cands,
                decider="select_card",
            )
        return None

    @staticmethod
    def _card_fallback(raw: list[dict], known: list[int], recommended, bad: set | None = None) -> tuple[int, str]:
        """社区评级 S > A > B > 有说明 > 其余；同级先游戏推荐。bad：本页不该拿的候选（小牌组），没给时按卡判断。"""
        if bad is None:
            bad = {i for i in known if unwanted(raw[i]["card_id"])}

        def rank(i: int) -> tuple:
            k = YARUKI_CARDS.get(raw[i]["card_id"])
            if i in bad:
                # 冲突牌 / 可复用的非核心牌最后才拿；都不该拿时一次性的比可复用的好（打一次就除外，不长期占牌堆）
                return (-2 if is_once(raw[i]["card_id"]) is False else -1, i == recommended)
            if raw[i]["card_id"] in LOW_PICK_NO_DELETE:
                return (-0.5, i == recommended)  # 价值很低：有更好的（包括社区没提到的一次性牌）就不拿
            return (_tier_rank(raw[i]["card_id"]) or (0.5 if k else 0), i == recommended)

        best = max(known, key=rank)
        k = YARUKI_CARDS.get(raw[best]["card_id"])
        return best, f"兜底：社区评级最高（{k['name'] + ' ' + (k['tier'] or '—') if k else '均未提及，取推荐'}）"

    # ---------------------------------------------------------------- 饮料
    def select_drink(self, state: GameState, *, can_skip: bool = True) -> Decision:
        """饮料三选一（用户 2026-11-02 第十六局复盘：取代静态优先表）。候选 = 拿哪瓶（栏满时再加「丢哪瓶」）/ 不拿，
        按做完之后的持有组合比较：社区默认档位（hiro.brain.drinks）给默认选择，完整对局模拟显著更好（> 2 个标准误）时偏离并记录。"""
        from hiro.brain import drinks as dk
        from hiro.state.context import drink_name

        raw = state.page_fields.get("candidates") or []
        cands = [Candidate(Action.activate_effect(i), note=c.get("name")) for i, c in enumerate(raw)]
        if not raw:
            if can_skip:
                return Decision(
                    Action.exclude_card(0), "候选为空：不拿", "fallback", 0.2, cands, decider="select_drink"
                )
            return Decision(Action.ui(ActionKind.WAIT, why="饮料候选未识别"), "候选未识别、这一页又不能不拿：停下等重新识别（不盲选第 1 瓶）",
                            "fallback", 0.1, cands, warnings=["SELECT_DRINK candidates 为空"], decider="select_drink")  # fmt: skip
        known = [(i, c) for i, c in enumerate(raw) if c.get("drink_id")]
        if not known:
            if not can_skip:
                return Decision(Action.ui(ActionKind.WAIT, why="饮料候选都没认出"), "饮料都没认出、这一页又不能不拿：停下等重新识别", "fallback", 0.1, cands,
                                warnings=["SELECT_DRINK 候选没有 drink_id"], decider="select_drink")  # fmt: skip
            return Decision(
                Action.exclude_card(0), "饮料都没认出：不拿", "fallback", 0.3, cands, decider="select_drink"
            )
        ps = self.produce_state(state)
        held, capacity, full, warn = self._drink_holding(state, ps)
        if full and held is None:
            pick = min(known, key=lambda x: _drink_rank(x[1]["drink_id"]))[0]
            return Decision(Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False, "pick_index": pick}),
                            f"饮料栏满、持有饮料没识别：先不收 {raw[pick].get('name')}（要识别持有饮料才能决定丢哪瓶）", "fallback", 0.3, cands,
                            warnings=["SELECT_DRINK 栏满但没有 held"], decider="select_drink")  # fmt: skip
        hold = [h.get("drink_id") for h in held or []]
        stage = dk.stage_of(ps.mode if ps else None)
        opts = []
        for i, c in known:
            name = c.get("name") or drink_name(c["drink_id"])
            if full:
                for j, h in enumerate(held):
                    if h.get("visible") is False:
                        continue
                    opts.append(dk.Option(f"pick{i}-drop{h['index']}", f"拿 {name}、丢 {h.get('name') or drink_name(h.get('drink_id'))}",
                                          hold[:j] + [c["drink_id"]] + hold[j + 1:],
                                          Action(ActionKind.DISCARD_DRINK, {"discard": [h["index"]], "receive": True, "pick_index": i})))  # fmt: skip
            else:
                opts.append(dk.Option(f"pick{i}", f"拿 {name}", hold + [c["drink_id"]], Action.activate_effect(i)))
        if full:
            best_in = min(known, key=lambda x: _drink_rank(x[1]["drink_id"]))[0]
            opts.append(
                dk.Option(
                    "keep",
                    "不收（保持现有）",
                    hold,
                    Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False, "pick_index": best_in}),
                )
            )
        elif can_skip:
            opts.append(dk.Option("skip", "不拿", hold, Action.exclude_card(0)))
        res = dk.choose(
            opts,
            stage,
            capacity,
            sim_context=self._drink_sim_context(ps, stage),
            where="SELECT_DRINK",
            take_on_tie=True,
        )
        return self._drink_decision(res, opts, stage, "select_drink", warn, state)

    def _drink_holding(self, state: GameState, ps) -> tuple[list | None, int, bool, list]:
        """持有饮料 [{index, drink_id, name}]、栏容量、是否已满、警告。page_fields.held 优先，没有时用 ProduceState.drinks（已知时）。"""
        held = state.page_fields.get("held")
        warn = []
        known = ps is not None and "drinks" not in self.unknown_fields(state, ps) and bool(ps.drinks)
        if held is None and known:
            held = [{"index": i, "drink_id": d} for i, d in enumerate(ps.drinks)]
        elif held is not None and known and state.page_fields.get("complete") is False and len(ps.drinks) > len(held) \
                and all(h.get("drink_id") in (None, ps.drinks[h["index"]]) for h in held if h.get("index") is not None and h["index"] < len(ps.drinks)):  # fmt: skip
            # 画面只读到一部分持有（第十六局第 18 天：4 瓶只读到 2 瓶）：比较用状态机的完整持有，能丢的只是画面上那几瓶
            seen = {h["index"]: h for h in held}
            held = [
                dict(seen.get(i) or {"index": i, "drink_id": d}, visible=i in seen) for i, d in enumerate(ps.drinks)
            ]
            warn.append(f"画面只读到 {len(seen)} 瓶持有：按状态机的 {len(ps.drinks)} 瓶比较，只丢画面上能点到的")
        if held is not None:
            raw_drinks = [
                d.get("drink_id") if isinstance(d, dict) else d for d in (state.produce or {}).get("drinks") or []
            ]
            held, w = _resolve_held(held, list(ps.drinks) if ps is not None and ps.drinks else (raw_drinks or None))
            warn += w
        capacity = (
            ps.drink_capacity if ps is not None and ps.drink_capacity else None
        ) or 3  # 同 ProduceState.drink_capacity 的缺省
        full = state.page_fields.get("full")
        if full is None:
            full = held is not None and len(held) >= capacity
        if held is None:
            warn.append("持有饮料未识别：按空栏比较")
        elif any(not h.get("drink_id") for h in held):
            warn.append(f"持有饮料有 {sum(not h.get('drink_id') for h in held)} 瓶没认出：按未知（其他类）算")
        if full and held and state.page_fields.get("full"):
            capacity = len(held)  # 画面说满了：栏就这么大（容量没读到时默认 4 会把空位当成还在）
        return held, max(capacity, len(held or [])), bool(full), warn

    def _drink_sim_context(self, ps, stage: str) -> dict | None:
        """饮料比较的模拟上下文：牌组（状态机记账；没有时 resource 用参考牌组）、体力（下一场考试开考时：选拔开考自动回复，按满体力算）、倍率。"""
        if ps is None or not _sim_ready():
            return None  # 开源版：模拟器主数据没构建时只按社区规则
        if _is_hajime_nia(ps.mode):
            return None  # 初 / N.I.A：饮料模拟用的是 H.I.F 选拔考试的配置，对不上，只按社区默认档位比
        deck = [(c.card_id, c.upgrade) for c in ps.deck] if ps.deck else None
        permil = None
        if stage != "selection":
            from hiro.sim.interval import CONTEXT

            deck = deck or [tuple(x) for x in CONTEXT.get("deck") or []] or None
            permil = CONTEXT.get("permil")
        max_hp = ps.max_stamina
        if not max_hp:
            return None  # 最大体力没从画面读到：只按社区规则（不拿写死的数值去模拟）
        hp = max_hp if stage != "r2" else (ps.stamina or max_hp)
        return {"exam": stage, "deck": deck, "hp": hp, "max_hp": max_hp, "permil": permil}

    def _drink_resolve(self, res: dict, opts: list, stage: str, path: str, state: GameState | None) -> None:
        """drinks.choose 拿不准（res["unsure"]）且这条 LLM 路径开着时，让 LLM 在社区默认 + 其余不同的持有组合里选，改写 res["pick"]，
        LLM 的记录放 res["llm_decision"]。只做一次（同一个 res 再调直接返回）。"""
        from hiro.brain import drinks as dk

        if "llm_decision" in res or not res.get("unsure") or state is None or not llm_path_on(path) or self.llm is None:
            return
        rows = {r["key"]: r for r in res["rows"]}
        by_key = {o.key: o for o in opts}
        order = [res["pick"]] + [
            by_key[r["key"]] for r in sorted(res["rows"], key=lambda r: r["rule_rank"]) if r["key"] in by_key
        ]
        picked, seen = [], set()
        for o in order:
            sig = tuple(sorted(d for d in o.drinks if d))
            if sig in seen or o.action is None:
                continue
            seen.add(sig)
            picked.append(o)
        picked = picked[:4]
        options = []
        for o in picked:
            r = rows.get(o.key) or {}
            info = {
                "名字": o.label,
                "之后持有": dk.describe(list(o.drinks), stage),
                "社区档位": r.get("tiers"),
                "社区排名": r.get("rule_rank"),
            }
            if o.cost:
                info["花 P"] = o.cost
            if "sim_gain" in r:
                info.update({"模拟期望": r["sim_mean"], "比社区默认": f"{r['sim_gain']:+,} ± {r['sim_gain_se']:,}"})
            options.append((o.action, info))
        stub = Decision(res["pick"].action, res["why"], "rule", 0.85, decider=path)
        d = self._ask_choice(path, stub, options, f"饮料（下一场：{ {'selection': '选拔', 'r1': '本战 R1', 'r2': '本战 R2'}.get(stage, stage)}）：选做完之后的持有组合",
                             res["unsure"], state, {"饮料栏容量": max(len(o.drinks) for o in opts)})  # fmt: skip
        if d is stub:
            return
        res["llm_decision"] = d
        res["pick"] = next(o for o, (a, _) in zip(picked, options) if a is d.action)

    def _drink_decision(
        self, res: dict, opts: list, stage: str, decider: str, warn: list, state: GameState | None = None
    ) -> Decision:
        from hiro.brain import drinks as dk

        self._drink_resolve(res, opts, stage, decider, state)
        pick = res["pick"]
        rows = {r["key"]: r for r in res["rows"]}
        cands = []
        for o in opts:
            r = rows.get(o.key) or {}
            prev = {
                "选项": o.label,
                "持有": dk.describe(list(o.drinks), stage),
                "社区档位": r.get("tiers"),
                "社区排名": r.get("rule_rank"),
            }
            if "sim_gain" in r:
                prev.update({"模拟期望": r["sim_mean"], "比社区默认": f"{r['sim_gain']:+,} ± {r['sim_gain_se']:,}"})
            cands.append(Candidate(o.action, preview=prev, expected=r.get("sim_mean"), note=o.label))
        why = f"{res['why']}；之后持有：{dk.describe(list(pick.drinks), stage)}"
        w = list(warn)
        if res.get("deviation"):
            w.append(
                f"community_deviation：{res['deviation']['rule']} → {res['deviation']['pick']}（{res['deviation']['gain']:+,} ± {res['deviation']['gain_se']:,}）"
            )
        ld = res.get("llm_decision")
        if ld is not None:
            why = f"{ld.reason}（LLM 选「{pick.label}」；{why}）" if ld.source == "llm" else why
            return Decision(pick.action, why, ld.source, ld.confidence, cands, warnings=w + ld.warnings, line=ld.line,
                            llm=ld.llm, decider=decider)  # fmt: skip
        return Decision(
            pick.action, why, "sim" if res.get("deviation") else "rule", 0.85, cands, warnings=w, decider=decider
        )

    def _drink_free(self, state: GameState) -> int | None:
        ps = self.produce_state(state)
        if ps is None or "drinks" in self.unknown_fields(state, ps):
            return None
        return ps.drink_capacity - len(ps.drinks)

    def special_guidance(self, state: GameState) -> Decision:
        """特別指導：定制哪张卡、选哪一项（一次一个动作，定制完主循环重新观测再调用）。按社区定制优先表（knowledge.CUSTOMIZE_PRIORITY）。
        page_fields.cards = [{index（牌组下标）, card_id, upgrade, customizations: [{id, price, available}]}]，格式见 docs/llm-context.md；
        缺失时牌组已知就按 master 的定制项与基础价推出（价格未含折扣，执行器以画面为准）。"""
        from hiro.brain.idol_know import customize_plan
        from hiro.state.knowledge import CUSTOMIZE_LEVELS, CUSTOMIZE_PRIORITY

        ps = self.produce_state(state)
        # 初 / N.I.A 有偶像卡攻略的 card_upgrade 条目时按它定制（协调 11-20；广要 HIRO_GUIDES_HIRO=1，迁过去的条目和这两张表一致）；H.I.F 照旧
        if (plan := customize_plan(ps.mode if ps is not None else (state.produce or {}).get("mode"),
                                   ps.idol_card_id if ps is not None else (self.profile.idol_id if self.profile else None))) is not None:  # fmt: skip
            CUSTOMIZE_PRIORITY, CUSTOMIZE_LEVELS = plan  # noqa: N806
        cards, warn = state.page_fields.get("cards"), []
        if cards is None and ps is not None and ps.deck and "deck" not in self.unknown_fields(state, ps):
            cards, warn = _master_customize_offers(ps), ["识别未提供特別指導页的 cards：按 master 定制项与基础价推出"]
        end = Action(ActionKind.CUSTOMIZE_END)
        if not cards:
            return Decision(
                end,
                "可定制的卡未识别，不定制直接结束",
                "fallback",
                0.3,
                warnings=warn + ["识别未提供 cards"],
                decider="special_guidance",
            )
        # P：页面给了就用页面的；否则用 state.produce 里上一次的；都没有就不按价格过滤（执行器在画面上判断，P 不够时按钮是灰的）
        p = state.page_fields.get("produce_point")
        if p is None:
            p = ps.produce_point if ps is not None else (state.produce or {}).get("produce_point")
        cands = []
        for card in cards:
            for opt in card.get("customizations") or []:
                cands.append(Candidate(Action(ActionKind.CUSTOMIZE_CARD, _customize_args(card, opt)),
                                       note=f"{opt['id']}（{opt.get('price')}P）"))  # fmt: skip
        cands.append(Candidate(end, note="结束"))
        done_here = getattr(self, "_customized_here", set())

        def levels_for(card_id: str, price, steps=None) -> int | None:
            """这一次要做几级：目标级数，按 P 能付的封顶；必须做满的卡 P 不够做满（且没开始过）就返回 None（不做）。
            steps：逐级价格（识别 / master 的 levels，例如 えいえいおー 40 / 40 / 70）；没有时按每级 price 算。"""
            target, full = CUSTOMIZE_LEVELS.get(card_id, (1, False))
            costs = list(steps or [])[:target] or [price] * target
            afford = target
            if p is not None and all(c for c in costs):
                total, afford = 0, 0
                for c in costs:
                    if total + c > p:
                        break
                    total += c
                    afford += 1
            if afford < 1 or (full and afford < target):
                return None
            return afford

        # 先做必须做满的（えいえいおー やる気 3 级），再按优先表：先给还没定制过的卡，再升级定制过的（新的一次特別指導）。
        # 一张卡一次特別指導只做一次（实行后就回不来），所以每张卡只出一个动作，带 levels
        musts = [(c, n, k, w) for c, n, k, w in CUSTOMIZE_PRIORITY if CUSTOMIZE_LEVELS.get(c, (1, False))[1]]
        rest = [(c, n, k, w) for c, n, k, w in CUSTOMIZE_PRIORITY if not CUSTOMIZE_LEVELS.get(c, (1, False))[1]]
        for order, fresh in ((musts, None), (rest, True), (rest, False)):
            for card_id, name, keys, why in order:
                for card in cards:
                    if card.get("card_id") != card_id or (card.get("index"), card_id) in done_here:
                        continue
                    if fresh is True and card.get("customized"):
                        continue
                    for key in keys:
                        for opt in card.get("customizations") or []:
                            price = opt.get("price")
                            # available 为 None（识别读不到按钮状态，q11 第 19 天每项都是 null）不算不可用，只有明确 False 才跳过
                            if (
                                key not in _customize_text(opt["id"])
                                or opt.get("available") is False
                                or (p is not None and price is not None and price > p)
                            ):
                                continue
                            n = levels_for(card_id, price, opt.get("levels"))
                            if n is None and not (fresh is None and card.get("customized")):
                                continue
                            n = n or max(
                                1, min(CUSTOMIZE_LEVELS[card_id][0], p // price if p and price else 1)
                            )  # 做满的卡已开始过：P 能付几级做几级
                            self._customized_here = done_here | {(card.get("index"), card_id)}
                            args = dict(_customize_args(card, opt), levels=n)
                            return Decision(Action(ActionKind.CUSTOMIZE_CARD, args),
                                            f"定制 {name}：{why}（{'+'.join(map(str, (opt.get('levels') or [price] * n)[:n]))}P，{n} 级）{'；已定制过，升级' if card.get('customized') else ''}", "rule", 0.8, cands,
                                            warnings=warn, decider="special_guidance")  # fmt: skip
        rule = Decision(
            end, f"社区优先表里没有能定制的卡（P {p}）", "rule", 0.6, cands, warnings=warn, decider="special_guidance"
        )
        # 决策点补全：优先表做完了，但还有付得起的表外定制 → 拿不准做不做，交 LLM（结束是规则的选择）。只问一次：选了某项照做，下一帧再到这里不重复问
        extra = (
            []
            if getattr(self, "_guidance_asked", False)
            else [
                (card, opt)
                for card in cards
                if (card.get("index"), card.get("card_id")) not in done_here
                for opt in card.get("customizations") or []
                if opt.get("available") is not False and p is not None and opt.get("price") and opt["price"] <= p
            ]
        )
        if extra and llm_path_on("special_guidance") and self.llm is not None:
            self._guidance_asked = True
            extra.sort(key=lambda co: -card_value(co[0].get("card_id"), co[0].get("upgrade") or 0))
            options = [(end, {"名字": "结束（剩下的 P 留着）"})] + [
                (Action(ActionKind.CUSTOMIZE_CARD, dict(_customize_args(card, opt), levels=1)),
                 {"名字": f"定制 {_card_label(card)}：{_customize_desc(opt['id'])}", "花 P": opt["price"]}) for card, opt in extra[:4]]  # fmt: skip
            d = self._ask_choice("special_guidance", rule, options, "特別指導（技能卡定制）：剩下的 P 还要不要定制一张（一级）",
                                 f"社区优先表里的都做完了，还剩 {p}P，有 {len(extra)} 项表外定制付得起", state)  # fmt: skip
            if d.action.kind is ActionKind.CUSTOMIZE_CARD:
                self._customized_here = done_here | {(d.action.args["card_index"], d.action.args["card_id"])}
                return d
            rule = d
        self._customized_here = set()  # 离开特別指導：下一次重新计
        self._guidance_asked = False
        return rule

    # ---------------------------------------------------------------- 偶像之路
    TOWER_BUDGET_S = 900.0  # 一层编成评估的总时限（协调 11-14：15 分钟）
    TOWER_WORKERS = 4  # evaluate 的进程数（开 4 个以上先跟协调说）
    TOWER_RETRY_MAX = 3  # 每层免费重试上限（协调 11-14）
    TOWER_RETRY_SAME = 0.85  # 失败分数 ≥ 过关线 × 这个比例：同编成再打；更低换下一个编成
    TOWER_EVAL = None  # 评估函数（测试替换）；None = Rust（hiro.search.tower.evaluate_rust），Rust 包没有 plan_many 时 hiro.bench_bridge.tower.evaluate
    TOWER_SEED0 = 1000003  # 种子 = TOWER_SEED0 × (i + 1) + 17（所有候选同一批，配对比）

    def tower_formation(self, state: GameState) -> Decision:
        """偶像之路这一层的编成（用户 11-14：不用おまかせ、接 bench 算法；追求三星通关、在全部回忆里选最优组合。
        设计稿 偶像之路-编成决策-设计.md）。

        page_fields：tower_id、layer、idol_card_id?、mechanics?（画面上可选的考试机制，没读到时按已知的层绑定）、
          sub_slots（副回忆几格，缺省 1）、candidates：[{index, memory_id, params: [Vo, Da, Vi], max_stamina, deck: [「卡ID+强化/定制*次数」],
          items, drinks, rank?, current?}]（这条路偶像的全部回忆；回忆选择页没接上之前只给当前编成一组、current=True）、
          shown_bonus? / shown_stamina?（编成页画面读到的三属性倍率 / 体力：只有当前编成一组时以它为准）。
        算法：每种考试 × 每份主回忆（副回忆按倍率公式贪心 + 两两交换）→ bench 引擎 + UINet 同种子逐轮淘汰（tower.ROUNDS：4 局留 8 →
        累计 16 局留 3 → 累计 48 局），按「三星概率 → 拿第 1 的概率 → 平均分」排；第一名明显更好 → sim，接近 / 模拟不了 → LLM
        （tower_formation）→ 兜底（按倍率和）。结果按（塔, 层, 回忆库存签名）缓存，回忆有增减 / 强化变了就失效。
        输出 SelectFormation {kind: tower, mechanic, main, subs}（main / subs 是 candidates 的 index）。"""
        import time

        from hiro.bench_bridge import tower as tw
        from hiro.brain import tower as tf

        pf = state.page_fields
        tower_id, layer = pf.get("tower_id"), pf.get("layer")
        cands = sorted(pf.get("candidates") or [], key=lambda c: c.get("index", 0))
        k = int(pf.get("sub_slots") or 1)
        stop = lambda why: Decision(  # noqa: E731
            Action.ui(ActionKind.WAIT, why=why), why, "fallback", 0.1, warnings=[why], decider="tower_formation"
        )
        if not tower_id or layer is None or not cands:
            return stop("偶像之路编成：塔 / 层 / 候选回忆没读出来，停下等重新识别")
        mechs = list(pf.get("mechanics") or tw.layer_mechanics(tower_id, int(layer)) or [None])
        idol = pf.get("idol_card_id") or _tower_idol(tower_id, int(layer))
        key = (tower_id, int(layer), tuple(sorted(str(m) for m in mechs)), k, tf.signature(cands))
        cache = getattr(self, "_tower_cache", {})
        notes: list[str] = []
        if key in cache:
            combos, evaluated, notes = cache[key]
            notes = notes + ["用这一层的缓存评估（回忆库存没变）"]
        else:
            combos = []
            for mech in mechs:
                lex = tw.layer_exam(tower_id, int(layer), mech) if mech else tw.layer_exam(tower_id, int(layer))
                if lex is None:
                    notes.append(f"考试 {mech or '（未知）'} 的设置查不到")
                    continue
                for main in cands:
                    subs = tf.choose_subs(lex, main, cands, k)
                    lo = tf.loadout(lex, main, subs, idol)
                    if main.get("current") and len(cands) == 1:
                        # 当前编成（只有这一组时）：倍率 / 体力以编成页画面读到的为准，公式算的只作对照
                        shown, hp = pf.get("shown_bonus"), pf.get("shown_stamina")
                        if shown and len(shown) == 3:
                            if lo["score_bonus_permils"] and list(lo["score_bonus_permils"]) != list(shown):
                                notes.append(f"倍率按画面 {list(shown)}（公式算的 {lo['score_bonus_permils']}）")
                            lo["score_bonus_permils"] = [int(x) for x in shown]
                        if hp:
                            lo["max_stamina"] = lo["starting_stamina"] = int(hp)
                    combos.append(
                        {"lex": lex, "main": main, "subs": subs, "loadout": lo, "why_not": tf.evaluable(lo), "rows": []}
                    )
            if not combos:
                return stop("偶像之路编成：这一层没有一种考试查得到设置（" + "；".join(notes) + "）")
            # 「除外 SSR ≤ N」这类条件：牌组里打出即除外的 SSR 张数只作排序参考（static_margin，软惩罚），不筛编成（协调 11-14：
            # 评估换成 Rust GNRPA 后，能控牌的搜索自己判断满不满足得了；AS-FPKTEPQC 8 张的牌组 GNRPA 打到除外 5 张拿过三星）
            for c in combos:
                c["static"] = tf.static_check(c["loadout"], c["lex"].get("star_conditions"))
            over = [c for c in combos if c["static"]["ok"] is False]
            if over:
                notes.append(f"{len(over)} 个编成打出即除外的 SSR 超过通关条件（不筛，靠模拟判断，排序时扣分）")
            evaluated = self._tower_rounds([c for c in combos if c["why_not"] is None], notes, time.monotonic())
            for c in combos:
                c["sim"] = tf.summarize(c["rows"], c["lex"].get("border"), c["lex"].get("star_conditions"))
                c["sim"]["static_margin"] = c["static"]["margin"]
                c["bonus_sum"] = sum(c["loadout"]["score_bonus_permils"] or tf._vec(c["main"]))
            cache[key] = (combos, evaluated, list(notes))
            self._tower_cache = cache
        if evaluated:
            # 先按撑到第几轮（局数）再按排序键：早轮被淘汰的局数少、运气好时分数虚高，不能排到决赛的前面
            order = sorted(
                [c for c in combos if c["sim"]["n"]], key=lambda c: (c["sim"]["n"], tf.rank_key(c["sim"])), reverse=True
            )
        else:
            order = sorted(combos, key=lambda c: -c["bonus_sum"])

        def act(c):
            return Action(ActionKind.SELECT_FORMATION, {"kind": "tower", "mechanic": c["lex"]["mechanic"], "main": c["main"].get("index"),
                                                        "subs": [s.get("index") for s in c["subs"]]})  # fmt: skip

        def desc(c):
            s = c["sim"]
            if not s["n"]:
                sim = f"没模拟（{c['why_not'] or '评估不可用'}）"
            else:
                parts = [f"三星 {s['p3']:.0%}" if s["p3"] is not None else None, f"第 1 名 {s['first']:.0%}" if s["first"] is not None else None,
                         f"条件满足 {s['cond']:.0%}" if s.get("cond") is not None else None, f"平均 {s['mean']:,}"]  # fmt: skip
                sim = f"模拟 {s['n']} 局：" + "、".join(x for x in parts if x)
            return f"{c['lex']['mechanic']}·主回忆 {c['main'].get('memory_id') or c['main'].get('index')}（副 {[x.get('index') for x in c['subs']]}）：{sim}"

        self._tower_rank = {"key": (tower_id, int(layer)), "order": order}
        best = order[0]
        cand_list = [Candidate(act(c), note=desc(c), expected=c["sim"]["mean"]) for c in order[:8]]
        reason = f"偶像之路第 {layer} 层：{desc(best)}" + ("；" + "；".join(notes) if notes else "")
        if evaluated and (len(order) == 1 or tf.clearly_better(best["sim"], order[1]["sim"])):
            return Decision(act(best), reason, "sim", 0.85, cand_list, decider="tower_formation")
        rule = Decision(act(best), reason + ("；前两名在噪声内" if evaluated else "；没有模拟，按本层倍率和排"),
                        "sim" if evaluated else "fallback", 0.5 if evaluated else 0.3, cand_list, decider="tower_formation")  # fmt: skip
        options = [(act(c), {"名字": desc(c), "考试机制": c["lex"]["mechanic"], "过关线": c["lex"].get("border"), "回合数": c["lex"].get("turns"),
                             "主回忆牌组": c["main"].get("deck"), "主回忆道具": c["main"].get("items"), "倍率": c["loadout"]["score_bonus_permils"],
                             "模拟": c["sim"]}) for c in order[:4]]  # fmt: skip
        why = "前两名在模拟噪声内" if evaluated else ("；".join(notes) or "这一层的编成没法模拟")
        return self._ask_choice("tower_formation", rule, options, f"偶像之路第 {layer} 层：选考哪种考试、带哪份主回忆（副回忆已按倍率挑好）", why, state,
                                {"说明": "目标三星通关（第 1 名 + 本层条件），其次拿第 1、再看分数；模拟用的出牌算法 UINet 没在偶像之路上训练过，数值只作参考"})  # fmt: skip

    def _tower_rounds(self, ok: list[dict], notes: list[str], t0: float) -> bool:
        """逐轮淘汰（tower.ROUNDS）：每轮给还留着的组合补打到累计局数，按排序键留前几个。超时 TOWER_BUDGET_S 停在已跑完的结果。
        evaluate 不支持 detail / workers（旧版中转层）时退回只要分数。返回有没有评估出结果。"""
        import time

        from hiro.bench_bridge import tower as tw
        from hiro.brain import tower as tf

        if not ok:
            return False
        run = self.TOWER_EVAL
        if run is None:
            # 缺省：Rust 引擎 + GNRPA 评估（用户 11-14：不绑死 uinet / GKMS）；没有带 plan_many 的 Rust 包时退回 bench 中转层（GKMS + uinet）
            from hiro.search import tower as st

            run = st.evaluate_rust if st.rust_available() else tw.evaluate
        alive, done = list(ok), 0
        for upto, keep in tf.ROUNDS:
            left = self.TOWER_BUDGET_S - (time.monotonic() - t0)
            if left <= 0:
                notes.append(f"评估到时间（{self.TOWER_BUDGET_S:.0f} 秒），用已跑完的 {done} 局")
                break
            seeds = [self.TOWER_SEED0 * (i + 1) + 17 for i in range(done, upto)]
            try:
                try:
                    res = run(
                        [c["loadout"] for c in alive], seeds, budget_s=left, detail=True, workers=self.TOWER_WORKERS
                    )
                except TypeError:  # 旧版 evaluate：只有分数
                    res = run([c["loadout"] for c in alive], seeds, budget_s=left)
            except Exception as e:  # noqa: BLE001  bench / GKMS 不可用：兜底
                notes.append(f"模拟评估不可用（{type(e).__name__}: {e}）")
                return any(c["rows"] for c in ok)
            for c, rows in zip(alive, res):
                c["rows"] = c["rows"] + list(rows)
            done = upto
            alive.sort(
                key=lambda c: tf.rank_key(
                    tf.summarize(c["rows"], c["lex"].get("border"), c["lex"].get("star_conditions"))
                ),
                reverse=True,
            )
            alive = alive[:keep]
        return any(c["rows"] for c in ok)

    def tower_retry(self, state: GameState) -> Decision:
        """偶像之路这一层失败后（每层免费重试上限 TOWER_RETRY_MAX 次，不花资源）：
        失败分数 ≥ 过关线 × TOWER_RETRY_SAME → 同编成再打（考试有随机性）；更低 → 换评估排下一位的编成；到上限 → 不再挑战，停下报告。
        page_fields：tower_id、layer、attempt（已经失败几次）、last_score、border?、last_pick（上一次的 SelectFormation args）。
        输出 Dialog {kind: tower_retry, confirm, pick?}。"""
        pf = state.page_fields
        n = int(pf.get("attempt") or 1)
        last, border, pick = pf.get("last_score"), pf.get("border"), dict(pf.get("last_pick") or {})
        if n >= self.TOWER_RETRY_MAX:
            why = f"偶像之路第 {pf.get('layer')} 层已失败 {n} 次（上限 {self.TOWER_RETRY_MAX}），不再挑战，停下报告"
            return Decision(
                Action(ActionKind.DIALOG, {"kind": "tower_retry", "confirm": False}),
                why,
                "rule",
                1.0,
                warnings=[why],
                decider="tower_retry",
            )
        if last is not None and border and last >= border * self.TOWER_RETRY_SAME:
            return Decision(Action(ActionKind.DIALOG, {"kind": "tower_retry", "confirm": True, "pick": pick}),
                            f"第 {n} 次失败：{last:,} / 过关线 {border:,}（≥ {self.TOWER_RETRY_SAME:.0%}），同编成再打", "rule", 0.8, decider="tower_retry")  # fmt: skip
        cache = getattr(self, "_tower_rank", None) or {}
        order = cache.get("order") if cache.get("key") == (pf.get("tower_id"), int(pf.get("layer") or -1)) else None
        nxt = None
        for c in order or []:
            p = {
                "kind": "tower",
                "mechanic": c["lex"]["mechanic"],
                "main": c["main"].get("index"),
                "subs": [s.get("index") for s in c["subs"]],
            }
            if (p["mechanic"], p["main"]) != (pick.get("mechanic"), pick.get("main")) and p not in getattr(
                self, "_tower_tried", []
            ):
                nxt = p
                break
        self._tower_tried = getattr(self, "_tower_tried", []) + [pick]
        if nxt is None:
            return Decision(Action(ActionKind.DIALOG, {"kind": "tower_retry", "confirm": True, "pick": pick}),
                            f"第 {n} 次失败（{last} / 过关线 {border}），没有别的编成可换，同编成再打", "fallback", 0.4, decider="tower_retry")  # fmt: skip
        return Decision(Action(ActionKind.DIALOG, {"kind": "tower_retry", "confirm": True, "pick": nxt}),
                        f"第 {n} 次失败：{last} / 过关线 {border}，差得多，换评估排下一位的编成（{nxt['mechanic']}·主回忆 {nxt['main']}）",
                        "rule", 0.7, decider="tower_retry")  # fmt: skip

    def formation(
        self, state: GameState, *, support_sets: list[dict] | None = None, memory_sets: list[dict] | None = None
    ) -> Decision:
        """开局编成：从预存的支援卡 / 记忆编成里选第几号（brain/formation.py，社区规则）。选中的支援卡编成决定主属性，
        同时写进 self.main_attr 供之后的选行动使用。候选编成来自 page_fields（support_sets / memory_sets）或参数。"""
        from hiro.brain.formation import memory_profile, support_profile
        from hiro.state.context import catalog

        conf = _formation_conf()
        pf = state.page_fields
        if support_sets is None:
            support_sets = (
                pf["support_sets"] if "support_sets" in pf else conf.get("support_sets") or []
            )  # 页面给了（哪怕为空）就以页面为准
        if memory_sets is None:
            memory_sets = pf["memory_sets"] if "memory_sets" in pf else conf.get("memory_sets") or []
        plan = catalog().one("IdolCard", self.profile.idol_id)["planType"] if self.profile else "ProducePlanType_Plan2"
        sup = [(s["index"], support_profile(s.get("cards") or [], plan)) for s in support_sets]
        mem = [(m["index"], memory_profile(m.get("memories") or [])) for m in memory_sets]
        cands, warn = [], []
        best_sup = max(sup, key=lambda x: x[1]["key"]) if sup else None
        best_mem = max(mem, key=lambda x: (x[1]["score"], -x[0])) if mem else None
        if best_sup is None:
            warn.append("没有候选支援卡编成：沿用 KAA 配置")
        if best_mem is None:
            warn.append("没有候选记忆编成：沿用 KAA 配置")
        for i, p in sup:
            cands.append(Candidate(Action(ActionKind.SELECT_FORMATION, {"support_set": i}),
                                   note=f"支援 #{i}：{p['counts']}，主属性 {p['main_attr']}，SP 发生率 {p['sp_rate']} 张、SP 结束奖励 {p['sp_end']} 张"))  # fmt: skip
        for i, p in mem:
            cands.append(
                Candidate(
                    Action(ActionKind.SELECT_FORMATION, {"memory_set": i}),
                    note=f"记忆 #{i}：{p['score']} 分（{'、'.join(p['hits']) or '无推荐卡'}）",
                )
            )
        args = {"support_set": best_sup[0] if best_sup else None, "memory_set": best_mem[0] if best_mem else None,
                "main_attr": best_sup[1]["main_attr"] if best_sup else None}  # fmt: skip
        if args["main_attr"]:
            self.main_attr = args["main_attr"]
        if best_mem:  # 记忆带进来的卡开局就在牌组里：不再当「必拿还缺」去刷新 / 禁用（第 6 局 デイドリーミング 由记忆带入，仍被列为缺）
            chosen = next(m for m in memory_sets if m["index"] == best_mem[0])
            self.picked_musts |= {m["card_id"] for m in chosen.get("memories") or [] if m.get("card_id")}
        reason = []
        if best_sup:
            p = best_sup[1]
            reason.append(
                f"支援 #{best_sup[0]}：主属性 {p['main_attr']} {p['counts'][p['main_attr']]} 张（目标 4），SP 发生率 {p['sp_rate']} 张 [seesaa][もりし]"
            )
            if p["counts"][p["main_attr"]] < 4:
                warn.append(f"候选支援编成里主属性最多只有 {p['counts'][p['main_attr']]} 张，社区建议 4 张")
        if best_mem:
            reason.append(f"记忆 #{best_mem[0]}：{'、'.join(best_mem[1]['hits']) or '没有社区推荐的卡'}")
            warn.extend(best_mem[1].get("conflicts") or [])
            if not any(h.startswith("届いて！") for h in best_mem[1]["hits"]):
                warn.append("候选记忆编成里都没有届いて！（社区：必拿，带パラメータ+ 定制）")
        return Decision(Action(ActionKind.SELECT_FORMATION, args), "；".join(reason) or "没有候选编成", "rule" if reason else "fallback",
                        0.8 if reason else 0.2, cands, warnings=warn, decider="formation")  # fmt: skip

    def qualify_memory(self, state: GameState, *, memories: list[dict] | None = None) -> Decision:
        """本战开始前选哪份选拔回忆（goal H4）。候选来自 page_fields["qualify_memories"]（格式见 docs/llm-context.md「QUALIFY_MEMORY」）。
        规则见 brain/formation.qualify_memory_profile：能用 → 必拿卡 → 一番星专属卡 → 星星 → 属性审查线 → S/A → 牌组张数 → 属性合计。
        开不开本战由用户决定（system-hardening §6：每局先请示），这里只给「用哪一份」。"""
        from hiro.brain.formation import qualify_memory_profile

        memories = state.page_fields.get("qualify_memories") if memories is None else memories
        if not memories:
            return Decision(Action.ui(ActionKind.WAIT, why="选拔回忆列表未识别"), "选拔回忆列表未识别，不选", "fallback", 0.1,
                            warnings=["识别未提供 qualify_memories"], decider="qualify_memory")  # fmt: skip
        idol = self.profile.idol_id if self.profile else None
        profs = [qualify_memory_profile(m, idol) for m in memories]
        cands = []
        for p in profs:
            desc = (f"回忆 #{p['index']}：必拿 {'、'.join(p['musts']) or '无'}，星星 {p['star']}（ランク {p['star_rank']}），"
                    f"属性过线 {p['params_ok']} 项，S/A {p['s_a']} 张，牌组 {p['deck_size']} 张" + (f"；{'；'.join(p['notes'])}" if p["notes"] else "")
                    + (f"；不能用：{'；'.join(p['problems'])}" if p["problems"] else ""))  # fmt: skip
            cands.append(Candidate(Action(ActionKind.SELECT_QUALIFY_MEMORY, {"index": p["index"]}), note=desc))
        usable = [p for p in profs if p["usable"]]
        if not usable:
            return Decision(Action.ui(ActionKind.WAIT, why="没有能用的选拔回忆"), "候选回忆都不能用（偶像不符 / 活动模式）", "rule", 0.5, cands,
                            warnings=[f"#{p['index']}：{'；'.join(p['problems'])}" for p in profs], decider="qualify_memory")  # fmt: skip
        best = max(usable, key=lambda p: (p["key"], -p["index"]))
        warn = list(best["notes"])
        if best["core_missing"] or best["conflicts"]:
            warn.insert(0, f"回忆 #{best['index']} 本战核心不齐（缺：{'、'.join(best['core_missing']) or '无'}；冲突牌 {best['conflicts']} 张），"
                           "本战底线（R1 约 40 万 / R2 约 60 万）大概率达不到，建议重做选拔；开不开本战由用户决定")  # fmt: skip
        if best["star_rank"] is not None and best["star"] < 600:
            warn.append("社区：回忆弱时本战很难赢，可以回选拔重做回忆 [wiki][game8]")
        reason = (f"回忆 #{best['index']}：必拿 {'、'.join(best['musts']) or '无'}"
                  + ("、一番星专属卡" if best["ichibanboshi"] else "")
                  + f"，星星 {best['star']}（スター性影响本战倍率最大 3 倍 [wiki]），属性过本战审查线 {best['params_ok']} 项，S/A {best['s_a']} 张，牌组 {best['deck_size']} 张")  # fmt: skip
        return Decision(Action(ActionKind.SELECT_QUALIFY_MEMORY, {"index": best["index"]}), reason, "rule", 0.7, cands, warnings=warn,
                        decider="qualify_memory")  # fmt: skip

    def drink_overflow(self, state: GameState) -> Decision:
        d = self._drink_overflow(state)
        args = d.action.args
        self.overflow_skip = (
            d.action.kind is ActionKind.DISCARD_DRINK and not args.get("receive") and not args.get("discard")
        )
        return d

    def drink_overflow_confirm(self, state: GameState) -> Decision:
        """PDRINK_MAX 之后的「報酬スキップ」确认弹窗：只有上一次 PDRINK_MAX 决定了「不收新饮料」才确认，否则取消（回去换饮料）。"""
        skip = getattr(self, "overflow_skip", None)
        self.overflow_skip = None  # 一次 PDRINK_MAX 只对应一次确认
        if skip:
            return Decision(Action(ActionKind.DIALOG, {"kind": "reward_skip", "confirm": True}), "上一步决定不收新饮料：确认跳过奖励", "rule", 0.9,
                            decider="drink_overflow")  # fmt: skip
        why = "上一步决定丢旧饮料收新的" if skip is False else "没有 PDRINK_MAX 的决策记录"
        return Decision(Action(ActionKind.DIALOG, {"kind": "reward_skip", "confirm": False}), f"{why}：不确认跳过，取消返回", "rule", 0.8,
                        warnings=[] if skip is False else ["PDRINK_MAX_CONFIRM 前没有 PDRINK_MAX 决策"], decider="drink_overflow")  # fmt: skip

    def _deck_counts(self, state: GameState) -> tuple[dict | None, int | None]:
        """确认过的牌组张数（状态机记账的 ProduceState.deck）：({card_id: 张数}, 总张数)；没记账时 (None, None)。
        换卡 / 删卡对话框只显示一屏（第十九局 12 / 22 张），不能拿对话框数张数。"""
        ps = self.produce_state(state)
        if ps is None or not ps.deck or "deck" in self.unknown_fields(state, ps):
            return None, None
        counts: dict = {}
        for c in ps.deck:
            counts[c.card_id] = counts.get(c.card_id, 0) + 1
        return counts, len(ps.deck)

    def remove_card(self, state: GameState) -> Decision:
        """删卡对话框（SKILL_CARD_REMOVAL，相談「削除」/ 删卡事件，page_fields.cards = 识别卡格 [{index, card_id, upgrade}]）：
        和换卡同一个顺序（cull_pool，用户 2026-11-03）：眠気等トラブル > 非一次性的非核心牌（思い出し笑い）> 会塞眠気的非一次性牌 >
        一次性的牌（按价值）> 核心牌的多余副本；保护卡按确认过的张数保留（没记账时所有副本都留），定制过的不删，没认出的格不选。
        本战牌组 ≤ 22 张时只删 0–1 档（删了 R2 会补基础卡）。卡格没读到或没有可删的卡时取消（不盲选：KAA 默认 commit(0)
        可能删掉排在第 0 张的 デイドリーミング）。"""
        from hiro.state.knowledge import FINALS_DECK_TARGET, FINALS_DELETE
        from hiro.state.produce import FINALS

        cards = state.page_fields.get("cards") or []
        self._note_seen_deck(cards)
        cancel = Action(ActionKind.REMOVE_CARD, {"cancel": True})
        if not cards:
            return Decision(
                cancel,
                "删卡页的卡格没读到：取消，不盲选",
                "rule",
                0.6,
                warnings=["识别未提供 cards"],
                decider="remove_card",
            )
        warn = (
            [f"{sum(1 for c in cards if not c.get('card_id'))} 格没认出，不选"]
            if any(not c.get("card_id") for c in cards)
            else []
        )
        finals = (state.produce or {}).get("mode") == FINALS
        counts, size = self._deck_counts(state)
        pool = self._cull(state, cards, "delete", counts=counts, finals=finals, deck_size=size)
        lean = self.consult_delete_lean
        if lean and pool and "flow_tier" in pool[0]:
            self.consult_delete_lean = False  # 流派删卡池本来就只有明确该删的几档
            lean = False
        elif lean:
            # 选拔相談（用户 2026-11-03 删牌顺序）：眠気 > 非一次性不带再動 > 初始卡 / 基础卡 > 不好用的其余
            self.consult_delete_lean = False
            pool = [c for c in pool if qualify_consult_deletable(c["card_id"], c.get("upgrade") or 0)]
        if not pool:
            if lean:
                why = "选拔相談只删 眠気 / 非一次性不带再動的牌 / 初始卡 / 不好用的牌：这里没有"
            elif finals and (size is None or size <= FINALS_DECK_TARGET):
                why = f"本战牌组 {size if size is not None else '张数未知'}（≤ {FINALS_DECK_TARGET} 张时删了 R2 会补基础卡），只删 眠気 / 非一次性的非核心牌：这里没有"
            else:
                why = "卡格里只剩核心 / 保护卡 / 定制卡（或都没认出）"
            return Decision(cancel, f"{why}：取消", "rule", 0.8 if lean else 0.7, warnings=warn, decider="remove_card")
        best = pool[0]
        if "flow_tier" in best:
            return Decision(Action(ActionKind.REMOVE_CARD, {"card_id": best["card_id"], "index": best.get("index"), "upgrade": best.get("upgrade")}),
                            f"删{FLOW_CULL[best['flow_tier']]} {_card_label(best)}（按流派 + 偶像卡攻略）", "rule", 0.85, warnings=warn,
                            decider="remove_card")  # fmt: skip
        kind = CULL_TIERS.get(cull_tier(best["card_id"]), "价值最低的卡")
        if finals and cull_tier(best["card_id"]) >= 2 and best["card_id"] in FINALS_DELETE:
            kind = "本战模拟删除收益最高的牌"

        def act(c: dict) -> Action:
            return Action(
                ActionKind.REMOVE_CARD, {"card_id": c["card_id"], "index": c.get("index"), "upgrade": c.get("upgrade")}
            )

        rule = Decision(act(best), f"删{kind} {_card_label(best)}（换 / 删同序，用户 2026-11-03；保护卡不删）", "rule", 0.9,
                        warnings=warn, decider="remove_card")  # fmt: skip
        # 拿不准：最该删的已经不是明确的垃圾牌（0–3 档），只能按启发式价值排。这时「不删」也是个选项（相談删卡要花 P）
        if cull_tier(best["card_id"]) < 4:
            return rule
        options = [(act(c), {"名字": _card_label(c), "卡 id": c["card_id"], "档": CULL_TIERS.get(cull_tier(c["card_id"]), ""),
                             "价值估计": round(card_value(c["card_id"], c.get("upgrade") or 0), 1)}) for c in pool[:3]]  # fmt: skip
        options.append((cancel, {"名字": "不删（取消）"}))
        why = f"没有明确该删的牌（最该删的是「{CULL_TIERS.get(cull_tier(best['card_id']), '')}」档），只能按价值估"
        if counts is None:
            why += "；牌组没记账，保护卡的所有副本都不在候选里"
        return self._ask_choice(
            "remove_card",
            rule,
            options,
            "删卡：从牌组里删一张，或者不删（取消）",
            why,
            state,
            {"本战牌组目标张数": FINALS_DECK_TARGET} if finals else None,
        )

    def enhance_card(self, state: GameState) -> Decision:
        """强化事件页（SKILL_CARD_ENHANCE）：未强化、有 + 版的卡里按 UPGRADE_ORDER（届いて！ > せのび > デイドリーミング > 紙吹雪）>
        社区评级 > card_value 选；不强化冲突牌（除非没有别的）。卡格没读到或没有可强化的卡时取消。"""
        from hiro.state.context import catalog

        cards = state.page_fields.get("cards") or []
        self._note_seen_deck(cards or [])
        cancel = Action(ActionKind.ENHANCE_CARD, {"cancel": True})
        # D42（总控 2026-11-04，第三十三局本战 18:45）：P 道具触发的免费强化页是必选的，没有キャンセル（画面只有「強化」）。
        # page_fields.cancelable：识别 / 执行按画面有没有キャンセル 填（False = 必选；None = 不知道，按原来的可取消处理）
        forced = state.page_fields.get("cancelable") is False or state.page_fields.get("trigger") == "pitem"
        if not cards:
            if forced:
                return Decision(Action.ui(ActionKind.WAIT, why="必选强化页卡格没读到"), "必选强化页（没有キャンセル）卡格没读到：等重新识别，不取消、不盲选",
                                "rule", 0.3, warnings=["识别未提供 cards"], decider="enhance_card")  # fmt: skip
            return Decision(
                cancel,
                "强化页的卡格没读到：取消，不盲选",
                "rule",
                0.6,
                warnings=["识别未提供 cards"],
                decider="enhance_card",
            )

        from hiro.state.knowledge import SMALL_DECK_CORE

        # H.I.F 里不是广的偶像（HIRO_HIF_GUIDES=1）：广的强化顺序表对她没用，按偶像卡攻略的 card_upgrade 条目（排行榜里强化过的占比）+ 循环件
        guided = self._upgrade_guide(state) if self._hif_other(state) else None

        def worth(cid: str) -> bool:
            """值得花强化的卡：强化顺序表 / 必拿 / S·A / 小牌组核心 / 偶像固有卡。基础卡、可复用非核心、冲突牌不强化
            （第十二局第 12 天相談：核心卡都已强化或没认出，花 100P 强化了 距離感の基本；小牌组下它应该被删）。"""
            if guided is not None:
                return (cid in guided or "-ido-" in cid) and not (_is_basic(cid) or _is_trouble_card(cid))
            k = YARUKI_CARDS.get(cid) or {}
            return (cid in UPGRADE_ORDER or k.get("must") or k.get("tier") in ("S", "A") or cid in SMALL_DECK_CORE or "-ido-" in cid) and not (
                _is_basic(cid) or unwanted(cid))  # fmt: skip

        def upgradable(c: dict) -> bool:
            if not c.get("card_id") or c.get("upgrade") or _is_trouble_card(c["card_id"]) or not worth(c["card_id"]):
                return False
            try:
                catalog().card(c["card_id"], 1)
                return True
            except Exception:
                return False

        pool = [c for c in cards if upgradable(c)]
        if not pool and forced:
            return self._forced_enhance(cards)
        if not pool:
            return Decision(cancel, "没有值得强化的卡（核心 / S·A 都已强化或没认出；基础卡、非核心不强化）：取消，相談接着删卡", "rule", 0.7,
                            decider="enhance_card")  # fmt: skip

        def score(c: dict) -> tuple:
            cid = c["card_id"]
            if guided is not None:
                return (guided.get(cid, 1.0 if "-ido-" in cid else 0.0), card_value(cid), -c.get("index", 0))
            order = len(UPGRADE_ORDER) - UPGRADE_ORDER.index(cid) if cid in UPGRADE_ORDER else 0
            return (not is_conflict(cid), order, _card_priority(cid), card_value(cid), -c.get("index", 0))

        best = max(pool, key=score)
        args = {"card_id": best["card_id"], "index": best.get("index"), "upgrade": 0}
        why = (
            "偶像卡攻略的强化占比"
            if guided is not None
            else "UPGRADE_ORDER"
            if best["card_id"] in UPGRADE_ORDER
            else "社区评级 / 卡牌价值"
        )
        return Decision(
            Action(ActionKind.ENHANCE_CARD, args),
            f"强化 {_card_label(best)}（按 {why}）",
            "rule",
            0.9,
            decider="enhance_card",
        )

    @staticmethod
    def _forced_enhance(cards: list[dict]) -> Decision:
        """必选强化页（D42）、核心都已强化：非核心里价值最高的未强化卡 → 基础卡 → 冲突卡（强化它们没用，但页面必须选一张）。"""
        from hiro.state.context import catalog

        def has_plus(c: dict) -> bool:
            if not c.get("card_id") or c.get("upgrade") or _is_trouble_card(c["card_id"]):
                return False
            try:
                catalog().card(c["card_id"], 1)
                return True
            except Exception:
                return False

        rest = [c for c in cards if has_plus(c)]
        if not rest:
            return Decision(Action.ui(ActionKind.WAIT, why="必选强化页没有能强化的卡"), "必选强化页（没有キャンセル）认出的卡都不能强化：等重新识别",
                            "rule", 0.3, warnings=["必选强化页没有可强化的卡"], decider="enhance_card")  # fmt: skip

        def key(c: dict) -> tuple:
            cid = c["card_id"]
            return (not is_conflict(cid), not _is_basic(cid), card_value(cid), -c.get("index", 0))

        best = max(rest, key=key)
        kind = (
            "冲突卡"
            if is_conflict(best["card_id"])
            else ("基础卡" if _is_basic(best["card_id"]) else "非核心里价值最高的卡")
        )
        args = {"card_id": best["card_id"], "index": best.get("index"), "upgrade": 0}
        return Decision(Action(ActionKind.ENHANCE_CARD, args), f"必选强化页（P 道具触发，没有キャンセル）、核心都已强化：强化{kind} {_card_label(best)}",
                        "rule", 0.7, decider="enhance_card")  # fmt: skip

    def _drink_overflow(self, state: GameState) -> Decision:
        """饮料满了（PDRINK_MAX）：收不收、丢哪瓶。候选 = 不收 / 丢第 j 瓶收下，按做完之后的持有组合比较（同 select_drink）。
        page_fields：held=[{index, drink_id, name}]（缺时用 produce.drinks）、incoming={drink_id, name}。格式见 docs/llm-context.md。"""
        from hiro.brain import drinks as dk
        from hiro.state.context import drink_name

        if not any(k in (state.page_fields or {}) for k in ("held", "incoming", "incomings")):
            # page_fields 是别的页面的（溢出页还没在状态里稳定）：兜底「不换」会点「残す」放弃新饮料，等重读（DMM qualifyverify4 11-17）
            return Decision(Action.ui(ActionKind.WAIT, why="溢出页字段还没进状态"), "饮料溢出页的持有 / 新饮料还没读进状态：等重读，不兜底「不换」",
                            "rule", 0.3, decider="drink_overflow")  # fmt: skip
        ps = self.produce_state(state)
        held, capacity, _, warn = self._drink_holding(state, ps)
        incomings = [x for x in state.page_fields.get("incomings") or [] if x.get("drink_id")]
        if len(incomings) > 1 and held:
            return self._drink_overflow_multi(state, ps, held, incomings, warn)
        if held and not any(h.get("drink_id") for h in held):
            return Decision(Action.ui(ActionKind.WAIT, why="持有饮料全都没认出"), "持有饮料全都没认出：不丢（丢错会让执行找不到那一行、KAA 默认「残す」），等滚动重读",
                            "rule", 0.3, warnings=warn, decider="drink_overflow")  # fmt: skip
        incoming = state.page_fields.get("incoming") or {}
        keep = Action(ActionKind.DISCARD_DRINK, {"discard": [], "receive": False})
        if not held or not incoming.get("drink_id"):
            return Decision(keep, "持有饮料或新饮料未识别：不换（保持现状）", "fallback", 0.3, warnings=["识别未提供 held / incoming"],
                            decider="drink_overflow")  # fmt: skip
        hold = [h.get("drink_id") for h in held]
        name = incoming.get("name") or drink_name(incoming["drink_id"])
        opts = [dk.Option("keep", f"不收 {name}", hold, keep)]
        for j, h in enumerate(held):
            if h.get("visible") is False:
                continue  # 画面上点不到的那瓶不能丢
            opts.append(dk.Option(f"drop{h['index']}", f"丢 {h.get('name') or drink_name(h.get('drink_id'))}、收 {name}",
                                  hold[:j] + [incoming["drink_id"]] + hold[j + 1:],
                                  Action(ActionKind.DISCARD_DRINK, {"discard": [h["index"]], "receive": True})))  # fmt: skip
        stage = dk.stage_of(ps.mode if ps else None, interval=bool(state.page_fields.get("interval")))
        res = dk.choose(
            opts,
            stage,
            max(capacity, len(hold)),
            sim_context=self._drink_sim_context(ps, stage),
            where="PDRINK_MAX",
            take_on_tie=True,
        )
        return self._drink_decision(res, opts, stage, "drink_overflow", warn, state)

    def _drink_overflow_multi(self, state: GameState, ps, held: list, incomings: list, warn: list) -> Decision:
        """饮料溢出页「受け取ったドリンク」有多瓶（执行 23e6965：第十七局 #63 复制饮料 + ホエイ）：在「持有 + 新来的」里挑栏位容量内最好的一组
        （hiro.brain.drinks 同一套比较）。page_fields.incomings = [{index, drink_id, name}]（识别给全部新饮料）。
        输出 discard = 不留的持有下标，receive_indexes = 要收的新饮料下标（receive = 有要收的）。"""
        from itertools import combinations

        from hiro.brain import drinks as dk
        from hiro.state.context import drink_name

        visible = [h for h in held if h.get("visible") is not False]
        hidden = [h for h in held if h.get("visible") is False]  # 画面上点不到的持有：只能留着
        # 要留几瓶：持有默认全勾着，页面「あと N 個選択」还差 N 瓶 → 留 持有数 + N。栏里本来有空格时（Da SP 课结束随机送 2 瓶，
        # 持有 2 + 空 1：要留 3 瓶），「不收」不是可选项（「残す」灰着点不了，也没有「離れる」，Mac 账号 2 11-14 停机）。读不到按满了算
        left = state.page_fields.get("select_left")
        cap = len(held) + (int(left) if isinstance(left, int) and left > 0 else 0)
        pool = [("h", h) for h in visible] + [("n", x) for x in incomings]
        k = cap - len(hidden)
        opts = []
        for combo in combinations(pool, min(k, len(pool))):
            keep_h = {h["index"] for t, h in combo if t == "h"}
            take = [x["index"] for t, x in combo if t == "n"]
            drinks = [h.get("drink_id") for h in hidden] + [x.get("drink_id") for _, x in combo]
            discard = [h["index"] for h in visible if h["index"] not in keep_h]
            label = (
                "收 " + "、".join(x.get("name") or drink_name(x["drink_id"]) for t, x in combo if t == "n")
                if take
                else "不收"
            ) + (
                "，丢 "
                + "、".join(h.get("name") or drink_name(h.get("drink_id")) for h in visible if h["index"] in discard)
                if discard
                else ""
            )
            key = "keep" if not take else "take" + "-".join(map(str, take))
            opts.append(
                dk.Option(
                    key,
                    label,
                    drinks,
                    Action(
                        ActionKind.DISCARD_DRINK, {"discard": discard, "receive": bool(take), "receive_indexes": take}
                    ),
                )
            )
        stage = dk.stage_of(ps.mode if ps else None, interval=bool(state.page_fields.get("interval")))
        res = dk.choose(
            opts, stage, cap, sim_context=self._drink_sim_context(ps, stage), where="PDRINK_MAX", take_on_tie=True
        )
        return self._drink_decision(
            res, opts, stage, "drink_overflow", warn + [f"新来 {len(incomings)} 瓶，按持有 + 新来的合起来挑"], state
        )

    # ---------------------------------------------------------------- 相談 / 授業 / 外出 / 轮间（识别字段到位前为保守规则）
    def consult(self, state: GameState) -> Decision:
        """相談（普通商店），一次给一个动作；买完由主循环重新观测再调用。page_fields 格式见 docs/llm-context.md「商店字段」。

        选拔（用户 2026-11-02 第十六局复盘，_selection_consult）：P 点统一预算（给第 19 天特別指導留出计划的定制），选项全列、
        按社区默认档位花：补全核心卡 + 饮料 > 删 眠気 > 强化核心卡；饮料用 hiro.brain.drinks（社区默认 + 模拟偏离）。
        本战（もなみ：最后的相談基本不花，留 P 给轮间）：删眠気、牌组 > 22 张时删冲突牌；饮料「事故」时才买。"""
        from hiro.state.produce import FINALS

        shelf = state.page_fields.get("shelf")
        services = state.page_fields.get("services") or {}
        ps = self.produce_state(state)
        if not shelf and not services:
            return Decision(Action(ActionKind.SHOP_END), "相談货架未识别，不买直接离开", "rule", 0.3,
                            warnings=["识别未提供 shelf：删眠気 / 强化 / 换饮料都没做"], decider="consult")  # fmt: skip
        if ps is None:
            return Decision(
                Action(ActionKind.SHOP_END),
                "P 点等局面未识别，不买",
                "fallback",
                0.3,
                warnings=["ProduceState 不全"],
                decider="consult",
            )
        if _is_hajime_nia(ps.mode):
            return self._hajime_nia_consult(state, ps, services)
        if ps.mode != FINALS:
            return self._selection_consult(state, ps, shelf or [], services)
        unknown = self.unknown_fields(state, ps)
        p = ps.produce_point
        cands = _shop_candidates(shelf or [], services, p, ActionKind.SHOP_BUY)
        deck_known = "deck" not in unknown and bool(ps.deck)

        def done(action: Action, reason: str) -> Decision:
            return Decision(action, reason, "rule", 0.8, cands, decider="consult",
                            warnings=[] if deck_known else ["牌组未识别：没有做删卡 / 强化"])  # fmt: skip

        delete = services.get("delete") or {}
        can_delete = deck_known and delete.get("available", True) and _affordable(delete, p)
        # 换 / 删同序（cull_pool，用户 2026-11-03）：眠気 > 非一次性的非核心牌（思い出し笑い「能换掉的时候就应该换掉」）；
        # 牌组 > 22 张时再删 FINALS_DELETE / 冲突牌（A5 模拟删除收益），删到 22 为止。顺序：删眠気 → 买缺的必拿卡 → 删其余
        from hiro.state.knowledge import FINALS_DECK_TARGET

        pool = []
        if can_delete:
            cards = [
                {"index": i, "card_id": c.card_id, "upgrade": c.upgrade, "customized": c.customized}
                for i, c in enumerate(ps.deck)
            ]
            counts, size = self._deck_counts(state)
            pool = self._cull(state, cards, "delete", counts=counts, finals=True, deck_size=size)

        def delete_first() -> Decision:
            c = pool[0]
            tier = c.get("flow_tier", cull_tier(c["card_id"]))
            label = FLOW_CULL[tier] if "flow_tier" in c else CULL_TIERS.get(tier, "")
            return done(Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": c["index"]}),
                        f"删{label} {_card_label(c)}（{delete['price']}P；换 / 删同序，用户 2026-11-03"
                        + (f"；本战牌组 {len(ps.deck)} 张 > {FINALS_DECK_TARGET}，按模拟删除收益（A5）" if tier >= 3 else "") + "）")  # fmt: skip

        if pool and pool[0].get("flow_tier", cull_tier(pool[0]["card_id"])) == 0:
            return delete_first()
        owned = {c.card_id for c in ps.deck}
        for it in sorted(_items(shelf, "card"), key=lambda it: -_card_priority(it.get("card_id"))):
            know = YARUKI_CARDS.get(it.get("card_id"))
            if know and know.get("must") and it["card_id"] not in owned and _affordable(it, p):
                return done(
                    Action(ActionKind.SHOP_BUY, {"index": it["index"]}),
                    f"买缺的核心卡 {know['name']}（{it['price']}P）",
                )
        if pool:
            return delete_first()
        d = self._finals_consult_drinks(state, ps, shelf or [], p)
        if d is not None:
            return d
        why = getattr(self, "_finals_drink_why", "")
        plan = getattr(self, "_finals_drink_plan", None) or {}
        if plan.get("source") in (
            "llm",
            "fallback",
        ):  # 不买是 LLM 定的（或 LLM 失败按规则兜底）：结束 / 刷新的决定带上来源和调用记录（只带一次）
            base_done = done

            def done(action: Action, reason: str) -> Decision:  # noqa: F811
                d = base_done(action, reason)
                d.source, d.line, d.llm, d.warnings = (
                    plan["source"],
                    plan.get("line", ""),
                    plan.pop("llm", []),
                    d.warnings + plan["warnings"],
                )
                plan["source"] = "rule"
                return d

        v = self._consult_visit(state, ps, shelf or [], services)
        if self._consult_free_slots(state, ps) > 0 and p >= 50 and self._consult_can_reroll(state, ps, shelf or [], v):
            # 免费刷新：按价值没有值得买的饮料 → 刷新，新货架重新估值（_finals_consult_drinks 按货架签名重算）
            return done(Action(ActionKind.REROLL, {"scope": "consult"}),
                        f"【刷新】本战相談免费刷新（あと {state.page_fields.get('rerolls_left')} 回）：当前货架按价值没有值得买的饮料（{why}），刷新再估一次")  # fmt: skip
        return done(
            Action(ActionKind.SHOP_END),
            f"本战相談给轮间留 P 点（{p}P）：轮间卡便宜，补卡、R2 饮料、回体力都要用钱 [もなみ][もりし]{('；' + why) if why else ''}",
        )

    def _hajime_nia_consult(self, state: GameState, ps: ProduceState, services: dict) -> Decision:
        """初 / N.I.A 相談：只删卡（H.I.F 选拔的 P 点预算、第 19 天特別指導预留在这两个模式里不适用）。
        删的顺序同换 / 删（cull_pool）：トラブル > 不带「レッスン中1回」、不带再動的非核心卡 > 冲突 / 好印象系 > 「○○の基本」等初始卡，
        核心卡和循环卡不删 [g8-NIAM nia.delete_order][g8-PRO hajime.consult_price]。不买饮料、不买卡（以前局面构造不出时一律不买，这里先只加删卡）。"""
        from hiro.brain import hajime_nia_rules as hr
        from hiro.state.modes import NIA, family

        unknown = self.unknown_fields(state, ps)
        p = ps.produce_point if "produce_point" not in unknown else (state.page_fields.get("produce_point") or 0)
        delete = services.get("delete") or {}
        rid = "nia.delete_order" if family(ps.mode) == NIA else "hajime.consult_price"
        if "deck" in unknown or not ps.deck:
            return Decision(
                Action(ActionKind.SHOP_END),
                "相談：牌组没识别，不删卡",
                "rule",
                0.5,
                warnings=["牌组未识别：没有做删卡"],
                decider="consult",
            )
        if "delete" in self.consult_used or delete.get("used") or delete.get("exchanged"):
            # 一次相談每种服务只能用一次（协调 11-18 初 PRO：删完一张按钮变「交換済」，又按 125P 重复删了 3 次停机）。
            # 画面上的 125P 是下一次相談的删卡价（平台写进 produce.consult_delete_price，选行动时用）
            return Decision(Action(ActionKind.SHOP_END), f"相談：这次已经删过一张（每种服务一次相談只能用一次；{delete.get('price')}P 是下一次的价），结束",
                            "rule", 0.8, decider="consult")  # fmt: skip
        if not delete.get("available", True) or not _affordable(delete, p):
            return Decision(
                Action(ActionKind.SHOP_END),
                f"相談：删卡用不了或 P 点不够（{p}P），不买",
                "rule",
                0.6,
                decider="consult",
            )
        cards = [
            {"index": i, "card_id": c.card_id, "upgrade": c.upgrade, "customized": c.customized}
            for i, c in enumerate(ps.deck)
        ]
        counts, _ = self._deck_counts(state)
        from hiro.brain import flows

        if flows.flow(ps.idol_card_id) not in (None, "yaruki"):
            pool = _flow_delete_pool(
                cards, ps.idol_card_id, ps.mode
            )  # 广以外的偶像：按自己的流派判冲突（协调 11-19 多偶像）+ 攻略的删卡条目
        else:
            pool = [
                c
                for c in cull_pool(cards, "delete", counts=counts)
                if qualify_consult_deletable(c["card_id"], c.get("upgrade") or 0)
            ]
        if not pool:
            return Decision(Action(ActionKind.SHOP_END), "相談：牌组里没有该删的卡（トラブル / 可复用的非核心卡 / 冲突卡 / 基本卡都没有了），不买", "rule", 0.7,
                            decider="consult")  # fmt: skip
        c = pool[0]
        self.consult_used.add("delete")
        kind = FLOW_CULL[c["flow_tier"]] if "flow_tier" in c else CULL_TIERS.get(cull_tier(c["card_id"]), "")
        return Decision(Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": c["index"]}),
                        f"相談删{kind} {_card_label(c)}（{delete.get('price')}P；{hr.cite(rid)}）", "rule", 0.8,
                        decider="consult")  # fmt: skip

    # ---- 选拔相談：P 点预算 + 选项表（用户 2026-11-02 第十六局复盘）
    def _consult_visit(self, state: GameState, ps: ProduceState, shelf: list, services: dict) -> dict:
        """同一次相談的记账。「本次已花」= 入场 P − 现在的 P（头部 P 读数，识屏 140621f 起相談页也读）；上一步做没做成也看 P：
        P 减少了才算成交、按实际减少的数记（DMM 第 12 天：饮料 75P 误读成 15P 记了 15；删卡实扣 100P 没记上；
        在过期货架上买失败的ホットコーヒー 反倒记了 50P）。P 读不到时退回老办法：上一步买的格子变成已购 / 消失、
        或强化 / 删除的价格上涨（同一计数 100 → 125 → 150 [wiki]）就算成交，按标价从入场 P 里扣。"""
        key = (ps.mode, ps.step)
        v = getattr(self, "_consult_state", None)
        if v is None or v["key"] != key:
            v = self._consult_state = {"key": key, "entry_p": ps.produce_point, "spent": 0, "last": None, "log": [], "no_target": set(),
                                       "svc_price": {}, "p_seen": ps.produce_point}  # fmt: skip
        if v["entry_p"] is None:
            v["entry_p"] = v["p_seen"] = ps.produce_point
        p_now, p_prev = ps.produce_point, v.get("p_seen")
        last = v.pop("last", None)
        v["last"] = None
        if last is not None:
            if p_now is not None and p_prev is not None:
                ok, cost = p_now < p_prev, p_prev - p_now
            else:
                ok, cost = False, last["cost"]
                if last.get("index") is not None:
                    it = next((x for x in shelf if x.get("index") == last["index"]), None)
                    ok = (it is None and bool(shelf)) or (
                        it is not None and (bool(it.get("purchased")) or not (it.get("card_id") or it.get("drink_id")))
                    )
                elif last.get("service"):
                    price = (services.get(last["service"]) or {}).get("price")
                    ok = price is not None and last.get("price_before") is not None and price > last["price_before"]
            if not ok and last.get("service"):
                v["no_target"].add(last["service"])  # 对话框里没选（没有该做的卡）或没做成：本次不再开
            if ok:
                v["log"].append(f"{last['label']}（{cost}P）")
                if p_now is None or v["entry_p"] is None:
                    v["spent"] += cost
        if p_now is not None and v["entry_p"] is not None:
            v["spent"] = max(0, v["entry_p"] - p_now)  # 以 P 差为准：没做成的不算，标价读错也不影响
        if p_now is not None:
            v["p_seen"] = p_now
        if p_now is None:
            v["p"] = None if v["entry_p"] is None else v["entry_p"] - v["spent"]
        else:
            v["p"] = p_now if v["entry_p"] is None else min(p_now, v["entry_p"] - v["spent"])
        return v

    def _consult_free_slots(self, state: GameState, ps: ProduceState) -> int:
        held, capacity, full, _ = self._drink_holding(state, ps)
        if full or state.page_fields.get("drinks_full"):
            return 0
        return capacity - len(held or [])

    def _consult_can_reroll(self, state: GameState, ps: ProduceState, shelf: list, v: dict) -> bool:
        """相談左下「リフレッシュ」（免费，角标 あと N回，page_fields.rerolls_left；执行 3c81c5c）。次数是每次相談重置还是整局共用，
        trace 里还没用过、wiki 没写——我们每局只去两次相談（选拔第 12 天、本战第 6 天），本战相談按もなみ基本不花，
        所以就算整局共用，选拔先用的机会成本也小，按「有次数就用」处理（第十八局核对本战相談的 あと N回）。
        一次相談最多刷 rerolls_left 次；刷完货架没变（执行失败 / 取消）就不再刷。"""
        left = state.page_fields.get("rerolls_left")
        if not isinstance(left, int) or left <= 0:
            return False
        sig = sorted(
            (it.get("index"), it.get("card_id") or it.get("drink_id"))
            for it in shelf
            if it.get("card_id") or it.get("drink_id")
        )
        tried = v.setdefault("rerolls", [])
        if tried and tried[-1] == sig:
            return False  # 上一次刷新后货架没变：没刷成
        if len(tried) >= 3:
            return False  # 防循环（实拍是 あと1回）
        tried.append(sig)
        return True

    def _guidance_reserve(self, ps: ProduceState, deck_known: bool) -> tuple[int, str]:
        """给第 19 天特別指導留多少 P：计划定制（knowledge.CUSTOMIZE_PRIORITY 里前 2 张能做的卡，一次特別指導最多 2 张）的
        master 逐级价格之和，减去第 13–18 天预计进账（knowledge.selection_p_income）。牌组未识别时按优先表前两张（えいえいおー 3 级 + 届いて）。"""
        from hiro.state.knowledge import (
            CUSTOMIZE_LEVELS,
            CUSTOMIZE_PRIORITY,
            SELECTION_GUIDANCE_DAY,
            selection_p_income,
        )

        if ps.step >= SELECTION_GUIDANCE_DAY:
            return 0, "第 19 天之后没有特別指導"
        owned = {c.card_id: c for c in ps.deck} if deck_known else None
        plan, cost = [], 0
        musts = [x for x in CUSTOMIZE_PRIORITY if CUSTOMIZE_LEVELS.get(x[0], (1, False))[1]]
        rest = [x for x in CUSTOMIZE_PRIORITY if not CUSTOMIZE_LEVELS.get(x[0], (1, False))[1]]
        for cid, name, keys, _ in musts + rest:
            if len(plan) >= 2:
                break
            if owned is not None and (cid not in owned or getattr(owned[cid], "customized", False)):
                continue
            c = _customize_cost(cid, keys, CUSTOMIZE_LEVELS.get(cid, (1, False))[0])
            if c:
                plan.append(f"{name} {c}P")
                cost += c
        income = selection_p_income(ps.step)
        reserve = max(0, cost - income)
        return (
            reserve,
            f"第 19 天特別指導计划 {'、'.join(plan) or '无'}（{cost}P），第 {ps.step + 1}–18 天预计进账 {income:+}P → 预留 {reserve}P",
        )

    def _selection_consult(self, state: GameState, ps: ProduceState, shelf: list, services: dict) -> Decision:
        """选拔相談，一次一个动作。顺序（社区默认档位，用户 2026-11-02；もなみ：选拔② 前相談主要强化 + 删眠気，饮料事故就补）：
        1. 补全核心卡（必拿、牌组里没有）：可以动用给特別指導的预留；
        2. 补全饮料（hiro.brain.drinks：社区默认 + 模拟偏离；「事故」时可以动用预留）；
        3. 删 眠気（只删 眠気：选拔①② 后游戏会各随机删 2 张，R2 不满 22 张会被応援棒塞基础卡 [wiki][もなみ][いおん ep07]）；
        4. 强化核心卡（可重复，价格逐次上涨）；
        5. 最后一次相談（选拔③ 前，模板只在第 12 天来）预算还有剩：空栏补期望不为负的饮料（3 档及以上）。
        普通卡不买（もなみ），除非是缺的核心卡。预算 = 当前 P − 给第 19 天特別指導的预留。牌组未识别时强化 / 删卡打开对话框，在对话框里选。"""
        from hiro.state.knowledge import SELECTION_CONSULT_DAYS

        unknown = self.unknown_fields(state, ps)
        deck_known = "deck" not in unknown and bool(ps.deck)
        v = self._consult_visit(state, ps, shelf, services)
        p = v["p"]
        reserve, reserve_why = self._guidance_reserve(ps, deck_known)
        budget = p - reserve
        last_visit = not any(
            d > ps.step for d in SELECTION_CONSULT_DAYS
        )  # 修：原来判 step == 19，但相談在第 12 天，永远不触发
        cands = _shop_candidates(shelf, services, p, ActionKind.SHOP_BUY)
        head = (f"P {p}（入场 {v['entry_p']}，本次已花 {v['spent']}：{'、'.join(v['log']) or '无'}）；{reserve_why}；可花 {budget}P"
                f"{'；选拔③ 前最后一次相談' if last_visit else ''}")  # fmt: skip
        warn = [] if deck_known else ["牌组未识别：强化 / 删卡在对话框里选"]

        def go(action: Action, label: str, cost: int, tier: str, **track) -> Decision:
            v["last"] = {"label": label, "cost": cost, **track}
            return Decision(
                action, f"【{tier}】{label}（{cost}P）。{head}", "rule", 0.85, cands, warnings=warn, decider="consult"
            )

        owned = {c.card_id for c in ps.deck} if deck_known else set(self.picked_musts)
        # 1. 缺的核心卡
        for it in sorted(_items(shelf, "card"), key=lambda it: -_card_priority(it.get("card_id"))):
            know = YARUKI_CARDS.get(it.get("card_id"))
            if know and know.get("must") and it["card_id"] not in owned and _affordable(it, p):
                return go(
                    Action(ActionKind.SHOP_BUY, {"index": it["index"]}),
                    f"买缺的核心卡 {know['name']}",
                    it["price"],
                    "第一档：补全核心卡",
                    index=it["index"],
                )
        # 2. 饮料
        d = self._consult_drinks(state, ps, shelf, p, budget, stage="selection", only_accident=False, go=go)
        if d is not None:
            return d
        # 2'. 刷新（免费、有次数）：当前货架上值得买的已经买完，还有饮料空位 / 还缺必拿卡、预算够一瓶饮料 → 刷新再看
        missing = (
            [c for c, k in YARUKI_CARDS.items() if k.get("must") and c not in owned]
            if deck_known and not self._hif_other(state, ps)
            else []
        )
        if (
            budget >= 50
            and (self._consult_free_slots(state, ps) > 0 or missing)
            and self._consult_can_reroll(state, ps, shelf, v)
        ):
            v["last"] = None
            why = (
                f"免费刷新（あと {state.page_fields.get('rerolls_left')} 回）：当前货架上没有值得买的饮料 / 核心卡了"
                + (f"，还缺 {len(missing)} 张必拿卡" if missing else "")
            )
            return Decision(
                Action(ActionKind.REROLL, {"scope": "consult"}),
                f"【刷新】{why}。{head}",
                "rule",
                0.8,
                cands,
                warnings=warn,
                decider="consult",
            )
        # 3. 删卡（用户 2026-11-03：「优先删眠気，然后是非一次性的 思い出し笑い 这类不提供再动的牌，然后再是不好用的卡（初始卡）」；
        #    原来 2026-11-02 只删眠気）。顺序同 cull_pool，能删的范围见 qualify_consult_deletable；P 预算顺序照旧（核心卡 + 饮料 → 删卡 → 强化）
        delete = services.get("delete") or {}
        if (
            delete.get("available", True)
            and delete.get("price") is not None
            and delete["price"] <= budget
            and "delete" not in v["no_target"]
        ):
            if deck_known:
                cards = [
                    {"index": i, "card_id": c.card_id, "upgrade": c.upgrade, "customized": c.customized}
                    for i, c in enumerate(ps.deck)
                ]
                counts, _ = self._deck_counts(state)
                pool = self._cull(state, cards, "delete", counts=counts)
                if pool and "flow_tier" not in pool[0]:
                    pool = [c for c in pool if qualify_consult_deletable(c["card_id"], c["upgrade"])]
                # 原来「删初始卡不挤掉核心卡强化」的保护已去掉（用户 2026-11-03：饮料和删牌优先于强化；R2 模拟 せのび 不强化 +0.1 万 ± 1.4 万，不反对）
                if pool:
                    c = pool[0]
                    label = f"删{FLOW_CULL[c['flow_tier']] if 'flow_tier' in c else CULL_TIERS[cull_tier(c['card_id'])]} {_card_label(c)}"
                    return go(Action(ActionKind.SHOP_BUY, {"service": "delete", "card_index": c["index"]}), label, delete["price"],
                              "删卡（眠気 > 非一次性不带再動 > 初始卡 > 不好用的牌）", service="delete", price_before=delete["price"])  # fmt: skip
            else:
                self.consult_delete_lean = (
                    True  # 对话框里按同一顺序删 qualify_consult_deletable 的牌，没有就取消（remove_card）
                )
                return go(Action(ActionKind.SHOP_BUY, {"service": "delete"}), "打开「削除」对话框删卡（眠気 > 非一次性不带再動 > 初始卡 > 不好用的牌；牌组未识别，没有就取消）",
                          delete["price"], "删卡（眠気 > 非一次性不带再動 > 初始卡 > 不好用的牌）", service="delete", price_before=delete["price"])  # fmt: skip
        # 4. 强化核心卡
        upgrade = services.get("upgrade") or {}
        if (
            upgrade.get("available", True)
            and upgrade.get("price") is not None
            and upgrade["price"] <= budget
            and "upgrade" not in v["no_target"]
        ):
            if deck_known:
                target = _upgrade_target(ps)
                if target is not None:
                    return go(Action(ActionKind.SHOP_UPGRADE_CARD, {"card_index": target[0]}), f"强化核心卡 {target[1]}", upgrade["price"], "第二档：强化核心卡",
                              service="upgrade", price_before=upgrade["price"])  # fmt: skip
            else:
                return go(Action(ActionKind.SHOP_UPGRADE_CARD, {}), "打开「強化」对话框强化核心卡（牌组未识别，在对话框里选）", upgrade["price"], "第二档：强化核心卡",
                          service="upgrade", price_before=upgrade["price"])  # fmt: skip
        # 5. 最后一次相談：预算还有剩，空栏补期望不为负的饮料
        if last_visit:
            d = self._consult_drinks(
                state, ps, shelf, budget, budget, stage="selection", only_accident=False, go=go, fill=True
            )
            if d is not None:
                return d
        v["last"] = None
        left = "；剩下的留给第 19 天特別指導" if reserve else ""
        return Decision(
            Action(ActionKind.SHOP_END),
            f"预算内没有要做的了。{head}{left}",
            "rule",
            0.8,
            cands,
            warnings=warn,
            decider="consult",
        )

    def _finals_consult_drinks(self, state: GameState, ps: ProduceState, shelf: list, p: int) -> Decision | None:
        """本战相談买不买饮料（用户 2026-11-02：看缺不缺好用的饮料，按价值定，不用瓶数阈值）。
        候选 = 不买 / 空位内货架饮料的每一种买法；比较「做完之后的持有组合 + 剩余 P」：
          价值 = 1.2 ×（R1 期望分：合成 R1 开局按新持有续打，100 局配对）− 留 P 的损失（knowledge.interval_p_value：轮间要补卡到 22 张、
          买 R2 饮料、回体力，需要量以内每 P 约 150 R2 分）。
        社区默认（もなみ：最后的相談基本不花、よほど事故ったら才买——当先验）：只买能让持有多一瓶 0–1 档、而且买完不挤占轮间需要量的；
        模拟价值比社区默认好过 2 个标准误才改，记偏离。一次相談只算一次，之后逐瓶执行。栏满时相談买不了（「交換する」不亮）。"""
        from itertools import combinations

        from hiro.brain import drinks as dk
        from hiro.state.context import drink_name
        from hiro.state.knowledge import FINALS_P_VALUE, interval_p_value

        self._finals_drink_why = ""
        key = (ps.mode, ps.step, tuple(sorted((it.get("index"), it.get("drink_id")) for it in _items(shelf, "drink"))))
        plan = getattr(self, "_finals_drink_plan", None)
        if plan is not None and plan["key"][:2] == key[:2] and set(plan["key"][2]) >= set(key[2]):
            for idx in plan["buy"]:
                it = next((x for x in shelf if x.get("index") == idx), None)
                if it is not None and not it.get("purchased") and it.get("drink_id") and _affordable(it, p):
                    return Decision(Action(ActionKind.SHOP_BUY, {"index": idx}), plan["reason"], plan["source"], 0.8, plan["cands"],
                                    warnings=plan["warnings"], decider="consult")  # fmt: skip
            self._finals_drink_why = plan["why_end"]
            return None
        held, capacity, full, warn = self._drink_holding(state, ps)
        if state.page_fields.get("drinks_full"):
            full = True
        hold = [h.get("drink_id") for h in held or []]
        free = 0 if full else capacity - len(hold)
        items = [
            it
            for it in _items(shelf, "drink")
            if it.get("drink_id") and it.get("price") is not None and it["price"] <= p
        ]
        deck_n = len(ps.deck) if ps.deck else None
        r1p = FINALS_P_VALUE["r1_reward"]
        if free <= 0 or not items:
            self._finals_drink_why = "饮料栏满（相談里换不了）" if free <= 0 else "货架没有买得起的饮料"
            self._finals_drink_plan = {
                "key": key,
                "buy": [],
                "reason": "",
                "source": "rule",
                "cands": [],
                "warnings": [],
                "why_end": self._finals_drink_why,
            }
            return None
        opts = [dk.Option("none", "不买（留 P 给轮间）", hold, None, cost=0, extra={"buy": []})]
        for k in range(1, min(free, len(items)) + 1):
            for combo in combinations(items, k):
                cost = sum(it["price"] for it in combo)
                if cost > p:
                    continue
                names = "、".join(it.get("name") or drink_name(it["drink_id"]) for it in combo)
                opts.append(dk.Option("buy" + "-".join(str(it["index"]) for it in combo), f"买 {names}", hold + [it["drink_id"] for it in combo],
                                      None, cost=cost, extra={"buy": [it["index"] for it in combo]}))  # fmt: skip
        need_loss = lambda o: interval_p_value(p + r1p, deck_n) - interval_p_value(p - o.cost + r1p, deck_n)  # noqa: E731

        def rule_ok(
            o,
        ) -> bool:  # 社区默认：多一瓶 0–1 档（不只是比空着好），且不挤占轮间需要量（留 P 的损失只按低价计）
            if o.key == "none":
                return True
            tags = dk.tag(o.drinks, "r1")
            bought = tags[len(hold) :]
            # 买的每一瓶在新持有里都要是 0–1 档（多买一瓶回复 / 元気 不算「缺好饮料」）
            return all(t <= 1 for t, _, _ in bought) and need_loss(o) <= FINALS_P_VALUE["low"] * o.cost + 1e-6

        allowed = [o for o in opts if rule_ok(o)]
        rule = min(allowed, key=lambda o: (dk.holding_key(o.drinks, "r1", capacity), o.cost))
        rows = {o.key: {"选项": o.label, "持有": dk.describe(o.drinks, "r1"), "花 P": o.cost, "剩 P": p - o.cost,
                        "留 P 损失（R2 分）": round(need_loss(o)), "社区默认": "是" if o is rule else ("可选" if o in allowed else "否")} for o in opts}  # fmt: skip
        pick, source, w, dev, unsure, val = rule, "rule", list(warn), None, None, {}
        why = f"社区默认（もなみ先验 + 不挤占轮间需要量）：{rule.label}"
        ctx = self._drink_sim_context(ps, "r1")
        if dk.SIM and ctx is not None:
            try:
                from hiro.sim import interval as iv
                from hiro.sim import resource as rv

                c = dict(ctx)
                c.pop("exam")
                base, notes = rv.setup_for("r1", drinks=hold, **c)
                ranked = sorted(opts[1:], key=lambda o: dk.holding_key(o.drinks, "r1", capacity))[: dk.MAX_SIM_OPTIONS]
                sims = [opts[0]] + ranked
                res = rv.adaptive_eval(
                    [base] + [iv._with(base, drinks=o.drinks) for o in ranked], threshold=dk.OVERRIDE_SE, workers=4
                )
                val = {}
                for o, r in zip(sims, res):
                    v = 1.2 * r["gain"] - need_loss(o)
                    val[o.key] = (v, 1.2 * r["gain_se"])
                    rows[o.key].update(
                        {
                            "R1 期望": r["mean"],
                            "R1 比不买": f"{r['gain']:+,} ± {r['gain_se']:,}",
                            "总价值（R2 分）": round(v),
                        }
                    )
                best = max(sims, key=lambda o: val[o.key][0])
                if rule.key not in val:
                    val[rule.key] = (0.0, 0.0)
                d_v = val[best.key][0] - val[rule.key][0]
                se = (val[best.key][1] ** 2 + val[rule.key][1] ** 2) ** 0.5
                tail = f"（模拟 {rv.POLICY} 续打 R1 {res[0]['n']} 局配对（自适应），价值 = 1.2 × R1 增益 − 留 P 损失{'；' + '、'.join(notes) if notes else ''}）"
                if best is not rule and d_v > dk.OVERRIDE_SE * max(se, 1):
                    pick, source = best, "sim"
                    dev = {"where": "CONSULT(本战)", "stage": "r1", "rule": rule.label, "pick": best.label, "gain": round(d_v), "gain_se": round(se),
                           "cross_tier": True, "policy": rv.POLICY, "seeds": res[0]["n"], "notes": notes}  # fmt: skip
                    dk.log_deviation(dev)
                    w.append(f"community_deviation：{rule.label} → {best.label}（{d_v:+,.0f} ± {se:,.0f}）")
                    why = f"偏离社区默认：{best.label} 比 {rule.label} 价值高 {d_v:+,.0f} ± {se:,.0f}（> {dk.OVERRIDE_SE:g} 个标准误）{tail}"
                else:
                    why += f"；模拟里最好的是 {best.label}（比默认 {d_v:+,.0f} ± {se:,.0f}），不到 {dk.OVERRIDE_SE:g} 个标准误，按默认{tail}"
                    if best is not rule and d_v > 0:
                        unsure = f"模拟里 {best.label} 比社区默认价值高 {d_v:+,.0f} ± {se:,.0f}，在噪声内"
            except Exception as e:
                logger.warning("本战相談饮料估值失败，按社区默认：%s", e)
                why += f"；模拟失败（{e}），按社区默认"
                unsure = f"模拟失败（{e}）"
        line, llm_traces, conf = "", [], 0.8
        if unsure and llm_path_on("consult") and self.llm is not None:
            others = sorted((o for o in opts if o is not rule), key=lambda o: -val.get(o.key, (float("-inf"), 0))[0])
            picked = [rule] + others[:3]
            options = [(Action(ActionKind.SHOP_BUY, {"index": o.extra["buy"][0]}) if o.extra["buy"] else Action(ActionKind.SHOP_END),
                        {"名字": o.label, **{k: v for k, v in rows[o.key].items() if k != "选项"}}) for o in picked]  # fmt: skip
            stub = Decision(options[0][0], why, "rule", 0.8, decider="consult")
            d = self._ask_choice("consult", stub, options, "本战相談：买不买饮料（买哪几瓶），剩下的 P 留给轮间", unsure, state,
                                 {"现有 P": p, "饮料栏空位": free})  # fmt: skip
            if d is not stub:
                pick = next(o for o, (a, _) in zip(picked, options) if a is d.action)
                source, w, line, llm_traces, conf = d.source, w + d.warnings, d.line, d.llm, d.confidence
                if d.source == "llm":
                    why = f"{d.reason}（LLM；{why}）"
        cands = [Candidate(Action(ActionKind.SHOP_BUY, {"index": o.extra["buy"][0]}) if o.extra["buy"] else Action(ActionKind.SHOP_END),
                           preview=rows[o.key], expected=rows[o.key].get("总价值（R2 分）"), note=o.label) for o in opts]  # fmt: skip
        reason = f"【本战相談·饮料按价值】{why}；之后持有：{dk.describe(pick.drinks, 'r1')}，剩 {p - pick.cost}P"
        self._finals_drink_plan = {"key": key, "buy": pick.extra["buy"], "reason": reason, "source": source, "cands": cands, "warnings": w,
                                   "why_end": why, "line": line, "llm": llm_traces}  # fmt: skip
        if not pick.extra["buy"]:
            self._finals_drink_why = why
            return None
        return Decision(Action(ActionKind.SHOP_BUY, {"index": pick.extra["buy"][0]}), reason, source, conf, cands, warnings=w, line=line,
                        llm=llm_traces, decider="consult")  # fmt: skip

    def _consult_drinks(self, state: GameState, ps: ProduceState, shelf: list, p: int, budget: int, *, stage: str, only_accident: bool,
                        go=None, fill: bool = False) -> Decision | None:  # fmt: skip
        """相談买饮料：候选 = 买货架上的每一瓶（栏有空位时；栏满时「交換する」不亮，买不了）/ 不买，按买完的持有组合比较
        （hiro.brain.drinks）。「事故」（drinks.accident）时可以花到 p，否则只花预算内的。only_accident：本战，只在事故时买。
        fill：选拔③ 前最后一次相談的收尾，空栏补期望不为负的饮料（不比空着差的都算）。返回 None = 不买饮料。"""
        from hiro.brain import drinks as dk
        from hiro.state.context import drink_name

        held, capacity, full, warn = self._drink_holding(state, ps)
        if state.page_fields.get("drinks_full"):
            full = True  # 执行侧看到「所持上限」/ 识别读到饮料栏满（第六局第 12、19 天：栏满时买饮料「交換する」不亮，重试也没用）
        hold = [h.get("drink_id") for h in held or []]
        if full or len(hold) >= capacity:
            return None
        trouble = dk.accident(hold, stage, capacity)
        if only_accident and not trouble:
            return None
        limit = p if trouble else budget
        items = [it for it in _items(shelf, "drink") if it.get("drink_id") and _affordable(it, limit)]
        if not items:
            return None
        opts = [dk.Option("none", "不买", hold, Action(ActionKind.SHOP_END))]
        for it in items:
            opts.append(dk.Option(f"drink{it['index']}", f"买 {it.get('name') or drink_name(it['drink_id'])}", hold + [it["drink_id"]],
                                  Action(ActionKind.SHOP_BUY, {"index": it["index"]}), cost=it["price"], extra={"index": it["index"]}))  # fmt: skip
        res = dk.choose(opts, stage, capacity, sim_context=self._drink_sim_context(ps, stage), where="CONSULT")
        self._drink_resolve(res, opts, stage, "consult", state)  # 拿不准时 LLM 先定 pick，后面的预算记账按它来
        pick = res["pick"]
        if pick.key == "none":
            return None
        if fill and dk.tag(pick.drinks, stage)[-1][0] > 3:
            return None
        tier = (
            "第一档：补全饮料（事故：" + "、".join(trouble) + "）"
            if trouble
            else ("最后一次相談收尾：空栏补饮料" if fill else "第一档：补全饮料")
        )
        if go is None:
            dec = self._drink_decision(res, opts, stage, "consult", warn, state)
            dec.reason = (
                f"【本战相談·饮料事故：{'、'.join(trouble)}】{dec.reason}（もなみ：事故时才在这里买，价格和轮间一样）"
            )
            return dec
        dec = go(pick.action, pick.label, pick.cost, tier, index=pick.extra.get("index"))
        sub = self._drink_decision(res, opts, stage, "consult", warn, state)
        dec.reason += f"；{sub.reason}"
        dec.candidates = dec.candidates + sub.candidates
        dec.warnings = dec.warnings + sub.warnings
        dec.source = sub.source
        if sub.llm:
            dec.llm, dec.line, dec.confidence = dec.llm + sub.llm, sub.line or dec.line, sub.confidence
        return dec

    def study(self, state: GameState) -> Decision:
        """授業三选一：选本人おすすめ効果那一档（社区：授業避开眠気档，除非体力不够）。

        page_fields.choices 两种格式都接（见 _event_roles）；choices 缺失时按实机核实的固定位置（knowledge.STUDY_LAYOUT：A / B / 眠気）。
        非眠気的两项按顺序是 A、B；选项文字或选中项的效果说明（page_fields.effect）里出现效果名时以效果名为准。"""
        if (d := self._hajime_nia_choice(state, "study")) is not None:
            return d
        choices = state.page_fields.get("choices") or []
        ps = self.produce_state(state)
        effect, slot = self._study_effect()
        roles = _event_roles(choices)
        trouble = roles["trouble"] if choices else STUDY_LAYOUT.index("trouble")
        if ps is not None and ps.stamina < 5 and trouble is not None:
            return Decision(Action.event_select(trouble), "体力不足 5，只能选眠気档", "rule", 0.8, decider="study")
        if not choices:
            idx = STUDY_LAYOUT.index(slot)
            return Decision(Action.event_select(idx), f"选{effect}关系那档：按固定位置第 {idx + 1} 项（识别未提供选项）", "rule", 0.8,
                            warnings=["识别未提供 choices，按实机核实的位置选择"], decider="study")  # fmt: skip
        hit = next((c["index"] for c in choices if effect and effect in (c.get("text") or "")), None)
        if hit is None:
            hit = _from_selected_effect(state.page_fields, roles["plain"], effect)
        if hit is not None:
            if (
                d := self._study_change_cost(
                    state, choices, hit, trouble, f"选{effect}关系那档（本人おすすめ効果，按效果名）"
                )
            ) is not None:
                return d
            return Decision(
                Action.event_select(hit),
                f"选{effect}关系那档（本人おすすめ効果，按效果名）",
                "rule",
                0.9,
                decider="study",
            )
        plain = roles["plain"]
        pos = {"A": 0, "B": 1}[slot]
        if len(plain) == 2:
            return Decision(Action.event_select(plain[pos]), f"选{effect}关系那档：非眠気两项里的第 {pos + 1} 项（{slot} 档）", "rule", 0.85,
                            decider="study")  # fmt: skip
        idx = STUDY_LAYOUT.index(slot)
        return Decision(Action.event_select(idx), f"选{effect}关系那档：选项识别不全，按固定位置第 {idx + 1} 项", "rule", 0.6,
                        warnings=[f"choices 不完整（非眠気项 {plain}），按固定位置选择"], decider="study")  # fmt: skip

    def _study_change_cost(
        self, state: GameState, choices: list[dict], hit: int, trouble: int | None, base: str = "选本人おすすめ効果档"
    ) -> Decision | None:
        """授業档位是「セレクトチェンジ」（选拔第 10、17 天、本战第 1、4 天 [wiki]）：会从牌组换出一张卡，换入的卡也未必合用。
        换卡不受决策器控制（change_controlled=False，执行仍是 KAA 盲换第 0 张）时，改选不换卡的眠気档：眠気 费用 0、可以打掉除外，
        代价远小于盲换走核心卡（第 4 局本战两次授業换走了 デイドリーミング、がむしゃら，换进 ハイテンション、紅葉ランニング）。
        受控时照常选本人おすすめ効果档，换入 / 换出由 change_pick / change_remove 决定。"""
        flat = lambda t: "".join(str(t or "").split())  # noqa: E731  识别的效果说明按行折开（「セレク\nトチェンジ」）
        text = lambda i: next((flat(c.get("text")) + flat(c.get("effect")) for c in choices if c.get("index") == i), "")  # noqa: E731
        sel = flat(state.page_fields.get("effect"))
        ps = self.produce_state(state)
        change_day = ps is not None and (ps.mode, ps.step) in CHANGE_STUDY_DAYS
        is_change = (
            "セレクトチェンジ" in text(hit)
            or ("セレクトチェンジ" in sel and state.page_fields.get("selected") == hit)
            or change_day
        )
        if not is_change:
            return None
        from hiro.state.produce import FINALS

        if self.change_controlled:
            # 换卡一旦开始就取消不了（阶段 1 没有取消键，取消会绕回换卡页：main8 第 1 次授業死循环），按「必须换掉一张」评估这一档：
            # 牌组里有明显废牌（基础卡 / 冲突牌 / 好印象系，价值 ≤ 1）才选；没有废牌或牌组未知（本战）时改选眠気档，避免换亏核心牌组
            deck = (
                [{"card_id": c.card_id, "upgrade": c.upgrade} for c in ps.deck] if ps is not None and ps.deck else None
            )
            finals = ps is not None and ps.mode == FINALS
            worst = _removable(deck, self._deck_counts(state)[0], finals=finals) if deck else None
            # 有可换的废牌：价值 ≤ 1，或非一次性的非核心牌（用户 2026-11-03 思い出し笑い「能换掉的时候就应该换掉」）
            junk = worst is not None and (worst[0] <= CHANGE_JUNK_VALUE or cull_tier(worst[1]["card_id"]) <= 2)
            if junk or (deck is None and not finals) or (finals and (deck is None or worst is not None)):
                # 本战一律换（协调 2026-11-04 审计：第 21 / 22 / 25 局）：手册「本战第 4 天课程换掉剩余弱卡」「四次课程…一次换掉最弱卡」；
                # 另一档是眠気档，会往牌组里加 眠気，而用户删牌顺序第一位就是 眠気（小牌组）。换出由 change_remove 保护核心卡、换入按价值选；
                # 牌组未知也换（阶段 2 读对话框里的牌组再挑换出）；只有已知牌组里全是保护卡（没有可换的）时才选眠気档
                why = (
                    f"牌组里有可换的废牌 {_card_label(worst[1])}（价值 {worst[0]:g}）"
                    if junk
                    else "本战按手册换掉最弱的卡（另一档会加 眠気）"
                    if finals
                    else "选拔期牌组里通常还有基础卡可换"
                )
                return Decision(Action.event_select(hit), f"{base}；セレクトチェンジ：{why}，换入 / 换出由换卡决策器按卡牌价值指定",
                                "rule", 0.85, decider="study")  # fmt: skip
            if trouble is not None and "セレクトチェンジ" not in text(trouble):
                why = (f"牌组里最差的可换卡也有价值 {worst[0]:g}（{_card_label(worst[1])}），没有废牌" if worst is not None
                       else "牌组未知（本战开局还没读牌组页）")  # fmt: skip
                return Decision(Action.event_select(trouble), f"这一档是セレクトチェンジ，开始后取消不了、必须换掉一张；{why}，换亏风险大。"
                                "改选眠気档：眠気 费用 0、打出后除外", "rule", 0.8, decider="study")  # fmt: skip
            return Decision(
                Action.event_select(hit), f"{base}；三档都会换卡，按卡牌价值换", "rule", 0.6, decider="study"
            )

        if ps is None or ps.mode != FINALS:
            # 选拔：执行侧已保护核心卡（96c7acc），换入的卡可能是冲突牌，但之后的相談还能删；照常选本人おすすめ効果档
            return Decision(Action.event_select(hit), f"{base}；セレクトチェンジ：执行侧不换核心卡，换入的卡若是冲突牌，之后相談再删",
                            "rule", 0.8, warnings=["换卡未受控：换入候选按第 0 张，可能换进冲突牌（第 4 局本战换进了 ハイテンション）"],
                            decider="study")  # fmt: skip
        if trouble is not None and "セレクトチェンジ" not in text(trouble):  # 本战：眠気档只加眠気、不换卡
            return Decision(Action.event_select(trouble), "这一档是セレクトチェンジ，换入 / 换出还不由决策器按价值指定（执行侧只做核心卡保护；"
                            "第 4 局本战盲换第 0 张换走了 デイドリーミング）；改选眠気档：眠気 费用 0、可打掉除外，代价小得多", "rule", 0.8,
                            warnings=["换卡未受控：授業セレクトチェンジ 改选眠気档"], decider="study")  # fmt: skip
        return Decision(Action.event_select(hit), "三档都会换卡，选本人おすすめ効果档", "rule", 0.6,
                        warnings=["换卡未受控，可能换走核心卡（执行侧应先读阶段 2 的牌组）"], decider="study")  # fmt: skip

    # ---------------------------------------------------------------- 换卡（授業セレクトチェンジ）
    def change_pick(self, state: GameState) -> Decision:
        """换卡阶段 1：从候选里选换入的卡（page_fields.candidates，格式同 SELECT_CARD）。按 card_value 选最高。
        这一步取消不了，换不换亏要在授業选档时判断（_study_change_cost）。"""
        cands = state.page_fields.get("candidates") or []
        if not cands:
            return Decision(
                Action.ui(ActionKind.WAIT, why="换卡候选未识别"),
                "换卡候选未识别",
                "fallback",
                0.1,
                decider="card_change",
            )
        # 换入按删卡顺序挑（协调 2026-11-07）：挑 cull_key 最大的（最晚才会被删），同档再比价值。只按 card_value 挑时，
        # 换进来的往往正是下次最先换掉的那类（Win2：换进「止められない想い」，下一次换卡就把它换掉，3 张元気な挨拶 没动）
        from hiro.state.produce import FINALS

        finals = (state.produce or {}).get("mode") == FINALS
        scored = sorted(((card_value(c.get("card_id"), c.get("upgrade") or 0), c) for c in cands),
                        key=lambda x: (cull_key(x[1].get("card_id"), x[1].get("upgrade") or 0, finals=finals), x[0], -x[1].get("index", 0)),
                        reverse=True)  # fmt: skip
        value, best = scored[0]
        notes = [f"#{c.get('index')} {_card_label(c)} {v:g}" for v, c in scored]
        # 阶段 1 没有取消键（main8：取消后绕回换卡页死循环），所以总是选最好的那张
        self.change_target = best.get("card_id")
        return Decision(Action(ActionKind.CHANGE_CARD, {"stage": 1, "pick_index": best.get("index")}),
                        f"换入 {_card_label(best)}（价值 {value:g}；{'、'.join(notes)}）", "rule", 0.8, decider="card_change")  # fmt: skip

    def _note_seen_deck(self, cards: list[dict]) -> None:
        """换卡 / 删卡 / 强化对话框里见过的牌：记下来（card_id → 见过的最多张数），produce.deck 没识别时选卡用它判断「有没有」。
        对话框只显示一屏（第十九局 12 / 22 张），所以这是下限：只用来判断「有」，不用来判断「缺」和张数放开。"""
        ids = [c.get("card_id") for c in cards or [] if c.get("card_id")]
        if len(ids) >= 8:  # 太少的不像牌组对话框
            now: dict = {}
            for cid in ids:
                now[cid] = now.get(cid, 0) + 1
            seen = dict(getattr(self, "seen_deck", {}) or {})
            for cid, n in now.items():
                seen[cid] = max(seen.get(cid, 0), n)
            self.seen_deck = seen

    def change_remove(self, state: GameState) -> Decision:
        """换卡阶段 2：从牌组卡格（识别 read_card_dialog 的 cards）里选换出的卡，和删卡同一个顺序（cull_pool，用户 2026-11-03）：
        非一次性的非核心牌（思い出し笑い）> 会塞眠気的非一次性牌 > 一次性的牌（按价值）> 核心牌的多余副本；不换 眠気等トラブル（游戏不允许）、
        保护卡（按确认过的张数；牌组没记账时所有副本都不换）、定制卡。取消会绕回换卡页（main8 死循环），
        所以只有卡格没读到、或者剩下的全是保护卡时才输出取消；换入的卡不比换出的强时照样换，并给提醒。"""
        cards = state.page_fields.get("cards") or []
        self._note_seen_deck(cards)
        if not cards:
            return Decision(Action(ActionKind.CHANGE_CARD, {"cancel": True}), "换卡阶段 2 的牌组没读到：取消换卡，不盲换", "rule", 0.6,
                            warnings=["read_card_dialog 没给 cards"], decider="card_change")  # fmt: skip
        if any(c.get("card_id") is None for c in cards):
            warn = [f"有 {sum(c.get('card_id') is None for c in cards)} 格没认出，不会选它们"]
        else:
            warn = []
        from hiro.state.produce import FINALS

        counts, _ = self._deck_counts(state)
        if self._hif_other(state):
            flow_pool = self._cull(state, cards, "change")
            worst = (
                (card_value(flow_pool[0]["card_id"], flow_pool[0].get("upgrade") or 0), flow_pool[0])
                if flow_pool
                else None
            )
        else:
            worst = _removable(cards, counts, finals=(state.produce or {}).get("mode") == FINALS)
        if counts is None and any(c.get("card_id") for c in cards):
            warn.append("牌组没记账：保护卡（えいえいおー 等）所有副本都不换（对话框只显示一屏，张数不可信）")
        if worst is None:
            return Decision(Action(ActionKind.CHANGE_CARD, {"cancel": True}), "牌组里只剩核心 / 保护卡 / 定制卡，取消换卡", "rule", 0.7, warnings=warn,
                            decider="card_change")  # fmt: skip
        value, card = worst
        incoming_id = self.change_target
        incoming = card_value(incoming_id, 0) if incoming_id else None
        if incoming is not None and incoming <= value:
            # 取消会绕回换卡页（main8 死循环），换亏也只能换：换出价值最低的那张
            warn.append(
                f"换入的卡（{incoming:g}）不比换出的 {_card_label(card)}（{value:g}）强；取消会绕回换卡页，只能换"
            )
        self.change_target = None

        def act(c: dict) -> Action:
            return Action(ActionKind.CHANGE_CARD, {"stage": 2, "remove_card_id": c["card_id"], "remove_index": c.get("index"),
                                                   "remove_upgrade": c.get("upgrade")})  # fmt: skip

        if "flow_tier" in card:
            return Decision(act(card), f"换出{FLOW_CULL[card['flow_tier']]} {_card_label(card)}（按流派 + 偶像卡攻略）", "rule", 0.8, warnings=warn,
                            decider="card_change")  # fmt: skip
        tier = CULL_TIERS.get(cull_tier(card["card_id"]), "")
        rule = Decision(act(card), f"换出{tier} {_card_label(card)}（价值 {value:g}；换 / 删同序，用户 2026-11-03；保护卡不换）", "rule", 0.8,
                        warnings=warn, decider="card_change")  # fmt: skip
        # 拿不准：最该换出的已经不是明确的垃圾牌（0–3 档），只能按启发式价值排。牌组没记账 / 有格没认出只作补充说明
        # （没记账时规则已经保护所有副本，没认出的格 LLM 也选不了）
        if cull_tier(card["card_id"]) < 4:
            return rule
        unsure = [f"没有明确该换的牌（最该换出的是「{tier}」档），只能按价值估"]
        if counts is None:
            unsure.append("牌组没记账，保护卡的所有副本都不在候选里")
        if any(c.get("card_id") is None for c in cards):
            unsure.append("有卡格没认出，不在候选里")
        pool = cull_pool(cards, "change", counts=counts, finals=(state.produce or {}).get("mode") == FINALS)[:4]
        options = [(act(c), {"名字": _card_label(c), "卡 id": c["card_id"], "档": CULL_TIERS.get(cull_tier(c["card_id"]), ""),
                             "价值估计": round(card_value(c["card_id"], c.get("upgrade") or 0), 1)}) for c in pool]  # fmt: skip
        extra = {"换入的卡": _card_label({"card_id": incoming_id, "upgrade": 0}) if incoming_id else "未知"}
        return self._ask_choice(
            "change_remove",
            rule,
            options,
            "授業セレクトチェンジ第 2 步：从牌组里选一张换出（不能取消，必须换一张）",
            "；".join(unsure),
            state,
            extra,
        )

    def _study_effect(self) -> tuple[str | None, str]:
        """本人おすすめ効果的日文名，以及它在授業里是 A 档还是 B 档。"""
        from hiro.brain import hif_rules as rules
        from hiro.state.context import catalog

        idol = catalog().one("IdolCard", self.profile.idol_id) if self.profile else None
        if not idol:
            return None, "B"
        name = idol["examEffectType"].split("_", 1)[1]
        slot = "A" if rules.PLAN_EFFECTS[idol["planType"]][0] == name else "B"
        return EFFECT_JA.get(name), slot

    def outing(self, state: GameState) -> Decision:
        """外出三档：第 5 天选眠気档（社区常规：多一张强化卡 + ワッペン星星）；P 点充裕时付 50P；否则「なし」。

        page_fields.choices 两种格式都接（见 _event_roles）；choices 缺失时按实机核实的固定位置（knowledge.OUTING_LAYOUT：付 50P / 眠気 / なし）。"""
        if (d := self._hajime_nia_choice(state, "outing")) is not None:
            return d
        choices = state.page_fields.get("choices") or []
        ps = self.produce_state(state)
        warn = [] if choices else ["识别未提供 choices，按实机核实的位置选择"]
        if choices:
            roles = _event_roles(choices)
            trouble, paid = roles["trouble"], roles["paid"]
            free = next((i for i in roles["plain"] if i != paid), None)
            if free is None:
                free = OUTING_LAYOUT.index("free")
                warn.append("没认出免费档，按固定位置")
        else:
            trouble, paid, free = (OUTING_LAYOUT.index(k) for k in ("trouble", "paid", "free"))
        if ps and ps.mode == "produce-007" and ps.step == 5 and trouble is not None:
            return Decision(
                Action.event_select(trouble),
                "社区：选拔第 5 天外出选中间的眠気档，之后在相談花 100P 删掉——比当场花 50P 避开多拿一次获得 / 删卡收益 [ep07]",
                "rule",
                0.8,
                warnings=warn,
                decider="outing",
            )
        if ps and paid is not None and ps.produce_point >= 150:
            return Decision(
                Action.event_select(paid),
                f"P 点 {ps.produce_point} 充裕，付 50P 多拿一张强化卡",
                "rule",
                0.6,
                warnings=warn,
                decider="outing",
            )
        if ps is None:
            warn = warn + ["局面不全（P 点 / 日程未知），选「なし」"]
        return Decision(Action.event_select(free), "不加眠気、不花 P 点", "rule", 0.5, warnings=warn, decider="outing")

    def select_pitem(self, state: GameState) -> Decision:
        """P 道具背包三选一（カスタムPアイテム：取得背包 / 第一段 / 第二段定制）。page_fields.pitems = [{index?, item_id?, name?}]。
        社区规则见 hiro.brain.pitem：背包黄 > 緑 > 赤；ロボ > もじゃ；花 > リボン > メダル > 羽。"""
        from hiro.brain import pitem
        from hiro.state.modes import NIA, family

        items = state.page_fields.get("pitems") or []
        if family((state.produce or {}).get("mode")) == NIA and items:
            from hiro.brain.hajime_nia import choose_nia_pitem

            plan = None
            try:
                from hiro.state import modes

                plan = modes.master().one(
                    "IdolCard",
                    (state.produce or {}).get("idol_card_id") or (self.profile.idol_id if self.profile else ""),
                )["planType"]
            except Exception:  # noqa: BLE001
                pass
            k, reason, _ = choose_nia_pitem(self._hajime_nia_status(state), items, plan)
            if k is not None:
                return Decision(
                    Action.activate_effect(items[k].get("index", k)), reason, "rule", 0.7, decider="select_pitem"
                )
        parsed = [
            None if it.get("custom") and not (it.get("name") or it.get("item_id") or it.get("id")) else pitem.parse(it)
            for it in items
        ]
        if any(q and q["stage"] != "pouch" for q in parsed):
            # 同一页别的候选已是玩偶 / 装饰段：只有颜色（名字没认出）的格子不是背包，按没认出算（原来当「背包 黄」3 分，压过 インコ / うさぎ）
            parsed = [None if q and q["stage"] == "pouch" and not (it.get("name") or it.get("item_id") or it.get("id")) else q
                      for it, q in zip(items, parsed)]  # fmt: skip
        idx = [it.get("index", it.get("slot", i)) for i, it in enumerate(items)]
        cands = [Candidate(Action.activate_effect(idx[i]), note=it.get("name") or it.get("item_id"),
                           preview={"名称": it.get("name"), "阶段": p and p["stage"], "社区": p and pitem.reason(p)} if p else None)
                 for i, (it, p) in enumerate(zip(items, parsed))]  # fmt: skip
        known = [i for i, p in enumerate(parsed) if p is not None]
        if not known:
            # 名称认不出时交给 decide() 的兜底：按背包颜色（黄 > 緑 > 赤），颜色也没有就停下（决策-08：不选第一个）
            return Decision(Action.ui(ActionKind.WAIT, why="P 道具候选未识别"), "P 道具候选名称未识别", "fallback", 0.2, cands,
                            warnings=["识别未提供 pitems 或无法解析"], decider="select_pitem")  # fmt: skip
        best = max(known, key=lambda i: (pitem.score(parsed[i]), -i))
        rule = Decision(Action.activate_effect(idx[best]), pitem.reason(parsed[best]), "rule", 0.9 if pitem.score(parsed[best]) >= 3 else 0.6,
                        cands, decider="select_pitem")  # fmt: skip
        if len(known) > 1 and all(parsed[i] == parsed[known[0]] for i in known):
            if any(it.get("custom") for it in items):
                # 定制页（pitem_custom 能认装饰了）三个候选名字一样：多半是候选还没出来（DMM own2 seq 170：定制页刚进场、三格还空，
                # 识别按背包颜色给了「ロボ（黄）」×3，按第 1 个点下去定制成了 うさぎ）。停下重读，不盲选
                why = f"P 道具定制 {len(known)} 个候选识别成一样的（{items[known[0]].get('name')}）：候选像是还没出来 / 没认出，重读"
                return Decision(
                    Action.ui(ActionKind.WAIT, why=why),
                    why,
                    "fallback",
                    0.1,
                    cands,
                    warnings=[why],
                    decider="select_pitem",
                )
            # 非定制页（旧识别）：第二段装饰没认出时规则只能按第 1 个选。LLM 也看不到装饰，不问；记成兜底，报告里能数出来
            return replace(
                rule,
                source="fallback",
                confidence=0.3,
                warnings=[
                    f"P 道具 {len(known)} 个候选识别成一样的（{items[known[0]].get('name')}）：多半是第二段装饰没认出，按第 1 个选"
                ],
            )
        top = pitem.score(parsed[best])
        ties = {str(sorted(parsed[i].items())) for i in known if pitem.score(parsed[i]) == top}
        unknown_n = len(items) - len(known)
        stage_max = max(
            {"pouch": pitem.POUCH_RANK, "plush": pitem.PLUSH_RANK, "deco": pitem.DECO_RANK}[
                parsed[best]["stage"]
            ].values()
        )
        why = (f"{len(ties)} 个不同的候选社区排序打平" if len(ties) > 1 else
               f"{unknown_n} 个候选没认出，认出的最好只是 {items[best].get('name')}" if unknown_n and top < stage_max else None)  # fmt: skip
        if why is None:
            return rule
        order = [best] + [i for i in range(len(items)) if i != best]
        options = [(Action.activate_effect(idx[i]), {"名字": items[i].get("name") or "未识别", "颜色": items[i].get("color"),
                                                    "社区": pitem.reason(parsed[i]) if parsed[i] else "未识别"}) for i in order]  # fmt: skip
        return self._ask_choice("select_pitem", rule, options, "P 道具（カスタムPアイテム）三选一", why, state)

    MOVE_OUT = ("lost", "grave")  # 移走（除外 / 弃牌）：选最该丢的
    MOVE_KEEP = ("hold", "hand", "deck_top")  # 留着用（保留 / 手牌 / 山札顶）：选最想用的

    def card_move(self, state: GameState) -> Decision:
        """考试里的「技能卡移动」对话框（卡效果让你从候选里选牌移到别处；协调 11-14 新决策点，原来盲选找到的最后一张）。

        page_fields（平台模块的执行器读出来交给这里）：
          destination：lost（除外）/ grave（弃牌）/ hold（保留）/ hand（手牌）/ deck_top（山札顶）/ deck（山札）/ None（没认出）；
          count：要选几张（缺省 1）；title：对话框标题文字（可空，给 LLM 看）；
          candidates：[{index（画面上第几张，执行时点它）, card_id, upgrade}]；exam：考试局面摘要（回合、剩余回合、体力、元気、分数、やる気…，可空）。
        算法：移走时选最该丢的（眠気等トラブル 优先，再按价值从低到高，核心卡最后）；留着用时选价值最高的（核心卡优先）。
        前两名差距小（价值差 < 0.3、同档）或有候选没认出时交 LLM（开关 card_move）；目的地没认出时按原来的做法选最后一张、记 fallback。
        输出 ActivateEffect {"pick_indexes": [...]}。没有候选：Wait（停下等重新识别）。"""
        pf = state.page_fields
        raw = sorted(pf.get("candidates") or [], key=lambda c: c.get("index", 0))
        dest = pf.get("destination")
        n = max(1, int(pf.get("count") or 1))
        if not raw:
            return Decision(Action.ui(ActionKind.WAIT, why="技能卡移动候选未识别"), "技能卡移动：候选没读出来，停下等重新识别", "fallback", 0.1,
                            warnings=["EXAM_CARD_MOVE candidates 为空"], decider="card_move")  # fmt: skip
        pick = lambda idx: Action(ActionKind.ACTIVATE_EFFECT, {"pick_indexes": [raw[i].get("index", i) for i in idx]})  # noqa: E731
        name = lambda c: _card_label(c) if c.get("card_id") else "未识别"  # noqa: E731
        if dest not in self.MOVE_OUT + self.MOVE_KEEP:
            idx = list(range(len(raw)))[-n:]
            return Decision(pick(idx), f"技能卡移动：目的地没认出（{dest}），按原来的做法选最后 {n} 张（{'、'.join(name(raw[i]) for i in idx)}）",
                            "fallback", 0.3, warnings=["EXAM_CARD_MOVE destination 未识别"], decider="card_move")  # fmt: skip
        out = dest in self.MOVE_OUT

        def key(i):
            c = raw[i]
            cid = c.get("card_id")
            v = card_value(cid, c.get("upgrade") or 0) if cid else 0.0
            core = _is_core_card(cid) if cid else False
            if out:  # 越该丢越前：トラブル → 非核心低价值 → 核心
                return (0 if cid and cull_tier(cid) == 0 else 1, core, v)
            return (-int(core), -v)  # 越想用越前

        order = sorted(range(len(raw)), key=key)
        idx = order[:n]
        why_dest = {"lost": "除外", "grave": "弃牌", "hold": "保留", "hand": "手牌", "deck_top": "山札顶", "deck": "山札"}[dest] if dest in (
            "lost", "grave", "hold", "hand", "deck_top", "deck") else dest  # fmt: skip
        reason = (f"技能卡移动到{why_dest}：{'、'.join(name(raw[i]) for i in idx)}"
                  f"（{'移走选最该丢的：トラブル优先、再按价值从低到高、核心卡最后' if out else '留着用选价值最高的、核心卡优先'}）")  # fmt: skip
        cands = [Candidate(pick([i]), note=name(raw[i]), preview={"名字": name(raw[i]), "价值估计": round(card_value(raw[i].get("card_id"), raw[i].get("upgrade") or 0), 2) if raw[i].get("card_id") else None})
                 for i in order]  # fmt: skip
        rule = Decision(pick(idx), reason, "rule", 0.8, cands, decider="card_move")
        if n != 1 or len(raw) < 2:
            return rule
        unknown = [i for i in range(len(raw)) if not raw[i].get("card_id")]
        a, b = order[0], order[1]
        close = key(a)[:-1] == key(b)[:-1] and abs(key(a)[-1] - key(b)[-1]) < 0.3
        if not unknown and not close:
            return rule
        why = f"{len(unknown)} 张候选没认出" if unknown else f"{name(raw[a])} 和 {name(raw[b])} 价值差不多"
        options = [(pick([i]), {"名字": name(raw[i]), "强化": raw[i].get("upgrade"), "价值估计": cands[k].preview["价值估计"],
                                "核心卡": _is_core_card(raw[i].get("card_id")) if raw[i].get("card_id") else None}) for k, i in enumerate(order)]  # fmt: skip
        return self._ask_choice("card_move", rule, options, f"考试中的技能卡移动：选一张移到{why_dest}（{'移走' if out else '留着用'}）", why, state,
                                {"对话框标题": pf.get("title"), "考试局面": pf.get("exam")})  # fmt: skip

    def event_choice(self, state: GameState) -> Decision:
        """事件选项页（随机事件 / 支援事件 / 开局二选一等多选项按钮页；去 KAA 设计稿 §8-2，用户 11-14：按整局培育来选，算法 → LLM → 兜底）。

        page_fields（平台模块的新主循环读出来交给这里，页面层不写选择规则）：
          title：事件标题（可空）；kind：random / support / start …（可空，只给 LLM 看）；
          choices：[{index, text, effect?（选中后弹出的效果说明）, cost_hp?, cost_p?, trouble?}]，按画面从上到下；
          dialogue_only：平台判的纯台词页（每个按钮都没有效果），true 时直接选第一个。
        输出 EventSelect {"select": index}；source = rule（严格占优）/ llm / fallback（粗估分）；读不出选项、或有选项效果没解析出来
        而 LLM 给不出答案时 WAIT（停机等人，同现在的做法）。LLM 路径开关 event_choice（HIRO_PRODUCE_LLM）。"""
        from hiro.brain import event_choice as ev

        pf = state.page_fields
        choices = sorted(pf.get("choices") or [], key=lambda c: c.get("index", 0))
        title = pf.get("title") or ""
        stop = lambda why, w: Decision(  # noqa: E731
            Action.ui(ActionKind.WAIT, why=why), why, "fallback", 0.1, warnings=w, decider="event_choice"
        )
        if pf.get("dialogue_only") is True and choices:
            # 平台按按钮外观判的纯台词页（白底深色字、没有图标 / 代价角标 / トラブル，ef07f2b）：选第一个，不停机
            return Decision(Action.event_select(choices[0]["index"]), f"事件「{title or '未识别'}」：{len(choices)} 个选项都是纯台词（没有效果），选第一个",
                            "rule", 0.9, decider="event_choice")  # fmt: skip
        if len(choices) < 2:
            return stop("事件选项没读出来（少于 2 个）：停下等人", ["EVENT_CHOICE choices 缺失"])
        if all(
            c.get("no_effect")
            or (c.get("effect") == "" and not any(c.get(k) for k in ("cost_hp", "cost_p", "trouble")))
            for c in choices
        ):
            # 识别读过、每一项都没有效果（开场纯台词二选一等，协调 11-17）：直接选第一个，不停机。effect 缺失（None）仍当没读到
            return Decision(Action.event_select(choices[0]["index"]), f"事件「{title or '未识别'}」：每个选项都没有效果（纯台词），选第一个",
                            "rule", 0.9, decider="event_choice")  # fmt: skip
        ps = self.produce_state(state)
        parsed = [ev.parse(c) for c in choices]
        vecs = [v for v, _ in parsed]
        unparsed = {i for i, (_, ok) in enumerate(parsed) if not ok}  # choices 里的位置
        raw = state.produce or {}  # 局面不全（ProduceState 构造不出）时体力 / 成长率直接读原始字段
        kw = {"stamina": ps.stamina if ps else raw.get("stamina"), "max_stamina": ps.max_stamina if ps else raw.get("max_stamina"),
                  "growth": ps.growth_permil if ps else raw.get("growth_permil"), "main_attr": (ps.main_attr if ps else raw.get("main_attr")) or self.main_attr}  # fmt: skip
        scores = [ev.score(v, **kw) for v in vecs]
        cands = [Candidate(Action.event_select(c["index"]), preview={"选项": c.get("text"), "效果": ev.describe(v), "粗估分": round(sc, 1)},
                           expected=sc, note=c.get("text")) for c, v, sc in zip(choices, vecs, scores)]  # fmt: skip
        head = f"事件「{title}」：" if title else "事件："
        if not unparsed and (k := ev.dominant(vecs)) is not None:
            return Decision(Action.event_select(choices[k]["index"]), f"{head}{choices[k].get('text')}（{ev.describe(vecs[k])}）在每一项上都不差于其他选项",
                            "rule", 0.9, cands, decider="event_choice")  # fmt: skip
        order = sorted(range(len(choices)), key=lambda i: (i in unparsed, -scores[i]))
        best = order[0]
        rule = Decision(Action.event_select(choices[best]["index"]),
                        f"{head}没有哪个选项全面更好，按粗估分选 {choices[best].get('text')}（{ev.describe(vecs[best])}，{scores[best]:.0f}）",
                        "fallback", 0.4, cands, warnings=[f"事件选项有 {len(unparsed)} 个效果没解析出来"] if unparsed else [], decider="event_choice")  # fmt: skip
        options = [(Action.event_select(choices[i]["index"]),
                    {"名字": choices[i].get("text") or f"第 {i + 1} 项", "效果说明": choices[i].get("effect"),
                     "解析出的变化": ev.describe(vecs[i]) if i not in unparsed else "没解析出来，看效果说明",
                     "代价": {k: choices[i][k] for k in ("cost_hp", "cost_p", "trouble") if choices[i].get(k)} or None,
                     "粗估分": round(scores[i], 1) if scores[i] != float("-inf") else "体力不够付"}) for i in order]  # fmt: skip
        why = "没有哪个选项全面更好" + (f"，{len(unparsed)} 个选项效果没解析出来" if unparsed else "")
        d = self._ask_choice("event_choice", rule, options, f"培育中的事件选项（{pf.get('kind') or '事件'}「{title or '标题未识别'}」）：选哪一项",
                             why, state, {"粗估分说明": "参数按成长率加权；体力低于一半时更值钱；只作参考"})  # fmt: skip
        if d.source != "llm" and (unparsed or scores[best] == float("-inf")):
            return stop(f"{head}有选项效果没解析出来 / 都付不起，LLM 没给出答案：停下等人", d.warnings + rule.warnings)
        return d

    def interval(self, state: GameState) -> Decision:
        """轮间（R1 与 R2 之间），一次一个动作。page_fields["interval"] 格式见 docs/llm-context.md「商店字段」。

        按计算选（协调 4a 第八局分析 B3，用户确认）：R1 的牌组 / 剩余饮料 / 倍率已记下（hiro.state.finals_context.CONTEXT）、
        模拟器主数据已构建时，进轮间算一次方案——回体力到几、买哪些饮料 / 卡，各组合用模拟器跑 R2 取期望分，选最高的；
        之后每帧按「还差什么」出下一个动作，货架刷新也按模拟估期望。算不了时走社区规则（_interval_rule）：
        先回体力到角色目标，再按 饮料 > 必拿 / S / A 卡 > 强化核心卡 花 P（货架没有复制 / 爆发时先刷新）。"""
        items = state.page_fields.get("interval")
        if not items:
            return self._interval_without_shelf(state)
        ps, unknown = self._interval_state(state)
        if ps is None:
            return Decision(Action(ActionKind.INTERVAL_END), "本页页头的体力 / P 点没读到，不买", "fallback", 0.2,
                            warnings=["轮间页头 stamina / produce_point 未识别（ProduceState 里的是 R1 之前的旧值，不能用）"],
                            decider="interval")  # fmt: skip
        if (d := self._interval_by_sim(state, ps, unknown, items)) is not None:
            return d
        return self._interval_rule(state, ps, unknown, items)

    INTERVAL_SIM = True  # 测试 / 兜底开关

    def _interval_by_sim(self, state: GameState, ps: ProduceState, unknown: set, items: dict) -> Decision | None:
        from hiro.sim import interval as iv

        if not self.INTERVAL_SIM or not _sim_ready():
            return None  # 开源版：模拟器主数据没构建（fetch_master.py）时走社区规则
        p, hp, max_hp = ps.produce_point, ps.stamina, ps.max_stamina
        ledger = self._interval_ledger_for(ps, p)
        # 货架签名只算还认得出、没买过的格子（第十七局：买掉的饮料格读成 drink_id=None，签名变了 → 整套重算，计划里的 ホエイ 没买）
        shelf_key = {(it.get("index"), it.get("card_id") or it.get("drink_id")) for k in ("cards", "drinks") for it in items.get(k) or []
                     if (it.get("card_id") or it.get("drink_id")) and not it.get("purchased")}  # fmt: skip
        cache = getattr(self, "_interval_plan", None)
        if cache is not None and cache.get("visit") != ledger["key"]:
            cache = self._interval_plan = None  # 换了一次轮间：重新规划
        if cache is not None and cache.get("rerolling"):
            cache["shelf"] = shelf_key  # 刷新过：新货架交给 _interval_reroll 处理（增益表已算好），不重算整套方案
        # 一次轮间只规划一次：买掉的格子会从货架消失，不因此重算（第九局：每买一次就重算、再找「显著」选项，多次检验后买进了
        # ありがとうの言葉）。货架出现新格子（刷新）才重算
        if cache is None or not shelf_key <= cache["shelf"]:
            from hiro.state.knowledge import interval_hp_target

            target_hp = iv.value_hp_target(
                max_hp, interval_hp_target(self.profile.idol_id if self.profile else None, max_hp)
            )  # HP_TARGET_VALUE（默认关）
            try:
                # 用户 2026-11-02：社区默认方案（补到 22 张 → R2 饮料 → 回体力到目标）为默认，B3 模拟方案好过 2 个标准误才偏离
                # 中途重算（第十八局本战：强化失败 → 结束 → 轮间重进，方案被推翻又叠加一套）：以账本里已执行的部分为前提
                res = iv.plan_tiered(items, hp=hp, max_hp=max_hp, p=p, star=None if "star" in unknown else ps.star, target_hp=target_hp,
                                     drink_capacity=ps.drink_capacity, wappen_left=None if "wappen_left" in unknown else ps.wappen_left,
                                     extra_drinks=ledger["drinks"], extra_cards=ledger["cards"], upgraded=ledger["upgraded"])  # fmt: skip
            except Exception as e:  # 模拟器不支持本场牌组等：走兜底
                logger.warning("轮间模拟失败，走兜底规则：%s", e)
                res = None
            if res is None:
                self._interval_plan = None
                return None
            cache = self._interval_plan = {"shelf": shelf_key, "res": res, "tried": {}, "visit": ledger["key"],
                                           "replanned": bool(ledger["drinks"] or ledger["cards"] or ledger["upgraded"])}  # fmt: skip
            self._interval_ask(state, cache)
            self._interval_prefetch_gains(state, ps, items, res)
            iv.save_interval_cache({"plan": cache, "ledger": ledger})
        res = cache["res"]
        if not res.get("significant", True):
            # 模拟里没有哪个选项明显好过「不买」（差在标准误以内）：按兜底规则执行，模拟表格写进记录
            d = self._interval_rule(state, ps, unknown, items)
            top = "；".join(f"{'、'.join(r['labels'])} {r['gain']:+,}±{r['gain_se']:,}" for r in res["rows"][:5])
            d.reason += f"（模拟 R2 {len(res['seeds'])} 个种子配对：没有选项显著好过不买，按规则。前几名：{top}）"
            d.candidates = d.candidates + [Candidate(Action(ActionKind.INTERVAL_END), preview={"组合": r["labels"], "P": r["cost"],
                                           "R2 期望分": r["mean"], "比不买": f"{r['gain']:+,} ± {r['gain_se']:,}"}, expected=r["mean"])
                                           for r in res["rows"][:12]]  # fmt: skip
            return d
        cands = [Candidate(Action(ActionKind.INTERVAL_END), preview={"组合": r["labels"], "P": r["cost"], "R2 期望分": r["mean"],
                                                                      "比不买": f"{r['gain']:+,} ± {r['gain_se']:,}"}, expected=r["mean"])
                 for r in res["rows"][:12]]  # fmt: skip
        dev = res.get("deviation")
        why = (f"{'偏离社区默认，用模拟方案' if dev else ('LLM 选的方案' if cache.get('llm') is not None and cache['llm'].source == 'llm' else '社区默认方案')}：{'、'.join(res['labels']) or '不买'}，比不买 {res['gain']:+,} ± {res['gain_se']:,}"
               f"（{res['cost']}P；模拟 R2 {res['policy']} {len(res['seeds'])} 个种子配对，{res['seconds']}s）。" + "；".join(res.get("rule_notes") or []))  # fmt: skip
        if dev:
            from hiro.brain.drinks import log_deviation

            if not cache.get("logged"):
                log_deviation({**dev, "kind_detail": "interval"})
                cache["logged"] = True

        def done(action: Action, step: str) -> Decision:
            iv.save_interval_cache({"plan": cache, "ledger": ledger})  # 每发一个动作落盘一次（重启后接着执行，不重算）
            hw = getattr(self, "_interval_held_warn", None)
            w = ([hw] if hw else []) + (
                [
                    f"community_deviation：{'、'.join(dev['rule']) or '不买'} → {'、'.join(dev['pick']) or '不买'}（{dev['gain']:+,} ± {dev['gain_se']:,}）"
                ]
                if dev
                else []
            )
            if dev and dev.get("has_hp"):
                # 协调 2026-11-02：社区续打对体力估值偏高，偏离方案里含体力档的都单独记一条（续打第 2、3 步改好前）
                w.append("community_deviation 含体力档：社区续打对体力的估值偏高（第十七局专家核对只是噪声），请复核")
            ld = cache.get("llm")
            if ld is not None:  # 方案是 LLM 定的：来源 / 台词都按它，调用记录只带在第一个动作上
                tail = f"；LLM：{ld.reason}" if ld.source == "llm" and ld.reason else ""
                return Decision(action, f"{step}；{why}{tail}", ld.source, ld.confidence, cands, warnings=w + ld.warnings, line=ld.line,
                                llm=cache.pop("llm_traces", []), decider="interval")  # fmt: skip
            return Decision(
                action, f"{step}；{why}", "sim" if dev else "rule", 0.8, cands, warnings=w, decider="interval"
            )

        recover = items.get("recover") or {}
        per2 = recover.get("price_per_2hp") or 10
        self.interval_target_hp = res[
            "target_hp"
        ]  # 执行侧离开前的体力检查用同一个目标（第十二局：决策 24、收尾 17 不一致）
        if hp < res["target_hp"] and recover.get("available", True) and p >= per2:
            return done(
                Action(ActionKind.INTERVAL_BUY, {"service": "recover"}),
                f"回体力 {hp} → 目标 {res['target_hp']}/{max_hp}",
            )
        shelf = {it["index"]: it for k in ("cards", "drinks") for it in items.get(k) or []}
        # drink_step 能用（有增益表的开局信息、能刷新）时，方案里的饮料一律交给它（第二十八局：社区默认方案把 ミックススムージー（换手·2 档，
        # 增益常为负）当抽牌类目标先买了）；用不了时弱饮料（3–4 档）仍不按方案买，其余照方案买
        step_ready = (self.INTERVAL_DRINK_STEP and self.INTERVAL_REROLL and (items.get("refresh") or {}).get("available") is not False
                      and self._gains_setup(ps, self._interval_held(state, ps), res) is not None)  # fmt: skip

        def plan_buy(step: bool, note: str = "") -> Decision | None:
            for idx in (
                [] if cache.get("rerolling") else res["buy"]
            ):  # 刷新过：方案里按格子编号记的东西已经换了，不再按编号买
                it = shelf.get(idx)
                if it is None or it.get("purchased"):
                    continue
                if it.get("drink_id") and self.INTERVAL_DRINK_STEP and (step or _drink_tier(it["drink_id"]) >= 3):
                    continue
                tried = cache["tried"].get(idx)
                if tried is not None and p < tried:
                    continue  # 点过、P 点已经减少：买到了（货架的 purchased 还没刷新）
                if not _affordable(it, p):
                    continue
                cache["tried"][idx] = p
                ledger["pending"] = {
                    "kind": "drink" if it.get("drink_id") else "card",
                    "id": it.get("drink_id") or (it.get("card_id"), it.get("upgrade") or 0),
                    "p": p,
                }
                return done(
                    Action(ActionKind.INTERVAL_BUY, {"index": idx}), f"买 {it.get('name')}（{it.get('price')}P）{note}"
                )
            return None

        if (d := plan_buy(step_ready)) is not None:
            return d
        d = self._interval_reroll(state, ps, items, cache, res, done)
        if d is not None:
            return d
        if step_ready and cache.get("reroll_failed") and "gains" not in cache:  # noqa: SIM102
            # 逐瓶 / 刷新估值失败：方案里的饮料按方案买，不因为估值失败就一瓶不买（DMM 本战轮间：估值 KeyError，
            # 方案「回体力 + 买 おしゃれハーブティー、ルイボスティー」只回了体力，剩 516P、R2 四次都是零饮料开局）
            if (d := plan_buy(False, "（刷新估值失败，按方案买）")) is not None:
                return d
        up = res.get("upgrade")
        upgrade = items.get("upgrade") or {}
        if up and up not in ledger["upgraded"] and "upgrade" not in cache["tried"] and upgrade.get("available", True) \
                and (upgrade.get("left") is None or upgrade["left"] > 0) and _affordable(upgrade, p):  # fmt: skip
            cache["tried"]["upgrade"] = p
            ledger["pending"] = {"kind": "upgrade", "id": up, "p": p}
            return done(
                Action(ActionKind.INTERVAL_UPGRADE_CARD, {"card_id": up}),
                f"强化 {_card_label({'card_id': up})}（{upgrade.get('price')}P）",
            )
        # 不清空方案 / 账本：执行侧可能误判已离开（第十八局本战 22:33「IntervalEnd 时已不在轮间」其实还在），重进时接着用；下一次轮间按 key 重置
        cache["ended"] = True
        rr = f"；{cache['reroll_note']}" if cache.get("reroll_note") else ""
        return done(Action(ActionKind.INTERVAL_END), f"方案执行完（剩 {p}P，体力 {hp}/{max_hp}）{rr}")

    def _interval_ask(self, state: GameState, cache: dict) -> None:
        """轮间方案拿不准（plan_tiered 的 unsure：模拟方案更好但在噪声内）时，让 LLM 在社区默认 / 模拟方案里二选一。一次轮间问一次；
        选了模拟方案就把 res 的执行字段（回体力目标、买哪些格子、强化）换成它。"""
        res = cache["res"]
        if not res.get("unsure") or not res.get("alt") or not llm_path_on("interval") or self.llm is None:
            return
        alt = res["alt"]
        options = [(Action(ActionKind.INTERVAL_END), {"名字": "社区默认方案", "内容": res["rule_labels"] or ["不买"], "花 P": res["cost"],
                                                       "R2 比不买": f"{res['gain']:+,} ± {res['gain_se']:,}"}),
                   (Action(ActionKind.INTERVAL_END), {"名字": "模拟方案", "内容": alt["labels"] or ["不买"], "花 P": alt["cost"],
                                                       "R2 比不买": f"{alt['gain']:+,} ± {alt['gain_se']:,}"})]  # fmt: skip
        stub = Decision(options[0][0], "社区默认方案", "rule", 0.8, decider="interval")
        d = self._ask_choice("interval", stub, options, "本战轮间（R1 与 R2 之间）：按哪套方案花 P（回体力 / 买饮料 / 买卡 / 强化）", res["unsure"], state,
                             {"社区默认的说明": res.get("rule_notes") or []})  # fmt: skip
        if d is stub:
            return
        cache["llm"], cache["llm_traces"] = replace(d, llm=[]), d.llm
        if d.source == "llm" and d.action is options[1][0]:
            res.update({k: alt[k] for k in ("target_hp", "buy", "upgrade", "labels", "gain", "gain_se", "cost")})

    @staticmethod
    def _keep_last_slot(gains: dict, it: dict, spare: int, price: int) -> bool:
        """饮料栏只剩最后一格时的最优停止（总控 2026-11-03，第二十局轮间：用最后一格买了第 2 瓶 おしゃれハーブティー（R2 +0.8 万），
        随后刷出 ホエイ / 厳選ブレンド 已经买不了，剩 225P）：「再刷一次、新货架上最好那瓶的期望净值 − 刷新的机会成本」高于
        现在买这瓶的净值（增益 − 每 P 机会成本 × 价格）时，留着这格先刷新。"""
        from hiro.sim import interval as iv

        g = gains[it["drink_id"]][0] - iv.SPARE_P_VALUE * (it.get("price") or 0)
        ev = iv.reroll_ev(gains, 1, spare, price, 0.0)["ev"] - iv.SPARE_P_VALUE * price
        return ev > g

    def _interval_held(self, state: GameState, ps: ProduceState) -> list:
        """轮间开始时手里的饮料（不含本次轮间账本里买的）。依据（协调 2026-11-04）：
        1. 轮间画面这一帧读到的饮料栏（field_status = observed / verified）：权威；减掉本次轮间已买的（账本另算）；
        2. 否则用 R1 结束时考试里的实际剩余（interval.CONTEXT["drinks"]，note_exam 在 R1 结束时记下、随 finals_context 落盘）；
        3. 都没有时才用 produce.drinks。绝不用 R1 开局的持有。
        画面 / R1 结束记录 / produce.drinks 不一致时取前者并报警（self._interval_held_warn）。
        第二十五局：produce.drinks 读成空、R1 实剩 ホエイ；第二十九局：produce.drinks 停在 R1 开局的 4 瓶（seq 115，pending），
        R1 实际喝光 → 判成「饮料栏满」，一瓶没买、去强化。"""
        from hiro.sim import interval as iv

        page = None if "drinks" in self.unknown_fields(state, ps) else [d for d in ps.drinks if d]
        src = (state.sources or {}).get("produce.drinks")
        src_seq = src.get("seq") if isinstance(src, dict) else getattr(src, "seq", None)
        # 「本帧读到」：状态 observed / verified 且来源就是这一帧（执行 cdce573 离开考试时把 exam.drinks 写回 produce.drinks、记 observed，
        # 那是 R1 结束时的值，不含本次轮间买的；不能当本帧画面去减账本）
        fresh = (
            (state.field_status or {}).get("produce.drinks") in ("observed", "verified")
            and src_seq is not None
            and src_seq == state.seq
        )
        ctx = iv.CONTEXT.get("drinks")
        ctx = None if ctx is None else [d for d in ctx if d]
        self._interval_held_warn = None
        if fresh and page is not None:
            held = list(page)
            for d in (getattr(self, "_interval_ledger", None) or {}).get("drinks") or []:
                if d in held:
                    held.remove(d)  # 画面上已经包括本次轮间买的，账本那边另算
            if ctx is not None and sorted(held) != sorted(ctx):
                self._interval_held_warn = f"轮间持有：画面 {held} 和 R1 结束剩余 {ctx} 不一致，按画面"
            return held
        if ctx is not None:
            if page and sorted(page) != sorted(ctx):
                self._interval_held_warn = (
                    f"轮间持有：produce.drinks {page}（未在本帧读到）和 R1 结束剩余 {ctx} 不一致，按 R1 结束剩余"
                )
            return ctx
        return page or []

    @staticmethod
    def _gains_setup(ps: ProduceState, holding: list, res: dict):
        """轮间刷新估值的增益表开局：R1 牌组 + 持有 + 本战倍率（hiro.sim.interval.CONTEXT）。CONTEXT 不全时 None。"""
        from hiro.sim import interval as iv

        deck = [tuple(x) for x in iv.CONTEXT.get("deck") or []]
        permil = {a: v for a, v in (iv.CONTEXT.get("permil") or {}).items() if v}
        if not deck or not permil:
            return None
        for a in ("Vo", "Da", "Vi"):
            permil.setdefault(a, min(permil.values()))
        return iv.R2Setup(
            deck=deck,
            drinks=list(holding),
            hp=max(ps.stamina, res.get("target_hp") or ps.stamina),
            max_hp=ps.max_stamina,
            permil=permil,
        )

    def _interval_prefetch_gains(self, state: GameState, ps: ProduceState, items: dict, res: dict) -> None:
        """方案一算出来，就按「现在持有 + 方案里要买的饮料」在后台算刷新估值要用的增益表（执行回体力 / 买东西的这段时间里算）。"""
        from hiro.sim import interval as iv

        try:
            held = self._interval_held(state, ps)
            shelf = {it["index"]: it for it in items.get("drinks") or []}
            planned = [shelf[i]["drink_id"] for i in res.get("buy") or [] if i in shelf and shelf[i].get("drink_id")]
            setup = self._gains_setup(ps, held + list(self._interval_ledger["drinks"]) + planned, res)
            if (
                setup is not None
                and ps.drink_capacity > len(setup.drinks)
                and (items.get("refresh") or {}).get("available") is not False
            ):
                iv.start_gains(setup)
        except Exception as e:  # noqa: BLE001  预取失败不影响决策
            logger.debug("增益表预取失败：%s", e)

    def _interval_ledger_for(self, ps: ProduceState, p: int) -> dict:
        """本次轮间账本：成交的饮料 / 卡 / 强化、刷新次数。发出动作时记 pending，下一次看到 P 减少才算成交（失败的不记）。
        按 (mode, step) 认同一次轮间；重进（执行侧误判已离开）时接着用。"""
        key = (ps.mode, ps.step)
        led = getattr(self, "_interval_ledger", None)
        if led is None:
            # 进程重启（第二十二局通信エラー 后 -r2 整套重算 71s + 78s）：读回落盘的账本和方案（同一次轮间才用）
            from hiro.sim import interval as iv

            saved = iv.load_interval_cache()
            if saved and (saved.get("ledger") or {}).get("key") == key:
                led = self._interval_ledger = saved["ledger"]
                if (saved.get("plan") or {}).get("visit") == key:
                    self._interval_plan = saved["plan"]
                logger.info("轮间：读回落盘的账本 / 方案（%s）", key)
        if led is None or led["key"] != key:
            led = self._interval_ledger = {
                "key": key,
                "drinks": [],
                "cards": [],
                "upgraded": [],
                "rerolls": 0,
                "pending": None,
            }
        pend = led.pop("pending", None)
        led["pending"] = None
        if pend is not None and p < pend["p"]:
            {"drink": led["drinks"], "card": led["cards"], "upgrade": led["upgraded"]}[pend["kind"]].append(pend["id"])
        return led

    INTERVAL_REROLL = True  # 测试 / 兜底开关

    def _interval_fill_card(
        self, items: dict, ledger: dict, cache: dict, nrr: int, spare: int, p: int, done
    ) -> Decision | None:
        """刷新后的货架上补卡（牌组不满 22 张时）。HIRO_INTERVAL_FILL_22（缺省开，用户 11-18「本战轮间还是要补充到 22 张卡」）：
        按 interval.fill_order（一次性 / 循环件 / 社区评级 > 最不污染的可复用卡，冲突卡不拿）；关掉时照旧只买核心卡。"""
        from hiro.sim import interval as iv

        deck = [tuple(c) for c in iv.CONTEXT.get("deck") or []] + [tuple(c) for c in ledger["cards"]]
        if len(deck) >= iv.OUENBOU_SIZE:
            return None
        avail = [it for it in items.get("cards") or [] if it.get("card_id") and not it.get("purchased")
                 and (it.get("price") or 0) <= spare and cache["tried"].get(("rc", nrr, it["index"])) is None]  # fmt: skip
        if iv.INTERVAL_FILL_22:
            order = iv.fill_order([it["card_id"] for it in avail], deck)
            it = next((x for cid, _ in order for x in avail if x["card_id"] == cid), None)
            what = "补到 22 张"
        else:
            owned = {c[0] for c in deck}
            cards = [it for it in avail if not unwanted(it["card_id"]) and _is_core_card(it["card_id"])
                     and wants_copy(it["card_id"], sum(1 for c in deck if c[0] == it["card_id"]))]  # fmt: skip
            it = max(cards, key=lambda x: iv._card_rule_key(x["card_id"], owned)) if cards else None
            what = "核心卡"
        if it is None:
            return None
        cache["tried"][("rc", nrr, it["index"])] = p
        ledger["pending"] = {"kind": "card", "id": (it["card_id"], it.get("upgrade") or 0), "p": p}
        return done(
            Action(ActionKind.INTERVAL_BUY, {"index": it["index"]}),
            f"刷新后的货架：{what}，买 {it.get('name')}（{it.get('price')}P；牌组 {len(deck)} 张）",
        )

    def _interval_reroll(
        self, state: GameState, ps: ProduceState, items: dict, cache: dict, res: dict, done
    ) -> Decision | None:
        """轮间刷新货架（用户 2026-11-02：第十六局 R2 只有 漢方、第十七局只有 ハーブティー，剩 435P）。计划里的回体力 / 买卡 / 买饮料做完之后、强化之前：
        1. 刷新过的新货架：先买值得买的饮料（R2 增益 > 1.5 个标准误，空位 / 可花 P 内增益和最大），牌组不满 22 张时按补卡顺序买卡；
        2. 再估一次刷新：价值 = 新货架期望最优买法 − 当前货架最优买法（hiro.sim.interval.reroll_ev，商品池按实机观测等概率），
           > 0 就刷（不为强化预留 P，用户 2026-11-03）；否则交回（强化用剩下的钱 → 结束）。
        社区默认（playbook：R2 两瓶爆发 + 两瓶抽牌；刷新止损——先给体力和可见替代饮料留钱，不为单瓶目标无限刷新）作先验：
        持有没达到 R2 目标才刷；估值和默认不一致时写进记录（community_deviation）。动作：Reroll {"scope": "interval"}（和执行 23e6965 对齐中）。"""
        from hiro.brain import drinks as dk
        from hiro.sim import interval as iv

        refresh = items.get("refresh") or {}
        if (
            not self.INTERVAL_REROLL
            or refresh.get("available") is False
            or refresh.get("price") is None
            or cache.get("reroll_failed")
        ):
            return None
        p = ps.produce_point
        price = int(refresh["price"])
        # 不为强化预留 P（用户 2026-11-03：「100P 拿去强化 せのび 没必要啊，强化的收益不如买饮料」）：饮料在强化前面，强化只用剩下的钱
        need = 0
        if iv.INTERVAL_REFRESH_YIELD:
            # ③ 刷新给补卡、回体力让 P（协调 11-18，缺省关）：还差的张数 × 货架上最便宜的补卡价 + 回到约 29 的价
            deck = [tuple(c) for c in iv.CONTEXT.get("deck") or []] + [tuple(c) for c in self._interval_ledger["cards"]]
            short = max(0, iv.OUENBOU_SIZE - len(deck))
            fill = [it.get("price") or 0 for it in items.get("cards") or [] if it.get("card_id") and not it.get("purchased")
                    and iv.fill_order([it["card_id"]], deck)]  # fmt: skip
            per2 = (items.get("recover") or {}).get("price_per_2hp") or 10
            need = short * (min(fill) if fill else 0) + iv.recover_cost(
                ps.stamina, max(ps.stamina, iv.recover_target(ps.max_stamina)), per2
            )
        spare = p - need
        held = self._interval_held(state, ps)
        ledger = self._interval_ledger
        # 持有 = R1 剩下的（读得到时）+ 本次轮间账本里成交的（第十八局本战：按货架 id 反推，买掉的格读成 None → 「持有 空」，拿垃圾饮料填空位）
        holding = held + list(ledger["drinks"])
        free = ps.drink_capacity - len(holding)
        if any(not it.get("drink_id") and not it.get("purchased") for it in items.get("drinks") or []) and cache.get(
            "rerolling"
        ):
            # 刷新后的新货架还没认出来：等识别，不拿空货架去估（第十八局：两次刷新估值一模一样）
            cache["unread_waits"] = cache.get("unread_waits", 0) + 1
            if cache["unread_waits"] <= 3:
                return Decision(
                    Action.ui(ActionKind.WAIT, why="刷新后货架未识别"),
                    "刷新后的货架还没认出：重新观测",
                    "rule",
                    0.6,
                    decider="interval",
                )
        cache["unread_waits"] = 0
        if "gains" not in cache:
            setup = self._gains_setup(ps, holding, res)
            if setup is None:
                return None
            try:
                # 方案算完时已按「持有 + 方案里的饮料」在后台开算（_interval_prefetch_gains）；持有一致就直接用 / 等它算完
                cache["gains"] = iv.take_gains(setup) or iv.drink_gains(setup)
                iv.save_interval_cache({"plan": cache, "ledger": self._interval_ledger})
            except Exception as e:
                logger.warning("轮间刷新估值失败，不刷：%r", e, exc_info=True)
                cache["reroll_failed"] = True
                return None
        gains = cache["gains"]
        shelf = [(it["index"], it["drink_id"], it.get("price") or iv.drink_price(it["drink_id"])) for it in items.get("drinks") or []
                 if it.get("drink_id") and not it.get("purchased")]  # fmt: skip
        last = cache.pop("reroll_pending", None)
        if last is not None and p >= last["p"] and sorted(shelf) == last["shelf"]:
            # 刷新没成（P 没扣、货架没变；执行侧遇到ジュエル确认框 / 失败会取消）：这次轮间不再刷
            cache["reroll_failed"] = True
            cache["reroll_note"] = "刷新没做成（P 和货架都没变），本次轮间不再刷"
            return None
        nrr = cache.get("rerolls", 0)
        if self.INTERVAL_DRINK_STEP:
            return self._interval_drink_step(
                ps, items, cache, res, done, gains, shelf, holding, free, spare, price, nrr
            )
        if cache.get("rerolling"):
            # 1. 新货架：值得买的饮料
            _v0, picks = iv.shelf_value(shelf, gains, free, spare)
            for idx in picks:
                # 键带上刷新次数：刷新后同一格编号是另一瓶（第二十五局：第 1 次刷新后在 6 号格买了 ブレンド，第 2 次刷新后 6 号格的 ホエイ 被当成已买）
                if cache["tried"].get(("rr", nrr, idx)) is not None and p < cache["tried"][("rr", nrr, idx)]:
                    continue
                it = next(x for x in items["drinks"] if x.get("index") == idx)
                if free == 1 and self._keep_last_slot(gains, it, spare, price):
                    # 最后一格不买弱饮料：再刷一次期望更高，交给下面的刷新估值（第二十局：第 2 瓶 ハーブティー 占满栏位，随后刷出 ホエイ / ブレンド 买不了）
                    cache["kept_slot"] = it.get("name")
                    break
                cache["tried"][("rr", nrr, idx)] = p
                ledger["pending"] = {"kind": "drink", "id": it["drink_id"], "p": p}
                g, se = gains[it["drink_id"]]
                return done(Action(ActionKind.INTERVAL_BUY, {"index": idx}),
                            f"刷新后的货架：买 {it.get('name')}（{it.get('price')}P，R2 增益 {g:+,} ± {se:,}，> 价格 × 每 P {iv.SPARE_P_VALUE} 分）")  # fmt: skip
            # 牌组不满 22 张：按补卡顺序买卡（cards 也随刷新换了）
            if (d := self._interval_fill_card(items, ledger, cache, nrr, spare, p, done)) is not None:
                return d
        cur_v, _ = iv.shelf_value(shelf, gains, free, spare)
        r = iv.reroll_ev(gains, free, spare, price, cur_v)
        r["ev"] -= iv.SPARE_P_VALUE * price  # 刷新价格（实际价格：10 → 20 → 30…）按余 P 的机会成本计
        target_met = not [x for x in ("复制", "爆发", "抽牌") if x in _r2_missing(holding)]
        default = (not target_met) and spare >= price  # 社区默认：没达到 R2 目标、P 够才刷
        want = r["ev"] > 0
        n = cache.get("rerolls", 0)
        note = (f"刷新估值：新货架期望最优买法比现在 {r['ev']:+,.0f}（± {r['se']:,.0f}，至少一瓶值得买的概率 {r['p_good']:.0%}），"
                f"刷新 {price}P，可花 {spare}P（不为强化预留），已刷 {n} 次；持有 {dk.describe(holding, 'r2')}")  # fmt: skip
        if cache.pop("kept_slot", None):
            note += "；最后一格留着：刷新的期望比买货架上这瓶弱饮料高"
        cache["reroll_note"] = note
        if not want:
            if default:
                cache["reroll_note"] += "；社区默认会继续刷（R2 目标没达到），估值说不划算：不刷"
            return None
        cache["rerolling"] = True
        cache["rerolls"] = n + 1
        cache["reroll_pending"] = {"p": p, "shelf": sorted(shelf)}
        ledger["rerolls"] = ledger.get("rerolls", 0) + 1
        d = done(
            Action(ActionKind.REROLL, {"scope": "interval"}),
            f"刷新货架（第 {n + 1} 次）：{note}" + ("" if default else "；R2 目标已达到，按估值仍刷（偏离社区默认）"),
        )
        if not default:
            d.warnings = d.warnings + [f"community_deviation：轮间刷新（R2 目标已达到仍刷，期望 {r['ev']:+,.0f}）"]
        return d

    INTERVAL_DRINK_STEP = True  # 买饮料 / 刷新按栏位机会成本 + 社区先验（第二十五局，用户 2026-11-04「瞎买饮料」；False 回到原来的「值得买就买 + 刷新估值」）

    def _interval_drink_step(
        self, ps, items, cache, res, done, gains, shelf, holding, free, spare, price, nrr
    ) -> Decision | None:
        """当前货架（刷新前后都算）上下一步：买 / 刷新 / 停（hiro.sim.interval.drink_step）。停了才轮到强化（用户 2026-11-03：强化的收益不如买饮料；
        第二十五局：饮料栏空 2 格、P 215 时去强化了 ワクワク）。方案里按格子编号买的（res["buy"]）已经在前面执行过。"""
        from hiro.brain import drinks as dk
        from hiro.sim import interval as iv

        p = ps.produce_point
        ledger = self._interval_ledger

        def bought(idx) -> bool:  # 点过买、P 已经减少（货架的 purchased 还没刷新）；方案里按编号买的只在没刷新过时算
            keys = [("rr", nrr, idx)] + ([idx] if not cache.get("rerolling") else [])
            return any(cache["tried"].get(k) is not None and p < cache["tried"][k] for k in keys)

        avail = [x for x in shelf if not bought(x[0])]
        st = iv.drink_step(avail, gains, holding, free, spare, price, rerolls=nrr)
        warn = [f"community_deviation：轮间饮料 {st['deviation']}"] if st.get("deviation") else []
        tail = f"；持有 {dk.describe(holding, 'r2')}" + (
            f"；不买：{'、'.join(st['skipped'])}" if st.get("skipped") else ""
        )
        if st["kind"] == "buy":
            idx = st["index"]
            it = next(x for x in items["drinks"] if x.get("index") == idx)
            cache["tried"][("rr", nrr, idx)] = p
            ledger["pending"] = {"kind": "drink", "id": it["drink_id"], "p": p}
            d = done(
                Action(ActionKind.INTERVAL_BUY, {"index": idx}),
                f"{it.get('name')}（{it.get('price')}P）：{st['why']}{tail}",
            )
            d.warnings = d.warnings + warn
            return d
        if cache.get("rerolling"):  # noqa: SIM102
            # 牌组不满 22 张：按补卡顺序买卡（cards 也随刷新换了）
            if (d := self._interval_fill_card(items, ledger, cache, nrr, spare, p, done)) is not None:
                return d
        cache["reroll_note"] = f"饮料：{st['why']}{tail}"
        if st["kind"] == "stop":
            return None
        cache["rerolling"] = True
        cache["rerolls"] = nrr + 1
        cache["reroll_pending"] = {"p": p, "shelf": sorted(shelf)}
        ledger["rerolls"] = ledger.get("rerolls", 0) + 1
        d = done(Action(ActionKind.REROLL, {"scope": "interval"}), f"刷新货架（第 {nrr + 1} 次）：{st['why']}{tail}")
        d.warnings = d.warnings + warn
        return d

    def _interval_rule(self, state: GameState, ps: ProduceState, unknown: set, items: dict) -> Decision:
        """兜底（模拟器算不了时）。用户 2026-11-01：体力不回满——広 有回血，エウレカ 要 HP ≤ 50% 才触发，回到角色的体力目标
        （広：刚过半血）就够；不为星星买废卡（ワッペン 每张 +15 星，约 R2 +1.5%，抵不过废卡稀释）。
        社区 [もなみ][game8][seesaa]：R1 消耗的体力只能在这里回复；饮料按优先级补；やる気 不必勉强补到 22 张。
        做法：先预留回到目标体力的 P 点，预留之外按 饮料 > 必拿/S/A 卡 > 强化核心卡；最后结束。"""
        from hiro.brain import hif_rules as cr
        from hiro.sim import interval as _iv
        from hiro.state.knowledge import interval_hp_target

        p, hp, max_hp = ps.produce_point, ps.stamina, ps.max_stamina
        target = interval_hp_target(self.profile.idol_id if self.profile else None, max_hp)
        if _sim_ready():  # HP_TARGET_VALUE（默认关）：回体力目标按价值表每 P 回报逐段往上（主仓库 1b16f1b）
            target = _iv.value_hp_target(max_hp, target)
        if _iv.INTERVAL_RECOVER_FIRST and hp < _iv.recover_target(max_hp):
            target = max(target, _iv.recover_target(max_hp))  # ② 回到约 29（协调 11-18，缺省关）
        shelf = [dict(it, kind=kind) for kind in ("card", "drink") for it in items.get(kind + "s") or []]
        recover = items.get("recover") or {}
        upgrade = items.get("upgrade") or {}
        cands = _shop_candidates(shelf, {"recover": recover, "upgrade": upgrade}, p, ActionKind.INTERVAL_BUY)
        per2 = recover.get("price_per_2hp") or 10  # 货架给的价格优先，缺时按社区规则（每 2 体力 10P）
        need = (
            (
                per2 * math.ceil((target - hp) / 2)
                if recover.get("price_per_2hp")
                else cr.interval_recover_price(target - hp)
            )
            if hp < target
            else 0
        )
        budget = p - need
        owned = {c.card_id for c in ps.deck}

        def done(action: Action, reason: str) -> Decision:
            return Decision(action, reason, "rule", 0.8, cands, decider="interval")

        recover_ok = hp < target and recover.get("available", True) and p >= per2
        cheapest = min(
            (it["price"] for it in shelf if not it.get("purchased") and it.get("price") is not None), default=None
        )
        if recover_ok and (cheapest is None or budget < cheapest):
            return done(Action(ActionKind.INTERVAL_BUY, {"service": "recover"}),
                        f"体力 {hp}/{max_hp}，回到目标 {target} 要 {need}P、现有 {p}P：P 点不够兼顾时先回体力（R1 消耗只能在轮间回复）")  # fmt: skip
        from hiro.brain import drinks as dk

        held = [] if "drinks" in unknown else list(ps.drinks)
        free = None if "drinks" in unknown else ps.drink_capacity - len(ps.drinks)
        if free is None or free > 0:
            cur = dk.holding_key(held, "r2", ps.drink_capacity)
            better = [(dk.holding_key(held + [it["drink_id"]], "r2", ps.drink_capacity), it) for it in _items(shelf, "drink")
                      if it.get("drink_id") and _affordable(it, budget)]  # fmt: skip
            better = [x for x in better if x[0] < cur]
            if why := self._interval_refresh_why(items, shelf, budget):
                return done(Action(ActionKind.REROLL, {"scope": "interval"}), why)
            if better:
                it = min(better, key=lambda x: x[0])[1]
                return done(Action(ActionKind.INTERVAL_BUY, {"index": it["index"]}),
                            f"买 R2 饮料（{it['price']}P，留足回体力的 {need}P）：买完持有 {dk.describe(held + [it['drink_id']], 'r2')}（社区默认：两瓶爆发 + 两瓶抽牌）")  # fmt: skip
        # 兜底只买「缺的核心卡」（必拿、且牌组已知确实没有）：用户 2026-11-01 第九局复盘——兜底只做体力补到目标 + 饮料，
        # 买卡必须是模拟证明 R2 有提升的卡或缺的核心卡；轮间牌组未知时（owned 为空）一张也不买（第九局买了 アイドル宣言）
        cards = sorted(_items(shelf, "card"), key=lambda it: -_card_priority(it.get("card_id")))
        deck_known = bool(ps.deck) and "deck" not in unknown
        if not deck_known:  # 轮间页没有牌组：用本战 R1 记下的牌组（hiro.state.finals_context.CONTEXT）判断缺不缺
            from hiro.state.finals_context import CONTEXT

            if CONTEXT.get("deck"):
                owned, deck_known = {c[0] for c in CONTEXT["deck"]}, True
        for it in cards:
            know = YARUKI_CARDS.get(it.get("card_id"))
            if know and know.get("must") and deck_known and it["card_id"] not in owned and _affordable(it, budget):
                return done(
                    Action(ActionKind.INTERVAL_BUY, {"index": it["index"]}),
                    f"买缺的核心卡 {know['name']}（{it['price']}P）",
                )
        if _iv.INTERVAL_FILL_22 and deck_known:
            # ① 补到 22 张（用户 11-18）：牌组用状态机记账，没有时用 R1 记下的
            deck = (
                [(c.card_id, c.upgrade) for c in ps.deck]
                if ps.deck and "deck" not in unknown
                else [tuple(c) for c in _iv.CONTEXT.get("deck") or []]
            )
            if len(deck) < _iv.OUENBOU_SIZE:
                avail = [it for it in cards if it.get("card_id") and _affordable(it, budget)]
                order = _iv.fill_order([it["card_id"] for it in avail], deck)
                it = next((x for cid, _ in order for x in avail if x["card_id"] == cid), None)
                if it is not None:
                    return done(
                        Action(ActionKind.INTERVAL_BUY, {"index": it["index"]}),
                        f"补到 22 张：买 {_card_label(it)}（{it['price']}P；牌组 {len(deck)} 张，不拿冲突卡）",
                    )
        if ps.deck and "deck" not in unknown and upgrade.get("available", True) and _affordable(upgrade, budget):
            up = _upgrade_target(
                ps
            )  # 别复用 target：后面的「回体力到目标」要用体力目标（原来写成 target 被覆盖成 None）
            if up is not None:
                return done(
                    Action(ActionKind.INTERVAL_UPGRADE_CARD, {"card_index": up[0]}),
                    f"强化核心卡 {up[1]}（{upgrade['price']}P）",
                )
        if recover_ok:
            return done(
                Action(ActionKind.INTERVAL_BUY, {"service": "recover"}), f"回体力到目标 {target}（{hp}/{max_hp}）"
            )
        return done(Action(ActionKind.INTERVAL_END), f"没有要买的（剩 {p}P，体力 {hp}/{max_hp}，目标 {target}）")

    def _interval_refresh_why(self, items: dict, shelf: list, budget: int) -> str | None:
        """轮间货架没有 R2 优先档饮料（复制 / 爆发）时先刷新再买。
        Windows 第七局：货架只有 ホエイ + 青汁，买完剩 380P 没花；A5b 买到了复制饮料 特製ハツボシエキス。
        只在还没买饮料时刷（买过以后持有数未知，再买可能溢出）；两格饮料都认出才判断；刷完货架没变（没刷成）、
        刷满 INTERVAL_REFRESH_MAX 次、或刷完不够买两瓶 100P 饮料就不刷。返回刷新理由，不刷返回 None。"""
        from hiro.brain import drinks as dk

        drinks = _items(shelf, "drink")
        refresh = items.get("refresh") or {}
        price = refresh.get("price")
        if (
            not drinks
            or any(it.get("purchased") for it in (items.get("drinks") or []))
            or any(not it.get("drink_id") for it in drinks)
        ):
            return None
        if refresh.get("available") is False or price is None or budget - price < INTERVAL_DRINK_RESERVE:
            return None
        if any(dk.tag([it["drink_id"]], "r2")[0][0] == 0 for it in drinks):
            return None  # 已有复制 / 爆发
        sig = tuple(sorted((it.get("index"), it.get("card_id") or it.get("drink_id")) for it in shelf))
        tried = getattr(self, "_interval_rerolls", None)
        if tried is None or tried["budget_hint"] < budget:  # P 变多 = 新的一次轮间（每局只有一次，防跨局残留）
            tried = self._interval_rerolls = {"sigs": [], "budget_hint": budget}
        tried["budget_hint"] = budget
        if (tried["sigs"] and tried["sigs"][-1] == sig) or len(tried["sigs"]) >= INTERVAL_REFRESH_MAX:
            return None
        tried["sigs"].append(sig)
        names = "、".join(it.get("name") or it["drink_id"] for it in drinks)
        return (
            f"【刷新】轮间货架饮料（{names}）没有复制 / 爆发（R2 优先档），刷新找一次（{price}P，第 {len(tried['sigs'])} 次，"
            f"最多 {INTERVAL_REFRESH_MAX} 次；刷完还剩 {budget - price}P）"
        )

    def _interval_state(self, state: GameState) -> tuple[ProduceState | None, set[str]]:
        """轮间用的 ProduceState：体力 / 最大体力 / P 点取本页页头读数（识别-06 / ef5d764 三读投票），其余字段由 ProduceState 补。
        ProduceState 里的体力、P 点、饮料是 R1 之前的旧值（第五局实拍帧离线跑：produce_state() 不全时直接 IntervalEnd）；
        页头读不到时返回 None，不退回旧值。缺的必填字段用占位值补，并记入 unknown。"""
        from dataclasses import MISSING, fields

        pf = state.page_fields
        hp, p = pf.get("stamina"), pf.get("produce_point")
        produce = dict(state.produce or {})
        max_hp = pf.get("max_stamina") or produce.get("max_stamina")
        if hp is None or p is None or not max_hp:
            return None, set()
        observed = {k for k, v in produce.items() if v is not None}
        produce.update(stamina=hp, max_stamina=max_hp, produce_point=p)
        placeholder = {"idol_card_id": self.profile.idol_id if self.profile else "", "mode": "produce-008", "step": 8,
                       "params": {}, "growth_permil": {}, "star": 0}  # fmt: skip
        for f in fields(ProduceState):
            if (f.name == "star" or (f.default is MISSING and f.default_factory is MISSING)) and produce.get(
                f.name
            ) is None:
                produce[f.name] = placeholder.get(
                    f.name
                )  # star 有默认值（初 / N.I.A 没有星星），但 H.I.F 构造时必须有：照旧补占位
        if self.main_attr and not produce.get("main_attr"):
            produce["main_attr"] = self.main_attr
        ps = replace(state, produce=produce).produce_state()
        unknown = {f.name for f in fields(ProduceState)} - observed - {"stamina", "max_stamina", "produce_point"}
        unknown.add("drinks")  # R1 里喝掉的饮料没有回写，持有数未知（满了由 PDRINK_MAX 取舍）
        return ps, unknown

    def _interval_without_shelf(self, state: GameState) -> Decision:
        """货架没识别（识别-06：页头只读到 stamina / max_stamina / produce_point）：体力没满、P 点够就先回体力，回满或 P 点用完再结束。
        社区 [もなみ][game8][seesaa]：R1 消耗的体力只能在轮间回复（第 4 局 380P 一分没花就结束，R2 只得 +62,069）。
        只用本页读到的体力与 P 点：ProduceState 里的是 R1 之前的旧值。最大体力读不到时用 ProduceState / 角色常量。"""
        from hiro.brain import hif_rules as cr

        pf = state.page_fields
        hp, p = pf.get("stamina"), pf.get("produce_point")
        max_hp = pf.get("max_stamina") or (state.produce or {}).get("max_stamina")
        skipped = "货架未识别：没有补卡 / 买饮料 / 用ワッペン（社区：轮间卡便宜，用来用完ワッペン、补卡）"
        if hp is None or p is None or not max_hp:
            return Decision(Action(ActionKind.INTERVAL_END), f"轮间货架未识别，体力 / P 点也没读到（体力 {hp}，P {p}），直接结束", "rule", 0.2,
                            warnings=[skipped, "没有回复体力（社区：R1 消耗的体力只能在轮间回复）"], decider="interval")  # fmt: skip
        if hp < max_hp and p >= cr.interval_recover_price(1):
            need = cr.interval_recover_price(max_hp - hp)
            return Decision(Action(ActionKind.INTERVAL_BUY, {"service": "recover"}),
                            f"货架未识别，先回体力：{hp}/{max_hp}，回满要 {need}P、现有 {p}P（R1 消耗的体力只能在轮间回复，R2 开局不会自动回）",
                            "rule", 0.7, warnings=[skipped], decider="interval")  # fmt: skip
        why = "体力已满" if hp >= max_hp else f"P 点 {p} 不够回复（每 2 体力 10P）"
        return Decision(Action(ActionKind.INTERVAL_END), f"货架未识别；{why}（体力 {hp}/{max_hp}，P {p}），结束轮间", "rule", 0.6,
                        warnings=[skipped], decider="interval")  # fmt: skip

    # ---------------------------------------------------------------- 公共
    def _ask(
        self, prompts: list[str], user: dict, *, extra_system: str = "", extra_version: str = ""
    ) -> tuple[dict | None, list[dict]]:
        version = prompt_version(*prompts) + (f"+{extra_version}" if extra_version else "")
        system = load_prompt(*prompts) + (f"\n\n{extra_system}" if extra_system else "")
        res = self.llm.ask_json(system, json.dumps(user, ensure_ascii=False))
        return res.data, [res.record(prompt_version=version)]

    def _ask_choice(
        self,
        path: str,
        rule: Decision,
        options: list[tuple[Action, dict]],
        question: str,
        why: str,
        state: GameState,
        extra: dict | None = None,
    ) -> Decision:
        """规则拿不准（why）时让 LLM 在 options 里选一个（options：[(动作, 给 LLM 看的说明)]，第一个是规则的选择）。
        开关没开 / 没有 LLM / 只有一个选项：原样返回规则的决定。LLM 输出非法：按规则的选择兜底（source=fallback），带 LLM 记录和提醒。"""
        if not llm_path_on(path) or self.llm is None or len(options) < 2:
            return rule
        ps = self.produce_state(state)
        unknown = set(state.unobserved_produce_fields()) if state.produce else set()
        from hiro.state.context import situation

        user = {"局面": situation(ps, unknown) if ps is not None and state.produce else "识别未提供完整局面",
                "问题": question, "规则为什么拿不准": why,
                "候选": [dict(序号=i, **info) for i, (_, info) in enumerate(options)],
                "规则的选择": 0} | (extra or {})  # fmt: skip
        mode = ps.mode if ps is not None else (state.produce or {}).get("mode")
        if HAJIME_NIA_STATE and _is_hajime_nia(mode):
            # 初 / N.I.A：不带 H.I.F 的培育说明和广的 H.I.F 打法（选拔 / 本战、星星），打法用这个模式的社区规则清单
            from hiro.brain import hajime_nia_rules as hr
            from hiro.state.modes import family as _family
            from hiro.state.modes import mode as _mode

            try:
                diff = _mode(mode).difficulty
            except KeyError:
                diff = None
            idol = (
                ps.idol_card_id
                if ps is not None
                else ((state.produce or {}).get("idol_card_id") or (self.profile.idol_id if self.profile else None))
            )
            data, calls = self._ask(["hajime_nia_choice.md"], user, extra_system=_idol_section(idol, self.profile, mode) + "\n\n" + hr.guide(_family(mode), diff),
                                    extra_version=f"hajime_nia_rules@{hr.load().get('captured_at', '?')}")  # fmt: skip
        else:
            data, calls = self._ask(["produce_choice.md", "hif_produce.md", *self.playbook], user)
        k = _int(data, "choice")
        traces = [llm_trace_from_call(c) for c in calls]
        if k is None or not 0 <= k < len(options):
            bad = data if data is not None else (calls[-1].get("error") if calls else None)
            return replace(
                rule,
                source="fallback",
                confidence=0.3,
                llm=traces,
                warnings=[*rule.warnings, f"规则拿不准（{why}）；LLM 输出无效（{bad}），按规则的选择兜底"],
            )
        return replace(rule, action=options[k][0], reason=str((data or {}).get("reason", ""))[:300], source="llm", confidence=None,
                       line=str((data or {}).get("line", "")), llm=traces,
                       warnings=[*rule.warnings, f"规则拿不准（{why}），交 LLM：选 {options[k][1].get('名字', k)}"
                                 + ("（和规则相同）" if k == 0 else f"（规则原本选 {options[0][1].get('名字', 0)}）")])  # fmt: skip

    @staticmethod
    def _fallback(
        action: Action,
        reason: str,
        cands: list[Candidate],
        decider: str,
        calls: list[dict],
        *,
        source: str = "fallback",
    ) -> Decision:
        return Decision(action, reason, source, 0.3 if source == "fallback" else 0.8, cands,
                        llm=[llm_trace_from_call(c) for c in calls], decider=decider)  # fmt: skip


def _resolve_held(held: list, state_drinks: list | None) -> tuple[list, list]:
    """D41（第三十三局 18:18）：饮料溢出页「手持ちのドリンク」最后一行常被视口切掉，识别标 partial、按露出的效果说明给 candidates。
    1）没认出的行先用 candidates ∩ produce.drinks[index] 认出（held 行序 = 底栏顺序）；
    2）还认不出的行标 visible=False：不作为丢弃对象（原来按「未知 = 最差」丢它，执行找不到这一行，KAA 默认「残す」丢了新来的 特製）。"""
    out, warn = [], []
    # 状态机持有里还没被画面认出的瓶（多重集）：同一溢出页连着换过一瓶时 produce.drinks 的行序可能还是旧的（第三十三局 #111）
    rest = list(state_drinks or [])
    for h in held:
        if h.get("drink_id") in rest:
            rest.remove(h["drink_id"])
    for h in held:
        h = dict(h)
        if not h.get("drink_id"):
            i = h.get("index")
            st = state_drinks[i] if state_drinks and isinstance(i, int) and 0 <= i < len(state_drinks) else None
            cands = h.get("candidates") or []
            alt = sorted({d for d in rest if d in cands})
            if st and st in cands:
                h.update(drink_id=st, method="state_match")
                warn.append(f"第 {i + 1} 瓶没认全：按候选 ∩ 状态机持有（同一行）认成 {st}")
            elif len(alt) == 1:
                h.update(drink_id=alt[0], method="state_match_rest")
                warn.append(
                    f"第 {(i if isinstance(i, int) else -1) + 1} 瓶没认全：按候选 ∩ 状态机持有里还没对上的瓶认成 {alt[0]}"
                )
            else:
                h["visible"] = False
                warn.append(
                    f"第 {(i if isinstance(i, int) else -1) + 1} 瓶没认出（候选 {cands or '无'}）：不作为丢弃对象"
                )
        out.append(h)
    return out, warn


SLEEP_ID = "p_card-00-acc-0_002"  # 眠気
# D40（协调 2026-11-04，第三十三局选拔 #339）：选卡页没有「不拿」按钮。LLM 说不想拿（skip / index -1）或输出无效、且必拿已齐、候选都是社区没提到的卡时，
# 按瘦牌组规则（能刷新先刷新，否则拿最不污染的：一次性 > 不生成眠気 > 不冲突），不取游戏推荐。行为变化，默认关
SELECT_NONE_LEAN = True  # 开源版默认开（主仓库 D40）
# 初 / N.I.A 选卡走 hajime_nia 规则（_select_card_hajime_nia）：协调 11-17 派的「N.I.A 必拿卡表」；只影响初 / N.I.A，H.I.F 照旧。
# HIRO_HAJIME_NIA_CARD_PICK=0 退回 H.I.F 的选卡逻辑
HAJIME_NIA_CARD_PICK = os.environ.get("HIRO_HAJIME_NIA_CARD_PICK", "1") != "0"
# 初 / N.I.A 的局面：没有星星（star 一直 None）、step 没读到时也构造 ProduceState（协调 11-17 派）；相談只删卡、饮料不跑 H.I.F 模拟、
# LLM 用初 / N.I.A 自己的提示词和局面文本（不带 H.I.F、星星）。
# HIRO_HAJIME_NIA_STATE=0 退回原来（局面构造不出、这几个决策器走兜底）
HAJIME_NIA_STATE = os.environ.get("HIRO_HAJIME_NIA_STATE", "1") != "0"


def _is_hajime_nia(mode: str | None) -> bool:
    from hiro.state.modes import HAJIME, NIA, family

    return family(mode) in (HAJIME, NIA)


SELECTION_LEAN_PICK = False  # 选拔里必拿已齐、候选无核心卡时按瘦牌组规则选（不调 LLM；协调 2026-11-03 提速）。行为变化，默认关，等用户答复（总控 2026-11-03）


def _sim_ready() -> bool:
    """模拟器可用：引擎能导入、主数据（catalog.sqlite）已构建。HIRO_SIM=0 强制不用（只按社区规则）。"""
    import os

    if os.environ.get("HIRO_SIM") == "0":
        return False
    try:
        import hiro.sim

        return hiro.sim.master_ready()
    except Exception:  # noqa: BLE001  引擎缺失 / 导入失败
        return False


# 轮间货架没有复制 / 爆发饮料时先刷新（协调 2026-11-08，Windows 第七 / 九 / 十 / 十二局剩 400P 以上），最多刷这么多次
INTERVAL_REFRESH_MAX = 3
INTERVAL_DRINK_RESERVE = 200  # 刷完至少还够买两瓶 100P 饮料
CHANGE_JUNK_VALUE = (
    1.0  # card_value ≤ 它算「废牌」（冲突 0、基础卡 0.5、好印象系 1）：授業セレクトチェンジ 只在有废牌时才选
)
# 授業是セレクトチェンジ 的日子 [wiki]：选拔第 10、17 天，本战第 1、4 天（step = 日程位置）
CHANGE_STUDY_DAYS = {("produce-007", 10), ("produce-007", 17), ("produce-008", 1), ("produce-008", 4)}


def _items(shelf, kind: str) -> list[dict]:
    return [it for it in shelf or [] if it.get("kind") == kind and not it.get("purchased")]


def _affordable(item: dict, p: int) -> bool:
    return item.get("price") is not None and not item.get("purchased") and item["price"] <= p


def _tier_rank(card_id: str | None) -> int:
    """社区评级换成分数；必拿按 A 级算。protect（がむしゃら 等）只表示换卡 / 删卡时保留，不抬高选卡评级：
    第七局把 がむしゃら 当 A 级，选卡拿了 6 次（条件「トラブル 2 张以上」几乎不满足，全是废牌），回滚（reports/analysis/q7-postmortem.md）。"""
    k = YARUKI_CARDS.get(card_id or "") or {}
    rank = TIER_RANK.get(k.get("tier"), 0)
    if k.get("must"):
        rank = max(rank, TIER_RANK["A"])
    return rank


def _budget_ok(budget: dict, kind: str) -> bool:
    return budget.get(kind, 0) > 0


def _budget_text(budget: dict) -> str:
    def one(kind: str, name: str) -> str:
        left = budget.get(kind + "_left")
        return f"{name}本页还可 {budget[kind]} 次（剩 {'未识别' if left is None else left}）"

    if budget.get("stuck"):
        return "上次刷新后候选没变（刷新没生效，次数应已用完）：不再刷新，直接拿卡"
    if budget.get("other"):
        note = "全是强化卡，可以用光" if budget.get("high") else f"每页最多 1 次，留 {budget.get('reserve')} 次备用"
        return f"{one('reroll', '刷新')}，{one('exclude', '禁用')}；{note}"
    note = (
        "确定强化页，可以用光 [もなみ]"
        if budget.get("high")
        else "还缺 せのびの魔法：刷新每页最多 2 次（第 5 天前留 1 次给确定强化页）；禁用每页 1 次、照常留预留（决策-11）"
        if budget.get("cap") == 2
        else f"每页最多 1 次，预留 {budget.get('reserve')} 次给第 5 天确定强化 / 本战（次数随回忆带进本战 [wiki]）"
    )
    return f"{one('reroll', '刷新')}，{one('exclude', '禁用')}；{note}"


def _invalid_decision(page: str, d: Decision, n: int | None, kw: dict) -> str | None:
    """决策结果在画面上执行不了的原因；None 表示可用。"""
    a = d.action
    if page in ("STUDY", "OUTING"):
        if a.kind is not ActionKind.EVENT_SELECT:
            return f"输出了 {a.kind.value}，不是 EventSelect"
        if n is not None and not 0 <= int(a.args.get("select", -1)) < n:
            return f"选项 {a.args.get('select')} 越界（共 {n} 项）"
    if (
        page in ("SELECT_DRINK", "SELECT_CARD", "SELECT_PITEM")
        and a.kind is ActionKind.ACTIVATE_EFFECT
        and n is not None
    ):
        i = (a.args.get("pick_indexes") or [-1])[0]
        if not 0 <= int(i) < n:
            return f"序号 {i} 越界（共 {n} 项）"
    if page == "SELECT_DRINK" and kw.get("can_skip") is False and a.kind is ActionKind.EXCLUDE_CARD:
        return "这一页不能跳过，却输出了不拿"
    if page == "SELECT_PITEM" and a.kind is ActionKind.WAIT:
        return a.args.get("why") or "P 道具未识别"
    return None


def _drink_tier(drink_id: str | None) -> int:
    from hiro.state.knowledge import DRINK_CLASSES, drink_class

    return DRINK_CLASSES.get(drink_class(drink_id), DRINK_CLASSES["other"])["tier"]


def _r2_missing(drinks: list) -> list[str]:
    """R2 社区目标（playbook：两瓶爆发 + 两瓶抽牌，复制优先）还缺什么。"""
    from hiro.state.knowledge import DRAW_CLASSES, drink_class

    cls = [drink_class(d) for d in drinks if d]
    out = []
    if "copy" not in cls:
        out.append("复制")
    if cls.count("burst") < 2:
        out.append("爆发")
    if sum(c in DRAW_CLASSES for c in cls) < 2:
        out.append("抽牌")
    return out


def wants_copy(card_id: str | None, copies: int) -> bool:
    """牌组里已有 copies 张（确认过的张数）时还要不要再拿一张：没有就要；必拿 / 核心卡不到 keep 张也要
    （せのびの魔法 keep=2：第二张 R2 +4.3 万 ± 1.6 万，决策-17 §3；届いて 第二张 ≈0，不设）。"""
    if copies <= 0:
        return True
    k = YARUKI_CARDS.get(card_id or "") or {}
    return bool(k.get("must")) and copies < int(k.get("keep") or 1)


STEP_SEARCH = (1, 2, -1, 3)  # 日程对不上时往前后找几步（页头读偏多半是还停在前一天）


def _step_for_options(ps: ProduceState, available: list[dict]) -> int | None:
    """和页面选项对得上的日程位置：当前 step 对得上就是它；否则按 STEP_SEARCH 找附近的；都对不上 None。
    「对得上」= 页面上的每种选项（休息除外）日程那天都有，页面有课程时日程那天有 open_lesson_ids。"""
    from hiro.state.context import schedule

    kinds = {o.get("option") for o in available if o.get("option")} - {"rest"}
    try:
        sch = schedule(ps.mode)
        n = len(sch.export()["stages"])
    except Exception:  # noqa: BLE001  日程表不可用：不核对，照旧
        return ps.step

    def fits(pos: int) -> bool:
        if not 1 <= pos <= n:
            return False
        st = sch.stage(pos)
        if "training" in kinds and not st.get("open_lesson_ids"):
            return False
        return kinds <= set(st.get("options") or ())

    if fits(ps.step):
        return ps.step
    return next((ps.step + d for d in STEP_SEARCH if fits(ps.step + d)), None)


def _is_core_card(card_id: str | None, copies: int | None = None) -> bool:
    """本战瘦牌组的「值得拿」：必拿、社区 S / A、核心角色（终结 / 延迟 / 持续 / 循环）。
    保护卡（えいえいおー 之类：换卡 / 删卡不动，不等于值得多拿）只在知道牌组张数、且不够保留数时才算
    （第十九局本战 00:03：牌组没识别，えいえいおー+ 被当成缺的核心卡，瘦牌组规则被跳过、进了 LLM）。"""
    from hiro.state.knowledge import CORE_ROLES

    k = YARUKI_CARDS.get(card_id or "") or {}
    if k.get("must") or k.get("tier") in ("S", "A") or k.get("role") in CORE_ROLES:
        return True
    return bool(k.get("protect")) and copies is not None and copies < int(k.get("keep") or 1)


@cache
def _makes_sleep(card_id: str | None) -> bool:
    """打出会往牌堆塞眠気（成長痛 / 気まぐれハート 之类）。"""
    from hiro.brain.cards import description_text
    from hiro.state.context import catalog

    try:
        return "眠気" in description_text(
            catalog().card(card_id, 0)["produceDescriptions"]
        ) and "生成" in description_text(catalog().card(card_id, 0)["produceDescriptions"])
    except Exception:
        return False


def _pollution_key(card_id: str, bad: bool) -> tuple:
    """越小越不污染牌组：冲突牌 / 纯好印象系排最后（用户 2026-11-03「这张卡不该进牌组」）；一次性（打一次就除外）> 可复用；不生成眠気；同等时价值高的先。"""
    return (conflict_like(card_id), is_once(card_id) is not True, _makes_sleep(card_id), bad, -card_value(card_id))


def _best_drink(raw: list[dict]) -> int | None:
    """饮料候选里最好的一瓶（兜底用）：饮料类别的默认档位 / 顺序 > 本计划（Plan2）/ 通用 > 稀有度；没有一瓶认得出时 None。"""
    from hiro.state.context import catalog

    rarity = {"ProduceDrinkRarity_Ssr": 3, "ProduceDrinkRarity_Sr": 2, "ProduceDrinkRarity_R": 1}

    def score(i: int) -> tuple | None:
        did = raw[i].get("drink_id")
        if not did:
            return None
        try:
            row = catalog().one("ProduceDrink", did)
        except Exception:
            row = {}
        return (
            -_drink_rank(did),
            row.get("planType") in ("ProducePlanType_Plan2", "ProducePlanType_Common"),
            rarity.get(row.get("rarity"), 0),
            -i,
        )

    scored = [(s, i) for i in range(len(raw)) if (s := score(i)) is not None]
    return max(scored)[1] if scored else None


# 偶像拿手 buff（IdolCard.examEffectType → 卡面字样）[g8-試験]；没列的类型不按 buff 加分
# 主数据：ExamLessonBuff = 集中、ExamConcentration = 強気（アノマリー）、ExamFullPower = 全力（原来把 ExamConcentration 写成了集中、漏了 ExamLessonBuff，
# 11-20 铺四个流派时发现；只影响广以外的偶像）
SIGNATURE_BUFF = {"ProduceExamEffectType_ExamCardPlayAggressive": "やる気", "ProduceExamEffectType_ExamReview": "好印象",
                  "ProduceExamEffectType_ExamParameterBuff": "好調", "ProduceExamEffectType_ExamLessonBuff": "集中",
                  "ProduceExamEffectType_ExamConcentration": "強気", "ProduceExamEffectType_ExamFullPower": "全力"}  # fmt: skip


@cache
def _signature_buff(idol_card_id: str | None) -> str | None:
    from hiro.state.context import catalog

    try:
        effect = (
            catalog().one("IdolCard", (idol_card_id or "").replace("i_card-skin-", "i_card-")).get("examEffectType")
        )
        return SIGNATURE_BUFF.get(effect) if effect else None
    except Exception:
        return None


_IMPRESSION_MAIN = (
    r"好印象の\d+%分パラメータ上昇",
    r"好印象が\d+以上の場合、使用可",
    r"以降.*好印象\+",
    r"好印象増加量増加",
)


@cache
def _impression_main(card_id: str) -> bool:
    """主要得分 / 使用条件挂在好印象上（协调 11-18）：「好印象のN%分パラメータ上昇」「好印象がN以上の場合、使用可」「以降…好印象+」
    「好印象増加量増加」。只消費好印象、主体是やる気 / 元気的（ゆめみごこち、あのときの約束）、顺带加好印象的（成長痛、止められない想い）不算。"""
    import re

    from hiro.brain.cards import description_text
    from hiro.state.context import catalog

    try:
        text = description_text(catalog().card(card_id, 0)["produceDescriptions"])
    except Exception:
        return False
    return any(re.search(p, text) for p in _IMPRESSION_MAIN)


@cache
def _card_rarity(card_id: str) -> int:
    from hiro.state.context import catalog

    try:
        return {"ProduceCardRarity_R": 1, "ProduceCardRarity_Sr": 2, "ProduceCardRarity_Ssr": 3}.get(
            catalog().card(card_id, 0).get("rarity"), 0
        )
    except Exception:
        return 0


@cache
def _gives_buff(card_id: str, word: str) -> bool:
    """卡面写着「<buff>+N」（やる気+3 之类；未强化版的说明）。"""
    from hiro.brain.cards import description_text
    from hiro.state.context import catalog

    try:
        return f"{word}+" in description_text(catalog().card(card_id, 0)["produceDescriptions"])
    except Exception:
        return False


@cache
def _impression_role(card_id: str) -> tuple[bool, bool]:
    """(好印象来源, 消耗好印象)：卡面「好印象+N」/ 费用类型 ExamCostType_ExamReview（私がスター、ゆめみごこち、手書きのメッセージ）。"""
    try:
        from hiro import master
        from hiro.brain.cards import card_view

        kaa = master.card(card_id)
        if kaa is None:
            return False, False
        v = card_view(0, kaa, available=True)
        return "好印象+" in v.text, bool(v.extra_cost and "ExamReview" in str(v.extra_cost.get("type")))
    except Exception:
        return False, False


def is_pure_impression(card_id: str | None) -> bool:
    """纯好印象牌：好印象系（knowledge.OFF_PLAN_CARDS），或卡面无条件「好印象+N」且不消耗好印象（ノートの端の決意、止められない想い、成長痛）。
    「元気が30以上の場合、好印象+3」这类附带条件的（ほかほか焼き芋）不算。"""
    from hiro.state.knowledge import OFF_PLAN_CARDS

    if not card_id:
        return False
    if card_id in OFF_PLAN_CARDS:
        return True
    try:
        import re

        from hiro import master
        from hiro.brain.cards import card_view

        kaa = master.card(card_id)
        text = card_view(0, kaa, available=True).text if kaa else ""
    except Exception:
        return False
    return "好印象+" in text and not re.search(r"の場合、好印象\+", text) and not _impression_role(card_id)[1]


def unwanted(card_id: str | None, copies: int = 0) -> str | None:
    """选卡时不该拿的原因（用户 2026-11-01 小牌组）：冲突牌；可复用的非核心牌；可复用的核心牌已经够张数（copies = 牌组里已有张数）。
    必拿卡不在此列。"""
    from hiro.state.knowledge import SMALL_DECK_CORE, core_keep, small_deck_reject

    if not card_id or (YARUKI_CARDS.get(card_id) or {}).get("must"):
        return None
    if conflict_like(card_id):
        return "冲突牌 / 好印象系（用户 2026-11-03：这张卡不该进牌组）"
    if card_id in SMALL_DECK_CORE and is_once(card_id) is False and copies >= core_keep(card_id):
        return f"{SMALL_DECK_CORE[card_id].split('：')[0]} 已有 {copies} 张，可复用的牌再多会堵牌堆（小牌组）"
    return small_deck_reject(card_id)


# 换牌 / 删牌同一个顺序（用户 2026-11-03：「换牌的优先级理应和删牌是一致的」；删牌顺序「优先删眠気，然后是非一次性的 思い出し笑い
# 这类不提供再动（篠澤広 Campus mode 打元気弹能再动）的牌，然后再是不好用的卡（初始卡）」）。
# 一次性 = master 的「レッスン中1回」（playMovePositionType=Lost，knowledge.is_once）；再動 = master playEffects 里有
# exam_playable_value_add（スキルカード使用数追加）/ exam_extra_turn（ターン追加）。都不看识别文字。
# 只保留游戏规则本身造成的差别：授業セレクトチェンジ 不能选トラブル（眠気），删卡可以。
CULL_TIERS = {
    0: "眠気等トラブル",
    1: "非一次性、不带再動的牌",
    2: "冲突牌 / 纯好印象系",
    3: "初始卡 / 基础卡",
    4: "其余（按价值）",
    5: "核心牌的多余副本",
}
REDO_EFFECTS = ("e_effect-exam_playable_value_add", "e_effect-exam_extra_turn")


def has_redo(card_id: str | None) -> bool:
    """打出后本回合能多打一张（再動）：master playEffects 里有 使用数追加 / ターン追加（含条件触发的）。"""
    from hiro.state.context import catalog

    if not card_id:
        return False
    try:
        effects = catalog().card(card_id, 0).get("playEffects") or []
    except Exception:
        return False
    return any(str(e.get("produceExamEffectId") or "").startswith(REDO_EFFECTS) for e in effects)


def conflict_like(card_id: str | None) -> bool:
    """冲突牌 / 纯好印象系（用户 2026-11-03：「这张卡不该进牌组，优先清掉」）：knowledge.is_conflict（CONFLICT_CARDS：ハイテンション 这类
    元気増加無効；OFF_PLAN_CARDS：好印象系）、CULL_AS_IMPRESSION（夢色リップ），以及社区没提到、卡面纯好印象、不带再動的牌（ノートの端の決意）。
    止められない想い（広 专属高相性）、成長痛（带再動）这类社区点名的牌不算。"""
    from hiro.state.knowledge import CULL_AS_IMPRESSION

    if not card_id:
        return False
    if is_conflict(card_id) or card_id in CULL_AS_IMPRESSION:
        return True
    return card_id not in YARUKI_CARDS and is_pure_impression(card_id) and not has_redo(card_id)


def cull_tier(card_id: str | None) -> int:
    """换 / 删顺序档位（越小越先去掉），见 CULL_TIERS（用户 2026-11-03）：
    0 眠気等トラブル（只有删卡能选）> 1 非一次性、不带再動的非核心牌（思い出し笑い、励まし、ふれあい）> 2 冲突牌 / 纯好印象系（conflict_like：
    ハイテンション、夢色リップ、ノートの端の決意）> 3 初始卡 / 基础卡（名字含「基本」）> 4 其余（一次性的牌、带再動的非一次性非核心牌，按价值）>
    5 可复用核心牌的多余副本（えいえいおー 第 3 张、成長痛 / ゆめみごこち 第 2 张；保留张数由 _protected_slots 挡掉；広 打元気弹靠再動）。"""
    from hiro.state.knowledge import small_deck_reject

    if not card_id:
        return 9
    if _is_trouble_card(card_id):
        return 0
    if _is_basic(card_id):
        return 3
    if small_deck_reject(card_id) and not has_redo(card_id):
        return 1
    if conflict_like(card_id):
        return 2
    if small_deck_reject(card_id) or is_once(card_id) is not False:
        return 4
    return 5


def cull_key(card_id: str | None, upgrade: int = 0, *, finals: bool = False) -> tuple:
    """删牌 / 换牌的排序键（越小越先去掉）：cull_tier → 本战时先按模拟删除收益（knowledge.FINALS_DELETE）→ card_value。"""
    from hiro.state.knowledge import FINALS_DELETE

    rank = FINALS_DELETE.index(card_id) if finals and card_id in FINALS_DELETE else len(FINALS_DELETE)
    return (cull_tier(card_id), rank, card_value(card_id, upgrade))


def card_value(card_id: str | None, upgrade: int = 0) -> float:
    """换卡用的卡牌价值（越高越该留）：核心 / 必拿 10 > S 8 > A 6 > 末段循环 5 > B 3 > 社区提到的其他卡 2 > 未知 1.5 >
    好印象系 1 > 基本卡 0.5 > 冲突卡 0；强化 +0.5。依据 knowledge（YARUKI_CARDS / CORE_CARDS / CONFLICT_CARDS / OFF_PLAN_CARDS）。"""
    from hiro.state.knowledge import CONFLICT_CARDS, CORE_CARDS, OFF_PLAN_CARDS

    if not card_id:
        return 1.5
    k = YARUKI_CARDS.get(card_id)
    if k and k.get("value") is not None:
        return float(k["value"]) + (0.5 if upgrade else 0.0)  # 显式价值（えいえいおー：固有道具触发牌，按 A 级）
    if card_id in CONFLICT_CARDS:
        base = 0.0
    elif card_id in CORE_CARDS or (k and k.get("must")):
        base = 10.0
    elif k and k.get("tier"):
        base = {"S": 8.0, "A": 6.0, "B": 3.0}.get(k["tier"], 2.0)
    elif k and k.get("loop"):
        base = 5.0
    elif k:
        base = 2.0
    elif card_id in OFF_PLAN_CARDS:
        base = 1.0
    elif _is_basic(card_id):
        base = 0.5
    else:
        base = 1.5
    return base + (0.5 if upgrade else 0.0)


def _is_basic(card_id: str) -> bool:
    from hiro.state.context import catalog

    try:
        return "基本" in catalog().card(card_id, 0)["name"]
    except Exception:
        return False


def _is_trouble_card(card_id: str) -> bool:
    from hiro.state.context import catalog

    if card_id == SLEEP_ID:
        return True
    try:
        return catalog().card(card_id, 0)["category"] == "ProduceCardCategory_Trouble"
    except Exception:
        return False


def _card_label(c: dict) -> str:
    from hiro.state.context import card_name

    if c.get("name"):
        return c["name"]
    try:
        return card_name(c["card_id"], c.get("upgrade") or 0)
    except Exception:
        return str(c.get("card_id"))


def choose_card_to_change(deck: list, incoming=None) -> str | None:
    """换卡（授業セレクトチェンジ 阶段 2）要换出的卡 card_id；None 表示不换（取消）。

    deck：牌组卡格（识别 read_card_dialog 的 cards：[{index, card_id, upgrade}]）或 ProduceState.deck（DeckCard）；
    incoming：阶段 1 选的换入卡（card_id 或 {card_id, upgrade}），给出时换入不比换出强就不换。
    顺序同 cull_pool(op="change")（换 / 删同序，用户 2026-11-03）；传 ProduceState.deck 时张数按它确认，传对话框卡格时保护卡所有副本都不换。"""
    cards = [
        c if isinstance(c, dict) else {"card_id": c.card_id, "upgrade": c.upgrade, "customized": c.customized}
        for c in deck or []
    ]
    counts = None
    if deck and not isinstance(deck[0], dict):
        counts = {}
        for c in cards:
            counts[c["card_id"]] = counts.get(c["card_id"], 0) + 1
    worst = _removable(cards, counts)
    if worst is None:
        return None
    if incoming is not None:
        inc = incoming if isinstance(incoming, dict) else {"card_id": incoming, "upgrade": 0}
        if card_value(inc.get("card_id"), inc.get("upgrade") or 0) <= worst[0]:
            return None
    return worst[1]["card_id"]


def _protected_slots(cards: list[dict], counts: dict | None = None) -> set[int]:
    """卡格里要保留的格（换 / 删同一套）：核心 / 必拿 / 偶像固有卡所有张都保留；其余保护卡（protect、S/A、记忆卡等）按张数保留
    keep 张（えいえいおー 2 张，其余 1 张；强化度高的先留），多余的副本才可以换 / 删（第七局 がむしゃら ×6 全被保护，换卡只能换掉 勝利をつかめ！）。
    counts：确认过的牌组张数（状态机记账的 ProduceState.deck）。None 时**保护卡所有副本都保留**（总控 2026-11-03 第十九局：牌组未知时
    换卡对话框只读到 12 / 22 张；第十四局删卡对话框 18 张拼成 36 张——没有确认过的张数，按对话框数张数放开就会换 / 删到要留的那张）。
    定制过的卡也保留（定制花了 P / 次数）。"""
    from hiro.state.knowledge import CORE_CARDS, is_protected, small_deck_reject

    keep, kept = set(), {}
    order = sorted(range(len(cards)), key=lambda i: -(cards[i].get("upgrade") or 0))
    for i in order:
        cid = cards[i].get("card_id")
        if not cid:
            continue
        if cards[i].get("customized"):
            keep.add(i)
            continue
        if not is_protected(cid) or small_deck_reject(cid):
            continue  # 可复用的非核心牌（思い出し笑い 等）即使有评级也不保护（用户 2026-11-01 小牌组 / 2026-11-03）
        want = (YARUKI_CARDS.get(cid) or {}).get("keep") or 1
        if cid in CORE_CARDS or (YARUKI_CARDS.get(cid) or {}).get("must") or "-ido-" in cid:
            keep.add(i)
        elif (
            counts is None or counts.get(cid) is None or counts[cid] < sum(1 for c in cards if c.get("card_id") == cid)
        ):
            keep.add(i)  # 张数没确认 / 对话框比记账多（拼接出错）：所有副本都留
        else:
            visible = sum(1 for c in cards if c.get("card_id") == cid)
            surplus = max(0, counts[cid] - want)  # 牌组里比 keep 多出来的张数，才能换 / 删
            if kept.get(cid, 0) < visible - min(surplus, visible):
                kept[cid] = kept.get(cid, 0) + 1
                keep.add(i)
    return keep


def cull_pool(
    cards: list[dict], op: str, *, counts: dict | None = None, finals: bool = False, deck_size: int | None = None
) -> list[dict]:
    """能去掉的卡格，按换 / 删同一个顺序排好（第一个最该去掉）。op："change"（授業セレクトチェンジ 阶段 2）/ "delete"（相談削除、删卡事件）。
    - 两者共用：保护格（_protected_slots）、没认出的格不选；顺序 cull_key。
    - 游戏规则造成的差别：换卡不能选トラブル（眠気）。
    - 本战删卡（deck_size ≤ FINALS_DECK_TARGET 22）：删了 R2 会被応援棒补一张基础卡，所以只删 0–1 档（トラブル、非一次性的非核心牌——
      用户 2026-11-03「能换掉的时候就应该换掉」；换成基础卡对 R2 持平、R1 净赚；基础卡（2 档）本身不删，见 lean_deletable）；
      > 22 张时再加 FINALS_DELETE / 冲突牌，删到 22 为止。
      换卡不改变张数，这一条不适用。"""
    from hiro.state.knowledge import FINALS_DECK_TARGET, FINALS_DELETE

    keep = _protected_slots(cards, counts)
    pool = [c for i, c in enumerate(cards) if c.get("card_id") and i not in keep]
    if op == "change":
        pool = [c for c in pool if not _is_trouble_card(c["card_id"])]
    elif finals:
        big = deck_size is not None and deck_size > FINALS_DECK_TARGET
        pool = [
            c
            for c in pool
            if lean_deletable(c["card_id"])
            or (big and (c["card_id"] in FINALS_DELETE or c["card_id"] in CONFLICT_CARDS))
        ]
    return sorted(
        pool, key=lambda c: (*cull_key(c["card_id"], c.get("upgrade") or 0, finals=finals), c.get("index", 0))
    )


def lean_deletable(card_id: str | None) -> bool:
    """本战牌组 ≤ 22 张时能删的：眠気等トラブル、非一次性不带再動的非核心牌、冲突牌 / 纯好印象系（0–2 档，用户 2026-11-03「优先清掉」）。
    基础卡不删——删了 R2 开考时応援棒会再补一张。"""
    return bool(card_id) and cull_tier(card_id) <= 2


def _idol_section(idol_card_id: str | None, profile: IdolProfile | None, mode_id: str | None = None) -> str:
    """初 / N.I.A 提示词里的「偶像」一节：按偶像卡的流派写（协调 11-19 多偶像）。广照原来的说法；别的偶像写拿手 buff 和冲突的 buff，
    社区依据只有流派优先表（いおん）和 g8-NIAM 的循环件，没有更细的就写「暂无」。"""
    from hiro.brain import flows
    from hiro.brain.idol_know import notes as guide_notes

    f = flows.flow(idol_card_id)
    # 偶像卡攻略（协调 11-20）：各决策点排最前的条目的 why + 出处；广要 HIRO_GUIDES_HIRO=1，没包 / 开关关时没有
    extra = guide_notes(mode_id, idol_card_id)
    tail = ("\n\n这张卡的攻略要点：\n" + "\n".join(extra)) if extra else ""
    if not idol_card_id:
        # N10（11-26 G1）：桌面端续打不带偶像卡编号，原来这里当成广写，藤田ことね的局 LLM 理由里出现「和篠澤広的打法契合」
        return "## 偶像\n\n这局的偶像卡没认出来：不要按某个偶像或流派推断，只按局面、候选和下面的社区规则判断。" + tail
    if f == "yaruki" and "-shro-" in idol_card_id:
        return (
            "## 偶像\n\n篠澤広（Campus mode!!）是ロジック的やる気流派：能上やる気的卡、用完就除外的卡对她有用；好印象系的卡和她的打法冲突。"
            + tail
        )
    buff = _signature_buff(idol_card_id)
    others = "、".join(flows.OTHER_BUFFS.get(buff or "", ())) or "暂无（这个流派的冲突卡没有社区说法）"
    loops = "、".join(_card_label({"card_id": c}) for c in flows.loop_parts(idol_card_id)) or "暂无"
    name = (
        profile.name
        if profile is not None and profile.idol_id == (idol_card_id or "").replace("i_card-skin-", "i_card-")
        else (idol_card_id or "未知")
    )
    if buff is None:  # 主数据查不到这张卡的流派：不编「主打 None」
        return (
            f"## 偶像\n\n{name}：流派没查到（主数据里没有这张卡的考试效果），按局面和社区规则判断，不要套用别的偶像的打法。"
            + tail
        )
    return (
        f"## 偶像\n\n{name}：{flows.flow_name(idol_card_id) or '流派未知'}，拿手 buff 是{buff}。"
        f"主打{buff}、加{buff}的卡和用完就除外的卡对她有用；主打{others}的卡和她的打法冲突。"
        f"循环件 [g8-NIAM]：{loops}。"
        + ("" if extra else "更细的初 / N.I.A 卡表：暂无（社区篇只有回忆用的流派优先表）。")
        + tail
    )


FLOW_CULL = {0: "トラブル", 1: "基本卡", 2: "冲突卡（主打同プラン另一种 buff）", 3: "可复用、不是这个流派循环件的卡"}


def _flow_delete_pool(cards: list[dict], idol_card_id: str | None, mode_id: str | None = None) -> list[dict]:
    """广以外的偶像在初 / N.I.A 相談里能删的卡，按 FLOW_CULL 档位排（第一个最该删）：トラブル > 「○○の基本」[g8-NIAM] >
    冲突卡 > 不带「レッスン中1回」、不是循环件、也不加 / 不主打拿手 buff 的可复用卡 [g8-NIAM]。
    不删：一次性的卡、循环件、定制过的、偶像固有卡（-ido-）、没认出的格。
    偶像卡攻略的 card_remove 条目（协调 11-20）：prefer 里的卡保护不删；avoid 里的卡 / 卡类（class:trouble / basic / other_buff / off_plan）
    按条目进对应的档（卡 id 算冲突卡那一档）。"""
    from hiro.brain import flows, guides
    from hiro.brain.idol_know import lookup
    from hiro.state.knowledge import is_once

    buff = _signature_buff(idol_card_id)
    loops = set(flows.loop_parts(idol_card_id))
    merged = guides.merge(lookup(mode_id, idol_card_id, "card_remove"))
    # 攻略级别高于 flows（协调 11-20）：条目 prefer 里的卡保护不删，哪怕 flows 判它冲突
    protect = {it.id for it in merged if it.side == "prefer"}
    drop = {it.id for it in merged if it.side == "avoid"}
    plan_of = guides.idol_traits(idol_card_id)[0]

    def tier(c: dict) -> int | None:
        cid = c.get("card_id")
        if not cid or c.get("customized") or "-ido-" in cid or cid in protect:
            return None
        if _is_trouble_card(cid):  # class:trouble / class:basic 本来就是先删的两档，条目里写不写都一样
            return 0
        if _is_basic(cid):
            return 1
        if flows.conflicts(cid, buff) or cid in drop:  # class:other_buff 就是 flows.conflicts，本来就删
            return 2
        if "class:genki_cash" in drop and flows.buff_main(
            cid, "元気"
        ):  # 靠元気出分的卡（元気のN%分パラメータ上昇），条目点名才删
            return 2
        if "class:off_plan" in drop and plan_of and _card_plan(cid) not in (None, "common", plan_of):
            return 2
        fits = bool(buff) and (_gives_buff(cid, buff) or flows.buff_main(cid, buff))
        if is_once(cid) is False and cid not in loops and not fits:
            return 3
        return None

    out = [(t, c) for c in cards if (t := tier(c)) is not None]
    out.sort(key=lambda x: (x[0], x[1].get("index", 0)))
    return [dict(c, flow_tier=t) for t, c in out]


def _card_plan(card_id: str) -> str | None:
    """技能卡的プラン（sense / logic / anomaly / common），按主数据 planType；查不到 None。"""
    from hiro.brain.guides import PLAN_OF
    from hiro.state.context import catalog

    try:
        pt = catalog().card(card_id, 0).get("planType")
    except Exception:  # noqa: BLE001
        return None
    return "common" if pt == "ProducePlanType_Common" else PLAN_OF.get(pt)


def qualify_consult_deletable(card_id: str | None, upgrade: int = 0) -> bool:
    """选拔相談能删的（用户 2026-11-03 删牌顺序）：0 眠気 > 1 非一次性不带再動 > 2 冲突牌 / 纯好印象系 > 3 初始卡 / 基础卡 >
    4 里「不好用」的（不带再動、价值 ≤ CHANGE_JUNK_VALUE）。核心牌的多余副本（5 档）和一般的牌不在相談里花 P 删。"""
    if not card_id:
        return False
    tier = cull_tier(card_id)
    if tier <= 3:
        return True
    return tier == 4 and not has_redo(card_id) and card_value(card_id, upgrade) <= CHANGE_JUNK_VALUE


def deletable(card: dict) -> bool:
    """单张卡能不能删（不看张数）：没认出 / 定制过 / 受保护卡（knowledge.is_protected，非一次性的非核心牌除外）不删。
    带张数的判断用 cull_pool(op="delete")。"""
    from hiro.state.knowledge import is_protected, small_deck_reject

    cid = card.get("card_id")
    return bool(cid) and not card.get("customized") and (not is_protected(cid) or bool(small_deck_reject(cid)))


def _removable(cards: list[dict], counts: dict | None = None, *, finals: bool = False) -> tuple[float, dict] | None:
    """换卡时最该换出的卡（cull_pool op="change" 的第一张）和它的价值；没有可换的返回 None。"""
    pool = cull_pool(cards, "change", counts=counts, finals=finals)
    if not pool:
        return None
    best = pool[0]
    return card_value(best["card_id"], best.get("upgrade") or 0), best


def _card_priority(card_id: str | None) -> float:
    k = YARUKI_CARDS.get(card_id or "")
    if not k:
        return 0.0
    return 3 * bool(k.get("must")) + TIER_RANK.get(k["tier"], 0) + 0.5 * bool(k.get("loop"))


def _upgrade_target(ps: ProduceState) -> tuple[int, str] | None:
    """牌组里最值得强化的未强化卡：UPGRADE_ORDER（届いて！ > せのび > デイドリーミング > 紙吹雪，决策-07）> 必拿 > S > A > 末段循环，必须有 + 版。"""
    from hiro.state.context import catalog

    best = None
    for k, c in enumerate(ps.deck):
        pri = _card_priority(c.card_id) + (
            10 * (len(UPGRADE_ORDER) - UPGRADE_ORDER.index(c.card_id)) if c.card_id in UPGRADE_ORDER else 0
        )
        if c.upgrade or pri <= 0:
            continue
        try:
            catalog().card(c.card_id, 1)
        except Exception:
            continue
        if best is None or pri > best[0]:
            best = (pri, k, YARUKI_CARDS[c.card_id]["name"])
    return (best[1], best[2]) if best else None


def _shop_candidates(shelf: list[dict], services: dict, p: int, buy: ActionKind) -> list[Candidate]:
    """货架每一项与服务 → Candidate（预演摘要：价格、买后 P 点、社区说明）。"""
    from hiro.state.context import card_name, drink_name

    out = []
    for it in shelf:
        if it.get("kind") == "card" and it.get("card_id"):
            name, note = (
                card_name(it["card_id"], it.get("upgrade") or 0),
                YARUKI_CARDS.get(it["card_id"], {}).get("note"),
            )
        elif it.get("kind") == "drink" and it.get("drink_id"):
            from hiro.state.knowledge import DRINK_CLASSES, drink_class

            info = DRINK_CLASSES[drink_class(it["drink_id"])]
            name, note = drink_name(it["drink_id"]), f"{info['label']}（默认 {info['tier']} 档）：{info['note']}"
        else:
            name, note = it.get("name") or "（未识别）", None
        preview = {"商品": name, "价格": it.get("price"), "买后P点": p - it["price"] if it.get("price") is not None else None,
                   "SALE": bool(it.get("sale")), "已购": bool(it.get("purchased")), "社区": note}  # fmt: skip
        out.append(
            Candidate(
                Action(buy, {"index": it.get("index")}), preview={k: v for k, v in preview.items() if v is not None}
            )
        )
    for name, svc in services.items():
        if svc:
            out.append(
                Candidate(
                    Action(buy, {"service": name}),
                    preview={"服务": name, "价格": svc.get("price"), "可用": svc.get("available", True)},
                )
            )
    return out


FORMATION_CONF = "conf/formations.json"


def _formation_conf(path: str | None = None) -> dict:
    """识别读出编成页之前的过渡：用户在 conf/formations.json 里写明游戏里预存的编成内容（格式同 page_fields 的
    support_sets / memory_sets，见 brain/formation.py）。没有文件时返回 {}。"""
    import json
    from pathlib import Path

    from hiro.brain.llm import REPO

    f = Path(path) if path else REPO / FORMATION_CONF
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}


SENOBI = "p_card-02-act-3_186"  # せのびの魔法


def _customize_args(card: dict, opt: dict) -> dict:
    """CustomizeCard 的参数：card_index / card_id / customize_id，加上游戏执行要的 item_index（定制项在画面上从上到下的序号）。
    item_index：page_fields 给了 item_index 就用它；否则用它在本卡 customizations 列表里的位置（识别按画面顺序给出，
    master 推出时按 produceCardCustomizeIds 的顺序——假设与画面顺序一致，尚无实机截图核对）。"""
    opts = card.get("customizations") or []
    index = opt.get(
        "item_index", next((k for k, o in enumerate(opts) if o is opt or o.get("id") == opt.get("id")), None)
    )
    return {"card_index": card["index"], "card_id": card["card_id"], "customize_id": opt["id"], "item_index": index}


def _customize_text(customize_id: str) -> str:
    """定制 ID + master 里展开的 grow effect ID（せのびの魔法 的 wrapper-a 要展开才看得出是 exam_block）。"""
    from hiro.state.context import catalog

    try:
        rows = catalog().by_id("ProduceCardCustomize", customize_id)
    except Exception:
        rows = []
    return "|".join([customize_id] + [g for r in rows for g in r["produceCardGrowEffectIds"]])


def _customize_desc(customize_id: str) -> str:
    """定制项给人看的说明（master ProduceCardCustomize.description，第 1 级）。"""
    from hiro.state.context import catalog

    try:
        rows = sorted(catalog().by_id("ProduceCardCustomize", customize_id), key=lambda r: r["customizeCount"])
        return str(rows[0]["description"] or customize_id) if rows else customize_id
    except Exception:
        return customize_id


def _customize_cost(card_id: str, keys: list, levels: int) -> int | None:
    """一张卡按定制优先表的关键字做 levels 级要多少 P（master 逐级价格之和，例如 えいえいおー やる気+ 40 + 40 + 70 = 150）。"""
    from hiro.state.context import catalog

    for up in (1, 0):
        try:
            ids = catalog().card(card_id, up)["produceCardCustomizeIds"]
        except Exception:
            continue
        for key in keys:
            for cid in ids:
                if key in _customize_text(cid) or key in cid:
                    rows = sorted(
                        catalog().by_id("ProduceCardCustomize", cid), key=lambda r: r.get("customizeCount") or 0
                    )
                    if rows:
                        return sum(r["producePoint"] for r in rows[:levels])
    return None


def _master_customize_offers(ps: ProduceState) -> list[dict]:
    """牌组 → 每张卡在 master 里的定制项与基础价（已定制过的卡跳过：社区路线每张核心卡定制一次）。"""
    from hiro.state.context import catalog

    out = []
    for i, c in enumerate(ps.deck):
        if c.customized:
            continue
        try:
            ids = catalog().card(c.card_id, c.upgrade)["produceCardCustomizeIds"]
        except Exception:
            continue
        opts = []
        for cid in ids:
            rows = catalog().by_id("ProduceCardCustomize", cid)
            if rows:
                opts.append({"id": cid, "price": rows[0]["producePoint"], "available": True})
        if opts:
            out.append({"index": i, "card_id": c.card_id, "upgrade": c.upgrade, "customizations": opts})
    return out


def _drink_rank(drink_id: str | None) -> float:
    """单瓶饮料的默认排名（小的好）：类别默认档位 × 10 + 同档顺序（knowledge.DRINK_CLASSES；不看持有组合，组合比较用 hiro.brain.drinks）。"""
    from hiro.state.knowledge import DRINK_CLASSES, drink_class

    info = DRINK_CLASSES[drink_class(drink_id)]
    return info["tier"] * 10 + info["order"]


SPEND_MIN_P = 100  # 相談删卡 / 强化最低价 [seesaa]


def _rest_blocked(ps: ProduceState, rows: list[dict], i: int) -> str | None:
    """选行动的休息是否该拦：返回原因，不拦返回 None。
    1. 明天就开考、考前自动回复 50% 会把休息多回的体力封顶吃掉（零收益）；
    2. 社区模板今天去相談 / 特別指導（选拔第 12、19 天），P 点够用、能去，且不休息开考体力也 ≥ 21 [もりし]。"""
    from hiro.state.context import rest_real_gain
    from hiro.state.knowledge import OUTING_HP_LINE

    if i is None or not 0 <= i < len(rows) or rows[i]["option"] != "rest":
        return None
    _, no_rest, rest = rest_real_gain(ps)
    others = [r["option"] for k, r in enumerate(rows) if k != i]
    if not others:
        return None
    if rest == no_rest and any(o != "rest" for o in others):
        return f"零收益：明天开考自动回复后不休息也是 {no_rest} 体力"
    template = template_option(ps.mode, ps.step, ps.stamina)
    spend = [o for o in others if o in ("shop", "special_guidance")]
    if (
        template in ("shop", "special_guidance")
        and spend
        and ps.produce_point >= SPEND_MIN_P
        and no_rest >= OUTING_HP_LINE
    ):
        return f"社区模板今天去{'相談' if 'shop' in spend else '特別指導'}，P {ps.produce_point} 不带进本战，不休息开考体力也有 {no_rest}"
    return None


def _int(data: dict | None, key: str) -> int | None:
    try:
        return int(data[key]) if data and data.get(key) is not None else None
    except (TypeError, ValueError):
        return None


def _event_roles(choices: list[dict]) -> dict:
    """授業 / 外出的选项 → {trouble: index | None, paid: index | None, plain: [非眠気项 index（含付费档），按画面顺序]}。

    两种 page_fields.choices 格式：
    - 结构化（识别-01）：{index, text, cost_hp, cost_p, trouble, selected, center}，代价不在 text 里：优先读 trouble / cost_p；
    - 文字拼接（旧格式）：text = 「选项名｜体力-5｜Pポイント-50｜トラブル追加（眠気）｜效果」：没有结构化字段时解析 text。"""
    trouble = paid = None
    plain = []
    for c in sorted(choices, key=lambda c: c["index"]):
        text = c.get("text") or ""
        is_trouble = bool(c["trouble"]) if c.get("trouble") is not None else _is_trouble(text)
        if "cost_p" in c:
            is_paid = bool(c.get("cost_p"))
        else:
            is_paid = "Pポイント" in text or "P-50" in text.replace(" ", "") or "-50" in text
        if is_trouble and trouble is None:
            trouble = c["index"]
            continue
        if is_paid and paid is None:
            paid = c["index"]
        plain.append(c["index"])
    return {"trouble": trouble, "paid": paid, "plain": plain}


def _from_selected_effect(fields: dict, plain: list[int], effect: str | None) -> int | None:
    """选中项弹出的效果说明（page_fields.selected / effect）里有效果名：是本人效果就选它，是另一种效果就选另一项。"""
    sel, eff = fields.get("selected"), fields.get("effect") or ""
    if effect is None or sel is None or not eff or sel not in plain or len(plain) != 2:
        return None
    if effect in eff:
        return sel
    if "関係" in eff:
        return next(i for i in plain if i != sel)
    return None


def _is_trouble(text: str) -> bool:
    # 换卡档的文字是「トラブルカード以外のスキルカードを選択して…セレクトチェンジ」，不能只看「トラブル」
    return "眠気" in text or "トラブル追加" in text or ("トラブル" in text and "トラブルカード以外" not in text)


def _r1_requirements(ps: ProduceState) -> dict:
    """本战 R1 的三项审查基准；没有考试要求数据（exam_requirements 插件）时抛 ExamRequirementsUnavailable。"""
    from hiro.state.context import catalog, hif_preview

    cfg = hif_preview("produce-008", 7, ps.params, idol_card_id=ps.idol_card_id, catalog=catalog())["source"][
        "battle_config"
    ]
    return {"Vo": cfg["vocal"], "Da": cfg["dance"], "Vi": cfg["visual"]}


def _empty_state(profile: IdolProfile | None) -> ProduceState:
    """识别还没给出完整局面时，三选一的预演只需要牌组为空的占位状态（ワッペン、拿后张数按未知处理）。"""
    return ProduceState(idol_card_id=profile.idol_id if profile else "i_card-shro-3-007", mode="produce-007", step=1, stamina=0,
                        max_stamina=0, produce_point=0, params=dict.fromkeys(ATTRS, 0), growth_permil=dict.fromkeys(ATTRS, 1000), star=0,
                        wappen_left=0)  # fmt: skip
