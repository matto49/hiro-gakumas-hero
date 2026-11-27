"""商店的コインガシャ（用各自的コイン抽，不花ジュエル）：日常任务「コインガシャを引く」用。KAA tasks/daily/capsule_toys 的思路，按 OCR 文字做。

HIRO_COIN_GACHA：要抽哪几个、各几次，写成 `friend:1,sense:2`（friend フレンド / sense センス / logic ロジック / anomaly アノマリー，0 = 不抽）；
`auto` = 按 フレンド → センス → ロジック → アノマリー 的顺序抽 1 次（第一个币够的）；`off`（缺省：花币的事默认不做，开局页的日常选项会显式传）= 不抽。

11-25 DMM 实测：コインガシャ页从上到下四个池，每个池标题「フレンドガシャ（毎月1日 5時00分更新）」下面约 80 像素是「引く 1回10」；
点了弹「コインガシャ確認」（「〇〇コインを消費して」），「ガシャ回数: N回」缺省是能抽的最多次，「－」最少到 0，
「－」「＋」在回数那行下面约 80 像素的左右两头；「決定」后是 Result 页，点「閉じる」回来。
"""

from __future__ import annotations

import logging
import os
import re

from hiro.daily import common
from hiro.daily.shop import tap_word, text_at
from hiro.runtime import nav

logger = logging.getLogger(__name__)

POOLS = {"friend": "フレンド", "sense": "センス", "logic": "ロジック", "anomaly": "アノマリー"}
NAMES = {"friend": "友情", "sense": "感性", "logic": "理性", "anomaly": "非凡"}  # 结果里给人看的
TIMEOUT = 40.0
SWIPE = (600, 950, 600, 300)
SWIPE_S = 1.5
MAX_COUNT_TAPS = 30
MAX_TIMES = 10  # 每个池一次最多抽几次（填得很大时会一直空点「＋」到超时，#122 review）；界面也限这个数


def plan(value: str | None = None) -> list[tuple[str, int]] | str:
    """[(池, 次数)]；auto 返回 "auto"；off 返回 []。value 不给就读 HIRO_COIN_GACHA（确认摘要用同一套解析）。"""
    v = (value if value is not None else os.environ.get("HIRO_COIN_GACHA") or "off").strip().lower() or "off"
    if v == "off":
        return []
    if v == "auto":
        return "auto"
    total: dict[str, int] = {}
    for part in v.split(","):
        k, colon, n = part.strip().partition(":")
        if k not in POOLS:
            continue
        n = n.strip()
        if not colon:
            times = 1  # 只写池名 = 1 次
        elif n.isascii() and n.isdigit():
            times = int(n)
        else:  # friend:-1 / friend:abc / friend: / friend:²：写错了就不抽这个池（不当成 1 次，CodeRabbit #122 / #197）
            logger.warning("HIRO_COIN_GACHA 里「%s」的次数认不出，这个池不抽", part.strip())
            continue
        total[k] = min(total.get(k, 0) + times, MAX_TIMES)  # 同一个池写了两遍：合起来也不超过上限（CodeRabbit #122）
    return [(k, n) for k, n in total.items() if n > 0]


def _gacha_title(f, pool: str):
    """池的标题（OCR 常把「ガシャ」读成「ガシヤ」）。"""
    for t in f.texts:
        x = t.text.replace("ヤ", "ャ")
        if POOLS[pool] + "ガシャ" in x and t.rect.center.x < 400:
            return t
    return None


def goto_coin_gacha(rt) -> bool:
    nav.goto_home(rt)

    def step(f):
        if text_at(f, "コインガシャ", y_max=80) is not None:
            return True
        if (t := text_at(f, "コインガシャ", y_min=760, y_max=880)) is not None and text_at(
            f, "ショップ", y_max=80
        ) is not None:
            rt.device.tap(*t.rect.center)
            rt.sleep(2.5)
            return None
        if nav.at_home(f) and (t := text_at(f, "ショップ", y_min=940, y_max=1010, x_min=500)) is not None:
            rt.device.tap(t.rect.center.x + 40, t.rect.center.y - 50)
            rt.sleep(2.5)
        return None

    return bool(rt.drive(step, TIMEOUT, "进硬币扭蛋页", every=0.8))


