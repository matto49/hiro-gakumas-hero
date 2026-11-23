"""开局和续跑（去 KAA 设计稿 §3.5，替代 KAA do_produce / prepare / goto_home 和 hiro.loop.resume.enter_produce）。

全部跑在 hiro.runtime.Runtime 上：每一步 rt.drive（每拍过暂停检查点和中断层、带超时），点击都过危险点击拦截。
模板走 hiro.vision.ui_templates（KAA 的同一批模板，tools/assets/ui_from_kaa.py 搬来）。不 import KAA / kotonebot。

规矩（同旧流程）：
- 破弃框（其他端末有进行中的培育）只点キャンセル，停机，是否破弃由用户决定；别端末「開始する」绝不点。
- 新开时看到「再開する」（本机有进行中的培育）停下，让使用者改用 resume。
- AP 不足只喝 APドリンク（hiro.loop.ap_refill；确认框有ジュエル就取消）。
- 「プロデュース開始」只在 new 流程里点（使用者放行开局后才会跑 new）。

new_produce：培育入口 → 翻到 H.I.F → 选拔 / 本战（AP）→ 编成第 1–4 步（选偶像 / 支援卡和租借 / 回忆 / 加成；本战核对选拔回忆）
→ プロデュース開始 → 跳过交流。选偶像、翻编成号、核对、租借、回忆选择都用 hiro.produce 里不依赖 KAA 的实现。
还没有调用方：run_hif 的 HIRO_RUNTIME=own 接线由平台模块做。
"""

from __future__ import annotations

import logging

from hiro.loop.errors import ResumeBlocked
from hiro.runtime import nav
from hiro.vision.ocr import screen_texts
from hiro.vision.ui_templates import box, point

logger = logging.getLogger(__name__)

ENTER_TIMEOUT = 40.0
LOGO_TIMEOUT = 30.0
MODE_TIMEOUT = 40.0
START_RETAP_S = 2.5  # 点「プロデュース開始」后还在第 4 步：隔这么久再点
START_SKIP_S = 5.0  # 点开始后这么久内一直找「跳过交流」单选框 / 确认按钮（同 KAA）

LOGOS = ("Produce.LogoHajime", "Produce.LogoNia", "Produce.LogoHif")
MODE_BUTTON = {"qualify": "Produce.ButtonHifQualify", "main": "Produce.ButtonHifMain"}
DISCARD = (
    "游戏提示「プロデュースデータの破棄」：其他端末有进行中的培育。已点「キャンセル」，不自动新开；是否破弃由用户决定"
)


def _ocr(img):
    return [(t.text, t.conf, tuple(t.rect)) for t in screen_texts(img)]


def enter_produce(rt, *, new: bool, timeout: float = ENTER_TIMEOUT) -> str:
    """从首页点「プロデュース中」进培育入口。返回 "new"（到了剧本选择页）或 "resume"（本机有进行中的培育，「再開する」框）。
    new=True 时看到「再開する」就停（ResumeBlocked：应改用 resume）；破弃框只点キャンセル并停。"""
    nav.goto_home(rt)

    def step(f):
        if nav.find(f, "Produce.BreakProduceDialog.Title") is not None:
            if (c := next((t for t in f.texts if "キャンセル" in t.text), None)) is not None:
                rt.device.tap(*c.rect.center)
            raise ResumeBlocked(DISCARD)
        if any(nav.find(f, n) is not None for n in LOGOS):
            return "new"
        if nav.find(f, "Produce.ButtonResume") is not None:
            if new:
                raise ResumeBlocked("本机有进行中的培育（看到「再開する」），请用 resume 继续，而不是新开")
            return "resume"
        if nav.at_home(f):
            rt.device.tap(*box("Produce.BoxProduceOngoing").center)
        return None

    return rt.drive(step, timeout, "进培育入口")


SCENARIO_ORDER = ("Produce.LogoHajime", "Produce.LogoNia", "Produce.LogoHif")  # 剧本页从左到右：初、N.I.A、H.I.F
FLIP_WAIT_S = 4.0  # 点了左右箭头后等画面换到别的剧本；这么久还是原来那个（或认不出）才再点


