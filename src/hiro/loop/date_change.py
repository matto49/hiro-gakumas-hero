# 来源：digital-hiro hiro/loop/date_change.py（c3fc27f）
"""游戏日切换（凌晨 4:00）「日付変更 / 日付が変わりました。/ タイトルへ」与数据更新「データ更新 / データ更新があります。/ タイトルへ」的处理。

main13 本战（2026-11-02 04:00）：R1 结果页之后弹出这个框，hif.on_exam_entered 收尾的 `for _ in Loop()` 一直找到压在弹窗下面的
「次へ」并点它（没反应），没有日志、没有超时，空转 5 分钟（kotonebot 的 Loop 不检查 timeout 参数）。
- loop_callback()：挂到 kotonebot conf().loop.loop_callbacks，每个 KAA Loop 的每一轮都会调；按间隔 OCR 一次，认出日期切换就抛 DateChanged，
  把卡在任何 Loop 里的流程打出来。
- recover_to_home()：点「タイトルへ」→ 标题页点「TAP TO START」→ 登录奖励 / 公告一路点「閉じる」或空白处（出现购买 / ジュエル字样时不点空白，
  只认「閉じる / キャンセル」）→ 到首页。run_hif 收到 DateChanged 后调它，再从首页「プロデュース中」续跑同一 trace。
"""

from __future__ import annotations

from hiro.vision.textnorm import contains_any

import time
from collections.abc import Callable

from hiro.log import logging

logger = logging.getLogger(__name__)

DATE_TITLES = ("日付変更", "日付が変わりました")
# 游戏推送数据更新（Windows 第四局 R2 最后一回合 09:40：「データ更新 / データ更新があります。」只有「タイトルへ」一个按钮，
# 看门狗空转 183s 停机）：和日期切换一样是游戏强制回标题，同一条路处理（协调 2026-11-07 批准点タイトルへ）
DATA_UPDATE_TITLES = ("データ更新",)
FORCED_TITLES = DATE_TITLES + DATA_UPDATE_TITLES
TO_TITLE = ("タイトルへ",)
# 回标题后可能要下载数据：有「ダウンロード」字样时点这些按钮，绝不点キャンセル（不下载就进不去）
DOWNLOAD_HINTS = ("ダウンロード",)
DOWNLOAD_OK = ("ダウンロード", "OK", "はい", "同意する", "決定")  # 「データダウンロード 214.6 MB」确认框是 キャンセル / 決定（Windows 第十七局卡了 15 分钟）
TAP_TO_START = ("TAPTOSTART",)
CLOSE = ("閉じる", "キャンセル")
PURCHASE_HINTS = ("購入", "ジュエル", "パック", "ショップ", "円")
MAX_RECOVER_S = 900.0  # 下载会续时间，但总共不超过 15 分钟
BLANK_TAP = (360, 220)  # 登录奖励 / 公告：点空白推进（画面上方，避开底部按钮与横幅）


class DateChanged(Exception):
    """游戏日切换：已经（或需要）回到首页，run_hif 应从首页续跑同一局。"""


def _norm(t: str) -> str:
    return t.replace(" ", "").replace("　", "")


def _center(b) -> tuple[int, int]:
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2


def date_change_button(boxes) -> tuple[int, int] | None:
    """OCR 结果里有日期切换 / 数据更新弹窗就返回「タイトルへ」的位置，否则 None（要标题和按钮都在）。"""
    texts = [(_norm(t), b) for t, _, b in boxes]
    if not any(any(k in t for k in FORCED_TITLES) for t, _ in texts):
        return None
    return next((_center(b) for t, b in texts if contains_any(t, TO_TITLE)), None)


def _safe_step(assist, img, boxes) -> bool:
    try:
        return assist.step(img, boxes)
    except Exception as e:  # noqa: BLE001  认不出画面处理出错不能打断回首页
        logger.debug("日切换恢复：认不出画面处理出错：%r", e)
        return False


