"""日常任务（hiro.daily）：假设备 + 假帧（每帧标出能看到哪些模板、在哪、按钮亮没亮），不跑真识别；颜色 / 滚动条用合成图。"""

import os
import subprocess
import sys

from hiro.daily import (
    TASKS,
    Deadline,
    Popup,
    TaskTimeout,
    activity_funds,
    club_reward,
    common,
    make_runtime,
    mission_reward,
    presents,
)
from hiro.runtime import Frame, nav
from hiro.vision.ocr import Box
from hiro.vision.ui_templates import Match, box, point

import numpy as np
import pytest

pytestmark = pytest.mark.play_free  # hiro.daily 不要 OCR 等 play 组依赖也能导入

HOME = {"Daily.ButtonHomeCurrent": (359, 1155)}


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.taps, self.swipes = [], []

    def screenshot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *a, **kw):
        self.swipes.append((a, kw))


def frame(seen: dict, *, off=(), red=False, boxes=None, dialog=False):
    """seen：模板名 → 中心点；off：其中灰掉的按钮；red：活动费入口有红点；boxes：OCR 结果（有就当有弹窗）。"""
    f = Frame(np.zeros((1280, 720, 3), np.uint8), 0.0)
    f.__dict__.update(texts=[], has_dialog=dialog or boxes is not None, dialog=None)
    f.seen, f.off, f.red, f.fake_boxes = seen, set(off), red, boxes or []
    return f


class Rig:
    """Runtime：截图依次给 frames（用完重复最后一帧）；script(rig, x, y) 在每次点击 / 拖动后可换帧队列（模拟换页）。"""

    def __init__(self, monkeypatch, frames, script=None, *, popup=None, deadline=None):
        self.t = 0.0
        self.queue = list(frames)
        self.script = script
        dev = FakeDevice()
        self.rt = make_runtime(dev, popup=popup, deadline=deadline, clock=lambda: self.t, sleep=self._sleep)
        self.rt.screen.shot = self._shot
        self.dev = dev
        raw_tap, raw_swipe = dev.tap, dev.swipe

        def tap(x, y):
            raw_tap(x, y)
            self._after(("tap", x, y))

        def swipe(*a, **kw):
            raw_swipe(*a, **kw)
            self._after(("swipe", *a))

        dev.tap, dev.swipe = tap, swipe
        monkeypatch.setattr(nav, "find", lambda f, name, **kw: Match(Box(*(p := f.seen[name]), p[0] + 1, p[1] + 1), 0.9)
                            if name in getattr(f, "seen", {}) else None)  # fmt: skip
        monkeypatch.setattr(common, "enabled", lambda f, m: not any(f.seen.get(n) == tuple(m.rect[:2]) for n in f.off))
        monkeypatch.setattr(common, "has_color", lambda img, c, rect: self.rt.screen.frame.red)
        monkeypatch.setattr(Frame, "boxes", property(lambda f: getattr(f, "fake_boxes", [])))
        monkeypatch.setattr("hiro.runtime.guard.word_at", lambda f, x, y: None)
        self.rt.interrupts = tuple(
            i for i in self.rt.interrupts if isinstance(i, (Popup, Deadline))
        )  # 通用中断另有测试

    def _sleep(self, s):
        self.t += s

    def _shot(self):
        f = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        f.t = self.t
        self.rt.screen.frame = f
        return f

    def _after(self, act):
        if self.script is not None and (nxt := self.script(self, act)) is not None:
            self.queue[:] = nxt

    @property
    def taps(self):
        return self.dev.taps


def on(table):
    """点击坐标 → 下一批帧。"""
    return lambda rig, act: table.get(act[1:3]) if act[0] == "tap" else None


# ---------------------------------------------------------------- presents


def test_presents_claims_all_and_closes(monkeypatch):
    entry, claim, close = (650, 460), (349, 1083), (360, 1200)
    page = frame({"Daily.Presents.ButtonClaimAllNoIcon": claim})
    result = frame({"Common.ButtonClose": close})
    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonPresentsPartial": entry})],
              on({entry: [page], claim: [result], close: [frame(HOME)]}))  # fmt: skip
    assert presents.run(rig.rt) == "领了礼物"
    assert rig.taps == [entry, claim, close]


