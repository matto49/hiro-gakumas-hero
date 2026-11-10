# 来源：digital-hiro tests/test_rental_value.py（288c548）；开源版：没有主数据 / 截图样例时跳过
"""开局第 2 步租借格按编成价值挑（有开局编成方案时）：support_select.rental_scorer + rental.scored_best / _pick_once。"""

from pathlib import Path

import cv2
import pytest

from hiro.brain import support_select as ss
from hiro.state.context import catalog

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
        if r["rarity"] == "SupportCardRarity_Ssr" and ss.ATTR_OF.get(r["type"]) == attr and r["planType"] in ("ProducePlanType_Common", ss.PLAN):
            out.append(_max(r["id"]))
    return out[skip : skip + n]


def _row(e, player):
    return {"player": player, "card": {"support_card_id": e["support_card_id"], "level": e["level"], "name": e["support_card_id"]}, "center": [360, 600]}


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

    rows = read_rental_list(cv2.imread(str(FIX / "05-rental-support-list.png")))["rows"]  # 两行都是 Vi「もうすぐ本番ですね」，第三行被盖住
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
    monkeypatch.setattr(rental.time, "sleep", lambda s: None)
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