def recover_to_home(shoot: Callable, click: Callable[[int, int], None], ocr: Callable, at_home: Callable[[object], bool], *,
                    clock: Callable[[], float] | None = None, sleep: Callable[[float], None] = time.sleep,
                    timeout: float = 240.0, interval: float = 2.0) -> bool:  # fmt: skip
    """从日期切换弹窗一路回到首页。返回是否到了首页。"""
    if clock is None:
        from hiro.loop.pause_flag import active_clock

        clock = active_clock  # 录屏暂停期间不计时
    from hiro.loop.assist import Assist

    assist = Assist("日切换恢复", click=click)  # 快速判页 + 卡住交 LLM（同主循环；登录奖励等认不出的画面）
    t0 = start = clock()
    while clock() - t0 < timeout and clock() - start < MAX_RECOVER_S:
        img = shoot()
        if at_home(img):
            logger.info("日期切换：已回到首页（%.0fs）", clock() - t0)
            return True
        boxes = ocr(img)
        texts = [(_norm(t), b) for t, _, b in boxes]
        if no_boost := next((_center(b) for t, b in texts if t == MUMU_NO_BOOST), None):
            # MuMu 拉起游戏时的「是否为《学マス》开启 MuMu 加速服务?」：点「不加速」（第十五局、GKMS 第五局都是这么处理的）
            logger.info("回首页：MuMu 加速服务提示，点「不加速」")
            click(*no_boost)
        elif pos := date_change_button(boxes):
            logger.info("日期切换 / 数据更新：点「タイトルへ」")
            click(*pos)
        elif any(any(k in t for k in DOWNLOAD_HINTS) for t, _ in texts):
            ok = next((_center(b) for t, b in texts if t in DOWNLOAD_OK and _center(b)[1] > 700), None)
            if ok:
                logger.info("回首页：下载确认，点「%s」", next(t for t, b in texts if _center(b) == ok))
                click(*ok)
            else:
                logger.info("回首页：数据下载中，等待")
            t0 = max(t0, clock() - timeout / 2)  # 下载可能很久：看到下载字样就把剩余时间至少续到一半
        elif tap := next((_center(b) for t, b in texts if any(k in t.upper() for k in TAP_TO_START)), None):
            logger.info("日期切换：标题页点「TAP TO START」")
            click(*tap)
        elif close := next((_center(b) for t, b in texts if any(t == k or t.endswith(k) for k in CLOSE) and _center(b)[1] > 900), None):
            click(*close)
        elif _event_login_bonus(img):
            # 活动登录奖励（「開催期間」白字；奖励里有「ジュエル」，会命中下面的购买字样）：点底部空白翻页。
            # DMM dmm2 日切换「学マス水曜日」送 250 ジュエル：以前停在这里等不存在的「閉じる」，240s 后停机
            # 但限时扭蛋 / 礼包 / 公告也印「開催期間」，底部中间是「引く」「購入」：有这些字就不点，等「閉じる」
            if _purchase_texts([t for t, _ in texts]):
                logger.info("日期切换：「開催期間」页面有购买类按钮，不点，等「閉じる」")
            else:
                from hiro.vision.quick import LOGIN_BONUS_TAP

                logger.info("日期切换：活动登录奖励，点底部空白")
                click(*LOGIN_BONUS_TAP)
        elif any(any(k in t for k in PURCHASE_HINTS) for t, _ in texts):
            logger.info("日期切换：画面有购买相关字样，不点空白，等「閉じる」")
        elif _safe_step(assist, img, boxes):
            pass  # 快速判页认出并点了 / LLM 兜底执行了一个受限动作
        else:
            click(*BLANK_TAP)
        sleep(interval)
    logger.warning("日期切换：%.0fs 内没回到首页", timeout)
    return False


def _purchase_texts(texts) -> bool:
    """「開催期間」页面上有「引く」「購入」「ガシャ」「パック」「円」：是扭蛋 / 礼包 / 商店，不按登录奖励点底部中间。"""
    from hiro.vision.quick import has_purchase_words

    return has_purchase_words(texts)


def _event_login_bonus(img) -> bool:
    """活动登录奖励弹窗：快速判页的「開催期間」白字模板（hiro.vision.quick，fixtures 反例 ≤ 0.52）。截图不是图像时不判。"""
    if getattr(img, "ndim", 0) < 2:
        return False
    try:
        from hiro.vision.quick import _white_hit

        return bool(_white_hit(img, "quick_event_period"))
    except Exception:  # noqa: BLE001
        return False


MUMU_NO_BOOST = "不加速"  # MuMu 加速服务提示的取消按钮（另一个是「立即加速」）


DIALOG_STRIP = (880, 1240)  # 弹窗标题「日付変更」y≈973、按钮「タイトルへ」y≈1159（2026-10-30 实拍）；只 OCR 这一条，约 0.2s（整帧约 2s）


def strip_boxes(img, ocr) -> list:
    """只 OCR 弹窗所在的横条，框坐标换回整帧。"""
    y1, y2 = DIALOG_STRIP
    if img is None or getattr(img, "ndim", 0) < 2 or img.shape[0] < y2 or img.shape[0] < img.shape[1]:
        return []  # 横屏（LIVE 1280x720）或截图异常：弹窗横条不存在，不检测（q14：LIVE 时空图 OCR 抛 cv2.error，主循环连崩 6 次退出）
    return [(t, c, (bx1, by1 + y1, bx2, by2 + y1)) for t, c, (bx1, by1, bx2, by2) in ocr(img[y1:y2])]


def loop_callback(ocr: Callable, *, every: float = 5.0, clock: Callable[[], float] = time.monotonic):
    """kotonebot Loop 全局回调：每 every 秒用这一轮的截图 OCR 一次（只看弹窗横条），认出日期切换就抛 DateChanged。"""
    last = [-1e9]

    def cb(loop) -> bool:
        now = clock()
        if now - last[0] < every or getattr(loop, "screenshot", None) is None:
            return False
        last[0] = now
        try:
            boxes = strip_boxes(loop.screenshot, ocr)
        except Exception as e:  # noqa: BLE001  检测本身出错不能打断主流程
            logger.warning("日期切换检测出错（%s），本轮跳过", e)
            return False
        if date_change_button(boxes) is not None:
            kind = "データ更新" if any(any(k in _norm(t) for k in DATA_UPDATE_TITLES) for t, _, _ in boxes) else "日付変更"
            logger.warning("日期切换 / 数据更新：在 KAA 循环里认出「%s」，打断当前流程", kind)
            raise DateChanged(kind)
        # 通信エラー（第二十二局轮间卡死 195s）：同一条带里认，リトライ就点；只能回标题就点閉じる并抛 CommErrorToTitle（同日期切换的回首页路）
        from hiro.loop import comm_error

        if comm_error.comm_error(boxes) is not None:
            from kotonebot import device

            comm_error.handle(boxes, lambda x, y: device.click(x, y))
        return False

    return cb
