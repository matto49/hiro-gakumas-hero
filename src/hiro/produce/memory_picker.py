# 调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""回忆（メモリー）选择页的点击路径与只读扫描：给总控在实机上跑，产出 reports/live/memory-inventory.json。

画面（编成页点某一格回忆打开的选择页，720x1280；依据 reports/live/memory-formation/cand-*.jpg、memscan2-*.jpg）：
- 顶部：所选回忆的属性 ボーカル / ダンス / ビジュアル / 体力（y≈70）与百分比（y≈103）。
- 详情栏两页（中间「1/2」(361,533)，左右箭头约 (230,530) / (485,530)）：
  1/2 = 獲得可能スキルカード：入手时机标签（「開始時に獲得」/「中間試験/1次オーディション後に獲得」，y≈198）、卡名（y≈240）、效果；
  2/2 = 能力（被动）列表。
- 回忆格：3 列（中心 x 140 / 360 / 580，宽约 210），行高约 118、行距约 137.5；行的上下沿按饱和度空隙找（滚动后位置会变）。
- 底栏 y≈1050：「編成詳細」(135)、筛选图标 (399) + 当前排序名「プロデュースク…」(495)、升降序图标 (653)；
  最底「キャンセル」(211,1162) /「決定」(475,1160)。
只读：选回忆不会改编成，只有「決定」才生效。扫描结束一律点「キャンセル」；筛选 / 排序对话框只用「キャンセル / 閉じる」关闭。
筛选 / 排序 / 关键词搜索的对话框还没有样本：explore() 负责去拍，拍完再补点击路径。
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from hiro.devices.active import sleep as _sleep
from hiro.produce.ops import find_text, ocr_boxes, roi_diff
from hiro import imgio

logger = logging.getLogger(__name__)

COL_X = (140, 360, 580)
CELL_W, CELL_H_MIN = 210, 100
GRID_X = (35, 687)
GRID_Y = (560, 1040)  # 回忆格区（上面是详情栏，下面是底栏），用来判断画面变没变
ROW_BOTTOM_MAX = 995  # 完整行的下沿上限：再往下被浮在列表上的「編成詳細 / 筛选 / 排序」按钮（y≈1000–1060）挡住
ROW_H = (108, 128)  # 完整行高（实测 117–120）
GAP_SAT_MAX = 40  # 行间空隙的平均饱和度
DETAIL_ROI = (40, 170, 640, 340)  # 详情栏 (x, y, w, h)：换选中回忆 / 换页时这里变
STATS_ROI = (100, 30, 600, 90)
PAGE_PREV, PAGE_NEXT = (230, 530), (485, 530)
CANCEL_TAP = (211, 1162)
FILTER_TAP, SORT_KEY_TAP, SORT_DIR_TAP = (399, 1050), (495, 1050), (653, 1050)
SWIPE_FROM, SWIPE_ROWS = 1000, 2  # 一次上滑 2 行（行距 137.5），可见 3 行 → 与上一页重叠 1 行，用来核对没有跳行
SCROLL_STEP_MAX = int(3 * 137.5)  # 一次滑动最多这么多像素（3 行）：上滑 1000→588、下滑 600→1012，都在列表区（560–1040）里
SWIPE_DURATION, SWIPE_HOLD = 1.0, 0.4  # 慢滑 + 终点停住再抬起：不带惯性（2026-11-01 实机：3 行快滑一次滚过 4 行以上，整行漏扫）
CELL_MATCH_MAX = 14.0  # 两张格子缩略图（48x24 灰度）平均差 ≤ 这个算同一格（行带上下差 1–2 像素也能对上）
MAX_BACKTRACK = 3
ROW_PITCH = 137.5
TIMING = {"開始時": "start", "中間試験": "mid", "オーディション後": "mid"}
CLOSE_TEXTS = ("キャンセル", "閉じる", "戻る")
ABILITY_GAP = 22  # 同一张能力卡片里相邻两行的间距 ≤ 这个；卡片之间更大

TAP_TIMEOUT = 3.0
SELECTED_BAND = (2, 14)  # 选中框（橙色四角）在格子外沿外侧这么多像素里
SELECTED_ARM = 40  # 每个角量横竖各这么长
SELECTED_MIN = 800  # 四角橙色像素合计 ≥ 这个算选中（11-19 账号 2 实机：选中格 1350–1570，其他格 ≤ 约 380，邻格的框会漏进来一点）
SCROLL_SETTLE = 0.8


