"""Session：主循环的运行时核心。observe → reduce → (decide) → act → verify，每一步落 trace。

所有实机依赖（截图、场景分类、字段提取、时钟）都可注入，便于离线测试；决策与点击由调用方（策略钩子 / run_exam）完成，
Session 只负责把它们按契约记下来，并提供两类恢复：缺字段等待（observe_complete）与执行后校验（verify）。
"""

import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from hiro.contracts import Decision, ExecutionResult, GameState, Observation, Preview, Step, TraceWriter
from hiro.loop.reduce import reduce
from hiro.loop.transitions import is_transient, successors
from hiro.log import logging

logger = logging.getLogger(__name__)

# 故障恢复的类别（notes 前缀 recover:<kind>），报告按此统计
RECOVER_KINDS = ("page_misjudged", "missing_fields", "llm_invalid", "tap_no_effect", "dialog", "crash_restart", "date_change", "unhandled", "llm_rescue")


def _default_shoot():
    from kotonebot.backend.context import device

    return device.screenshot()


def _default_classify() -> tuple[str, str]:
    from hiro.vision.scene import classify_current

    r = classify_current()
    return r.scene, r.area


def _default_extract(scene: str, img) -> tuple[dict | None, float]:
    from hiro.vision.extract import extract

    return extract(scene, img)


