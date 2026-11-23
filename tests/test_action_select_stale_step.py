"""选行动页的日程和页面对不上时不停机（DMM own 测试局第三次停机）：页头「選抜試験1まで」读偏 → step 停在前一天（授業日），
用旧 step 的日程找课程 KeyError、重读 3 次后 unhandled。现在：附近找得到对得上的一步就按它预览，找不到按页面选项兜底；都记 fallback。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

pytestmark = pytest.mark.master


def _state(step, options):
    produce = {"idol_card_id": "i_card-shro-3-007", "mode": "produce-007", "step": step, "stamina": 30, "max_stamina": 33,
               "produce_point": 100, "params": {"Vo": 200, "Da": 300, "Vi": 150}, "growth_permil": {"Vo": 1100, "Da": 1300, "Vi": 1000},
               "star": 10}  # fmt: skip
    return GameState.from_dict(
        {
            "mode": "produce",
            "page": {"name": "ACTION_SELECT", "area": "produce"},
            "produce": produce,
            "page_fields": {"options": options},
        }
    )


LESSONS = [{"option": "training", "attr": a, "hp_cost": 6} for a in ("Vo", "Da", "Vi")] + [{"option": "rest"}]


def test_stale_step_previews_with_matching_step():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "ACTION_SELECT", _state(3, LESSONS)
    )  # 第 3 步是授業，页面是课程（第 4 步）
    assert d.action.kind is ActionKind.PRODUCE_NEXT and d.source == "fallback"
    assert any("按第 4 步预览" in w for w in d.warnings), d.warnings


def test_no_matching_step_falls_back_to_page_options():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "ACTION_SELECT", _state(3, [{"option": "special_guidance"}, {"option": "rest"}])
    )
    assert d.source == "fallback" and d.action.kind is ActionKind.PRODUCE_NEXT
    assert any("对不上" in w for w in d.warnings)


def test_consistent_step_unchanged():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide("ACTION_SELECT", _state(4, LESSONS))
    assert not any("对不上" in w for w in d.warnings)