def picker_open(boxes) -> bool:
    """在回忆选择页：底部有「キャンセル」「決定」，底栏有「編成詳細」。"""
    # 「編成詳細」RapidOCR 常读歪（半分辨率帧「成詳綱」），认中间的「成詳」也算；キャンセル / 決定 仍须都在
    return all(find_text(boxes, t) is not None for t in ("キャンセル", "決定")) and find_text(boxes, ("編成詳細", "成詳")) is not None


def grid_rows(img: np.ndarray) -> list[tuple[int, int]]:
    """可见的完整回忆行 [(y1, y2)]：按横向饱和度找行间空隙，取高度够的段。"""
    sat = cv2.cvtColor(img[:, GRID_X[0] : GRID_X[1]], cv2.COLOR_BGR2HSV)[:, :, 1].mean(1)
    rows, start = [], None
    for y in range(GRID_Y[0], GRID_Y[1]):
        filled = sat[y] >= GAP_SAT_MAX
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            # 顶部被详情栏截断的半行、底部被浮动按钮挡住的行（2026-11-01 第二次扫描：最后一行被挡、对不上下一页）都不要
            if ROW_H[0] <= y - start <= ROW_H[1] and start > GRID_Y[0] and y <= ROW_BOTTOM_MAX:
                rows.append((start, y))
            start = None
    return rows


