"""考试没过线时把再挑戦交给人：HIRO_RETRY_HANDOFF=1（hiro.runtime.retry_handoff）。看画面判断，不用人删文件（用户 11-21）。"""

from hiro.loop import pause_flag
from hiro.runtime import retry_handoff as rh


class Rig:
    def __init__(self, tmp_path, screens, timeout="60"):
        self.t, self.screens = 0.0, list(screens)
        self.env = {"HIRO_RETRY_HANDOFF": "1", "HIRO_RETRY_HANDOFF_TIMEOUT_S": timeout, "HIRO_WAIT_FILE": str(tmp_path / "w.txt"),
                    "HIRO_NOTIFY": "0"}  # fmt: skip
        self.path = tmp_path / "w.txt"
        self.paused = []

    def sleep(self, s):
        self.t += s
        self.paused.append(pause_flag.paused())

    def look(self):
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    def run(self):
        return rh.wait(
            "qualify-2", look=self.look, sleep=self.sleep, clock=lambda: self.t, notifier=lambda *a: None, env=self.env
        )


def test_defaults():
    assert not rh.enabled({}) and rh.enabled({"HIRO_RETRY_HANDOFF": "1"}) and rh.timeout_s({}) == 60


def test_nobody_touches_result_page_goes_back_to_program_after_60s(tmp_path):
    r = Rig(tmp_path, ["result"])
    assert r.run() == "auto" and 60 <= r.t < 60 + 2 * rh.POLL_S
    assert not r.path.exists() and all(r.paused)


def test_user_plays_and_passes(tmp_path):
    """点了再挑戦（确认框 / 读取）→ 考试中很久（不计时）→ 离开结算页：程序接着走。"""
    screens = ["result"] * 5 + ["other"] * 3 + ["exam"] * 400 + ["other"]
    r = Rig(tmp_path, screens)
    assert r.run() == "user" and r.t > 400 * rh.POLL_S


def test_user_plays_fails_again_gets_another_minute(tmp_path):
    screens = ["result"] * 3 + ["exam"] * 50 + ["result"]
    r = Rig(tmp_path, screens)
    assert r.run() == "auto"
    assert r.t >= (3 + 50) * rh.POLL_S + 60  # 回到结算页后重新计 60 秒


def test_user_declines_by_leaving_result_page(tmp_path):
    r = Rig(tmp_path, ["result"] * 3 + ["other"])
    assert r.run() == "user"


def test_short_other_blip_is_not_leaving(tmp_path):
    """点再挑戦后的确认框只停几秒、随后进考试：不算离开。"""
    r = Rig(tmp_path, ["result"] * 2 + ["other"] * 3 + ["exam"] * 5 + ["result"])
    assert r.run() == "auto"


def test_mid_exam_unrecognized_frames_do_not_end_handoff(tmp_path):
    """11-24 mao3：你打到一半，考试画面有 30 秒认不成考试（选中卡的放大图、移动框、你点开的审查日程面板）→ 不能判你打完了、抢过去自己打。"""
    screens = ["result"] * 3 + ["exam"] * 20 + ["other"] * 15 + ["exam"] * 20 + ["result"]
    r = Rig(tmp_path, screens)
    assert r.run() == "auto"  # 打完回到结算页（又没过）：照常再给 60 秒，最后交回程序
    assert r.t >= (3 + 20 + 15 + 20) * rh.POLL_S + 60


def test_played_then_left_needs_longer(tmp_path):
    """打过之后离开（过线进了下一页）：要离开 AWAY_PLAYED_S 才交回。"""
    r = Rig(tmp_path, ["result"] * 2 + ["exam"] * 10 + ["other"])
    assert r.run() == "user"
    assert r.t >= (2 + 10) * rh.POLL_S + rh.AWAY_PLAYED_S
