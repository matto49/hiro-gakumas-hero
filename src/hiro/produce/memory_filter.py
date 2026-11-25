"""回忆选择页的「ソート・フィルタ」弹窗（资料库扫描设计 5.4 / D10）：设成某一片的筛选 + 按「入手」排序，扫完恢复。

11-26 实机（Mac 账号 2）：
- 选择页底部右侧的胶囊按钮显示当前排序名（「プロデュースク…」「入手」），点它打开弹窗；偶尔点了没反应，要看到「ソート・フィルタ」再往下走。
- 「ソート」页：表示順 4 个单选 プロデュースクリアランク / 入手 / アイドル / タグ。
- 「フィルタ」页要往下滑：「プラン」组（共通 / センス / ロジック / アノマリー）在「スキルカードタイプ」上面；
  更下面「プロデュース」组（定期公演『初』/ NEXT IDOL AUDITION / Hatsuboshi IDOL FESTIVAL）；底部「フィルタをリセット」。
  复选框灰勾 = 没选，点一下变橙 = 选中；一组里都没选 = 不按这组筛（11-26 实机）。
  「タグ」组里也有 センス / ロジック / アノマリー，只点「プラン」标题和「スキルカードタイプ」标题之间的。
- 排序和筛选退出选择页后会保留，所以扫完必须恢复。
恢复：排序改回扫描前的那种（按钮上有字）；筛选点「フィルタをリセット」回到默认（游戏读不出扫描前勾了哪些，玩家原来自己设的筛选会被重置）。
"""

from __future__ import annotations

from hiro.state.memory_slices import PLAN_LABELS, PRODUCE_LABELS
from hiro.vision.textnorm import fold

SORTS = ("プロデュースクリアランク", "入手", "アイドル", "タグ")
PILL = (485, 1051)
SORT_ZONE = (240, 400)
DIR_BTN = (653, 1051)  # 选择页右下的升降序按钮
DIR_ICON = (
    628,
    1030,
    680,
    1072,
)  # 按钮图标（x1, y1, x2, y2）：左边是箭头，↓ = 新的在前（缺省），↑ = 旧的在前（11-26 实机）  # 「ソート」页 4 个单选所在的高度（选项前有单选圆点，OCR 读成「•入手」，按包含找）  # 认不出按钮文字时点这里
OPEN_TRIES = 3
SCROLL_TRIES = 10
TOP_SWIPES = 6


class MemoryFilterError(RuntimeError):
    pass


class EmptySlice(MemoryFilterError):
    """这一片没有回忆：点「決定」后游戏提示「条件に当てはまるメモリーがありません」，弹窗不关（11-26 实机）。"""


def _boxes(p) -> list[tuple[str, tuple[int, int, int, int]]]:
    return [(fold(t), (int(b[0]), int(b[1]), int(b[2]), int(b[3]))) for t, _, b in p.ocr(p.shoot())]


def _center(b) -> tuple[int, int]:
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def _find(boxes, word: str, *, exact: bool = False, y_min: int = 0, y_max: int = 1280):
    w = fold(word)
    for t, b in boxes:
        cy = (b[1] + b[3]) // 2
        if y_min <= cy <= y_max and (t == w if exact else w in t):
            return b
    return None


def _sort_of(text: str) -> str | None:
    """按钮上的排序名。长的截成「プロデュースク…」，前面常带图标杂字（11-26 实机「TE プロデュースク…」「7入手」）：
    含完整名字，或含名字的前 6 个字，就算。"""
    t = fold(text)
    return next((s for s in SORTS if fold(s) in t or (len(fold(s)) > 6 and fold(s)[:6] in t)), None)


def read_state(p) -> dict:
    """选择页上当前的排序名和升降序（弹窗没开时读按钮）。"""
    img = p.shoot()
    d = sort_direction(img)
    for t, _, b in p.ocr(img):
        x, y = _center(b)
        if 1030 <= y <= 1075 and 380 <= x <= 620 and (s := _sort_of(t)):
            return {"sort": s, "dir": d}
    return {"sort": None, "dir": d}


def _popup_open(boxes) -> bool:
    return _find(boxes, "ソート・フィルタ", y_max=200) is not None


def _open(p) -> None:
    for _ in range(OPEN_TRIES):
        boxes = _boxes(p)
        if _popup_open(boxes):
            return
        pill = next((b for t, b in boxes if 1030 <= _center(b)[1] <= 1075 and _sort_of(t)), None)
        p.click(*(_center(pill) if pill else PILL))
        p.sleep(1.5)
        if _popup_open(_boxes(p)):
            return
    raise MemoryFilterError("点了排序按钮，「ソート・フィルタ」弹窗没出来")


def _tap(p, word: str, *, exact: bool = False, y_min: int = 0, y_max: int = 1280, why: str = "") -> None:
    b = _find(_boxes(p), word, exact=exact, y_min=y_min, y_max=y_max)
    if b is None:
        raise MemoryFilterError(f"弹窗里找不到「{word}」{why}")
    p.click(*_center(b))
    p.sleep(0.6)


def _scroll_top(p) -> None:
    for _ in range(TOP_SWIPES):
        p.swipe(360, 450, 360, 850)
    p.sleep(0.6)


