"""开局第 2 步租借格按编成价值挑（有开局编成方案时）：support_select.rental_scorer + rental.scored_best / _pick_once。"""

from pathlib import Path

from hiro.brain import support_select as ss
from hiro.state.context import catalog

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "support"


def _have_master() -> bool:
    try:
        catalog().one("SupportCard", next(r["id"] for r in catalog().rows("SupportCard")))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _have_master(), reason="需要主数据（scripts/setup.sh 下载）")


def _max(card_id):  # noqa: D103
    c = catalog()
    card = c.one("SupportCard", card_id)
    lim = sorted(c.by_id("SupportCardLevelLimit", card["supportCardLevelLimitId"]), key=lambda r: r["levelLimit"])[-1]
    return {"support_card_id": card_id, "level": lim["levelLimit"]}


def _pool(attr, n, skip=0):
    out = []
    for r in catalog().rows("SupportCard"):
        if (
            r["rarity"] == "SupportCardRarity_Ssr"
            and ss.ATTR_OF.get(r["type"]) == attr
            and r["planType"] in ("ProducePlanType_Common", ss.PLAN)
        ):
            out.append(_max(r["id"]))
    return out[skip : skip + n]


def _row(e, player):
    return {
        "player": player,
        "card": {"support_card_id": e["support_card_id"], "level": e["level"], "name": e["support_card_id"]},
        "center": [360, 600],
    }


OWN = (_pool("Da", 3) + _pool("Vo", 1) + _pool("Vi", 1)) if _have_master() else []


def test_scorer_matches_choose_rental():
    rentals = _pool("Da", 6, skip=3)
    score = ss.rental_scorer(OWN, "Da", "ProducePlanType_Plan2")
    vals = {e["support_card_id"]: score(_row(e, i)) for i, e in enumerate(rentals)}
    assert all(v is not None for v in vals.values())
    best = ss.choose_rental(rentals, OWN)
    assert max(vals, key=vals.get) == best["support_card_id"]
    assert round(max(vals.values())) == best["formation_value"]


def test_scorer_rejects_wrong_attr_and_unknown():
    score = ss.rental_scorer(OWN, "Da", "ProducePlanType_Plan2")
    assert score(_row(_pool("Vi", 1, skip=2)[0], "x")) is None  # 补不成 4 张 Da
    assert score({"card": {"support_card_id": None, "level": 60}}) is None
    assert score({"card": None}) is None


@pytest.mark.skipif(not (FIX / "05-rental-support-list.png").is_file(), reason="需要实机截图 tests/fixtures/support/")
@pytest.mark.sim_master  # 帧里的 s_card-3-0099 不在 KAA game.db，要退回模拟器主数据查（vendor/gakumas-player/scripts/fetch_master.py）
def test_scored_best_on_real_rental_frame():
    from hiro.produce.rental import scored_best
    from hiro.vision.support_screens import read_rental_list

    rows = read_rental_list(cv2.imread(str(FIX / "05-rental-support-list.png")))[
        "rows"
    ]  # 两行都是 Vi「もうすぐ本番ですね」，第三行被盖住
    da = ss.rental_scorer(OWN, "Da", "ProducePlanType_Plan2")
    assert scored_best(rows, da) is None  # 主属性 Da 时这页没有能租的
    own_vi = _pool("Vi", 3) + _pool("Vo", 1) + _pool("Da", 1)
    vi = ss.rental_scorer(own_vi, "Vi", "ProducePlanType_Plan2")
    hit = scored_best(rows, vi)
    assert hit is not None and hit[0]["card"]["attr"] == "Vi" and not hit[0].get("covered")
    assert scored_best(rows, vi, exclude={"もうすぐ本番ですね"}) is None


