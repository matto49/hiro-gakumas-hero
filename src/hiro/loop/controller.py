# 来源：digital-hiro hiro/loop/controller.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""HiroController：KAA ProduceController 的子类，每个调度周期把场景喂给 Session（页面级观测 → reduce），
弹窗类中断记为 recover:dialog。决策点的完整记录在策略钩子（hiro.produce.hif）与考试循环（hiro.exec.exam）里。"""

import time

def _cx(rect) -> float:
    return rect.center.x if hasattr(rect, "center") else rect[0] + rect[2] / 2


def _cy(rect) -> float:
    return rect.center.y if hasattr(rect, "center") else rect[1] + rect[3] / 2


from kaa.tasks.produce.new.consts import Scene, SceneType
from kaa.tasks.produce.new.controller import ProduceController
from kotonebot import logging

from hiro.loop.session import Session

logger = logging.getLogger(__name__)
DECK_LIST_CLOSE = (360, 1157)  # 牌组一览「閉じる」（720x1280）

# KAA 场景名 → 状态机页面名
PAGE_NAME = {"UNKNOWN": "unknown", "EXAM": "HIF_EXAM"}
DIALOGS = {
    SceneType.PDRINK_MAX, SceneType.PDRINK_MAX_CONFIRM, SceneType.OUTING_STAMINA_MAX, SceneType.SKILL_CARD_ENHANCE,
    SceneType.SKILL_CARD_REMOVAL, SceneType.SKILL_CARD_CHANGE_1, SceneType.SKILL_CARD_CHANGE_2,
}  # fmt: skip


QUICK_WAIT_TAP_S = 15.0  # 快速判页认出的「不点」页连续这么久没变：点一下 (12,12) 兜底
# 按种类缩短：差し入れ的「Pドリンク獲得」光效之后就是饮料三选一，(12,12) 在弹窗外，点了安全（第二十一局「已等 15 / 17 秒才点」）
QUICK_WAIT_TAP_BY_KIND = {"gift_effect": 1.0}
QUICK_WAIT_S = 0.3  # 快速判页认出读取 / 过场时等多久再看


INTERVAL_TITLE_BAND = (0, 0, 260, 220)  # 左上角标题徽章搜索区（x1, y1, x2, y2；720x1280）
INTERVAL_TITLE_MIN = 0.88


def interval_title_visible(img) -> bool:
    """轮间「インターバル」标题徽章（KAA R.InProduce.HifInterval.Title 同一张模板）在左上角任意位置出现。
    KAA 的 exists() 只在模板原位附近找；回标题再進入后徽章会下移 13px（第二十二局）。"""
    import cv2

    tpl = _interval_title_tpl()
    if img is None or tpl is None:
        return False
    x1, y1, x2, y2 = INTERVAL_TITLE_BAND
    roi = img[y1:y2, x1:x2]
    if roi.shape[0] < tpl.shape[0] or roi.shape[1] < tpl.shape[1]:
        return False
    return float(cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED).max()) >= INTERVAL_TITLE_MIN


_TPL: dict = {}


def _interval_title_tpl():
    if "interval" not in _TPL:
        from pathlib import Path

        import cv2
        import kaa.tasks.R as kaa_r

        p = Path(kaa_r.__file__).parents[1] / "sprites/f762b40a-df6a-4229-9dfb-e76e24142b37_template.png"
        img = cv2.imread(str(p))
        _TPL["interval"] = img[:, :, :3] if img is not None else None
    return _TPL["interval"]


LIVE_START_TEXT = "TAPTOSTART"
LIVE_INFO_TEXTS = ("作詞", "作曲", "編曲")


def live_start_tap(img, boxes) -> tuple[int, int] | None:
    """横屏 LIVE 开始前的曲目页：认到「TAP TO START」就点它；只认到曲目信息（作詞 / 作曲 / 編曲）就点屏幕下方中间。其余 None。
    boxes：[(text, conf, (x1, y1, x2, y2))]，坐标同截图（横屏 1280x720）。"""
    for t, _, b in boxes:
        if LIVE_START_TEXT in t.replace(" ", "").upper():
            return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
    if any(k in t for t, _, _ in boxes for k in LIVE_INFO_TEXTS):
        h, w = img.shape[:2]
        return w // 2, int(h * 0.85)
    return None


class HiroController(ProduceController):
    session: Session | None = None
    _landscape = None  # LandscapeGuard，首次 _update 时创建  # 由 run_hif 注入（KAA 在 produce.py 里用类名实例化，拿不到实例参数）

    def _rearm_exam(self, scene) -> None:
        """再挑戦后画面从结果页直接回到同一场考试，KAA 记的上一页还是 EXAM，_dispatch 只在「上一页不是 EXAM」时才进
        on_exam_entered → 主循环一直判页什么都不做（第十八局本战 R2 两次再挑戦后各卡 18 / 7 分钟）。
        策略点了再挑戦会置 exam_restarted；再看到考试页时清掉上一页，让它重新进入考试处理。"""
        if scene.type is SceneType.EXAM and getattr(self.strategy, "exam_restarted", False):
            self.strategy.exam_restarted = False
            self._last_scene = None
            logger.info("再挑戦后的考试：重新进入考试处理")

    def _update(self):
        if HiroController._landscape is None:
            from hiro.device import is_playcover
            from hiro.loop.landscape import LandscapeGuard

            HiroController._landscape = LandscapeGuard(active=is_playcover())
        self._texts = self._boxes = None  # 本轮整帧 OCR 结果（UNKNOWN 时按需识别一次，各补判共用）
        from kotonebot import device

        img = device.screenshot()
        if self._landscape.tick(img):
            return  # 横屏 LIVE 黑屏：刚做了全屏切换，下一轮再判
        if img is not None and img.shape[1] > img.shape[0]:
            # 横屏 LIVE 演出中（第三十一局：夺冠后 LIVE 约 2 分 45 秒）：画面本来就认不出，不跑快速判页 / KAA 判页
            # （KAA 拿竖屏 rect 去裁 1280x720，每 2s 报一次「rect … exceeds image bounds」，共 76 条），
            # 记一次进展（空转看门狗以前在 LIVE 结束 2s 后按「181s 没进展」停机），等演出结束
            self._unknown_since = None
            if HiroController.session is not None:
                HiroController.session.guard.touch()
            self._live_tick(img)
            time.sleep(1.0)
            return
        quick = self._quick_page()
        if quick == "handled":
            return  # 点一下就过的页面（获得演出 / 结果气泡 / TAP 提示 / 礼物箱）：已点，下一轮再判
        scene = Scene(SceneType.SELECT_PITEM) if quick == "SELECT_PITEM" else (self.page.check_scene() or Scene(SceneType.UNKNOWN))
        from hiro.loop import kaa_patches

        if kaa_patches.BLIND_CHOICE["pending"]:
            kaa_patches.BLIND_CHOICE["pending"] = False
            # 用户规则 3f：没接管的选择页停下报警，不交给 KAA 盲选。KAA 的剧情按钮识别在过场动画上会误报（q9 公開レッスン）：
            # 盲点已被拦下（不会点），只有 8 秒内连续 3 次都判到才认为真是选择页，停下
            import time as _t

            now = _t.monotonic()
            hits = [t for t in getattr(self, "_blind_hits", []) if now - t < 8.0] + [now]
            self._blind_hits = hits
            if len(hits) >= 3:
                self.strategy._stop("INITIAL_CHOICE", "KAA 连续判到剧情 / 初始二选一（多个剧情按钮），我们还没有这页的决策")
            logger.info("KAA 判到多个剧情按钮（第 %d 次 / 8 秒内），已拦下不点，下一轮再看", len(hits))
        if scene.type is SceneType.UNKNOWN and interval_title_visible(device.screenshot()):
            # 第二十二局：通信エラー 回标题后「再開」进轮间，「インターバル」标题徽章比 KAA 模板位置低 13px，KAA 判不出 → 197s 停机
            scene = Scene(SceneType.HIF_ROUND_INTERVAL)
        if scene.type in (SceneType.UNKNOWN, SceneType.IDLE) and self._is_hif_exam():
            # KAA 只认普通考试的排名文字（TextExamRank*），H.I.F 选拔 / 本战的考试画面它判不出来
            scene = Scene(SceneType.EXAM)
        elif scene.type is SceneType.UNKNOWN and (fb := self._fallback_page()) is not None:
            # 像素兜底（hiro.vision.quick.fallback_page）：选行动页带提示气泡 / 淡入、饮料溢出列表，KAA 判 UNKNOWN（第二十一局选拔）
            scene = Scene(SceneType[fb])
            if HiroController.session is not None:
                HiroController.session.recover("page_misjudged", f"KAA 判不出，像素兜底补判为 {fb}")
            logger.info("UNKNOWN 补判为 %s（像素兜底）", fb)
        elif scene.type is SceneType.UNKNOWN and self._is_date_changed():
            from hiro.loop.date_change import DateChanged

            raise DateChanged("日付変更")  # 凌晨 4 点日切换：交给 run_hif 回首页续跑
        elif scene.type is SceneType.UNKNOWN and self._clear_hif_drink_dialog(self._ocr_boxes()):
            return  # 考试中残留的 H.I.F 饮料弹窗（例如崩溃续跑时停在弹窗上）：已点掉，下一轮重新判断
        elif scene.type is SceneType.UNKNOWN and self._skip_settings_dialog():
            return  # 设置被重置后开局弹的「演出スキップ設定」：四项打开 → 決定（盖在開始確認页上，要先于开始确认判据）
        elif scene.type is SceneType.UNKNOWN and self._is_start_confirm():
            # 培育 / 本战开始确认页：开局要先请示用户（选拔与本战是两种独立的局，用户 2026-10-31）。到这里就结束本次运行，绝不点开始
            logger.warning("到了培育开始确认页（%s），停止运行，等待用户决定是否开局", " / ".join(self._ocr_texts()[:4]))
            if HiroController.session is not None:
                HiroController.session.note("到达开始确认页，按规则停止，未开局")
            self.abort()
            return
        elif scene.type is SceneType.UNKNOWN and self._is_produce_finished():
            # H.I.F「プロデュース完了」KAA 判不出 PRODUCE_END：点完结算回首页并结束本局
            from hiro.loop.resume import finish_produce_end

            logger.info("UNKNOWN 补判为培育结束（プロデュース完了）")
            if HiroController.session is not None:
                HiroController.session.observe(scene="PRODUCE_END", area="produce", img=None, fields=False, save_frame=True)
                HiroController.session.record()
            finish_produce_end()
            self.abort()
            return
        elif scene.type is SceneType.UNKNOWN and self._is_drink_offer():
            # 差し入れ等的饮料三选一（带「受け取らない」勾选框、饮料满时「Pドリンク所持上限」）：KAA 的 TextPDrink 模板认不出
            scene = Scene(SceneType.SELECT_DRINK)
            if HiroController.session is not None:
                HiroController.session.recover("page_misjudged", "KAA 判不出饮料选择页（差し入れ），OCR 补判为 SELECT_DRINK")
            logger.info("UNKNOWN 补判为 SELECT_DRINK")
        elif scene.type is SceneType.UNKNOWN and self._retry_network_error():
            return  # 通信エラー：点「リトライ」，下一轮重判
        elif scene.type is SceneType.UNKNOWN and self._skip_commu():
            return  # 培育剧情在逐句播放（重新登录后跳过设置被重置，KAA 只处理「未読」弹窗）：点 SKIP
        elif scene.type is SceneType.UNKNOWN and self._first_run_settings():
            return  # 重新登录后游戏重置本地设置，开局弹的一次性设置框：按推荐项点「決定」
        elif scene.type is SceneType.UNKNOWN and self._cancel_recover_dialog():
            return  # 轮间「体力回復確認」残留（KAA 认不出轮间）：点キャンセル，下一轮由轮间钩子重新决策
        elif scene.type is SceneType.UNKNOWN and self._resume_special_guidance():
            return  # 续跑落在特別指導页：由策略接着做定制，再离开
        elif scene.type is SceneType.UNKNOWN and self._record_finals_ranking():
            return  # 本战排名 / 轮次结果页：记下分数（存帧）并点「次へ」，下一轮重判
        elif scene.type in (SceneType.UNKNOWN, SceneType.SELECT_PITEM) and self._close_produce_log(self._ocr_texts()):
            return  # 行动记录覆盖层（底栏文档图标）：KAA 会因「Pアイテム…を獲得」判成 SELECT_PITEM；点 ✕ 关掉，下一轮重判
        elif scene.type is SceneType.UNKNOWN and self._is_pitem_select():
            # H.I.F 考试后的 P 道具三选一（背包），KAA 的 TextPItem 模板认不出
            scene = Scene(SceneType.SELECT_PITEM)
            if HiroController.session is not None:
                HiroController.session.recover("page_misjudged", "KAA 判不出 H.I.F P 道具选择页，OCR 补判为 SELECT_PITEM")
            logger.info("UNKNOWN 补判为 SELECT_PITEM")
        elif scene.type is SceneType.IDLE:
            scene = self._reclassify_idle(scene)
        if self._watch_unknown(scene):
            return  # 认不出的画面交给 LLM 看图：已执行一个受限动作（点框 / 点空白 / 等待），下一轮重判
        s = HiroController.session
        if s is not None:
            name = PAGE_NAME.get(scene.type.name, scene.type.name)
            before = s.state.page.name
            # P 道具页等靠补判认出的页面存一帧，供操作层标定点击位置
            keep = scene.type is SceneType.SELECT_PITEM or (s.debug_recog and name != before)  # 调试模式：页面每次变化都存帧（§7 3b）
            s.observe(scene=name, area="produce", img=None, fields=False, save_frame=keep)
            if s.state.page.name != before:
                s.record()  # 页面变化才落一条（无决策）
            if scene.type in DIALOGS:
                s.recover("dialog", f"KAA 中断弹窗 {scene.type.name}")
        if scene.type in (SceneType.UNKNOWN, SceneType.IDLE):
            self._count_taps(1)  # KAA 的 _dispatch 在 UNKNOWN / IDLE 上点一次 skip() (12,12)
        self._rearm_exam(scene)
        self._dispatch(scene, self._last_scene)
        self._last_scene = scene
        if scene.type in (SceneType.UNKNOWN, SceneType.IDLE):
            self._advance()

    LIVE_OCR_EVERY = 2.0  # 横屏帧多久 OCR 一次找「TAP TO START」
    LIVE_STILL_WARN = 60.0  # 横屏下同一画面这么久不变：报一次警

    def _live_tick(self, img) -> None:
        """横屏 LIVE 期间：演出开始前的曲目页（第三十二局：「Campus mode!! / 篠澤広 / 作詞… / TAP TO START」，1280x720）
        要点一下才开始，认到就点；其余横屏帧不点。同一画面超过 LIVE_STILL_WARN 秒不变报一次警（看门狗把横屏算进展，不会停机）。"""
        import numpy as np

        st = getattr(self, "_live", None)
        if st is None:
            st = self._live = {"ocr_at": -1e9, "thumb": None, "since": time.monotonic(), "warned": False}
        now = time.monotonic()
        try:
            import cv2

            th = cv2.cvtColor(cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype("float32")
            if st["thumb"] is None or float(np.abs(th - st["thumb"]).mean()) >= 6.0:
                st["thumb"], st["since"], st["warned"] = th, now, False
            elif not st["warned"] and now - st["since"] >= self.LIVE_STILL_WARN:
                st["warned"] = True
                logger.warning("横屏画面 %.0fs 没变（LIVE 卡住？），看一下", now - st["since"])
                if HiroController.session is not None:
                    HiroController.session.note(f"横屏画面 {now - st['since']:.0f}s 没变")
        except Exception as e:  # noqa: BLE001
            logger.debug("横屏画面比对出错：%r", e)
        if now - st["ocr_at"] < self.LIVE_OCR_EVERY:
            return
        st["ocr_at"] = now
        from kotonebot import device

        from hiro.produce.ops import ocr_boxes

        try:
            tap = live_start_tap(img, ocr_boxes(img))
        except Exception as e:  # noqa: BLE001  OCR 出错不能打断等待
            logger.debug("横屏 OCR 出错：%r", e)
            return
        if tap is not None:
            logger.info("横屏 LIVE 开始前的曲目页：点 %s 开始演出", tap)
            device.click(*tap)
            self._count_taps(1)

    @staticmethod
    def _count_taps(n: int) -> None:
        s = HiroController.session
        if s is not None and n:
            s.advance_taps = getattr(s, "advance_taps", 0) + n

    def _advance(self) -> int:
        """过场推进（hiro.loop.advance）：认不出的帧上，安全时每 0.4s 再点几下空白处。"""
        from kotonebot import device

        from hiro.loop import advance
        from hiro.vision.pages import dialog_header
        from hiro.vision.quick import quick_page

        texts = self._ocr_texts()  # 本轮共用 OCR（UNKNOWN 已做过；IDLE 轮次这里才做）：判按钮字样

        def quick(img):
            try:
                return quick_page(img)
            except Exception:
                return "error"  # 判页出错：不点

        since = getattr(self, "_unknown_since", None)
        alt = False
        if since is not None and time.monotonic() - since >= advance.ALT_AFTER:
            boxes = [(b.text, 1.0, (int(b.rect.x1), int(b.rect.y1), int(b.rect.x2), int(b.rect.y2))) for b in self._ocr_boxes()]
            alt = advance.alt_clear(boxes)
        try:
            n = advance.burst(texts, shoot=device.screenshot, click=lambda x, y: device.click(x, y), in_live=self._landscape.in_live,
                              quick=quick, header=dialog_header, alt=alt)  # fmt: skip
        except Exception as e:  # 推进出错不能打断主循环
            logger.warning("过场推进出错：%r", e)
            return 0
        self._count_taps(n)
        return n

    @staticmethod
    def _fallback_page() -> str | None:
        from kotonebot import device

        from hiro.vision.quick import fallback_page

        try:
            return fallback_page(device.screenshot())
        except Exception as e:  # 兜底判页出错不能打断主循环
            logger.warning("像素兜底判页出错：%r", e)
            return None

    def _quick_page(self) -> str | None:  # noqa: C901
        """快速判页（hiro.vision.quick，≤ 0.3 秒，只用模板 / 像素 / 局部 OCR），在 KAA check_scene（这类页面上 1–2.5 秒、返回 None）
        和整帧 OCR 补判链之前：点一下就过的页面直接点并记观测（事件照常产生），返回 "handled"；P 道具三选一 / 定制后三选一返回
        "SELECT_PITEM"（原来 KAA 判 IDLE / None、要等 30 秒后整帧 OCR 补判）；认不出返回 None，照旧。
        协调 2026-11-02：每局 4–7 次「页面认不出超过 30 秒」，都是这几页。"""
        from kotonebot import device

        from hiro.vision.quick import quick_page

        img = device.screenshot()
        try:
            from hiro.vision.deck import is_deck_list

            if is_deck_list(img):
                # 牌组一览挂着（第二十局：开局读整副时一览晚于 6 秒才弹出，读牌放弃后留在画面上，KAA 把「Pアイテム」标签判成 P 道具三选一 → 停机）
                device.click(*DECK_LIST_CLOSE)
                logger.info("牌组一览挂着：点「閉じる」")
                return "handled"
        except Exception as e:  # noqa: BLE001
            logger.warning("牌组一览判定出错：%r", e)
        try:
            q = quick_page(img)
        except Exception as e:  # 快速判页出错不能打断主循环
            logger.warning("快速判页出错：%r", e)
            return None
        if q is None:
            return None
        if q["dispatch"] is not None:
            return q["dispatch"]
        self._unknown_since = None
        s = HiroController.session
        if s is not None:
            before = s.state.page.name
            s.observe(scene=q["page"], area="produce", img=img, fields=dict(q["fields"]), save_frame=bool(s.debug_recog) or q["page"] != before)
            if s.state.page.name != before:
                s.record()
        if q["tap"] is None:
            key = (q["page"], q["fields"].get("kind"))
            if getattr(self, "_quick_wait", None) is None or self._quick_wait[0] != key:
                self._quick_wait = (key, time.monotonic())
            elif time.monotonic() - self._quick_wait[1] >= QUICK_WAIT_TAP_BY_KIND.get(key[1], QUICK_WAIT_TAP_S):
                # 不点的页（读取 / 闪屏 / 考前说明页演出）停太久：点一下 (12,12)（KAA skip() 同位置），防止一直等不到定格
                logger.info("快速判页 %s 已等 %.0f 秒，点 (12,12)", key, time.monotonic() - self._quick_wait[1])
                self._quick_wait = (key, time.monotonic())
                device.click(12, 12)
                return "handled"
            time.sleep(QUICK_WAIT_S)  # 读取 / 过场：不点，等一下再看
            return "handled"
        self._quick_wait = None
        device.click(*q["tap"])
        self._count_taps(1)
        logger.info("快速判页 %s（%.0f ms）%s，点 %s", q["page"], q["ms"], q["fields"].get("text") or q["fields"].get("name") or "", q["tap"])
        return "handled"

    @staticmethod
    def _is_hif_exam() -> bool:
        from kotonebot import device

        from hiro.vision.hif import is_hif_exam

        return is_hif_exam(device.screenshot())

    @staticmethod
    def _in_exam_texts(texts: list[str]) -> bool:
        """考试页的标志：左上「残りターン」或 HUD「ターン」（与 is_hif_exam 同源的文字判据，粗判）。"""
        return any("残りターン" in t or "ターン目" in t for t in texts)

    @staticmethod
    def _clear_hif_drink_dialog(boxes=None) -> bool:
        """boxes：本轮共用的整帧 OCR（主循环传入，不再自己 OCR；以前每个 UNKNOWN 轮次这里多做一次整帧 OCR，饮料弹窗兜底再做一次）。"""
        from hiro.exec.exam import _hif_drink_dialogs

        # 考试外打开的饮料详情（「レッスン・試験・オーディション以外では使用できません」）：只点キャンセル
        from kotonebot import device, ocr

        shared = boxes is not None
        boxes = boxes if shared else ocr.ocr()
        if any("以外では使用できません" in b.text for b in boxes) or (
            any("ドリンク詳細" in b.text for b in boxes) and not HiroController._in_exam_texts([b.text for b in boxes])
        ):
            btn = next((b for b in boxes if "キャンセル" in b.text), None)
            if btn is not None:
                device.click(btn.rect)
                logger.warning("考试外的饮料详情：点「キャンセル」")
                if HiroController.session is not None:
                    HiroController.session.recover("dialog", "考试外误开饮料详情，点キャンセル")
                return True

        if _hif_drink_dialogs(boxes=boxes if shared else None):
            if HiroController.session is not None:
                HiroController.session.recover("dialog", "主循环清掉 H.I.F 饮料弹窗")
            return True
        return False

    UNKNOWN_REPORT_EVERY = 30.0
    UNKNOWN_STOP_AFTER = 180.0  # 用户规则 3f：没接管的页面不能一直空转，认不出超过 3 分钟就停下（存帧、报警）

    def _watch_unknown(self, scene: Scene) -> bool:
        """画面持续认不出（UNKNOWN / IDLE）时每 30s 打一条 WARNING（带画面文字）并存一帧，避免静默空转（实机曾空转 1.5 小时）。
        认不出超过 rescue.RESCUE_AFTER 秒（有带标题条的弹窗时 RESCUE_AFTER_DIALOG 秒）交给 LLM 看图（hiro.loop.rescue），
        执行了动作返回 True（本轮不再 dispatch / 推进）。"""
        import time

        if scene.type not in (SceneType.UNKNOWN, SceneType.IDLE):
            self._unknown_since = None
            if HiroController._rescuer is not None:
                HiroController._rescuer.progressed()
            return False
        from kotonebot import device

        shot = device.screenshot()
        self._check_game_alive(shot)
        if self._landscape is not None and self._landscape.in_live(shot):
            self._unknown_since = None  # LIVE 横屏演出：认不出是正常的，不计时（q8 LIVE 会被 3 分钟停下）
            return False
        now = time.monotonic()
        if self._in_comm_error_episode(now):
            # 刚点过「リトライ」：两次弹窗之间是 CONNECTING / 原页面，认不出是正常的，不计时、不问 LLM。
            # 游戏连不上会自己弹「時間が経っているのでタイトルに戻します」，由 comm_error 点閉じる回首页续跑（Windows 第二十二局：
            # 断网 8 分钟，リトライ 二十多次之间的 UNKNOWN 累计 220s 停机，回标题框是停机后才弹的）
            self._unknown_since = self._unknown_reported = now
            return False
        since = getattr(self, "_unknown_since", None)
        if since is None:
            self._unknown_since = self._unknown_reported = now
            return False
        if self._llm_rescue(scene, now - since):
            return True
        if now - self._unknown_reported < self.UNKNOWN_REPORT_EVERY:
            return False
        self._unknown_reported = now
        texts = [t for t in self._ocr_texts() if t.strip()][:12]
        logger.warning("画面持续未识别 %.0fs（%s），画面文字：%s", now - since, scene.type.name, " / ".join(texts))
        s = HiroController.session
        if s is not None:
            s.observe(scene="unknown", area="unknown", img=None, fields=False, save_frame=True)
            s.note(f"未识别画面持续 {now - since:.0f}s：{' / '.join(texts)}")
        if now - since >= self.UNKNOWN_STOP_AFTER:
            self.strategy._stop("unknown", f"画面持续 {now - since:.0f}s 认不出（{' / '.join(texts)}）")
        return False

    @staticmethod
    def _check_game_alive(img) -> None:
        """横屏又认不出：可能是 LIVE，也可能是学マス被杀后的安卓桌面（GKMS 第五局）。安卓后端查一下前台应用，
        不是学マス就抛 GameNotForeground，交给 run_hif 的续跑看守（HIRO_RELAUNCH_GAME=1 时重新拉起，否则讲清原因停下）。"""
        from hiro.loop import game_app

        if game_app.is_landscape(img) and game_app.alive() is False:
            raise game_app.GameNotForeground("学マス不在前台（截到横屏桌面）")

    _rescuer = None  # hiro.loop.rescue.Rescuer，首次用到时创建（整局共用：同屏计数、10 分钟次数上限、本局统计）

    def _llm_rescue(self, scene: Scene, stuck_s: float) -> bool:
        """认不出的画面问 LLM（用户 2026-11-03）。到时间才问；考试画面不问（考试有自己的执行路径，手牌区绝不交给 LLM）。
        LLM 给 stop、被安全规则拦下、调用失败、同屏没进展 → 报警并放手（这个画面不再问），仍由原来的 180 秒停机兜底。"""
        import time

        from kotonebot import device

        from hiro.loop import rescue

        if not rescue.LLM_UNKNOWN:
            return False
        r = HiroController._rescuer
        if r is None:
            r = HiroController._rescuer = rescue.Rescuer()
        try:
            img = device.screenshot()
            if img is None:
                return False
            if r.pending:
                rec = r.poll()  # LLM 在后台想（25–40 秒），主循环照常判页 / 推进；回复到了在这里复核画面并执行
                if rec is None:
                    return False
            else:
                try:
                    from hiro.vision.pages import dialog_header

                    dialog = dialog_header(img) is not None
                except Exception:  # noqa: BLE001
                    dialog = False
                if not r.due(stuck_s, dialog=dialog, img=img):
                    return False
                if self._in_exam_texts(self._ocr_texts()) or self._is_hif_exam():
                    return False
                boxes = [(b.text, 1.0, (int(b.rect.x1), int(b.rect.y1), int(b.rect.x2), int(b.rect.y2))) for b in self._ocr_boxes()]
                s = HiroController.session
                ctx = {"kaa_scene": scene.type.name, "stuck_s": round(stuck_s), "dialog_header": dialog}
                if s is not None:
                    ctx.update(mode=s.state.mode, page=s.state.page.name, recent=list(s.notes[-6:]))
                rec = r.begin(img, ctx, boxes)
                if rec is None:
                    logger.info("画面认不出 %.0fs：截图交给 LLM 看（后台）", stuck_s)
                    return False
        except Exception as e:  # noqa: BLE001  兜底本身出错：退回原来的 30 秒报告 / 180 秒停机
            logger.warning("LLM 兜底出错：%r", e)
            return False
        self._record_rescue(rec, img)
        a = rec["action"]
        if a["action"] == "stop":
            # 报警并放手：同一画面不再问，仍由 30 秒报告 / 180 秒停机兜底（认不出的选择页以前等一会儿会自己恢复，不能因 LLM 说停就提前停局）
            why = rec.get("blocked") or rec.get("reason") or "LLM 建议停机"
            texts = [b[0] for b in rec.get("boxes") or []][:12]
            logger.warning("画面认不出，LLM 兜底放弃（%s）：%s；画面文字：%s。交回 180 秒停机", rec.get("page") or "?", why, " / ".join(texts))
            return False
        if rec.get("changed"):
            self._unknown_since = self._unknown_reported = time.monotonic()  # 画面有变化：重新计时
        return a["action"] != "none"

    @staticmethod
    def _record_rescue(rec: dict, img) -> None:
        """兜底写进 trace：recover:llm_rescue + 一个 decider=llm_rescue 的步骤（存帧、带 LLM 调用记录）。"""
        s = HiroController.session
        if s is None:
            return
        try:
            from hiro.contracts import Action, ActionKind, Decision, ExecutionResult, llm_trace_from_call

            a = rec["action"]
            detail = (f"{rec.get('page') or '?'} → {a['action']}{'「' + a['text'] + '」' if a.get('text') else ''}"
                      f"（{rec.get('blocked') or rec.get('reason') or ''}）{'；' + rec['skipped'] if rec.get('skipped') else ''}")  # fmt: skip
            s.recover("llm_rescue", detail[:300])
            obs = s.observe(scene="unknown", area="unknown", img=img, fields=False, save_frame=True)
            action = Action.ui(ActionKind.RECOVER, strategy="llm_rescue", rescue=a["action"], box=a.get("box"), text=a.get("text"),
                               seconds=a.get("seconds"), page=rec.get("page"), blocked=rec.get("blocked"), dir=rec.get("dir"))  # fmt: skip
            calls = []
            if rec.get("model"):
                adapter, _, model = rec["model"].partition(":")
                calls = [{"seconds": rec.get("seconds"), "error": rec.get("error"), "reply": rec.get("reply") or rec.get("raw") or "",
                          "adapter": adapter, "model": model or None, "prompt_version": rec.get("prompt")}]  # fmt: skip
            d = Decision(action=action, reason=(rec.get("blocked") or rec.get("reason") or "")[:300], source="llm",
                         llm=[llm_trace_from_call(c) for c in calls], seconds=rec.get("seconds"), decider="llm_rescue")  # fmt: skip
            taps = [list(a["xy"])] if a.get("xy") and not rec.get("skipped") and a["action"] != "stop" else []
            ex = ExecutionResult(action=action, ok=bool(rec.get("changed")), taps=taps, page_before="unknown",
                                 error=rec.get("blocked") or rec.get("skipped"))  # fmt: skip
            s.record(obs, decision=d, execution=ex)
        except Exception:  # noqa: BLE001  留档失败不能打断主循环
            logger.warning("LLM 兜底写 trace 失败", exc_info=True)

    def _ocr_boxes(self) -> list:
        """本轮整帧 OCR（Vision，1.5 倍，约 0.5s；kotonebot 整帧约 2–5s），各补判共用一次。框是整帧坐标（.text / .rect）。
        UNKNOWN 画面每轮以前要做 2–3 次 kotonebot 整帧 OCR，第十五到十七局未识别画面每轮中位 6.7s，KAA 的 skip() 也就 6.7s 才点一下。"""
        if getattr(self, "_boxes", None) is None:
            from kotonebot import device

            from hiro.exec.exam import _roi_texts

            img = device.screenshot()
            boxes = _roi_texts(img, 0, img.shape[0]) if img is not None else []
            self._boxes = sorted(boxes, key=lambda b: (int(b.rect.center.y) // 12, int(b.rect.center.x)))  # 自上而下（同 kotonebot 顺序）
        return self._boxes

    def _ocr_texts(self) -> list[str]:
        if getattr(self, "_texts", None) is None:
            self._texts = [t.text for t in self._ocr_boxes()]
        return self._texts

    def _is_date_changed(self) -> bool:
        from hiro.loop.date_change import FORCED_TITLES

        from hiro.vision.textnorm import contains_any

        texts = [t.replace(" ", "") for t in self._ocr_texts()]
        # 标题和「タイトルへ」按钮都要在（「データ更新」这类字样别处也可能出现）
        return any(k in t for t in texts for k in FORCED_TITLES) and any(contains_any(t, ("タイトルへ",)) for t in texts)

    # 盖在開始確認页上的设置框（第二十三局：データ連携重新登录后，开局点了开始先弹「早送り設定」再弹「演出スキップ設定」，
    # 背景的「2.開始確認」被 OCR 读到 → 判成开始确认页停机，实际已开局、AP 已扣）
    START_CONFIRM_OVERLAYS = ("演出スキップ設定", "早送り設定", "サポートイベント設定", "スキップ確認", "ボイス再生確認")
    NEW_GAME_GRACE_S = 120.0  # new 开局（KAA 已点开始）后这么久内看到開始確認页不停：开局过场 / 设置框
    new_game_at: float | None = None  # run_hif new 在 do_produce 前写入 time.monotonic()

    def _is_start_confirm(self) -> bool:
        texts = self._ocr_texts()
        if not any("開始確認" in t or "本戦へ" in t for t in texts):
            return False
        if any(o in t for t in texts for o in self.START_CONFIRM_OVERLAYS) or any(t.strip() == "決定" for t in texts):
            logger.info("開始確認页上盖着设置框（%s），不按开始确认页停", " / ".join(texts[:4]))
            return False
        at = HiroController.new_game_at
        if at is not None and time.monotonic() - at < self.NEW_GAME_GRACE_S:
            logger.info("刚开局 %.0fs（KAA 已点开始），開始確認页还在：等过场，不停", time.monotonic() - at)
            return False
        return True

    def _skip_settings_dialog(self) -> bool:
        """开局一次性设置框（演出スキップ設定 / コミュ早送り設定 / ボイス再生確認）：按用户设置选好再点「決定」（hiro.loop.skip_settings）。"""
        from hiro.loop import skip_settings

        if not skip_settings.is_skip_settings(self._ocr_texts()):
            return False
        from kotonebot import device

        from hiro.produce.ops import ocr_boxes

        boxes = [(b.text, 1.0, (int(b.rect.x1), int(b.rect.y1), int(b.rect.x2), int(b.rect.y2))) for b in self._ocr_boxes()]
        r = skip_settings.handle(boxes, shoot=device.screenshot, ocr=ocr_boxes, click=lambda x, y: device.click(x, y))
        if r is None:
            return False
        self._unknown_since = None
        if HiroController.session is not None:
            HiroController.session.recover("dialog", f"{r['kind']}：选项={r['option']}，打开 {r['clicked']} 个开关，決定={r['ok']}，{r['items']}")
        return True

    def _is_produce_finished(self) -> bool:
        # 结算页标题是两行「プロデュース / 完了」，同页有「ファン数」「獲得アイテム」；
        # 不能只看「プロデュース」+「完了」子串：定制说明里有「プロデュース中2回」（实机 15:04 误判、在培育中乱点 3 分钟）
        texts = [t.strip() for t in self._ocr_texts()]
        if any("メモリーにするフォト" in t or "選抜試験突破者" in t or t == "完了する" for t in texts):  # 选拔赛 / 本战通关后的回忆照片选择 → 结算流程（生成回忆、次へ）
            return True
        return "完了" in texts and any("ファン数" in t for t in texts) and any(t.startswith("プロデュース") for t in texts)

    def _is_drink_offer(self) -> bool:
        texts = self._ocr_texts()
        return any("受け取らない" in t for t in texts) and any("ドリンク" in t or "体力" in t or "効果" in t for t in texts) \
            and not any("スキルカード" in t and "選択" in t for t in texts)

    SG_HANDOFF_MAX = 3
    NET_RETRY_MAX = 5

    def _retry_network_error(self) -> bool:
        """「通信エラー / 通信中にエラーが発生しました」弹窗：点「リトライ」（绝不点「タイトルへ」）。连续 5 次还在就交给未识别停机。"""
        texts = self._ocr_texts()
        if not any("通信" in t and ("エラー" in t or "工ラー" in t) for t in texts):
            self._net_retries = 0
            return False
        self._net_retries = getattr(self, "_net_retries", 0) + 1
        if self._net_retries > self.NET_RETRY_MAX:
            return False
        from kotonebot import device

        from hiro.loop import comm_error

        boxes = [(b.text, 1.0, (int(b.rect.x1), int(b.rect.y1), int(b.rect.x2), int(b.rect.y2))) for b in self._ocr_boxes()]
        if HiroController.session is not None:
            HiroController.session.recover("dialog", f"通信エラー（第 {self._net_retries} 次）")
        # retry：点リトライ（绝不点タイトルへ）；只有「閉じる」（時間が経っているのでタイトルに戻します）：点閉じる并抛 CommErrorToTitle，
        # run_hif 按日期切换同一条路回首页续跑（第二十二局：这种框以前认不出，195s 后停机）
        done = comm_error.handle(boxes, lambda x, y: device.click(x, y))
        if done == "retry":
            self._note_comm_retry(time.monotonic())
        return done == "retry"

    COMM_QUIET_S = 90.0  # 上次点リトライ后这么久没再弹，算这一轮通信故障结束
    COMM_PATIENCE_S = 900.0  # 一轮通信故障最多不计时这么久（游戏通常几分钟后自己弹回标题框）；超过照常 180 秒停机

    def _note_comm_retry(self, now: float) -> None:
        first, last = getattr(self, "_comm_first", None), getattr(self, "_comm_last", None)
        if first is None or last is None or now - last > self.COMM_QUIET_S:
            self._comm_first = now
        self._comm_last = now

    def _in_comm_error_episode(self, now: float) -> bool:
        first, last = getattr(self, "_comm_first", None), getattr(self, "_comm_last", None)
        return first is not None and last is not None and now - last <= self.COMM_QUIET_S and now - first <= self.COMM_PATIENCE_S

    def _skip_commu(self) -> bool:  # noqa: D401
        """q13-r1：开场剧情一句句播放，UNKNOWN 138s 差点停机，总控手动点了 SKIP。剧情页 = 左上「プロデュースコミュ」+ 底栏「SKIP」。"""
        texts = self._ocr_texts()
        # 剧情底栏：SKIP / ログ / AUTO / 早送り。OCR 常把 SKIP 读成「NskIp」「MSkIP」，「AUTO」读成「AuT0」，所以按「早送り」+ 任一其他键判
        if not any("早送" in t for t in texts) or not any("ログ" in t or "AUT" in t.upper() or "SKI" in t.upper() for t in texts):
            return False
        from kotonebot import device, ocr

        # 只点真的认出来的 SKIP 框，且在剧情底栏位置（左下）。q14：判据误中非剧情页，按固定位置 (220,1200) 点到饮料栏第 3 格，
        # 打开「Pドリンク詳細」后主循环在考试外连点「使う」67 次
        # 点之前重新截图核对（q15：用的是旧帧，剧情刚结束切回培育页，SKIP 的位置正好是饮料栏 → 又开了饮料详情）
        device.screenshot()
        fresh = ocr.ocr()
        if not any("早送" in b.text for b in fresh):
            return False
        btn = next((b for b in fresh if "SKI" in b.text.upper() and _cy(b.rect) > 1120 and _cx(b.rect) < 360), None)
        if btn is None:
            return False
        device.click(btn.rect)
        logger.info("培育剧情：点 SKIP")
        self._unknown_since = None  # 有进展：剧情 / 确认框之后的动画重新计时（q14：确认框 163s + 动画 34s 累计到 197s 停机）
        return True

    # 一次性设置框（标题, 要选的项, 确认按钮）。q13（2026-11-02 重新登录后）：「サポートイベント設定」卡 188s 停机
    FIRST_RUN_SETTINGS = (("サポートイベント設定", "強化済みで獲得する", "決定"),
                          ("スキップ確認", None, "決定"))  # q14：剧情点 SKIP 后「未読のコミュです スキップしますか?」，133s 未识别

    def _first_run_settings(self) -> bool:
        texts = self._ocr_texts()
        hit = next((x for x in self.FIRST_RUN_SETTINGS if any(x[0] in t for t in texts)), None)
        if hit is None:
            return False
        from kotonebot import device, ocr

        boxes = ocr.ocr()
        opt = next((b for b in boxes if hit[1] in b.text), None) if hit[1] else None
        if opt is not None:
            device.click(opt.rect)
            time.sleep(0.5)
        from hiro.loop.skip_settings import is_ok_text

        btn = next((b for b in boxes if b.text.strip() == hit[2] or (hit[2] == "決定" and is_ok_text(b.text))), None)
        if btn is None:
            return False
        device.click(btn.rect)
        self._unknown_since = None
        logger.warning("一次性设置「%s」：选「%s」→「%s」", hit[0], hit[1], hit[2])
        if HiroController.session is not None:
            HiroController.session.recover("dialog", f"一次性设置「{hit[0]}」选「{hit[1]}」")
        return True

    def _cancel_recover_dialog(self) -> bool:
        """main12 R2 前：轮间回体力的确认框没关掉，KAA 判 UNKNOWN，空等 3 分钟停机。点「キャンセル」交回轮间钩子（最多 3 次）。"""
        texts = self._ocr_texts()
        if any("リフレッシュ" in t and "確認" in t for t in texts):
            # 刷新确认框残留（第十八局本战轮间：30s UNKNOWN，总控手动点了キャンセル）：点キャンセル，交回轮间 / 相談钩子
            from kotonebot import device

            btn = next((t for t in self._ocr_boxes() if "キャンセル" in t.text), None)
            if btn is not None:
                device.click(btn.rect)
            else:
                device.click(220, 1160)
            logger.warning("残留刷新确认框（リフレッシュ確認）：点「キャンセル」交回钩子")
            if HiroController.session is not None:
                HiroController.session.recover("dialog", "刷新确认框残留，点キャンセル")
            return True
        if not any("体力回復確認" in t for t in texts):
            self._recover_cancels = 0
            return False
        self._recover_cancels = getattr(self, "_recover_cancels", 0) + 1
        if self._recover_cancels > 3:
            return False
        from kotonebot import device, ocr

        btn = next((t for t in ocr.ocr() if "キャンセル" in t.text), None)
        if btn is not None:
            device.click(btn.rect)
        else:
            device.click(220, 1160)
        logger.warning("轮间残留「体力回復確認」：点「キャンセル」交回轮间钩子（第 %d 次）", self._recover_cancels)
        if HiroController.session is not None:
            HiroController.session.recover("dialog", "轮间「体力回復確認」残留，点キャンセル")
        return True

    def _resume_special_guidance(self) -> bool:
        """画面是特別指導（KAA 没有这个场景，续跑或进入后转场时落在这里）：交给策略的定制流程，做完离开。"""
        if not any("特別指導" in t for t in self._ocr_texts()[:6]) or not any("カスタマイズ" in t for t in self._ocr_texts()):
            return False
        if not hasattr(self.strategy, "_special_guidance"):
            return False
        # q12 第 19 天：每次接手都定制失败又「离开成功」，回来还在特別指導页，5 秒一轮空转了 3 分钟。3 分钟内接手超过 3 次就停机
        now = time.monotonic()
        self._sg_handoffs = [t for t in getattr(self, "_sg_handoffs", []) if now - t < 180] + [now]
        if len(self._sg_handoffs) > self.SG_HANDOFF_MAX:
            self.strategy._stop("SPECIAL_GUIDANCE", f"3 分钟内第 {len(self._sg_handoffs)} 次落回特別指導页，定制 / 离开都没生效")
        logger.info("画面在特別指導页：接着做定制（第 %d 次接手）", len(self._sg_handoffs))
        self.strategy._special_guidance(None, entered=True)
        return True

    def _record_finals_ranking(self) -> bool:
        """本战「現在順位」/ 轮次结果页：识别字段（entries / my_gain / final …）连同截图记进 trace（main8 结算太快，R2 结果没留下）。
        同一页只记一次（按 entries 去重）。返回 False，让后面的补判照常进行。"""
        texts = self._ocr_texts()
        if not any("現在順位" in t or "ラウンド" in t or "最終順位" in t for t in texts):
            return False
        from kotonebot import device

        from hiro.vision.pages import classify_page

        hit = classify_page(device.screenshot())
        if not hit or hit[0] != "HIF_FINALS_RANKING":
            return False
        key = (hit[1].get("kind"), hit[1].get("finals_round"))  # OCR 会抖（40570 / 46570），按页种 + 轮次去重
        if key == getattr(self, "_last_ranking", None):
            self._ranking_repeats = getattr(self, "_ranking_repeats", 0) + 1
            if self._ranking_repeats > 20:
                return False  # 点了 20 次还在：交给未识别看门狗（存帧报警），不在这里无限循环
            self._advance_ranking(hit[1], self.strategy)
            return True
        self._ranking_repeats = 0
        self._last_ranking = key
        s = HiroController.session
        if s is not None:
            s.observe(scene="HIF_FINALS_RANKING", area="produce", img=None, fields=hit[1], save_frame=True)
            s.record()
        f = hit[1]
        self._advance_ranking(f, self.strategy)
        logger.info("本战排名页（%s）：第 %s 轮，我方第 %s 名，总分 %s，本轮 %s；全员 %s", f.get("kind"), f.get("finals_round"), f.get("rank"),
                    f.get("score") or f.get("my_score"), f.get("round_score"),
                    [(e.get("rank"), e.get("name"), e.get("score"), e.get("round_score")) for e in f.get("entries") or []])  # fmt: skip
        return True

    @staticmethod
    def _advance_ranking(f: dict, strategy=None) -> None:
        """排名页 / 轮次结果页 KAA 认不出、不会自己点「次へ」（main10 R1 结果页卡住）。
        最终页（final）：续跑正好落在这一页时考试结束流程不会跑（main10 -r6 空转 17 分钟），这里先按上限规则判断再挑戦，不挑戦就点「次へ」收尾。"""
        from kotonebot import device, ocr

        if f.get("kind") == "final" and strategy is not None and hasattr(strategy, "_finals_retry"):
            try:
                if strategy._finals_retry(device.screenshot(), "r2"):
                    return
            except Exception as e:  # 再挑戦判断失败也不能卡住：照常点「次へ」
                logger.warning("本战最终页再挑戦判断失败：%s", e)

        boxes = ocr.ocr()
        btn = next((t for t in boxes if t.text.strip() == "次へ" and t.rect.center.y > 900), None)
        if btn is not None:
            device.click(btn.rect)
        elif any("タップして次へ" in t.text or "タップして" in t.text for t in boxes):
            device.click(360, 1180)  # 开考前排名页「タップして次へ」：点画面下方（main11 卡 29 分钟）

    @staticmethod
    def _close_produce_log(texts: list[str] | None = None) -> bool:
        """texts：本轮共用 OCR 的文字；没有「N日目」就不做整帧判页（classify_page 约 1.4s，以前每个 UNKNOWN 轮次都做）。"""
        from kotonebot import device

        if texts is not None and not any("日目" in t for t in texts):
            return False

        from hiro.vision.pages import classify_page

        img = device.screenshot()
        hit = classify_page(img)
        if not hit or hit[0] != "PRODUCE_LOG":
            return False
        device.click(*hit[1].get("close", (360, 1160)))
        if HiroController.session is not None:
            HiroController.session.recover("dialog", "行动记录覆盖层：点 ✕ 关闭")
        return True

    def _is_pitem_select(self) -> bool:
        # 选拔①后取得背包「Pアイテムを選択」；选拔②③后定制「カスタム後を選択」（同一套三选一 + 实行按钮）
        return any("Pアイテムを選択" in t or "カスタム後を選択" in t for t in self._ocr_texts())

    def _reclassify_idle(self, scene: Scene) -> Scene:
        """KAA 把「三选一里已选中某项」判成 IDLE 并空转；用 hiro.vision.scene（含受け取る按钮补判）再认一次。"""
        from hiro.vision.scene import classify_current

        name = classify_current().scene
        mapped = {"SELECT_CARD": SceneType.SELECT_CARD, "SELECT_DRINK": SceneType.SELECT_DRINK}.get(name)
        if mapped is None:
            return scene
        s = HiroController.session
        if s is not None:
            s.recover("page_misjudged", f"KAA 判 IDLE，hiro.vision.scene 判 {name}，按 {name} 处理")
        logger.info("IDLE 补判为 %s", name)
        return Scene(mapped)
