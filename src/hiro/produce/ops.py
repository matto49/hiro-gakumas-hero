# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""操作层：执行一个动作、等画面给出反馈，返回 ExecutionResult；收拢 KAA 上下文的补丁操作。

等待不用固定 sleep，而是轮询截图，满足任一条件即返回：
- done(img) 为真：调用方给的完成条件（例如「受け取る」按钮消失）；
- 页面签名换了：hiro.vision.signature，覆盖不到的页面返回 None，不参与判断；
- 点击区域（roi）的帧差超过阈值：按钮是不透明 UI，背景 3D 动画影响很小。整帧帧差不能用——
  选拔赛数据集里考试页 / 剧情页同页相邻帧的整帧差中位数就有 36，而换页的 p10 只有 18。

坐标一律是 720x1280 逻辑坐标（kotonebot device 的坐标系）。截图、点击、时钟可注入，便于离线测试。
不做决策：入参是契约 Action 与要点的位置，出参是 ExecutionResult。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from hiro import imgio
from hiro.contracts import Action, ExecutionResult
from hiro.devices.active import device
from hiro.devices.active import sleep as _sleep

import cv2
import numpy as np

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.15
ROI_PAD = 4  # 点击区域外扩少许；外扩多了会把背景动画算进来
ROI_DEFAULT = (140, 70)  # 只给点、不给矩形时的区域大小
ROI_DIFF_THRESHOLD = 12.0  # 灰度平均绝对差（0–255）。选拔赛数据集按钮区同页噪声中位数约 1.5，背景动画区可到 20+；见任务单 游戏执行-01 进度
STABLE_THRESHOLD = 2.0
TAP_RETRIES = 2
# 橙色主按钮（受け取る / 交換する / 確認框的交換・削除）亮 / 灰。选拔赛数据集实测：亮 S 147–159 / V 232–234，
# 标题页同款 S 182 / V 252；灰 S 113–138 / V 172–194。
LIT_SAT, LIT_VAL = 145, 215
LIT_VAL_GAIN = 30  # 或比基准帧亮度高出这么多


@dataclass
class Waited:
    """一次等待的结果。reason：done / page / roi；超时为 None。"""

    changed: bool
    reason: str | None
    waited_s: float
    img: np.ndarray | None
    page: str | None


def _xywh(target, size: tuple[int, int] = ROI_DEFAULT) -> tuple[int, int, int, int]:
    """Rect / (x, y, w, h) / (x, y) → (x, y, w, h)；点按 size 取以它为中心的区域。"""
    if hasattr(target, "xywh"):  # Box（hiro.vision.ocr）或别的带 xywh 的框
        return tuple(int(v) for v in target.xywh)
    if len(target) == 4:
        return tuple(int(v) for v in target)
    x, y = target
    w, h = size
    return int(x - w / 2), int(y - h / 2), w, h


def center(target) -> tuple[int, int]:
    x, y, w, h = _xywh(target)
    return x + w // 2, y + h // 2


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def roi_diff(a: np.ndarray, b: np.ndarray, roi=None, pad: int = ROI_PAD) -> float:
    """两帧在 roi（外扩 pad）内的灰度平均绝对差；roi=None 时比较整帧。"""
    ga, gb = _gray(a), _gray(b)
    if roi is not None:
        x, y, w, h = _xywh(roi)
        H, W = ga.shape
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)
        ga, gb = ga[y0:y1, x0:x1], gb[y0:y1, x0:x1]
    return float(cv2.absdiff(ga, gb).mean())


def button_sv(img: np.ndarray, roi) -> tuple[float, float]:
    """roi 内的平均饱和度、亮度（HSV，0–255）。"""
    x, y, w, h = _xywh(roi)
    hsv = cv2.cvtColor(img[y : y + h, x : x + w], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()), float(hsv[..., 2].mean())


def orange_lit(img: np.ndarray, roi, base_val: float | None = None) -> bool:
    """橙色主按钮是否亮着（可点）。base_val 是点之前同一区域的亮度，用来兜底绝对阈值。"""
    sat, val = button_sv(img, roi)
    return (sat >= LIT_SAT and val >= LIT_VAL) or (base_val is not None and val - base_val >= LIT_VAL_GAIN)


def page_of(img: np.ndarray) -> str | None:
    from hiro.vision.signature import match

    return match(img)[0]