def select_scenario(rt, logo: str, timeout: float = LOGO_TIMEOUT) -> None:
    """剧本选择页翻到 logo：认出当前是哪个剧本，按顺序往前 / 往后点一下箭头，等画面换到别的剧本再决定下一下。
    原来每拍都点（N.I.A 采样第一次实机 11-17：翻页动画中认不出 logo 又连着点，来回翻，30 秒停在 H.I.F）。"""

    state = {"at": None, "from": None}

    def step(f):
        cur = next((n for n in SCENARIO_ORDER if nav.find(f, n) is not None), None)
        if cur == logo:
            return True
        now = rt.clock()
        if state["at"] is not None and (cur is None or cur == state["from"]) and now - state["at"] < FLIP_WAIT_S:
            return None  # 刚点过：翻页动画还没完 / 画面还没换
        if cur is None:
            return None  # 认不出是哪个剧本（还没到剧本页 / 动画中）：不点
        name = "Produce.PointPrev" if SCENARIO_ORDER.index(cur) > SCENARIO_ORDER.index(logo) else "Produce.PointNext"
        logger.info("剧本页：现在是 %s，要 %s，点 %s", cur, logo, name)
        rt.device.tap(*point(name))
        state["at"], state["from"] = now, cur
        return None

    rt.drive(step, timeout, f"翻到 {logo}")


def select_hif(rt, timeout: float = LOGO_TIMEOUT) -> None:
    """剧本选择页翻到 H.I.F（select_scenario）。"""
    select_scenario(rt, "Produce.LogoHif", timeout)


IN_PRODUCE_PAGES = ("ACTION_SELECT", "STUDY", "OUTING", "CONSULT", "SELECT_CARD", "SELECT_DRINK", "SELECT_PITEM", "HIF_EXAM_BRIEFING",
                    "SPECIAL_GUIDANCE", "PRACTICE", "HIF_EXAM", "EXAM")  # fmt: skip  # 已经在培育里的页面：开局流程到此为止，交主循环


FINALS_START_BUTTON = "Produce.ButtonHifFinalsStart"  # 「H.I.F本戦へ」按钮字（从 DMM 实机帧切的；本战開始確認页 0.96+，其余 607 张帧 ≤ 0.46）


def _norm(s: str) -> str:
    return s.replace(" ", "").replace("ヘ", "へ").replace("戰", "戦")


def _finals_button(f):
    """「H.I.F本戦へ」按钮的位置 (x, y)：先找按钮模板，再找 OCR 读到的底部「本戦」字。没有返回 None。"""
    if (m := nav.find(f, FINALS_START_BUTTON)) is not None:
        return tuple(m.rect.center)
    b = next((t for t in f.texts if "本戦" in _norm(t.text) and t.rect.center.y > 1000), None)
    return tuple(b.rect.center) if b is not None else None


def _finals_start(f) -> bool:
    """本战「開始確認」页（选拔回忆概要 + 「H.I.F本戦へ」按钮）。按钮字我们的 OCR 常读不出（橙底白字：三张实机帧都没读到），
    RapidOCR 也读偏过（Windows 9580c0c 11-17：没认出 → 落到 Step1.ButtonNext 模板，把「H.I.F本戦へ」当次へ点了）：
    按钮模板命中就算；没命中时 OCR 要同时读到页头「開始確認」和底部「本戦」（ヘ / へ、戰 / 戦 都认）。"""
    if nav.find(f, FINALS_START_BUTTON) is not None:
        return True
    texts = [(_norm(t.text), t.rect.center.y) for t in f.texts]
    return any("本戦" in t and y > 1000 for t, y in texts) and any("開始確認" in t for t, y in texts)


def _in_produce(rt, f) -> bool:
    return rt.classify(f.img).name in IN_PRODUCE_PAGES


