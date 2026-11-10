# 来源：digital-hiro hiro/produce/hif.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""数字小广的 HIF 培育策略。

流程骨架、弹窗和被动事件沿用 KAA 的 HifGrindStrategy；每个决策点按 observe → reduce → decide → act → verify 走一遍，
全部经契约对象记入 trace（hiro.loop.session.Session）：
- 考试出牌：hiro.exec.exam.run_exam，局面来自 ExamStateSource 插件、每一步由 ExamAdvisor 插件决定（hiro.plugins；
  默认 = 画面识别 + 状态机记账、社区规则 + LLM：规则 → 本回合计划 → LLM → 硬约束 → 兜底），饮料和跳过也由它决定。
- 选行动、选卡、选饮料、授業 / 外出、相談、轮间：hiro.brain.deciders.ProduceDeciders（决策会话维护；规则 → LLM → 社区兜底）。
  识别还没给的页面字段（相談货架、授業 / 外出选项、轮间可买项）缺失时决策器给保守动作并写 warnings。
  这里只做 observe → decide → 把契约 Action 翻译成 KAA 上下文的操作 → verify。
"""

import time
from pathlib import Path
from dataclasses import replace
from typing import TYPE_CHECKING

from kaa.config.const import HifScenario, ProduceAction
from kaa.kaa_context import produce_solution
from kaa.tasks import R
from kaa.tasks.produce.new.strategies.hif_grind import HifGrindStrategy
from kotonebot import Loop, contains, device, logging, ocr, sleep

from hiro.brain.battle import BattleBrain
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.llm import from_spec
from hiro.brain.state import profile_for
from hiro.contracts import Action, ActionKind, Candidate, Decision, ExecutionResult
from hiro.loop.resume import ResumeBlocked
from hiro.loop.session import Session
from hiro.produce import ops
from hiro.produce.ops import button_text
from hiro.exec.exam import run_exam
from hiro.recorder import Recorder

if TYPE_CHECKING:
    from kaa.tasks.produce.new.controller import ProduceController
    from kaa.tasks.produce.new.page import (
        ActionSelectContext,
        CardSelectContext,
        ConsultContext,
        DrinkSelectContext,
        ExamContext,
        HifRoundIntervalContext,
        OutingContext,
        StudyContext,
    )


logger = logging.getLogger(__name__)

# KAA ProduceAction → 契约 Action.produce_next 的参数
_PA = {
    ProduceAction.VOCAL_SP: ("training", "Vo", True), ProduceAction.DANCE_SP: ("training", "Da", True), ProduceAction.VISUAL_SP: ("training", "Vi", True),
    ProduceAction.VOCAL: ("training", "Vo", False), ProduceAction.DANCE: ("training", "Da", False), ProduceAction.VISUAL: ("training", "Vi", False),
    ProduceAction.STUDY_VOCAL_HIF: ("class", "Vo", None), ProduceAction.STUDY_DANCE_HIF: ("class", "Da", None), ProduceAction.STUDY_VISUAL_HIF: ("class", "Vi", None),
    ProduceAction.STUDY: ("class", None, None), ProduceAction.OUTING: ("outing", None, None), ProduceAction.GIFT: ("gift", None, None),
    ProduceAction.CONSULT: ("shop", None, None), ProduceAction.REST: ("rest", None, None),
}  # fmt: skip
ACTION_REQUIRED = ("stamina", "produce_point", "params", "options")
TAP_RETRIES = 2
EXAM_END_LOOP_TIMEOUT = 90.0  # 考试结束后点「次へ」/ 结果页的总时长上限
EXAM_END_SETTLE = 2.0  # 考试结束后点「次へ」等按钮区变化的上限（原来固定睡 2s）


def to_action(pa: ProduceAction) -> Action:
    option, attr, sp = _PA.get(pa, (pa.value, None, None))
    return Action.produce_next(option, attr=attr, sp=sp)


def _card_summary(card) -> dict | None:
    if card is None:
        return None
    return {"name": card.name, "id": card._id, "upgraded": bool(card.upgrade_count), "effect": card.effect_display_text}


REWARD_SKIP_CANCEL = (218, 1160)  # 「報酬スキップ」左下「キャンセル」（qualify6 帧 00412）


class UnhandledPage(ResumeBlocked):
    """我们没有接管的页面 / 钩子，或读不出、决策不合法：停下、存帧、报警（不重试，不交给 KAA 默认）。"""



def retry_tickets(boxes) -> tuple[int, int] | None:
    """再挑戦确认框「所持 a → b」的 (a, b)；读不到 None。"""
    import re

    row = next((t for t in boxes if "所持" in t.text), None)
    if row is None:
        return None
    y = row.rect.center.y
    same = sorted((t for t in boxes if abs(t.rect.center.y - y) <= 20), key=lambda t: t.rect.center.x)
    nums = [int(n) for t in same for n in re.findall(r"\d+", t.text.replace(",", ""))]
    return (nums[-2], nums[-1]) if len(nums) >= 2 else None


def retry_cost(boxes) -> str | None:
    """再挑戦确认框「所持 260 → 259」：张数减少 = 用了再挑戦チケット（"ticket"），不变 = 免费（"free"），读不到 None。
    第二十五局：只看「券」字，确认框写的是「再挑戦チケット」，用了一张券（260→259）却记成 free；免费那次框里也有「チケット」，
    所以按所持数前后判断。boxes：.text / .rect.center 的 OCR 结果（kotonebot ocr）。"""
    got = retry_tickets(boxes)
    if got is None:
        return None
    before, after = got
    if after == before - 1:
        return "ticket"
    if after == before:
        return "free"
    return None

# 再挑戦止损（协调 2026-11-07）：本战 R2 打完、要夺冠还需要的 R2 分数超过本次 R2 得分的 RETRY_STOP_LOSS_FACTOR 倍时，
# 再挑戦不用券（免费照常）。Win2：R2 第一次 19 万、要夺冠需要约 83.5 万，还用了 2 张券、越打越低。行为变化，默认关；HIRO_RETRY_STOP_LOSS=1 打开
RETRY_STOP_LOSS = __import__("os").environ.get("HIRO_RETRY_STOP_LOSS") == "1"
RETRY_STOP_LOSS_FACTOR = 2.0


def finals_hopeless(f: dict) -> str | None:
    """本战最终结果页字段 → 翻不了盘时给原因（字符串），否则 None。读数不全时不判（None）。"""
    score, rnd = f.get("score"), f.get("round_score")
    tops = [e.get("score") for e in f.get("entries") or [] if e.get("score") is not None and e.get("score") != score]
    if not score or not rnd or not tops:
        return None
    need = max(tops) - (score - rnd)  # 我方 R1 不变，R2 至少要拿这么多才追平第一
    if need > RETRY_STOP_LOSS_FACTOR * rnd:
        return f"夺冠需要 R2 {need:,}，本次 R2 {rnd:,}，超过 {RETRY_STOP_LOSS_FACTOR:g} 倍"
    return None


def screen_retries_left(texts) -> int | None:
    """结算页「再挑戦」按钮上方的「あと N回」→ N；读不到 None。"""
    import re

    for t in texts or []:
        m = re.search(r"あと\s*(\d)\s*回", getattr(t, "text", "") or "")
        if m:
            return int(m.group(1))
    return None


def finals_label_round(npc_round: int | None, turns: int | None) -> int | None:
    """开考时判本战第几轮。R1 9 回合、R2 12 回合：剩余回合 > 9 一定是 R2；
    否则用 NPC 目标分归的轮（识别层已按主数据区间核对、两位矛盾时给 None）；
    NPC 判不了而剩余回合正好 9：R2 开考是 12，只能是 R1 刚开考（Windows 第三局 R1 开考帧 NPC 读成 [599562, 303083]）。
    都判不了给 None，调用方退回 KAA。"""
    if turns is not None and turns > 9:
        return 2
    if npc_round in (1, 2):
        return npc_round
    return 1 if turns == 9 else None


class HiroHifStrategy(HifGrindStrategy):
    recorder: Recorder | None = None
    session: Session | None = None  # run_hif 注入；None 时自动建一个（trace 仍会写）
    use_llm: bool = True  # run_hif 的 --no-llm 会关掉，只用规则与兜底
    exams_done: int = 0  # 已完成的选拔 / 本战考试场数（进程内跨重启保留）
    lessons_done: int = 0
    main_attr: str | None = None  # run_hif 的 --main-attr：Vo / Da / Vi；支援卡编成识别到位前靠它指定
    llm_spec: str | None = None  # run_hif 的 --llm：claude:sonnet / openai:<model>，None 读 HIRO_LLM

    def __init__(self, controller: "ProduceController") -> None:
        super().__init__(controller)
        if HiroHifStrategy.recorder is None:
            HiroHifStrategy.recorder = Recorder()
        if HiroHifStrategy.session is None:
            from hiro.contracts import TraceWriter

            HiroHifStrategy.session = Session(TraceWriter())
        self.rec = HiroHifStrategy.recorder
        self.s = HiroHifStrategy.session
        logger.info("Decision log: %s；trace: %s", self.rec.dir, self.s.trace.dir)
        solution = produce_solution().data
        self.scenario = solution.mode
        self.profile = profile_for(solution.idol)
        llm = from_spec(self.llm_spec) if self.use_llm else None
        self.brain = BattleBrain(self.profile, llm)
        # 特別指導：执行器有了（游戏执行-04），但定制页 cards 识别还没有，进去也只能直接结束 → 暂不进候选（选行动按候选顺序兜底）
        self.deciders = ProduceDeciders(self.profile, llm, main_attr=self.main_attr, unavailable_options=self.UNAVAILABLE_OPTIONS,
                                          change_controlled=True)  # 决策会话的培育决策器；换卡已由 on_skill_card_change 按决策执行（GameState → Decision）
        # 续跑：从同一局之前的 trace 段（<基名>、<基名>-rN）恢复本局已拿到的卡，决策器据此判断必拿卡是否已齐（牌组识别接入前的兜底）
        from hiro.kaa_env import MEMORY_CARDS

        self.deciders.picked_musts |= set(MEMORY_CARDS) | self._picked_cards_from_trace()  # 回忆带的卡算已有
        self._load_finals_context()
        self._seed_produce_from_trace()
        # 同一进程内崩溃重启（run_hif 的 _supervised_resume）时沿用已完成的场次；跨进程续跑用 run_hif --exams-done
        self.exam_count = HiroHifStrategy.exams_done
        self.lesson_count = HiroHifStrategy.lessons_done
        self.advisor = self._exam_advisor(llm)
        self.rec.log("strategy_init", idol=solution.idol, profile=self.profile.idol_id if self.profile else None,
                     llm=f"{llm.name}:{llm.model}" if llm else None)  # fmt: skip

    def _exam_advisor(self, llm):
        """考试出招插件。默认实现（RuleLLMAdvisor）用本局的偶像档案和同一个 LLM（--llm / --no-llm）；
        注册 / 环境变量 / 入口点给了别的实现就原样用它（返回 None，run_exam 自己取插件）。"""
        from hiro import plugins

        if not plugins.source("exam_advisor").startswith("default:"):
            return None
        from hiro.defaults.advisor import RuleLLMAdvisor

        adv = RuleLLMAdvisor(self.profile, llm)
        if llm is None:  # --no-llm：只用规则（RuleLLMAdvisor 收到 None 会去读 HIRO_LLM）
            adv.brain = BattleBrain(self.profile, None)
        return adv

    # ---------------------------------------------------------------- 选行动
    @staticmethod
    def _to_produce_action(action: Action, actions: list[ProduceAction]) -> ProduceAction | None:
        """契约 ProduceNext(option, attr, sp) → 当前可用的 KAA ProduceAction（精确匹配优先；SP 与普通不混判）。"""
        if action.kind is not ActionKind.PRODUCE_NEXT:
            return None
        want = (action.args.get("option"), action.args.get("attr"), action.args.get("sp"))
        for a in actions:
            o, at, sp = _PA.get(a, (a.value, None, None))
            if (o, at) == want[:2] and (o != "training" or bool(sp) == bool(want[2])):
                return a
        for a in actions:  # SP 判断与 KAA 识别不一致时退一步：同课程同属性
            o, at, _ = _PA.get(a, (a.value, None, None))
            if (o, at) == want[:2]:
                return a
        return None

    ACTION_WAIT_RETRIES = 3

    def on_action_select(self, ctx: "ActionSelectContext"):
        t0 = time.monotonic()
        obs = self.s.observe_complete("ACTION_SELECT", ACTION_REQUIRED)
        # 先等选行动页读完整（淡入结束、能点了）再开牌组一览（第二十局开局：页面刚出来就点，一览很晚才弹）
        self._maybe_review_deck()
        actions, objects = ctx.fetch_available_actions()
        d = self.deciders.decide("ACTION_SELECT", self.s.state)
        for _ in range(self.ACTION_WAIT_RETRIES):
            # 决策器异常 / 读数被拦（第十九局本战第 1 天 Da 2296 读成 3296 被不变量拦下 → 决策器缺属性抛异常 → Wait）：
            # 重新截图观测再决策，不把 Wait 交给 KAA（它会按 v0 规则自己选，第十九局因此盲进了 Vi 授業）
            if d.action.kind is not ActionKind.WAIT:
                break
            sleep(self.SELECT_RETRY_WAIT)
            device.screenshot()
            obs = self.s.observe_complete("ACTION_SELECT", ACTION_REQUIRED)
            actions, objects = ctx.fetch_available_actions()
            d = self.deciders.decide("ACTION_SELECT", self.s.state)
        if d.action.kind is ActionKind.WAIT:
            self._stop("ACTION_SELECT", f"选行动重读 {self.ACTION_WAIT_RETRIES} 次决策仍是 Wait（{d.reason}），不交给 KAA 盲选", obs=obs, decision=d)
        if d.action.kind is ActionKind.PRODUCE_NEXT and d.action.args.get("option") == "special_guidance":
            # 特別指導：KAA 没有这个行动，由我们的执行器进出（hiro.produce.special_guidance）
            self.s.record(obs, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
            if self._special_guidance(obs):
                return
            d = self.deciders.decide("ACTION_SELECT", self.s.state)  # 进不去：再决策一次，下面按候选走
            d = replace(d, warnings=[*d.warnings, "特別指導 没进去，改用候选"])
        choice = self._to_produce_action(d.action, actions)
        if choice is None and d.action.kind is ActionKind.PRODUCE_NEXT:
            # 首选执行不了（例如「特別指導」KAA 没有这个行动）：按决策器的候选顺序找第一个能执行的，不交给 KAA 的 v0 规则（它会回退成休息）
            alt = next(((c.action, a) for c in d.candidates if c.action != d.action
                        and (a := self._to_produce_action(c.action, actions)) is not None), None)  # fmt: skip
            self.s.recover("page_misjudged", f"选行动：{d.action} 不在 KAA 可用行动里")
            if alt is not None:
                d.warnings.append(f"首选 {d.action} 无法执行（KAA 可用 {[a.value for a in actions]}），改用候选 {alt[0]}")
                choice = alt[1]
            else:
                d.warnings.append(f"决策 {d.action} 与全部候选在 KAA 可用行动 {[a.value for a in actions]} 里都找不到，交给 KAA")
        self.rec.log("action_select", image=self.s.img, available=[str(a) for a in actions], choice=str(choice), source=d.source, reason=d.reason)
        logger.info("选行动 %s ← %s：%s", choice, d.source, d.reason)
        if choice is None:
            self.s.record(obs, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
            return super().on_action_select(ctx)
        taps = [[int(o.rect.center.x), int(o.rect.center.y)] for a, o in zip(actions, objects) if a == choice][:1]
        result = self._act(lambda: ctx.commit(choice), d.action, page_before="ACTION_SELECT", taps=taps)
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    MAX_CUSTOMIZE_STEPS = 4

    def _special_guidance(self, obs, *, entered: bool = False) -> bool:
        """进入特別指導 → 每步观测定制页 → 决策 special_guidance（CustomizeCard / CustomizeEnd）→ 执行 → 离开。返回是否进去过。"""
        from hiro.produce import special_guidance as sg

        res = sg.enter(dict(self.s.state.page_fields)) if not entered else None
        if res is not None and not res.ok:
            self.s.recover("tap_no_effect", f"特別指導 没进去：{res.error}")
            logger.warning("特別指導 没进去：%s", res.error)
            return False
        for _ in range(self.MAX_CUSTOMIZE_STEPS):
            t0 = time.monotonic()
            # 进入后有「移動中」转场（q11：转场帧上读卡，判「可定制的卡未识别」直接结束，之后退不出来空转 3 分钟）：等到卡格读出来再决策
            o = None
            for _w in range(12):
                o = self.s.observe(scene="SPECIAL_GUIDANCE")
                if self.s.state.page_fields.get("cards"):
                    break
                # 续跑 / 定制后停在菜单页或提示上（q12-r1：菜单页读不到卡格，决策判「卡未识别」直接结束，少做一次定制）：先退回一览
                if not sg._to_grid(sg._runner(None, None, None, time.monotonic, time.sleep, None, True), tries=3):
                    time.sleep(1.0)
            d = self.deciders.decide("SPECIAL_GUIDANCE", self.s.state)
            logger.info("特別指導 %s ← %s：%s", d.action, d.source, d.reason)
            if d.action.kind is not ActionKind.CUSTOMIZE_CARD:
                self.s.record(o, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
                break
            a = d.action.args
            card_index = a.get("card_index", a.get("index"))
            item_index = a.get("item_index", 0)
            # 一张卡一次特別指導只能实行一次（之后回不来升级），多级目标在菜单里一次做完（第十五局 えいえいおー 只做了 1 级）
            r = sg.customize(int(card_index), int(item_index), levels=int(a.get("levels") or 1), cards=self.s.state.page_fields.get("cards"))
            self.s.record(o, decision=d, execution=r, timings_ms={"total": (time.monotonic() - t0) * 1000})
            if not r.ok:
                self.s.recover("tap_no_effect", f"特別指導 定制失败：{r.error}")
                if r.page_after != "SPECIAL_GUIDANCE":
                    return True  # 执行器已退出
                break
        out = sg.leave()
        if not out.ok:
            self._stop("SPECIAL_GUIDANCE", f"特別指導 退不出来：{out.error}")
        return True

    def _act(self, commit, action: Action, *, page_before: str, taps: list[list[int]], expect: set[str] | None = None,
             trust_commit: bool = True) -> ExecutionResult:  # fmt: skip
        """执行 + 校验 + 点击未生效重试。commit 由 KAA 上下文完成（它查坐标、过确认框，多数会等按钮消失才返回）。

        - trust_commit：commit 正常返回就算生效（只适用于会等按钮消失才返回的 KAA commit：选行动、相談、轮间）。
          页面名可能不变（H.I.F 公開レッスン结果叠在选行动页上，签名仍判 ACTION_SELECT），这时只记说明不重试。
          授業 / 外出的 commit 只双击一次不等结果，必须 trust_commit=False。
        - 重试时 commit 抛异常（例如 KAA「Action … is not available now」= 画面已经变了）视为上一次已生效，停止重试，不让整局崩溃。
        """
        result = None
        for attempt in range(TAP_RETRIES + 1):
            returned = False
            try:
                commit()
                returned = True
            except TimeoutError as e:  # KAA wait_disappear 超时：按钮还在
                self.s.note(f"commit 超时：{e}")
            except (ValueError, RuntimeError) as e:
                if attempt == 0:
                    raise
                self.s.note(f"第 {attempt + 1} 次 commit 失败（{e}），视为上一次已生效")
                return replace(result, ok=True, error=f"重试时 {e}", retries=attempt) if result else result
            result = self.s.verify(action, page_before=page_before, taps=taps, expect=expect, retries=attempt, left_ok=returned and trust_commit)
            if result.ok:
                return result
            if returned and trust_commit:
                self.s.note(f"{action}：KAA commit 已返回但页面名仍是 {page_before}（结果画面叠在原页面上），按已生效处理")
                return replace(result, ok=True, expected_transition=None, error=None)
            self.s.recover("tap_no_effect", f"{action} 第 {attempt + 1} 次执行后页面仍是 {page_before}")
            device.screenshot()
        return result

    # ---------------------------------------------------------------- 三选一
    def _confirm_acquire(self, tries: int = 3) -> int:
        """KAA 的 commit 只在找到灰色「受け取る」时才点；选中后按钮变橙色就退出，画面停在三选一（KAA 判 IDLE）。
        这里补点亮着的「受け取る」（ops.confirm_acquire，等按钮区变化而不是固定睡），返回点击次数。"""
        clicks = ops.confirm_acquire(tries)
        if clicks:
            self.s.recover("tap_no_effect", f"KAA commit 后「受け取る」未按下，补点 {clicks} 次")
        return clicks

    def _pick_index(self, d: Decision, n: int, fallback: int) -> int:
        """决策 → 要点的候选序号。ExcludeCard / Reroll 还没有 KAA 操作支持：退到候选里第一个「拿」的动作。"""
        if d.action.kind is ActionKind.ACTIVATE_EFFECT and d.action.args.get("pick_indexes"):
            i = int(d.action.args["pick_indexes"][0])
            if 0 <= i < n:
                return i
        alt = next((int(c.action.args["pick_indexes"][0]) for c in d.candidates
                    if c.action.kind is ActionKind.ACTIVATE_EFFECT and c.action.args.get("pick_indexes")), fallback)  # fmt: skip
        if alt != fallback:
            d.warnings.append(f"{d.action} 暂不支持执行，改拿决策候选里的第 {alt} 项")
        return alt if 0 <= alt < n else fallback

    MAX_REROLLS_PER_PAGE = 3
    exclude_failures = 0  # カード除去 连续失败次数（进程内）；≥2 后本局不再尝试，直接再抽選
    _rerolls_this_page = 0
    REROLL_CONFIRM = ("再抽選する", "決定", "はい", "OK")
    REROLL_BUTTON = (611, 1096)  # 720x1280，三选一右下「再抽選」（实机帧实测）

    def _reroll(self, obs, d: Decision, t0: float) -> bool:
        """三选一点「再抽選」（KAA 没有这个操作）。按钮用整帧 OCR 找（底部右侧），可能的确认框按文字点。返回是否已刷新。"""
        left = self.s.state.page_fields.get("rerolls_left")
        if left == 0:
            d.warnings.append("刷新次数已用完（识别为 0），改为拿卡")
            return False
        img = device.screenshot()
        btn = next((t for t in ocr.ocr() if ("抽選" in t.text or "再抽" in t.text) and t.rect.center.y > 900 and t.rect.center.x > 360), None)
        if btn is not None:
            pos = (int(btn.rect.center.x), int(btn.rect.center.y))
        elif left:  # OCR 常读不出这个按钮：识别到还有次数时按实测位置点（与左侧「カード除去」(133,1096) 对称）
            pos = self.REROLL_BUTTON
        else:
            d.warnings.append("没找到「再抽選」按钮且刷新次数未识别，改为拿卡")
            return False
        device.click(*pos)
        end = time.monotonic() + 4.0
        while time.monotonic() < end:
            sleep(0.8)
            device.screenshot()
            hit = next((t for t in ocr.ocr() if t.text.strip() in self.REROLL_CONFIRM and t.rect.center.y > 600), None)
            if hit:
                device.click(hit.rect)
                break
        sleep(1.5)
        self._rerolls_this_page += 1
        logger.info("选卡 再抽選（本页第 %d 次）← %s：%s", self._rerolls_this_page, d.source, d.reason)
        result = ExecutionResult(action=d.action, ok=True, taps=[list(pos)], waited_s=0.0,
                                 page_before="SELECT_CARD", page_after="SELECT_CARD", expected_transition=True, retries=0, error=None,
                                 seconds=round(time.monotonic() - t0, 3))  # fmt: skip
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})
        return True

    SELECT_RETRIES = 4  # 三选一候选为空时重读几次（每次等 SELECT_RETRY_WAIT 秒，约 2–3 秒）
    SELECT_RETRY_WAIT = 0.5
    PDRINK_DECIDE_RETRIES = 2  # 饮料溢出页决策没给出 DiscardDrink（异常兜底成 Wait）时重新观测再决策几次

    def on_select_card(self, ctx: "CardSelectContext"):
        from hiro.vision.extract import extract

        t0 = time.monotonic()
        img = device.screenshot()
        options = ctx.fetch_cards()
        for _ in range(3):  # 弹窗入场动画中 KAA 可能一张都找不到（IDLE 补判进来时尤其常见）
            if options:
                break
            sleep(1)
            img = device.screenshot()
            options = ctx.fetch_cards()
        if not options:
            self.s.recover("missing_fields", "三选一页 KAA 没找到候选卡，回主循环重新观测")
            logger.warning("三选一页没找到候选卡，跳过本次，等下一轮")
            return
        recommend = ctx.fetch_recommend_card()
        rec_index = next((i for i, o in enumerate(options) if recommend and o.rect == recommend.rect), None)
        raw, _ = extract("SELECT_CARD", img)
        for _ in range(self.SELECT_RETRIES):  # 识别层候选为空（淡入帧）：等一下重读，同 on_select_drink
            if (raw or {}).get("candidates"):
                break
            sleep(self.SELECT_RETRY_WAIT)
            img = device.screenshot()
            options = ctx.fetch_cards() or options
            raw, _ = extract("SELECT_CARD", img)
        raw = dict(raw or {})
        raw["recommended"] = rec_index
        obs = self.s.observe(scene="SELECT_CARD", img=img, fields=raw)
        for _ in range(2):  # 页面切换防抖中字段没进 state（同 on_select_drink）：再观测一两次让页面落定
            if self.s.state.page_fields.get("candidates") or not raw.get("candidates"):
                break
            obs = self.s.observe(scene="SELECT_CARD", img=img, fields=raw)
        d = self.deciders.decide("SELECT_CARD", self.s.state, n_options=len(options))
        self._note_fallback("SELECT_CARD", d)
        if d.action.kind is ActionKind.REROLL and self._rerolls_this_page < self.MAX_REROLLS_PER_PAGE:
            if self._reroll(obs, d, t0):
                return  # 候选换了：主循环重新 observe → 再决策
        if d.action.kind is ActionKind.EXCLUDE_CARD and HiroHifStrategy.exclude_failures >= 2:
            d = replace(d, warnings=[*d.warnings, "カード除去 本局已连续失败，改用再抽選"])
            if self._rerolls_this_page < self.MAX_REROLLS_PER_PAGE and self._reroll(obs, replace(d, action=Action(ActionKind.REROLL)), t0):
                return
        elif d.action.kind is ActionKind.EXCLUDE_CARD and self.s.state.page_fields.get("excludes_left") != 0:
            from hiro.produce.card_exclude import exclude_card

            k = int(d.action.args.get("pick_index", 0))
            res = exclude_card(k, candidates=[(int(o.rect.center.x), int(o.rect.center.y)) for o in options])
            logger.info("选卡 カード除去 第 %d 张 ← %s：%s（%s）", k, d.source, d.reason, "成功" if res.ok else res.error)
            self.s.record(obs, decision=d, execution=res, timings_ms={"total": (time.monotonic() - t0) * 1000})
            if res.ok:
                HiroHifStrategy.exclude_failures = 0
                return  # 候选变了：主循环重新 observe → 再决策
            HiroHifStrategy.exclude_failures += 1
            # 除去不成：决策本意是不要这些候选，改用再抽選（还有次数时），不退成拿一张不想要的卡
            if self._rerolls_this_page < self.MAX_REROLLS_PER_PAGE and self._reroll(obs, replace(d, action=Action(ActionKind.REROLL)), t0):
                return
            d = replace(d, warnings=[*d.warnings, f"カード除去失败（{res.error}），改为拿卡"])
        i = self._pick_index(d, len(options), -1)
        if not 0 <= i < len(options):  # 决策和社区兜底都没给出可拿的卡：停下，不拿第 0 张（用户规则 3c）
            self._stop("SELECT_CARD", f"决策没给出可拿的候选（{d.action}，来源 {d.source}）", obs=obs, decision=d)
        self._rerolls_this_page = 0
        choice = options[i]
        self.rec.log("select_card", image=img, options=[_card_summary(o.card) for o in options], recommended=_card_summary(recommend.card) if recommend else None,
                     choice=_card_summary(choice.card), source=d.source, reason=d.reason)  # fmt: skip
        logger.info("选卡 %s ← %s：%s", choice.card.name if choice.card else "?", d.source, d.reason)
        def commit():
            # 先走快速提交（点卡 → 等「受け取る」亮 → 点 → 等消失），亮不起来才退回 KAA commit + 补点
            if not ops.fast_acquire((int(choice.rect.center.x), int(choice.rect.center.y))):
                ctx.commit(choice)
                self._confirm_acquire()

        result = self._act(commit, Action.activate_effect(i), page_before="SELECT_CARD",
                           taps=[[int(choice.rect.center.x), int(choice.rect.center.y)]])  # fmt: skip
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    def _stage(self) -> str:
        mode = "qualify" if self.scenario == HifScenario.QUALIFY else "main"
        return f"{mode}，已完成 {self.exam_count} 场考试"

    POUCH_NAME = {"red": "ポーチ（赤）", "green": "ポーチ（緑）", "yellow": "ポーチ（黄）"}

    PLUSH_BY_HUE = {"green": "インコ", "blue": "ロボ", "yellow": "うさぎ"}  # 实机帧：インコ绿 / ロボ蓝 / うさぎ黄；もじゃ待样本
    PITEM_SLOTS_X = (147, 360, 573)
    POUCH_KANJI = {"red": "赤", "green": "緑", "yellow": "黄"}

    import re as _re

    _PLUSH_TITLE = _re.compile(r"^(ロボ|もじゃ|インコ|うさぎ|くま|人形)[（(](赤|緑|黄)[）)]$")  # 第二段：顶部是已定制的毛绒、无装饰前缀
    _DECO_TITLE = _re.compile(r"(花|リボン|メダル|羽)(ロボ|もじゃ|インコ|うさぎ|くま|人形)[（(](赤|緑|黄)[）)]")

    def _deco_options(self, options: list[dict]) -> list[dict]:
        """第二段定制（花 / リボン / メダル / 羽）：画面只显示选中项的名称，逐格点一下、OCR 下方说明栏标题拿全名（「花ロボ（黄）」）。"""
        out = []
        for i, x in enumerate(self.PITEM_SLOTS_X):
            device.click(x, 880)
            sleep(0.9)
            device.screenshot()
            name = next((m.group(0).replace("(", "（").replace(")", "）") for t in ocr.ocr() if (m := self._DECO_TITLE.search(t.text))), None)
            base = options[i] if i < len(options) else {"slot": i}
            out.append({**base, "slot": i, "name": name})
        logger.info("定制候选（装饰）：%s", [o["name"] for o in out])
        return out

    def _plush_options(self, img, options: list[dict]) -> list[dict]:
        """定制第一段（ぬいぐるみ）：每格中心 80x80 取高饱和像素的主色 → 种类；名称拼成「ロボ（黄）」供决策器解析（ロボ > もじゃ）。"""
        import cv2
        import numpy as np

        frame = cv2.resize(img, (720, 1280)) if img.shape[:2] != (1280, 720) else img
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        pouch = self.s.state.page_fields.get("pouch_color") or next((o.get("color") for o in options if o.get("color")), "yellow")
        out = []
        for i, x in enumerate(self.PITEM_SLOTS_X):
            roi = hsv[840:920, x - 40 : x + 40]
            h = roi[..., 0][(roi[..., 1] > 110) & (roi[..., 2] > 110)]
            counts = {"yellow": int(((h >= 18) & (h <= 35)).sum()), "green": int(((h >= 40) & (h <= 85)).sum()),
                      "blue": int(((h >= 90) & (h <= 130)).sum())}  # fmt: skip
            hue = max(counts, key=counts.get) if max(counts.values()) > 1500 else None
            plush = self.PLUSH_BY_HUE.get(hue)
            base = options[i] if i < len(options) else {"slot": i}
            name = f"{plush}（{self.POUCH_KANJI.get(pouch, '黄')}）" if plush else None
            out.append({**base, "slot": i, "name": name, "plush_hue": hue})
        logger.info("定制候选：%s", [o["name"] for o in out])
        return out

    def on_select_pitem(self, ctx):
        """H.I.F P 道具三选一：识别 pitems（颜色）→ 决策器（社区：黄 > 緑 > 赤）→ kaa_patches.pitem_commit 点包 + 受け取る。"""
        from hiro.vision.extract import extract

        t0 = time.monotonic()
        img = device.screenshot()
        raw, _ = extract("SELECT_PITEM", img)
        raw = dict(raw or {})
        # 识别给颜色、决策器按名称解析：选拔①后的背包阶段补上名称（定制阶段的道具名待识别提供）
        texts = [t.text for t in ocr.ocr()]
        if any(self._PLUSH_TITLE.search(t) for t in texts):  # 选拔③后第二段定制（装饰）：逐格点开读名称
            raw["options"] = self._deco_options(raw.get("options") or [])
        elif any("カスタム" in t for t in texts):  # 选拔②后第一段定制：按毛绒颜色认种类（识别正式支持前的最小实现）
            raw["options"] = self._plush_options(img, raw.get("options") or [])
        else:
            raw["options"] = [{**it, "name": it.get("name") or self.POUCH_NAME.get(it.get("color"))} for it in raw.get("options") or []]
        obs = self.s.observe(scene="SELECT_PITEM", img=img, fields=raw)
        for _ in range(2):  # 页面切换还在防抖时字段没进 state（D39 第三十三局 seq 229，同 SELECT_CARD / SELECT_DRINK）：再观测让页面落定
            if self.s.state.page.name == "SELECT_PITEM" and self.s.state.page_fields.get("pitems"):
                break
            obs = self.s.observe(scene="SELECT_PITEM", img=img, fields=raw)
        n = len(raw.get("options") or []) or 3
        d = self.deciders.decide("SELECT_PITEM", self.s.state, n_options=n)
        self._note_fallback("SELECT_PITEM", d)
        i = int(d.action.args["pick_indexes"][0]) if d.action.args.get("pick_indexes") else -1
        if not 0 <= i < n:  # 认不出 / 没模板：停下，不选第一个（用户规则 3c）
            self._stop("SELECT_PITEM", f"决策没给出合法的 P 道具（{d.action}，来源 {d.source}）", obs=obs, decision=d)
        logger.info("P 道具 第 %d 个 ← %s：%s", i, d.source, d.reason)
        result = self._act(lambda: ctx.commit(i), Action.activate_effect(i), page_before="SELECT_PITEM", taps=[])
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    DRINK_FULL_SOURCES = ("rule", "sim")

    def _drink_full_skip(self) -> bool:
        """饮料已满（「Pドリンク所持上限」，「受け取る」不可点）：勾「受け取らない」再按按钮。返回是否走了这条路。"""
        boxes = ocr.ocr()
        if not any("所持上限" in t.text for t in boxes):
            return False
        t0 = time.monotonic()
        obs = self.s.observe(scene="SELECT_DRINK")
        # 用户规则（2026-11-01）：饮料满不能默认放弃新饮料，要走换饮料决策。新饮料不比持有的强 → 决策不收（勾受け取らない）；
        # 新饮料更好 → 需要先在底栏丢一瓶再领取，这一步还没有执行器 → 停下；持有 / 新饮料认不出 → 停下
        from dataclasses import replace as dc_replace

        from hiro.brain import drinks as dk

        pf = self.s.state.page_fields
        cands = [c for c in pf.get("candidates") or [] if c.get("drink_id")]
        held = pf.get("held")  # 识别 0f58dc4：底栏已有饮料 [{index, drink_id, name, center}]
        produce = self.s.state.produce or {}
        # 用户 2026-11-02：按「换进来之后的持有组合」挑候选（hiro.brain.drinks），不再用单瓶静态排名
        best = dk.best_incoming(cands, held, dk.stage_of(produce.get("mode")), produce.get("drink_capacity") or max(4, len(held or [])))
        d = self.deciders.decide("PDRINK_MAX", dc_replace(self.s.state, page_fields={"incoming": best, "held": held}) if best else self.s.state)
        if d.source not in self.DRINK_FULL_SOURCES:
            # 规则 / 模拟偏离（> 2 SE，第二十一局 06:01 source=sim 被当成「认不出」停机）都是有效决策；兜底 / LLM 才说明认不出
            self._stop("SELECT_DRINK", f"饮料已满（所持上限），持有或新饮料认不出，不能判断换不换：{d.reason}", obs=obs, decision=d)
        if d.action.args.get("receive") and d.action.args.get("discard"):
            # 先在底栏丢掉决策选的那瓶，再领取最好的候选（游戏执行 5cb96a4；读不出就一下都不点）
            from hiro.produce.drink_swap import discard_then_receive

            slot = best.get("slot", cands.index(best)) if best else None
            res = discard_then_receive(int(d.action.args["discard"][0]), int(slot), held=held)
            logger.info("选饮料：已满，丢第 %s 瓶、领取 %s ← %s（%s）", d.action.args["discard"][0], best.get("name"), d.reason,
                        "成功" if res.ok else res.error)  # fmt: skip
            self.s.record(obs, decision=d, execution=res, timings_ms={"total": (time.monotonic() - t0) * 1000})
            if not res.ok:
                self._stop("SELECT_DRINK", f"饮料已满、决策要换，但「先丢再领」没完成：{res.error}", decision=d)
            return True
        box = next((t for t in boxes if "受け取らない" in t.text), None)
        if box is not None:
            device.click(box.rect)
            sleep(0.8)
        btn = next((t for t in ocr.ocr() if t.text.strip() in ("受け取る", "決定", "受け取らない") and t.rect.center.y > 1050), None)
        if btn is not None:
            device.click(btn.rect)
        sleep(1.5)
        result = ExecutionResult(action=d.action, ok=True, taps=[], waited_s=0.0, page_before="SELECT_DRINK", page_after=None,
                                 expected_transition=True, retries=0, error=None, seconds=round(time.monotonic() - t0, 3))  # fmt: skip
        self.s.record(obs, decision=d, execution=result)
        self._skip_incoming_drink = True  # 随后的「報酬スキップ」确认是这一步的结果，由 on_pdrink_max_confirm 放行
        logger.info("选饮料：已满，勾「受け取らない」跳过")
        return True

    UNAVAILABLE_OPTIONS = ()  # 特別指導 已接入（识别 26675e4 + 执行 c9ede96，hif._special_guidance）

    def _load_finals_context(self) -> None:
        """续跑：从同一局之前的 trace 段读回本战 R1 记下的牌组 / 饮料 / 倍率（hiro.state.finals_context.CONTEXT），轮间决策用。"""
        import re

        from hiro.state.finals_context import CONTEXT_FILE, load_context

        for d in self._prior_trace_dirs():
            if (d / CONTEXT_FILE).exists():
                load_context(d)
                logger.info("续跑：读回本战上下文 %s", d / CONTEXT_FILE)

    def _r1_done_in_trace(self) -> bool:
        """本局（当前和之前的 trace 段）已经出现过本战 R1 的轮次结果页（HIF_FINALS_RANKING kind=round_result）。"""
        try:
            dirs = self._prior_trace_dirs()
        except Exception:  # noqa: BLE001  没有 trace（测试替身）就当没打过
            return False
        for d in dirs:
            f = d / "steps.jsonl"
            if not f.exists():
                continue
            for line in f.open(encoding="utf-8"):
                if '"kind": "round_result"' in line and "HIF_FINALS_RANKING" in line:
                    return True
        return False

    def _prior_trace_dirs(self) -> list[Path]:
        """同一局之前的 trace 段：<基名>、<基名>-rN（不含当前目录），再加上当前目录里已有的步骤（run_hif resume --trace 同名续写，
        D43 第三十三局本战）。按时间顺序，当前目录排最后。"""
        import re

        cur = Path(self.s.trace.dir)
        base = re.sub(r"-r\d+$", "", cur.name)
        dirs = [d for d in sorted(cur.parent.glob(base + "*")) if d != cur and re.fullmatch(re.escape(base) + r"(-r\d+)?", d.name)]
        steps = cur / "steps.jsonl"
        if steps.exists() and steps.stat().st_size > 0:
            dirs.append(cur)
        return dirs

    def _seed_produce_from_trace(self) -> None:
        """续跑：画面还没读到 P / 体力 / 模式（续跑停在相談等不读头部的页面）时，用之前 trace 段最后一个有 produce 的状态打底
        （D43：第三十三局本战续跑进相談，state.produce 全空，决策只能不买）。来源记 infer（优先级最低），画面一读到就覆盖。"""
        import json

        from hiro.contracts import Source

        last, last_seq = None, 0
        for d in self._prior_trace_dirs():
            try:
                with (d / "steps.jsonl").open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            step = json.loads(line)
                        except ValueError:
                            continue
                        st = step.get("state_after") or step.get("state") or {}
                        if st.get("produce"):
                            last, last_seq = st, int(step.get("seq") or 0)
            except OSError:
                continue
        if last is None:
            return
        cur = self.s.state
        produce = {**last["produce"], **{k: v for k, v in (cur.produce or {}).items() if v is not None}}
        sources = {**{f"produce.{k}": Source("infer", last_seq) for k in last["produce"]}, **cur.sources}
        mode = cur.mode if cur.mode != "unknown" else last.get("mode", cur.mode)
        self.s.state = replace(cur, produce=produce, sources=sources, mode=mode)
        p = last["produce"]
        self.s.note(f"续跑：用之前 trace 的最后状态打底（seq {last_seq}：P {p.get('produce_point')}、体力 {p.get('stamina')}/{p.get('max_stamina')}），"
                    "画面读到就覆盖")  # fmt: skip
        logger.info("续跑：produce 用之前 trace 的最后状态打底（seq %d）", last_seq)

    def _deck_size(self) -> int | None:
        """状态机记账的牌组张数（选卡对话框多屏拼接核对用，识屏 0d1136c：拼出张数与它差 >2 就 complete=False、取消不交决策）；
        牌组没记账时 None（不核对）。"""
        deck = (self.s.state.produce or {}).get("deck") or []
        return len(deck) or None

    def _picked_cards_from_trace(self) -> set[str]:
        """同一局之前各 trace 段（<基名>、<基名>-rN）按事件记账得到的现有卡（hiro.loop.ledger：拿卡 / 买卡 / 换入 + 回忆，减去换出 / 删除）。"""
        import re

        from hiro.kaa_env import MEMORY_CARDS
        from hiro.loop.ledger import owned_cards, steps_of

        dirs = self._prior_trace_dirs()
        if not dirs:
            return set()
        owned = owned_cards(steps_of(dirs), MEMORY_CARDS)
        logger.info("续跑：从之前的 trace 记账恢复现有卡 %d 种", len(owned))
        return set(owned)

    PDRINK_RETRIES = 3  # 饮料溢出页决策给不出（Wait）时重读几次
    PDRINK_RETRY_WAIT = 0.5

    def on_pdrink_max(self, ctx):
        """饮料满（课程 / 考试奖励溢出页）：决策器（新饮料优于最差一瓶才换）→ drink_swap.resolve_overflow；决策不换时走 KAA 默认（留旧、跳过新）。
        替换执行失败不落到 KAA 默认（D41）：只在看得到的已有饮料里重新决策一次，还不行停下。"""
        from hiro.produce import drink_swap
        from hiro.produce.drink_swap import resolve_overflow

        t0 = time.monotonic()
        obs = self.s.observe(scene="PDRINK_MAX")
        d = self.deciders.decide("PDRINK_MAX", self.s.state)
        for _ in range(self.PDRINK_DECIDE_RETRIES):
            # 只有明确的 DiscardDrink（换 / 不换）才往下走。决策器异常会兜底成 Wait（第十八局 20:23:29：None 崩溃 → Wait → 落到 KAA 默认
            # 点 Leave，新饮料被跳过）：重新观测、重新决策，仍不行就停机，不交给 KAA
            if d.action.kind is ActionKind.DISCARD_DRINK:
                break
            logger.warning("饮料溢出：决策给 %s（%s），重新观测后再决策", d.action, d.reason)
            sleep(self.SELECT_RETRY_WAIT)
            device.screenshot()
            obs = self.s.observe(scene="PDRINK_MAX")
            d = self.deciders.decide("PDRINK_MAX", self.s.state)
        if d.action.kind is not ActionKind.DISCARD_DRINK:
            self._stop("PDRINK_MAX", f"决策重试 {self.PDRINK_DECIDE_RETRIES} 次仍没给出换 / 不换（{d.action}，{d.reason}）", obs=obs, decision=d)
        for attempt in range(self.PDRINK_SWAP_RETRIES + 1):
            discard = d.action.args.get("discard") or []
            if not (discard and d.action.args.get("receive")):
                break  # 决策是不要新饮料：走下面 KAA 的跳过
            pf = self.s.state.page_fields
            recv = d.action.args.get("receive_indexes")
            res = resolve_overflow([int(i) for i in discard], receive_indexes=recv, action=d.action, held=pf.get("held"), incoming=pf.get("incoming"))
            logger.info("饮料溢出：丢第 %s 瓶、收新饮料 %s ← %s：%s（%s）", discard, recv if recv is not None else "最下面那瓶", d.source, d.reason,
                        "成功" if res.ok else res.error)  # fmt: skip
            self.s.record(obs, decision=d, execution=res, timings_ms={"total": (time.monotonic() - t0) * 1000})
            if res.ok:
                return
            # D41 第三十三局 18:18：要丢的那瓶在列表视口外（最后一行被切掉），执行失败后落到 KAA 默认「残す」= 丢掉新饮料（特製ハツボシエキス）。
            # 改为：视口外的已有饮料标成点不到（决策不会丢它），重新观测、重新决策一次；还不行就停下，不交给 KAA
            if attempt >= self.PDRINK_SWAP_RETRIES:
                self._stop("PDRINK_MAX", f"饮料替换 {attempt + 1} 次都没完成（{res.error}），不落到 KAA 默认（会丢新饮料）", obs=obs, decision=d)
            self.s.recover("tap_no_effect", f"饮料替换没完成（{res.error}），只在画面上看得到的已有饮料里重新决策")
            sleep(self.SELECT_RETRY_WAIT)
            device.screenshot()
            obs = self.s.observe(scene="PDRINK_MAX")
            self._mark_offscreen_held(drink_swap.LAST_ROWS.get("owned"))
            d = self.deciders.decide("PDRINK_MAX", self.s.state)
            if d.action.kind is not ActionKind.DISCARD_DRINK:
                self._stop("PDRINK_MAX", f"替换失败后重新决策没给出换 / 不换（{d.action}，{d.reason}）", obs=obs, decision=d)
        else:  # pragma: no cover  循环里成功 return、失败 _stop
            return
        self.s.record(obs, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
        # 决策是不要新饮料：点 KAA 的跳过，随后的「報酬スキップ」确认由 on_pdrink_max_confirm 放行
        self._skip_incoming_drink = True
        return super().on_pdrink_max(ctx)

    PDRINK_SWAP_RETRIES = 1  # 饮料替换执行失败后（只在看得到的已有饮料里）重新决策、再执行几次

    def _mark_offscreen_held(self, visible_rows: int | None) -> None:
        """溢出页「手持ちのドリンク」只看到前 visible_rows 行（列表没滚、最后一行被视口切掉）：后面的已有饮料标 visible=False，
        决策（_drink_overflow）不会选它丢。"""
        if visible_rows is None:
            return
        pf = self.s.state.page_fields
        held = pf.get("held") or []
        if not any(h.get("index", i) >= visible_rows for i, h in enumerate(held)):
            return
        marked = [{**h, "visible": False} if h.get("index", i) >= visible_rows else h for i, h in enumerate(held)]
        self.s.state = replace(self.s.state, page_fields={**pf, "held": marked})
        self.s.note(f"PDRINK_MAX：已有饮料只看得到前 {visible_rows} 行，其余标为点不到")

    def on_select_drink(self, ctx: "DrinkSelectContext"):
        if self._drink_full_skip():
            return
        t0 = time.monotonic()
        data = ctx.fetch_select_drink()
        obs = self.s.observe(scene="SELECT_DRINK")
        for _ in range(self.SELECT_RETRIES):
            # 弹窗淡入时读的帧候选为空（第十八局选拔第 1 天差し入れ：读到空 → 决策 Wait → 停机；同一画面稍后三瓶都认得出）：等一下重读。
            # 判断用决策实际读的 state.page_fields，不用 obs.fields：页面切换还在防抖时 obs 有候选但字段没进 state（第十八局本战 22:02）
            if self.s.state.page_fields.get("candidates"):
                break
            sleep(self.SELECT_RETRY_WAIT)
            device.screenshot()
            data = ctx.fetch_select_drink()
            obs = self.s.observe(scene="SELECT_DRINK")
        if not self.s.state.page_fields.get("candidates"):
            self.s.recover("missing_fields", f"SELECT_DRINK 重读 {self.SELECT_RETRIES} 次候选仍为空（或页面防抖未结束）")
        d = self.deciders.decide("SELECT_DRINK", self.s.state, can_skip=bool(data.can_skip), n_options=len(data.drinks))
        self._note_fallback("SELECT_DRINK", d)
        if d.action.kind is ActionKind.ACTIVATE_EFFECT and data.drinks:
            i = min(int(d.action.args["pick_indexes"][0]), len(data.drinks) - 1)
            drink, act = data.drinks[i], Action.activate_effect(i)
        elif data.can_skip:
            drink, act = None, d.action
            self._skip_incoming_drink = True  # 决策不拿：随后的「報酬スキップ」确认是这一步的结果（main8 实机误停）
        else:  # 不能跳过、决策也没选：不默认拿第 1 瓶（用户规则 3c），停下
            self._stop("SELECT_DRINK", f"不能跳过，但决策没选饮料（{d.action}，来源 {d.source}）", obs=obs, decision=d)
        def commit():
            if drink is not None:
                # 快速提交（点饮料 → 等「受け取る」亮 → 点 → 等消失），亮不起来才退回 KAA commit（固定 sleep 0.5 + 2s 再循环）
                from kaa.tasks.produce.new.page import POSTIONS

                idx = getattr(drink, "index", None)
                r = POSTIONS[idx] if isinstance(idx, int) and 0 <= idx < len(POSTIONS) else None
                if r is not None and ops.fast_acquire((int(r.center.x), int(r.center.y))):
                    self._confirm_acquire()  # 万一按钮还在（没关掉）再补点
                    return
            ctx.commit(data, drink)
            if drink is not None:
                self._confirm_acquire()

        result = self._act(commit, act, page_before="SELECT_DRINK", taps=[])
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    # ---------------------------------------------------------------- 授業 / 外出 / 相談 / 轮间
    def _event_page(self, page: str, commit_index, n_options: int | None = None):
        t0 = time.monotonic()
        obs = self.s.observe(scene=page)
        kaa_n = n_options
        choices = self.s.state.page_fields.get("choices") or []
        if n_options is not None and len(choices) > n_options:
            # 第二十五局本战授業：3 项里第 3 项「声の細かいチェック」被下方对话框挡住一半，KAA fetch_options 只认出 2 项。
            # 选项数按识别层的 choices；选到 KAA 没认出的那项时按识别的 center 点（同 KAA commit：点两下 = 选中再确认）
            n_options = len(choices)
            kaa_commit = commit_index

            def commit_index(i):  # noqa: F811
                if i < kaa_n:
                    return kaa_commit(i)
                c = next((c for c in choices if c.get("index") == i and c.get("center")), None)
                if c is None:
                    return False
                x, y = c["center"]
                for _ in range(2):
                    device.click(int(x), int(y))
                    sleep(0.5)
                return True

            self.s.note(f"{page}：KAA 认出 {kaa_n} 项，识别层 {len(choices)} 项（有选项被挡住），按识别层")
        d = self.deciders.decide(page, self.s.state, n_options=n_options)
        self._note_fallback(page, d)
        i = int(d.action.args.get("select", -1)) if d.action.kind is ActionKind.EVENT_SELECT else -1
        if d.action.kind is not ActionKind.EVENT_SELECT or (n_options is not None and not 0 <= i < n_options):
            # 用户规则（3c）：兜底不选第 0 项。决策器自己会按社区模板兜底；到这里说明兜底也没给出合法选项 → 停下
            self._stop(page, f"决策没给出合法选项（{d.action}，共 {n_options} 项，来源 {d.source}）", obs=obs, decision=d)
        # KAA 的 Study/OutingContext.commit 只双击一次、不等按钮消失；实机第 6 天双击只选中没确认，所以这里不信任 commit 返回，
        # 页面没变就再双击（第二次点在已选中的项上即确认）
        result = self._act(lambda: commit_index(i), Action.event_select(i), page_before=page, taps=[], trust_commit=False)
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    def on_study(self, ctx: "StudyContext"):
        self._event_page("STUDY", ctx.commit, len(ctx.fetch_options()))

    def on_outing(self, ctx: "OutingContext"):
        self._event_page("OUTING", ctx.commit, len(ctx.fetch_options()))

    # ---------------------------------------------------------------- 主动查看牌组（state-events §6）
    _reviewed_at_exam: int | None = None

    def _maybe_review_deck(self) -> None:
        """pending_review 含牌组、每场考试之间第一次到选行动页、或调试模式（同一考试段只看一次）时，打开牌组一览读全量，
        作为 DECK_REVIEW 事件写入。读不全只记 pending，不下结论；没关掉一览就停下。"""
        import os

        # 牌组为空（本战开局：本战牌组来自选拔，开局时 produce.deck 是空的；第十九局保护卡 / 必拿因此按空牌组算）：一定读一次。
        # 其余情况仍由 HIRO_DECK_REVIEW 开关（q7 实机 find_deck_button 误开行动记录，识屏已修，礼盒图标约 (558,1200)）
        empty = not (self.s.state.produce or {}).get("deck")
        due = self._reviewed_at_exam != self.exam_count
        if not (empty and due):
            if os.environ.get("HIRO_DECK_REVIEW") != "1":
                return
            pending = "produce.deck" in self.s.state.pending_review
            if not (pending or due):
                return
        from hiro.contracts import Event
        from hiro.produce.deck_review import review_deck

        def ocr_texts(img):
            return [(t.text, (int(t.rect.center.x), int(t.rect.center.y))) for t in ocr.ocr()]

        from hiro.produce import deck_review

        if deck_review.SAVE_DIR is None and ops.CALIB_DIR is not None:
            deck_review.SAVE_DIR = ops.CALIB_DIR.parent / "deck-list"  # 一览页帧留档（reports/live/deck-list，核对识别用）
        t0 = time.monotonic()
        r = review_deck(shoot=device.screenshot, click=device.click,
                        swipe=lambda x1, y1, x2, y2, d: device.swipe(x1, y1, x2, y2, d), ocr_texts=ocr_texts)  # fmt: skip
        self._reviewed_at_exam = self.exam_count
        seq = self.s.trace.next_seq()
        if r["deck"] is not None:
            self.s.emit(Event(seq=seq, kind="DECK_REVIEW", origin="review", args={"deck": r["deck"], "deck_size": r["deck_size"]},
                              basis="observe_only", confirmed=True))  # fmt: skip
            # 观测 DECK_LIST：reduce 把 deck 整体写进 produce.deck（识屏 0146646 约定）
            self.s.observe(scene="DECK_LIST", area="produce", img=None, fields=dict(r.get("page") or {"deck": r["deck"], "deck_size": r["deck_size"]}))
            logger.info("主动查看牌组：%d 张（%.1fs）", len(r["deck"]), time.monotonic() - t0)
        else:
            self.s.note(f"主动查看牌组没读全：{'；'.join(r['problems'])}")
            logger.warning("主动查看牌组没读全：%s", "；".join(r["problems"]))
        if r["opened"] and not r["closed"]:
            self._stop("DECK_LIST", "主动查看牌组后一览没关掉")

    # ---------------------------------------------------------------- 兜底总开关（用户规则 3f）
    def _stop(self, page: str, why: str, *, obs=None, decision: Decision | None = None):
        """没有接管 / 读不出 / 决策不合法：存帧、记 recover:unhandled、报警，停止运行。绝不交给 KAA 默认（弃赛刷奖励的 commit(0)）。"""
        if obs is None:
            obs = self.s.observe(scene=page, fields=False, save_frame=True)
        else:
            self.s.observe(scene=page, fields=False, save_frame=True)
        self.s.recover("unhandled", f"{page}：{why}")
        self.s.record(obs, decision=decision)
        logger.error("未接管的页面 / 钩子：%s：%s。已存帧，停止运行", page, why)
        raise UnhandledPage(f"{page}：{why}。已存帧，停止运行，需要补逻辑")

    def _note_fallback(self, page: str, d: Decision) -> None:
        """决策器走了社区模板兜底（source=fallback）：记 recover:llm_invalid，理由取 warnings 第一条。"""
        if d.source == "fallback":
            self.s.recover("llm_invalid", f"{page}：{(d.warnings or [d.reason])[0]}")

    def on_practice_entered(self, ctx):
        self._stop("PRACTICE", "H.I.F 不应出现练习课程页（KAA 默认只打日志后交给它自己的出牌）")

    def on_practice_exited(self):
        pass  # 不会进 PRACTICE（进了已经停下）

    def on_allowance(self, ctx):
        """活動支給：领取后出现的选饮料 / 选卡由 on_select_drink / on_select_card 决策；这里只领取并记录。"""
        t0 = time.monotonic()
        obs = self.s.observe(scene="ALLOWANCE")
        act = Action(ActionKind.CONTINUE, {"allowance": "claim"})
        ctx.claim()
        result = self.s.verify(act, page_before="ALLOWANCE", taps=[], stay_ok=True)
        self.s.record(obs, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    def on_pdrink_max_confirm(self, ctx):
        """「報酬スキップ」确认：只有本局最近一次 PDRINK_MAX 决策是「不要新饮料」时才确认，否则停下（不让 KAA 默认确认放弃奖励）。"""
        t0 = time.monotonic()
        obs = self.s.observe(scene="PDRINK_MAX_CONFIRM")
        d = self.deciders.decide("PDRINK_MAX_CONFIRM", self.s.state)
        # 决策器：上一次 PDRINK_MAX 决定不收新饮料才确认；饮料替换执行失败改为跳过（_skip_incoming_drink）也确认
        if not (d.action.args.get("confirm") or getattr(self, "_skip_incoming_drink", False)):
            # 没有「放弃新饮料」的决策记录（例如续跑：记录在上一个进程里）：点キャンセル 回到饮料页，由选饮料 / 饮料溢出重新决策；
            # 连续两次都没有记录才停下
            self._confirm_unknown = getattr(self, "_confirm_unknown", 0) + 1
            if self._confirm_unknown >= 2:
                self._stop("PDRINK_MAX_CONFIRM", f"「報酬スキップ」确认连续出现且没有放弃新饮料的决策（{d.reason}）", obs=obs, decision=d)
            device.click(*REWARD_SKIP_CANCEL)
            self.s.recover("dialog", "「報酬スキップ」确认没有对应决策：点キャンセル 回到饮料页重新决策")
            self.s.record(obs, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
            return
        self._confirm_unknown = 0
        from kaa.tasks.produce.shared.common import ProduceInterrupt

        act = d.action
        ProduceInterrupt._check_pdrink_max_confirm(device.screenshot())
        self._skip_incoming_drink = False
        result = self.s.verify(act, page_before="PDRINK_MAX_CONFIRM", taps=[])
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    def on_date_change(self, ctx):
        """日期变更（凌晨 5 点）：KAA 点确认 → 回标题 → 进游戏 → 再開培育（不经过破弃对话框）。记录一次 recover:dialog。"""
        from kaa.tasks.produce.shared.common import acquisition_date_change_dialog

        obs = self.s.observe(scene="DATE_CHANGE", fields=False, save_frame=True)
        self.s.recover("dialog", "日期变更弹窗：确认后回标题并再開培育")
        result = acquisition_date_change_dialog()
        self.s.record(obs)
        if result is None:
            self._stop("DATE_CHANGE", "判成日期变更，但没找到日期变更对话框")

    def on_produce_end(self, ctx):
        """培育结算：用 resume.finish_produce_end（只点次へ / 閉じる 等，遇到「開始確認 / 本戦へ」就停），不用 KAA 默认的乱点 click_center。"""
        from hiro.loop.resume import finish_produce_end

        obs = self.s.observe(scene="PRODUCE_END", fields=False, save_frame=True)
        self.s.record(obs)
        finish_produce_end()
        ctx.controller.abort()

    def _card_event(self, page: str, ctx, executor_name: str):
        """强化 / 删卡事件：观测卡格 → 决策 → 按 card_id 执行（游戏执行-06）→ 记录。决策器或执行器还没有、读不出、决策取消执行失败 → 停下。"""
        from hiro.brain import deciders as deciders_mod
        from hiro.loop import kaa_patches

        t0 = time.monotonic()
        if page not in deciders_mod.PAGES:
            self._stop(page, "决策器还没有这个页面（决策-08）")
        kind = "enhance" if page == "SKILL_CARD_ENHANCE" else "remove"
        failed = HiroHifStrategy.op_failed.setdefault(kind, set())

        def read():
            """观测卡格 → 一屏放不下时读整副 → 去掉本局做不成的卡 → 按画面有没有「キャンセル」填 cancelable（False = 必选，决策 512e3c3）。"""
            for _ in range(self.CHANGE_OBSERVE_TRIES):
                o = self.s.observe(scene=page)
                if self.s.state.page_fields.get("cards"):
                    break
                time.sleep(0.5)
            self._scan_change_deck(kaa_patches.CARD_OP_REGION[kind], page)  # 一屏放不下时读整副交给决策（执行按 card_id 翻屏找）
            pf = self.s.state.page_fields
            cards = [c for c in pf.get("cards") or [] if c.get("card_id") not in failed] if failed else pf.get("cards")
            # cancelable 以识别填的为准（识屏 e226c6b：底部按钮判据）；识别没给（None）时用这里看到的「キャンセル」补
            cancelable = pf.get("cancelable")
            if cancelable is None:
                cancelable = self._card_op_cancel() is not None
            self.s.state = replace(self.s.state, page_fields={**pf, "cards": cards, "cancelable": cancelable})
            return o, cancelable

        obs, cancelable = read()
        d = self.deciders.decide(page, self.s.state)
        for _ in range(self.CARD_WAIT_REREADS):
            if d.action.kind is not ActionKind.WAIT:
                break
            # 必选页（没有キャンセル）卡格没读到 / 认出的都不能强化：决策等重新识别（D42），重读再决策，不盲选
            self.s.record(obs, decision=d)
            time.sleep(self.CARD_WAIT_INTERVAL)
            obs, cancelable = read()
            d = self.deciders.decide(page, self.s.state)
        if d.action.kind is ActionKind.WAIT:
            self._stop(page, f"必选页重读 {self.CARD_WAIT_REREADS} 次决策仍在等待：{d.reason}", obs=obs, decision=d)
        card_id = d.action.args.get("card_id") or d.action.args.get("remove_card_id")
        if d.action.args.get("cancel") or not card_id:
            if not cancelable:  # 画面没有キャンセル（必选）而决策取消：停下，不盲选
                self._stop(page, f"决策没给出要操作的卡，画面也没有「キャンセル」：{d.reason}", obs=obs, decision=d)
            # D42 第三十三局本战：决策说没有值得操作的卡 → 点「キャンセル」退回（相談 / 上一页），不停机
            btn = self._card_op_cancel()
            if btn is not None:
                device.click(btn)
            else:  # 识别说有、这一帧 OCR 没读到：按固定位置点（识屏：11 个样本都在 x≈165、y≈1148）
                device.click(*self.CARD_OP_CANCEL_XY)
            result = self.s.verify(d.action, page_before=page, taps=[], retries=0)
            self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})
            logger.info("%s：%s → 点キャンセル 退回", page, d.reason)
            return
        execute = getattr(kaa_patches, executor_name, None)
        if execute is None:
            self._stop(page, f"执行器 {executor_name} 还没有（游戏执行-06）", obs=obs, decision=d)
        done = execute(ctx, card_id)
        result = self.s.verify(d.action, page_before=page, taps=[], retries=0)
        result = replace(result, ok=bool(done), error=None if done else f"{executor_name} 未完成")
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})
        if not done:
            failed.add(card_id)
            if len(failed) <= 2:
                self.s.recover("tap_no_effect", f"{executor_name}({card_id}) 没完成，本局不再选它，重新决策")
                return self._card_event(page, ctx, executor_name)
            # 连续做不成（相談的強化 / 削除对话框，识别读不出「+」时会把满级卡逐张试一遍）：有「キャンセル」就退回，不停机
            cancel = next((t for t in ocr.ocr() if "キャンセル" in t.text and t.rect.center.y > 1050), None)
            if cancel is not None:
                device.click(cancel.rect)
                self.s.recover("tap_no_effect", f"{executor_name} 连续 {len(failed)} 张做不成，点キャンセル 退回（{sorted(failed)}）")
                logger.warning("%s 连续做不成，点キャンセル 退回", executor_name)
                return
            self._stop(page, f"{executor_name}({card_id}) 没完成（读不出 / 选中不符）", decision=d)

    CARD_WAIT_REREADS = 3  # 必选强化 / 删卡页决策等待（卡格没读到）时重读几次
    CARD_WAIT_INTERVAL = 1.0

    CARD_OP_CANCEL_XY = (165, 1148)  # 强化 / 删卡对话框「キャンセル」的固定位置（720x1280）

    @staticmethod
    def _card_op_cancel():
        """强化 / 删卡对话框底部的「キャンセル」（y > 1050）；没有（礼物强化等必须选一张）或读不了返回 None。"""
        try:
            return next((t.rect for t in ocr.ocr() if "キャンセル" in t.text and t.rect.center.y > 1050), None)
        except Exception as e:  # noqa: BLE001  OCR 出错当没看到
            logger.warning("找「キャンセル」时 OCR 出错：%r", e)
            return None

    def on_skill_card_removal(self, ctx):
        self._card_event("SKILL_CARD_REMOVAL", ctx, "remove_card")

    def on_skill_card_enhance(self, ctx):
        self._card_event("SKILL_CARD_ENHANCE", ctx, "enhance_card")

    # ---------------------------------------------------------------- 授業セレクトチェンジ 换卡
    CHANGE_OBSERVE_TRIES = 4

    def _stage1_column(self, pick: int, d: Decision) -> int:
        """决策的 pick_index 是识别 candidates 的序号；执行侧 change_stage1 按「第几列」点。用候选卡面中心 x 换算成列
        （qualify6 r2 实机：识别多读了一张重复卡，决策选 #3 せのびの魔法，执行去找第 3 列，超时空转）。"""
        from hiro.loop.kaa_patches import CHANGE1_MARK_X

        cands = self.s.state.page_fields.get("candidates") or []
        c = next((c for c in cands if c.get("index") == pick), None)
        if not c or not c.get("center"):
            return pick if 0 <= pick < len(CHANGE1_MARK_X) else 0
        cols = [x + 10 for x in CHANGE1_MARK_X]  # 卡面中心在角标右侧约 10px（实机帧：223 / 362 / 500）
        col = min(range(len(cols)), key=lambda k: abs(c["center"][0] - cols[k]))
        if col != pick:
            d.warnings.append(f"候选 #{pick}（中心 x={c['center'][0]}）对应第 {col} 列")
        return col

    CHANGE_EMPTY_REREADS = 3  # 换卡阶段 2 卡格读成空时再读几次（每次隔 1s）

    def _scan_change_deck(self, region=None, page: str = "换卡阶段 2") -> bool:
        """换卡阶段 2：牌组一屏放不下时往下滑读整副（kaa_patches.scan_dialog_deck，拼完滑回顶部），整副交给决策。
        决策 da6c8e5：第十八、十九局对话框只读到一屏 12 张（牌组 22 张），只能在可见的牌里选。返回是否用了多屏整副。
        拼接不可信时不替换（保留这一屏），在 page_fields 标 deck_complete=False / deck_screens，决策据此当「张数不可信」。"""
        from hiro.loop.kaa_patches import CHANGE2_CARD_AREA, scan_dialog_deck

        pf = self.s.state.page_fields
        if not pf.get("cards"):
            return False
        try:
            scan = scan_dialog_deck(region or CHANGE2_CARD_AREA, expected=self._deck_size())
        except Exception as e:  # 扫描出错：用这一屏
            logger.warning("%s 整副扫描出错：%r", page, e)
            return False
        if scan["screens"] <= 1:
            self.s.state.page_fields = {**pf, "deck_complete": True, "deck_screens": 1}
            return False
        if scan["complete"]:
            self.s.state.page_fields = {**pf, "cards": scan["cards"], "deck_complete": True, "deck_screens": scan["screens"]}
            self.s.note(f"{page}：滑动读整副 {len(scan['cards'])} 张（{scan['screens']} 屏）")
            return True
        self.s.state.page_fields = {**pf, "deck_complete": False, "deck_screens": scan["screens"]}
        self.s.recover("missing_fields", f"{page} 整副拼接不可信（{scan['problems']}），只用第一屏 {len(pf['cards'])} 张")
        return False

    CHANGE1_DECIDE_RETRIES = 3  # 换卡阶段 1 拿不到换卡决策时再读几帧（等页面提交进状态），之后才退回第 0 张
    CHANGE1_DECIDE_WAIT = 0.4

    def on_skill_card_change(self, ctx):
        """换卡：阶段 1 按决策选换入候选（候选未识别时选第 0 张），阶段 2 按决策的 card_id 换出；决策取消时点キャンセル。
        执行在 kaa_patches.change_stage1 / change_stage2（核心卡保护、认不出就取消）。KAA 原版固定换第 0 张，第 4 局因此丢了 デイドリーミング。"""
        cancel = False
        if ctx.stage == 1:
            t0 = time.monotonic()
            # 换卡页刚出现的第一帧：识别已经认出候选，但页面要连续两帧才提交进状态，状态里还是上一页（获得饮料 / 饮料已满）的字段，
            # 决策器读不到候选只能回 Wait，下面就会盲换第 0 张（Windows 第二局两次、Mac A5b 一次，2026-11-06）。再读几帧等状态提交。
            for attempt in range(1 + self.CHANGE1_DECIDE_RETRIES):
                obs = self.s.observe(scene="SKILL_CARD_CHANGE_1")
                d = self.deciders.decide("SKILL_CARD_CHANGE_1", self.s.state)
                if d.action.kind is ActionKind.CHANGE_CARD:
                    break
                if attempt < self.CHANGE1_DECIDE_RETRIES:
                    self.s.record(obs, decision=d, timings_ms={"total": (time.monotonic() - t0) * 1000})
                    time.sleep(self.CHANGE1_DECIDE_WAIT)
            args = d.action.args if d.action.kind is ActionKind.CHANGE_CARD else {}
            cancel = bool(args.get("cancel"))
            pick = int(args.get("pick_index") or 0)
            if d.action.kind is not ActionKind.CHANGE_CARD:
                d.warnings.append("候选未识别，换入第 0 张（阶段 2 仍按决策换出 / 取消）")
            pick = self._stage1_column(pick, d)
            ok = ctx.commit_stage1(pick)  # 自带轮询到阶段 2；不走 _act 的重复点击
            result = self.s.verify(d.action, page_before="SKILL_CARD_CHANGE_1", taps=[], retries=0)
            if ok is False:
                result = replace(result, ok=False, error="阶段 1 超时没进入阶段 2")
            self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})
        t0 = time.monotonic()
        from hiro.vision.scene import classify_current

        for _ in range(self.CHANGE_OBSERVE_TRIES):  # 阶段 2 卡格有入场动画：读不到卡格就再读一帧
            # 先核对画面确实是第 2 步（q6 r2 #33：阶段 1 超时后强制按第 2 步读，读到的是第 1 步画面，卡格为空）
            try:
                now = classify_current().scene
            except Exception:  # 没有设备（离线测试）等：无法核对，照常读
                now = "unknown"
            if now not in ("SKILL_CARD_CHANGE_2", "unknown"):
                time.sleep(0.5)
                continue
            obs = self.s.observe(scene="SKILL_CARD_CHANGE_2")
            if self.s.state.page_fields.get("cards"):
                break
            time.sleep(0.5)
        else:
            obs = self.s.observe(scene="SKILL_CARD_CHANGE_2", fields=False)
        if not self.s.state.page_fields.get("cards"):
            # 卡格读成空：可能是上一次点「チェンジ」后晚弹出来的确认框挡着（第二十局 03:21:58：读成 [] → 判不换 → 点了确认框的キャンセル，
            # 多花约 80 秒）。是我们自己的换卡确认框就点「決定」（换哪张上一次已按决策选好）；否则再重读几次，不直接取消
            from hiro.loop.kaa_patches import confirm_change_dialog

            try:
                late = confirm_change_dialog(device.screenshot())
            except Exception:  # 没有设备（离线测试）/ 判断出错：当作没有确认框
                late = False
            if late:
                self.s.recover("dialog", "换卡确认框晚弹出：点「決定」完成上一次的换卡")
                result = self.s.verify(Action(ActionKind.CHANGE_CARD, {"stage": 2, "confirm_late": True}), page_before="SKILL_CARD_CHANGE_2",
                                       taps=[], retries=0)  # fmt: skip
                self.s.record(obs, execution=replace(result, ok=True), timings_ms={"total": (time.monotonic() - t0) * 1000})
                return
            for _ in range(self.CHANGE_EMPTY_REREADS):
                time.sleep(1.0)
                obs = self.s.observe(scene="SKILL_CARD_CHANGE_2")
                if self.s.state.page_fields.get("cards"):
                    break
        scanned = self._scan_change_deck()
        d = self.deciders.decide("SKILL_CARD_CHANGE_2", self.s.state)
        args = d.action.args if d.action.kind is ActionKind.CHANGE_CARD else {"cancel": True}
        if cancel:
            d.warnings.append("阶段 1 决策已判定不换，阶段 2 取消")
        cancel = cancel or bool(args.get("cancel"))
        if cancel:
            # 阶段 1 没有取消键，阶段 2 取消后可能回到阶段 1（游戏执行-06 推断）：同一次换卡只取消 1 次；
            # 第 2 次改由执行侧换一张非保护卡（pick_change_target 跳过核心卡），还回来就停下，避免来回打转
            self._change_cancels = getattr(self, "_change_cancels", 0) + 1
            if self._change_cancels >= 2:
                # 取消后又回到换卡页：游戏里阶段 1 没有取消键，取消只会绕回来（main8 实机死循环）。
                # 改按决策器 choose_card_to_change 换出价值最低的非保护卡（规则选出，不是执行侧盲选）；没有可换的才停下
                from hiro.brain.deciders import choose_card_to_change

                forced = choose_card_to_change(self.s.state.page_fields.get("cards") or [], None)
                if forced is None:
                    self._stop("SKILL_CARD_CHANGE_2", f"取消换卡后又回到换卡页，且没有可换出的非保护卡（第 {self._change_cancels} 次）", obs=obs, decision=d)
                cancel, args = False, {**args, "remove_card_id": forced}
                d.warnings.append(f"取消会绕回换卡页：改换出价值最低的非保护卡 {forced}")
                self.s.recover("tap_no_effect", f"换卡不能取消（阶段 1 无取消键），按规则换出 {forced}")
        else:
            self._change_cancels = 0
        card_id = None if cancel else args.get("remove_card_id")
        # 整副扫描过（多屏）时 remove_index 是牌组序号、不是这一屏的格子号：只按 card_id + 强化次数找（翻屏）
        changed = ctx.commit_stage2(0, card_id=card_id, cancel=cancel, remove_index=None if cancel or scanned else args.get("remove_index"),
                                    remove_upgrade=None if cancel else args.get("remove_upgrade"))  # 自带确认框处理；取消 / 不符时点キャンセル 返回 False  # fmt: skip
        result = self.s.verify(d.action, page_before="SKILL_CARD_CHANGE_2", taps=[], retries=0)
        result = replace(result, ok=bool(changed) or cancel, error=None if changed or cancel else "换卡未完成（见 kaa_patches 日志与 stage2 帧）")
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    def _end_page(self, page: str, commit):
        t0 = time.monotonic()
        obs = self.s.observe(scene=page)
        d = self.deciders.decide(page, self.s.state)
        if d.action.kind not in (ActionKind.SHOP_END, ActionKind.INTERVAL_END):
            d.warnings.append(f"{d.action} 还没有执行实现，本次仍直接离开")
        result = self._act(commit, d.action if d.action.kind in (ActionKind.SHOP_END, ActionKind.INTERVAL_END) else Action(ActionKind.SHOP_END),
                           page_before=page, taps=[])  # fmt: skip
        self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})

    MAX_SHOP_STEPS = 8

    CONSULT_P_REREADS = 2  # 相談页 P 点没进状态时重读几次

    def on_consult(self, ctx: "ConsultContext"):
        """相談：每步 observe → 决策器（github-d9）→ execute_shop（github-8f）→ 重新 observe，直到 ShopEnd。
        同一天的相談里（重新进入也算）记住：买失败的格子（确认框商品不符算 2 次，其他失败每次 1 次，满 2 次标成已购、不再选）、
        用过 / 打不开的强化 / 删除服务。某一步失败不离开，重新观测后继续；连续失败 3 次才离开（q12 第 12 天：
        货架第 0 格识别错名，买失败后直接离开；再进来又去点已用过的「強化」两次）。"""
        from hiro.produce.shop import execute_shop

        key = (self.s.state.produce or {}).get("step")
        if getattr(self, "_consult_key", object()) != key:
            self._consult_key, self._consult_fails, self._consult_done = key, {}, set()
        fails, done_services = self._consult_fails, self._consult_done
        streak = 0
        for step in range(self.MAX_SHOP_STEPS):
            t0 = time.monotonic()
            obs = self.s.observe_complete("CONSULT", ("shelf",), tries=4, interval=0.8)
            for _ in range(self.CONSULT_P_REREADS):  # 要用 P 判断买不买：读不到先重读（D43），还读不到由决策按「未识别」处理
                if (self.s.state.produce or {}).get("produce_point") is not None:
                    break
                sleep(self.SELECT_RETRY_WAIT)
                obs = self.s.observe(scene="CONSULT")
            blocked = {i for i, n in fails.items() if n >= 2}
            if blocked:  # 买不成的格子标成已购，决策不会再选
                pf = self.s.state.page_fields
                self.s.state.page_fields = {**pf, "shelf": [dict(it, purchased=True) if it.get("index") in blocked else it
                                                            for it in pf.get("shelf") or []]}  # fmt: skip
            d = self.deciders.decide("CONSULT", self.s.state)
            d = self._consult_open_dialog(d, done_services)
            action = d.action if step < self.MAX_SHOP_STEPS - 1 else Action(ActionKind.SHOP_END)
            logger.info("相談 第 %d 步 %s ← %s：%s", step + 1, action, d.source, d.reason)
            result = execute_shop(action, dict(self.s.state.page_fields), choose_card=self._choose_dialog_card, deck_size=self._deck_size())
            self.s.record(obs, decision=d, execution=result, timings_ms={"total": (time.monotonic() - t0) * 1000})
            service = ("upgrade" if action.kind is ActionKind.SHOP_UPGRADE_CARD else action.args.get("service")) if action.kind in (
                ActionKind.SHOP_UPGRADE_CARD, ActionKind.SHOP_BUY) else None  # fmt: skip
            if action.kind is ActionKind.SHOP_END:
                if result.ok:
                    return
                break
            if result.ok:
                streak = 0
                if service:
                    done_services.add(service)
                if action.kind is ActionKind.REROLL:
                    fails.clear()  # 新货架：失败计数按格子号记的，作废
                continue
            streak += 1
            if service and HiroHifStrategy.last_dialog_pick and HiroHifStrategy.last_dialog_pick[1] and "没打开选卡页" not in (result.error or ""):
                kind, cid = HiroHifStrategy.last_dialog_pick
                HiroHifStrategy.op_failed.setdefault(kind, set()).add(cid)
                HiroHifStrategy.last_dialog_pick = None
                self.s.recover("tap_no_effect", f"相談 {action} 选的 {cid} 做不成（{result.error}），本局不再选它，换下一张")
            elif service:  # 打不开 / 已用过：本次相談不再开这个服务
                done_services.add(service)
                self.s.recover("tap_no_effect", f"相談 {action} 未成功：{result.error}，本次相談不再开「{service}」")
            elif (idx := action.args.get("index")) is not None:
                fails[idx] = fails.get(idx, 0) + (2 if "确认框里是" in (result.error or "") else 1)
                self.s.recover("tap_no_effect", f"相談 {action} 未成功：{result.error}（第 {idx} 格失败 {fails[idx]} 次）")
            if streak >= 3:
                logger.warning("相談连续失败 %d 次，离开", streak)
                break
        end = execute_shop(Action(ActionKind.SHOP_END), dict(self.s.state.page_fields))
        if not end.ok:  # 不退回 KAA 的 ConsultContext.commit：它会先买第一格再离开
            self.s.recover("tap_no_effect", f"相談离开失败：{end.error}，交回主循环重新判断")
            logger.warning("相談离开失败（%s），交回主循环", end.error)

    def _consult_open_dialog(self, d: Decision, done: set) -> Decision:
        """牌组未识别时决策器不出强化 / 删卡（q9、q10 第 12 天 440P 没花）。选卡对话框里能读到整副牌组（识别 26675e4 + 执行 c9ede96），
        所以这时先打开对话框，由 enhance_card / remove_card 在对话框里选；决策不选会点キャンセル 退回。强化优先（第九局复盘：+24–30%）。"""
        if d.action.kind is not ActionKind.SHOP_END or not any("牌组未识别" in w for w in d.warnings):
            return d
        if "轮间留" in (d.reason or ""):
            return d  # 本战相談：决策要给轮间留 P 点，不强开对话框
        pf = self.s.state.page_fields
        services = pf.get("services") or {}
        p = (self.s.state.produce or {}).get("produce_point")
        for name in ("upgrade", "delete"):
            info = services.get(name) or {}
            price = info.get("price")
            if name in done or info.get("available") is False or (p is not None and price is not None and p < price):
                continue
            act = Action(ActionKind.SHOP_UPGRADE_CARD, {}) if name == "upgrade" else Action(ActionKind.SHOP_BUY, {"service": "delete"})
            return replace(d, action=act,
                           reason=f"牌组未识别：打开「{'強化' if name == 'upgrade' else '削除'}」对话框，在对话框里读整副牌组再由决策选卡（{d.reason}）",
                           warnings=[*d.warnings, f"总控兜底：打开 {name} 对话框选卡"])  # fmt: skip
        return d

    op_failed: dict = {}  # {"enhance"|"remove": {card_id}}：点了却做不成的卡（q10：届いて！+ 已满级，识别没读出「+」），本局不再选
    last_dialog_pick: tuple | None = None

    def _choose_dialog_card(self, kind: str, cards: list) -> str | None:
        """相談 / 轮间的強化 / 削除对话框：把整副卡格交给决策 enhance_card / remove_card，返回要点的 card_id（None = 不选，执行器点キャンセル）。"""
        page = "SKILL_CARD_ENHANCE" if kind == "enhance" else "SKILL_CARD_REMOVAL"
        failed = HiroHifStrategy.op_failed.setdefault(kind, set())
        cards = [c for c in cards if c.get("card_id") not in failed]
        state = replace(self.s.state, page_fields={**self.s.state.page_fields, "cards": cards})
        d = self.deciders.decide(page, state)
        HiroHifStrategy.last_dialog_pick = (kind, d.action.args.get("card_id"))
        logger.info("相談%s选卡 %s ← %s：%s", "强化" if kind == "enhance" else "删除", d.action.args.get("card_id"), d.source, d.reason)
        return None if d.action.args.get("cancel") else d.action.args.get("card_id")

    MAX_INTERVAL_STEPS = 12

    def on_hif_round_interval(self, ctx: "HifRoundIntervalContext"):
        """轮间：observe → 决策器 interval → execute_interval，一步一步直到 IntervalEnd（hiro.produce.interval.interval_loop）。
        某一步失败不退出（重新观测后继续）；买过 / 买不了的格子标成已购；离开前先按画面回满体力（R2 不自动回血）。"""
        from hiro.produce.interval import interval_loop

        self.rec.log("round_interval", image=device.screenshot())
        cur = {}

        def observe() -> dict:
            t = time.monotonic()
            cur["obs"] = self.s.observe_complete("HIF_ROUND_INTERVAL", ("interval",), tries=4, interval=0.8)
            cur["t0"] = time.monotonic()
            cur["observe_ms"] = (cur["t0"] - t) * 1000
            return dict(self.s.state.page_fields)

        def decide(pf: dict):
            self.s.state.page_fields = pf  # 带上本轮间已买 / 买不了的标记
            t = time.monotonic()
            d = self.deciders.decide("HIF_ROUND_INTERVAL", self.s.state)
            cur["decide_ms"] = (time.monotonic() - t) * 1000
            cur["d"] = d
            logger.info("轮间 %s ← %s：%s", d.action, d.source, d.reason)
            return d

        def record(d, result):
            # 轮间每步耗时（协调 2026-11-02）：观测 / 决策（含模拟）/ 执行；收尾的回体力、结束（d 为 None）只有执行
            tm = {"total": (time.monotonic() - cur.get("t0", time.monotonic())) * 1000, "execute": (result.seconds or 0.0) * 1000}
            if d is not None:
                tm.update(observe=cur.get("observe_ms", 0.0), decide=cur.get("decide_ms", 0.0))
            self.s.record(cur.get("obs") if d is not None else None, decision=d, execution=result, timings_ms=tm)
            if not result.ok:
                self.s.recover("tap_no_effect", f"轮间 {result.action} 未成功：{result.error}")

        # 离开前的体力目标：用户 2026-11-01，不用回满；広 有回血、エウレカ 在 HP ≤ 50% 才触发 → 兜底刚过半血。
        # 之后由决策按模拟给目标（profile.interval_hp_target）；没有就用刚过半血
        base = getattr(self.profile, "interval_hp_target", None) or (lambda m: m // 2 + 1)
        # 决策按模拟定了更高的目标（ProduceDeciders.interval_target_hp）时用它，前后口径一致（第十二局：决策 24、收尾 17）
        target = lambda m: max(base(m), min(m, getattr(self.deciders, "interval_target_hp", None) or 0))  # noqa: E731
        from hiro.produce.interval import screen_status

        def status():  # 画面读不到时用观测状态补（q10 轮间收尾：OCR 把「21/33」读成「21/3ろ」，目标检查没生效）
            pr = self.s.state.produce or {}
            return screen_status(fallback=lambda: (pr.get("stamina"), pr.get("max_stamina"), pr.get("produce_point")))

        from functools import partial

        from hiro.produce.interval import execute_interval

        execute = partial(execute_interval, deck_size=self._deck_size())
        if interval_loop(observe, decide, record, execute=execute, max_steps=self.MAX_INTERVAL_STEPS, hp_target=target, status=status):
            return
        self.s.recover("tap_no_effect", "轮间结束失败，交回 KAA 结束")
        ctx.end()  # 兜底：执行器结束不了时用 KAA 的结束流程

    # ---------------------------------------------------------------- 考试
    def _battle_label(self, ctx: "ExamContext") -> str:
        """KAA 把 H.I.F 的公開レッスン也当 EXAM 场景；用我们的场景识别区分：HIF_EXAM 才是选拔 / 本战。"""
        from hiro.vision.scene import classify_current

        if self.scenario != HifScenario.QUALIFY and self._r1_done_in_trace():
            # 本战 R1 结算过之后只剩 R2（中间只有轮间，没有レッスン）：不看场景直接是 r2。DMM dmm2（maindmm2-20261111-043916）：
            # 考试刚开始那一刻画面还在过场，classify_current 不是 HIF_EXAM，R2 四次挑战三次标成 lesson-N（不走 R2 预演 / 打法）
            self.exam_count += 1
            HiroHifStrategy.exams_done = self.exam_count
            return "r2"
        scene = classify_current().scene
        if scene != "HIF_EXAM" and self.scenario != HifScenario.QUALIFY:
            scene = self._settle_exam_scene(scene)
        if scene == "HIF_EXAM" and self.scenario != HifScenario.QUALIFY:
            try:
                from hiro.vision.hif import read_hif_exam

                f = read_hif_exam(device.screenshot())
            except Exception:
                f = {}
            self.exam_count += 1
            HiroHifStrategy.exams_done = self.exam_count
            if self._r1_done_in_trace():
                # R1 打完不会再考 R1：之后的考试一定是 R2（Windows 第十局 04:00 日切换后续跑进 R2 末回合，
                # NPC 读不到、剩余回合不是 9，退回 KAA 颜色判断成了 r1，再挑戦按 r1 计数又多点了一次）
                return "r2"
            rnd = finals_label_round(f.get("finals_round"), f.get("remaining_turns"))
            if rnd in (1, 2):
                return f"r{rnd}"
            return "r2" if ctx.is_final_exam() else "r1"
        if scene != "HIF_EXAM":
            self.lesson_count += 1
            HiroHifStrategy.lessons_done = self.lesson_count
            return f"lesson-{self.lesson_count}"
        self.exam_count += 1
        if self.scenario == HifScenario.QUALIFY:
            # 考试画面排名栏的突破线人数：64/128 → 选拔①、32/64 → ②、16/32 → ③。以画面为准（续跑时不必再手动给 --exams-done）
            from hiro.vision.hif import read_hif_exam

            try:
                participants = read_hif_exam(device.screenshot()).get("participants")
            except Exception:  # 读不到就沿用计数
                participants = None
            n = {128: 1, 64: 2, 32: 3}.get(participants)
            if n is not None and n != self.exam_count:
                logger.info("考试场次以画面为准：選抜試験%d（计数为 %d）", n, self.exam_count)
                self.exam_count = n
            HiroHifStrategy.exams_done = self.exam_count
            return f"qualify-{self.exam_count}"
        HiroHifStrategy.exams_done = self.exam_count
        return "r2" if ctx.is_final_exam() else "r1"

    EXAM_SCENE_SETTLE_S = 6.0  # 本战流程里考试开头认不出 HIF_EXAM 时，再看这么久才当成レッスン

    def _settle_exam_scene(self, scene: str, *, clock=time.monotonic, sleep=time.sleep) -> str:
        """考试刚开始的过场里 classify_current 认不出：本战流程再连看几秒，认出 HIF_EXAM 就用它（截图快的后端更容易撞上）。"""
        from hiro.vision.scene import classify_current

        deadline = clock() + self.EXAM_SCENE_SETTLE_S
        while scene != "HIF_EXAM" and clock() < deadline:
            sleep(0.5)
            scene = classify_current().scene
        return scene

    RETRY_CONFIRM = ("再挑戦する", "決定", "はい", "OK")

    def _retry_if_failed(self, label: str, *, timeout: float = 10.0) -> bool:
        """结算页玩家「敗退」时点「再挑戦」：免费 / 再挑戦券都用；确认框里出现ジュエル就取消（用户 2026-11-01 授权）。
        返回是否已发起再挑戦。"""
        from hiro.vision.hif import is_hif_result, read_hif_result

        deadline = time.monotonic() + timeout
        img = device.screenshot()
        while not is_hif_result(img):
            if time.monotonic() >= deadline:
                return False
            sleep(0.5)
            img = device.screenshot()
        # 结算名单有入场动画：玩家行的「突破 / 敗退」和分数要过一两秒才出来。读到 passed 为止（最多再等 8s）
        res = read_hif_result(img)
        settle = time.monotonic() + 8.0
        while res.get("passed") is None and time.monotonic() < settle:
            sleep(1.0)
            img = device.screenshot()
            res = read_hif_result(img)
        self.rec.log("exam_result", image=img, exam=label, result={k: res.get(k) for k in ("score", "rank", "passed", "exam_no", "rank_line")})
        self.s.observe(scene="HIF_EXAM_RESULT", img=img, fields=res)
        if res.get("passed") is not False:
            return False
        texts = ocr.ocr()
        btn = next((t for t in texts if button_text(t.text, "再挑戦") and t.rect.center.y > 900), None)
        if btn is None:
            self.s.note(f"{label} 敗退（{res.get('score')} Pt），没有再挑戦按钮，结束培育")
            logger.warning("%s 敗退，没有再挑戦按钮", label)
            return False
        kind = self._click_retry(label, btn, texts)
        if kind is None:
            return False
        logger.info("%s 敗退（%s Pt），使用再挑戦（%s）", label, res.get("score"), kind)
        self.s.note(f"{label} 敗退（{res.get('score')} Pt），使用再挑戦（{kind}）")
        # 再挑戦同一场考试：场次不加
        self.exam_restarted = True  # 主循环据此重新进入 on_exam_entered（HiroController._rearm_exam）
        self.exam_count -= 1
        HiroHifStrategy.exams_done = self.exam_count
        self.rec.log("exam_retry", exam=label, retry_kind=kind)
        return True

    exam_restarted = False  # 刚点了再挑戦、同一场考试要重新进入（见 HiroController._rearm_exam）
    _retry_comm_error = False  # 上一次点再挑戦遇到通信エラー（不算决定不再挑戦）
    _retry_declined: tuple | None = None  # 已决定不再挑戦的本战结算页（label, 总分, 本轮分）：同一页不再点开确认框
    RETRY_JEWEL = ("ジュエル",)
    RETRY_CONFIRM_WAIT = 15.0
    RETRY_CONFIRM_TAPS = 3
    retries_used: dict = {}  # {"free": n, "ticket": n}（进程内，复盘统计）

    MAX_RETRIES_PER_EXAM = 3  # 同一场考试最多再挑戦几次（含续跑前的段）：打法 / 引擎没改时重复挑戦只花时间

    def _retries_so_far(self, label: str) -> int:
        """本局（同一 trace 基名的各段）已对 label 用过几次再挑戦：数 trace 里的「使用再挑戦」说明。"""
        import json as _json
        import re

        cur = Path(self.s.trace.dir)
        base = re.sub(r"-r\d+$", "", cur.name)
        n = sum(1 for x in self.s.notes if "使用再挑戦" in x and label in x)
        for d in cur.parent.glob(base + "*"):
            f = d / "steps.jsonl"
            if not f.exists() or not re.fullmatch(re.escape(base) + r"(-r\d+)?", d.name):
                continue
            for line in f.open(encoding="utf-8"):
                if "使用再挑戦" in line:
                    try:
                        n += sum(1 for x in _json.loads(line).get("notes") or [] if "使用再挑戦" in x and (label in x or "本战" in x))
                    except ValueError:
                        pass
        return n

    def _click_retry(self, label: str, btn, texts, *, allow_ticket: bool = True) -> str | None:
        """点「再挑戦」并处理确认框（用户 2026-11-01 授权：免费 / 再挑戦券随便用，消耗ジュエル的绝不点）。
        返回 "free" / "ticket"，没有确认或要花ジュエル（已点キャンセル）时返回 None。"""
        left = screen_retries_left(texts)
        # 画面上「あと N回」是游戏自己的剩余次数，比数 trace 记录可靠：Windows 第七局通信エラー（ERR_AM_IDTOKEN_FAIL）期间
        # 记了 3 条没真正开始的「免费再挑戦」，按记录数会以为 3 次用完
        # 游戏上限与我们的上限（3）一致才对得上：读出的 N 比上限大（读错或规则变了）就不信，退回数 trace 记录（协调 2026-11-08）
        if left is not None and left > self.MAX_RETRIES_PER_EXAM:
            logger.warning("%s 画面「あと %d回」超过上限 %d，按 trace 记录计数", label, left, self.MAX_RETRIES_PER_EXAM)
            left = None
        used = self.MAX_RETRIES_PER_EXAM - left if left is not None else self._retries_so_far(label)
        if used >= self.MAX_RETRIES_PER_EXAM:
            self.s.note(f"{label} 已再挑戦 {used} 次，到上限 {self.MAX_RETRIES_PER_EXAM}，不再挑戦")
            logger.warning("%s 已再挑戦 %d 次（上限 %d），不再挑戦", label, used, self.MAX_RETRIES_PER_EXAM)
            return None
        free_hint = any("無料" in t.text for t in texts)
        device.click(btn.rect)
        # 确认框可能晚弹（第二十局 04:06 日期切换后：6 秒窗口过了才弹，没点「決定」就当成已再挑戦，主循环 UNKNOWN 60 秒）
        end = time.monotonic() + self.RETRY_CONFIRM_WAIT
        kind = "free" if free_hint else None
        clicks, last, tickets = 0, -1e9, None
        while time.monotonic() < end:
            sleep(0.8)
            device.screenshot()
            boxes = ocr.ocr()
            words = " ".join(t.text for t in boxes)
            if "通信" in words and ("エラー" in words or "リトライ" in words):
                # 点了再挑戦却弹出通信エラー（Windows 第七局 ERR_AM_IDTOKEN_FAIL）：这次没开始，不记账；弹窗交给 comm_error 处理
                self.s.note(f"{label} 点再挑戦后出现通信エラー：这次不算再挑戦")
                logger.warning("%s 点再挑戦后出现通信エラー，这次不算再挑戦", label)
                self._retry_comm_error = True  # 不是「决定不再挑戦」：恢复后同一页还要再判断
                return None
            if any(j in words for j in self.RETRY_JEWEL):
                cancel = next((t for t in boxes if "キャンセル" in t.text), None)
                if cancel is not None:
                    device.click(cancel.rect)
                self.s.note(f"{label} 再挑戦要消耗ジュエル：已取消（用户规则：不用ジュエル）")
                logger.warning("%s 再挑戦要消耗ジュエル，已取消", label)
                return None
            cost = retry_cost(boxes)
            if cost is not None:
                kind, tickets = cost, retry_tickets(boxes)
            elif "無料" in words:
                kind = "free"
            elif "券" in words:
                kind = "ticket"
            if kind == "ticket" and not allow_ticket:
                # 止损（RETRY_STOP_LOSS）：翻不了盘时不用券，点キャンセル（免费的照常用）
                cancel = next((t for t in boxes if "キャンセル" in t.text), None)
                if cancel is not None:
                    device.click(cancel.rect)
                self.s.note(f"{label} 再挑戦要用再挑戦チケット，但差距翻不了盘：已取消（RETRY_STOP_LOSS）")
                logger.warning("%s 再挑戦要用券，差距翻不了盘，已取消（RETRY_STOP_LOSS）", label)
                return None
            hit = next((t for t in boxes if any(button_text(t.text, w) for w in self.RETRY_CONFIRM) and t.rect.center.y > 600), None)
            if hit is None:
                if clicks:
                    break  # 点过确认、确认框已经关了
                continue
            # 第二十五局 R2：确认框淡入时点了「決定」没响应，以前点一下就当完成，确认框挂 46s。还在就隔一会儿再点（最多 3 次）
            if clicks >= self.RETRY_CONFIRM_TAPS or time.monotonic() - last < 1.5:
                continue
            device.click(hit.rect)
            clicks, last = clicks + 1, time.monotonic()
            if clicks > 1:
                logger.info("%s 再挑戦确认框点了「%s」还在，再点一次（第 %d 次）", label, hit.text.strip(), clicks)
        kind = kind or "free"
        HiroHifStrategy.retries_used[kind] = HiroHifStrategy.retries_used.get(kind, 0) + 1
        # 记账（协调 2026-11-04）：这一次用的是免费还是券、券还剩几张，写进日志和 trace（复盘报告读 note）
        what = {"free": "免费", "ticket": "再挑戦チケット"}[kind]
        left = f"，再挑戦チケット {tickets[0]} → {tickets[1]} 张" if tickets else "（所持张数没读到）"
        logger.info("%s 再挑戦用了%s%s", label, what, left)
        self.s.note(f"{label} 再挑戦：用了{what}{left}")
        return kind

    def _finals_retry(self, img, label: str) -> bool:
        """本战最终结果页（HIF_FINALS_RANKING final）敗退 / 没拿第 1，且有「再挑戦」时使用（免费 / 券；ジュエル不点）。"""
        from hiro.vision.pages import classify_page

        try:
            hit = classify_page(img)
        except Exception:
            return False
        if not hit or hit[0] != "HIF_FINALS_RANKING" or hit[1].get("kind") != "final":
            return False
        f = hit[1]
        if f.get("rank") == 1 or (f.get("outcome") and "敗退" not in str(f.get("outcome")) and f.get("passed") is not False):
            return False
        page_key = (label, f.get("score"), f.get("round_score"))
        if page_key == self._retry_declined:
            return False  # 这张结算页已经决定不再挑戦：别再点开确认框，让调用方点「次へ」收尾
        texts = ocr.ocr()
        btn = next((t for t in texts if button_text(t.text, "再挑戦") and t.rect.center.y > 900), None)
        if btn is None:
            return False
        hopeless = RETRY_STOP_LOSS and finals_hopeless(f)
        if hopeless:
            logger.info("本战差距翻不了盘（%s）：再挑戦只用免费次数，不用券（RETRY_STOP_LOSS）", hopeless)
        self._retry_comm_error = False
        kind = self._click_retry(label, btn, texts, allow_ticket=not hopeless)
        if kind is None:
            # Windows 第七局：止损取消后收尾循环每轮又点开再挑戦、再取消，187s 后空转看门狗停机，一直没点「次へ」
            if not self._retry_comm_error:
                self._retry_declined = page_key
            return False
        logger.info("本战敗退（第 %s 名，总分 %s），使用再挑戦（%s）", f.get("rank"), f.get("score"), kind)
        self.s.note(f"本战敗退（第 {f.get('rank')} 名，总分 {f.get('score')}），使用再挑戦（{kind}）")
        self.rec.log("exam_retry", exam=label, retry_kind=kind, finals=True)
        self.exam_restarted = True  # 主循环据此重新进入 on_exam_entered（HiroController._rearm_exam）
        return True

    def _record_round_result(self, img, label: str, seen: set) -> None:
        """本战 R1 / R2 打完后的「現在順位」弹窗（HIF_FINALS_RANKING kind=round_result / final）：读全员分数，存帧写进 trace。"""
        from hiro.vision.pages import classify_page

        try:
            hit = classify_page(img)
        except Exception:
            return
        if not hit or hit[0] != "HIF_FINALS_RANKING" or hit[1].get("kind") not in ("round_result", "final"):
            return
        f = hit[1]
        key = (f.get("kind"), f.get("finals_round"), f.get("score"), f.get("round_score"))
        if key in seen:
            return
        seen.add(key)
        obs = self.s.observe(scene="HIF_FINALS_RANKING", img=img, fields=f, save_frame=True)
        self.s.record(obs)
        self.rec.log("finals_round_result", image=img, exam=label, page_kind=f.get("kind"), **{k: f.get(k) for k in ("finals_round", "rank", "score", "round_score", "entries")})
        logger.info("本战轮次结果（%s）：第 %s 轮，我方第 %s 名，总分 %s，本轮得分 %s；全员 %s", f.get("kind"), f.get("finals_round"), f.get("rank"),
                    f.get("score"), f.get("round_score"), [(e.get("rank"), e.get("name"), e.get("score"), e.get("round_score")) for e in f.get("entries") or []])  # fmt: skip

    def on_exam_entered(self, ctx: "ExamContext"):
        label = self._battle_label(ctx)
        self.rec.log("exam_enter", image=device.screenshot(), exam=label)
        self.s.state.exam_session.label = label

        def ended() -> bool:
            # 先看「次へ」按钮模板（约 60ms），有了才 OCR 顶栏（约 1s）：考试中每轮都要判，第十六局本战每轮都先 OCR 了一次
            return bool(R.Common.ButtonNext.find() and not ocr.find(contains("残りターン"), rect=R.InProduce.BoxExamTop))

        run_exam(self.brain, label, self.rec, ended, session=self.s, advisor=self.advisor)
        self.rec.log("exam_end", image=device.screenshot(), exam=label)

        if self._retry_if_failed(label):
            return  # 已点「再挑戦」：考试重开，主循环会再次进入 on_exam_entered

        recorded_ranking = set()
        deadline = time.monotonic() + EXAM_END_LOOP_TIMEOUT
        for _ in Loop():
            if time.monotonic() > deadline:  # kotonebot Loop 不检查 timeout：main13 R1 后在日期切换弹窗下点「次へ」空转 5 分钟
                logger.warning("%s 考试收尾 %.0fs 还没走完，交回主循环重新判断", label, EXAM_END_LOOP_TIMEOUT)
                break
            before = device.screenshot()
            if label in ("r1", "r2") and not recorded_ranking:
                # 本战结果弹窗还没记到：入场动画中途的帧认不出，紧接着就点了「次へ」（Windows 第二局第三次再挑戦用完后
                # 结算没读到）。先等画面停稳再认，弹窗已记过就不再等
                settled = ops.wait_stable(timeout=EXAM_END_SETTLE).img
                if settled is not None:  # numpy 数组不能直接 or
                    before = settled
            self._record_round_result(before, label, recorded_ranking)  # 本战每轮结果弹窗：点「次へ」前存帧、记分（用户指示）
            if self._finals_retry(before, label):
                return  # 本战已点再挑戦：考试重开，主循环会再次进入 on_exam_entered
            if btn := R.Common.ButtonNext.find():
                device.click(btn.rect)
            elif btn := R.InProduce.TextRechallengeEndProduce.find():
                device.click(btn.rect)
                self.rec.log("exam_failed", exam=label)
                self.s.note(f"{label} 未过线，游戏提示重挑战 / 结束培育")
            else:
                break
            ops.wait_change(before, roi=btn.rect, timeout=EXAM_END_SETTLE, use_signature=False)
            ops.wait_stable(timeout=EXAM_END_SETTLE)  # 转场中间帧找不到「次へ」会提前退出循环，等画面停稳再判断
