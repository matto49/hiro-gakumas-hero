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