def choose_mode(rt, mode: str, *, extra_ap: int | None = None, timeout: float = MODE_TIMEOUT):
    """点 H.I.F 的选拔 / 本战，直到进了第 1 步（编成页）。AP 不足时喝 APドリンク 再点（只打选拔时本战还要 20 AP，一起补）。
    本战：选拔后保留的本战没有编成页，点了「本戦」直接到「開始確認」页（也算进了）；画面已经是培育里的页面（本战已经开始了）
    返回 "started"，开局到此为止（Mac 账号 2 11-17：原来接着等「本战第 1 步」，20 秒超时停机）。"""
    from hiro.loop import ap_refill

    name = MODE_BUTTON[mode]
    extra = (0 if mode == "main" else 20) if extra_ap is None else extra_ap
    refilled = {"done": False}

    def step(f):
        if nav.find(f, "Produce.Step1.ButtonNext") is not None or nav.find(f, "Produce.TextStepIndicator1") is not None:
            return True
        if mode == "main" and _finals_start(f):
            return True
        if mode == "main" and _in_produce(rt, f):
            return "started"
        if nav.find(f, "Produce.TextAPInsufficient") is not None:
            if refilled["done"]:
                raise ResumeBlocked("喝过 APドリンク 后仍然 AP 不足，停下")
            r = ap_refill.refill(lambda: rt.screen.shot().img, rt.device.tap, _ocr, extra_ap=extra, sleep=rt.sleep, clock=rt.clock,
                                 log=logger.warning)  # fmt: skip
            if not r["ok"]:
                raise ResumeBlocked(f"AP 不足，APドリンク 没补上：{r['error']}")
            refilled["done"] = True
            return None
        if (m := nav.find(f, name)) is not None:
            logger.info("进 H.I.F %s：点 %s %s", mode, name, tuple(m.rect.center))
            nav.tap_match(rt, m)
        return None

    return rt.drive(step, timeout, f"进 H.I.F {mode}")


def start(rt, timeout: float = 20.0) -> None:
    """第 4 步点「プロデュース開始」，之后 START_SKIP_S 秒内点「コミュをスキップ」单选框和确认按钮（同 KAA）。"""

    # 点了要看到离开第 4 步（开始按钮不在了：弹出设置框 / 跳过交流 / 进培育）才算点上。z6（11-19 10:51 DMM）：第 3 步刚切到第 4 步就点，
    # 被切换动画吞了，原来点了就当成功交给主循环，PRODUCE_START_CONFIRM 没处理器 180s 停机。还在第 4 步就隔 START_RETAP_S 再点
    state = {"last": None}

    def press(f):
        m = nav.find(f, "Produce.Step4.ButtonProduceStart")
        if m is None:
            return True if state["last"] is not None else None
        if state["last"] is None or rt.clock() - state["last"] >= START_RETAP_S:
            nav.tap_match(rt, m)
            state["last"] = rt.clock()
        return None

    rt.drive(press, timeout, "点プロデュース開始")
    t0 = rt.clock()
    while rt.clock() - t0 < START_SKIP_S:
        f, done = rt.tick()
        if (
            done is None and nav.find(f, "Produce.Step4.ButtonProduceStart") is None
        ):  # 第 4 步页还在时不点（「決定」会误命中开始按钮一带）
            for name in ("Produce.RadioTextSkipCommu", "Common.ButtonConfirmNoIcon"):
                if (m := nav.find(f, name)) is not None:
                    nav.tap_match(rt, m)
        rt.sleep(0.3)


# ---------------------------------------------------------------- 第 1 步：选偶像（同 KAA prepare.step1 / _select_idol）
IDOL_THRESHOLD = 20  # 同 KAA match_idol
STEP_TIMEOUT = 20.0
IDOL_TRIES = 5


def idol_selected(f, skin_id: str) -> bool:
    """第 1 步左上「已选偶像」框里是不是 skin_id（偶像卡图库，hiro.vision.idols）。"""
    from hiro.vision import idols

    x1, y1, x2, y2 = box("Produce.BoxSelectedIdol")
    hit = idols.query(f.img[y1:y2, x1:x2], threshold=IDOL_THRESHOLD)
    return hit is not None and hit[0].startswith(skin_id)


