# KAA 派生代码：对 kotones-auto-assistant（GPL-3.0）流程的运行时补丁，部分逻辑改写自 KAA 源码，按 GPL-3.0 发布
"""对 KAA 流程的运行时补丁（不改 vendor）。下面换卡两阶段、P 道具三选一的实现已搬到 hiro.produce.card_ops（新旧主循环共用），
这里只在 apply 里把它们挂到 KAA 的上下文上。

- locate_idol：KAA 靠滚动条翻偶像总览，本机 PlayCover 9:16 下滚动条滑块检测失败（thumb height 1），只看第一页就报
  IdolCardNotFoundError。这里改为在偶像区域内上滑翻页，每页做同样的图像匹配。
- H.I.F 授業换卡（SkillCardChangeContext，先选新卡、再选被换掉的原卡）：KAA 阶段 1 只在 20px 高的
  BoxCardLetters（y 943–963）里找卡角标 A/M，画面差 8px 或还在入场动画就找不到，记一条错误后直接返回；
  hif_grind 随后无条件调用阶段 2，在阶段 1 的画面上找不到卡，抛 UnrecoverableError（2026-10-31 实机 r5）。
  这里放宽搜索区域、找不到就等动画重试；阶段 2 先确认画面已到阶段 2，找不到卡也不抛，交回主循环重新认场景。
  阶段 2（换掉牌组里哪张）不再盲点第 0 张：第四局本战两次授業セレクトチェンジ 换掉了 デイドリーミング、がむしゃら
  （reports/analysis/q4-daydreaming-missing-exec.md；牌组页按卡 id 排序，act-1 的卡排在最前）。现在用识别
  read_card_dialog 读卡格：给了 card_id 就换那张；没给就从 index 起找第一张认得出、且不在保护名单（hiro.state.knowledge
  的必拿 / 循环 / S·A 级 / 记忆想带的卡）里的卡；认不出或全是保护卡就点「キャンセル」不换。两个阶段都存 calib 帧、
  日志写明换出 / 换入了哪张。
- H.I.F P 道具背包三选一（PItemSelectContext）：KAA 写死的 PITEM_POSTIONS 是初模式中間試験的位置，H.I.F 背包间距更宽，
  左右两个点压在包的边缘（2026-10-31 r2 实机帧）。KAA commit 还在 Loop() 里无限重试。这里按 H.I.F 实测中心点包，
  等「受け取る」变亮再点，等待按时间封顶，点不动返回 False 交回主循环。
"""

import importlib
import time
from collections.abc import Callable

from kotonebot import device, logging
from kotonebot.primitives import Rect
from hiro import imgio
from hiro.produce.card_ops import change_stage1, change_stage2, pitem_commit
from hiro.vision.ocr import screen_texts

logger = logging.getLogger(__name__)




def _progress(why: str) -> None:
    from hiro.loop.repeat_guard import progress

    progress(why)


def locate_idol_by_swipe(skin_id: str) -> Rect | None:
    """KAA prepare 的 locate_idol 换成这个：找法在 hiro.produce.idol_select（不依赖 KAA），这里接 kotonebot 设备、换回 KAA 的 Rect。"""
    from hiro.produce import idol_select

    hit = idol_select.locate(skin_id, shoot=device.screenshot, swipe=lambda x1, y1, x2, y2, d: device.swipe(x1, y1, x2, y2, duration=d),
                             progress=_progress)  # fmt: skip
    return None if hit is None else Rect(hit.x1, hit.y1, hit.w, hit.h)


EXPECT_MEMORY: tuple[str, ...] = ()  # run_hif --expect-memory：本战开局前，开始确认页上必须出现的选拔回忆属性数字


from hiro.produce.memory_select import MemoryMismatch, memory_value_seen  # noqa: E402,F401  run_hif 按 kaa_patches.MemoryMismatch 捕获


def select_memory(expect: tuple[str, ...], **kw) -> bool:
    """在回忆列表里选中 expect（hiro.produce.memory_select.select_memory），没给设备时用 kotonebot 的。"""
    from hiro.produce import memory_select

    kw.setdefault("shoot", device.screenshot)
    kw.setdefault("click", device.click)
    kw.setdefault("swipe", device.swipe)
    return memory_select.select_memory(expect, **kw)