def wait_change(
    before: np.ndarray,
    *,
    roi=None,
    page_before: str | None = None,
    done: Callable[[np.ndarray], bool] | None = None,
    timeout: float = 8.0,
    interval: float = POLL_INTERVAL,
    threshold: float = ROI_DIFF_THRESHOLD,
    use_signature: bool = True,
    shoot: Callable[[], np.ndarray] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
) -> Waited:
    """动作下发后轮询，直到出现反馈（见模块说明）或超时。第一帧立即截，不先睡。"""
    shoot = shoot or device.screenshot
    t0 = clock()
    img = page = None
    while True:
        img = shoot()
        if done is not None and done(img):
            return Waited(True, "done", clock() - t0, img, page)
        if use_signature:
            page = page_of(img)
            if page is not None and page_before is not None and page != page_before:
                return Waited(True, "page", clock() - t0, img, page)
        if roi is not None and roi_diff(before, img, roi) > threshold:
            return Waited(True, "roi", clock() - t0, img, page)
        if clock() - t0 >= timeout:
            return Waited(False, None, clock() - t0, img, page)
        sleep(interval)


def wait_stable(
    *,
    roi=None,
    timeout: float = 3.0,
    interval: float = POLL_INTERVAL,
    threshold: float = STABLE_THRESHOLD,
    shoot: Callable[[], np.ndarray] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
) -> Waited:
    """等相邻两帧在 roi 内几乎不变（转场 / 弹窗动画结束）。超时返回最后一帧，changed=False。"""
    shoot = shoot or device.screenshot
    t0 = clock()
    prev = shoot()
    while True:
        sleep(interval)
        img = shoot()
        if roi_diff(prev, img, roi) <= threshold:
            return Waited(True, "stable", clock() - t0, img, None)
        if clock() - t0 >= timeout:
            return Waited(False, None, clock() - t0, img, None)
        prev = img


def tap(
    action: Action,
    target,
    *,
    page_before: str | None = None,
    done: Callable[[np.ndarray], bool] | None = None,
    roi=None,
    timeout: float = 5.0,
    retries: int = TAP_RETRIES,
    shoot: Callable[[], np.ndarray] | None = None,
    click: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _sleep,
) -> ExecutionResult:
    """点 target（Rect / (x, y, w, h) / (x, y)），等反馈；没反馈就重点，最多 retries 次。

    roi 默认取 target 本身：点按钮看按钮那块有没有变。page_after 是最后一帧的页面签名（覆盖不到时沿用 page_before）。
    expected_transition 留给主循环（hiro.loop.session.verify）按转移图判断，这里填 None。
    """
    shoot = shoot or device.screenshot
    click = click or device.click
    x, y = center(target)
    roi = target if roi is None else roi
    t0 = clock()
    taps: list[list[int]] = []
    waited_total = 0.0
    w = None
    for attempt in range(retries + 1):
        before = shoot()
        click(x, y)
        taps.append([x, y])
        w = wait_change(
            before, roi=roi, page_before=page_before, done=done, timeout=timeout, shoot=shoot, clock=clock, sleep=sleep
        )
        waited_total += w.waited_s
        if w.changed:
            break
        logger.info("%s：点击 (%d, %d) 后 %.1fs 无反馈（第 %d 次）", action, x, y, w.waited_s, attempt + 1)
    return ExecutionResult(action=action, ok=w.changed, taps=taps, waited_s=round(waited_total, 3), page_before=page_before,
                           page_after=w.page or page_before, expected_transition=None, retries=len(taps) - 1,
                           error=None if w.changed else f"点击 {len(taps)} 次，每次 {timeout:.0f}s 内画面无反馈",
                           seconds=round(clock() - t0, 3))  # fmt: skip


# ---------------------------------------------------------------- 多步页面操作


CALIB_DIR = imgio.calib_root()  # 多步页面操作每一步存帧（标定用；reports/ 已 gitignore；初星助手的任务存进任务目录）
OcrBoxes = Callable[[np.ndarray], list]  # img → [(text, conf, (x1, y1, x2, y2))]


def ocr_boxes(img: np.ndarray) -> list:
    from hiro.vision.ocr import ocr_boxes as _ocr

    return _ocr(img, scale=1.5)


def find_text(boxes: list, needles, *, region=None, exact: bool = False):
    """在 OCR 结果里找第一个包含（exact 时等于）任一 needle 的框，返回中心 (x, y) 或 None。region=(x1, y1, x2, y2) 限定中心位置。"""
    needles = (needles,) if isinstance(needles, str) else needles
    for text, _, (x1, y1, x2, y2) in boxes:
        t = text.replace(" ", "")
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if region is not None and not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
            continue
        if any(t == n if exact else n in t for n in needles):
            return cx, cy
    return None


def button_text(t: str, text: str) -> bool:
    """按钮文字匹配：完全一致，或前面多了 1–2 个图标残字（按钮左边的 ✓ / ✕ 常被读成「く」「X」「メ」：
    第二十五局「ターン終了」的「はい」读成「くはい」没点上；「✕キャンセル」读成「メキャンセル」）。"""
    t = t.strip().replace(" ", "").replace("　", "")
    return t == text or (t.endswith(text) and len(t) - len(text) <= 2)


