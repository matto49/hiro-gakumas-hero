"""危险点击拦截：所有点击都经过这里，处理器拿到的设备就是 GuardedDevice，绕不过去。

规则（去 KAA 设计稿 §2.3 第 2 条、§3.4）：
1. 当前画面开着危险弹窗（リタイア 确认、培育数据破棄 / 别的端末进行中「開始する」，hiro.vision.pages.read_dialog 的 danger）：
   只放行点在「キャンセル / 閉じる」上的点击，其余一律拦下。
2. 点的位置落在含 ジュエル / 購入 / 課金 / 有償 / 破棄 / リタイア 的文字上：拦下（不管有没有弹窗）。
   培育中 ≡ 菜单、「プロデュース再開」框上也有「リタイア」字样，只要不点在它上面就不拦。
   （「購入」在培育里只会是ジュエル / 金币购买；日常任务的金币商店以后走 runtime 时要单独放行。）
3. 模式自带的弹窗危险词（Runtime.dialog_words，缺省空）：弹窗标题或正文含其中任一个，同第 1 条只放行キャンセル / 閉じる。
   偶像之路用它拦「再挑戦」确认框上的消耗（ジュエル / アイテム / チケット / 回数，hiro.runtime.tower.DIALOG_WORDS）；
   H.I.F 不开（本战再挑戦按用户规则可以用券，由执行层读确认框判断）。
拦下就抛 DangerBlocked（ResumeBlocked：停机交给人，不重试），画面留给人看。

代价：判弹窗先看像素（约 20 ms），有弹窗才读标题和正文；「点在什么字上」只 OCR 点击点周围 TAP_ROI 那一小块，不做整帧 OCR。
判断用的帧比 FRESH_S 旧（或还没截过图）时先重截一张：处理器截完图等了一会儿才点，期间弹出的危险框也看得见。
"""

from __future__ import annotations

from hiro.loop.errors import ResumeBlocked
from hiro.runtime.frame import Frame, Screen
from hiro.vision.ocr import screen_texts

HARD_WORDS = ("ジュエル", "購入", "課金", "有償", "破棄", "リタイア")
SAFE_ON_DANGER = ("キャンセル", "閉じる")
TAP_ROI = (160, 40)  # 点击点左右 / 上下各看这么远（320×80 的一块，够读一个按钮）
FRESH_S = 0.5
PAD = 6  # 文字框外扩这么多像素也算点在字上


class DangerBlocked(ResumeBlocked):
    """要点的地方危险（见模块说明）：停机，不点。"""


def word_at(frame: Frame, x: int, y: int) -> str | None:
    """(x, y) 落在哪段文字上：只 OCR 点击点周围一小块。落在空白处 None。"""
    h, w = frame.img.shape[:2]
    dx, dy = TAP_ROI
    x1, y1, x2, y2 = max(0, x - dx), max(0, y - dy), min(w, x + dx), min(h, y + dy)
    if x2 <= x1 or y2 <= y1:
        return None
    for t in screen_texts(frame.img, (x1, y1, x2 - x1, y2 - y1)):
        if t.rect.x1 - PAD <= x <= t.rect.x2 + PAD and t.rect.y1 - PAD <= y <= t.rect.y2 + PAD:
            return t.text
    return None


def check_tap(screen: Screen, x: int, y: int, dialog_words: tuple[str, ...] = ()) -> None:
    """(x, y) 能不能点；不能就抛 DangerBlocked。dialog_words：模式自带的弹窗危险词（见模块说明第 3 条）。"""
    f = screen.frame
    if f is None or screen.clock() - f.t > FRESH_S:
        f = screen.shot()
    word = word_at(f, x, y)
    if f.has_dialog and (d := f.dialog) is not None and not (word and any(s in word for s in SAFE_ON_DANGER)):
        if d["danger"]:
            raise DangerBlocked(
                f"危险弹窗「{d['title']}」开着，只能点キャンセル：拦下 ({x}, {y}) {word or '（空白处）'}"
            )
        if hit := next((w for w in dialog_words if w in d["title"] or w in d.get("body", "")), None):
            raise DangerBlocked(
                f"弹窗「{d['title']}」含「{hit}」，只能点キャンセル：拦下 ({x}, {y}) {word or '（空白处）'}"
            )
    if word and (hit := next((w for w in HARD_WORDS if w in word), None)):
        raise DangerBlocked(f"要点的「{word}」含「{hit}」：拦下 ({x}, {y})")


class GuardedDevice:
    """hiro.devices 的设备 + 点击前检查。点击类方法（tap，以及底层有的 real_tap）都过 check_tap；
    其余只转发白名单里的（截图、滑动、连接、状态）；别的方法一律不给，免得新加的点击方法绕过检查。
    AdbDevice.shell 不转发（能 input tap）：拉起游戏这类窄用途直接用 Runtime.raw_device。"""

    PASS = frozenset({"screenshot", "swipe", "alive", "orientation", "close", "connect"})

    def __init__(self, device, screen: Screen, dialog_words: tuple[str, ...] = ()):
        self._device, self._screen, self._dialog_words = device, screen, dialog_words
        self.name = device.name
        self.on_tap = None  # on_tap(x, y)：过了拦截、真点之前调（开局阶段存帧用，hiro.runtime.opening.new_produce）；出错不影响点击
        if hasattr(
            device, "real_tap"
        ):  # 只有 MaaToolsDevice 有（PlayCover 真实鼠标兜底）；调用方按 getattr(dev, "real_tap", None) 判断
            self.real_tap = self._real_tap

    def tap(self, x: int, y: int) -> None:
        check_tap(self._screen, x, y, self._dialog_words)
        self._notify(x, y)
        self._device.tap(x, y)

    def _real_tap(self, x: int, y: int) -> None:
        check_tap(self._screen, x, y, self._dialog_words)
        self._notify(x, y)
        self._device.real_tap(x, y)

    def _notify(self, x: int, y: int) -> None:
        if self.on_tap is not None:
            try:  # noqa: SIM105
                self.on_tap(x, y)
            except Exception:  # noqa: BLE001  存帧之类的旁路出错不影响点击
                pass

    def __getattr__(self, name):
        if name in self.PASS:
            return getattr(self._device, name)
        raise AttributeError(f"GuardedDevice 不转发 {name!r}（点击类方法要先过危险词拦截）")
