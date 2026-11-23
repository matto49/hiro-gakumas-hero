"""H.I.F 轮间（インターバル）结束的兜底：执行器结束不了时点「終了」和确认框，直到轮间标题消失
（替代 KAA HifRoundIntervalContext.end；KAA 没超时，这里加了）。设备走 hiro.devices.active 门面，不依赖 KAA。"""

from __future__ import annotations

import logging
import time

from hiro.devices.active import device, sleep
from hiro.vision.ui_templates import tpl

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
BLANK_TAP = (12, 12)  # 都没认出：点左上空白推进（同 KAA skip()）


def end_interval(*, timeout: float = TIMEOUT, clock=time.monotonic) -> bool:
    """点到轮间标题消失为止，返回 True；timeout 秒还在轮间返回 False（调用方停机）。"""
    t0 = clock()
    while clock() - t0 < timeout:
        img = device.screenshot()
        if (m := tpl("InProduce.HifIntervalConfirm.Title").find(img)) is not None:  # noqa: F841
            if (b := tpl("InProduce.HifIntervalConfirm.ButtonConfirm").find(img)) is not None:
                logger.info("轮间结束：确认框点确定")
                device.click(*b.rect.center)
                sleep(1.0)
                continue
        elif (b := tpl("InProduce.HifInterval.ButtonEnd").find(img)) is not None:
            logger.info("轮间结束：点「終了」")
            device.click(*b.rect.center)
            sleep(1.0)
            continue
        elif not tpl("InProduce.HifInterval.Title").exists(img):
            logger.info("已离开轮间")
            return True
        device.click(*BLANK_TAP)
        sleep(0.3)
    return False