def test_presents_nothing_to_claim(monkeypatch):
    entry, claim = (650, 460), (349, 1083)
    page = frame({"Daily.Presents.ButtonClaimAllNoIcon": claim, "Common.ButtonIconClose": (650, 100)},
                 off={"Daily.Presents.ButtonClaimAllNoIcon"})  # fmt: skip
    rig = Rig(
        monkeypatch,
        [frame({**HOME, "Daily.ButtonPresentsPartial": entry})],
        on({entry: [page], (650, 100): [frame(HOME)]}),
    )
    assert presents.run(rig.rt) == "没有可领的礼物"
    assert claim not in rig.taps  # 灰的一括受取 不点


def test_presents_stops_on_grey_button_after_claiming(monkeypatch):
    """领完按钮变灰又没有结果框：算领完（KAA 会一直点灰按钮到超时）。"""
    entry, claim = (650, 460), (349, 1083)
    grey = frame({"Daily.Presents.ButtonClaimAllNoIcon": claim, "Common.ButtonIconClose": (650, 100)},
                 off={"Daily.Presents.ButtonClaimAllNoIcon"})  # fmt: skip
    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonPresentsPartial": entry})],
              on({entry: [frame({"Daily.Presents.ButtonClaimAllNoIcon": claim})], claim: [grey], (650, 100): [frame(HOME)]}))  # fmt: skip
    assert presents.run(rig.rt) == "领了礼物" and rig.taps.count(claim) == 1


# ---------------------------------------------------------------- activity_funds


def test_activity_funds_taps_red_dot_then_closes_dialog(monkeypatch):
    entry, cross = tuple(box("Daily.ActivityFunds.EntryArea").center), (412, 600)
    dialog = frame({"Daily.ActivityFunds.DialogTitle": (108, 767), "Common.IconButtonCross": cross}, red=True)
    rig = Rig(monkeypatch, [frame(HOME, red=True)], on({entry: [dialog], cross: [frame(HOME)]}))
    assert activity_funds.run(rig.rt) == "领了活动费"
    assert rig.taps == [entry, cross]


def test_activity_funds_no_red_dot(monkeypatch):
    rig = Rig(monkeypatch, [frame(HOME)])
    assert activity_funds.run(rig.rt) == "没有活动费可领" and rig.taps == []


# ---------------------------------------------------------------- mission_reward

MISSION = {"Daily.MissonRewards.TitleIcon": (36, 54)}
PASS = {"Daily.PassRewards.IconTitle": (36, 54)}


def test_mission_reward_claims_enabled_tab_and_pass(monkeypatch):
    m_entry, p_entry, home_btn = (650, 571), (601, 790), (60, 1200)
    daily, weekly = tuple(point("Daily.MissonRewards.PointDaily")), tuple(point("Daily.MissonRewards.PointWeekly"))
    claim, x, p_claim = (274, 1083), (650, 100), (76, 627)
    home = frame({**HOME, "Daily.ButtonMission": m_entry, "Daily.ButtonPass": p_entry})
    grey = {"off": {"Daily.MissonRewards.ButtonClaim"}}
    monkeypatch.setattr(common, "scrollbar", lambda img, rect: None)  # 滚动条认不出：只看这一屏
    state = {"page": "home", "pass_left": 1}

    def script(rig, act):
        xy = act[1:3]
        if xy == m_entry:
            return [frame(MISSION)]
        if xy == daily:  # 日任务：按钮亮
            return [frame({**MISSION, "Daily.MissonRewards.ButtonClaim": claim})]
        if xy == claim:
            return [frame({"Common.ButtonIconClose": x})]
        if xy == weekly:  # 周任务：按钮灰着，不点
            return [frame({**MISSION, "Daily.MissonRewards.ButtonClaim": claim, "Common.ButtonIconClose": x}, **grey)]
        if xy == x and state["page"] == "home":  # 日任务奖励框关掉 → 按钮灰；周任务页的 ✕ → 回首页
            if rig.taps.count(weekly):
                return [home]
            return [frame({**MISSION, "Daily.MissonRewards.ButtonClaim": claim}, **grey)]
        if xy == p_entry:
            state["page"] = "pass"
            return [frame({**PASS, "Daily.PassRewards.ButtonClaim": p_claim})]
        if xy == p_claim:
            state["pass_left"] -= 1
            return [frame({"Common.ButtonIconClose": x})]
        if xy == x:  # 通行证奖励框关掉
            return [
                frame(
                    {**PASS, "Daily.PassRewards.ButtonClaim": p_claim}
                    if state["pass_left"]
                    else {**PASS, "Common.ButtonHome": home_btn}
                )
            ]
        if xy == home_btn:
            return [home]
        return None

    rig = Rig(monkeypatch, [home], script)
    assert mission_reward.run(rig.rt) == "任务奖励：daily；通行证：1 个"
    assert rig.taps.count(claim) == 1 and rig.taps.count(p_claim) == 1
    assert (
        rig.taps.index(daily)
        < rig.taps.index(claim)
        < rig.taps.index(weekly)
        < rig.taps.index(p_entry)
        < rig.taps.index(p_claim)
    )
    assert rig.taps[-1] == home_btn