def _close_dialog(rt, f) -> bool:
    """普通提示框（Another 偶像可选等）：点 ✕ 或「キャンセル / 閉じる」。"""
    if (m := nav.find(f, "Common.ButtonIconClose")) is not None:
        nav.tap_match(rt, m)
        return True
    if (t := next((t for t in f.texts if any(w in t.text for w in ("キャンセル", "閉じる"))), None)) is not None:
        rt.device.tap(*t.rect.center)
        return True
    return False


def select_idol(rt, skin_id: str) -> None:
    """打开偶像一览 → 翻页找到 skin_id → 点它 → 确认，回到第 1 步。"""
    from hiro.produce import idol_select

    def open_list(f):
        if nav.find(f, "Produce.IdolOverview.ButtonConfirm") is not None:
            return True
        if nav.find(f, "Common.ButtonConfirmNoIcon") is None:
            if (m := nav.find(f, "Produce.ButtonPIdolOverview")) is not None:
                nav.tap_match(rt, m)
        elif nav.find(f, "Produce.TextAnotherIdolAvailableDialog") is not None:
            _close_dialog(rt, f)  # 有新的アナザー偶像可选的提示
        return None

    rt.drive(open_list, STEP_TIMEOUT, "打开偶像一览")
    pos = idol_select.locate(skin_id, shoot=lambda: rt.screen.shot().img, sleep=rt.sleep,
                             swipe=lambda x1, y1, x2, y2, d: rt.device.swipe(x1, y1, x2, y2, duration=d))  # fmt: skip
    if pos is None:
        raise ResumeBlocked(f"偶像一览里找不到 {skin_id}")

    # 卡位只点一次：z6（11-19 10:37 DMM 3.6.0）点了「決定」后一览淡出几帧里「決定」还认得出，原来每帧都再点一次卡位中心 (104,898)，
    # 那时已回到第 1 步，正好点在底部轮播的 月村手毬 上，选成了她。之后只在一览本体还在时隔一会儿补点「決定」
    state = {"picked": False, "last": 0.0}

    def confirm(f):
        if nav.find(f, "Produce.TextStepIndicator1") is not None:
            return True
        if (m := nav.find(f, "Common.ButtonConfirmNoIcon")) is None:
            return None
        if not state["picked"]:
            rt.device.tap(*pos.center)
            rt.sleep(0.3)
            nav.tap_match(rt, m)
            state["picked"], state["last"] = True, rt.clock()
        elif (
            nav.find(f, "Produce.IdolOverview.ButtonConfirm") is not None
            and rt.clock() - state["last"] >= CONFIRM_RETAP_S
        ):
            nav.tap_match(rt, m)  # 一览还在：「決定」没点上，只补点「決定」
            state["last"] = rt.clock()
        return None

    rt.drive(confirm, STEP_TIMEOUT, "确认偶像")


CONFIRM_RETAP_S = 2.0  # 偶像一览点过「決定」后一览还在时，隔这么久才补点「決定」
SELECTED_RECHECKS = 2  # 第 1 步判「已选偶像」不是目标时再看几帧（轮播入场动画中读不准）
SELECTED_RECHECK_WAIT = 0.6


def _selected_settled(rt, f, skin_id: str) -> bool:
    """第 1 步刚出现、底部轮播还在动时「已选偶像」框读不准（z4 / z5 / z6 都先判成「选中的不是」去开一览）：
    不是目标时隔 SELECTED_RECHECK_WAIT 再看几帧，有一帧对上就算已选好。"""
    if idol_selected(f, skin_id):
        return True
    for _ in range(SELECTED_RECHECKS):
        rt.sleep(SELECTED_RECHECK_WAIT)
        if idol_selected(rt.screen.shot(), skin_id):
            return True
    return False


