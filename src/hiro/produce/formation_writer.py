# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""把编成位改成方案（hiro.brain.formation_plan）：支援卡、回忆，就地修正对不上的格子。

两个用处：开局第 2 步支援卡（HIRO_FORMATION_AUTOFIX=1 才启用，在 KAA 切到方案的编成号之后、点「次へ」之前）；
编成菜单（hiro.produce.formation_menu，tools/write_formation.py）里写支援卡和回忆。开局第 3 步不再写回忆、只核对
（协调 11-17：第 3 步选择页只露约 3 行、排序和菜单不同，按库存行号找不准）。不碰方案编成号以外的编成位。

规则：
- 只改必须改的格子：已经在编成里的方案卡不动；先换「认出来但不在方案里」的格子，再用认不出的格子。
- 每一步都核对：列表里找到目标、点中后才按「決定」；回到第 2 / 3 步后重读，那一格必须变成目标。任何一步不对就
  キャンセル、抛 FormationWriteError（kaa_patches 转成 FormationMismatch，不开局）。
- 回忆按方案里记录的库存位置（cell = [行, 列]，扫描时的排序）滚过去，点开读详情、和库存记录比对（memory_picker.entry_key），
  附近 SEARCH_ROWS 行内找不到就放弃，不盲选。
所有设备操作（shoot / click / swipe / ocr / sleep）可注入，离线测试用假设备。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field  # noqa: F401

from hiro.devices.active import sleep as _sleep

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
    sleep: Callable = _sleep
    calib: Callable | None = None  # calib(img, tag)：存帧（实机排查用）

    def snap(self, img, tag: str) -> None:
        if self.calib is not None and img is not None:
            try:  # noqa: SIM105
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
    return next(
        (c for c in cards if c.get("support_card_id") == target and not c.get("partial") and c.get("center")), None
    )


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


SETTLE_READS, SETTLE_GAP_S = 8, 0.5


def _settled_step2(dev: Device, read_formation, view: str = "step2"):
    """第 2 步 5 格读到连续两次一样才算数（刚从别的编成号翻过来时卡片还在动画里，第 1 格常读成认不出，
    11-16 Mac 实机：于是去列表里找一张已经在编成里的卡，找不到就不开局）。读不到稳定结果时用最后一次。"""
    prev, page = None, None
    for _ in range(SETTLE_READS):
        page = read_formation(dev.shoot())
        if page is not None and page.get("view") == view:
            cur = [s.get("support_card_id") for s in page["slots"]]
            if cur == prev:
                return page
            prev = cur
        dev.sleep(SETTLE_GAP_S)
    return page if page is not None and page.get("view") == view else None


SLOT_OPEN_TRIES = 3  # 点支援卡格没打开选择列表时再点几次（DMM 后台点击偶尔丢，11-21）


def _open_support_list(dev: Device, center, read_formation, read_list, view: str) -> None:
    """点支援卡格打开选择列表。没打开、而且画面还是编成页（点击丢了）才再点；已经不在编成页就不再点（免得点进列表里）。"""
    for attempt in range(SLOT_OPEN_TRIES):
        dev.click(*center)
        img, r = _wait(dev, read_list, lambda r: True, tries=6, delay=0.5)
        if r is not None:
            return
        page = read_formation(img)
        if page is None or page.get("view") != view:
            return  # 不在编成页了：交给 pick_support 再判（读不到列表会报错停下）
        logger.info("点支援卡格没打开选择列表，再点一次（第 %d 次）", attempt + 2)


def fix_support(dev: Device, plan: dict, *, read_formation, read_list, view: str = "step2") -> list[dict]:
    """第 2 步：读 5 格，和方案不一致的格子逐个点开、选方案的卡、決定，再核对。返回改动记录。
    view：read_formation 的页面类型，开局第 2 步是 step2，编成菜单（hiro.produce.formation_menu）是 formation。"""
    want = [o["support_card_id"] for o in (plan.get("support") or {}).get("own") or []]
    if not want:
        return []
    page = _settled_step2(dev, read_formation, view)
    if page is None:
        raise FormationWriteError("不在第 2 步支援卡页面" if view == "step2" else "不在支援卡编成页")
    current = [s.get("support_card_id") for s in page["slots"]]
    moves = fix_plan(current, want)
    done = []
    for slot, target in moves:
        tile = page["slots"][slot]
        logger.info(
            "编成修正：支援卡第 %d 格 %s → %s",
            slot + 1,
            tile.get("name") or tile.get("support_card_id") or "认不出",
            target,
        )
        _open_support_list(dev, tile["center"], read_formation, read_list, view)
        pick_support(dev, target, read_list=read_list)
        img, page2 = _wait(dev, read_formation, lambda p: p.get("view") == view)
        if page2 is None:
            raise FormationWriteError("決定后没回到第 2 步" if view == "step2" else "決定后没回到支援卡编成页")
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
SEARCH_ROWS = 8  # 目标行上下各找几行（库存行号可能偏：扫描漏行 / 重扫后列表变了，11-16 账号 2 偏了十几行）
ABILITY_SIMILAR = (
    0.85  # 能力文字（OCR 原文）相似度达到这个就算同一份；卡 / 强化 / 时机一致但能力不像的，整窗口找完没有更好的才用
)
PICKER_OPEN_TRIES = 4  # 点回忆格后最多看几次选择页开没开（每次 1.5 秒，中途再点一次）
PICKER_DECIDE = (497, 1157)


