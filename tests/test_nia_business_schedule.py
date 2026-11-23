"""协调 11-17（逆向 10 节）三件：② N.I.A 营业场所的花费类型 / 数值 / 大成功概率；③ N.I.A PRO 简单模式的 N.I.Aキー（黄緑）；
① 日程列表采样探针（开关 HIRO_SCHEDULE_PROBE，默认关）。实机帧在私有 fixtures pages/nia-sales-place*。"""

from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import numpy as np
import pytest

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _read(name):
    from hiro.vision.nia import read_nia_page
    from hiro.vision.ocr import screen_texts

    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    img = cv2.imread(str(p))
    return read_nia_page(screen_texts(img), img)


def _costs(places):
    return [(p["cost_type"], p["cost"], p["hp_cost"], p["p_cost"]) for p in places]


def test_sales_place_cost_types():
    _, f = _read("nia-sales-place.png")
    assert _costs(f["places"]) == [("stamina", 5, 5, 0), (None, 0, 0, 0), ("stamina", 5, 5, 0)]
    assert all("great_success_pct" not in p for p in f["places"])  # 没展开：看不到大成功


@pytest.mark.parametrize(
    ("name", "i", "pct", "votes"),
    [("nia-sales-place-camp-expanded.png", 0, 90, 840), ("nia-sales-place-expanded.png", 2, 100, 720)],
)
def test_expanded_great_success(name, i, pct, votes):
    _, f = _read(name)
    assert f["selected"] == i
    assert (f["places"][i]["great_success_pct"], f["places"][i]["great_success_votes"]) == (pct, votes)
    assert all("great_success_pct" not in p for k, p in enumerate(f["places"]) if k != i)


def _pill(hue: int, n: int = 600):
    """合成一条药丸：HEART_X 里放 n 个指定色相的高亮像素。"""
    from hiro.vision.nia import HEART_X

    hsv = np.zeros((60, 720, 3), np.uint8)
    hsv[..., 2] = 120
    ys, xs = np.unravel_index(np.arange(n), (40, HEART_X[1] - HEART_X[0]))
    hsv[ys + 10, xs + HEART_X[0]] = (hue, 220, 240)
    return hsv


def test_cost_icon_by_color():
    from hiro.vision.nia import _cost_icon

    assert _cost_icon(_pill(60), 0, 59) == "stamina"  # 爱心：黄绿渐变，绿占多数
    assert _cost_icon(_pill(25), 0, 59) == "produce_point"  # P 图标：黄色
    assert _cost_icon(_pill(110), 0, 59) is None  # 认不出：不定类型
    assert _cost_icon(_pill(60, n=50), 0, 59) is None  # 没有图标（消費なし）


# ---------------------------------------------------------------- ③ N.I.Aキー（黄緑）
def _obs(seq, page, fields):
    from hiro.contracts import Observation, PageObs

    return Observation(seq=seq, t=0.0, page=PageObs(name=page, area="produce", stable_frames=1), fields=fields)


def test_easy_key_seen_on_acquire_even_while_debouncing():
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce

    st = GameState(produce={"mode": "produce-004"})
    st = reduce(
        st, _obs(3, "ACQUIRE", {"kind": "pitem", "pitem_id": "pitem_00-3-330-0", "pitem_ids": ["pitem_00-3-330-0"]})
    )
    assert st.produce["nia_easy_key"] is True and st.sources["produce.nia_easy_key"].seq == 3


def test_easy_key_from_exam_pitems_and_absent_otherwise():
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce

    st = reduce(
        GameState(produce={"mode": "produce-004"}), _obs(1, "HIF_EXAM", {"pitems": [{"pitem_id": "pitem_00-3-330-0"}]})
    )
    assert st.produce["nia_easy_key"] is True
    st = reduce(
        GameState(produce={"mode": "produce-004"}),
        _obs(1, "ACQUIRE", {"kind": "pitem", "pitem_id": "pitem_02-3-102-0"}),
    )
    assert "nia_easy_key" not in st.produce  # 没看到不写（不断言没有）


# ---------------------------------------------------------------- ① 日程列表探针
def _frames(*vals):
    return [np.full((1280, 720, 3), v, np.uint8) for v in vals]


def _probe(tmp_path, monkeypatch, shots, at_action, reads):
    """shots：依次截到的帧；reads：read_schedule_list 依次的返回（替身）。"""
    from hiro.produce import schedule_probe as sp

    seq = iter(shots)
    last = {"img": shots[0]}

    def shoot():
        last["img"] = next(seq, last["img"])
        return last["img"]

    rd = iter(reads)
    clicks, swipes, t = [], [], [0.0]
    monkeypatch.setattr(sp, "_at_action_select", at_action)
    r = sp.probe(tmp_path / "p", shoot=shoot, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: swipes.append(a),
                 sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], read=lambda img: next(rd, None))  # fmt: skip
    return r, clicks, swipes


def test_probe_not_opened_waits_then_does_nothing(tmp_path, monkeypatch):
    """点了以后至少等 OPEN_MIN_S、停稳了还认不出日程列表：还在选行动页就什么也不做（不点 ✕）。"""
    r, clicks, swipes = _probe(
        tmp_path, monkeypatch, _frames(100, 100, 100, 100, 100, 100, 100), lambda img: True, [None]
    )
    assert r["opened"] is False and clicks == [(112, 92)] and swipes == [] and (tmp_path / "p" / "probe.json").exists()