def grid_cells(img: np.ndarray) -> list[dict]:
    """可见的完整回忆格：[{row, col, center, rect, hash}]，从左到右、从上到下。hash 用于翻页去重。"""
    out = []
    for r, (y1, y2) in enumerate(grid_rows(img)):
        for c, cx in enumerate(COL_X):
            x1 = cx - CELL_W // 2
            crop = img[y1:y2, x1 : x1 + CELL_W]
            if float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].mean()) < GAP_SAT_MAX:
                continue  # 最后一行不满 3 格
            # 去掉外沿 8 像素（选中时的橙色框）再算指纹，选中与否不影响
            small = cv2.resize(cv2.cvtColor(crop[8:-8, 8:-8], cv2.COLOR_BGR2GRAY), (48, 24))
            out.append({"row": r, "col": c, "center": [cx, (y1 + y2) // 2], "rect": [x1, y1, CELL_W, y2 - y1],
                        "hash": hashlib.md5((small // 16).tobytes()).hexdigest()[:12]})  # fmt: skip
    return out


def detail_page(boxes) -> int | None:
    for t, _, (x1, y1, x2, y2) in boxes:
        # 斜杠 RapidOCR 常读成 1（「1/2」→「112」）；页码只有 1/2、2/2，三位且中间是 1 / l / I / | 也按斜杠算
        if 480 <= (y1 + y2) // 2 <= 580 and (m := re.fullmatch(r"([12])[/1lI|]2", t.replace(" ", ""))):
            return int(m.group(1))
    return None


def _lines(boxes, y1: int, y2: int) -> list[str]:
    return [t for t, _, b in sorted(boxes, key=lambda b: ((b[2][1] + b[2][3]) // 2, b[2][0])) if y1 <= (b[1] + b[3]) // 2 <= y2]


def read_stats(boxes) -> dict:
    """顶部属性：{Vo, Da, Vi, hp}（「145+15」这类预览差值只取前面的数）。"""
    out = {}
    for key, x in (("Vo", 190), ("Da", 326), ("Vi", 468), ("hp", 630)):
        hit = next((t for t, _, b in boxes if abs((b[0] + b[2]) // 2 - x) <= 45 and 55 <= (b[1] + b[3]) // 2 <= 90), None)
        m = re.match(r"\d+", hit.replace(" ", "")) if hit else None
        out[key] = int(m.group()) if m else None
    return out


OCR_SWAPS = (("ヘ", "へ"), ("宜", "宣"), ("ー", "一"), ("一", "ー"), ("へ", "ヘ"))  # 实机扫描读错的：輝くキミヘ / アイドル宜言 / 精神統ー


def _name_variants(text: str) -> list[str]:
    """卡名原文 + 常见 OCR 混淆逐个替换后的写法（原文优先）。"""
    out = [text]
    for a, b in OCR_SWAPS:
        if a in text:
            out.append(text.replace(a, b))
    return out


FUZZY_NAME_MIN = 0.80  # 卡名精确 / 混淆替换都没命中时，和主数据卡名的相似度至少这么高
FUZZY_NAME_MARGIN = 0.05  # 而且要比第二像的高出这么多（同名不同强化「+」只差一个字，靠这个分开）


def fuzzy_card(text: str) -> dict | None:
    """卡名 OCR 漏字 / 错字时按相似度找主数据卡名（11-19 账号 2：「アイドル宣言」读成「アイドル言」，写编成找不到这份回忆；
    「嬉しい誤算+」读成「嬌しい誤算＋」）。最像的不够像、或和第二像的分不开就给 None，不猜。"""
    from difflib import SequenceMatcher

    from hiro.vision.deck import _cards_by_full_name, card_from_name

    name = text.replace("＋", "+").replace(" ", "").strip()
    if len(name.rstrip("+")) < 3:
        return None
    scored = sorted(((SequenceMatcher(None, name, k).ratio(), k) for k in _cards_by_full_name()), reverse=True)
    if not scored or scored[0][0] < FUZZY_NAME_MIN:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < FUZZY_NAME_MARGIN:
        return None
    return card_from_name(scored[0][1])


def read_card_page(boxes) -> dict:
    """详情 1/2：{timing: start|mid|None, timing_text, card_text（卡名原文）, card（主数据命中 {card_id, name, upgrade}）, effect}。"""
    from hiro.vision.deck import card_from_name

    timing_text = next((t for t in _lines(boxes, 180, 215) if "獲得" in t and "獲得可能" not in t), None)
    timing = next((v for k, v in TIMING.items() if timing_text and k in timing_text), None)
    names = [t for t in _lines(boxes, 222, 260)]
    card = next((c for t in names for v in _name_variants(t) if (c := card_from_name(v))), None)
    if card is None and names:
        card = fuzzy_card(names[0])
    return {"timing": timing, "timing_text": timing_text, "card_text": names[0] if names else None, "card": card,
            "effect": _lines(boxes, 270, 510)}  # fmt: skip


def read_ability_page(boxes) -> list[dict]:
    """详情 2/2：能力（被动）卡片，两列排布。按列、按纵向间距把 OCR 框拼成一张张卡片：
    [{text, disabled}]（disabled = 卡片角上有「発動不可」，例如别的偶像专用的能力），左列在前、各列从上到下。"""
    tiles = []
    for lo, hi in ((0, 360), (360, 720)):
        col = sorted(((b[1] + b[3]) // 2, b[1], b[3], t) for t, _, b in boxes
                     if lo <= (b[0] + b[2]) // 2 < hi and 120 <= (b[1] + b[3]) // 2 <= 515 and t != "獲得可能スキルカード")  # fmt: skip
        cur, last_bottom = [], None
        for _, top, bottom, t in col:
            if cur and top - last_bottom > ABILITY_GAP:
                tiles.append(cur)
                cur = []
            cur.append(t)
            last_bottom = bottom
        if cur:
            tiles.append(cur)
    out = []
    for parts in tiles:
        disabled = "発動不可" in parts
        text = "".join(p for p in parts if p != "発動不可").replace(" ", "")
        if text:
            out.append({"text": text, "disabled": disabled})
    return out


class PickerClosed(RuntimeError):
    """回忆选择页不见了（多半点成了「決定」，编成可能被改了）：扫描 / 读取立即停下，不再点任何东西。"""


class Picker:
    """回忆选择页上的操作。shoot / click / swipe / ocr / clock / sleep 可注入（离线测试）。"""

    def __init__(self, shoot, click, swipe, *, ocr=ocr_boxes, clock=time.monotonic, sleep=_sleep, calib_dir: Path | None = None):
        self.shoot, self.click, self.swipe, self.ocr, self.clock, self.sleep = shoot, click, swipe, ocr, clock, sleep
        self.calib_dir, self._n = calib_dir, 0

    def snap(self, img, tag: str) -> None:
        if self.calib_dir is None:
            return
        self.calib_dir.mkdir(parents=True, exist_ok=True)
        self._n += 1
        imgio.imwrite(str(self.calib_dir / f"{self._n:04d}-{tag}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    def _wait_change(self, before, roi, timeout=TAP_TIMEOUT):
        t0 = self.clock()
        while True:
            img = self.shoot()
            if roi_diff(before, img, roi) > 6:
                self.sleep(0.3)  # 等文字渐入
                return True, self.shoot()
            if self.clock() - t0 >= timeout:
                return False, img
            self.sleep(0.15)

    def select(self, cell: dict) -> np.ndarray:
        """点一格；详情栏变了（或本来就是这格）后返回画面。"""
        return self.select_checked(cell)[1]

    def ensure_open(self, boxes=None, img=None) -> list:
        """还在回忆选择页（キャンセル / 決定 / 編成詳細 都在）；不在了抛 PickerClosed。返回用过的 OCR 结果。"""
        if boxes is None:
            boxes = self.ocr(img if img is not None else self.shoot())
        if not picker_open(boxes):
            raise PickerClosed("回忆选择页不见了（可能点成了「決定」，编成可能被改了）")
        return boxes

    def select_checked(self, cell: dict) -> tuple[bool, np.ndarray]:
        """点一格（只点一次），返回 (详情栏变了没有, 画面)。点之前核对还在选择页。
        详情栏没变也不再点同一格：再点已选中的格子可能被游戏当成「決定」（11-16 账号 2：扫描中选择页关了、编成第 1 格被换成
        ラブリーウインク），只多等一会儿。"""
        before = self.shoot()
        self.ensure_open(img=before)
        if cell.get("rect") and cell_selected(before, cell):
            # 已经是选中格（从空格子打开选择页时第一格默认选中）：再点会被当成「決定」（11-19 账号 2 扫描一开始就关了选择页），只读不点
            logger.info("回忆选择页：第 %s 格已经选中，不点", cell.get("row", "?"))
            return True, before
        self.click(*cell["center"])
        changed, img = self._wait_change(before, DETAIL_ROI)
        if not changed:
            self.sleep(0.8)
            changed, img = self._wait_change(before, DETAIL_ROI)
        return changed, img

    def to_page(self, page: int) -> tuple[np.ndarray, list]:
        """详情栏翻到第 page 页（1 / 2）。每次点翻页箭头前核对还在选择页、并且认出了页码（认不出页码就不盲点，等一下重读）。"""
        img = self.shoot()
        boxes = self.ensure_open(self.ocr(img))
        for _ in range(3):
            cur = detail_page(boxes)
            if cur == page:
                return img, boxes
            if cur is None:
                self.sleep(0.4)
                img = self.shoot()
                boxes = self.ensure_open(self.ocr(img))
                continue
            self.click(*(PAGE_NEXT if cur < page else PAGE_PREV))
            _, img = self._wait_change(img, DETAIL_ROI)
            boxes = self.ensure_open(self.ocr(img))
        return img, boxes

    def read_selected(self) -> dict:
        img, boxes = self.to_page(1)
        entry = {"stats": read_stats(boxes), **read_card_page(boxes)}
        img2, boxes2 = self.to_page(2)
        entry["abilities"] = read_ability_page(boxes2)
        self.to_page(1)
        return entry

    def scroll(self, rows: float = SWIPE_ROWS, *, px: int | None = None, x: int = 360, y_from: int | None = None) -> np.ndarray:
        """上滑 rows 行（负数下滑；或直接给像素 px），慢滑、终点停住，返回滑后的画面。x / y_from：换个起点滑（原起点没让列表动时）。"""
        dy = int(px if px is not None else rows * ROW_PITCH)
        # 一次最多滑 SCROLL_STEP_MAX：起止点都留在列表区里（11-17 开局第 3 步：一次滑约 16 行，终点 y 成了负数，kotonebot swipe 报
        # argument out of range）。大距离拆成几次
        while abs(dy) > SCROLL_STEP_MAX:
            step = SCROLL_STEP_MAX if dy > 0 else -SCROLL_STEP_MAX
            y1 = y_from if y_from is not None else (SWIPE_FROM if step > 0 else GRID_Y[0] + 40)
            self.swipe(x, y1, x, y1 - step)
            self.sleep(SCROLL_SETTLE)
            dy -= step
        y1 = y_from if y_from is not None else (SWIPE_FROM if dy > 0 else GRID_Y[0] + 40)
        self.swipe(x, y1, x, y1 - dy)
        self.sleep(SCROLL_SETTLE)
        return self.shoot()

    def cancel(self) -> bool:
        """点「キャンセル」关掉选择页（按 OCR 找：编成菜单的选择页在 (211,1162)，开局第 3 步的多了 所持メモリー / レンタル 页签、
        在 (220,1160) 附近；找不到才点 CANCEL_TAP）。返回选择页关了没有。"""
        boxes = self.ocr(self.shoot())
        pos = find_text(boxes, "キャンセル", region=(0, 1080, 720, 1280)) or CANCEL_TAP
        self.click(*pos)
        self.sleep(1.0)
        return not picker_open(self.ocr(self.shoot()))


def cell_selected(img: np.ndarray, cell: dict) -> bool:
    """格子是不是选中格：看格子四角外侧的橙色 L 形选中框（不看格子里面，卡面本身常有橙色）。"""
    x, y, w, h = cell["rect"]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 0] >= 8) & (hsv[..., 0] <= 24) & (hsv[..., 1] > 140) & (hsv[..., 2] > 200)
    H, W = m.shape
    near, far = SELECTED_BAND
    total = 0
    for cx, cy, sx, sy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        y1, y2 = sorted((cy - sy * far, cy - sy * near))
        x1, x2 = sorted((cx - sx * far, cx - sx * near))
        ax1, ax2 = sorted((cx, cx + sx * SELECTED_ARM))
        ay1, ay2 = sorted((cy, cy + sy * SELECTED_ARM))
        total += int(m[max(0, y1) : max(0, y2), max(0, ax1) : min(W, ax2)].sum())  # 横臂（格子外上 / 下方）
        total += int(m[max(0, ay1) : min(H, ay2), max(0, x1) : max(0, x2)].sum())  # 竖臂（格子外左 / 右方）
    return total >= SELECTED_MIN


def cell_thumb(img: np.ndarray, cell: dict) -> np.ndarray:
    """格子缩略图（去掉外沿 8 像素的选中框），用于跨页对齐。"""
    x, y, w, h = cell["rect"]
    return cv2.resize(cv2.cvtColor(img[y + 8 : y + h - 8, x + 8 : x + w - 8], cv2.COLOR_BGR2GRAY), (48, 24)).astype(np.float32)


def page_rows(img: np.ndarray) -> list[list[dict]]:
    """可见完整行：每行若干格（带 thumb）。"""
    rows: dict[int, list[dict]] = {}
    for c in grid_cells(img):
        rows.setdefault(c["row"], []).append({**c, "thumb": cell_thumb(img, c)})
    return [rows[k] for k in sorted(rows)]


def same_row(a: list[dict], b: list[dict]) -> bool:
    return len(a) == len(b) and all(float(np.abs(x["thumb"] - y["thumb"]).mean()) <= CELL_MATCH_MAX for x, y in zip(a, b))


def row_overlap(prev: list[list[dict]], cur: list[list[dict]]) -> int:
    """cur 开头有几行就是 prev 结尾那几行（最大的 m）；0 = 没重叠（滚过头了，或到了别处）。"""
    for m in range(min(len(prev), len(cur)), 0, -1):
        if all(same_row(prev[len(prev) - m + i], cur[i]) for i in range(m)):
            return m
    return 0


def to_top(picker: "Picker", max_swipes: int = 80) -> np.ndarray:
    """往下滑回列表顶部（画面不再变 = 到顶），返回顶部画面。"""
    img = picker.shoot()
    still = 0
    for _ in range(max_swipes):
        nxt = picker.scroll(-3)
        still = still + 1 if grid_still(img, nxt) else 0
        img = nxt
        if still >= 2:
            break
    return img


def entry_key(e: dict) -> str:
    """同一份回忆的内容键：卡（名 + 时机）+ 属性 + 能力文字（只用于统计重复，不再用来去重）。"""
    s = e.get("stats") or {}
    card = (e.get("card") or {}).get("card_id") or e.get("card_text")
    return json.dumps([card, (e.get("card") or {}).get("upgrade"), e.get("timing"), s.get("Vo"), s.get("Da"), s.get("Vi"), s.get("hp"),
                       e.get("abilities")], ensure_ascii=False)  # fmt: skip


def grid_still(a: np.ndarray, b: np.ndarray) -> bool:
    """回忆格区两帧几乎一样（滑动没让列表动）。"""
    return roi_diff(a, b, (GRID_X[0], GRID_Y[0], GRID_X[1] - GRID_X[0], GRID_Y[1] - GRID_Y[0])) < 2.0


def content_key(e: dict) -> str:
    """一格读到的内容（卡 + 强化 + 时机 + 能力）：判断详情栏是不是没刷新（和上一格一模一样）。
    不含属性：选择页顶部是「当前编成合计 ± 这份回忆的增减」（如 Vo 20 -15），不是这份回忆自己的（协调 11-16）。"""
    card = e.get("card") or {}
    return json.dumps([card.get("card_id") or e.get("card_text"), card.get("upgrade"), e.get("timing"), e.get("abilities")], ensure_ascii=False)


ALT_SWIPES = ((150, 940), (570, 940), (360, 880))  # 原起点 (360, SWIPE_FROM) 没让列表动时换这几个起点再试（起点可能落在锁格 / 浮动按钮上）


def scroll_moved(picker: "Picker", before: np.ndarray, px: int) -> tuple[bool, np.ndarray]:
    """往上滑 px：列表区（整块像素，不靠认出的行）动了返回 (True, 画面)；原起点不动就换 ALT_SWIPES 的起点再试，都不动 (False, 最后一帧)。"""
    img = picker.scroll(px=px)
    if not grid_still(before, img):
        return True, img
    for x, y1 in ALT_SWIPES:
        img = picker.scroll(px=px, x=x, y_from=y1)
        if not grid_still(before, img):
            logger.info("回忆扫描：原起点滑不动，换起点 (%d, %d) 滑动了", x, y1)
            return True, img
    return False, img


def _at_end(picker: "Picker", inv: dict, img: np.ndarray, pages: int) -> None:
    """列表滑不动：还在回忆选择页才算到底（complete）；不在了（被别的画面挡住）记 aborted。都存帧。"""
    picker.snap(img, f"page{pages:03d}-end")
    if picker_open(picker.ocr(img)):
        inv["complete"] = True
    else:
        inv["aborted"] = f"第 {pages} 页后列表滑不动，而且画面不是回忆选择页了（被别的画面挡住？），停止扫描"
        logger.warning("回忆扫描：%s", inv["aborted"])


def align(prev: list[list[dict]], cur: list[list[dict]]) -> int | None:
    """prev 的最后一行在 cur 里是第几行（往前能对上的行也都要对上）；找不到 None。
    不只看 cur 开头：滑多了 / 滑少了 / 没滑动，都能对上位置。"""
    if not prev or not cur:
        return None
    for k in range(len(cur) - 1, -1, -1):
        n = min(k + 1, len(prev))
        if all(same_row(prev[len(prev) - 1 - i], cur[k - i]) for i in range(n)):
            return k
    return None


def iter_rows(picker: "Picker", *, max_pages: int = 600):
    """从顶部往下逐页走，产出 (全局行号, 这一行的格子)，行号按页与页的重叠精确计数（同 scan：读完重截当上一页、align 找上一页最后一行、
    对不上一行一行往回滑），不按滑过的像素估算（11-17 编成菜单：按每屏行数累计会高估，第 70 行 ±30 行都找不到）。
    调用方可以在拿到一行后点格子读详情；走不下去（到底 / 对不上）就结束。"""
    img = to_top(picker)
    base, start = 0, 0
    for _ in range(max_pages):
        rows = page_rows(img)
        for i in range(start, len(rows)):
            yield base + i, rows[i]
        prev_img = picker.shoot()
        prev = page_rows(prev_img) or rows
        if not prev:
            return
        dy = prev[-1][0]["rect"][1] - prev[0][0]["rect"][1] if len(prev) > 1 else int(ROW_PITCH)
        moved, img = scroll_moved(picker, prev_img, dy)
        if not moved:
            return  # 到底
        k = align(prev, page_rows(img))
        back = 0
        while k is None and back < SCAN_BACKTRACK:
            back += 1
            img = picker.scroll(-1)
            k = align(prev, page_rows(img))
        if k is None:
            logger.warning("回忆列表：第 %d 行之后对不上（往回滑了 %d 行），行号没法继续数，停", base + len(prev) - 1, back)
            return
        base, start = base + len(prev) - 1 - k, k + 1


def mark_suspects(entries: list[dict]) -> int:
    """标可疑记录（suspect 写原因；formation_plan 不选可疑的）：
    - card_unread：卡名读不出；
    - same_as_prev：和按格子顺序的上一格内容一模一样、但格子缩略图不同（点了格子详情栏没刷新，读到的是上一格；2026-11-15 账号 2）；
    - detail_not_refreshed：点了两次详情栏都没变（且不是同一张缩略图）。
    返回可疑条数。"""
    n = 0
    prev = None
    for e in sorted(entries, key=lambda x: x.get("slot", 0)):
        why = e.get("suspect")
        if why is None and not (e.get("card") or {}).get("card_id"):
            why = "card_unread"
        if why is None and prev is not None and content_key(e) == content_key(prev) and e.get("thumb") != prev.get("thumb"):
            why = "same_as_prev"
        if why is None and e.get("refreshed") is False and (prev is None or e.get("thumb") != prev.get("thumb")):
            why = "detail_not_refreshed"
        if why is not None:
            e["suspect"] = why
            n += 1
        prev = e
    return n


SCAN_BACKTRACK = 6  # 新页里找不到上一页最后一行时，最多往回滑几行再找


def scan(picker: Picker, out: Path, *, max_pages: int = 600, on_entry: Callable[[dict], None] | None = None) -> dict:
    """从顶部开始逐行读。每页读完重新截一张图当「上一页」（读的时候点了格子，选中框会留在上一页最后一格，不重截会和新页对不上），
    再把最后一个完整行滑到最上面；新页里找上一页最后一行的位置（align），之后的行是新的。
    对不上：一行一行往回滑（最多 SCAN_BACKTRACK 行）找上一页最后一行；还找不到就停止扫描（gaps 记下，complete=False），不接着往下记
    （2026-11-15 账号 2：对不上后往回滑了 3 行接着记，旧行被当成新行记了一遍又一遍，行号全偏）。
    到底的判定：连续 2 次滑动后回忆格区都完全不变。每格读完核对详情栏有没有刷新，最后 mark_suspects 标可疑记录。
    每个格子一条记录（slot = 全局行号 * 3 + 列，thumb = 格子缩略图指纹）；每读一格写一次 out。结束点「キャンセル」。只读。"""
    img = picker.shoot()
    if not picker_open(picker.ocr(img)):
        raise RuntimeError("不在回忆选择页（没看到 キャンセル / 決定 / 編成詳細）：请先在编成页点开一格回忆")
    if out.exists():
        out.with_suffix(out.suffix + ".bak").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    img = to_top(picker)
    inv = {"entries": [], "gaps": [], "scanned_at": None, "complete": False}
    pages = new = 0
    row_base, start = 0, 0  # 当前页第 0 行的全局行号；本页从第几行开始读（前面的行上一页读过）
    last = None  # 上一条记录：判断详情栏是不是没刷新

    def save():
        inv["scanned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out.write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")

    closed = False
    try:
        while pages < max_pages:
            picker.snap(img, f"page{pages:03d}")
            rows = page_rows(img)
            for r in range(start, len(rows)):
                for cell in rows[r]:
                    changed, _ = picker.select_checked(cell)
                    e = picker.read_selected()
                    if last is not None and content_key(e) == content_key(last) and cell["hash"] != last.get("thumb"):
                        # 详情栏还是上一格的内容：等一下重读（不再点同一格：再点已选中的格子可能被当成「決定」）
                        picker.sleep(1.0)
                        e = picker.read_selected()
                    e.update(page=pages, slot=(row_base + r) * 3 + cell["col"], cell=[row_base + r, cell["col"]], thumb=cell["hash"],
                             refreshed=changed)  # fmt: skip
                    if last is not None and content_key(e) == content_key(last) and cell["hash"] != last.get("thumb"):
                        e["suspect"] = "same_as_prev"
                        logger.warning("回忆扫描：第 %s 格读到的和上一格一模一样（缩略图不同），记为可疑", e["cell"])
                    inv["entries"].append(e)
                    last = e
                    new += 1
                    if on_entry:
                        on_entry(e)
                    save()
            pages += 1
            prev_img = picker.shoot()  # 读完重截：选中框状态和下一页一致
            prev, prev_base = page_rows(prev_img) or rows, row_base
            if not prev:  # 一行都没认出：不能直接算到底，按整块列表区像素看滑不滑得动
                moved, img = scroll_moved(picker, prev_img, int(SWIPE_ROWS * ROW_PITCH))
                if not moved:
                    _at_end(picker, inv, img, pages)
                    break
                row_base, start = prev_base, 0
                continue
            dy = prev[-1][0]["rect"][1] - prev[0][0]["rect"][1] if len(prev) > 1 else int(ROW_PITCH)
            moved, img = scroll_moved(picker, prev_img, dy)
            if not moved:  # 原起点和换的几个起点都滑不动，还在选择页：到底
                _at_end(picker, inv, img, pages)
                break
            cur = page_rows(img)
            k = align(prev, cur)
            if k is None:  # 留证据：读完重截的上一页、滑过去的这一页（11-15 那次对不上的原因没留帧，查不了）
                picker.snap(prev_img, f"page{pages - 1:03d}-after-read")
                picker.snap(img, f"page{pages:03d}-nomatch")
            back = 0
            while k is None and back < SCAN_BACKTRACK:  # 滑过头（惯性）：一行一行往回滑，直到看见上一页最后一行
                back += 1
                img = picker.scroll(-1)
                k = align(prev, page_rows(img))
            if k is None:
                inv["gaps"].append({"after_row": prev_base + len(prev) - 1, "page": pages})
                inv["aborted"] = f"第 {pages} 页和上一页对不上（往回滑了 {back} 行也找不到），停止扫描，后面没扫"
                logger.warning("回忆扫描：%s", inv["aborted"])
                break
            row_base = prev_base + len(prev) - 1 - k
            start = k + 1
    except PickerClosed as ex:
        # 选择页在扫描中关了：多半点成了「決定」，编成号的这一格可能被换成刚选中的回忆。立即停，不再点任何东西（不点キャンセル）
        closed = True
        picker.snap(picker.shoot(), f"page{pages:03d}-picker-closed")
        last_cell = inv["entries"][-1]["cell"] if inv["entries"] else None
        inv["aborted"] = f"读格子时回忆选择页关了（最后一条记录 {last_cell}）：{ex}。编成可能被改成了刚选中的那份回忆，请人工核对编成"
        inv["formation_may_have_changed"] = True
        logger.error("回忆扫描：%s", inv["aborted"])
    inv["suspects"] = mark_suspects(inv["entries"])
    save()
    if not closed and picker_open(picker.ocr(picker.shoot())):
        picker.cancel()  # 只在还在选择页时点キャンセル（不在了就别在别的画面上乱点）
    logger.info("回忆扫描：%d 页，读了 %d 格，可疑 %d 格，漏行 %d 处，complete=%s → %s", pages, new, inv["suspects"], len(inv["gaps"]),
                inv["complete"], out)  # fmt: skip
    return inv


def explore(picker: Picker, out_dir: Path) -> list[dict]:
    """拍下筛选 / 排序对话框与翻到底的画面（含 OCR），给补点击路径用。只用「キャンセル / 閉じる / 戻る」关闭对话框。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = []

    def shot(tag: str):
        img = picker.shoot()
        boxes = picker.ocr(img)
        imgio.imwrite(str(out_dir / f"{len(log):02d}-{tag}.png"), img)
        log.append({"tag": tag, "texts": [[t, list(b)] for t, _, b in boxes]})
        (out_dir / "explore.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        return img, boxes

    img, boxes = shot("picker")
    if not picker_open(boxes):
        raise RuntimeError("不在回忆选择页")
    for tag, tap in (("filter", FILTER_TAP), ("sort-key", SORT_KEY_TAP)):
        picker.click(*tap)
        picker.sleep(1.2)
        img, boxes = shot(tag)
        if picker_open(boxes):
            continue  # 没弹对话框（可能是排序方向切换）
        close = next((p for t in CLOSE_TEXTS if (p := find_text(boxes, t))), None)
        if close is None:
            raise RuntimeError(f"{tag} 对话框里没找到 キャンセル / 閉じる / 戻る，停下（已拍 {out_dir}）")
        picker.click(*close)
        picker.sleep(1.0)
        _, boxes = shot(f"{tag}-closed")
        if not picker_open(boxes):
            raise RuntimeError(f"关掉 {tag} 对话框后不在回忆选择页了（可能点到了选择页自己的キャンセル），停下（已拍 {out_dir}）")
    picker.to_page(2)
    shot("detail-p2")
    picker.to_page(1)
    img, still = picker.shoot(), 0
    for i in range(200):
        nxt = picker.scroll()
        still = still + 1 if grid_still(img, nxt) else 0
        img = nxt
        if still >= 2:
            break
    shot(f"bottom-after-{i + 1}-scrolls")
    picker.cancel()
    shot("after-cancel")
    return log