def _memory_missing_on_page(**kw) -> list:
    """开始确认页缺哪几项选拔回忆属性（hiro.produce.memory_select.memory_missing_on_page），没给截图时用 kotonebot 的。"""
    from hiro.produce import memory_select

    kw.setdefault("shoot", device.screenshot)
    return memory_select.memory_missing_on_page(EXPECT_MEMORY, **kw)


def _check_memory_then(step4):
    def wrapped(*a, **k):
        if EXPECT_MEMORY:
            missing = _memory_missing_on_page()
            if missing:
                select_memory(EXPECT_MEMORY)
                # 不管列表里找没找到都重读本页：已选中的那份不在「変更」列表里（第二十五局：上次中断时已选好 1554/2505/830，
                # 首读没认全 → 进列表翻 12 页找不到 → 不开局），关掉列表后页面上仍是它
                missing = _memory_missing_on_page()
            if missing:
                logger.error("本战开始确认页的选拔回忆不是指定的那份（缺 %s），不开局", missing)
                raise MemoryMismatch(f"选拔回忆不符：页面上找不到 {missing}")
            logger.info("本战开局：选拔回忆核对通过（%s）", "/".join(EXPECT_MEMORY))
        return step4(*a, **k)

    return wrapped


ALLOW_DISCARD = False  # 只有 run_hif --allow-discard（用户明确同意破弃）时为 True
_ORIG_DISCARD_CONFIRM = None


class NeverConfirmDiscard:
    """替换 R.Produce.BreakProduceDialog.ButtonConfirm：KAA do_produce 遇到「プロデュースデータの破棄」会直接点「開始する」破弃
    其他端末的培育。这里改成一律点「キャンセル」并抛 ResumeBlocked，由用户处理（用户规则：绝不按開始する）。"""

    @staticmethod
    def _block(*a, **k):
        if ALLOW_DISCARD and _ORIG_DISCARD_CONFIRM is not None:
            return _ORIG_DISCARD_CONFIRM.try_click(*a, **k)
        from hiro.loop.resume import CANCEL_BUTTON, DISCARD_TITLE, ResumeBlocked

        device.click(*CANCEL_BUTTON)
        logger.error("KAA 想确认「%s」：已点「キャンセル」，停止运行", DISCARD_TITLE)
        raise ResumeBlocked(f"游戏提示「{DISCARD_TITLE}」：其他端末有进行中的培育。已点「キャンセル」，不破弃；由用户决定。")

    try_click = click = wait = _block

    @staticmethod
    def exists(*_a, **_k) -> bool:
        return False


BLIND_CHOICE: dict = {"pending": False}  # KAA 想盲点的二选一被拦下后置 True，HiroController 下一轮停下报警


class _BlindDoubleClick(Exception):
    pass


def _guard_action_like(orig):
    """KAA ProducePage._check_action_like 末尾：画面上有 >1 个剧情按钮（培育初始饮料 / 卡二选一、带选项的剧情）时直接 double_click(buttons[0])。
    这是盲选第 0 项（用户规则 3c / 3f 禁止）：换掉 page 模块的 device，让 double_click 抛异常，判成 UNKNOWN 并置 BLIND_CHOICE。"""
    if getattr(orig, "_hiro_guard", False):
        return orig

    def wrapped(self):
        import importlib

        pg = importlib.import_module("kaa.tasks.produce.new.page")
        from kaa.tasks.produce.new.consts import Scene, SceneType

        real = pg.device

        class _Proxy:
            def __getattr__(self, name):
                return getattr(real, name)

            def double_click(self, *a, **k):
                raise _BlindDoubleClick(a)

        pg.device = _Proxy()
        try:
            return orig(self)
        except _BlindDoubleClick:
            BLIND_CHOICE["pending"] = True
            logger.error("KAA 想双击第 0 个剧情按钮（初始饮料 / 卡二选一或剧情选项），已拦下")
            return Scene(SceneType.UNKNOWN)
        finally:
            pg.device = real

    wrapped._hiro_guard = True
    return wrapped


class RentalPending(RuntimeError):
    """开局第 2 步租借格：支援卡选择系统还没交付，不让 KAA 直接确认列表第一张，停下交总控手动挑（用户 2026-11-02）。"""