class Run:
    """一次多步页面操作的记录：点击坐标、等待时长、起止时间；可选把每一步的画面存到 CALIB_DIR/<label>/ 供标定。"""

    def __init__(
        self,
        action: Action,
        page: str | None,
        shoot,
        click,
        clock,
        sleep,
        *,
        label: str = "",
        ocr=None,
        calib: bool = False,
    ):
        self.action, self.page, self.label = action, page, label or (page or "")
        self.shoot, self.click_fn, self.clock, self.sleep = shoot, click, clock, sleep
        self.ocr = ocr or ocr_boxes
        self.taps: list[list[int]] = []
        self.waited = 0.0
        self.t0 = clock()
        self.calib = CALIB_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{self.label}" if calib else None
        self._snaps = 0
        self.real_click = None  # 真实鼠标点击（hiro.real_click.click）：只在实机路径显式打开，测试里的假点击不会触发
        self.real_taps = 0

    def snap(self, img: np.ndarray, tag: str) -> None:
        if self.calib is None:
            return
        self.calib.mkdir(parents=True, exist_ok=True)
        self._snaps += 1
        imgio.imwrite(str(self.calib / f"{self._snaps:02d}-{tag}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    def tap(self, xy) -> None:
        self.click_fn(int(xy[0]), int(xy[1]))
        self.taps.append([int(xy[0]), int(xy[1])])

    def tap_confirmed(self, xy, done: Callable[[np.ndarray], bool], timeout: float, *, what: str = ""):
        """点 xy，等 done(img)；timeout 内没变化且开了 real_click：用真实鼠标再点一次再等（第二十三局：饮料详情「捨てる」、
        确认框「はい」、「受け取る」用 MaaTools 点没反应，真实鼠标才生效）。返回 (结果, 最后一帧)。"""
        self.tap(xy)
        ok, img = self.until(done, timeout, min_checks=2)
        if ok or self.real_click is None:
            return ok, img
        logger.warning("%s：点「%s」%s 后 %.0fs 画面没反应，改用真实鼠标再点", self.label, what, tuple(xy), timeout)
        try:
            self.real_click(int(xy[0]), int(xy[1]))
        except Exception as e:  # noqa: BLE001  找不到窗口等：按没点成处理
            logger.warning("真实鼠标点击失败：%r", e)
            return False, img
        self.real_taps += 1
        self.taps.append([int(xy[0]), int(xy[1])])
        return self.until(done, timeout, min_checks=2)

    def wait(self, before, **kw) -> Waited:
        w = wait_change(before, shoot=self.shoot, clock=self.clock, sleep=self.sleep, use_signature=False, **kw)
        self.waited += w.waited_s
        return w

    def until(self, check: Callable[[np.ndarray], bool], timeout: float, *, tick: Callable[[np.ndarray], None] | None = None,
              min_checks: int = 1):  # fmt: skip
        """轮询 check(img)，直到为真或超时；tick 每轮调用一次（例如点击跳过动画）。返回 (结果, 最后一帧)。
        min_checks：超时前至少看几帧（check 本身很慢时——第二十三局录像时读一次卡格要数秒——不能只看点完立刻截的那一帧）。"""
        t0 = self.clock()
        checks = 0
        while True:
            img = self.shoot()
            checks += 1
            if check(img):
                self.waited += self.clock() - t0
                return True, img
            if self.clock() - t0 >= timeout and checks >= min_checks:
                self.waited += self.clock() - t0
                return False, img
            if tick is not None:
                tick(img)
            self.sleep(POLL_INTERVAL)

    def until_text(self, needles, timeout: float, *, region=None, exact: bool = False):
        """轮询 OCR 直到找到文字。返回 (中心 (x, y) 或 None, 最后一帧)。"""
        found = [None]

        def check(img):
            found[0] = find_text(self.ocr(img), needles, region=region, exact=exact)
            return found[0] is not None

        _, img = self.until(check, timeout)
        return found[0], img

    def result(self, ok: bool, error: str | None = None, page_after: str | None = "same") -> ExecutionResult:
        if error:
            logger.warning("%s %s：%s", self.label, self.action, error)
        return ExecutionResult(action=self.action, ok=ok, taps=self.taps, waited_s=round(self.waited, 3), page_before=self.page,
                               page_after=self.page if page_after == "same" else page_after, expected_transition=None,
                               retries=0, error=error, seconds=round(self.clock() - self.t0, 3))  # fmt: skip


# ---------------------------------------------------------------- KAA 上下文补丁


RECEIVE_BAND = (1040, 1150)  # 三选一底部「受け取る」所在的一条（实测中心 (355,1096)）
RECEIVE_ROI = (260, 1062, 200, 50)  # 「受け取る」按钮：没选中时灰（S 116–134 / V 171–186），选中后橙色
RECEIVE_TAP = (360, 1092)
RECEIVE_SAT_GAIN = 50  # 选中后按钮由灰转橙：饱和度比点之前高这么多也算亮


def find_receive(img: np.ndarray) -> tuple[int, int] | None:
    """底部条带局部 OCR 找「受け取る」（约 0.1s；以前整帧 kotonebot OCR 2–4s）。"""
    y1, y2 = RECEIVE_BAND
    for t, _, (bx1, by1, bx2, by2) in ocr_boxes(img[y1:y2]):
        if "受け取る" in t.replace(" ", ""):
            return (bx1 + bx2) // 2, (by1 + by2) // 2 + y1
    return None


def confirm_acquire(tries: int = 3, *, timeout: float = 3.0) -> int:
    """KAA CardSelectContext.commit 只在找到灰色「受け取る」时才点；选中后按钮变橙色就退出，画面停在三选一（KAA 判 IDLE）。
    这里补点亮着的「受け取る」：每次点完等按钮区域变化（弹窗关闭），不再固定睡 1.5s。返回补点次数。"""
    clicks = 0
    for _ in range(tries):
        img = device.screenshot()
        pos = find_receive(img)
        if pos is None:
            break
        device.click(*pos)
        clicks += 1
        x, y = pos
        w = wait_change(img, roi=(x - 80, y - 25, 160, 50), timeout=timeout, use_signature=False)
        logger.info("补点「受け取る」第 %d 次：%s（%.2fs）", clicks, w.reason or "无反馈", w.waited_s)
    return clicks


def fast_acquire(card_center, *, shoot=None, click=None, sleep=_sleep, clock=time.monotonic, lit_timeout: float = 1.5,
                 close_timeout: float = 3.0) -> bool:  # fmt: skip
    """三选一（卡 / 饮料）：点选项 → 等「受け取る」亮起 → 点 → 等它消失。替代 KAA commit 的固定等待（点后 sleep 0.5 + 2s 再循环一轮，
    第二十一局选拔选卡执行中位 8.5s）。亮不起来（再点一次选项也不行）返回 False，调用方先看是不是弹了设置框，再退回逐步提交。"""
    shoot = shoot or device.screenshot
    click = click or (lambda x, y: device.click(x, y))
    base = shoot()
    base_sat, base_val = button_sv(base, RECEIVE_ROI)

    def lit(img) -> bool:
        # 亮起没有实拍帧核对过（trace 只在选之前存帧）：绝对阈值或相对点之前的亮度 / 饱和度明显升高都算
        sat, _val = button_sv(img, RECEIVE_ROI)
        return orange_lit(img, RECEIVE_ROI, base_val) or sat - base_sat >= RECEIVE_SAT_GAIN

    for _attempt in range(2):
        click(*card_center)
        deadline = clock() + lit_timeout
        ok = False
        while clock() < deadline:
            sleep(0.15)
            if lit(shoot()):
                ok = True
                break
        if ok:
            break
    else:
        logger.info("三选一：点了选项「受け取る」没亮，退回逐步提交")
        return False
    click(*RECEIVE_TAP)
    deadline = clock() + close_timeout
    while clock() < deadline:
        sleep(0.2)
        img = shoot()
        if not lit(img) and find_receive(img) is None:
            return True
    logger.info("三选一：点了「受け取る」%.0fs 后按钮还在", close_timeout)
    return True  # 已点过亮着的「受け取る」：交给调用方核对页面


# ---------------------------------------------------------------- 选卡再抽選确认框
REROLL_CONFIRM = ("再抽選する", "決定", "はい", "OK")
REROLL_COST_WORDS = ("ジュエル",)  # 确认框上出现这些就点キャンセル（绝不用ジュエル）


def _btn_norm(t: str) -> str:
    import re

    return re.sub(r"[^\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]", "", t)  # 按钮前的「→」「×」图标会和字连着读出来


def reroll_confirm_action(texts) -> tuple[str, object, str | None] | None:
    """「スキルカード再抽選確認」等再抽選确认框的文字（hiro.vision.ocr.Text，只看 y > 600）→ 该点什么：
    ("cost", キャンセル 的框或 None, 消耗字样) / ("ok", 确认按钮的框, None) / None（这一帧没有确认按钮）。
    「次回以降表示しない」不动（勾着第二次刷新照样弹，协调 11-14）。"""
    texts = [t for t in texts if t.rect.center.y > 600]
    if (cost := next((t.text for t in texts if any(w in t.text for w in REROLL_COST_WORDS)), None)) is not None:
        return "cost", next((t for t in texts if "キャンセル" in t.text), None), cost
    hit = next((t for t in texts if _btn_norm(t.text) in REROLL_CONFIRM), None)
    return ("ok", hit, None) if hit is not None else None