def test_claim_tab_skips_grey_button(monkeypatch):
    claim = (274, 1083)
    rig = Rig(
        monkeypatch,
        [frame({**MISSION, "Daily.MissonRewards.ButtonClaim": claim}, off={"Daily.MissonRewards.ButtonClaim"})],
    )
    assert mission_reward.claim_tab(rig.rt, "daily") is False and rig.taps == []


def test_claim_pass_scrolls_until_bottom(monkeypatch):
    bars = iter([common.Scrollbar(0, 100, 608), common.Scrollbar(60, 100, 608), common.Scrollbar(508, 100, 608)])
    monkeypatch.setattr(common, "scrollbar", lambda img, rect: next(bars))
    p_entry = (601, 790)
    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonPass": p_entry})], on({p_entry: [frame(PASS)]}))
    assert mission_reward.claim_pass(rig.rt) == 0
    assert len(rig.dev.swipes) == 2  # 到底（position 1.0）就不再拖
    (x1, y1, x2, y2), kw = rig.dev.swipes[0]
    assert x1 == x2 == 703 and y1 == 328 + 50 and y2 == y1 + 60 and kw == {"duration": 0.3}


def test_claim_pass_stops_when_bar_does_not_move(monkeypatch):
    monkeypatch.setattr(common, "scrollbar", lambda img, rect: common.Scrollbar(100, 100, 608))
    p_entry = (601, 790)
    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonPass": p_entry})], on({p_entry: [frame(PASS)]}))
    mission_reward.claim_pass(rig.rt)
    assert len(rig.dev.swipes) == 1


# ---------------------------------------------------------------- club_reward

CLUB = {"Daily.Club.TitleIcon": (35, 54)}


def test_goto_club_via_toolbar_menu(monkeypatch):
    menu, club = (653, 1213), (605, 410)
    rig = Rig(monkeypatch, [frame({**HOME, "Common.ButtonToolbarMenu": menu})],
              on({menu: [frame({"Daily.IconMenuClub": club})], club: [frame(CLUB)]}))  # fmt: skip
    club_reward.goto_club(rig.rt)
    assert rig.taps == [menu, club]


def test_request_note_picks_configured_note(monkeypatch):
    req, note, ok = (382, 1086), (105, 652), (500, 1150)
    picker = {"Common.ButtonConfirm": ok, "Shop.ItemAnomalyNoteVisual": note}
    rig = Rig(monkeypatch, [frame({**CLUB, "Daily.Club.ButtonRequest": req})],
              on({req: [frame(picker)], note: [frame(picker)], ok: [frame({**CLUB, "Daily.Club.ButtonRequestOngoing": (383, 1085)})]}))  # fmt: skip
    assert club_reward.request_note(rig.rt) == "发起了笔记请求"
    assert rig.taps == [req, note, ok]


def test_request_note_already_ongoing(monkeypatch):
    rig = Rig(monkeypatch, [frame({**CLUB, "Daily.Club.ButtonRequestOngoing": (383, 1085)})])
    assert club_reward.request_note(rig.rt) == "笔记请求进行中" and rig.taps == []