def step1(rt, skin_id: str) -> None:
    """第 1 步：目标偶像没选上就去一览里选（最多 IDOL_TRIES 次），然后点「次へ」到第 2 步。"""
    f = rt.drive(
        lambda f: f if nav.find(f, "Produce.TextStepIndicator1") is not None else None, STEP_TIMEOUT, "等第 1 步"
    )
    for _ in range(IDOL_TRIES):
        if _selected_settled(rt, f, skin_id):
            break
        logger.info("第 1 步：选中的不是 %s，去偶像一览选", skin_id)
        select_idol(rt, skin_id)
        f = rt.screen.shot()
    else:
        raise ResumeBlocked(f"选了 {IDOL_TRIES} 次，第 1 步选中的仍不是 {skin_id}")

    def next_step(f):
        if (
            nav.find(f, "Produce.TextStepIndicator2") is not None
            or nav.find(f, "Produce.TextStepIndicator4Or2") is not None
        ):
            return True
        _tap_step1_next(rt, f)
        return None

    rt.drive(next_step, STEP_TIMEOUT, "第 1 步点次へ")


# ---------------------------------------------------------------- 第 2 步：支援卡编成 + 租借（同 kaa_patches._rent_step2）
class _RentalDevice:
    """formation_writer / memory_picker 要的设备形状（screenshot / click / swipe(…, duration)），转到 runtime 的设备上。"""

    def __init__(self, rt):
        self.rt = rt

    def screenshot(self):
        return self.rt.screen.shot().img

    def click(self, x, y):
        self.rt.device.tap(x, y)

    def swipe(self, x1, y1, x2, y2, duration=None):
        self.rt.device.swipe(x1, y1, x2, y2, duration=0.3 if duration is None else duration)


def _next_enabled(f, name: str):
    from hiro.vision.ui_templates import find_enabled

    return find_enabled(name, f.img)


def step2(
    rt, *, set_no: int | None, plan: dict | None = None, rental_attr: str | None = None, main_attr: str | None = None
) -> None:
    """第 2 步：翻到支援卡编成 set_no → 按方案自动改（HIRO_FORMATION_AUTOFIX=1）→ 核对 → 租借格空着就挑一张租借 → 次へ。
    编成号翻不到 / 核对不过 / 挑不到租借 都停（FormationMismatch / ResumeBlocked），不交给おまかせ。"""
    from hiro.produce import formation_steps, prep_steps
    from hiro.produce.formation_writer import Device as WriterDevice

    rt.drive(
        lambda f: True if nav.find(f, "Produce.TextStepIndicator2") is not None else None, STEP_TIMEOUT, "等第 2 步"
    )
    if set_no is None:
        raise ResumeBlocked("没有支援卡编成号（HIRO_SUPPORT_SET / 开局编成方案），不开局")
    if not prep_steps.select_set(
        set_no, shoot=lambda: rt.screen.shot().img, tap=rt.device.tap, sleep=rt.sleep, clock=rt.clock
    ):
        raise ResumeBlocked(f"支援卡编成翻不到 {set_no} 号")
    dev = _RentalDevice(rt)
    formation_steps.autofix_support(
        WriterDevice(shoot=dev.screenshot, click=dev.click, swipe=dev.swipe, sleep=rt.sleep), plan
    )
    formation_steps.check_support(
        rt.screen.shot().img, plan, set_no, shoot=lambda: rt.screen.shot().img, sleep=rt.sleep
    )
    if _next_enabled(rt.screen.shot(), "Produce.Step2.ButtonNext") is None:  # noqa: SIM102
        # 租借（hiro.produce.rental）走「当前设备」门面 hiro.devices.active（平台模块 feat_matto_handlers）：run_hif 在新主循环里
        # active.use(rt) 后它点的就是带拦截的 rt.device
        if not formation_steps.rental_pick(plan, rental_attr, main_attr):
            raise ResumeBlocked(f"支援卡编成 {set_no} 的租借格挑不到合适的卡，停下（不让おまかせ确认第一张）")

    def next_step(f):
        if nav.find(f, "Produce.TextStepIndicator3") is not None:
            return True
        if (m := _next_enabled(f, "Produce.Step2.ButtonNext")) is not None:
            nav.tap_match(rt, m)
        return None

    rt.drive(next_step, STEP_TIMEOUT, "第 2 步点次へ")


