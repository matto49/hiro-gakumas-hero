# 来源：digital-hiro src/hiro/produce/formation_writer.py（7744207）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""开局时把编成位改成方案（hiro.brain.formation_plan）：第 2 步支援卡、第 3 步回忆，就地修正对不上的格子。

只在 KAA 已经切到方案的编成号（建议 20 号专用位）之后、点「次へ」之前运行；不进菜单、不碰其他编成位。开关
HIRO_FORMATION_AUTOFIX=1 才启用（默认关，2026-11-06：执行步骤还没实机验证）。

规则：
- 只改必须改的格子：已经在编成里的方案卡不动；先换「认出来但不在方案里」的格子，再用认不出的格子。
- 每一步都核对：列表里找到目标、点中后才按「決定」；回到第 2 / 3 步后重读，那一格必须变成目标。任何一步不对就
  キャンセル、抛 FormationWriteError（kaa_patches 转成 FormationMismatch，不开局）。
- 回忆按方案里记录的库存位置（cell = [行, 列]，扫描时的排序）滚过去，点开读详情、和库存记录比对（memory_picker.entry_key），
  附近 SEARCH_ROWS 行内找不到就放弃，不盲选。
所有设备操作（shoot / click / swipe / ocr / sleep）可注入，离线测试用假设备。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


class FormationWriteError(RuntimeError):
    """编成修正中途出错（找不到卡 / 页面不对 / 改完核对不过）：已尽量退回原页面，不开局。"""


# ------------------------------------------------------------------ 修正计划（纯函数）


def fix_plan(current: list[str | None], want: list[str]) -> list[tuple[int, str]]:
    """当前各格（卡键；None = 认不出 / 空）+ 方案要的卡 → [(格子序号, 要放的卡)]，改动最少。
    已在编成里的方案卡不动；缺的卡先放进「认出来但不在方案里」的格子，再放进认不出的格子。格子不够时报错。"""
    missing = [w for w in want if w not in current]
    if not missing:
        return []
    wrong = [i for i, c in enumerate(current) if c is not None and c not in want]
    unknown = [i for i, c in enumerate(current) if c is None]
    slots = wrong + unknown
    if len(slots) < len(missing):
        raise FormationWriteError(f"编成格子不够放方案的卡：缺 {missing}，可换的格子 {slots}")
    return list(zip(slots, missing))


# ------------------------------------------------------------------ 设备注入


@dataclass
class Device:
    shoot: Callable
    click: Callable
    swipe: Callable
    ocr: Callable | None = None
    sleep: Callable = time.sleep
    calib: Callable | None = None  # calib(img, tag)：存帧（实机排查用）

    def snap(self, img, tag: str) -> None:
        if self.calib is not None and img is not None:
            try:
                self.calib(img, tag)
            except Exception:  # noqa: BLE001  存帧失败不影响流程
                pass


def _wait(dev: Device, read, ok, tries: int = 12, delay: float = 0.4):
    """反复截图直到 ok(read(img))，返回 (img, 读数)；超时返回 (img, None)。"""
    img = None
    for _ in range(tries):
        img = dev.shoot()
        r = read(img)
        if r is not None and ok(r):
            return img, r
        dev.sleep(delay)
    return img, None


# ------------------------------------------------------------------ 支援卡（第 2 步）

LIST_SWIPE = (360, 960, 360, 685)  # 选择列表上滑约 2 行（同 tools.support_inventory，实机扫描验证过）
LIST_SWIPE_DURATION, LIST_SETTLE = 1.0, 1.2
LIST_MAX_SCREENS = 12
LIST_TOP_SWIPES = 6


def _list_to_top(dev: Device, read_list) -> dict:
    img = dev.shoot()
    r = read_list(img)
    if r is None:
        raise FormationWriteError("没打开支援卡选择列表")
    for _ in range(3):  # 回到第 1 页
        if (r.get("page") or 1) <= 1:
            break
        dev.click(*r["controls"]["prev"])
        dev.sleep(1.5)
        r = read_list(dev.shoot()) or r
    for _ in range(LIST_TOP_SWIPES):
        dev.swipe(LIST_SWIPE[0], LIST_SWIPE[3], LIST_SWIPE[2], LIST_SWIPE[1], LIST_SWIPE_DURATION)
    dev.sleep(0.8)
    return read_list(dev.shoot()) or r


def find_support_tile(cards: list[dict], target: str) -> dict | None:
    """一屏列表读数里找目标卡（完整显示、不被盖住的那张）。"""
    return next((c for c in cards if c.get("support_card_id") == target and not c.get("partial") and c.get("center")), None)


def pick_support(dev: Device, target: str, *, read_list) -> None:
    """在已打开的支援卡选择列表里找到 target、点中、按「決定」。找不到就キャンセル并报错。"""
    r = _list_to_top(dev, read_list)
    controls = r["controls"]
    pages = r.get("pages") or 1
    for page in range(1, pages + 1):
        seen_screens, last = 0, None
        while seen_screens < LIST_MAX_SCREENS:
            img = dev.shoot()
            r = read_list(img)
            if r is None:
                raise FormationWriteError("支援卡选择列表中途消失")
            tile = find_support_tile(r["cards"], target)
            if tile is not None:
                dev.click(*tile["center"])
                dev.sleep(0.8)
                img2 = dev.shoot()
                r2 = read_list(img2)
                hit = find_support_tile((r2 or {}).get("cards") or [], target)
                if hit is None or not hit.get("selected"):
                    dev.snap(img2, f"support-pick-unconfirmed-{target}")
                    dev.click(*controls["cancel"])
                    raise FormationWriteError(f"点了 {target} 但列表里没显示为选中")
                dev.snap(img2, f"support-picked-{target}")
                dev.click(*controls["decide"])
                dev.sleep(1.5)
                return
            ids = [c.get("support_card_id") for c in r["cards"]]
            if ids == last:
                break  # 到底
            last = ids
            dev.swipe(*LIST_SWIPE, LIST_SWIPE_DURATION)
            dev.sleep(LIST_SETTLE)
            seen_screens += 1
        if page < pages:
            dev.click(*controls["next"])
            dev.sleep(1.8)
    dev.click(*controls["cancel"])
    dev.sleep(1.0)
    raise FormationWriteError(f"支援卡选择列表里没找到 {target}（库存里没有，或认不出）")