def test_send_gifts_stops_after_five_members(monkeypatch):
    send, nxt, ok = (564, 945), (682, 856), (360, 900)
    member = frame({**CLUB, "Daily.ButtonClubSendGift": send, "Daily.ButtonClubSendGiftNext": nxt})
    sent = frame(
        {**CLUB, "Daily.ButtonClubSendGift": send, "Daily.ButtonClubSendGiftNext": nxt},
        off={"Daily.ButtonClubSendGift"},
    )

    def script(rig, act):
        xy = act[1:3]
        if xy == send:
            return [frame({"Common.ButtonConfirm": ok})]
        if xy == ok:
            return [sent]
        if xy == nxt:
            return [member]
        return None

    rig = Rig(monkeypatch, [member], script)
    assert club_reward.send_gifts(rig.rt) == 5
    assert rig.taps.count(nxt) == 5 and rig.taps.count(send) == 5


def test_send_gifts_last_member_grey_button_ends(monkeypatch):
    send = (564, 945)
    rig = Rig(monkeypatch, [frame({**CLUB, "Daily.ButtonClubSendGift": send}, off={"Daily.ButtonClubSendGift"})])
    assert club_reward.send_gifts(rig.rt) == 0 and rig.taps == []


# ---------------------------------------------------------------- 中断：Popup / Deadline

INFO = [("ミッションパスPt獲得", 0.99, (200, 300, 520, 340)), ("閉じる", 0.99, (300, 1140, 420, 1180))]
JEWEL = [("ジュエルを消費します", 0.99, (150, 600, 570, 640)), ("キャンセル", 0.99, (120, 1140, 300, 1180)),
         ("OK", 0.99, (420, 1140, 600, 1180))]  # fmt: skip


def test_popup_closes_info_box_only_after_two_frames(monkeypatch):
    popup = Popup()
    rig = Rig(monkeypatch, [frame({}, boxes=INFO), frame({}, boxes=INFO), frame(HOME)], popup=popup)
    ok, _ = rig.rt.wait_until(lambda f: f.seen == HOME, 5)
    assert ok and rig.taps == [(360, 1160)] and popup.actions == [("close", (360, 1160))]


def test_popup_cancels_jewel_dialog_at_once(monkeypatch):
    """含ジュエル的框不等第二拍：任务这一拍可能正要点 OK。"""
    seen = []
    popup = Popup(lambda kind, img: seen.append(kind))
    rig = Rig(monkeypatch, [frame({"Common.ButtonConfirm": (510, 1160)}, boxes=JEWEL), frame(CLUB)], popup=popup)
    step_frames = []
    rig.rt.drive(lambda f: step_frames.append(f) or (True if f.seen == CLUB else None), 5, "x")
    assert rig.taps == [(210, 1160)] and seen == ["cancel"]
    assert all("Common.ButtonConfirm" not in f.seen for f in step_frames)  # 任务没看到那一帧，不会去点 OK


def test_popup_ignores_frames_without_dialog(monkeypatch):
    rig = Rig(monkeypatch, [frame({}, boxes=None)], popup=Popup())
    rig.rt.wait_until(lambda f: False, 1)
    assert rig.taps == []


def test_deadline_interrupts_task(monkeypatch):
    rig = Rig(monkeypatch, [frame({})], deadline=None)
    d = Deadline(10, lambda: rig.t)
    rig.rt.interrupts = (*rig.rt.interrupts, d)
    d.start()
    with pytest.raises(TaskTimeout):
        rig.rt.drive(lambda f: None, 60, "x")
    assert 10 <= rig.t <= 11


# ---------------------------------------------------------------- 合成图：颜色 / 滚动条


def test_has_color_matches_kotonebot_hls_distance():
    img = np.zeros((1280, 720, 3), np.uint8)
    rect = box("Daily.ActivityFunds.EntryArea")
    assert not common.has_color(img, "#ff6085", rect)
    img[560, 60] = (0x85, 0x60, 0xFF)  # BGR
    assert common.has_color(img, "#ff6085", rect)
    assert not common.has_color(img, "#ff6085", Box(200, 200, 300, 300))  # 不在框里不算
    img2 = np.zeros((1280, 720, 3), np.uint8)
    img2[560, 60] = (0x60, 0x60, 0x60)  # 灰：色相差远
    assert not common.has_color(img2, "#ff6085", rect)


