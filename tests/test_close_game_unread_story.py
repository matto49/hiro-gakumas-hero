"""跑完关游戏（hiro.loop.close_game）+ 未读剧情停下等人（hiro.loop.unread_story）。不碰设备：命令和截图都是假的。"""

import pytest

from hiro.loop import close_game as cg
from hiro.loop import pause_flag, skip_settings
from hiro.loop import unread_story as us

MGR = r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe"


@pytest.mark.parametrize("v, want", [(None, "off"), ("", "off"), ("0", "off"), ("off", "off"), ("1", "emulator"), ("on", "emulator"),
                                     ("emulator", "emulator"), ("game", "game"), ("GAME", "game"), ("bogus", "off")])  # fmt: skip
def test_target(v, want):
    assert cg.target({} if v is None else {"HIRO_CLOSE_GAME": v}) == want


def test_mumu_emulator_uses_manager_shutdown():
    env = {"HIRO_DEVICE": "mumu", "HIRO_ADB_PATH": r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe", "HIRO_EMULATOR_INSTANCE": "2"}
    assert cg.commands("mumu", "emulator", env, exists=lambda p: p == MGR) == [[MGR, "control", "-v", "2", "shutdown"]]
    assert cg.commands("mumu", "game", env, exists=lambda p: p == MGR) == [[MGR, "control", "-v", "2", "app", "close", "-pkg", cg.PACKAGE]]


def test_mumu_without_manager_falls_back_to_adb_force_stop():
    cmds = cg.commands("mumu", "emulator", {"HIRO_EMULATOR_INSTANCE": "1"}, exists=lambda p: False)
    assert cmds == [["adb", "-s", "127.0.0.1:16416", "shell", "am", "force-stop", cg.PACKAGE]]


def test_dmm_and_adb_commands():
    assert cg.commands("dmm", "game", {}) == [["taskkill", "/f", "/im", "gakumas.exe"]]
    assert cg.commands("dmm", "emulator", {})[-1] == ["taskkill", "/f", "/im", "DMMGamePlayer.exe"]
    ld = cg.commands("leidian", "emulator", {"HIRO_ADB": "127.0.0.1:5557", "HIRO_LDCONSOLE": "ld.exe"})
    assert ld == [["adb", "-s", "127.0.0.1:5557", "shell", "am", "force-stop", cg.PACKAGE], ["ld.exe", "quit", "--index", "0"]]
    assert cg.commands("maatools", "emulator", {}) == []


def test_close_at_exit_respects_switch_and_parent():
    ran = []
    run = lambda c: ran.append(c) or 0  # noqa: E731
    assert not cg.close_at_exit(env={"HIRO_DEVICE": "dmm"}, run=run) and ran == []  # 开关没开
    assert not cg.close_at_exit(env={"HIRO_DEVICE": "dmm", "HIRO_CLOSE_GAME": "game", "HIRO_CLOSE_GAME_BY_PARENT": "1"}, run=run)
    assert ran == []  # play_game 的子进程不关
    assert cg.close_at_exit(env={"HIRO_DEVICE": "dmm", "HIRO_CLOSE_GAME": "game"}, run=run)
    assert ran == [["taskkill", "/f", "/im", "gakumas.exe"]]


# ---------------------------------------------------------------- 未读剧情
DIALOG = ["スキップ確認", "未読のコミュです", "スキップしますか？", "キャンセル", "決定"]
COMMU = ["ログ", "AUTO", "SKIP", "早送り"]
HOME = ["プロデュース", "ホーム"]


def test_detectors():
    assert us.is_unread_dialog(DIALOG) and not us.is_unread_dialog(COMMU)
    assert us.is_commu(COMMU) and not us.is_commu(HOME)


class Rig:
    def __init__(self, tmp_path, screens, *, delete_at=None, mode="stop", timeout="60"):
        self.env = {"HIRO_UNREAD_STORY": mode, "HIRO_UNREAD_TIMEOUT_S": timeout, "HIRO_WAIT_FILE": str(tmp_path / "wait.txt")}
        self.screens, self.delete_at, self.t, self.calls, self.notes = list(screens), delete_at, 0.0, [], []
        us._allow_until[0] = 0.0

    def sleep(self, s):
        self.t += s
        if self.delete_at is not None and self.t >= self.delete_at:
            us.wait_file(self.env).unlink(missing_ok=True)

    def texts(self):
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    def run(self):
        return us.handle(cancel=lambda: self.calls.append("cancel"), confirm=lambda: self.calls.append("confirm"), texts=self.texts,
                         sleep=self.sleep, clock=lambda: self.t, close=lambda what, why: self.calls.append(("close", what)),
                         notifier=lambda t, b: self.notes.append(t), env=self.env)  # fmt: skip


def test_skip_mode_confirms_like_before(tmp_path):
    r = Rig(tmp_path, [COMMU], mode="skip")
    assert r.run() == "skipped" and r.calls == ["confirm"] and r.notes == []


def test_stop_waits_until_user_deletes_file_then_allows_skip(tmp_path):
    r = Rig(tmp_path, [COMMU], delete_at=30)
    assert r.run() == "user"
    assert r.calls == ["cancel"] and r.notes and not us.wait_file(r.env).exists()
    assert us.allow_skip(r.t + 1) and not us.allow_skip(r.t + us.ALLOW_SKIP_S + 1)
    assert r.run() == "skipped" and r.calls[-1] == "confirm"  # 你同意跳过后，下一个未读确认框按「決定」


def test_stop_resumes_when_story_page_is_gone(tmp_path):
    r = Rig(tmp_path, [COMMU, COMMU] + [HOME] * us.LEFT_POLLS)
    assert r.run() == "left" and r.calls == ["cancel"] and not us.wait_file(r.env).exists()


def test_stop_timeout_closes_emulator_and_raises(tmp_path):
    r = Rig(tmp_path, [COMMU], timeout="60")
    with pytest.raises(us.UnreadStoryTimeout):
        r.run()
    assert r.calls == ["cancel", ("close", "emulator")] and r.t >= 60
    assert "已关闭游戏" in us.wait_file(r.env).read_text(encoding="utf-8")


def test_timeout_uses_close_game_target(tmp_path):
    r = Rig(tmp_path, [COMMU], timeout="30")
    r.env["HIRO_CLOSE_GAME"] = "game"
    with pytest.raises(us.UnreadStoryTimeout):
        r.run()
    assert r.calls[-1] == ("close", "game")


def test_wait_counts_as_pause(tmp_path):
    seen = []
    r = Rig(tmp_path, [COMMU], delete_at=6)
    orig = r.sleep
    r.sleep = lambda s: (seen.append(pause_flag.paused()), orig(s))
    r.run()
    assert seen and all(seen) and not pause_flag.paused()


def test_skip_settings_stop_mode_turns_unread_fast_forward_off():
    boxes = [("コミュ早送り設定", 1, (200, 300, 520, 340)), ("コミュを早送りする", 1, (100, 500, 400, 540)),
             ("未読コミュでも早送りする", 1, (100, 700, 420, 740)), ("ON", 1, (580, 700, 640, 740)), ("決定", 1, (450, 1100, 560, 1150))]  # fmt: skip
    assert skip_settings.read(boxes, stop_story=False)["off"] == []  # 平常：ON 就不动
    assert skip_settings.read(boxes, stop_story=True)["off"] == [(610, 720)]  # stop：要 OFF，点一下
    perf = [("【プロデュース】演出スキップ設定", 1, (100, 200, 600, 240)), ("サポートイベントの", 1, (60, 400, 300, 430)),
            ("OFF", 1, (580, 400, 640, 440)), ("決定", 1, (450, 1100, 560, 1150))]  # fmt: skip
    assert skip_settings.read(perf, stop_story=False)["off"] == [(610, 420)]
    assert skip_settings.read(perf, stop_story=True)["off"] == []  # stop：跳剧情类开关不动


def test_flag_watcher_does_not_end_a_hold(tmp_path):
    """11-20 DMM 实测：等未读剧情时录屏看守看到「在暂停、但没有旗标」就当录屏恢复了，把等人的时间又算回看门狗。"""
    calls = []
    flow = type("F", (), {"request_pause": lambda s: calls.append("pause"), "request_resume": lambda s: calls.append("resume")})()
    w = pause_flag.Watcher(flow, flag=tmp_path / "none.flag")
    with pause_flag.hold():
        w.tick()
        assert pause_flag.paused() and calls == []
    assert not pause_flag.paused()


def test_dialog_text_ignores_bar_and_other_regions():
    items = [("麻央", 880), ("みんな、今日もよろしくね。", 960), ("ログ", 1220), ("AUTO", 1220), ("SKIP", 1220), ("早送り", 1220),
             ("プロデュースコミュ", 60)]  # fmt: skip
    assert us.dialog_text(items) == "麻央みんな、今日もよろしくね。"
    assert not us._changed("麻央みんな、今日もよろしくね。", "麻央みんな、今日もよろしくね")  # OCR 差一个字不算推进


def test_idle_timer_resets_while_story_advances(tmp_path):
    """剧情在推进（剧情框文字在变）就不关；停在同一句 60 秒才关。画面动态（人物待机、风）不影响：只看文字。"""
    says = ["おはよう", "今日もレッスン頑張ろう", "プロデューサー、見てて", "ふふっ、ありがとう", "次はダンスだね",
            "少し休憩しようか", "うん、大丈夫", "まだまだいけるよ", "本番が楽しみ", "それじゃあ、また明日"]  # fmt: skip
    lines = [[("麻央", 880), (t, 960), ("SKIP", 1220), ("早送り", 1220)] for t in says]
    screens = [lines[i // 4] for i in range(40)] + [lines[9]]  # 每 4 次（12 秒）换一句，换到最后一句后不动
    r = Rig(tmp_path, screens, timeout="60")
    with pytest.raises(us.UnreadStoryTimeout):
        r.run()
    assert r.t >= 40 * us.POLL_S - 12 + 60  # 最后一次换句之后又等满 60 秒
    assert r.calls[-1] == ("close", "emulator")


def test_translated_story_bar_still_counts_as_story():
    """汉化插件（用户 11-20：不重启就能开关）把底栏译成中文：照样认成剧情页。"""
    assert us.is_commu(["日志", "自动", "跳过", "快进"])
    assert not us.is_game_page(["日志", "自动", "跳过", "快进"])


def test_chinese_ui_after_story_does_not_resume(tmp_path):
    """剧情结束了但汉化还开着（中文界面，程序看不懂）：不继续操作，接着等；剧情框文字不动 60 秒就关游戏。"""
    chinese_home = ["制作", "主页", "体力回复", "商店"]
    r = Rig(tmp_path, [COMMU, chinese_home], timeout="60")
    with pytest.raises(us.UnreadStoryTimeout):
        r.run()


def test_turning_translation_off_resumes(tmp_path):
    chinese_home = ["制作", "主页", "商店"]
    r = Rig(tmp_path, [COMMU, chinese_home, chinese_home] + [HOME] * us.LEFT_POLLS, timeout="60")
    assert r.run() == "left"


def test_center_pos():
    from hiro.devices.win_window import center_pos

    assert center_pos(600, 1100, (0, 0, 1920, 1040)) == (660, 0)
    assert center_pos(400, 800, (0, 0, 1920, 1040)) == (760, 120)
    assert center_pos(2000, 1200, (100, 50, 1920, 1040)) == (100, 50)  # 放不下：贴左上角


def test_dmm_pause_centers_game_window(tmp_path, monkeypatch):
    from hiro.devices import win_window

    moved = []
    monkeypatch.setattr(win_window, "center", lambda title=win_window.WINDOW_TITLE: moved.append(1) or (1, 2))
    monkeypatch.setattr(us.sys, "platform", "win32")
    r = Rig(tmp_path, [COMMU], delete_at=3)
    r.env["HIRO_DEVICE"] = "dmm"
    r.run()
    assert moved == [1]
    r2 = Rig(tmp_path, [COMMU], delete_at=3)
    r2.env["HIRO_DEVICE"] = "mumu"
    r2.run()
    assert moved == [1]  # 模拟器不挪


def test_story_ending_into_exam_resumes(tmp_path):
    """11-20 DMM：第 2 回 選抜試験 的开场剧情看完直接进考试（Now loading → 开始页「スター性:達成…」），
    以前认不出是游戏页面，干等 60 秒关了游戏。"""
    loading, exam = ["第2回", "Now loading"], ["`229スター性:達成12744507%", "1H"]
    r = Rig(tmp_path, [COMMU, loading] + [exam] * us.LEFT_POLLS, timeout="60")
    assert r.run() == "left" and r.calls == ["cancel"]


def test_kana_heavy_page_counts_but_chinese_does_not():
    assert us.is_game_page(["実力は十分ですね。好成績を狙うには"])  # 没有关键词，但假名多、没有简体字
    assert not us.is_game_page(["表送情感的歌曲", "理解得根快", "我根期待"])
    assert not us.is_game_page(COMMU)
