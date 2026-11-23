"""新主循环的骨架（去 KAA 设计稿 §3.1、§3.3、§3.4）：暂停检查点、wait_until、页面处理器注册表、Runner。不 import KAA / kotonebot。

每一拍：暂停检查点 → 截图 → 中断层 → classify（纯函数，判页不点）→ Session 观测 → 页面处理器；没有处理器的页交给 advance。
处理器返回 done / stay / stop(原因)；stop 和停机类异常（ResumeBlocked 及其子类）一样，Runner 抛出去由调用方（run_hif）停机。
等待一律走 wait_until：每拍都过暂停检查点和中断层，都带超时，不会死等。计时用 pause_flag.active_clock（录屏暂停期间不走）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from hiro.loop import pause_flag
from hiro.loop.errors import ResumeBlocked
from hiro.runtime import interrupts as _interrupts
from hiro.runtime.frame import Frame, Screen
from hiro.runtime.guard import GuardedDevice
from hiro.vision.classify import UNKNOWN, PageHit

import numpy as np

logger = logging.getLogger(__name__)


_PASS: list = []


def _pass_through() -> tuple[type[BaseException], ...]:
    """总兜底不接的异常：停机类、日切换 / 通信错误回标题（run_hif 回首页续跑）、设备没了、日常单项超时（Deadline 用异常打断）。
    第一次用到时才 import（hiro.daily 反过来要 import 主循环）。"""
    if not _PASS:
        from hiro.daily.common import TaskTimeout
        from hiro.devices.base import DeviceError
        from hiro.loop.date_change import DateChanged

        _PASS.extend((ResumeBlocked, DateChanged, DeviceError, TaskTimeout))
    return tuple(_PASS)


TICK = 0.3  # wait_until 两拍之间至少隔这么久
UNKNOWN_ALERT_S = 30.0  # 认不出的画面连续这么久起报警
INTERRUPT_EXTRA = 3.0  # drive / wait_until：处理中断（设置弹窗等）花的时间不算超时，最多顺延 timeout 的这么多倍
# （DMM own 本战开局 11-14：三个设置弹窗各约 6 秒，把「本战第 1 步点次へ」的 20 秒用完，最后一个弹窗处理完当拍就超时停机）
ERROR_LIMIT = (
    10  # 识别 / 中断 / 处理器抛的普通异常：接住、记 trace、这一拍按认不出处理；同一处同一异常连续这么多次才停机
)
# （用户 11-15 测稳定性：一处识别异常不能让整局退出，Mac h8 read_dialog 空图那次 exit 1）
LIVE_MAX_S = 600.0  # LIVE 演出（含黑屏切全屏）最多不受看门狗管这么久；一直黑 / 横着超过这个就照常停机
INTERRUPT_FRAME_GAP_S = 60.0  # 同一句中断说明这么久内只存一帧
STALL_S = 180.0  # 页面没变、画面也没变（缩略图差 < repeat_guard.SCREEN_DIFF）这么久就停机；认不出的画面同样适用


DOWNLOAD_WORDS = ("ダウンロード中",)


def download_percent(f) -> float | None:
    """资源下载页的进度百分比（「データダウンロード中」旁的「27.27%」）；不是下载页返回 None。"""
    import re

    texts = [t.text.replace(" ", "") for t in f.texts]
    if not any(w in t for t in texts for w in DOWNLOAD_WORDS):
        return None
    for t in texts:
        m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%", t)
        if m and float(m.group(1)) <= 100:
            return float(m.group(1))
    return None


class _Flow:
    """给 pause_flag.Watcher 的流控：旗标出现 → 置暂停，消失 → 解除；主线程在检查点等它解除。"""

    def __init__(self) -> None:
        self._go = threading.Event()
        self._go.set()

    def request_pause(self) -> None:
        self._go.clear()

    def request_resume(self) -> None:
        self._go.set()

    def wait(self) -> None:
        self._go.wait()


@dataclass(frozen=True)
class Outcome:
    kind: str  # done（已离开本页）/ stay（还在本页，下一拍再来）/ stop（停机）/ end（这一局打完了，run 正常结束）
    reason: str = ""


DONE, STAY = Outcome("done"), Outcome("stay")


def stop(reason: str) -> Outcome:
    return Outcome("stop", reason)


Handler = Callable[["Runtime", Frame, PageHit], Outcome]


class _Rescuer(Protocol):
    """hiro.loop.rescue.Rescuer 里主循环用到的部分（测试可以换成假的）。"""

    @property
    def pending(self) -> bool: ...

    def due(self, stuck_s: float, *, dialog: bool = False, img=None) -> bool: ...

    def begin(self, img, context: dict, boxes=None) -> dict | None: ...

    def poll(self) -> dict | None: ...

    def progressed(self) -> None: ...


@dataclass
class Runtime:
    """一局的运行环境。device 是原始设备（hiro.devices），对外给的 self.device 是带拦截的。"""

    raw_device: object
    classify: Callable[[object], PageHit]  # img → PageHit，纯函数（hiro.vision.classify.classify）
    handlers: dict[str, Handler] = field(default_factory=dict)
    advance: Callable[[Runtime, Frame, PageHit], bool] = lambda rt, f, hit: (
        False
    )  # 没有处理器的页：安全推进，点了返回 True
    session: object | None = None  # hiro.loop.session.Session；有就每拍观测
    interrupts: tuple = _interrupts.DEFAULT
    clock: Callable[[], float] = pause_flag.active_clock
    sleep: Callable[[float], None] = time.sleep
    flow: _Flow = field(default_factory=_Flow)
    idle_stop_s: float | None = (
        None  # 这么久没有任何操作（处理器没做事、advance 没点、中断没处理）就写日志停机；None = 不管
    )
    landscape: object | None = (
        None  # hiro.loop.landscape.LandscapeGuard：横屏 LIVE 黑屏切全屏（只 MaaTools 上 active）+ in_live 判断
    )
    dialog_words: tuple[
        str, ...
    ] = ()  # 模式自带的弹窗危险词（GuardedDevice 第 3 条；偶像之路用 hiro.runtime.tower.DIALOG_WORDS）
    rescue: bool = False  # 没有处理器的页长时间没操作时交 LLM 看图（hiro.loop.rescue）；各模式按 --no-llm 传，HIRO_RESCUE_LLM=0 也关
    rescuer: _Rescuer | None = None  # 测试注入；None 且 rescue 开着时按本局的截图 / OCR / 带拦截的点击建一个

    def __post_init__(self) -> None:
        self.screen = Screen(self.raw_device, self.clock)
        self.device = GuardedDevice(self.raw_device, self.screen, self.dialog_words)
        self._unknown_since: float | None = None
        self._download_pct: float | None = None  # 资源下载页上次读到的百分比
        self._base: tuple[str, object, float] | None = None  # 进展看门狗的基准：(页面, 缩略图, 时刻)
        self._live_since: float | None = None
        self._acted_at: float | None = None  # 最近一次有操作（处理器 DONE / advance 点了 / 中断处理）的时刻
        self._idle_warned = False  # 进入 LIVE 演出（横屏 / 黑屏 / 全屏）的时刻：这期间看门狗不按「没变化 / 认不出」停机
        self._err: tuple[str, int] | None = None  # 上一个接住的异常（签名, 连续次数）
        if self.rescuer is None and self.rescue:
            self.rescuer = _make_rescuer(self)

    # ---------------------------------------------------------------- 等待
    def checkpoint(self) -> None:
        """暂停检查点：录屏暂停旗标在（pause_flag.Watcher 置的）就等到恢复。"""
        self.flow.wait()

    def tick(self) -> tuple[Frame, str | None]:
        """一拍：检查点 → 截图 → 中断层。返回 (帧, 中断说明或 None)。"""
        self.checkpoint()
        f = self.screen.shot()
        try:
            done = _interrupts.check(f, self, self.interrupts)
        except Exception as e:  # noqa: BLE001  中断层的识别异常：记下来，这一拍当「处理过」（不拿这一帧做判断），下一拍重截
            if isinstance(e, _pass_through()):
                raise
            self.absorb(e, "中断层", f)
            done = f"中断层异常（{type(e).__name__}）"
        self._log_interrupt(done, f)
        return f, done

    def _log_interrupt(self, done: str | None, f: Frame) -> None:
        """中断日志：同一句连续出现只记第一拍，换了 / 没了再记一句结束（成就提示条等每拍都会命中，11-17 一次记了 7 行）。
        第一拍存一帧进 trace（同一句 INTERRUPT_FRAME_GAP_S 内不重复存；11-17 通信エラー 点了 162 次リトライ，一帧都没有）。"""
        t = f.t
        last = getattr(self, "_int_log", None)  # (说明, 起始时刻, 拍数)
        if last is not None and done == last[0]:
            self._int_log = (last[0], last[1], last[2] + 1)
            return
        if last is not None and last[2] > 1:
            logger.info("中断结束：%s（连续 %d 拍，%.1fs）", last[0], last[2], t - last[1])
        self._int_log = (done, t, 1) if done is not None else None
        if done is None:
            return
        logger.info("中断：%s", done)
        saved = self.__dict__.setdefault("_int_saved", {})
        if self.session is not None and t - saved.get(done, -1e9) >= INTERRUPT_FRAME_GAP_S:
            saved[done] = t
            try:
                shot = self.session.trace.save_frame(self.session.trace.next_seq(), f.img)
                self.session.note(f"中断：{done}（帧 {shot}）")
            except Exception:  # noqa: BLE001  存帧失败不影响
                pass

    def wait_until(self, pred: Callable[[Frame], bool], timeout: float, *, every: float = TICK) -> tuple[bool, Frame]:
        """每拍过检查点和中断层后看 pred(帧)；成立返回 (True, 帧)，超时返回 (False, 最后一帧)。中断处理过的那一拍不算 pred。"""
        t0, paused = self.clock(), 0.0
        while True:
            t1 = self.clock()
            f, done = self.tick()
            if done is None and self._guarded(pred, f, "等待条件"):
                return True, f
            if done is not None:
                paused = min(
                    paused + self.clock() - t1 + every, timeout * INTERRUPT_EXTRA
                )  # 处理中断的那拍不算等待时间
            if self.clock() - t0 - paused >= timeout:
                return False, f
            self.sleep(every)

    def drive(self, step: Callable[[Frame], object], timeout: float, what: str, *, every: float = TICK):
        """多步流程的一段：每拍过检查点和中断层后调 step(帧)（可以点），返回非 None 就结束并返回它；
        超时抛 ResumeBlocked（what 写进原因）。开局 / 续跑这类「看画面 → 点 → 等下一页」的步骤都用它。"""
        t0, paused, live = self.clock(), 0.0, 0.0
        while True:
            t1 = self.clock()
            f, done = self.tick()
            if done is None and self._live_beat(f):
                # 横屏 LIVE（初 True End 等结算流程里的演出，协调 11-17）：步骤是竖屏流程，不交给它点（原来点 (12,12) 空点、黑屏也不切全屏）；
                # 演出的时间不算这一段的超时（最多 LIVE_MAX_S）
                live = min(live + self.clock() - t1 + every, LIVE_MAX_S)
                if self.clock() - t0 - paused - live >= timeout:
                    raise ResumeBlocked(f"{what}：{timeout:.0f}s 没完成")
                self.sleep(every)
                continue
            if done is None and (r := self._guarded(step, f, what)) is not None:
                return r
            if done is not None:
                paused = min(
                    paused + self.clock() - t1 + every, timeout * INTERRUPT_EXTRA
                )  # 处理中断的那拍不算这一段的时间
            if self.clock() - t0 - paused - live >= timeout:
                raise ResumeBlocked(f"{what}：{timeout:.0f}s 没完成")
            self.sleep(every)

    def _live_beat(self, f: Frame) -> bool:
        """drive 的一拍是不是横屏 LIVE：黑屏够久切全屏（LandscapeGuard.tick，同主循环）、曲目页点「TAP TO START」（scenes.live_start），
        演出中不点。是 LIVE 返回 True（这一拍不交给步骤）。"""
        img = getattr(f, "img", None)
        lg = self.landscape
        if img is None:
            return False
        if lg is not None and lg.tick(img):
            return True  # 刚切了全屏 / 退了全屏：下一拍重截
        if img.shape[1] <= img.shape[0] and not (lg is not None and lg.active and lg.in_live(img)):
            return False  # 竖屏、也不是 LIVE 黑屏（只在 LandscapeGuard 开着的设备上把黑屏当 LIVE）
        from hiro.runtime import scenes

        scenes.live_start(self, f)
        return True

    # ---------------------------------------------------------------- 主循环
    def step(self) -> Outcome:
        """主循环一拍。stop 时抛 ResumeBlocked。识别 / 处理器抛的普通异常接住（absorb），这一拍按认不出处理，下一拍重来；
        停机类（ResumeBlocked）、日切换 / 通信错误回标题（DateChanged）、设备没了（DeviceError）照常抛出。"""
        try:
            out = self._step()
        except Exception as e:  # noqa: BLE001  总兜底：一处异常不打死整局
            if isinstance(e, _pass_through()):
                raise
            self.absorb(e, "判页 / 处理器", self.screen.frame)
            self.sleep(TICK)
            return STAY
        self._err = None
        return out

    def _guarded(self, fn, f: Frame, where: str):
        """drive / wait_until 里调流程自己的 step / pred（开局、收尾的点击和判断）：普通异常接住（absorb），这一拍当没完成（None），
        下一拍重来；同一异常连续 ERROR_LIMIT 次停机。停机类照常抛。"""
        try:
            r = fn(f)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, _pass_through()):
                raise
            self.absorb(e, where, f)
            return None
        self._err = None
        return r

    def absorb(self, e: Exception, where: str, f: Frame | None) -> None:
        """接住一个普通异常：日志带栈，trace 记备注（异常、栈的最后几行、存帧路径）；同一处同一异常连续 ERROR_LIMIT 次抛 ResumeBlocked 停机。"""
        import traceback

        sig = f"{where}|{type(e).__name__}|{str(e)[:120]}"
        n = self._err[1] + 1 if self._err is not None and self._err[0] == sig else 1
        self._err = (sig, n)
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        logger.error(
            "%s抛了异常（连续第 %d 次，到 %d 次停机）：%s: %s\n%s", where, n, ERROR_LIMIT, type(e).__name__, e, tb
        )
        if self.session is not None:
            shot = None
            try:
                img = getattr(f, "img", None)
                if img is not None:
                    shot = self.session.trace.save_frame(self.session.trace.next_seq(), img)
            except Exception:  # noqa: BLE001  存帧失败不影响
                pass
            tail = " | ".join(line.strip() for line in tb.strip().splitlines()[-4:])
            self.session.recover(
                "unhandled", f"{where}异常（连续第 {n} 次）{type(e).__name__}: {e}；帧 {shot}；栈尾 {tail}"
            )
        if n >= ERROR_LIMIT:
            raise ResumeBlocked(f"{where}同一异常连续 {n} 次，停机：{type(e).__name__}: {e}") from e

    def _step(self) -> Outcome:
        f, done = self.tick()
        if done is not None:
            self._mark_acted(f.t)
            return STAY
        if self.landscape is not None and self.landscape.tick(f.img):
            self._mark_acted(f.t)
            return STAY  # 刚切了全屏 / 退了全屏（窗口操作）：下一拍重截
        hit = self.classify(f.img)
        self._watch_progress(hit, f)
        if self.session is not None:
            # 判页给了字段就带上；没给不在这里做完整字段提取（贵），由处理器自己观测
            self.session.observe(scene=hit.name, img=f.img, fields=hit.fields if hit.fields is not None else False,
                                 page_source=hit.source)  # fmt: skip
        handler = self.handlers.get(hit.name)
        out = handler(self, f, hit) if handler is not None else (DONE if self.advance(self, f, hit) else STAY)
        if out.kind == "stop":
            raise ResumeBlocked(f"{hit.name}：{out.reason}")
        if out.kind != "stay" or (self.landscape is not None and self.landscape.in_live(f.img)):
            # 按处理器返回时的时刻记（LIVE 演出里本来就不点：不算没进展）。原来记的是进处理器前那帧的时刻：考试处理器跑了 5 分钟，
            # 出来第一帧是结算后的过场，马上判成「284s 没有任何操作」停机（初 PRO 最終 11-18）
            self._mark_acted(self.clock() if handler is not None else f.t)
            if self.rescuer is not None:
                self.rescuer.progressed()
        elif handler is None and self._rescue(f, hit):
            self._mark_acted(self.clock())
        else:
            self._check_idle(hit, f.t, handler is not None)
        return out

    def _rescue(self, f: Frame, hit: PageHit) -> bool:
        """没有处理器、advance 也没点的页：没操作够久就把截图交给 LLM（后台问，主循环照常跑），回复到了按它点 / 等。
        返回这一拍有没有做事（点了 / 等了）。LLM 说停 / 被拦截：报警放手，仍由 idle_stop_s 停机。"""
        r = self.rescuer
        if r is None:
            return False
        idle = 0.0 if self._acted_at is None else f.t - self._acted_at
        try:
            if r.pending:
                rec = r.poll()
                if rec is None:
                    return False
            else:
                if not r.due(idle, dialog=hit.name == "DIALOG", img=f.img):
                    return False
                logger.info("页面 %s：%.0fs 没有操作、没有处理器，截图交给 LLM 看（后台）", hit.name, idle)
                rec = r.begin(f.img, {"page": hit.name, "idle_s": round(idle)})
                if rec is None:
                    return False
        except Exception as e:  # noqa: BLE001  兜底出错：照旧按空闲计时
            logger.warning("LLM 兜底出错：%r", e)
            return False
        a = (rec.get("action") or {}).get("action")
        return a in ("tap_box", "tap_blank", "wait") and not rec.get("blocked") and not rec.get("skipped")

    def _mark_acted(self, t: float) -> None:
        self._acted_at, self._idle_warned = t, False

    def _check_idle(self, hit: PageHit, t: float, handled: bool) -> None:
        """没有操作的时间：过一半写一次 WARNING，到 idle_stop_s 写日志停机（偶像之路 g5 11-17：停在首页 4 分钟一直存帧、没有任何日志）。
        首页这类会动的画面，「画面没变」的看门狗管不到，所以另按「没有操作」计时。"""
        if self.idle_stop_s is None:
            return
        if self._acted_at is None:
            self._acted_at = t
        idle = t - self._acted_at
        what = "处理器一直在等" if handled else "这一页没有处理器、也没有可以点过去的地方"
        if idle >= self.idle_stop_s:
            logger.error("页面 %s：%.0fs 没有任何操作（%s），停机", hit.name, idle, what)
            raise ResumeBlocked(f"{hit.name} 页 {idle:.0f}s 没有任何操作（{what}），停机")
        if idle >= self.idle_stop_s / 2 and not self._idle_warned:
            self._idle_warned = True
            logger.warning("页面 %s：%.0fs 没有任何操作（%s），到 %.0fs 停机", hit.name, idle, what, self.idle_stop_s)

    def _watch_progress(self, hit: PageHit, f: Frame) -> None:
        """进展看门狗：页面没变、画面也没变 STALL_S 秒就停机（处理器一直 stay、没处理器的已知页、认不出的画面都管）。
        认不出的画面另外连续 UNKNOWN_ALERT_S 秒起报警、STALL_S 秒停机（不管画面变没变）。"""
        from hiro.loop.repeat_guard import SCREEN_DIFF, _thumb

        if self.landscape is not None and self.landscape.in_live(f.img):
            # 横屏 LIVE（PlayCover 黑屏、全屏中截图也是黑的）：画面本来就认不出 / 不变，交给 LandscapeGuard 切全屏、退全屏，
            # 不按看门狗停（p13 11-16：LIVE 黑屏 181 秒 LOADING 停机）。连续 LIVE_MAX_S 还在「LIVE」才照常管
            if self._live_since is None:
                self._live_since = f.t
            if f.t - self._live_since < LIVE_MAX_S:
                self._base, self._unknown_since = None, None
                return
        else:
            self._live_since = None
        th = _thumb(f.img)
        if self._base is None or self._base[0] != hit.name or float(np.abs(th - self._base[1]).mean()) >= SCREEN_DIFF:
            self._base = (hit.name, th, f.t)
        elif (stuck := f.t - self._base[2]) >= STALL_S:
            raise ResumeBlocked(f"{hit.name} 页面和画面 {stuck:.0f}s 没变化，停机")
        if hit.name != UNKNOWN.name:
            self._unknown_since = None
        elif (pct := download_percent(f)) is not None and pct != self._download_pct:
            # 资源下载页（「データダウンロード中 27.27%」）：百分比在动就是有进展，不按认不出计时（11-20 账号 2：网络不稳，
            # E209 每 10 秒一次、リトライ 后接着下，180 秒按认不出停机）。百分比不动照常计时
            self._download_pct, self._unknown_since = pct, f.t
            self._base = None
            self._comm_retry = None  # 下载在涨：两次 E209 之间有进展，通信エラー 的连续リトライ 计数重新算
            logger.info("资源下载中 %.2f%%", pct)
        elif self._unknown_since is None:
            self._unknown_since = f.t
        elif (unknown := f.t - self._unknown_since) >= STALL_S:
            raise ResumeBlocked(f"画面连续 {unknown:.0f}s 认不出，停机")  # 画面一直在变（循环动画）也停
        elif unknown >= UNKNOWN_ALERT_S:
            logger.warning("画面已连续 %.0fs 认不出", unknown)

    def run(self, until: Callable[[], bool], *, max_steps: int | None = None) -> int:
        """跑到 until() 为真（或 max_steps 拍），返回跑了几拍。录屏暂停看守在这期间开着。"""
        watcher = pause_flag.Watcher(self.flow, session=self.session).start()
        n = 0
        try:
            while not until() and (max_steps is None or n < max_steps):
                n += 1
                if self.step().kind == "end":  # 处理器报这一局打完了（培育结束）
                    break
        finally:
            watcher.stop()
        return n


def _make_rescuer(rt: Runtime):
    """本局的 LLM 兜底：截图 / OCR 用 runtime 的，点击走带拦截的设备（危险词同样拦）。HIRO_RESCUE_LLM=0 时不建。"""
    from hiro.loop import rescue

    if not rescue.LLM_UNKNOWN:
        return None

    def ocr(img):
        from hiro.vision.ocr import screen_texts

        return [(t.text, 1.0, tuple(int(v) for v in t.rect)) for t in screen_texts(img)]

    return rescue.Rescuer(
        ocr=ocr, shoot=lambda: rt.screen.shot().img, click=rt.device.tap, sleep=rt.sleep, clock=rt.clock
    )