def test_pick_once_scans_all_then_picks_best(monkeypatch):
    """有 scorer 时不在第一张满级卡处停：看完全部屏，回到价值最高那行所在的屏再点。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    rentals = _pool("Da", 6, skip=3)
    score = ss.rental_scorer(OWN, "Da", "ProducePlanType_Plan2")
    ranked = sorted(rentals, key=lambda e: score(_row(e, "")))
    worst, best = ranked[0], ranked[-1]
    if score(_row(worst, "")) == score(_row(best, "")):
        pytest.skip("素材插件没有支援卡技能数据：候选价值都一样，比不出先后")
    screens = [[_row(worst, "A")], [_row(best, "B")], [_row(best, "B")]]  # 第 2 屏是最好的；第 3 屏和第 2 屏一样 = 到底
    state = {"screen": 0, "clicked": []}

    class Dev:
        def click(self, *a):
            state["clicked"].append(a)

        def swipe(self, *a):
            if a[1] > a[3]:  # 往下滑一屏
                state["screen"] = min(state["screen"] + 1, len(screens) - 1)
            else:  # 回到顶部
                state["screen"] = 0

        def screenshot(self):
            return None

    monkeypatch.setattr(rental, "device", Dev())
    monkeypatch.setattr(rental, "_ensure_plan_fit", lambda: None)
    monkeypatch.setattr(rental, "progress", lambda why: None)
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr(rental, "_rows", lambda: {"rows": screens[state["screen"]], "controls": {"decide": [497, 1157]},
                                                    "pager": {"page": 1, "pages": 1}})  # fmt: skip
    assert rental._pick_once("Da", set(), score) == best["support_card_id"]
    assert state["clicked"][-2] == (360, 600) and state["clicked"][-1] == (497, 1157)  # 点那一行、再点「決定」


def test_catalog_falls_back_to_sim_master(monkeypatch):
    """game.db 里没有的新卡（s_card-3-0099）：模拟器主数据构建过时退回查它（2026-11-10）。"""
    import sqlite3

    from hiro.state import catalog as cat

    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "SupportCard" (id TEXT, planType TEXT)')

    class _Sim:
        def by_id(self, table, key):
            return [{"id": key, "planType": "ProducePlanType_Common"}] if key == "s_card-new" else []

    monkeypatch.setattr(cat, "_sim_catalog", lambda: _Sim())
    c = cat.Catalog(db)
    assert c.one("SupportCard", "s_card-new")["planType"] == "ProducePlanType_Common"
    assert c.by_id("NoSuchTable", "x") == []
    monkeypatch.setattr(cat, "_sim_catalog", lambda: None)
    assert c.by_id("SupportCard", "s_card-new") == []  # 没有模拟器主数据：照旧查不到


def test_pick_once_finds_best_row_when_return_swipe_falls_short(monkeypatch):
    """回到最佳行时滑动距离和扫描时不一样（11-16 Mac 实机：滑同样次数没回到那一屏 → 取消、不开局）：逐屏往下找到那位玩家再点。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    worst = {"support_card_id": "s_card-worst", "level": 60}
    best = {"support_card_id": "s_card-best", "level": 60}

    def score(row):  # 固定打分，不依赖素材插件的支援卡技能数据
        return 2.0 if row["player"] == "B" else 1.0

    screens = [[_row(worst, "A")], [_row(worst, "C")], [_row(best, "B")], [_row(best, "B")]]
    state = {"screen": 0, "clicked": [], "returning": False, "lag": 0}

    class Dev:
        def click(self, *a):
            state["clicked"].append(a)

        def swipe(self, *a):
            if a[1] > a[3]:
                if state["lag"]:
                    state["lag"] -= 1  # 回去时第一次滑动没走满一屏
                    return
                state["screen"] = min(state["screen"] + 1, len(screens) - 1)
            else:
                state["screen"] = 0
                state["lag"] = 1

        def screenshot(self):
            return None

    monkeypatch.setattr(rental, "device", Dev())
    monkeypatch.setattr(rental, "_ensure_plan_fit", lambda: None)
    monkeypatch.setattr(rental, "progress", lambda why: None)
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr(rental, "_to_top", lambda: Dev().swipe(0, 0, 0, 1))
    monkeypatch.setattr(rental, "_rows", lambda: {"rows": screens[state["screen"]], "controls": {"decide": [497, 1157]},
                                                    "pager": {"page": 1, "pages": 1}})  # fmt: skip
    assert rental._pick_once("Da", set(), score) == best["support_card_id"]