RENTAL_ATTR: str | None = None  # 租借挑哪个属性（run_hif 按 --main-attr 设）；None = hiro.produce.rental.WANT_ATTR


def _default_rental_pick() -> bool:
    """有开局编成方案按编成价值挑，没有按主属性、等级最高（hiro.produce.formation_steps.rental_pick）。"""
    from hiro import kaa_env
    from hiro.produce import formation_steps

    return formation_steps.rental_pick(kaa_env.ACTIVE_PLAN, RENTAL_ATTR, kaa_env.PLAN_MAIN_ATTR)


RENTAL_PICK: Callable | None = _default_rental_pick  # 默认：主属性、等级最高的一张；策略层可替换成自己的选择


from hiro.produce.formation_check import FormationMismatch  # noqa: E402  run_hif 按 kaa_patches.FormationMismatch 捕获


def _check_support(img) -> None:
    """有开局编成方案时核对第 2 步支援卡（hiro.produce.formation_steps.check_support）；对不上抛 FormationMismatch。"""
    from hiro import kaa_env
    from hiro.produce import formation_steps

    formation_steps.check_support(img, kaa_env.ACTIVE_PLAN, kaa_env.SUPPORT_SET)


def _autofix_on() -> bool:
    from hiro import kaa_env
    from hiro.produce import formation_steps

    return formation_steps.autofix_on(kaa_env.ACTIVE_PLAN)


def _writer_device(tag: str):
    from pathlib import Path

    import cv2

    from hiro.produce.formation_writer import Device

    d = Path("reports/live/calib") / f"{time.strftime('%Y%m%d-%H%M%S')}-编成修正-{tag}"
    n = [0]

    def calib(img, name):
        d.mkdir(parents=True, exist_ok=True)
        n[0] += 1
        cv2.imwrite(str(d / f"{n[0]:02d}-{name}.jpg"), img)

    return Device(shoot=device.screenshot, click=lambda x, y: device.click(x, y),
                  swipe=lambda x1, y1, x2, y2, dur=None: device.swipe(x1, y1, x2, y2, duration=dur), sleep=time.sleep, calib=calib)  # fmt: skip


def _autofix_support() -> None:
    """第 2 步：编成和方案不一致的格子自动改（hiro.produce.formation_steps.autofix_support）。"""
    if not _autofix_on():
        return
    from hiro import kaa_env
    from hiro.produce import formation_steps

    formation_steps.autofix_support(_writer_device("支援卡"), kaa_env.ACTIVE_PLAN)


def _check_memory_set() -> None:
    """有开局编成方案时核对回忆编成号和四格回忆卡（hiro.produce.formation_steps.check_memory_set）。"""
    from hiro import kaa_env
    from hiro.produce import formation_steps

    formation_steps.check_memory_set(device.screenshot(), kaa_env.ACTIVE_PLAN, kaa_env.MEMORY_SET, shoot=device.screenshot)


def select_set(index: int) -> None:
    """KAA prepare._select_set 换成这个：翻编成号在 hiro.produce.prep_steps（自己的 OCR，读不出有超时）。
    翻不到只记日志（同 KAA），后面的编成核对（_check_support / _check_memory_set）会停下。"""
    from hiro.produce import prep_steps

    if not prep_steps.select_set(index, shoot=device.screenshot, tap=lambda x, y: device.click(x, y)):
        logger.error("编成没翻到 %d 号，交给编成核对", index)


def _rent_step2(orig):
    def wrapped(set_number=None, auto_set=None):
        if auto_set:
            from hiro import kaa_env

            if kaa_env.SUPPORT_SET == kaa_env.AUTO:  # 使用者明确写了 HIRO_SUPPORT_SET=auto：交给 KAA 的おまかせ
                logger.warning("支援卡：HIRO_SUPPORT_SET=auto，由 KAA「おまかせ」选支援卡和租借")
                return orig(set_number=set_number, auto_set=auto_set)
            raise RuntimeError("支援卡「おまかせ」没有被明确选择（HIRO_SUPPORT_SET=auto），auto_set_support_card 必须为 False")
        from kotonebot import device

        from hiro.vision.ui_templates import find_enabled, tpl

        if not tpl("Produce.TextStepIndicator2").exists(device.screenshot()):
            return False
        if set_number is not None:
            select_set(set_number)
        _autofix_support()
        _check_support(device.screenshot())
        if find_enabled("Produce.Step2.ButtonNext", device.screenshot()) is not None:
            logger.info("支援卡：编成 %s 已满（含租借格），直接下一步", set_number)
            return True
        if RENTAL_PICK is not None and RENTAL_PICK():
            for _ in range(10):
                if find_enabled("Produce.Step2.ButtonNext", device.screenshot()) is not None:
                    return True
                time.sleep(0.5)
        logger.error("支援卡：编成 %s 的租借格还空着，选择系统未交付，停下交总控手动挑（不让 KAA 确认第一张）", set_number)
        raise RentalPending(f"支援卡编成 {set_number} 的租借格需要手动挑选")

    wrapped._hiro_rent = True
    return wrapped


