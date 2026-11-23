"""过场推进：主循环判不出页面（KAA UNKNOWN / IDLE，快速判页也没认出）时，在安全的帧上连点空白处推进动画。

第十五到十七局：动作执行完到下一个可决策页面，未识别画面合计每局约 20 分钟（选拔 13–21 分钟）。
KAA 的 skip() 本来每轮点一次 (12,12)，但 UNKNOWN 每轮要做好几次整帧 OCR，中位 6.7s 才点一下。
主循环共用 OCR 之后每轮约 1.5–3s；这里在每轮末尾再连点几下（间隔 ADVANCE_EVERY），把点击频率提到约 0.4s 一次。

分工（与识别 0146646 对齐）：页面签名由识别层负责——hiro.vision.quick.quick_page 认出的「点一下就过」的页面由
HiroController._quick_page 按它给的位置点；这里只处理认不出的帧，点击位置固定用 KAA skip() 的 (12,12)
（左上角落，历次实机每个 UNKNOWN 轮次都点过）。

安全规则（任一不满足就不点，交给主循环下一轮）：
- 本轮 OCR 里有按钮或购买类字样（BLOCK_WORDS）；
- 有橙色标题条的弹窗（hiro.vision.pages.dialog_header，约 10ms）；
- 横屏 LIVE；
- 连点途中重新截图，快速判页认出了页面（交主循环按页面处理）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from hiro.vision.textnorm import contains

ADVANCE_TAP = (12, 12)  # 与 KAA kaa.tasks.common.skip() 相同
# 角落点了一阵还过不去时改点上方中间空白（第二十三局：P 道具效果触发说明条 / 获得卡片演出，(12,12) 点不掉停 78s，
# 总控点 (360,250) 才过）。只在这一块没有 OCR 文字时点（ALT_CLEAR：点击点周围），由主循环判断后传 alt=True
ALT_TAP = (360, 250)
ALT_CLEAR = (200, 170, 520, 330)
ALT_AFTER = 8.0  # 同一段认不出持续这么久才改点 ALT_TAP


EXAM_MARKS = ("残りターン", "ターン目")  # 考试画面：(360,250) 是排名区，绝不改点这里（决策 99fa922 审 1.2 时提出，c38）


def alt_clear(boxes) -> bool:
    """boxes = [(text, conf, (x1, y1, x2, y2))]：不是考试画面、且 ALT_CLEAR 区域里没有文字框中心才算空白。"""
    if any(m in t for t, _, _ in boxes for m in EXAM_MARKS):
        return False
    x1, y1, x2, y2 = ALT_CLEAR
    return not any(x1 <= (b[0] + b[2]) / 2 <= x2 and y1 <= (b[1] + b[3]) / 2 <= y2 for _, _, b in boxes)


ADVANCE_EVERY = 0.4
ADVANCE_BURST = 3  # 每轮末尾最多再点几下（加上 KAA skip() 那一下）
# 画面上有这些字（按钮 / 购买 / 危险操作）时不点空白：可能是要我们做决定的页面，交给对应流程
BLOCK_WORDS = ("購入", "ジュエル", "破棄", "リタイア", "キャンセル", "決定", "使う", "受け取る", "はい", "いいえ", "閉じる", "次へ",
               "終了", "交換", "強化", "削除", "チェンジ", "実行", "リトライ", "タイトルへ", "選択してください", "開始確認")  # fmt: skip


def blocked(texts: list[str]) -> str | None:
    """返回命中的字样（不点的原因），没有返回 None。"""
    return next((w for w in BLOCK_WORDS if any(contains(t, w) for t in texts)), None)


def burst(texts: list[str], *, shoot: Callable, click: Callable[[int, int], None], sleep: Callable[[float], None] = time.sleep,
          in_live: Callable = lambda img: False, quick: Callable = lambda img: None, header: Callable = lambda img: None,
          n: int = ADVANCE_BURST, alt: bool = False) -> int:  # fmt: skip
    """在当前帧安全时连点 ADVANCE_TAP 最多 n 下，返回点了几下。每下之前重新截图，检查弹窗标题条 / 横屏 LIVE / 快速判页。
    alt=True（角落点了 ALT_AFTER 秒还没过、ALT_TAP 附近没有文字）：第一下改点 ALT_TAP。"""
    if blocked(texts):
        return 0
    taps = 0
    for _ in range(n):
        img = shoot()
        if img is None or in_live(img) or header(img) is not None or quick(img) is not None:
            break
        click(*(ALT_TAP if alt and taps == 0 else ADVANCE_TAP))
        taps += 1
        sleep(ADVANCE_EVERY)
    return taps
