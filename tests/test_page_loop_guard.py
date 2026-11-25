"""页内循环守卫（实机帧在私有 fixtures）：DMM 第 12 天相談离开后，画面已经是选拔加载页 / 考试说明，相談循环沿用旧货架又点了 3 次。
现在 observe_complete 用尽后关键字段仍读不到（None / 空），就不指定页面重新分类；已经不在这一页就退出循环交回主循环。
还判成相談、但货架读不出（离开后的人物画面仍被分类成 CONSULT）：不拿旧货架决策，「終了」也不在人物画面上盲点。"""

from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision, GameState, TraceWriter
from hiro.produce import shop

import pytest
from test_shop_exec import FakeConsult, frame


def _session(tmp_path, img):
    from hiro.loop.session import Session
    from hiro.vision.offline import load

    load(img)
    return Session(TraceWriter(root=tmp_path, name="guard"), shoot=lambda: img)


@pytest.mark.parametrize(
    "name, page", [("exam_loading", "TRANSITION"), ("consult_end_scene", None), ("reroll_left1", None)]
)
def test_left_page_reclassifies_only_when_fields_missing(tmp_path, name, page):
    s = _session(tmp_path, frame(name))
    obs = s.observe(scene="CONSULT")
    assert (obs.fields.get("shelf") is None) is (
        name != "reroll_left1"
    )  # 货架不完整（加载页 / 人物画面）给 None，不给幻影格子
    assert s.left_page("CONSULT", obs, ("shelf",)) == page


def _consult(monkeypatch, left_page, shelf=None):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif

    obs = NS(fields={"shelf": shelf}, page=NS(name="CONSULT"))
    calls, decided = [], []
    sess = NS(state=GameState(page_fields={"shelf": [{"index": 7, "price": 50}]}, produce={"produce_point": 305}),  # 沿用的旧货架
              observe_complete=lambda *a, **k: obs, observe=lambda **k: obs, left_page=left_page, record=lambda *a, **k: None,
              recover=lambda *a, **k: None)  # fmt: skip
    h = hif.HifCore.__new__(hif.HifCore)
    h.s = sess
    h.deciders = NS(
        decide=lambda page, st: (
            decided.append(page) or Decision(Action(ActionKind.SHOP_BUY, {"index": 7}), "买", "rule", 0.9)
        )
    )
    h._consult_open_dialog = lambda d, done: d
    h._choose_dialog_card, h._deck_size = None, lambda: None
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(
        "hiro.produce.shop.execute_shop", lambda a, *x, **k: calls.append(a.kind) or NS(ok=True, error=None)
    )
    h.on_consult(None)
    return calls, decided


def test_consult_exits_when_screen_left(monkeypatch):
    calls, decided = _consult(monkeypatch, lambda scene, obs, req: "TRANSITION")
    assert calls == [] and decided == []  # 不决策、不点，也不点「終了」


def test_consult_no_stale_shelf_while_still_consult(monkeypatch):
    calls, decided = _consult(monkeypatch, lambda scene, obs, req: None)
    assert decided == [] and calls == [ActionKind.SHOP_END]  # 只剩收尾的 ShopEnd（它自己核对画面）


def test_interval_exits_when_screen_left(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif

    obs = NS(fields={"interval": None}, page=NS(name="HIF_ROUND_INTERVAL"))
    executed, ended = [], []
    sess = NS(state=GameState(page_fields={"interval": {"shelf": []}}, produce={}), observe_complete=lambda *a, **k: obs,
              left_page=lambda scene, o, req: "HIF_EXAM", record=lambda *a, **k: None, recover=lambda *a, **k: None)  # fmt: skip
    h = hif.HifCore.__new__(hif.HifCore)
    h.s, h.rec, h.profile = sess, NS(log=lambda *a, **k: None), NS()
    h.deciders = NS(decide=lambda *a, **k: pytest.fail("离开轮间后不该再决策"))
    h._deck_size = lambda: None
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr("hiro.produce.interval.execute_interval", lambda *a, **k: executed.append(a) or NS(ok=True))
    h.on_hif_round_interval(NS(end=lambda: ended.append(1)))
    assert executed == [] and ended == []


def test_end_does_not_tap_on_character_scene():
    fake = FakeConsult(start="consult_end_scene")
    res = shop.execute_shop(Action(ActionKind.SHOP_END, {}), {}, shoot=fake.shoot, click=fake.click, clock=fake.clock, sleep=fake.sleep,
                            calib=False)  # fmt: skip
    assert res.ok and fake.clicks == []