def test_pick_once_refreshes_list_when_nothing_fits(monkeypatch):
    """两页都没有能补成 4-2 的卡：点刷新换一批再找（11-16 Mac 实机：租借列表换了一批，没有 Da 就停了）。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    vi = {"player": "V", "card": {"support_card_id": "s_card-vi", "level": 60, "name": "vi"}, "center": [360, 600]}
    da = {"player": "D", "card": {"support_card_id": "s_card-da", "level": 60, "name": "da"}, "center": [360, 600]}
    state = {"batch": 0, "clicked": []}
    ctrl = {"decide": [497, 1157], "cancel": [220, 1157], "refresh": [400, 1071]}

    class Dev:
        def click(self, *a):
            state["clicked"].append(a)
            if list(a) == ctrl["refresh"]:
                state["batch"] = 1

        def swipe(self, *a):
            pass

        def screenshot(self):
            return None

    monkeypatch.setattr(rental, "device", Dev())
    monkeypatch.setattr(rental, "_ensure_plan_fit", lambda: None)
    monkeypatch.setattr(rental, "progress", lambda why: None)
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr(rental, "_to_top", lambda: None)
    monkeypatch.setattr(
        rental,
        "_rows",
        lambda: {"rows": [[vi], [da]][state["batch"]], "controls": ctrl, "pager": {"page": 1, "pages": 1}},
    )
    score = lambda row: 1.0 if row["player"] == "D" else None  # noqa: E731  只有 Da 能补成 4-2
    assert rental._pick_once("Da", set(), score) == "da"
    assert tuple(ctrl["refresh"]) in state["clicked"] and state["clicked"][-1] == tuple(ctrl["decide"])


class FakeRentalList:
    """租借列表：rows 一长串，一屏露 visible 行；往下拖（SWIPE）挪一行，往上拖挪一行；pager 是详情区的「1/2」（翻了列表不变）。
    after_scan(rows)：第一次往上拖（扫完开始往回找）时改读数（模拟回去时某行读不出来）。"""

    def __init__(self, rental, rows, visible=2, pages=1, after_scan=None):
        self.rental, self.rows, self.visible, self.pages = rental, rows, visible, pages
        self.pos, self.page, self.clicked, self.after_scan, self.back = 0, 1, [], after_scan, False

    def click(self, *a):
        self.clicked.append(a)
        if list(a) == [485, 533]:
            self.page = min(self.pages, self.page + 1)
        elif list(a) == [233, 533]:
            self.page = max(1, self.page - 1)

    def swipe(self, x1, y1, x2, y2, *a, **k):
        if y1 > y2:
            self.pos = min(self.pos + 1, len(self.rows) - self.visible)
        else:
            if not self.back and self.after_scan is not None:
                self.rows = self.after_scan(self.rows)
            self.back = True
            self.pos = max(self.pos - 1, 0)

    def screenshot(self):
        return None

    def read(self):
        return {
            "rows": self.rows[self.pos : self.pos + self.visible],
            "controls": {"decide": [497, 1157], "cancel": [220, 1157]},
            "pager": {"page": self.page, "pages": self.pages, "prev": [233, 533], "next": [485, 533]},
        }

    def install(self, monkeypatch):
        r = self.rental
        monkeypatch.setattr(r, "device", self)
        monkeypatch.setattr(r, "_ensure_plan_fit", lambda: None)
        monkeypatch.setattr(r, "progress", lambda why: None)
        monkeypatch.setattr(r, "_sleep", lambda s: None)
        monkeypatch.setattr(r, "_rows", self.read)


def _named(player, name, center_y=600):
    return {
        "player": player,
        "card": {"support_card_id": name, "level": 60, "name": name, "breaks": 4},
        "center": [360, center_y],
    }


def test_pick_once_long_list_best_at_top_with_detail_pager(monkeypatch):
    """11-17 Mac p15：最好的 leaf 在列表顶部，「1/2」是详情区的翻页（翻过去列表一样，又扫一遍停在底部），
    回去时固定滑 10 次回不到顶 → 没找到 → 退路也没找到 → 停机。现在从底部往上逐屏找，看到 leaf 就点。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    rows = [_named(f"p{i}", f"c{i}") for i in range(20)]
    rows[1] = _named("leaf", "もうすぐ本番ですね")
    fake = FakeRentalList(rental, rows, pages=2)
    fake.install(monkeypatch)
    score = {"leaf": 1531.0}
    assert rental._pick_once("Vi", set(), lambda r: score.get(r["player"], 1000.0)) == "もうすぐ本番ですね"
    assert fake.clicked[-1] == (497, 1157)
    assert fake.clicked.count((485, 533)) == 1  # 翻过一次「页」，发现列表没变就不再扫