def _snap_step3(orig):
    """第 3 步「メモリー選択」：存一帧并记下有没有租借入口（用户 2026-11-02 同意只看），然后照常走 KAA 的固定编成。"""

    def wrapped(*a, **k):
        try:
            from pathlib import Path

            img = device.screenshot()
            d = Path("reports/live/rental")
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"step3-memory-{time.strftime('%Y%m%d-%H%M%S')}.png"
            imgio.imwrite(str(p), img)
            texts = [t.text for t in screen_texts(img)]
            hit = [t for t in texts if "レンタル" in t]
            logger.info("第 3 步回忆选择：截图 %s；租借字样 %s", p, hit or "无")
        except Exception as e:  # noqa: BLE001  只是观察，不能影响开局
            logger.warning("第 3 步截图失败：%s", e)
        r = orig(*a, **k)
        if r:
            _check_memory_set()  # KAA 选完编成、点「次へ」之前（第 3 步只核对，回忆在编成菜单里写：hiro.produce.formation_menu）
        return r

    wrapped._hiro_snap = True
    return wrapped


_ORIG_ACTION_COMMIT = None  # KAA 原版（休息走它）


def action_commit(self, action) -> None:
    """选行动提交：同 KAA ActionSelectContext.commit，去掉点完后的固定 sleep(2)——之后的 wait_disappear 本来就轮询到按钮消失
    （第二十一局选拔：选行动每步执行 4–5s）。休息的确认框流程照旧。"""
    from kaa.tasks.produce.new.consts import ProduceAction

    actions = self.fetch_available_actions()
    if action not in actions[0]:
        raise ValueError(f"Action {action} is not available now.")
    button = actions[1][actions[0].index(action)]
    if action == ProduceAction.REST:
        return _ORIG_ACTION_COMMIT(self, action)
    for _ in range(3):
        button.click()
        time.sleep(0.3)
    logger.info("Entered action: %s", action)
    self.controller.wait_disappear(button.prefab)


PREP_STEPS = ("step1", "step2", "step3", "step4", "_select_set", "_select_idol")  # KAA 培育准备里每一步前后各报一次进展（D38）


def _prep_progress(fn, name: str):
    """KAA 培育准备的一步：开始前、结束后（含异常）各报一次进展，空转看门狗不会把整段准备算成没进展。
    单步自己卡住 STALL_S 秒照样停机。functools.wraps 带上原函数的 _hiro_* 标记，重复 apply 不会多包。"""
    import functools

    from hiro.loop.repeat_guard import progress

    @functools.wraps(fn)
    def wrapped(*a, **k):
        progress(f"培育准备 {name} 开始")
        try:
            return fn(*a, **k)
        finally:
            progress(f"培育准备 {name} 结束")

    wrapped._hiro_progress = True
    return wrapped


UNREAD_COMMU_MODULES = ("kaa.tasks.actions.commu", "kaa.tasks.produce.new.strategies.base", "kaa.tasks.produce.new.strategies.hif_grind",
                        "kaa.tasks.produce.shared.common", "kaa.tasks.produce.shared.produce_end", "kaa.tasks.start_game")  # fmt: skip


