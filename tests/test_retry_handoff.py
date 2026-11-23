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
