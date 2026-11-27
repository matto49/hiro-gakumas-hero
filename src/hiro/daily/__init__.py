"""日常任务（去 KAA）：KAA tasks/daily/ 里不花钱的小项，跑在 hiro.runtime.Runtime 上，不 import KAA / kotonebot。

    from hiro.daily import TASKS, make_runtime
    rt = make_runtime(device)                 # device：hiro.devices 的原始设备（已 connect）
    print(TASKS["presents"](rt))              # 每项返回一句结果；超时 / 停机抛 ResumeBlocked、TaskTimeout

每一拍过暂停检查点和中断层（通信错误只点リトライ、危险弹窗只点キャンセル、维护停机……，见 hiro.runtime.interrupts），
再加两个日常专用的：说明框 / ジュエル框看守（common.Popup）和单项超时（common.Deadline）。点击都过危险点击拦截。
模板是 KAA 的同一批（tools/assets/ui_from_kaa.py 搬来，在 assets/pack/ui）。

KAA tasks/daily 里不花钱的 6 项都已迁：presents、activity_funds、assignment、contest、mission_reward、club_reward（2026-11-25 去 KAA 第 2 批迁完后两项）。
11-26 加两项（用マニー / コイン，不花ジュエル）：shop（デイリー交換所，HIRO_SHOP / HIRO_SHOP_ITEMS）、coin_gacha（コインガシャ，HIRO_COIN_GACHA）；
放在 mission_reward 前面，买完 / 抽完的日常任务奖励一起领。竞赛挑哪个对手：HIRO_CONTEST_PICK。
"""

from __future__ import annotations

from hiro.daily import activity_funds, assignment, club_reward, coin_gacha, contest, mission_reward, presents, shop
from hiro.daily.common import Deadline, Popup, TaskTimeout
from hiro.runtime import UNKNOWN, Runtime
from hiro.runtime.interrupts import DEFAULT

TASKS = {
    "activity_funds": activity_funds.run,
    "presents": presents.run,
    "assignment": assignment.run,
    "contest": contest.run,
    "shop": shop.run,
    "coin_gacha": coin_gacha.run,
    "mission_reward": mission_reward.run,
    "club_reward": club_reward.run,
}


def make_runtime(device, *, popup: Popup | None = None, deadline: Deadline | None = None, **kw) -> Runtime:
    """日常用的 Runtime：不判页（只用 drive / wait_until），中断层 = 通用中断 + Popup（+ Deadline）。"""
    extra = tuple(x for x in (popup if popup is not None else Popup(), deadline) if x is not None)
    return Runtime(device, lambda img: UNKNOWN, interrupts=(*DEFAULT, *extra), **kw)


__all__ = ["TASKS", "Deadline", "Popup", "TaskTimeout", "make_runtime"]