def test_scrollbar_reads_thumb():
    img = np.zeros((1280, 720, 3), np.uint8)
    x, y, w, h = mission_reward.PASS_SCROLLBAR
    img[y + 200 : y + 300, x : x + w] = 141
    bar = common.scrollbar(img, mission_reward.PASS_SCROLLBAR)
    assert bar == common.Scrollbar(200, 100, h) and bar.position == pytest.approx(300 / h)
    img[y + 200 : y + 300, x : x + w] = 0
    img[y : y + 10, x : x + w] = 141  # 把手比轨道 5% 还短：不认
    assert common.scrollbar(img, mission_reward.PASS_SCROLLBAR) is None


# ---------------------------------------------------------------- 接线


def test_registry_and_no_kaa_import():
    assert set(TASKS) == {"activity_funds", "presents", "assignment", "contest", "mission_reward", "club_reward"}
    code = (
        "import sys, hiro.daily;"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('kotonebot', 'kaa'));"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    env = {
        k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"
    }  # 设了它 hiro.vision 会给 KAA 指主数据目录（KAA 还在时要）
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def test_run_daily_has_no_kaa_path():
    """6 项都在 hiro.daily：run_daily 不再有 KAA 那条路（--own 只为兼容留着，不起作用），源码里不 import kaa / kotonebot。"""
    from pathlib import Path

    tool = Path(__file__).resolve().parents[1] / "tools/daily/run_daily.py"
    src = tool.read_text(encoding="utf-8")
    assert "from kaa" not in src and "import kotonebot" not in src and "kaa_device" not in src
    assert not (tool.parent / "kaa_device.py").exists()
    r = subprocess.run([sys.executable, str(tool), "--only", "nope"], capture_output=True, text=True, check=False)
    assert r.returncode == 2 and "没有这些项" in r.stderr