# ---------------------------------------------------------------- 第 3 步：回忆编成（同 KAA prepare.step3 + kaa_patches._snap_step3）
MEMORY_UNSET_TITLE = "メモリー編成確認"  # 第 3 步点次へ 时有空的回忆格：「未編成の枠があります このまま編成を完了してもよろしいですか？」


def step3(rt, *, set_no: int | None, plan: dict | None = None) -> None:
    """第 3 步：翻到回忆编成 set_no → 核对编成号和回忆卡（只核对，不写；回忆在编成菜单里写，hiro.produce.formation_menu）
    → 次へ（中途「有可用租借次数」提示点确认），到第 4 步。"""
    from hiro.produce import formation_steps, prep_steps

    rt.drive(
        lambda f: True if nav.find(f, "Produce.TextStepIndicator3") is not None else None, STEP_TIMEOUT, "等第 3 步"
    )
    if set_no is None:
        raise ResumeBlocked("没有回忆编成号（HIRO_MEMORY_SET / 开局编成方案），不开局")
    if not prep_steps.select_set(
        set_no, shoot=lambda: rt.screen.shot().img, tap=rt.device.tap, sleep=rt.sleep, clock=rt.clock
    ):
        raise ResumeBlocked(f"回忆编成翻不到 {set_no} 号")
    formation_steps.check_memory_set(
        rt.screen.shot().img, plan, set_no, shoot=lambda: rt.screen.shot().img, sleep=rt.sleep
    )

    want = len(((plan or {}).get("memory") or {}).get("chosen") or [])

    def next_step(f):
        if nav.find(f, "Produce.TextStepIndicator4Or2") is not None:
            return True
        if f.has_dialog and MEMORY_UNSET_TITLE in ((f.dialog or {}).get("title") or ""):
            # 「メモリー編成確認」：有回忆格空着。方案本来就不到 4 份（空格是有意的）才点「完了する」；
            # 方案要 4 份（或没有方案）却空着：点キャンセル，停下（账号 2 11-16：原来没处理，卡在这个框）
            if 0 < want < 4:
                if (
                    b := next((t for t in f.texts if "完了する" in t.text and t.rect.center.y > 1080), None)
                ) is not None:
                    rt.device.tap(*b.rect.center)
                return None
            if (b := next((t for t in f.texts if "キャンセル" in t.text and t.rect.center.y > 1080), None)) is not None:
                rt.device.tap(*b.rect.center)
            raise ResumeBlocked(
                f"第 3 步回忆格有空的（方案要 {want or '?'} 份），游戏问「このまま編成を完了」：点了キャンセル，不开局"
            )
        if nav.find(f, "Produce.TextRentAvailable") is not None:
            if (m := nav.find(f, "Produce.RentAvailableConfirmButton")) is not None:
                nav.tap_match(rt, m)
        elif (m := nav.find(f, "Produce.Step3.ButtonNext")) is not None:
            nav.tap_match(rt, m)
        return None

    rt.drive(next_step, STEP_TIMEOUT, "第 3 步点次へ")


# ---------------------------------------------------------------- 第 4 步：加成 / 本战开局前的选拔回忆
def _set_checkbox(rt, name: str, want: bool) -> None:
    from hiro.vision.ui_templates import checkbox_checked

    f = rt.screen.shot()
    if (m := nav.find(f, name, threshold=0.6)) is None:
        return
    if (state := checkbox_checked(f.img, m)) is not None and state != want:
        nav.tap_match(rt, m)
        rt.sleep(1.0)


def step4(rt, *, note_boost: bool = False, pt_boost: bool = False) -> None:
    """第 4 步（本战是「2.開始確認」）：按设置勾 / 不勾两个加成（同 KAA step4）。"""
    rt.drive(
        lambda f: True if nav.find(f, "Produce.TextStepIndicator4Or2") is not None else None, STEP_TIMEOUT, "等第 4 步"
    )
    _set_checkbox(rt, "Produce.Step4.CheckboxNoteBoost", note_boost)
    _set_checkbox(rt, "Produce.Step4.CheckboxPtBoost", pt_boost)