def _section(p, header: str, labels: tuple[str, ...], stop: str | None):
    """往下滑到 header 标题和它下面的 labels 都在画面里（只认 header 和 stop 标题之间的），返回 (标题框, {label: 框})。"""
    _scroll_top(p)
    for _ in range(SCROLL_TRIES):
        boxes = _boxes(p)
        h = _find(boxes, header, exact=True, y_min=140, y_max=930)
        if h is not None:
            end = _find(boxes, stop, exact=True, y_min=h[3]) if stop else None
            y_end = end[1] if end else 930
            got = {w: _find(boxes, w, y_min=h[3], y_max=y_end) for w in labels}
            if all(got.values()):
                return h, got
        p.swipe(360, 850, 360, 550)
        p.sleep(0.6)
    raise MemoryFilterError(f"滑了 {SCROLL_TRIES} 次没找到「{header}」那一组")


def _confirm(p) -> None:
    # 弹窗的「決定」和选择页的「決定」几乎在同一位置：弹窗不在了就别点（会把选中的回忆装进编成，终审 I5）
    if not _popup_open(_boxes(p)):
        raise MemoryFilterError("要点「決定」时弹窗已经不在了，没点")
    _tap(p, "決定", y_min=1100, why="（确认）")
    p.sleep(1.0)
    boxes = _boxes(p)
    if _popup_open(boxes):
        if _find(boxes, "条件に当て") or _find(boxes, "メモリーがありません"):
            raise EmptySlice("条件に当てはまるメモリーがありません")
        raise MemoryFilterError("点了決定，弹窗没关")


def close_popup(p) -> bool:
    """弹窗还开着就点它的「キャンセル」关掉（不改设置）；返回弹窗是不是已经不在了。"""
    if not _popup_open(_boxes(p)):
        return True
    b = _find(_boxes(p), "キャンセル", y_min=1100)
    if b is None:
        return False
    p.click(*_center(b))
    p.sleep(1.0)
    return not _popup_open(_boxes(p))


def sort_direction(img) -> str | None:
    """升降序按钮的方向：desc = 新的在前（箭头尖在下），asc = 旧的在前（尖在上）；认不出 None。
    只看图标左边箭头那一列的亮像素：尖那一头的亮像素多得多（实机 28 比 10）。"""
    if img is None:
        return None
    x1, y1, _x2, y2 = DIR_ICON
    g = img[y1:y2, x1 : x1 + 22].min(axis=2) > 200  # 白色像素（三个通道都亮）
    h = g.shape[0] // 2
    top, bottom = int(g[:h].sum()), int(g[h:].sum())
    if bottom >= 10 and bottom >= 2 * top:
        return "desc"
    if top >= 10 and top >= 2 * bottom:
        return "asc"
    return None


def ensure_newest_first(p) -> None:
    """入手排序要新的在前（增量补扫读到已知就停，反了会读旧回忆就停、漏掉新的，终审 C2）：不是就点一下按钮再核对；认不出就停。"""
    d = sort_direction(p.shoot())
    if d == "desc":
        return
    if d == "asc":
        p.click(*DIR_BTN)
        p.sleep(1.5)
        if sort_direction(p.shoot()) == "desc":
            return
    raise MemoryFilterError(f"认不出 / 改不过来排序方向（{d}），停下，不扫")


def apply_slice(p, slice_key: str) -> None:
    """排序改成「入手」（新的在前），筛选只选这一片的プラン和プロデュース。先点「フィルタをリセット」：重置后全是灰勾 = 都没选、
    不筛选（11-26 实机：灰勾不是「全勾」，点一下变橙才是选中）；再在两组里各点目标那一个。"""
    plan, produce = slice_key.split(":")
    _open(p)
    _tap(p, "ソート", exact=True, y_min=1000, why="（页签）")
    _tap(p, "入手", y_min=SORT_ZONE[0], y_max=SORT_ZONE[1], why="（排序）")
    _tap(p, "フィルタ", exact=True, y_min=1000, why="（页签）")
    _tap(p, "リセット", y_min=900, y_max=1000, why="（フィルタをリセット）")
    _, got = _section(p, "プラン", tuple(PLAN_LABELS.values()), "スキルカードタイプ")
    p.click(*_center(got[PLAN_LABELS[plan]]))
    p.sleep(0.4)
    _, got = _section(p, "プロデュース", tuple(PRODUCE_LABELS.values()), "保護済みメモリー")
    p.click(*_center(got[PRODUCE_LABELS[produce]]))
    p.sleep(0.4)
    _confirm(p)
    ensure_newest_first(p)


def restore(p, state: dict, *, report: dict | None = None) -> bool:
    """排序改回 state["sort"]（不知道就不动），筛选点「フィルタをリセット」。返回按钮上的排序是不是已经恢复。"""
    _open(p)
    if state.get("sort"):
        _tap(p, "ソート", exact=True, y_min=1000, why="（页签）")
        _tap(p, state["sort"], y_min=SORT_ZONE[0], y_max=SORT_ZONE[1], why="（排序）")
    _tap(p, "フィルタ", exact=True, y_min=1000, why="（页签）")
    _tap(p, "リセット", y_min=900, y_max=1000, why="（フィルタをリセット）")
    _confirm(p)
    if report is not None:
        report["filter_reset"] = True  # 点了重置并且弹窗关了：筛选确实回到默认（#116 第二轮审查：没重置时别说重置了）
    want = state.get("dir")
    if want and sort_direction(p.shoot()) not in (want, None):
        p.click(*DIR_BTN)
        p.sleep(1.5)
    now = read_state(p)
    # 扫描前没认出排序 / 方向：恢复不了原样，也确认不了，如实返回 False（#116 审查重要 4）
    return bool(state.get("sort") and want and now["sort"] == state["sort"] and now["dir"] == want)
