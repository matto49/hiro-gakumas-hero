"""在编成菜单（アイドル → プロデュース編成）里按开局编成方案写回忆 / 支援卡，写完回读核对。

开局第 3 步的回忆选择页只露出约 3 行、底下一行被「プラン適正」和排序栏压住，排序也和菜单不一样，按库存行号找不准
（协调 11-17 初采样第 3 次：第 12 行之后对不上）。所以写回忆统一在菜单里做，开局第 2 / 3 步只核对（协调 11-17 定）。
支援卡也照这一套做一份。

流程：主页 → アイドル → プロデュース編成 →（模式不对）プロデュース切替 → 选模式 → 決定 → 切到「サポート編成 / メモリー編成」页签
→ 左右箭头翻到方案的编成号 → 和方案不一致的格子逐个点开、选、決定（formation_writer 的 fix_support / fix_memory，
回忆按库存行号用 memory_picker.iter_rows 精确数行）→ 回读整页核对。

只点这些：アイドル、プロデュース編成、プロデュース切替、模式横幅、切替框的決定 / キャンセル、两个页签、左右箭头、编成格、
选择页里的格子 / 決定 / キャンセル。コピー / リセット / おまかせ / 编成名编辑一律不碰。任何一步对不上就停（FormationWriteError）。
所有设备操作可注入（shoot / click / swipe / sleep / ocr），离线测试用假设备。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from hiro.produce.formation_writer import Device, FormationWriteError, fix_memory, fix_plan, fix_support

import cv2

logger = logging.getLogger(__name__)

MODES = ("hajime", "nia", "hif")
MODE_NAMES = {"hajime": "初", "nia": "N.I.A", "hif": "H.I.F"}
# 切替框里的三条横幅（720x1280，协调 11-17 实机帧 79_switch）：初 / N.I.A / H.I.F 从上到下
MODE_BANNERS = {"hajime": (360, 410), "nia": (360, 600), "hif": (360, 796)}
ARROWS = {"prev": (40, 635), "next": (680, 635)}  # 编成号左右箭头（同 support_screens.ARROWS["formation"]）
IDOL_TAB = (236, 1130)  # 主页底栏「アイドル」（OCR 找不到时用）
# 回忆页：下面 4 张插图（点开进选择页，左上、右上、左下、右下）；上面一排 4 个技能卡大图标（回读用，顺序同格子 01–04）
MEMORY_TILES = ((197, 730), (522, 730), (197, 861), (522, 861))
MEMORY_ICON_X, MEMORY_ICON_Y, MEMORY_ICON_SIZE = (75, 222, 370, 518), 345, 130
MEMORY_EMPTY_STD = 20.0  # 「未設定」空格的灰度标准差很小（同 memory_icons.EMPTY_STD）
MENU_MARGIN = 6.0  # 大图标认卡：第一名领先这么多也算认出（82 号帧 至高のエンターメ+ 分数 22.8 刚过线、领先 9.9）
MENU_SCORE_MAX = 30.0
SET_MAX_TAPS = 20
WAIT_TRIES, WAIT_GAP = 12, 0.5


# ---------------------------------------------------------------- 读页面
def _texts(img, ocr) -> list[tuple[str, tuple]]:
    return [(t.replace(" ", ""), tuple(b)) for t, _c, b in ocr(img)]


def _find(texts, word: str, *, y_min: int = 0, y_max: int = 1280):
    return next((b for t, b in texts if word in t and y_min <= b[1] <= y_max), None)


def _center(b) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def read_mode(texts) -> str | None:
    """左上角的模式标签：「定期公演『初』」/「Hatsuboshi IDOL FESTIVAL」/ N.I.A（「NEXT IDOL AUDITION」等）。"""
    for t, b in texts:
        if b[1] > 90 or b[0] > 320:
            continue
        u = t.upper()
        if "FESTIVAL" in u or "H.I.F" in u or "HIF" in u:
            return "hif"
        if "AUDITION" in u or "N.I.A" in u or "NIA" in u:
            return "nia"
        if "初" in t:
            return "hajime"
    return None


def _orange_ratio(img, b) -> float:
    x1, y1, x2, y2 = (int(v) for v in b)
    hsv = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    return (
        float(((hsv[..., 0] >= 5) & (hsv[..., 0] <= 25) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 180)).mean())
        if hsv.size
        else 0.0
    )


def read_menu(img, texts) -> dict | None:
    """プロデュース編成 页 → {mode, tab: support|memory|None, number, buttons{switch, support_tab, memory_tab}}；不是这一页时 None。
    认页：底部两个页签 + 左侧「N/20」+「おまかせ」都在（选择页 / 切替框盖住时不算）。当前页签按文字橙色比例判。"""
    sup, mem = _find(texts, "サポート編成", y_min=1040), _find(texts, "メモリー編成", y_min=1040)
    num = next((m for t, b in texts if b[0] < 250 and 900 <= b[1] <= 1000 and (m := re.search(r"(\d+)/20$", t))), None)
    if sup is None or mem is None or num is None or _find(texts, "おまかせ", y_min=900, y_max=1000) is None:
        return None
    if _find(texts, "決定", y_min=1100) is not None:
        return None  # 切替框 / 选择页盖在上面
    rs, rm = _orange_ratio(img, sup), _orange_ratio(img, mem)
    tab = "support" if rs > rm and rs > 0.05 else "memory" if rm > rs and rm > 0.05 else None
    switch = _find(texts, "プロデュース切替", y_max=90)
    return {"mode": read_mode(texts), "tab": tab, "number": int(num.group(1)),
            "buttons": {"switch": _center(switch) if switch else None, "support_tab": _center(sup), "memory_tab": _center(mem)}}  # fmt: skip


def read_memory_icons(img) -> list[dict]:
    """回忆页上排 4 个技能卡大图标 → memory_icons.read_icon 的读数（加 slot、empty）。第一名领先 MENU_MARGIN 以上、分数不超过
    MENU_SCORE_MAX 也算认出（大图标比第 3 步的小图标清楚，退路认卡法的分数线按小图标定的，偏严）。"""
    from hiro.vision import memory_icons

    out = []
    for i, x in enumerate(MEMORY_ICON_X):
        y, s = MEMORY_ICON_Y, MEMORY_ICON_SIZE
        icon = img[y : y + s, x : x + s]
        if float(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY).std()) < MEMORY_EMPTY_STD:
            out.append({"slot": i, "card": None, "known": False, "empty": True})
            continue
        r = memory_icons.read_icon(icon)
        if (
            not r["known"]
            and r["card"]
            and r["margin"] is not None
            and r["margin"] >= MENU_MARGIN
            and r["score"] <= MENU_SCORE_MAX
        ):
            r["known"] = True
        out.append(dict(r, slot=i))
    return out


# ---------------------------------------------------------------- 导航
class Menu:
    """编成菜单的导航。dev：formation_writer.Device（要带 ocr：img → [(text, conf, (x1, y1, x2, y2))]）。"""

    def __init__(self, dev: Device):
        if dev.ocr is None:
            raise ValueError("Menu 需要 dev.ocr")
        self.dev = dev

    def look(self):
        img = self.dev.shoot()
        texts = _texts(img, self.dev.ocr)
        return img, texts, read_menu(img, texts)

    def wait_menu(self, ok: Callable[[dict], bool] = lambda m: True, why: str = "编成菜单"):
        for _ in range(WAIT_TRIES):
            img, texts, m = self.look()  # noqa: RUF059
            if m is not None and ok(m):
                return img, m
            self.dev.sleep(WAIT_GAP)
        self.dev.snap(img, f"menu-wait-fail-{why}")
        raise FormationWriteError(f"等不到{why}")

    def open(self) -> dict:
        """停在 プロデュース編成 页就直接用；在主页就点 アイドル →「プロデュース編成・スキルカード」→ 子菜单「プロデュース編成」；
        别的页面停下。11-17 Mac 第一次实机：面板上的「プロデュース編成・スキルカード」(243,832) 也含「プロデュース編成」，
        原来点了它就去等编成页，没点弹出的子菜单里的「プロデュース編成」(198,579)。"""
        img, texts, m = self.look()
        if m is not None:
            return m
        if (
            not (_find(texts, "ホーム", y_min=1050) and _find(texts, "コンテスト", y_min=1050))
            and _find(texts, "プロデュース編成") is None
        ):
            self.dev.snap(img, "menu-not-home")
            raise FormationWriteError("不在主页也不在プロデュース編成页：请先回主页再运行")
        tapped_idol = tapped_panel = False
        for _ in range(WAIT_TRIES * 2):
            if (m := read_menu(img, texts)) is not None:
                return m
            item = next((b for t, b in texts if t == "プロデュース編成"), None)  # 子菜单里的那一项（完全等于这几个字）
            panel = next(
                (b for t, b in texts if "プロデュース編成" in t and t != "プロデュース編成"), None
            )  # 面板按钮「…・スキルカード」
            if item is not None:
                self.dev.click(*_center(item))
                return self.wait_menu(why="プロデュース編成页")[1]
            if panel is not None and not tapped_panel:
                self.dev.click(*_center(panel))  # 打开子菜单
                tapped_panel = True
            elif not tapped_idol and panel is None:
                idol = _find(texts, "アイドル", y_min=1050)
                self.dev.click(*(_center(idol) if idol else IDOL_TAB))
                tapped_idol = True
            self.dev.sleep(WAIT_GAP)
            img, texts, m = self.look()
        self.dev.snap(img, "menu-no-produce-formation")
        raise FormationWriteError("点了アイドル，没进到「プロデュース編成」（面板按钮 / 子菜单都没认出）")

    def ensure_mode(self, mode: str) -> dict:
        """模式不对就 プロデュース切替 → 点横幅 → 決定，回到菜单后模式标签必须变成 mode。"""
        if mode not in MODES:
            raise ValueError(f"mode 要是 {MODES} 之一")
        _, m = self.wait_menu(why="プロデュース編成页")
        if m["mode"] == mode:
            return m
        if m["buttons"]["switch"] is None:
            raise FormationWriteError("没找到「プロデュース切替」")
        logger.info("编成菜单：模式 %s → %s", MODE_NAMES.get(m["mode"], m["mode"]), MODE_NAMES[mode])
        self.dev.click(*m["buttons"]["switch"])
        decide = None
        for _ in range(WAIT_TRIES):
            self.dev.sleep(WAIT_GAP)
            img = self.dev.shoot()
            texts = _texts(img, self.dev.ocr)
            decide = _find(texts, "決定", y_min=1100)
            if decide is not None and _find(texts, "キャンセル", y_min=1100) is not None:
                break
            decide = None
        if decide is None:
            raise FormationWriteError("点了プロデュース切替，没看到切替框")
        self.dev.click(*MODE_BANNERS[mode])
        self.dev.sleep(0.8)
        self.dev.click(*_center(decide))
        return self.wait_menu(lambda x: x["mode"] == mode, why=f"模式切到{MODE_NAMES[mode]}")[1]

    def ensure_tab(self, tab: str) -> dict:
        _, m = self.wait_menu(why="プロデュース編成页")
        if m["tab"] == tab:
            return m
        self.dev.click(*m["buttons"][f"{tab}_tab"])
        return self.wait_menu(lambda x: x["tab"] == tab, why=f"{tab} 页签")[1]

    def ensure_set(self, number: int) -> dict:
        """左右箭头翻到编成号 number（每点一下都核对编号变了，不靠首尾循环）。"""
        if not 1 <= number <= 20:
            raise ValueError("编成号要在 1–20")
        _, m = self.wait_menu(why="プロデュース編成页")
        for _ in range(SET_MAX_TAPS):
            cur = m["number"]
            if cur == number:
                return m
            self.dev.click(*ARROWS["next" if number > cur else "prev"])
            _, m = self.wait_menu(lambda x, cur=cur: x["number"] != cur, why=f"编成号从 {cur} 变")
        raise FormationWriteError(f"点了 {SET_MAX_TAPS} 次箭头还没到编成 {number}")


# ---------------------------------------------------------------- 写入 + 回读
def _settle_icons(dev: Device, tries: int = 6):
    """回忆大图标读两次一样才算（翻编号 / 決定 后有动画）。"""
    prev = None
    reads = []
    for _ in range(tries):
        reads = read_memory_icons(dev.shoot())
        cur = [r.get("card") if r.get("known") else None for r in reads]
        if cur == prev:
            return reads
        prev = cur
        dev.sleep(WAIT_GAP)
    return reads


def memory_diff(plan: dict, reads: list[dict]) -> list[tuple[int, str]]:
    """方案的回忆卡 vs 菜单上排大图标 → 要改的 [(格子, 卡键)]（formation_writer.fix_plan）。"""
    from hiro.vision.memory_icons import card_key

    want = [card_key(c["card_id"]) for c in (plan.get("memory") or {}).get("chosen") or [] if c.get("card_id")]
    current = [r["card"] if r.get("known") else None for r in reads]
    return fix_plan(current, want)


def write_memory(dev: Device, plan: dict, make_picker, *, dry_run: bool = False) -> list[dict]:
    """当前停在方案编成号的回忆页：和方案不一致的格子逐个改（fix_memory，格子用 MEMORY_TILES，认卡用上排大图标），
    写完整页回读，四格都要是方案的卡。dry_run 只报差异。"""
    reads = _settle_icons(dev)
    moves = memory_diff(plan, reads)
    logger.info("回忆页现状 %s；要改 %s", [r.get("card") if r.get("known") else None for r in reads], moves)
    if dry_run or not moves:
        done = []
    else:
        done = fix_memory(
            dev, plan, read_icons=lambda img: read_memory_icons(img), make_picker=make_picker, slots=MEMORY_TILES
        )
    if not dry_run:
        left = memory_diff(plan, _settle_icons(dev))
        if left:
            raise FormationWriteError(f"回忆写完回读还差 {left}")
    return done if not dry_run else [{"slot": s, "to": k, "dry_run": True} for s, k in moves]


def support_diff(plan: dict, page: dict) -> list[tuple[int, str]]:
    want = [o["support_card_id"] for o in (plan.get("support") or {}).get("own") or []]
    return fix_plan([s.get("support_card_id") for s in page["slots"]], want)


def write_support(dev: Device, plan: dict, *, read_formation, read_list, dry_run: bool = False) -> list[dict]:
    """当前停在方案编成号的支援卡页：fix_support（view=formation），写完回读 5 格都要是方案的卡（租借格不在菜单里）。"""
    from hiro.produce.formation_writer import _settled_step2

    page = _settled_step2(dev, read_formation, view="formation")
    if page is None:
        raise FormationWriteError("不在支援卡编成页")
    moves = support_diff(plan, page)
    logger.info("支援卡页现状 %s；要改 %s", [s.get("support_card_id") for s in page["slots"]], moves)
    if dry_run:
        return [{"slot": s, "to": k, "dry_run": True} for s, k in moves]
    done = fix_support(dev, plan, read_formation=read_formation, read_list=read_list, view="formation") if moves else []
    page = _settled_step2(dev, read_formation, view="formation")
    left = support_diff(plan, page) if page is not None else ["读不到"]
    if left:
        raise FormationWriteError(f"支援卡写完回读还差 {left}")
    return done


def run(dev: Device, plan: dict, mode: str, *, what: tuple[str, ...] = ("support", "memory"), make_picker=None,
        read_formation=None, read_list=None, dry_run: bool = False) -> dict:  # fmt: skip
    """进菜单、切模式，按 what 写支援卡 / 回忆（编成号用方案的 support.set / memory.set）。返回 {support: [...], memory: [...]}。"""
    menu = Menu(dev)
    menu.open()
    menu.ensure_mode(mode)
    out = {}
    for kind in what:
        number = (plan.get(kind) or {}).get("set")
        if not number:
            raise FormationWriteError(f"方案里没有 {kind}.set（编成号）")
        menu.ensure_tab(kind)
        menu.ensure_set(int(number))
        if kind == "memory":
            out[kind] = write_memory(dev, plan, make_picker, dry_run=dry_run)
        else:
            out[kind] = write_support(dev, plan, read_formation=read_formation, read_list=read_list, dry_run=dry_run)
        logger.info("编成菜单：%s 编成 %s %s", kind, number, out[kind] or "不用改")
    return out


__all__ = [
    "Menu",
    "memory_diff",
    "read_memory_icons",
    "read_menu",
    "read_mode",
    "run",
    "support_diff",
    "write_memory",
    "write_support",
]


PICKER_TRIES = 4  # 点格子后最多看几次列表开没开（每次 1.5 秒，第一次和中途各点一次）


def open_picker(dev: Device, kind: str, *, ready: Callable[[object], bool], read_formation=None) -> None:
    """从首页（或编成菜单）进支援卡 / 回忆的选择列表，给库存扫描用（初星助手的「扫描」按钮）：进菜单 → 切到 kind 页 →
    点当前编成第 1 格。只点格子，不点「決定」；列表里由扫描自己读完点キャンセル。ready(截图) 判断列表开了没有。"""
    from hiro.produce.formation_writer import FormationWriteError, _settled_step2

    menu = Menu(dev)
    menu.open()
    menu.ensure_tab(kind)
    if kind == "memory":
        slot = MEMORY_TILES[0]
    else:
        page = _settled_step2(dev, read_formation, view="formation")
        if page is None or not page.get("slots"):
            raise FormationWriteError("读不到支援卡编成页的格子")
        slot = tuple(page["slots"][0]["center"])
    for attempt in range(PICKER_TRIES):
        if attempt in (0, PICKER_TRIES // 2):
            dev.click(*slot)
        dev.sleep(1.5)
        if ready(dev.shoot()):
            logger.info("编成菜单：%s 选择列表已打开（点了第 1 格）", kind)
            return
    raise FormationWriteError(f"点{'回忆' if kind == 'memory' else '支援卡'}格没打开选择列表")


def tool_device(d) -> Device:
    """hiro.tool_device 的设备 → 编成菜单 / 写编成用的 Device（同 tools/write_formation.py）。"""
    from hiro.devices.active import sleep
    from hiro.produce.formation_writer import Device as WriterDevice
    from hiro.vision.ocr import screen_texts

    return WriterDevice(shoot=d.screenshot, click=d.tap, swipe=lambda x1, y1, x2, y2, dur=None: d.swipe(x1, y1, x2, y2, duration=dur or 0.3),
                        ocr=lambda img: [(t.text, 1.0, tuple(int(v) for v in t.rect)) for t in screen_texts(img)], sleep=sleep)  # fmt: skip
