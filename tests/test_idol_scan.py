"""偶像卡库存扫描（hiro.produce.idol_scan）：读一览页顶部、逐格点卡、滑到底去重、最后点「<<」不点決定。"""

from hiro.produce import idol_scan as m


def head(title, chara, rarity="SSR", plan="ロジック", effect="好印象"):
    return [(title, (150, 25, 320, 55)), (rarity, (60, 45, 110, 72)), (chara, (150, 60, 320, 88)),
            (plan, (610, 78, 690, 100)), ("おすすめ効果", (500, 108, 650, 130)), (effect, (540, 150, 650, 178))]  # fmt: skip


def test_read_header():
    got = m.read_header(head("世界一可愛い私", "藤田 ことね"))
    assert got == {
        "title": "世界一可愛い私",
        "character": "藤田ことね",
        "rarity": "SSR",
        "plan": "logic",
        "effect": "好印象",
    }
    assert m.read_header([("L", (640, 80, 660, 95)), ("L", (660, 80, 680, 95))]) is None  # 详情页的单字图标
    assert m.read_header([("世界一可愛い私", (150, 25, 320, 55))]) is None  # 只有卡名没有角色：不猜


def test_scan_dedupes_until_two_idle_screens_then_backs_out():
    cards = [("A卡", "有村麻央"), ("B卡", "姫崎莉波"), ("C卡", "葛城リーリヤ")]
    state = {"screen": 0, "last": None}
    taps, swipes = [], []

    def tap(x, y):
        taps.append((x, y))
        if (x, y) != m.BACK:
            # 第 0 屏：前 3 格是三张卡，其余格重复第 3 张；之后的屏都是已见过的卡
            i = m.COL_X.index(x) + 4 * m.ROW_Y.index(y)
            state["last"] = cards[min(i, 2)] if state["screen"] == 0 else cards[i % 3]

    def swipe(*a):
        swipes.append(a)
        state["screen"] += 1

    out = m.scan(
        lambda: None, tap, swipe, lambda img: head(*state["last"]) if state["last"] else [], sleep=lambda s: None
    )
    assert [c["title"] for c in out["idols"]] == ["A卡", "B卡", "C卡"] and out["complete"] is True
    assert taps[-1] == m.BACK and len(swipes) == 2  # 第 1、2 屏没有新卡：两次空屏就停，不再滑


def test_swipe_stays_inside_the_grid_and_overlaps():
    """11-23 账号 2 实测：原来的滑动起点 (360,1000) 压在「プラン ALL」筛选按钮上，列表不动，只读到第一屏 8 张就当成到底。
    滑动要在卡片网格里（避开 y≈975–1025、x≥205 的筛选按钮），而且一次挪动（实测约为手指距离的 1.5 倍）小于点击覆盖的高度，屏与屏有重叠。"""
    x1, y1, _x2, y2 = m.SWIPE
    in_filter_bar = 205 <= x1 <= 695 and 975 <= y1 <= 1025
    assert not in_filter_bar and m.GRID_TOP < y2 < y1 < m.GRID_BOTTOM
    assert (y1 - y2) * 1.5 < (m.ROW_Y[-1] - m.ROW_Y[0])
