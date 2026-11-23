"""识别-04：本战（produce-008）选行动页的 mode / step。夹具：第四局本战 traces/<run>-main4-20261031-194839/frames/00058、00070。"""

from pathlib import Path

import hiro  # noqa: F401
from hiro.vision.action import read_action_select
from hiro.vision.observe import mode_and_step, observation, produce_observation

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "action"


@pytest.mark.parametrize(("name", "days", "step"), [("main4-00058", 6, 1), ("main4-00070", 5, 2)])
def test_finals_action_select_mode_step(name, days, step):
    raw = read_action_select(cv2.imread(str(FIX / f"{name}.jpg"))).to_dict()
    assert raw["exam"] == "H.I.F本戦" and raw["days_left"] == days
    f = produce_observation(raw)
    assert (f["mode"], f["step"]) == ("produce-008", step)
    assert observation(1, 0.0, "ACTION_SELECT", raw).problems(strict=True) == []


def test_mode_and_step_rules():
    assert mode_and_step("選抜試験1", 6) == ("produce-007", 1)
    assert mode_and_step("選抜試験3", 1) == ("produce-007", 19)
    assert mode_and_step("H.I.F本戦", 1) == ("produce-008", 6)
    assert mode_and_step("H.I.F本戦", None) == ("produce-008", None)  # 天数读不出：mode 照给
    assert mode_and_step("H.I.F本戦2", 1) == ("produce-008", 8)  # R2 前（尚无样本，按 R2 = 9）
    assert mode_and_step(None, 3) == (None, None)