def memory_key(e: dict) -> str:
    from hiro.produce.memory_picker import entry_key

    return entry_key(e)


def _core(key: str):
    """内容键里稳定的部分：卡、强化、时机。属性不比：选择页顶部显示的是「当前编成 + 这份回忆」的总加成，随编成变
    （11-16 账号 2：扫描时和开局时读到的属性对不上）；能力文字是 OCR 原文，常差一两个字，另外按相似度比。"""
    try:
        return json.loads(key)[:3]
    except (TypeError, ValueError):
        return None


def _abilities_text(key: str) -> str:
    try:
        ab = json.loads(key)[7] or []
    except (TypeError, ValueError, IndexError):
        return ""
    return "".join(a.get("text", "") if isinstance(a, dict) else str(a) for a in ab)


def _core_match(got, want) -> bool:
    """卡、强化一致；入手时机两边都读到了才比（11-20 MuMu：重读时时机横幅没读出来是 None，仕切り直し 在库存那一格、能力文字 100% 一样，
    却因为时机 None ≠ mid 两遍都没认，槽位写不进去）。卡没读出来不算。"""
    if got is None or want is None or got[0] is None or got[0] != want[0] or got[1] != want[1]:
        return False
    return got[2] is None or want[2] is None or got[2] == want[2]


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def pick_memory(picker, target: dict, *, decide=PICKER_DECIDE) -> None:
    """在已打开的回忆选择页里按库存位置找到 target（cell = [行, 列]、key = entry_key），核对后「決定」。
    从顶往下按精确行号走（滑动不按像素估算，扫描库存时也是这样数的）。先只读目标格，不中再读目标行上下 SEARCH_ROWS 行里同一列的格子：
    内容键完全一样、或卡 / 强化 / 时机一致且能力文字相似（≥ ABILITY_SIMILAR）就选；一个都没有时，再走一遍选第一份卡 / 强化 / 时机一致的。"""
    from hiro.produce.memory_picker import iter_rows

    row, col = target["cell"]
    want_core, want_ab = _core(target["key"]), _abilities_text(target["key"])
    tried = 0

    def walk(accept, rows) -> bool:
        """从顶往下按精确行号走（memory_picker.iter_rows），只点开 rows 里这些行同一列的格子读详情（滑动只对齐、不读详情）。"""
        nonlocal tried
        last = max(rows)
        for g, r in iter_rows(picker):
            if g > last:
                return False
            if g not in rows:
                continue
            cell = next((c for c in r if c["col"] == col), None)
            if cell is None:
                continue
            picker.select(cell)
            got = memory_key(picker.read_selected())
            tried += 1
            if accept(got):
                picker.click(*decide)
                picker.sleep(1.5)
                return True
        return False

    def strong(key: str) -> bool:
        return key == target["key"] or (
            _core_match(_core(key), want_core) and _similar(_abilities_text(key), want_ab) >= ABILITY_SIMILAR
        )

    # 先只读库存记的那一格（刚扫过的库存基本一次就中；每读一格详情约 20 秒，11-20 MuMu 从窗口顶读起一个槽位要 4 分钟以上），
    # 不中再读上下 SEARCH_ROWS 行；再不中，选第一份卡 / 强化 / 时机一致的
    window = set(range(max(0, row - SEARCH_ROWS), row + SEARCH_ROWS + 1))
    if walk(strong, {row}) or walk(strong, window - {row}):
        return
    if want_core is not None and walk(lambda key: _core(key) == want_core, window):
        logger.warning("回忆选择页：第 %d 行附近没有能力文字也对得上的，选了第一份卡 / 强化 / 时机一致的", row)
        return
    picker.cancel()
    raise FormationWriteError(
        f"回忆选择页第 {row} 行上下 {SEARCH_ROWS} 行里没找到方案里的回忆（试了 {tried} 格），库存可能过期，请重扫"
    )


def fix_memory(dev: Device, plan: dict, *, read_icons, make_picker, slots=STEP3_SLOTS) -> list[dict]:
    """第 3 步：认四格回忆的卡，和方案不一致的格子逐个点开、按库存位置选那份回忆、決定，再认一次核对。
    slots：点开选择页的四个位置（编成菜单用 formation_menu.MEMORY_TILES）。"""
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
        from hiro.produce.memory_picker import picker_open

        picker, opened = make_picker(), False
        for attempt in range(
            PICKER_OPEN_TRIES
        ):  # 页面打开有时慢（11-16 Mac 实机：1.5 秒后还没开 → 不开局）：多等、没开再点一次
            if attempt == 0 or attempt == PICKER_OPEN_TRIES // 2:
                dev.click(*slots[slot])
            dev.sleep(1.5)
            if picker_open(picker.ocr(picker.shoot())):
                opened = True
                break
        if not opened:
            raise FormationWriteError("点回忆格没打开回忆选择页")
        from hiro.produce.memory_picker import PickerClosed

        try:
            pick_memory(picker, targets[key])
        except PickerClosed as e:  # 找的途中选择页关了（点成了「決定」）：这一格可能被换成了别的回忆，停下交给人
            raise FormationWriteError(
                f"回忆第 {slot + 1} 格：找方案里的回忆时选择页关了，这一格可能被换成了别的回忆：{e}"
            ) from e
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
