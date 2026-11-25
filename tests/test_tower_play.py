"""偶像之路跑一段（hiro.runtime.tower_play）的页面处理器：第一次实机的范围（协调 11-14）——只打当前最前沿一层、编成不动、
打完一层停；结果页按 TowerRun 重试；再挑戦确认有消耗字样就停。"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision
from hiro.loop.errors import ResumeBlocked
from hiro.runtime import tower_play as tp
from hiro.runtime.loop import DONE
from hiro.runtime.tower import TowerRun

import pytest


def _rt():
    taps = []
    return NS(device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None), taps


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_TOWER_CACHE", str(tmp_path / "cache"))


def _play(tmp_path, decision=None, layers=1):
    import dataclasses

    @dataclasses.dataclass
    class State:
        page_fields: dict

    core = NS(s=NS(state=State({})), deciders=NS(decide=lambda page, st: decision))
    return tp.TowerPlay(core, TowerRun("t"), idol_name="月村手毬", layers=layers, loadout_path=tmp_path / "lo.json")


def test_layers_then_formation_keeps_current(tmp_path, monkeypatch):
    keep = Decision(
        Action(ActionKind.SELECT_FORMATION, {"kind": "tower", "mechanic": None, "main": 0, "subs": []}),
        "只有当前编成",
        "sim",
        0.8,
    )
    p = _play(tmp_path, keep)
    seen_pf = []
    p.core.deciders.decide = lambda page, st: seen_pf.append(st.page_fields) or keep
    rt, taps = _rt()
    assert (
        p.on_layers(rt, None, NS(fields={"idol": "月村手毬", "stage": 15, "challenge": [446, 987]})) is DONE
        and p.layer == 15
    )
    monkeypatch.setattr(tp, "idol_code", lambda name: "ttmr")
    f = {"layer": 15, "turns": 15, "score_bonus_permils": [23090, 11980, 2600], "params": [2348, 1701, 611], "max_stamina": 46,
         "challenge": [381, 1091], "omakase": [643, 1090], "idol_name": "月村手毬", "idol_card_title": "一匹狼",
         "memory_ratings": [12075, 10773, 9978]}  # fmt: skip
    monkeypatch.setattr(
        tp, "idol_card_id", lambda title, code: "i_card-ttmr-2-000" if (title, code) == ("一匹狼", "ttmr") else None
    )
    f = {**f, "page": 1, "next_page": [458, 674], "prev_page": [264, 674]}
    assert p.on_formation(rt, None, NS(fields=f)) is DONE and taps[-1] == (458, 674)  # 先翻到第 2 页读牌组
    deck = {"cards": ["p_card-01-ido-2_017+1", "p_card-00-sup-3_027"], "unread": 1}
    page2 = {"layer": 15, "turns": 15, "page": 2, "challenge": [381, 1091], "prev_page": [264, 674], "deck": deck}
    assert p.on_formation(rt, None, NS(fields=page2)) is DONE
    assert taps == [(446, 987), (458, 674), (381, 1091)]  # おまかせ 没点
    lo = json.loads((tmp_path / "lo.json").read_text(encoding="utf-8"))
    assert lo == {"produce_id": "tower", "tower_id": "tower_001-ttmr", "layer": 15, "turns": 15, "score_bonus_permils": [23090, 11980, 2600],
                  "max_stamina": 46, "idol_card_id": "i_card-ttmr-2-000", "produce_cards": ["p_card-01-ido-2_017+1", "p_card-00-sup-3_027"],
                  "formation_signature": "i_card-ttmr-2-000.12075-10773-9978"}  # fmt: skip
    assert p.run.tower_id == "tower_001-ttmr"
    assert seen_pf[-1]["idol_card_id"] == "i_card-ttmr-2-000"  # 编成评估要偶像卡（3940a9 11-15）
    assert (
        json.loads((tmp_path / "cache" / "tower_001-ttmr-15.json").read_text(encoding="utf-8")) == lo
    )  # 同编成重试时读这份


def test_formation_lands_on_page2_goes_back_first(tmp_path):
    p = _play(tmp_path)
    rt, taps = _rt()
    page2 = {"page": 2, "challenge": [381, 1091], "prev_page": [264, 674], "deck": {"cards": [], "unread": 0}}
    assert p.on_formation(rt, None, NS(fields=page2)) is DONE and taps == [(264, 674)]


def test_formation_change_stops(tmp_path, monkeypatch):
    change = Decision(
        Action(ActionKind.SELECT_FORMATION, {"kind": "tower", "mechanic": None, "main": 3, "subs": [1]}),
        "换",
        "sim",
        0.8,
    )
    p = _play(tmp_path, change)
    monkeypatch.setattr(tp, "idol_code", lambda name: "ttmr")
    with pytest.raises(ResumeBlocked, match="不换编成"):
        p.on_formation(_rt()[0], None, NS(fields={"layer": 15, "challenge": [381, 1091], "page": 1}))


def test_result_retry_until_three_stars_then_stop_after_one_layer(tmp_path):
    p = _play(tmp_path)
    p.layer = 15
    h = p.handlers()
    rt, taps = _rt()
    res = {"cleared": True, "layer": 15, "stars": 1, "retry": [129, 1160], "back": [350, 1160]}
    assert h["TOWER_RESULT"](rt, None, NS(fields=res)) is DONE and taps == [(129, 1160)]  # 没满三星：再挑戦
    # 实机 r2：确认框正在弹出时同一张结果页又被认出来——不再算一次、不再点
    assert h["TOWER_RESULT"](rt, None, NS(fields=res)) is tp.STAY and p.run.tries[15] == 1 and taps == [(129, 1160)]
    h["TOWER_RETRY_CONFIRM"](rt, None, NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]}))  # 换页：开门
    p.exam_no += 1  # 第二次挑战打完（on_exam）
    assert (
        h["TOWER_RESULT"](rt, None, NS(fields={**res, "stars": 3})) is tp.END and p.finished
    )  # 三星：这一层打完，停在结果页
    assert p.run.tries[15] == 2 and taps[-1] == (509, 1160)


def test_result_never_cleared_stops(tmp_path):
    p = _play(tmp_path)
    h = p.handlers()
    rt, _ = _rt()
    res = {"cleared": False, "layer": 15, "stars": 0, "retry": [129, 1160]}
    confirm = NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]})
    h["TOWER_RESULT"](rt, None, NS(fields=res))
    h["TOWER_RETRY_CONFIRM"](rt, None, confirm)
    p.exam_no += 1
    h["TOWER_RESULT"](rt, None, NS(fields=res))
    h["TOWER_RETRY_CONFIRM"](rt, None, confirm)
    p.exam_no += 1
    with pytest.raises(ResumeBlocked, match="没过关"):
        h["TOWER_RESULT"](rt, None, NS(fields=res))


def test_tower_id_from_idol_name_without_formation(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "idol_code", lambda name: "ttmr")
    p = tp.TowerPlay(NS(), TowerRun(None), idol_name="月村手毬", layers=1, loadout_path=tmp_path / "lo.json")
    assert p.run.tower_id == "tower_001-ttmr"  # 从名次页 / 结果页续跑也有路线（实机 r2 汇总里是 None）


def test_result_page_not_recognized_while_retry_confirm_opening():
    from hiro.vision.tower import tower_page

    boxes = [("CLEAR", (300, 210, 420, 240)), ("再挑戦", (90, 1145, 160, 1175)), ("一覧に戻る", (300, 1145, 400, 1175)),
             ("ステージを再挑戦しますか?", (100, 960, 320, 990))]  # fmt: skip
    assert tower_page(None, boxes) is None  # 确认框正文已经出来、标题条还没认出：不当结果页


def test_retry_confirm_free_only(tmp_path):
    p = _play(tmp_path)
    rt, taps = _rt()
    with pytest.raises(ResumeBlocked, match="消耗"):
        p.on_retry_confirm(rt, None, NS(fields={"cost": "ジュエル", "same": [492, 1037], "ok": [509, 1160]}))
    assert p.on_retry_confirm(rt, None, NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]})) is DONE
    assert taps == [(492, 1037), (509, 1160)]


def test_idol_code_from_master():
    assert tp.idol_code("月村 手毬") == "ttmr" and tp.idol_code("不存在") is None


class FakeLayers:
    """层选择页：layers {层: (星, 锁)}；居中 center；拖 d px 挪 round(d / px_per_layer) 层（吸附），往下拖看更高的层。"""

    def __init__(self, layers, center, px_per_layer=200):
        self.layers, self.center, self.px = layers, center, px_per_layer
        self.now = 0.0
        self.taps, self.swipes = [], []

    def rt(self):
        def swipe(x1, y1, x2, y2, duration=None, hold=None):
            self.swipes.append((y1, y2))
            n = round(abs(y2 - y1) / self.px) * (1 if y2 > y1 else -1)
            self.center = min(max(self.center + n, min(self.layers)), max(self.layers))

        return NS(
            device=NS(tap=lambda x, y: self.taps.append((x, y)), swipe=swipe),
            sleep=lambda s: None,
            clock=lambda: self.now,
        )

    def hit(self):
        stars, locked = self.layers[self.center]
        above = locked and self.layers.get(self.center - 1, (0, True))[1]  # 最前沿以上的层没有「挑戦する」
        return NS(fields={"idol": "月村手毬", "stage": self.center, "stars": stars, "locked": locked,
                          "challenge": None if above else [480, 988]})  # fmt: skip


def _drive(p, game, ticks=60):
    rt = game.rt()
    for _ in range(ticks):
        game.now += 2.0
        if p.on_layers(rt, None, game.hit()) is DONE and game.taps:
            return game.center
    return None


def test_layers_three_stars_moves_up_to_frontier(tmp_path):
    """g7 11-17：打完 17 层回到层选择页，居中 17 层已三星，原来直接停。现在往上滑到最前沿 18 层（没三星）打。"""
    p = _play(tmp_path)
    game = FakeLayers(dict.fromkeys(range(1, 18), (3, False)) | {18: (0, False), 19: (0, True), 20: (0, True)}, 17)
    assert _drive(p, game) == 18 and p.layer == 18 and game.swipes[0][1] > game.swipes[0][0]  # 往下拖 = 看更高的层


def test_layers_frontier_locked_goes_down_to_first_not_three_star(tmp_path):
    """最前沿锁着（要攒星）、上面没得打：往下找没三星的层（14 层 1 星）。跳过今天次数用完的 15 层。"""
    p = _play(tmp_path)
    p.run.tries[15] = 3
    game = FakeLayers(
        dict.fromkeys(range(1, 14), (3, False)) | {14: (1, False), 15: (2, False), 16: (0, True), 17: (0, True)},
        16,
        px_per_layer=260,
    )
    assert _drive(p, game) == 14


def test_layers_overshoot_and_calibration(tmp_path):
    """滑一下挪了两层（每层间距比初值小）：越过的层回头补看，不漏掉。"""
    p = _play(tmp_path)
    game = FakeLayers(
        dict.fromkeys(range(1, 18), (3, False)) | {18: (2, False), 19: (0, True), 20: (0, True), 21: (0, True)},
        17,
        px_per_layer=80,
    )
    assert _drive(p, game) == 18


def test_layers_nothing_to_play_stops(tmp_path):
    p = _play(tmp_path)
    game = FakeLayers(dict.fromkeys(range(1, 6), (3, False)) | {6: (0, True), 7: (0, True)}, 5)
    with pytest.raises(ResumeBlocked, match="没有能打的层"):
        _drive(p, game)
    assert game.taps == []


def test_layers_without_challenge_button_waits_one_frame(tmp_path):
    """居中的层没有「挑戦する」：第一帧先等（按钮可能还没出来），第二帧才当锁着往回滑。"""
    p = _play(tmp_path)
    rt, _taps = _rt()
    hit = NS(fields={"idol": "月村手毬", "stage": 19, "stars": 0, "locked": True, "challenge": None})
    assert p.on_layers(rt, None, hit) is tp.STAY


def test_ranking_records_and_taps_next(tmp_path):
    p = _play(tmp_path)
    p.layer = 15
    rt, taps = _rt()
    rows = [{"rank": 1, "name": "白草四音", "score": 105438}, {"rank": 3, "name": "月村手毬", "score": 54009}]
    assert p.on_ranking(rt, None, NS(fields={"my_rank": 3, "rows": rows, "next": [361, 1160]})) is DONE and taps == [
        (361, 1160)
    ]
    assert p.run.summary()["rankings"] == {15: [{"my_rank": 3, "rows": rows}]}


def test_idol_card_id_from_master():
    assert tp.idol_card_id("一匹狼", "ttmr") == "i_card-ttmr-2-000" and tp.idol_card_id("一匹狼", "hski") is None


def test_failed_ranking_retries_on_the_same_page(tmp_path):
    """不合格名次页：没有 CLEAR 结果页，在这一页按重试规则点再挑戦；确认框出来前同一页再被认出不重复算。"""
    p = _play(tmp_path)
    p.layer = 15
    h = p.handlers()
    rt, taps = _rt()
    rows = [{"rank": 1, "name": "白草四音", "score": 105438}, {"rank": 4, "name": "月村手毬", "score": 45231}]
    page = NS(
        fields={"my_rank": None, "rows": rows, "next": None, "failed": True, "retry": [127, 1160], "back": [351, 1160]}
    )
    assert h["TOWER_RANKING"](rt, None, page) is DONE and taps == [(127, 1160)]
    assert h["TOWER_RANKING"](rt, None, page) is tp.STAY and taps == [(127, 1160)] and p.run.tries[15] == 1
    assert p.run.rankings[15] == [{"my_rank": 4, "rows": rows, "failed": True}] and 15 not in p.run.cleared
    h["TOWER_RETRY_CONFIRM"](rt, None, NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]}))
    p.exam_no += 1
    assert h["TOWER_RANKING"](rt, None, page) is DONE and p.run.tries[15] == 2  # 第二次不合格：照样再挑戦


def test_layers_and_ranking_act_once_until_page_changes(tmp_path):
    """实机 11-15：层选择页「挑戦する」2 秒点了 4 次、同一张名次页记了两次。点过一次后页面没换就不再动，ACT_WAIT_S 后还没换才再点；
    换到别的页面再回来照常处理。"""
    p = _play(tmp_path, layers=3)
    h = p.handlers()
    now = [0.0]
    taps = []
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, clock=lambda: now[0])
    layers = NS(fields={"idol": "月村手毬", "stage": 15, "challenge": [446, 987]})
    assert h["TOWER_LAYERS"](rt, None, layers) is DONE
    now[0] = 1.0
    assert h["TOWER_LAYERS"](rt, None, layers) is tp.STAY and taps == [(446, 987)]
    now[0] = 1.0 + tp.ACT_WAIT_S
    assert h["TOWER_LAYERS"](rt, None, layers) is DONE and len(taps) == 2  # 还没换页：过了等待时间再点一次
    p.layer = 15
    rows = [{"rank": 1, "name": "月村手毬", "score": 33229}]
    ranking = NS(fields={"my_rank": 1, "rows": rows, "next": [361, 1160]})
    assert h["TOWER_RANKING"](rt, None, ranking) is DONE
    assert h["TOWER_RANKING"](rt, None, ranking) is tp.STAY
    assert p.run.rankings[15] == [{"my_rank": 1, "rows": rows}]  # 只记一次
    h["TOWER_LAYER_INFO"](rt, None, NS(fields={"conditions": [], "close": None}))  # 换页
    assert h["TOWER_LAYERS"](rt, None, layers) is DONE  # 回来照常处理


def test_navigates_home_to_contest_to_road(tmp_path):
    """首页起跑（g5 11-17：停在首页 4 分钟没动）：首页点「コンテスト」→ コンテスト页点「アイドルへの道」→ 路线列表照常。"""
    from hiro.vision.ocr import Box, Text

    p = _play(tmp_path)
    h = p.handlers()
    now = [0.0]
    taps = []
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, clock=lambda: now[0])
    home = NS(texts=[Text("コンテスト", Box(456, 1162, 536, 1192)), Text("ホーム", Box(330, 1180, 390, 1208))])
    assert h["home"](rt, home, NS(fields=None)) is DONE and taps == [(496, 1177)]
    now[0] = 1.0
    assert h["home"](rt, home, NS(fields=None)) is tp.STAY  # 刚点过，页面还没换
    assert h["TOWER_CONTEST"](rt, NS(texts=[]), NS(fields=None)) is DONE and taps[-1] == tp.ROAD_BUTTON


def test_tower_classify_recognizes_contest_and_home():
    import os

    import cv2

    base = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "tower" / "pages"
    want = {"00_contest.png": "TOWER_CONTEST", "home-g5.jpg": "home", "02_after_story.png": "TOWER_LIST"}
    for name, page in want.items():
        if not (base / name).exists():
            pytest.skip(f"需要实机帧 tower/pages/{name}")
        assert tp.tower_classify(cv2.resize(cv2.imread(str(base / name)), (720, 1280))).name == page, name


def test_find_idol_strips_spaces_and_fuzzy():
    idols = [
        {"name": "花海咲季", "center": [1, 1]},
        {"name": "月村手鞠", "center": [2, 2]},
        {"name": None, "stage": 11, "center": [3, 3]},
    ]
    assert tp.find_idol(idols, "花海 咲季")["center"] == [1, 1]  # 去空格
    assert tp.find_idol(idols, "月村手毬")["center"] == [2, 2]  # OCR 错一个字：相似度够就认
    assert tp.find_idol(idols, "藤田ことね") is None


def test_retry_confirm_fading_after_ok_is_not_a_stop(tmp_path):
    """g5c 11-17：点了「同じ編成で再挑戦」→「OK」，框淡出时又被认成确认框、按钮没读全，原来直接停机，游戏却开始了第 2 次挑戦没人打。
    点过之后同一页不再处理；按钮没读全先等，连续 PARTIAL_WAIT_S 秒都没读全才停。"""
    p = _play(tmp_path)
    h = p.handlers()
    now = [0.0]
    taps = []
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, clock=lambda: now[0])
    full = NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]})
    fading = NS(fields={"cost": None, "same": None, "ok": None})
    assert h["TOWER_RETRY_CONFIRM"](rt, None, full) is DONE and taps == [(492, 1037), (509, 1160)]
    now[0] = 0.5
    assert h["TOWER_RETRY_CONFIRM"](rt, None, fading) is tp.STAY  # 淡出帧：刚点过，不处理、不停
    # 刚弹出、按钮还没读全：等；一直读不全才停
    p2 = _play(tmp_path)
    h2 = p2.handlers()
    now[0] = 100.0
    assert h2["TOWER_RETRY_CONFIRM"](rt, None, fading) is tp.STAY
    now[0] = 100.0 + tp.PARTIAL_WAIT_S
    with pytest.raises(ResumeBlocked, match="没读全"):
        h2["TOWER_RETRY_CONFIRM"](rt, None, fading)


def test_retry_confirm_fading_frame_classified():
    """实机那一帧（OK 刚点下去）：读得出来就是确认框；读不全也只会 STAY（见上）。这里只核判页不出错。"""
    import os

    from hiro.vision.classify import classify

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "tower"
        / "pages"
        / "retry-confirm-ok-tapped-g5c.jpg"
    )
    if not path.exists():
        pytest.skip("需要实机帧")
    hit = classify(cv2.resize(cv2.imread(str(path)), (720, 1280)))
    assert hit.name in ("TOWER_RETRY_CONFIRM", "TOWER_RANKING", "unknown", "DIALOG")


def test_result_counted_once_per_exam_even_after_retry_confirm(tmp_path):
    """g9 11-17：结果页点再挑戦 → 确认框点 OK → 框淡出时下面的结果页又被认出，原来又记一次（tries 1 → 2），
    每层只打 2 次就当用满 3 次。现在按考试配对：没打新的一场就不再计数；RESULT_REDO_S 后结果页还在才照原结论再点一次。"""
    p = _play(tmp_path)
    p.layer = 20
    h = p.handlers()
    now = [0.0]
    taps = []
    rt = NS(device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, clock=lambda: now[0])
    res = NS(fields={"cleared": True, "layer": 20, "stars": 1, "retry": [129, 1160], "back": [350, 1160]})
    assert h["TOWER_RESULT"](rt, None, res) is DONE and p.run.tries[20] == 1
    now[0] = 3.0
    h["TOWER_RETRY_CONFIRM"](rt, None, NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]}))
    now[0] = 4.5
    assert h["TOWER_RESULT"](rt, None, res) is tp.STAY and p.run.tries[20] == 1  # 淡出中的同一张结果页：不再记
    now[0] = 15.0
    n = len(taps)
    assert (
        h["TOWER_RESULT"](rt, None, res) is DONE and p.run.tries[20] == 1 and taps[n:] == [(129, 1160)]
    )  # 过很久还在：重点，不计数
    p.exam_no += 1  # 第二次挑战真的打了
    h["TOWER_RETRY_CONFIRM"](rt, None, NS(fields={"cost": None, "same": [492, 1037], "ok": [509, 1160]}))  # 换页
    now[0] = 30.0
    assert h["TOWER_RESULT"](rt, None, res) is DONE and p.run.tries[20] == 2


def test_date_change_mid_tower_recovers_and_keeps_going(monkeypatch):
    """D4（初星課題规程待办）：偶像之路也接住日切换（初 / N.I.A 在 #62 修过）：回首页后清掉这一层打到一半的状态，
    游戏日变了就把每日次数（tries / best / cleared / gave_up_three，和 tower_cache 的每日记录同一约定）当新的一天，
    从首页重新走进偶像之路接着打，不再整段 traceback 退出。"""
    from types import SimpleNamespace

    from hiro.loop.date_change import DateChanged
    from hiro.runtime import play, tower_cache, tower_play

    calls = []
    monkeypatch.setattr(play, "recover_date_change", lambda rt, session: calls.append("recover"))
    day = ["2026-11-25"]
    monkeypatch.setattr(tower_cache, "game_day", lambda now=None: day[0])
    run = SimpleNamespace(tries={16: 3}, best={16: 2}, cleared={15}, gave_up_three=[16], tower_id=None)
    tp = SimpleNamespace(finished=False, run=run, result_handled=False, _acted=None, _partial_since=None, exam_draws=None,
                         done_layers=2, nav={"seen": {}, "start": None})  # fmt: skip
    tp._nav_reset = lambda: setattr(tp, "nav", {"seen": {}, "start": None})
    seen = []

    def go(until):
        calls.append("run")
        if len(seen) == 0:
            seen.append(1)
            # 打到一半：这些状态都在；然后过了日本时间 5 点
            tp.result_handled, tp._acted, tp._partial_since, tp.exam_draws = True, ("TOWER_RESULT", 1.0), 3.0, {"x": 1}
            tp.nav = {
                "seen": {1: (0, False)},
                "start": 2,
            }  # 选层搜索做到一半（CodeRabbit #96：第 1 层次数用完的记录要清）
            day[0] = "2026-11-26"
            raise DateChanged("日付変更")
        # 日切换后的这一轮：打到一半的状态和前一天的次数都清掉了，已打完的层数保留
        assert tp.result_handled is False and tp._acted is None and tp._partial_since is None and tp.exam_draws is None
        assert run.tries == {} and run.best == {} and run.cleared == set() and run.gave_up_three == []
        assert tp.done_layers == 2
        assert tp.nav == {"seen": {}, "start": None}
        tp.finished = True

    tower_play.play_segment(SimpleNamespace(run=go), object(), tp)
    assert calls == ["run", "recover", "run"] and tp.finished


def test_no_date_change_keeps_daily_counts(monkeypatch):
    from types import SimpleNamespace

    from hiro.runtime import tower_cache, tower_play

    monkeypatch.setattr(tower_cache, "game_day", lambda now=None: "2026-11-25")
    run = SimpleNamespace(tries={16: 3}, best={16: 2}, cleared={15}, gave_up_three=[16], tower_id=None)
    tp = SimpleNamespace(
        finished=False, run=run, result_handled=False, _acted=None, _partial_since=None, exam_draws=None
    )
    tp._nav_reset = lambda: None

    def go(until):
        tp.finished = True

    tower_play.play_segment(SimpleNamespace(run=go), object(), tp)
    assert run.tries == {16: 3} and run.cleared == {15}


def _resume_texts():
    from types import SimpleNamespace

    from hiro.vision.ocr import Box

    rows = [("リタイア", (541, 853, 620, 873)), ("挑戦中のステージ情報があります。", (54, 932, 426, 962)),
            ("ステージを再開しますか?", (56, 965, 340, 995)), ("✕キャンセル", (117, 1146, 311, 1176)), ("再開する", (467, 1148, 579, 1178))]  # fmt: skip
    return [SimpleNamespace(text=t, rect=Box(*b)) for t, b in rows]


def test_resume_dialog_after_date_change_is_tower_resume(monkeypatch):
    """D4b（11-26 04:07 实机）：日切换打断了挑战中的那一层，回首页再进偶像之路弹「アイドルへの道再開」（リタイア / キャンセル / 再開する）。
    认成 TOWER_RESUME，只点「再開する」；原来是通用弹窗、LLM 兜底见到リタイア不敢点，60s 停机。"""
    from hiro.runtime import tower_play
    from hiro.vision import classify as cls
    from hiro.vision import ocr

    monkeypatch.setattr(
        cls,
        "classify",
        lambda img: cls.PageHit(
            "DIALOG", "page_ocr", fields={"kind": "produce_resume", "title": "アイドルへの道再開リ"}
        ),
    )
    monkeypatch.setattr(ocr, "screen_texts", lambda img: _resume_texts())
    hit = tower_play.tower_classify(object())
    assert hit.name == "TOWER_RESUME" and hit.fields["resume"] == (523, 1163)


def test_produce_resume_dialog_of_other_mode_is_not_touched(monkeypatch):
    from hiro.runtime import tower_play
    from hiro.vision import classify as cls

    monkeypatch.setattr(
        cls,
        "classify",
        lambda img: cls.PageHit("DIALOG", "page_ocr", fields={"kind": "produce_resume", "title": "プロデュース再開"}),
    )
    assert tower_play.tower_classify(object()).name == "DIALOG"


def test_resume_handler_taps_resume_only():
    from types import SimpleNamespace

    from hiro.runtime import tower_play

    taps = []
    rt = SimpleNamespace(
        device=SimpleNamespace(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, clock=lambda: 100.0
    )
    tp = tower_play.TowerPlay.__new__(tower_play.TowerPlay)
    tp._acted = None
    out = tp.on_resume(rt, None, SimpleNamespace(fields={"resume": (523, 1163)}))
    assert taps == [(523, 1163)] and out.kind == "done"
