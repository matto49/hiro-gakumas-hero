"""日程列表（初 MASTER 第 4 周实拍 9 屏，协调 11-18 手动滑动截全 18 周）：逐周可选行动、已做 / 选了什么、考试周场次和分数线。
真值按画面逐周核对（和 ww-初M 第 3 型一致）。帧在私有 fixtures schedule_list/。"""

from pathlib import Path

import cv2
import pytest

D = Path(__file__).with_name("fixtures") / "schedule_list"
NAMES = ("0", "up1", "up2", "up3", "dn1", "dn2", "dn3", "dn4", "dn5")

TRUTH = {  # 周 → (种类, 选项)
    1: ("normal", ["class"]), 2: ("normal", ["class"]), 3: ("normal", ["outing", "allowance"]), 4: ("normal", ["shop"]),
    5: ("normal", ["training/Vo", "training/Da", "training/Vi"]), 6: ("normal", ["shop", "allowance"]), 7: ("oikomi", ["training"]),
    8: ("exam", []), 9: ("normal", ["outing", "shop", "allowance"]), 10: ("normal", ["class"]),
    11: ("normal", ["training/Vo", "training/Da", "training/Vi"]), 12: ("normal", ["outing", "class"]), 13: ("normal", ["outing", "allowance"]),
    14: ("normal", ["training/Vo", "training/Da", "training/Vi"]), 15: ("normal", ["training/Vo", "training/Da", "training/Vi", "class"]),
    16: ("normal", ["outing", "shop", "allowance"]), 17: ("oikomi", ["training"]), 18: ("exam", []),
}  # fmt: skip


def _screens(order=NAMES):
    from hiro.vision.schedule_list import read_schedule_list

    out = []
    for n in order:
        p = D / f"schedule-list-master-{n}.png"
        if not p.exists():
            pytest.skip("需要实机截图 schedule_list/")
        out.append(read_schedule_list(cv2.imread(str(p))))
    return out


def _opt(o):
    return o["option"] + (f"/{o['attr']}" if o.get("attr") else "")


@pytest.mark.parametrize("order", [NAMES, tuple(reversed(NAMES))])
def test_full_schedule_matches_screen(order):
    from hiro.vision.schedule_list import merge

    m = merge(_screens(order))
    assert m["current_week"] == 4 and m["total"] == 18
    assert {e["week"]: (e["kind"], [_opt(o) for o in e["options"]]) for e in m["weeks"]} == TRUTH
    by = {e["week"]: e for e in m["weeks"]}
    assert [by[w]["done"] for w in (1, 2, 3, 4, 5)] == [True, True, True, False, False]
    assert by[3]["faded"] == [True, False] and by[3]["chosen"] == {
        "option": "allowance",
        "attr": None,
    }  # 第 3 周选了活動支給
    assert by[8]["exam"] == {"stage": "mid1", "line": 150} and by[18]["exam"] == {"stage": "final", "line": 400}


def test_partial_rows_at_edges_not_read():
    """滑到边缘被 tab / 属性栏盖住一截的行不读（dn3 最下面的第 1 周原来读成 4 个乱图标）。"""
    s = _screens(("dn3",))[0]
    assert all(len(e["options"]) <= 4 for e in s["weeks"].values())
    assert 1 not in s["weeks"] or [_opt(o) for o in s["weeks"][1]["options"]] == ["class"]


def test_not_schedule_list():
    from hiro.vision.schedule_list import read_schedule_list

    p = Path(__file__).with_name("fixtures") / "pages" / "hajime-action-lessons.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/hajime-action-lessons.jpg")
    assert read_schedule_list(cv2.imread(str(p))) is None


def test_schedule_in_page_fields_on_action_select():
    from hiro.contracts import GameState, Observation, PageObs
    from hiro.loop.reduce import reduce

    sch = {"weeks": [{"week": 1, "kind": "normal", "options": []}], "current_week": 1, "total": 18}
    st = GameState(produce={"mode": "produce-003", "schedule": sch})
    o = Observation(
        seq=1, t=0.0, page=PageObs(name="ACTION_SELECT", area="produce", stable_frames=3), fields={"stamina": 20}
    )
    for _ in range(3):
        st = reduce(st, o)
    assert st.page_fields["schedule"] == sch
