"""D3（11-26）：检查游戏设置可以顺手改好——读到和要的不一样的开关就点一下，回读核对，最多 3 轮。

OCR 框取自实机设置页（tests/data/g33-ocr-boxes.json 的 settings/03-produce-01：四个跳过开关都是 OFF）。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
SRC = json.loads((Path(__file__).parent / "data" / "g33-ocr-boxes.json").read_text(encoding="utf-8"))
PAGE = [(t, c, tuple(b)) for t, c, b in SRC["settings/03-produce-01"]]


def _center(b):
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


class _Game:
    """假游戏：点到哪个开关就把它翻过来；stuck 里的开关点了不变（模拟点不动）。"""

    def __init__(self, boxes, stuck=()):
        self.boxes = list(boxes)
        self.clicks = []
        self.stuck = set(stuck)

    def screenshot(self):
        return object()

    def tap(self, x, y):
        self.clicks.append((x, y))
        for i, (t, c, b) in enumerate(self.boxes):
            if t in ("ON", "OFF") and _center(b) == (x, y) and (x, y) not in self.stuck:
                self.boxes[i] = ("ON" if t == "OFF" else "OFF", c, b)

    def ocr(self, img):
        return list(self.boxes)


ALL_ON = {
    "未読早送り": "ON",
    "支援事件剧情跳过": "ON",
    "开始说明剧情跳过": "ON",
    "定期公演剧情跳过": "ON",
    "親愛度映像跳过": "ON",
}


@pytest.mark.synthetic
def test_fix_screen_turns_off_switches_on_and_verifies():
    from check_settings import fix_screen

    g = _Game(PAGE)
    states, _, fixed = fix_screen(g, g.ocr, ALL_ON, sleep=lambda s: None)
    assert set(fixed) == {"支援事件剧情跳过", "开始说明剧情跳过", "定期公演剧情跳过", "親愛度映像跳过"}
    assert len(g.clicks) == 4  # 每个点一次，回读都对了就不再点
    assert all(states[k] == "ON" for k in fixed), states
    assert states["未読早送り"] is None  # 这一屏没有这项：不点、不算改过


@pytest.mark.synthetic
def test_fix_screen_leaves_alone_what_is_already_right_or_unwanted():
    """要的状态是 None（保持原样，如 HIRO_UNREAD_STORY=stop 时的跳剧情开关）不点；已经对的不点。"""
    from check_settings import fix_screen

    g = _Game(PAGE)
    goal = {**ALL_ON, "支援事件剧情跳过": None, "开始说明剧情跳过": "OFF"}
    _, _, fixed = fix_screen(g, g.ocr, goal, sleep=lambda s: None)
    assert set(fixed) == {"定期公演剧情跳过", "親愛度映像跳过"}


@pytest.mark.synthetic
def test_fix_screen_gives_up_after_three_rounds():
    from check_settings import fix_screen

    stuck = next(_center(b) for t, _, b in PAGE if t == "OFF")
    g = _Game(PAGE, stuck=[stuck])
    states, _, _ = fix_screen(g, g.ocr, ALL_ON, sleep=lambda s: None)
    assert g.clicks.count(stuck) == 3
    assert "OFF" in states.values()


def test_goals_follow_unread_story_switch(monkeypatch):
    from check_settings import goals

    assert goals(stop_story=False) == ALL_ON
    g = goals(stop_story=True)
    assert g["未読早送り"] == "OFF" and g["支援事件剧情跳过"] is None


def test_verdict_and_ok_respect_goal():
    from check_settings import is_ok, verdict

    found = {**ALL_ON, "未読早送り": "OFF"}
    goal = {**ALL_ON, "未読早送り": "OFF", "支援事件剧情跳过": None}
    assert is_ok(found, goal) and verdict(found, goal).startswith("ok")
    assert not is_ok(found, ALL_ON) and verdict(found, ALL_ON).startswith("设置被重置")
    v = verdict({**ALL_ON, "_fixed": ["親愛度映像跳过"]}, ALL_ON)
    assert v.startswith("改好了 1 项") and "親愛度映像跳过" in v
