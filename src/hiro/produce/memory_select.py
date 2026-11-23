"""本战开局前的选拔回忆：在「メモリー」列表里选中指定那份、核对开始确认页上的属性数字（不依赖 KAA）。
旧主循环经 kaa_patches 的同名包装用（设备是 kotonebot），新主循环（hiro.runtime.opening）直接用。"""

from __future__ import annotations

import logging
import time

from hiro import imgio
from hiro.devices.active import sleep as _sleep
from hiro.vision.ocr import screen_texts

logger = logging.getLogger(__name__)


class MemoryMismatch(RuntimeError):
    """本战开始确认页上选中的选拔回忆不是指定的那份：不开局。"""


MEMORY_CHANGE_BTN = (636, 577)  # 开始确认页「選択中の選抜試験メモリー」右侧「変更」
MEMORY_OK_BTN = (360, 1085)  # 回忆列表底部「OK」
MEMORY_SORT_BTN = (527, 860)  # 列表上的排序键按钮（「スター性」/「入手」…）→ ソート・フィルタ 弹窗
MEMORY_SORT_NEWEST = (413, 273)  # 弹窗里「入手」
MEMORY_SORT_OK = (500, 1160)  # 弹窗「決定」
MEMORY_CELLS = [(x, y) for y in (487, 627, 767) for x in (140, 360, 580)]  # 列表可见的 3×3 格
MEMORY_PAGES = 12  # 回忆 45 份以上（q10），每次只滑 2 行防跳行


MEMORY_HEADER_BAND = (100, 220)  # 列表上方所选回忆的属性行（110 < y < 210）
MEMORY_CELL_WAIT = 0.6  # 点一格后等上方属性更新
MEMORY_SORT_TEXT = "入手"  # ソート・フィルタ 弹窗里按入手排序的选项


def _memory_snap(img, tag: str) -> None:
    """回忆切换每一步存帧（第二十二局没找到第十八局回忆，又没有帧，分不清是排序错了还是回忆已不在列表）。"""
    if img is None:
        return
    try:
        from hiro.produce.ops import CALIB_DIR

        import cv2  # noqa: F401

        d = CALIB_DIR / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(_memory_snap.t0))}-回忆"
        d.mkdir(parents=True, exist_ok=True)
        _memory_snap.n += 1
        imgio.imwrite(str(d / f"{_memory_snap.n:02d}-{tag}.jpg"), img)
    except Exception:
        pass


_memory_snap.t0, _memory_snap.n = time.time(), 0


