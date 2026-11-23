"""授業 / おでかけ 三选一：选项、代价（cost_hp / cost_p / trouble 结构化字段）、选中项与效果说明，以及经契约到决策器的对接。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import profile_for
from hiro.contracts import GameState
from hiro.loop.reduce import reduce
from hiro.vision.event import read_event_choices
from hiro.vision.observe import observation

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "event"
# 文件 → [(体力消耗, P 消耗, 眠気)] 与选中项 / 选中项效果关键词（实机 <run> 局 00100/00125/00145，旧数据集 00261/00336）
TRUTH = {
    "study-00100": ([(5, None, False), (5, None, False), (None, None, True)], 1, "やる気関係"),
    "study-00145": ([(5, None, False), (5, None, False), (None, None, True)], 1, "やる気関係"),
    "study-00336": ([(5, None, False), (5, None, False), (None, None, True)], 0, "好印象関係"),
    "outing-00125": ([(None, 50, False), (None, None, True), (None, None, False)], 2, "最大体力の60%"),
    "outing-00261": ([(None, 50, False), (None, None, True), (None, None, False)], 1, "強化済み"),
}


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_event_choices(name: str):
    costs, selected, keyword = TRUTH[name]
    got = read_event_choices(cv2.imread(str(FIX / f"{name}.jpg")))
    assert [(c["cost_hp"], c["cost_p"], c["trouble"]) for c in got["choices"]] == costs
    assert got["selected"] == selected
    assert keyword in got["effect"]  # 选中项的效果说明（页面级，未选中项点开前看不到）


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_choices_reach_decider(name: str):
    scene = "STUDY" if name.startswith("study") else "OUTING"
    obs = observation(
        1, 0.0, scene, read_event_choices(cv2.imread(str(FIX / f"{name}.jpg"))), area="produce", stable_frames=3
    )
    assert obs.problems() == []
    state = GameState()
    for _ in range(3):
        state = reduce(state, obs)
    deciders = ProduceDeciders(profile_for("i_card-shro-3-007"))
    d = (deciders.study if scene == "STUDY" else deciders.outing)(state)
    assert d.source == "rule"  # 拿到了选项，不再是「未识别选第一项」的 fallback
    assert not obs.fields["choices"][d.action.args["select"]]["trouble"]