def check_main_memory(rt, expect: tuple[str, ...]) -> None:
    """本战开局前：开始确认页上必须是指定的那份选拔回忆（属性数字）；不是就去回忆列表选，选完再核，还不对抛 MemoryMismatch。"""
    if not expect:
        return
    from hiro.produce import memory_select

    shoot = lambda: rt.screen.shot().img  # noqa: E731
    missing = memory_select.memory_missing_on_page(expect, shoot=shoot, sleep=rt.sleep)
    if missing:
        memory_select.select_memory(expect, shoot=shoot, click=rt.device.tap, sleep=rt.sleep,
                                    swipe=lambda x1, y1, x2, y2, d=0.3: rt.device.swipe(x1, y1, x2, y2, duration=d))  # fmt: skip
        missing = memory_select.memory_missing_on_page(
            expect, shoot=shoot, sleep=rt.sleep
        )  # 已选中的那份不在「変更」列表里，关掉列表后再核
    if missing:
        raise memory_select.MemoryMismatch(f"选拔回忆不符：页面上找不到 {missing}")
    logger.info("本战开局：选拔回忆核对通过（%s）", "/".join(expect))


# ---------------------------------------------------------------- 串起来
def prepare(rt, mode: str, *, idol: str, support_set: int | None = None, memory_set: int | None = None, plan: dict | None = None,
            rental_attr: str | None = None, main_attr: str | None = None, expect_memory: tuple[str, ...] = (),
            note_boost: bool = False, pt_boost: bool = False) -> bool:  # fmt: skip
    """编成第 1–4 步。选拔：选偶像 → 支援卡 + 租借 → 回忆 → 加成；本战：第 1 步直接次へ到「2.開始確認」→ 核对选拔回忆 → 加成
    （同 KAA prepare / prepare_hif_main）。返回 True：本战已经开始了（开始确认页点了「H.I.F本戦へ」，或者本来就在培育里），
    调用方不用再点プロデュース開始。"""
    if mode == "qualify":
        step1(rt, idol)
        step2(rt, set_no=support_set, plan=plan, rental_attr=rental_attr, main_attr=main_attr)
        step3(rt, set_no=memory_set, plan=plan)
    else:

        def to_confirm(f):
            if _finals_start(f):
                return "finals_start"
            if nav.find(f, "Produce.TextStepIndicator4Or2") is not None:
                return "step4"
            if _in_produce(rt, f):
                return "started"
            _tap_step1_next(rt, f)  # 只在认出第 1 步页时点；认不出的页等，不点底部大按钮
            return None

        r = rt.drive(to_confirm, STEP_TIMEOUT, "本战第 1 步点次へ")
        if r == "started":
            logger.warning("本战已经开始了（没经过开始确认页的选拔回忆核对）：交给主循环")
            return True
        check_main_memory(rt, expect_memory)
        if r == "finals_start":
            return _press_finals_start(rt)
    step4(rt, note_boost=note_boost, pt_boost=pt_boost)
    return False


def _press_finals_start(rt) -> bool:
    """「開始確認」页核对过选拔回忆后点「H.I.F本戦へ」（按钮模板，退路 OCR 字），等离开这一页。返回 True（本战已开始，不用再点プロデュース開始）。"""

    def press(f):
        if not _finals_start(f):
            return True  # 离开开始确认页了（之后的设置框 / 教程 / 选行动由中断层和主循环接）
        if (pos := _finals_button(f)) is not None:
            logger.info("本战开局：点「H.I.F本戦へ」%s", pos)
            rt.device.tap(*pos)
            rt.sleep(1.5)
        return None

    rt.drive(press, STEP_TIMEOUT, "点 H.I.F本戦へ")
    return True


STEP1_RETAP_S = 5.0  # 点过第 1 步「次へ」后这么久内不再点（等页面换掉，同 KAA prepare_hif_main 点完等 5 秒）


