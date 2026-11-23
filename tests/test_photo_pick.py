"""LIVE 后选回忆照片：HIRO_PHOTO_PICK=stop 时停下等人（hiro.runtime.photo_pick）。"""

from hiro.runtime import photo_pick as pp

import pytest

PAGE = ["メモリーにするフォト", "次へ"]
CONFIRM = ["選択したフォトをメモリーとして保存しますか?", "決定"]
OTHER = ["プロデュース", "完了", "ファン数"]


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    pp._auto_until[0] = 0.0
    monkeypatch.setenv("HIRO_NOTIFY", "0")
    yield
    pp._auto_until[0] = 0.0


def _env(tmp_path, **kw):
    return {"HIRO_PHOTO_PICK": "stop", "HIRO_WAIT_FILE": str(tmp_path / "wait.txt"), **kw}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _run(tmp_path, frames, env=None, on_sleep=None):
    clk = Clock()
    seq = iter(frames)
    notes = []

    def sleep(s):
        clk.sleep(s)
        if on_sleep:
            on_sleep(clk)

    r = pp.wait(texts=lambda: next(seq, frames[-1]), sleep=sleep, clock=clk, notifier=lambda *a: notes.append(a),
                env=env or _env(tmp_path))  # fmt: skip
    return r, clk, notes


def test_mode_default_auto_and_bad_value():
    assert pp.mode({}) == "auto"
    assert pp.mode({"HIRO_PHOTO_PICK": "STOP"}) == "stop"
    assert pp.mode({"HIRO_PHOTO_PICK": "x"}) == "auto"


def test_should_wait_only_stop_and_photo_page(tmp_path):
    assert not pp.should_wait(PAGE, {}, now=0)
    assert pp.should_wait(PAGE, _env(tmp_path), now=0)
    assert not pp.should_wait(CONFIRM, _env(tmp_path), now=0)
    assert not pp.should_wait(OTHER, _env(tmp_path), now=0)


def test_user_picks_then_continues(tmp_path):
    r, clk, notes = _run(tmp_path, [PAGE, PAGE, CONFIRM, OTHER, OTHER, OTHER])
    assert r == "user"
    assert len(notes) == 1
    assert not (tmp_path / "wait.txt").exists()
    assert not pp.should_wait(PAGE, _env(tmp_path), now=clk.t + 1)  # 等完之后不再等第二次
    assert pp.should_wait(PAGE, _env(tmp_path), now=clk.t + pp.AUTO_AFTER_S + 1)


def test_confirm_dialog_keeps_waiting(tmp_path):
    r, clk, _ = _run(tmp_path, [CONFIRM] * 5 + [OTHER] * 3)
    assert r == "user"
    assert clk.t == pytest.approx(8 * pp.POLL_S)


def test_delete_wait_file_uses_default(tmp_path):
    def rm(clk):
        if clk.t >= 6:
            (tmp_path / "wait.txt").unlink(missing_ok=True)

    r, _, _ = _run(tmp_path, [PAGE], on_sleep=rm)
    assert r == "file"


def test_timeout_uses_default(tmp_path):
    r, clk, _ = _run(tmp_path, [PAGE], env=_env(tmp_path, HIRO_PHOTO_TIMEOUT_S="60"))
    assert r == "timeout"
    assert clk.t == pytest.approx(60)
    assert not (tmp_path / "wait.txt").exists()


def test_timeout_floor():
    assert pp.timeout_s({"HIRO_PHOTO_TIMEOUT_S": "5"}) == 30.0
    assert pp.timeout_s({}) == 600.0


def test_wait_is_paused_time(tmp_path):
    from hiro.loop import pause_flag

    seen = []

    def check(clk):
        seen.append(pause_flag.paused())

    _run(tmp_path, [PAGE, OTHER, OTHER, OTHER], on_sleep=check)
    assert seen and all(seen)
    assert not pause_flag.paused()
