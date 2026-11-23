"""LIVE 看 / 跳 / 照片谁选：HIRO_LIVE=play|watch|pick|auto（hiro.runtime.live_mode）。"""

from hiro.runtime import live_mode as lm
from hiro.runtime import photo_pick

import pytest

W, H = 1280, 720
SONG = [("Campus mode!!", (640, 200)), ("作詞 某某", (640, 400)), ("TAP TO START", (640, 650))]
PLAYING = [("", (0, 0))]
WITH_SKIP = [("SKIP", (1200, 50))]
CONFIRM = [("LIVEをスキップしますか？", (640, 300)), ("キャンセル", (500, 500)), ("スキップ", (780, 500))]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _env(tmp_path, live, **kw):
    return {"HIRO_LIVE": live, "HIRO_WAIT_FILE": str(tmp_path / "wait.txt"), "HIRO_NOTIFY": "0", **kw}


def _driver(env, clk, frames=(), closed=None):
    seq = iter(frames)
    shots = lambda: (W, H, next(seq, frames[-1] if frames else PLAYING))  # noqa: E731
    d = lm.LiveDriver(env=env, clock=clk)
    d.wait_kw = {
        "sleep": clk.sleep,
        "clock": clk,
        "notifier": lambda *a: None,
        "close": (lambda what, why=None: closed.append(what)) if closed is not None else (lambda *a, **k: None),
    }
    return d, shots


def test_modes_and_photo_override():
    assert lm.mode({}) == "play" and lm.mode({"HIRO_LIVE": "bad"}) == "play"
    assert [lm.photo_override({"HIRO_LIVE": m}) for m in lm.MODES] == [None, "stop", "stop", "auto"]
    assert photo_pick.mode({"HIRO_LIVE": "auto", "HIRO_PHOTO_PICK": "stop"}) == "auto"  # 全自动不等照片
    assert photo_pick.mode({"HIRO_LIVE": "pick"}) == "stop"
    assert photo_pick.mode({"HIRO_PHOTO_PICK": "stop"}) == "stop"


def test_find_skip_only_top_right_and_confirm():
    assert lm.find_skip(W, H, WITH_SKIP) == (1200, 50)
    assert lm.find_skip(W, H, [("SKIP", (100, 650))]) is None
    assert lm.find_confirm(CONFIRM) == (780, 500)
    assert lm.find_confirm([("キャンセル", (500, 500)), ("OK", (780, 500))]) is None  # 没有问句不点


def test_play_taps_start_and_never_skips(tmp_path):
    clk, taps = Clock(), []
    d, shots = _driver(_env(tmp_path, "play"), clk)
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "start" and taps == [(640, 650)]
    for _ in range(10):
        clk.t += 10
        assert d.step(W, H, WITH_SKIP, lambda x, y: taps.append((x, y)), shots) is None
    assert taps == [(640, 650)]


def test_auto_skips_and_confirms(tmp_path):
    clk, taps = Clock(), []
    d, shots = _driver(_env(tmp_path, "auto"), clk)
    tap = lambda x, y: taps.append((x, y))  # noqa: E731
    assert d.step(W, H, SONG, tap, shots) == "start"
    clk.t += 2
    assert d.step(W, H, WITH_SKIP, tap, shots) == "skip"
    assert d.step(W, H, CONFIRM, tap, shots) == "confirm"
    assert taps == [(640, 650), (1200, 50), (780, 500)]


def test_pick_reveals_menu_when_skip_hidden(tmp_path):
    clk, taps = Clock(), []
    d, shots = _driver(_env(tmp_path, "pick"), clk)
    tap = lambda x, y: taps.append((x, y))  # noqa: E731
    assert d.step(W, H, PLAYING, tap, shots) is None
    clk.t += lm.REVEAL_S
    assert d.step(W, H, PLAYING, tap, shots) == "reveal" and taps == [(640, 360)]
    for _ in range(20):
        clk.t += lm.REVEAL_S
        d.step(W, H, PLAYING, tap, shots)
    assert len(taps) == lm.REVEAL_MAX  # 有上限


def test_watch_user_starts(tmp_path):
    clk, taps = Clock(), []
    d, shots = _driver(_env(tmp_path, "watch"), clk, frames=[SONG, PLAYING, PLAYING])
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "watched"
    assert taps == [] and not (tmp_path / "wait.txt").exists()
    clk.t += 5
    assert d.step(W, H, WITH_SKIP, lambda x, y: taps.append((x, y)), shots) is None  # 你在看：不跳
    assert taps == []


def test_watch_delete_file_skips_this_live(tmp_path):
    clk, taps = Clock(), []
    env = _env(tmp_path, "watch")
    d, _ = _driver(env, clk)

    def shots():
        (tmp_path / "wait.txt").unlink(missing_ok=True)
        return W, H, SONG

    d.wait_kw["sleep"] = lambda s: (clk.sleep(s), (tmp_path / "wait.txt").unlink(missing_ok=True))
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "start"
    assert d.step(W, H, WITH_SKIP, lambda x, y: taps.append((x, y)), shots) == "skip"
    assert taps == [(640, 650), (1200, 50)]


def test_watch_timeout_closes_game(tmp_path):
    clk, closed = Clock(), []
    d, shots = _driver(_env(tmp_path, "watch", HIRO_LIVE_TIMEOUT_S="30"), clk, frames=[SONG], closed=closed)
    with pytest.raises(lm.LiveWatchTimeout):
        d.step(W, H, SONG, lambda x, y: None, shots)
    assert closed == ["emulator"] and clk.t == pytest.approx(30)


def test_new_live_resets(tmp_path):
    clk, taps = Clock(), []
    d, shots = _driver(_env(tmp_path, "watch"), clk, frames=[PLAYING, PLAYING])
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "watched"
    clk.t += 1
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "start"  # 同一场：不再等第二次
    clk.t += lm.NEW_LIVE_GAP_S + 1
    assert d.step(W, H, SONG, lambda x, y: taps.append((x, y)), shots) == "watched"  # 下一场重新等