def fix_support(dev: Device, plan: dict, *, read_formation, read_list) -> list[dict]:
    """第 2 步：读 5 格，和方案不一致的格子逐个点开、选方案的卡、決定，再核对。返回改动记录。"""
    want = [o["support_card_id"] for o in (plan.get("support") or {}).get("own") or []]
    if not want:
        return []
    img = dev.shoot()
    page = read_formation(img)
    if page is None or page.get("view") != "step2":
        raise FormationWriteError("不在第 2 步支援卡页面")
    current = [s.get("support_card_id") for s in page["slots"]]
    moves = fix_plan(current, want)
    done = []
    for slot, target in moves:
        tile = page["slots"][slot]
        logger.info("编成修正：支援卡第 %d 格 %s → %s", slot + 1, tile.get("name") or tile.get("support_card_id") or "认不出", target)
        dev.click(*tile["center"])
        dev.sleep(1.5)
        pick_support(dev, target, read_list=read_list)
        img, page2 = _wait(dev, read_formation, lambda p: p.get("view") == "step2")
        if page2 is None:
            raise FormationWriteError("決定后没回到第 2 步")
        got = page2["slots"][slot].get("support_card_id")
        if got != target:
            dev.snap(img, f"support-verify-fail-{slot}")
            raise FormationWriteError(f"支援卡第 {slot + 1} 格改完是 {got}，不是 {target}")
        page = page2
        done.append({"slot": slot, "from": current[slot], "to": target})
    return done


# ------------------------------------------------------------------ 回忆（第 3 步）

# 第 3 步四格回忆的插图（点开进回忆选择页）：720x1280，顺序 左上、右上、左下、右下（11-04 实机帧；技能卡小图标在其右侧）
STEP3_SLOTS = ((152, 770), (478, 770), (152, 901), (478, 901))
SEARCH_ROWS = 3  # 目标行上下各找几行
PICKER_DECIDE = (497, 1157)


def memory_key(e: dict) -> str:
    from hiro.produce.memory_picker import entry_key

    return entry_key(e)


def pick_memory(picker, target: dict, *, decide=PICKER_DECIDE) -> None:
    """在已打开的回忆选择页里按库存位置找到 target（cell = [行, 列]、key = entry_key），核对后「決定」。"""
    from hiro.produce.memory_picker import ROW_PITCH, page_rows, to_top

    row, col = target["cell"]
    img = to_top(picker)
    lead = max(0, row - SEARCH_ROWS)  # 目标行停在屏幕中间：滚动有误差时上下都还看得到
    if lead > 0:
        img = picker.scroll(px=int(lead * ROW_PITCH))
    rows = page_rows(img)
    tried = 0
    for r in rows[: SEARCH_ROWS * 2 + 1]:
        cell = next((c for c in r if c["col"] == col), None)
        if cell is None:
            continue
        picker.select(cell)
        got = picker.read_selected()
        tried += 1
        if memory_key(got) == target["key"]:
            picker.click(*decide)
            picker.sleep(1.5)
            return
    picker.cancel()
    raise FormationWriteError(f"回忆选择页第 {row} 行附近没找到方案里的回忆（试了 {tried} 格），库存可能过期，请重扫")


def fix_memory(dev: Device, plan: dict, *, read_icons, make_picker) -> list[dict]:
    """第 3 步：认四格回忆的卡，和方案不一致的格子逐个点开、按库存位置选那份回忆、決定，再认一次核对。"""
    from hiro.vision.memory_icons import card_key

    chosen = (plan.get("memory") or {}).get("chosen") or []
    targets = {card_key(c["card_id"]): c for c in chosen if c.get("card_id")}
    if not targets:
        return []
    missing_pos = [k for k, c in targets.items() if not c.get("cell") or not c.get("key")]
    img = dev.shoot()
    reads = read_icons(img)
    current = [r["card"] if r.get("known") else None for r in reads]
    moves = fix_plan(current, list(targets))
    if not moves:
        return []
    if any(k in missing_pos for _, k in moves):
        raise FormationWriteError("方案里的回忆没有库存位置（旧版方案），请重扫回忆库存后重新生成方案")
    done = []
    for slot, key in moves:
        logger.info("编成修正：回忆第 %d 格 %s → %s", slot + 1, current[slot] or "认不出", key)
        dev.click(*STEP3_SLOTS[slot])
        dev.sleep(1.5)
        picker = make_picker()
        from hiro.produce.memory_picker import picker_open

        if not picker_open(picker.ocr(picker.shoot())):
            raise FormationWriteError("点回忆格没打开回忆选择页")
        pick_memory(picker, targets[key])
        img = None
        for _ in range(10):
            img = dev.shoot()
            reads = read_icons(img)
            if any(r.get("known") for r in reads):
                break
            dev.sleep(0.5)
        if not reads[slot].get("known") or reads[slot]["card"] != key:
            dev.snap(img, f"memory-verify-fail-{slot}")
            raise FormationWriteError(f"回忆第 {slot + 1} 格改完认成 {reads[slot].get('card')}，不是 {key}")
        done.append({"slot": slot, "from": current[slot], "to": key})
    return done