class Session:
    def __init__(self, trace: TraceWriter, *, shoot: Callable | None = None, classify: Callable | None = None,
                 extract: Callable | None = None, sleep: Callable[[float], None] = time.sleep, clock=time.monotonic):  # fmt: skip
        self.trace = trace
        self.state = GameState()
        self.shoot = shoot or _default_shoot
        self.classify = classify or _default_classify
        self.extract = extract or _default_extract
        self.sleep = sleep
        self.clock = clock
        self.notes: list[str] = []  # 下一条 Step 要带的说明
        self.recoveries: Counter = Counter()
        self.last: Observation | None = None
        self.img = None  # 最近一次 observe 的截图
        self.steps = 0
        self._last_exec_t: float | None = None  # 上一个有执行结果的步落盘时刻（time.time），算过场耗时
        from hiro.loop.repeat_guard import Guard

        from hiro.loop.pause_flag import active_clock

        # 防空转：重复无效动作 + 没进展（hiro.loop.repeat_guard）；录屏暂停期间不计时（测试注入了 clock 时用注入的）
        self.guard = Guard(clock=active_clock if self.clock is time.monotonic else self.clock)
        self.repeat_streak = 0  # 最近一个决策步：同一局面同一动作连续第几次
        self.advance_taps = 0  # 上一个决策步之后主循环点空白推进的次数（HiroController）
        self._buffer: list[Observation] = []  # 上一条 Step 之后并入状态的全部观测（重放用）
        self._decision_state: GameState | None = None  # 第一次执行校验开始时的状态 = 决策输入（Step.state）
        # 1.2 状态推算与互验（docs/state-events.md）
        import os

        self.debug_recog = os.environ.get("HIRO_DEBUG_RECOG") == "1"  # run_hif --debug-recog 会设它
        self.predictions: list = []  # 尚未核对的预测
        self._checks: list = []  # 上一条 Step 之后的互验结果
        self._events: list = []  # 上一条 Step 之后的事件
        self._new_predictions: list = []
        self._recent_events: list = []  # 最近的事件（配对用）
        self.check_counts: Counter = Counter()  # verdict → 次数（一致率报告）

    # ---------------------------------------------------------------- 观测
    def observe(self, *, scene: str | None = None, area: str = "produce", img=None, fields: bool | dict = True,
                ready: bool = False, source: str = "screen", save_frame: bool = True, seq: int | None = None) -> Observation:  # fmt: skip
        """截图（或用给定的 img）→ 分类（或用给定的 scene）→ 提取字段（fields=True 走 extract，dict 为原始字段，ready=True 表示已是契约字段）→ reduce。"""
        from hiro.vision.observe import observation

        t0 = self.clock()
        timings: dict[str, float] = {}
        if img is None and (fields is not False or save_frame or scene is None):
            img = self.shoot()
            timings["capture"] = (self.clock() - t0) * 1000
        if scene is None:
            t1 = self.clock()
            scene, area = self.classify()
            timings["classify"] = (self.clock() - t1) * 1000
        raw = None
        if fields is True and img is not None:
            raw, ms = self.extract(scene, img)
            timings["extract"] = ms
        elif isinstance(fields, dict):
            raw = fields
        seq = seq or self.trace.next_seq()
        shot = self.trace.save_frame(seq, img) if (save_frame and img is not None) else None
        stable = self.state.page.frames + 1 if self.state.page.name == scene else 1
        obs = observation(seq, time.time(), scene, raw, area=area, screenshot=shot, source=source, stable_frames=stable,
                          timings_ms=timings, ready=ready)  # fmt: skip
        if fields is not False and source == "screen":
            self._decision_state = None  # 新的决策帧：上一次执行校验没有 record 时不能沿用旧快照
        self.state = self._reduce_checked(obs)
        self.last = obs
        self._buffer.append(obs)
        from hiro.loop.repeat_guard import state_sig

        self.guard.seen(self.state.page.name, state_sig(self.state.page.name, self.state.page_fields, self.state.exam, self.state.produce))
        if img is not None:
            self.img = img
        return obs

    def _reduce_checked(self, obs: Observation) -> GameState:
        """互验 → 去掉读错的读数 → reduce → 标字段状态（§5，hiro.loop.events.reduce_checked，离线回放同一函数）。"""
        from hiro.loop.events import reduce_checked, settle

        try:
            state, checks = reduce_checked(self.state, obs, self.predictions)
        except Exception as e:  # 互验不能拖垮主循环
            self.notes.append(f"互验失败：{type(e).__name__}: {e}")
            return reduce(self.state, obs)
        for c in checks:
            self.check_counts[c.verdict] += 1
            if c.verdict == "read_error":
                self.notes.append(f"读错拦截：{c.field}={c.observed!r}（{c.rule}，参考 {c.predicted!r}），不进状态与决策")
        self.predictions = settle(self.predictions, checks)
        self._checks.extend(checks)
        self.state = state
        self._observed_events(obs)
        return self.state

    def _observed_events(self, obs: Observation) -> None:
        """识别认出的事件（hiro.vision.events）：与最近的动作事件是同一件事就只标确认，否则并入状态。"""
        try:
            from hiro.vision.events import observed_events

            raw = observed_events(obs, self.last)
        except Exception as e:
            self.notes.append(f"事件识别失败：{type(e).__name__}: {e}")
            return
        from hiro.loop.events import to_event

        for d in raw:
            ev = to_event(d)
            if ev.kind not in __import__("hiro.contracts.events", fromlist=["EVENT_KINDS"]).EVENT_KINDS:
                continue
            self.emit(ev)

    def emit(self, event) -> None:
        """事件并入状态（apply_event）并登记预测。动作事件与识别认出的同一件事（获得弹窗 / 交換確認）配对：后到的只标确认，不重复改牌组。"""
        from hiro.loop.events import apply_event, find_pair

        other = find_pair(event, self._recent_events)
        if other is not None:
            other.ref["paired"] = event.seq
            other.confirmed = True
            event.ref["paired"] = other.seq
            event.confirmed = True
            self._events.append(event)  # 记进 trace，不再并入状态
            return
        self._recent_events.append(event)
        del self._recent_events[:-20]
        self.state, preds = apply_event(self.state, event)
        self.predictions.extend(preds)
        self._events.append(event)
        self._new_predictions.extend(preds)

    def observe_save(self, fields: dict, *, seq: int | None = None) -> Observation:
        """本地存档读出的考试局面（契约字段）作为 save 来源并入状态，与同帧截图共用 seq。"""
        return self.observe(scene="HIF_EXAM", area="produce", img=None, fields=fields, ready=True, source="save", save_frame=False,
                            seq=seq or (self.last.seq if self.last else None))  # fmt: skip

    def observe_complete(self, scene: str, required: list[str] | tuple[str, ...], *, tries: int = 5, interval: float = 0.5,
                         fields: bool | dict = True) -> Observation:  # fmt: skip
        """缺字段恢复：required 全读到且 complete 不为 False 才返回；否则等一帧重试，用尽后带着缺字段返回并记 recover:missing_fields。"""
        obs = None
        for i in range(tries):
            obs = self.observe(scene=scene, fields=fields)
            missing = obs.missing(required)
            if not missing and obs.fields.get("complete", True) is not False:
                return obs
            if i < tries - 1:
                self.sleep(interval)
        assert obs is not None
        self.recover("missing_fields", f"{scene} 连续 {tries} 帧缺 {obs.missing(required)}，带缺字段继续")
        return obs

    # ---------------------------------------------------------------- 记录
    def recover(self, kind: str, detail: str) -> None:
        assert kind in RECOVER_KINDS, kind
        self.recoveries[kind] += 1
        self.notes.append(f"recover:{kind}: {detail}")

    def note(self, text: str) -> None:
        self.notes.append(text)

    def add_checks(self, checks: list) -> None:
        """主循环之外算出的互验（如 battle 的模型倍率 vs 说明页真值）：并入下一条 Step 的 checks。"""
        for c in checks:
            self.check_counts[c.verdict] += 1
        self._checks.extend(checks)

    def record(self, obs: Observation | None = None, *, decision: Decision | None = None, previews: list[Preview] | None = None,
               execution: ExecutionResult | None = None, timings_ms: dict[str, float] | None = None) -> Step:  # fmt: skip
        obs = obs or self.last
        assert obs is not None, "先 observe 再 record"
        idx = next((i for i, o in enumerate(self._buffer) if o is obs), None)
        before = self._buffer[:idx] if idx is not None else list(self._buffer)
        after = self._buffer[idx + 1 :] if idx is not None else []
        self._buffer = []
        state_in = self._decision_state or self.state
        self._decision_state = None
        if decision is not None and execution is not None:
            from hiro.loop.events import events_from_step

            for ev in events_from_step(obs.page.name, decision.action.to_dict(), bool(execution.ok), state_in.page_fields,
                                       state_in.produce, seq=obs.seq):  # fmt: skip
                self.emit(ev)
        timings_ms = dict(timings_ms or {})
        if decision is not None and obs.page.name != "HIF_EXAM":
            # 过场耗时：上一个执行完的决策步 → 这一步的决策帧（不含本步决策），以及其间主循环点空白推进的次数（hiro.loop.advance）
            if self._last_exec_t is not None:
                timings_ms.setdefault("transition", max(0.0, obs.t - self._last_exec_t) * 1000)
            timings_ms.setdefault("advance_taps", self.advance_taps)
        if decision is not None:
            self.advance_taps = 0
        repeated = None
        if decision is not None and execution is not None and decision.action.kind.name not in ("WAIT", "NOOP"):
            from hiro.loop.repeat_guard import REPEAT_MAX, action_key, state_sig

            page = obs.page.name
            self.repeat_streak = self.guard.attempt(page, state_sig(page, state_in.page_fields, state_in.exam, state_in.produce), action_key(decision.action))
            if self.repeat_streak >= REPEAT_MAX and execution.ok and page != "HIF_EXAM":
                # 考试外这一下执行成功了（DMM dmm1m 相談删 眠気：前两次拼接失败取消，第 3 次删成功、P 380→280，
                # 但三次的执行前局面相同，以前照样判重复停机）：先不算无效；真没效果的话下一步局面不变，计数接着涨，第 4 次停
                logger.info("%s 同一局面下 %s 第 %d 次，这次执行成功，先不算重复无效", page, decision.action.kind.value, self.repeat_streak)
            elif self.repeat_streak >= REPEAT_MAX:
                repeated = f"重复无效动作：{page} 同一局面下 {decision.action.kind.value}{decision.action.args} 连续 {self.repeat_streak} 次"
                self.recover("tap_no_effect", repeated)
                self.note("blunder:repeat_no_effect " + repeated)
        elif decision is not None:
            self.repeat_streak = 0
        # 模型倍率核对每场 3 条，一致的也留（证明这场核对过）
        checks = self._checks if self.debug_recog else [c for c in self._checks if c.verdict in ("read_error", "model_gap") or c.rule == "model_permil"]
        events, preds = self._events, self._new_predictions
        self._checks, self._events, self._new_predictions = [], [], []
        step = Step(seq=obs.seq, t=time.time(), observation=obs, state=state_in, events=events, predictions=preds, checks=checks,
                    state_after=None if state_in is self.state else self.state, decision=decision, previews=list(previews or []),
                    execution=execution, observations_before=before, observations_after=after, notes=list(self.notes),
                    timings_ms=timings_ms)  # fmt: skip
        if execution is not None:
            self._last_exec_t = step.t
        self.notes = []
        self.trace.write(step)
        self.steps += 1
        if repeated and obs.page.name != "HIF_EXAM":
            # 考试外没有「本回合禁用」：停机报警（考试里由 battle 本回合禁用该动作，见 run_exam）
            from hiro.loop.repeat_guard import RepeatedNoEffect

            raise RepeatedNoEffect(repeated + "，停机报警")
        return step

    # ---------------------------------------------------------------- 校验
    def verify(self, action, *, page_before: str, taps: list[list[int]] | None = None, expect: set[str] | None = None,
               timeout: float = 8.0, interval: float = 0.4, retries: int = 0, stay_ok: bool = False,
               left_ok: bool = False) -> ExecutionResult:  # fmt: skip
        """动作下发后轮询页面直到离开 page_before（或 stay_ok 时任一稳定页面）。只记页面，不提字段。
        left_ok：画面一离开 page_before 就算生效（过渡 / 读取 / 认不出的帧也算），不再等下一个稳定页面——
        调用方已有执行生效的证据（KAA commit 正常返回）时用。第二十一局选拔：选行动后课程动画全是过渡帧，每步空等到 8s 超时。

        expected_transition：执行后页面是否在转移图允许的后继里；不在就记 recover:page_misjudged。
        """
        if self._decision_state is None:
            self._decision_state = self.state  # reduce 返回新对象，之后的观测不会改到它
        t0 = self.clock()
        page_after = page_before
        waited = 0.0
        deadline = t0 + timeout
        while True:
            img = self.shoot()
            scene, area = self.classify()
            if left_ok and is_transient(scene) and scene != page_before:
                page_after = None  # 已离开，去向待主循环判
                break
            if not is_transient(scene):
                page_after = scene
                if scene != page_before or stay_ok:
                    self.observe(scene=scene, area=area, img=img, fields=False, save_frame=False)
                    break
            if self.clock() >= deadline:
                break
            self.sleep(interval)
        waited = self.clock() - t0
        allowed = expect if expect is not None else successors(page_before)
        expected = None if page_after is None else page_after in allowed
        ok = page_after != page_before or stay_ok
        err = None
        if not ok:
            err = f"{timeout:.0f}s 内页面仍是 {page_before}"
        elif expected is False:
            err = f"执行后到了 {page_after}，不在 {page_before} 的允许后继里"
            self.recover("page_misjudged", err)
        return ExecutionResult(action=action, ok=ok, taps=list(taps or []), waited_s=round(waited, 3), page_before=page_before,
                               page_after=page_after, expected_transition=expected, retries=retries, error=err,
                               seconds=round(self.clock() - t0, 3))  # fmt: skip

    def summary(self) -> dict[str, Any]:
        return {"steps": self.steps, "recoveries": dict(self.recoveries), "page": self.state.page.name, "mode": self.state.mode,
                "checks": dict(self.check_counts), "debug_recog": self.debug_recog}