def test_pick_once_falls_back_to_best_seen_on_return(monkeypatch):
    """回去时原玩家那一行找不到（11-16 Mac 实机 p6：扫描时读到的行回去时读不出来）：改租往回找途中看到的最好一张，
    按那一张自己的玩家 / 卡去找（p15：原来退路还按原玩家找，自相矛盾）。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    rows = [_named("A", "a"), _named("B", "b"), _named("X", "x"), _named("C", "c"), _named("Y", "y"), _named("Z", "z")]

    def hide_b(rs):
        return [dict(r, player=None, card=None) if r["player"] == "B" else r for r in rs]

    fake = FakeRentalList(rental, rows, after_scan=hide_b)
    fake.install(monkeypatch)
    score = {"A": 1.0, "B": 3.0, "C": 2.0}
    assert rental._pick_once("Da", set(), lambda r: score.get(r["player"], 0.5)) == "c"
    assert fake.clicked[-1] == (497, 1157)


def test_pick_once_finds_best_row_when_player_name_reads_differently(monkeypatch):
    """回去找时玩家名小字 OCR 差一个字（11-17 Mac：chamiria 那张价值 1336 没找到，退成 1228）：按卡的身份（卡名 + 等级 + 突破）也认。"""
    pytest.importorskip("kotonebot")
    from hiro.produce import rental

    worst = {"support_card_id": "s_card-worst", "level": 60}
    best = {"support_card_id": "s_card-best", "level": 60}

    def score(row):
        return 2.0 if row["card"]["support_card_id"] == "s_card-best" else 1.0

    scan = [[_row(worst, "A")], [_row(best, "chamiria")], [_row(worst, "C")]]
    back = [[_row(worst, "A")], [_row(best, "chamirla")], [_row(worst, "C")]]  # 第二遍玩家名读错一个字
    state = {"screen": 0, "pass": scan, "clicked": []}

    class Dev:
        def click(self, *a):
            state["clicked"].append(a)

        def swipe(self, *a):
            if a[1] > a[3]:
                state["screen"] = min(state["screen"] + 1, len(scan) - 1)
            else:
                state["screen"], state["pass"] = 0, back

        def screenshot(self):
            return None

    monkeypatch.setattr(rental, "device", Dev())
    monkeypatch.setattr(rental, "_ensure_plan_fit", lambda: None)
    monkeypatch.setattr(rental, "progress", lambda why: None)
    monkeypatch.setattr(rental, "_sleep", lambda s: None)
    monkeypatch.setattr(rental, "_to_top", lambda: Dev().swipe(0, 0, 0, 1))
    monkeypatch.setattr(rental, "_rows", lambda: {"rows": state["pass"][state["screen"]], "controls": {"decide": [497, 1157]},
                                                    "pager": {"page": 1, "pages": 1}})  # fmt: skip
    assert rental._pick_once("Da", set(), score) == best["support_card_id"]