def count(f) -> int | None:
    """确认框里「ガシャ回数: N回」的 N。SIMD OCR 有时把「0回」整个漏掉、只剩「ガシャ回数:」：那一行没有数字也没有别的字时算 0。"""
    for t in f.texts:
        if "回数" not in t.text:
            continue
        if m := re.search(r"(\d+)\s*回\s*$", t.text):
            return int(m.group(1))
        if re.fullmatch(r".*回数\s*[:：]?\s*", t.text):
            # 标签和「N回」可能分成两个框：同一行右边找（CodeRabbit #198）；那一行右边什么都没有才算 0
            right = [o for o in f.texts if o is not t and abs(o.rect.center.y - t.rect.center.y) < 20
                     and o.rect.center.x > t.rect.center.x]  # fmt: skip
            for o in sorted(right, key=lambda o: o.rect.center.x):
                if m := re.fullmatch(r"\s*(\d+)\s*回\s*", o.text):
                    return int(m.group(1))
            return 0 if not right else None
    return None


def _scan_down(rt, pool: str, f):
    for i in range(5):  # 首屏 + 滑 4 次后的每一帧都看（最后一次滑完那帧原来没看，CodeRabbit #122）
        title = _gacha_title(f, pool)
        if title is not None and title.rect.center.y < 1080:
            return f, title
        if i == 4:
            break
        rt.device.swipe(*SWIPE, duration=SWIPE_S)
        rt.sleep(1.0)
        f = common.frame(rt)
    return f, None


def find_pool(rt, pool: str):
    """滑到能看见这个池的「引く」，返回 (帧, 标题)；找不到 (帧, None)。
    先在当前画面往下找（刚进扭蛋页就在顶上，11-27 用户：不用先往上划两次）；没找到再回到顶重找一遍。"""
    f, title = _scan_down(rt, pool, common.frame(rt))
    if title is not None:
        return f, title
    for _ in range(2):
        rt.device.swipe(SWIPE[0], SWIPE[3], SWIPE[2], SWIPE[1], duration=SWIPE_S)
    rt.sleep(1.0)
    return _scan_down(rt, pool, common.frame(rt))


def _cancel(rt, f) -> None:
    """确认框点「キャンセル」，并确认框真的关了；关不掉抛 DialogStuck（整个日常停下）：框还开着时去点下一个池的「引く」，
    读到的是这个旧框，次数、決定会作用在它上面（CodeRabbit #197）。"""
    tap_word(rt, f, "キャンセル", y_min=1080)
    for _ in range(2):
        streak = [0]

        # 回到扭蛋列表：顶上「コインガシャ」、没有確認框、没有キャンセル，连着两帧（一帧漏读标题不算，CodeRabbit #122）
        def back(g, streak=streak) -> bool:
            ok = (
                text_at(g, "コインガシャ", y_max=80) is not None
                and text_at(g, "コインガシャ確認") is None
                and text_at(g, "キャンセル", y_min=1080) is None
            )
            streak[0] = streak[0] + 1 if ok else 0
            return streak[0] >= 2

        ok, _f = rt.wait_until(back, 6.0, every=0.5)
        if ok:
            return
        tap_word(rt, common.frame(rt), "キャンセル", y_min=1080)
    raise common.DialogStuck("硬币扭蛋：确认框关不掉，日常停下（没抽），请手动关掉")


class DrawStopped(Exception):
    """抽完以后关结果画面超时：已经抽了 n 次。unsure：点了「決定」但还没看到框关掉 / 结果，抽没抽没核实。"""

    def __init__(self, pool: str, n: int, why: str, unsure: bool = False) -> None:
        super().__init__(why)
        self.pool, self.n, self.unsure = pool, n, unsure


_state = {
    "dialog": False,
    "missed": [],
}  # dialog：点了「引く」到确认框确认关掉之间（这时超时不能接着跑下一项）；missed：没找到 / 没弹框的池


