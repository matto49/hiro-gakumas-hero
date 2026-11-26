"""编成位现状（hiro.produce.formation_survey）：切模式、翻 1–20 号，记每个编成几格有东西；只翻页不点格子。"""

from hiro.produce import formation_menu as fm
from hiro.produce import formation_survey as m


def test_survey_reads_every_set(monkeypatch):
    seen = []
    monkeypatch.setattr(fm.Menu, "__init__", lambda self, dev: None)
    monkeypatch.setattr(fm.Menu, "open", lambda self: seen.append("open"))
    monkeypatch.setattr(fm.Menu, "ensure_mode", lambda self, mode: seen.append(mode))
    monkeypatch.setattr(fm.Menu, "ensure_tab", lambda self, tab: seen.append(tab))
    cur = {"n": 0}
    monkeypatch.setattr(fm.Menu, "ensure_set", lambda self, n: cur.__setitem__("n", n))
    full = [{"support_card_id": "s_card-3-0001", "level": 60}] * 5
    empty = [{"support_card_id": None, "level": None}] * 5
    monkeypatch.setattr(
        "hiro.produce.formation_writer._settled_step2",
        lambda dev, read, view: {"slots": full if cur["n"] in (4, 20) else empty},
    )
    icons = lambda img: [{"empty": cur["n"] != 5, "known": True, "card": "men-1"}] * 4  # noqa: E731

    class Dev:
        def shoot(self):
            return None

    out = m.survey(Dev(), "hif", read_icons=icons)
    assert seen == ["open", "hif", "support", "memory"]
    sup = {r["set"]: r["filled"] for r in out["sets"] if r["kind"] == "support"}
    mem = {r["set"]: r["filled"] for r in out["sets"] if r["kind"] == "memory"}
    assert len(sup) == 20 and sup[4] == 5 and sup[20] == 5 and sup[1] == 0
    assert mem[5] == 4 and mem[1] == 0 and out["complete"] is True and out["mode"] == "hif"


def test_survey_stop_at_blank_reads_until_first_empty(monkeypatch):
    """N33（G1 第 3 轮 11-26）：开局前只要一个空位，读到第一个空的就停；全满的那样照旧读完。"""
    seen = []
    monkeypatch.setattr(fm.Menu, "__init__", lambda self, dev: None)
    monkeypatch.setattr(fm.Menu, "open", lambda self: None)
    monkeypatch.setattr(fm.Menu, "ensure_mode", lambda self, mode: None)
    monkeypatch.setattr(fm.Menu, "ensure_tab", lambda self, tab: None)
    cur = {"n": 0}
    monkeypatch.setattr(fm.Menu, "ensure_set", lambda self, n: (cur.__setitem__("n", n), seen.append(n)))
    full = [{"support_card_id": "s_card-3-0001", "level": 60}] * 5
    monkeypatch.setattr("hiro.produce.formation_writer._settled_step2", lambda dev, read, view: {"slots": full})
    icons = lambda img: [{"empty": cur["n"] >= 3, "known": True, "card": "men-1"}] * 4  # noqa: E731

    class Dev:
        def shoot(self):
            return None

    out = m.survey(Dev(), "hajime", read_icons=icons, stop_at_blank=True)
    sup = [r for r in out["sets"] if r["kind"] == "support"]
    mem = [r for r in out["sets"] if r["kind"] == "memory"]
    assert len(sup) == 20 and all(r["filled"] == 5 for r in sup)  # 支援卡全满：读完 20 组
    assert [r["set"] for r in mem] == [1, 2, 3] and mem[-1]["filled"] == 0  # 回忆读到 3 号空的就停
    assert out["complete"] is False and out["stopped_at_blank"] is True
    assert len(seen) == 23
