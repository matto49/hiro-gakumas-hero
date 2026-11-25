"""通用导航（新主循环、开局 / 续跑、日常任务共用）：从任意页回首页、按模板找 / 点。不 import KAA / kotonebot。"""

from __future__ import annotations

import logging

from hiro.vision.ui_templates import tpl

logger = logging.getLogger(__name__)

HOME_TIMEOUT = 60.0
HOME_BUTTONS = ("Common.ButtonHome", "Common.ButtonToolbarHome", "Common.ButtonIconClose")


def find(f, name: str, **kw):
    """模板在这一帧的命中（ui_templates.Match）或 None。测试替身换这一个函数。"""
    return tpl(name).find(f.img, **kw)


def tap_match(rt, m) -> None:
    rt.device.tap(*m.rect.center)


def at_home(f) -> bool:
    """首页（底栏「ホーム」高亮，同 hiro.vision.classify.at_home）。"""
    return find(f, "Daily.ButtonHomeCurrent") is not None


TOWER_BACK = "一覧に戻る"  # 偶像之路结果页 / 不合格名次页底部：回层选择页（那里有左下首页按钮）


def tower_back(f):
    """偶像之路结果页 / 不合格名次页的「一覧に戻る」按钮（底部）；没有返回 None。这两页没有首页按钮（11-17 Mac：
    run_hif new 起跑时停在第 19 层 PERFECT 结果页，结算收尾一直点空白，180 秒超时）。旁边的「再挑戦」绝不点。"""
    return next((t for t in f.texts if TOWER_BACK in t.text.replace(" ", "") and t.rect.center.y > 1000), None)


DARK_TOOLBAR_HOME = ("Common.ButtonToolbarHome", 0.75, (0, 1140, 200, 140))
# 竞赛「ユニット編成」这类深色工具栏（深底白图标）：同一模板只有 0.79（默认线 0.8），只在左下角放宽到 0.75
# （11-25 账号 3：goto_home 60 秒回不去；51 张培育中帧在这个区域 0.75 下没有误命中）


def home_button(f):
    """这一帧能点回首页的按钮（ui_templates.Match）；没有 None。先按 HOME_BUTTONS 默认阈值，再试深色工具栏。"""
    for name in HOME_BUTTONS:
        if (m := find(f, name)) is not None:
            return m
    name, threshold, region = DARK_TOOLBAR_HOME
    return find(f, name, threshold=threshold, region=region)


def goto_home(rt, timeout: float = HOME_TIMEOUT):
    """回首页：点首页 / 工具栏首页 / ✕，直到认出首页（同 KAA goto_home）；偶像之路结果页先点「一覧に戻る」。返回首页那一帧。"""

    def step(f):
        if at_home(f):
            return f
        if (m := home_button(f)) is not None:
            tap_match(rt, m)
            return None
        if (b := tower_back(f)) is not None:
            rt.device.tap(*b.rect.center)
        return None

    return rt.drive(step, timeout, "回首页")


def back_home(device, timeout: float = HOME_TIMEOUT) -> bool:
    """工具脚本（库存扫描等）用：给设备（hiro.devices 原始设备，或 hiro.tool_device 包过的），走 goto_home 回首页。
    回到首页返回 True；回不去记一条 WARNING、返回 False，不抛异常（让调用方自己说清楚停在哪）。
    G1-F1（11-26 Mac 小白验收）：扫回忆读完停在「メモリー編成」页，下一个扫偶像卡开头盲点首页底栏，12 秒就失败。"""
    from hiro.daily import Popup, make_runtime
    from hiro.devices.base import DeviceError
    from hiro.runtime.loop import ResumeBlocked

    raw = getattr(device, "_device", device)  # hiro.tool_device.ToolDevice（GuardedDevice）里的原始设备
    rt = make_runtime(raw, popup=Popup())
    try:
        goto_home(rt, timeout)
    except (ResumeBlocked, DeviceError) as e:  # 收尾时设备断了也不能盖掉调用方原来的错误（#108 review）
        logger.warning("回首页没成功：%s", e)
        return False
    return True