def draw(rt, pool: str, times: int) -> int:
    """抽 times 次（币不够就抽能抽的），返回实际抽了几次。没找到池 / 确认框没弹记进 _state["missed"]（不算币不够）。"""
    _, title = find_pool(rt, pool)
    if title is None:
        logger.info("没找到%s扭蛋", NAMES[pool])
        _state["missed"].append(pool)
        return 0
    _state["dialog"] = True
    rt.device.tap(360, title.rect.center.y + 80)  # 标题下面的「引く」
    ok, g = rt.wait_until(lambda f: text_at(f, "コインガシャ確認") is not None, 6.0, every=0.5)
    if not ok:
        _state["missed"].append(pool)
        f = common.frame(rt)
        if text_at(f, "コインガシャ確認") is not None or text_at(f, "キャンセル", y_min=1080) is not None:
            _cancel(rt, f)  # 晚弹出来了（或标题漏读、按钮还在）：关掉，别留给下一个池（CodeRabbit #122 / #138）
        elif text_at(f, "コインガシャ", y_max=80) is None:  # 也认不出扭蛋列表：可能框开着只是字没读到，停下
            raise common.DialogStuck("硬币扭蛋：点了「引く」以后认不出画面，日常停下，请看一下游戏")
        _state["dialog"] = False
        return 0
    n = _draw_in_dialog(rt, pool, times, g)
    _state["dialog"] = False
    return n


def _draw_in_dialog(rt, pool: str, times: int, g) -> int:
    texts = " ".join(t.text for t in g.texts)
    if "ジュエル" in texts or "コインを消費" not in texts.replace(" ", ""):
        logger.warning("扭蛋确认框不是用硬币的，不抽：%s", texts[:120])
        _cancel(rt, g)
        return 0
    line = text_at(g, "回数")
    if line is None:
        _state["missed"].append(pool)  # 读不到次数：不是币不够（CodeRabbit #122 / #138）
        _cancel(rt, g)
        return 0
    y = line.rect.center.y + 80
    minus, plus = (133, y), (586, y)
    rt.device.swipe(500, y, 180, y, duration=0.6)  # 滑块拖到最左（缺省在最右 = 能抽的最多次，可能几十次）
    rt.sleep(0.5)
    n = count(common.frame(rt))
    for _ in range(MAX_COUNT_TAPS):  # 先减到 0（缺省是最多次）
        if n == 0:
            break
        rt.device.tap(*minus)
        rt.sleep(0.3)
        n = count(common.frame(rt))
    if n != 0:
        _state["missed"].append(pool)  # 次数减不到 0（读不准）：不是币不够
        _cancel(rt, common.frame(rt))
        return 0
    for _ in range(times):
        rt.device.tap(*plus)
        rt.sleep(0.3)
    h = common.frame(rt)
    n = count(h) or 0
    if n <= 0 or n > times:  # 币不够一次；或者读数比要的多（OCR 漏读了「回数」后的数字，没减到 0）：不抽，宁可少不可多
        if n > times:
            logger.warning("扭蛋次数读到 %d 次，比要的 %d 次多，不抽", n, times)
            _state["missed"].append(pool)
        _cancel(rt, h)
        return 0
    if not tap_word(rt, h, "決定", y_min=1080):  # 没点上「決定」= 没抽：关掉框，不报次数（CodeRabbit #197）
        _state["missed"].append(pool)
        _cancel(rt, common.frame(rt))
        return 0

    left = [
        False
    ]  # 确认框关掉过（点了「決定」之后进了演出 / 结果页）：没关掉过就是「決定」没生效、没抽（CodeRabbit #138）

    def back(f):
        if text_at(f, "コインガシャ確認") is None and text_at(f, "キャンセル", y_min=1080) is None and f.texts:
            left[0] = True  # 标题漏读、框的按钮还在的帧不算离开确认框（CodeRabbit #122）
        if (
            text_at(f, "コインガシャ", y_max=80) is not None
            and text_at(f, "コインガシャ確認") is None
            and text_at(f, "Result") is None
            and text_at(f, "キャンセル", y_min=1080) is None  # 标题漏读、框还在的帧不算回到列表（CodeRabbit #197）
        ):
            return True
        if not tap_word(rt, f, "閉じる", y_min=900):
            rt.device.tap(360, 1240)  # 抽卡演出：点下面空白跳过
        return None

    from hiro.loop.errors import ResumeBlocked

    try:
        rt.drive(back, 60, "关抽卡结果", every=1.0)
    except (
        ResumeBlocked,
        common.TaskTimeout,
    ) as e:  # 已经抽了：带着次数往外报，结果里要看得到（#122 review；单项超时同样）
        # 框一直没关过：多半「決定」没生效，但也可能是还没来得及看到结果——不记 0 次也不记 n 次，让玩家去游戏里核实（CodeRabbit #138）
        raise DrawStopped(pool, n, str(e), unsure=not left[0]) from e
    logger.info("%s扭蛋 抽了 %d 次", NAMES[pool], n)
    return n


