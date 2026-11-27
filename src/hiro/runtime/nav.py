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


_TITLE = object()  # goto_home 的 step 认出标题页时返回它（交给 finish.leave_title，不在 drive 的计时里走）


def at_title_screen(f) -> bool:
    """游戏刚打开的标题页（背景是视频，下面「TAP TO START」一明一暗地闪）。按不闪的字认：左上「プロデューサーID: タップで表示」
    + 「Ver.」/「メニュー」等（finish.at_title）；要求「タップで表示」或读到 TAP TO START，免得把プロフィール页（也有プロデューサーID）认成标题页。"""
    from hiro.runtime.finish import at_title

    texts = [t.text for t in f.texts]
    if not at_title(texts):
        return False
    return any("タップで表示" in t for t in texts) or any("TAPTOSTART" in t.upper().replace(" ", "") for t in texts)


def goto_home(rt, timeout: float = HOME_TIMEOUT):
    """回首页：点首页 / 工具栏首页 / ✕，直到认出首页（同 KAA goto_home）；偶像之路结果页先点「一覧に戻る」。返回首页那一帧。
    游戏停在标题页（刚打开游戏就发起日常、扫描等）：走续跑同一条「标题页 → 首页」的路（finish.leave_title：点 TAP TO START，
    闪到暗处读不到就点上方空白；数据下载确认、登录奖励 / 公告点掉；要「データ連携」就停下交给人）。"""

    def step(f):
        if at_home(f):
            return f
        if at_title_screen(f):
            return _TITLE
        if (m := home_button(f)) is not None:
            tap_match(rt, m)
            return None
        if (b := tower_back(f)) is not None:
            rt.device.tap(*b.rect.center)
            return None
        if (xy := _photo_step(f)) is not None:
            rt.device.tap(*xy)
        return None

    for _ in range(2):
        r = rt.drive(step, timeout, "回首页")
        if r is not _TITLE:
            return r
        from hiro.runtime.finish import leave_title

        leave_title(rt)  # 回首页可能要下载数据，单独计时；回不到首页抛 ResumeBlocked
    r = rt.drive(step, timeout, "回首页")
    if r is _TITLE:  # 离开标题页两次都又回到标题页：停下，别把内部记号当首页帧交出去
        from hiro.loop.errors import ResumeBlocked

        raise ResumeBlocked("回不到首页：点了两次还停在游戏标题页")
    return r


def _photo_step(f):
    """上一局结算停在选回忆照片页 / 保存确认框时（N50：G3 第 3 轮收尾没走完，下一个任务回首页 60 秒失败）：
    用已选中的那张（缺省第一张）点「次へ」，确认框点「決定」。玩家设了自己选照片（HIRO_PHOTO_PICK=stop / HIRO_LIVE=watch|pick）就不替他点。"""
    from hiro.runtime import photo_pick

    texts = [t.text.replace(" ", "") for t in f.texts]
    if photo_pick.mode() == "stop":
        return None
    if photo_pick.is_photo_confirm(texts):
        ok = next((t for t in f.texts if "決定" in t.text and t.rect.center.y > 1000), None)
        return ok.rect.center if ok is not None else (500, 1157)
    if photo_pick.is_photo_page(texts):
        nxt = next((t for t in f.texts if t.text.replace(" ", "") == "次へ"), None)
        return nxt.rect.center if nxt is not None else (360, 1157)
    return None


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