def _tap_step1_next(rt, f) -> bool:
    """第 1 步的「次へ」：只有认出第 1 步页（步骤指示器 TextStepIndicator1）才点 Step1.ButtonNext。
    这个模板是底部大橙按钮，本战開始確認页的「H.I.F本戦へ」也能命中（Windows 9580c0c 11-17：開始確認页没认出，
    把「H.I.F本戦へ」当次へ点了，选拔回忆核对没跑）；同 KAA prepare_hif_main 只在 TextStepIndicator1 时点。
    点过以后 STEP1_RETAP_S 秒内不再点：页面切换慢时下一拍还是第 1 步，再点一下落到「2.開始確認」同一位置的
    「プロデュース開始」上，本战直接开始、选拔回忆没核对（Mac p15-main 11-17，开局点击帧 3 / 4）。"""
    if nav.find(f, "Produce.TextStepIndicator1") is None:
        return False
    if (m := nav.find(f, "Produce.Step1.ButtonNext")) is None:
        return False
    now = rt.clock()
    last = getattr(rt, "_step1_tap_at", None)
    if last is not None and now - last < STEP1_RETAP_S:
        return False
    logger.info("第 1 步：点 Step1.ButtonNext %s", tuple(m.rect.center))
    nav.tap_match(rt, m)
    rt._step1_tap_at = now
    return True


def on_start_confirm(f) -> bool:
    """画面是本战的开始确认页：「H.I.F本戦へ」那种（选拔后保留的本战，_finals_start），或者从首页进本战的「2.開始確認」
    （步骤指示器 + 页头读到「2.開始確認」；选拔的「4.開始確認」不算）。"""
    import re

    if _finals_start(f):
        return True
    if nav.find(f, "Produce.TextStepIndicator4Or2") is None:
        return False
    return any(re.match(r"^2\.?開始確認", t.text.replace(" ", "")) and t.rect.center.y < 90 for t in f.texts)


def _snap_taps(rt):
    """开局阶段每次点击前存一帧（点之前看到的画面）到 trace，备注点了哪里（Windows 9580c0c 11-17：开局误点了，trace frames 是空的）。"""
    session = getattr(rt, "session", None)
    if session is None:
        return None

    def on_tap(x, y):
        f = rt.screen.frame
        if f is None or f.img is None:
            return
        shot = session.trace.save_frame(session.trace.next_seq(), f.img)
        session.note(f"开局点击 ({x}, {y})（帧 {shot}）")

    return on_tap


def new_produce(rt, mode: str, **prep) -> None:
    """新开一局（使用者放行开局后才跑）：培育入口 → H.I.F → 选拔 / 本战（AP）→ 编成第 1–4 步 → プロデュース開始 → 跳过交流。
    本战：开始确认页核对选拔回忆后点「H.I.F本戦へ」；已经在培育里就直接交给主循环。开局阶段每次点击都存一帧（_snap_taps）。"""
    dev = rt.device
    prev = getattr(dev, "on_tap", None)
    if hasattr(dev, "on_tap"):
        dev.on_tap = _snap_taps(rt)
    try:
        _new_produce(rt, mode, **prep)
    finally:
        if hasattr(dev, "on_tap"):
            dev.on_tap = prev


def _new_produce(rt, mode: str, **prep) -> None:
    if mode == "main" and on_start_confirm(rt.screen.shot()):
        # 已经停在本战开始确认页（选拔结束后 / 上一段停在这里）：直接核对选拔回忆再开始，不回首页重走一遍
        logger.info("本战开局：已经在开始确认页，直接核对选拔回忆")
        if prepare(rt, mode, **prep):
            return
        start(rt)
        return
    if enter_produce(rt, new=True) != "new":
        raise ResumeBlocked("没进到剧本选择页")
    select_hif(rt)
    if choose_mode(rt, mode) == "started":
        logger.warning("点了「本戦」后画面已经是培育里的页面（本战已经开始，没经过开始确认页）：交给主循环")
        return
    if prepare(rt, mode, **prep):
        return
    start(rt)