def _unread_commu(orig):
    """KAA 的 handle_unread_commu 看到未读确认框（スキップ確認 / 快进确认）就点「はい」跳过。HIRO_UNREAD_STORY=stop 时改成
    点キャンセル、停下等人（hiro.loop.unread_story）；skip（缺省）照旧。SKIP / 快进按钮照常点：读过的剧情不弹框直接跳。"""

    def wrapped(img=None):
        from hiro.loop import unread_story

        if not unread_story.stopping():
            return orig(img)
        from kaa.game_ui import dialog
        from kaa.tasks import R
        from kotonebot import device

        from hiro.vision.ocr import screen_texts

        device.screenshot()
        if R.Common.TextSkipCommuComfirmation.exists() or R.Common.TextFastforwardCommuDialogTitle.exists():
            unread_story.handle(cancel=lambda: dialog.no(), confirm=lambda: dialog.yes(),
                                texts=lambda: [(b.text, b.rect.center.y) for b in screen_texts(device.screenshot())],
                                snap=device.screenshot)  # fmt: skip
            return True
        return orig(img)

    wrapped._hiro_unread = True
    return wrapped


def _patch_unread_commu() -> None:
    import importlib

    base = importlib.import_module("kaa.tasks.actions.commu")
    orig = base.handle_unread_commu
    if getattr(orig, "_hiro_unread", False):
        return
    patched = _unread_commu(orig)
    for name in UNREAD_COMMU_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("未读剧情补丁：导入 %s 失败：%r", name, e)
            continue
        if getattr(mod, "handle_unread_commu", None) is orig:
            mod.handle_unread_commu = patched


def apply() -> None:
    import importlib

    import kaa.game_ui.idols_overview as io
    from kaa.tasks.produce.new.page import ActionSelectContext, PItemSelectContext, SkillCardChangeContext

    global _ORIG_ACTION_COMMIT
    if _ORIG_ACTION_COMMIT is None and ActionSelectContext.commit is not action_commit:
        _ORIG_ACTION_COMMIT = ActionSelectContext.commit

    # kaa.tasks 导出了同名任务函数 produce，遮住了子包属性，只能按模块全名导入
    prep = importlib.import_module("kaa.tasks.produce.prepare")
    io.locate_idol = locate_idol_by_swipe
    prep.locate_idol = locate_idol_by_swipe
    SkillCardChangeContext.commit_stage1 = change_stage1
    SkillCardChangeContext.commit_stage2 = change_stage2
    PItemSelectContext.commit = pitem_commit
    ActionSelectContext.commit = action_commit
    from kaa.tasks import R
    from kaa.tasks.produce.new.page import ProducePage

    ProducePage._check_action_like = _guard_action_like(ProducePage._check_action_like)
    global _ORIG_DISCARD_CONFIRM
    if R.Produce.BreakProduceDialog.ButtonConfirm is not NeverConfirmDiscard:
        _ORIG_DISCARD_CONFIRM = R.Produce.BreakProduceDialog.ButtonConfirm
    R.Produce.BreakProduceDialog.ButtonConfirm = NeverConfirmDiscard  # do_produce 按属性名取，替换后它点不到「開始する」
    prep._select_set = select_set  # 编成号翻页用自己的 OCR（先于下面 PREP_STEPS 的进展包装）
    if not getattr(prep.step3, "_hiro_snap", False):
        prep.step3 = _snap_step3(prep.step3)
    if not getattr(prep.step2, "_hiro_rent", False):
        prep.step2 = _rent_step2(prep.step2)
    if not getattr(prep.step4, "_hiro_memory_check", False):
        prep.step4 = _check_memory_then(prep.step4)
        prep.step4._hiro_memory_check = True
    for name in PREP_STEPS:
        fn = getattr(prep, name, None)
        if fn is not None and not getattr(fn, "_hiro_progress", False):
            setattr(prep, name, _prep_progress(fn, name))
    _patch_unread_commu()  # HIRO_UNREAD_STORY=stop：未读剧情不跳，停下等人
    from hiro.loop import win_desktop

    win_desktop.apply()  # DMM 后台点击：桌面暂时不可用（UAC / 锁屏）时等它恢复，不让主循环连崩
    from hiro.loop import kaa_usage

    kaa_usage.install()  # 最后挂：数的是补丁之后还在跑的 KAA 代码（去 KAA 第 0 步，只记不改行为）
    logger.info("已启用 KAA 补丁：locate_idol 改为上滑翻页；授業换卡放宽卡片定位、阶段 2 不换保护卡；H.I.F P 道具按实测位置点击；破弃对话框只点キャンセル")