def test_run_own_records_ok_timeout_and_error(monkeypatch, tmp_path):
    """run_daily --own 的接线：每项前后截图、结果 / 超时 / 停机都记进 summary，一项失败不影响下一项。"""
    import importlib.util
    from pathlib import Path

    import hiro.daily
    import hiro.devices
    from hiro.loop.errors import ResumeBlocked

    spec = importlib.util.spec_from_file_location(
        "run_daily", Path(__file__).resolve().parents[1] / "tools/daily/run_daily.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Dev(FakeDevice):
        def connect(self):
            pass

    monkeypatch.setattr(hiro.devices, "open_device", lambda name=None: Dev())

    def blocked(rt):
        raise ResumeBlocked("测试：停机")

    monkeypatch.setitem(hiro.daily.TASKS, "presents", lambda rt: "领了礼物")
    monkeypatch.setitem(hiro.daily.TASKS, "activity_funds", lambda rt: rt.drive(lambda f: None, 60, "空转"))
    monkeypatch.setitem(hiro.daily.TASKS, "club_reward", blocked)
    summary = mod.run_own(["presents", "activity_funds", "club_reward"], 0.5, tmp_path)
    by = {r["task"]: r for r in summary}
    assert by["presents"]["ok"] and by["presents"]["result"] == "领了礼物" and by["presents"]["impl"] == "own"
    assert not by["activity_funds"]["ok"] and "超时" in by["activity_funds"]["error"]
    assert Path(by["activity_funds"]["timeout_shot"]).exists()
    assert not by["club_reward"]["ok"] and "ResumeBlocked" in by["club_reward"]["error"]
    assert all(Path(r["before"]).exists() and Path(r["after"]).exists() for r in summary)


# ---------------------------------------------------------------- 匹配小工具（移植 kotonebot 的 colored / WhiteFilter / raw find）


def test_hist_match_tells_colors_apart():
    red = np.zeros((30, 30, 3), np.uint8)
    red[:] = (40, 40, 220)
    grey = np.full((30, 30, 3), 120, np.uint8)
    assert common.hist_match(red, red.copy())
    assert not common.hist_match(red, grey)


def test_white_match_ignores_background_colour():
    tpl = np.zeros((20, 20, 3), np.uint8)
    tpl[5:15, 8:12] = 255  # 白色竖条（图标）
    img = np.zeros((200, 200, 3), np.uint8)
    img[:] = (200, 60, 30)  # 彩色底
    img[105:115, 58:62] = 255
    m = common.white_match(img, tpl, threshold=0.7)
    assert m is not None and m.rect[:2] == (50, 100)
    assert common.white_match(np.zeros((200, 200, 3), np.uint8), tpl, threshold=0.7) is None


def test_same_image():
    a = np.random.default_rng(0).integers(0, 255, (40, 120, 3), dtype=np.uint8)
    b = a.copy()
    assert common.same(a, b)
    b[:, 60:] = 255 - b[:, 60:]
    assert not common.same(a, b)


# ---------------------------------------------------------------- 工作（assignment）、竞赛（contest）：移植 KAA tasks/daily


def _patch_multi(monkeypatch, *, all_hits=None, scores=None):
    """find_all / find_colored / find_white / same / 竞赛分数：按帧上登记的名字给结果（Rig 只替换了 nav.find）。"""
    from hiro.daily import assignment, contest

    def find_all(f, name):
        pts = (all_hits or {}).get(name) if name in getattr(f, "seen", {}) else None
        return [Match(Box(x, y, x + 1, y + 1), 0.9) for x, y in (pts or [])]

    def by_name(f, name, **kw):
        p = getattr(f, "seen", {}).get(name)
        return None if p is None else Match(Box(p[0], p[1], p[0] + 1, p[1] + 1), 0.9)

    monkeypatch.setattr(common, "find_all", find_all)
    monkeypatch.setattr(common, "find_colored", by_name)
    monkeypatch.setattr(common, "find_white", by_name)
    monkeypatch.setattr(common, "same", lambda a, b, threshold=0.95: False)
    monkeypatch.setattr(contest, "read_scores", lambda f: list(scores or [None, None, None]))
    return assignment, contest


ASSIGN_PAGE = {"Daily.IconTitleAssign": (40, 60), "Daily.IconAssignMiniLive": (180, 700),
               "Daily.IconAssignOnlineLive": (540, 700), "Daily.ButtonAssignmentShortenTime": (360, 1100)}  # fmt: skip


def test_assignment_nothing_to_do(monkeypatch):
    assignment, _ = _patch_multi(monkeypatch)
    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonAssignmentPartial": (130, 980)})])
    assert assignment.run(rig.rt) == "工作不用处理" and rig.taps == []


def test_assignment_claims_reward_then_reassigns_both(monkeypatch):
    """首页工作入口有红点：点进去领奖励 → ミニライブ、ライブ配信各派一位好調偶像（12 小时）。时短按钮绝不点。"""
    assignment, _ = _patch_multi(monkeypatch, all_hits={"Daily.IconAssignKouchou": [(400, 760), (200, 760)]})
    entry, claim = (130, 980), (360, 1000)
    mini, online = ASSIGN_PAGE["Daily.IconAssignMiniLive"], ASSIGN_PAGE["Daily.IconAssignOnlineLive"]
    select, ok, h12, start = (360, 1180), (500, 1180), (560, 900), (360, 1150)
    pick = frame({"Common.ButtonSelect": select, "Daily.IconAssignKouchou": (200, 760)})
    hours = frame({"Common.ButtonConfirmNoIcon": ok, "Daily.Assignment.Button12Hours": h12})
    done_mini = {k: v for k, v in ASSIGN_PAGE.items() if k != "Daily.IconAssignMiniLive"}
    state = {"kind": "mini"}

    def script(rig, act):
        xy = act[1:3]
        if xy == entry:
            return [frame({"Common.ButtonCompletion": claim})]
        if xy == claim:
            return [frame(ASSIGN_PAGE)]
        if xy in (mini, online):
            state["kind"] = "mini" if xy == mini else "online"
            return [pick]
        if xy == select:
            return [hours]
        if xy == ok:
            return [frame({"Common.ButtonStart": start})]
        if xy == start:
            return [frame(done_mini if state["kind"] == "mini" else {"Daily.IconTitleAssign": (40, 60),
                                                                       "Daily.ButtonAssignmentShortenTime": (360, 1100)})]  # fmt: skip
        if xy == HOME["Daily.ButtonHomeCurrent"]:
            return None
        return None

    rig = Rig(monkeypatch, [frame({**HOME, "Daily.ButtonAssignmentPartial": entry}, red=True)], script)
    out = assignment.run(rig.rt, home=False)
    assert rig.taps[:2] == [entry, claim]
    assert rig.taps[2:9] == [mini, (200, 760), select, h12, ok, start, online][:7]
    assert online in rig.taps and rig.taps.count(select) == 2 and rig.taps.count(h12) == 2
    assert ASSIGN_PAGE["Daily.ButtonAssignmentShortenTime"] not in rig.taps
    assert "领了工作奖励" in out and "ミニライブ" in out and "ライブ配信" in out


def test_contest_nothing_to_do(monkeypatch):
    _, contest = _patch_multi(monkeypatch)
    rig = Rig(monkeypatch, [frame({**HOME, "Common.ButtonContest": (480, 1140)})])
    assert contest.run(rig.rt) == "竞赛不用打" and rig.taps == []


def test_contest_picks_lowest_score_and_skips_through(monkeypatch):
    _, contest = _patch_multi(
        monkeypatch,
        all_hits={"Daily.TextContestOverallStats": [(130, 1060), (130, 620), (130, 860)]},  # 乱序：按 y 排
        scores=[500, 300, 400],
    )
    from hiro.vision.ui_templates import point

    btn, cpoint = (480, 1140), tuple(point("Daily.PointContest"))
    start, check, skip, end = (360, 1080), (250, 1005), (650, 80), (360, 1150)
    rank = {"Daily.ButtonContestRanking": (600, 300), "Daily.TextContestOverallStats": (130, 620)}

    def script(rig, act):
        xy = act[1:3]
        table = {btn: [frame({"Daily.TextRoadToIdol": (360, 200)})], cpoint: [frame(rank)],
                 (130, 860): [frame({"Daily.ButtonContestStart": start})], start: [frame({"Common.CheckboxUnchecked": check})],
                 check: [frame({"Daily.ButtonIconSkip": skip})], skip: [frame({"Common.ButtonEnd": end})],
                 end: [frame({"Daily.Contest.TextUsedUp": (360, 880)})]}  # fmt: skip
        return table.get(xy)

    rig = Rig(monkeypatch, [frame({**HOME, "Common.ButtonContest": btn}, red=True)], script)
    out = contest.run(rig.rt, home=False)
    # 第 2 位（中间那行）300 分最低；点「開始」那拍后面同 KAA 还点一下空白处（12, 12）跳动画
    assert rig.taps == [btn, cpoint, (130, 860), start, (12, 12), check, skip, end]
    assert "打了 1 场" in out


def test_contest_without_memory_set_skips(monkeypatch):
    """回忆没编成：同 KAA when_no_set=skip，关掉提示框、不自动编成。"""
    _, contest = _patch_multi(monkeypatch)
    btn, cross, challenge = (480, 1140), (620, 400), (360, 760)
    ongoing = {"Daily.TextRoadToIdol": (360, 200), "Daily.TextContestLastOngoing": (360, 500)}
    from hiro.vision.ui_templates import point

    cpoint = tuple(point("Daily.PointContest"))

    def script(rig, act):
        table = {btn: [frame(ongoing)], (360, 500): [frame(ongoing)],
                 cpoint: [frame({"Daily.ButtonContestChallengeStart": (360, 1000), "Daily.ButtonContestStart": (360, 1000)})],
                 (360, 1000): [frame({"Daily.TextContestNoMemory": (360, 640), "Common.IconButtonCross": cross,
                                      "Daily.ButtonContestChallenge": challenge})]}  # fmt: skip
        return table.get(act[1:3])

    rig = Rig(monkeypatch, [frame({**HOME, "Common.ButtonContest": btn}, red=True)], script)
    out = contest.run(rig.rt, home=False)
    assert cross in rig.taps and "回忆没编成" in out
    assert challenge not in rig.taps  # 不点「おまかせで挑戦」（不自动编成）


def test_contest_scores_keep_ocr_blocks_apart(monkeypatch):
    """一格 OCR 出两段数字（「40,000」「50,000」）时取较小的 40000，不拼成 4000050000（CodeRabbit）。"""
    from hiro.daily import contest
    from hiro.vision import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "ocr", lambda img: [("40,000", 0.9), ("50,000", 0.9)])
    f = frame({})
    assert contest.read_scores(f) == [40000, 40000, 40000]