def _screen(weeks, cur=None):
    return {"weeks": {w: {"kind": "normal", "options": [{"option": "class", "attr": None}], "faded": [False]} for w in weeks},
            "current_week": cur}  # fmt: skip


def test_probe_reads_both_directions_and_closes_with_x(tmp_path, monkeypatch):
    """先往后面的周滑、再往前面的周滑，拼全整局；点 ✕ (360,1190) 回到选行动页。"""
    from hiro.produce import schedule_probe as sp
    from hiro.vision.schedule_list import CLOSE

    reads = iter([_screen([4, 5, 6], 4), _screen([6, 7, 8]), _screen([8, 9]), _screen([8, 9]),  # 往后滑：到 9 不再有新的
                  _screen([2, 3, 4]), _screen([1, 2])])  # 往前滑：看到第 1 周就停  # fmt: skip
    img = np.full((1280, 720, 3), 100, np.uint8)
    clicks, swipes, t = [], [], [0.0]
    monkeypatch.setattr(sp, "_at_action_select", lambda im: bool(clicks) and clicks[-1] == CLOSE)
    got = sp.probe(tmp_path / "q", shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: swipes.append(a),
                   sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], read=lambda im: next(reads, None))  # fmt: skip
    sch = got["schedule"]
    assert got["opened"] and got["closed"] and clicks == [sp.ENTRY, CLOSE]
    assert [s_[:4] for s_ in swipes] == [(360, 450, 360, 950)] * 3 + [(360, 950, 360, 450)] * 2
    assert [e["week"] for e in sch["weeks"]] == list(range(1, 10)) and sch["current_week"] == 4 and sch["total"] == 9
    assert [e["done"] for e in sch["weeks"]][:4] == [True, True, True, False]


def test_probe_cannot_close_stops(tmp_path, monkeypatch):
    from hiro.loop.errors import ResumeBlocked

    with pytest.raises(ResumeBlocked):
        _probe(tmp_path, monkeypatch, _frames(*([100] * 40)), lambda img: False, [_screen([4], 4)])


def test_probe_resume_uses_existing_schedule(monkeypatch):
    """续跑：局面里已经有 produce.schedule（上一段 trace 打底）就不再点开。"""
    from hiro.produce import schedule_probe
    from hiro.produce.hif import HifCore

    monkeypatch.setenv("HIRO_SCHEDULE_PROBE", "1")
    called = []
    monkeypatch.setattr(schedule_probe, "probe", lambda *a, **k: called.append(1))

    class Core:
        _schedule_probed = False
        s = NS(state=NS(produce={"mode": "produce-003", "schedule": {"weeks": []}}))

    HifCore._maybe_probe_schedule(Core())
    assert called == []


def test_probe_off_by_default(monkeypatch):
    from hiro.produce import schedule_probe
    from hiro.produce.hif import HifCore

    monkeypatch.delenv("HIRO_SCHEDULE_PROBE", raising=False)
    called = []
    monkeypatch.setattr(schedule_probe, "probe", lambda *a, **k: called.append(1))

    class Core:
        _schedule_probed = False
        s = NS(state=NS(produce={"mode": "produce-004"}))

    HifCore._maybe_probe_schedule(Core())
    assert called == []
    monkeypatch.setenv("HIRO_SCHEDULE_PROBE", "1")
    Core.s = NS(state=NS(produce={"mode": "produce-007"}))  # H.I.F 不探
    HifCore._maybe_probe_schedule(Core())
    assert called == []


def test_probe_waits_for_late_list(tmp_path, monkeypatch):
    """协调 11-18 N.I.A MASTER：开局后第一次选行动，点了 8 秒后列表才弹出；原来 4 秒就判没打开，列表盖住选行动页。
    停稳后认不出再盯 LATE_OPEN_S 秒，弹出来照常读、滑、关。"""
    from hiro.produce import schedule_probe as sp
    from hiro.vision.schedule_list import CLOSE

    t, clicks = [0.0], []
    img = np.full((1280, 720, 3), 100, np.uint8)

    def read(im):
        return _screen([1, 2, 3], 1) if t[0] >= 8.0 else None

    monkeypatch.setattr(sp, "_at_action_select", lambda im: bool(clicks) and clicks[-1] == CLOSE)
    got = sp.probe(tmp_path / "late", shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), swipe=lambda *a: None,
                   sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], read=read)  # fmt: skip
    assert got["opened"] and got["closed"] and clicks == [sp.ENTRY, CLOSE] and got["schedule"]["current_week"] == 1


def test_nia_schedule_list_icons():
    """N.I.A 实拍（MASTER 第 1 周，列表晚弹出那一帧）：営業 紫、差し入れ 橙、おでかけ、自主レッスン 三色。"""
    from hiro.vision.schedule_list import read_schedule_list

    p = Path(__file__).parent / "fixtures" / "schedule_list" / "nia-schedule-list-late.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 schedule_list/nia-schedule-list-late.jpg")
    r = read_schedule_list(cv2.imread(str(p)))
    got = {w: [(o["option"], o["attr"]) for o in e["options"]] for w, e in r["weeks"].items()}
    lessons = [("training", "Vo"), ("training", "Da"), ("training", "Vi")]
    assert got == {
        1: lessons,
        2: [("sales", None)],
        3: [("outing", None), ("present", None)],
        4: lessons,
        5: [("sales", None)],
    }
    assert r["current_week"] == 1