def run(rt) -> str:
    """中途超时（ResumeBlocked，比如抽完关结果画面）时照样报出已经抽了的（#122 review）。"""
    from hiro.loop.errors import ResumeBlocked

    p = plan()
    if p == []:
        return "硬币扭蛋：设置里关了"
    done: list[str] = []
    _state.update(dialog=False, missed=[])
    try:
        if not goto_coin_gacha(rt):
            return "硬币扭蛋：没进到硬币扭蛋页"
        if isinstance(p, str):  # auto
            for pool in POOLS:
                if (n := draw(rt, pool, 1)) > 0:
                    done.append(f"{NAMES[pool]} {n} 次")
                    break
        else:
            for pool, times in p:
                if (n := draw(rt, pool, times)) > 0:
                    done.append(f"{NAMES[pool]} {n} 次")
        nav.goto_home(rt)
    except (ResumeBlocked, DrawStopped, common.TaskTimeout, common.DialogStuck) as e:
        if isinstance(e, DrawStopped) and e.n > 0:
            done.append(
                f"{NAMES[e.pool]}点了抽 {e.n} 次但没看到结果（先到游戏里看看抽没抽、硬币少没少）"
                if e.unsure
                else f"{NAMES[e.pool]} {e.n} 次"
            )
        logger.warning("硬币扭蛋中途停下：%s", e)
        why = str(e) or "超时"
        # 关结果页没关成（DrawStopped）/ 确认框关不掉：画面还停在扭蛋结果或框上，后面的项都不跑（CodeRabbit #197 / #198）
        # 确认框还可能开着时超时（调次数 / 取消中途）也一样（CodeRabbit #122）
        stop_all = isinstance(e, (DrawStopped, common.DialogStuck)) or _state["dialog"]
        raise common.PartialStop(
            "硬币扭蛋：" + ("、".join(done) + "；" if done else "") + f"中途停下：{why}", why, stop_all=stop_all
        ) from e  # 已抽的照样报，这一项算没做完（CodeRabbit #197）
    if done and _state["missed"] and not isinstance(p, str):
        # 指定了几个池、有的没认出来：抽了的照样报，这一项算没做完（CodeRabbit #197 / #198）
        miss = "、".join(NAMES[x] for x in dict.fromkeys(_state["missed"]))
        why = f"{miss}扭蛋的池 / 确认框没认出来"
        raise common.PartialStop("硬币扭蛋：" + "、".join(done) + f"；{why}，可以再试一次", why)
    if done:
        return "硬币扭蛋：" + "、".join(done)
    if _state["missed"]:  # 没认出池 / 确认框没弹：不是币不够（CodeRabbit #122）
        return (
            "硬币扭蛋：没抽（"
            + "、".join(NAMES[x] for x in dict.fromkeys(_state["missed"]))
            + "扭蛋的池 / 确认框没认出来，可以再试一次）"
        )
    return "硬币扭蛋：币不够，没抽"