def _find_text_center(boxes, word: str, *, exact: bool = False):
    hits = [((x1 + x2) // 2, (y1 + y2) // 2) for t, _, (x1, y1, x2, y2) in boxes
            if (t.replace(" ", "") == word if exact else word in t.replace(" ", ""))]  # fmt: skip
    return hits[0] if hits else None


def select_memory(expect: tuple[str, ...], *, shoot, click, swipe, texts_of=None, sleep=_sleep, boxes_of=None) -> bool:
    """本战开始确认页：点「変更」→ 按「入手」排序 → 逐格点开回忆、读上方属性，找到属性全部吻合的那份 → OK。找不到就关掉列表、返回 False
    （回首页再进开始确认页时游戏会重置为上次用过的回忆，q8 实机，所以只能在这一页当场选）。
    第二十二局：12 页 × 9 格每格「等 1.2s + 整帧 OCR（3 倍放大）」，翻完要 5 分半；排序选项按固定坐标点，总控看到列表按「タグ」排。
    现在排序选项按 OCR 找「入手」点（找不到才用固定坐标），每格只 OCR 上方属性条（1.5 倍），等 0.6s；每步存帧到 calib/*-回忆。"""
    from hiro.produce.ops import ocr_boxes

    y1, y2 = MEMORY_HEADER_BAND
    texts_of = texts_of or (lambda img: [t for t, _, _ in ocr_boxes(img[y1:y2])])
    boxes_of = boxes_of or (lambda img: ocr_boxes(img) if img is not None else [])
    _memory_snap.t0, _memory_snap.n = time.time(), 0
    click(*MEMORY_CHANGE_BTN)
    sleep(1.5)
    # 按「入手」排序：刚打完的选拔回忆排在最前（q11：按スター性 排时新回忆在 46 份里靠后，没找到）
    click(*MEMORY_SORT_BTN)
    sleep(1.0)
    img = shoot()
    _memory_snap(img, "sort-dialog")
    boxes = boxes_of(img)
    click(
        *(
            _find_text_center(boxes, MEMORY_SORT_TEXT, exact=True)
            or _find_text_center(boxes, MEMORY_SORT_TEXT)
            or MEMORY_SORT_NEWEST
        )
    )
    sleep(0.5)
    img = shoot()
    _memory_snap(img, "sort-picked")
    click(*(_find_text_center(boxes_of(img), "決定") or MEMORY_SORT_OK))
    sleep(1.2)
    for page in range(MEMORY_PAGES):
        for i, cell in enumerate(MEMORY_CELLS):
            click(*cell)
            sleep(MEMORY_CELL_WAIT)
            img = shoot()
            if i == 0:
                _memory_snap(img, f"page{page:02d}")
            got = "|".join(texts_of(img)).replace(",", "").replace(" ", "")
            # 拼成一串再找：OCR 常给「+1434」「⑨1454」这种带前缀 / 读混的块，按块精确相等永远对不上（第十八局本战两次没找到）
            if all(memory_value_seen(v, got) for v in expect):
                logger.info("本战开局：在回忆列表里选中 %s（第 %d 页第 %d 格）", "/".join(expect), page + 1, i + 1)
                click(*MEMORY_OK_BTN)
                sleep(2.0)
                return True
        swipe(360, 900, 360, 620, 1.2)  # 约 2 行，慢滑无惯性
        sleep(1.0)
    logger.warning("回忆列表里没找到 %s（翻了 %d 页，每页首格帧存在 calib/*-回忆）", "/".join(expect), MEMORY_PAGES)
    click(*MEMORY_OK_BTN)  # 关掉列表；之后的核对仍会不通过，不开局
    sleep(2.0)
    return False


_DIGIT_CONFUSION = {
    "3": "35",
    "5": "53",
    "6": "68",
    "8": "86",
    "0": "08",
    "1": "17",
    "7": "71",
}  # 开始确认页粗体数字 OCR 常混淆


def memory_value_seen(value: str, texts: str) -> bool:
    """页面文字里有没有这个回忆属性值；允许最多 1 位按 _DIGIT_CONFUSION 混淆（第十八局：1434 被读成 1454，本战核对拒绝开局）。"""
    if value in texts:
        return True
    import re

    for m in re.finditer(r"\d{%d}" % len(value), texts):  # noqa: UP031
        cand = m.group(0)
        diffs = [(a, b) for a, b in zip(value, cand) if a != b]
        if len(diffs) == 1 and diffs[0][1] in _DIGIT_CONFUSION.get(diffs[0][0], ""):
            return True
    return False


MEMORY_PAGE_READS = 3


def memory_missing_on_page(expect: tuple[str, ...], *, shoot, texts=None, sleep=_sleep) -> list:
    """开始确认页读选中回忆的属性，最多读 MEMORY_PAGE_READS 次（页面刚出来时数字可能还没画完）；返回没读到的那几项。
    shoot() → 帧，texts(帧) → 文字列表。"""
    texts = texts or (lambda img: [t.text for t in screen_texts(img)])
    missing = list(expect)
    for i in range(MEMORY_PAGE_READS):
        tokens = texts(shoot())
        got = "".join(tokens).replace(",", "")
        missing = [v for v in expect if not memory_value_seen(v, got)]
        if len(expect) >= 3 and len(missing) == 1 and _one_digit_off(missing[0], tokens):
            # 另外几项都完全对上，只差这一项：同一段文字里有只差 1 位的数（DMM RapidOCR 11-17：Vi「1191」读成「391121」——
            # 图标读成数字、9 读成 2）。几项要同时对上，别的回忆刚好每项都只差 1 位的可能很小
            logger.info(
                "选拔回忆核对：%s 读成了只差 1 位的数，其余 %s 都对上，算通过",
                missing[0],
                [v for v in expect if v != missing[0]],
            )
            return []
        if not missing:
            return []
        if i + 1 < MEMORY_PAGE_READS:
            sleep(1.0)
    return missing


def _one_digit_off(value: str, tokens) -> bool:
    """某一段 OCR 文字（去掉非数字）里有和 value 等长、只差 1 位（任意数字）的连续数字。按段比，不跨段拼。"""
    import re

    for t in tokens:
        d = re.sub(r"\D", "", str(t))
        for j in range(len(d) - len(value) + 1):
            if sum(a != b for a, b in zip(value, d[j : j + len(value)])) == 1:
                return True
    return False
