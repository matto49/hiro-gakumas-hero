# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""第十八局本战 R2 起的「再挑戦」相关：策略（HifCore）置 exam_restarted、本战重挑上限 / 差距门槛、计数来源。
原来前 3 个测试测旧主循环 hiro.loop.controller 收到 exam_restarted 后重进 on_exam_entered，2026-11-24 随旧主循环删了。"""

import hiro  # noqa: F401  兼容层（先于 kaa）

import pytest


def test_strategy_sets_flag_on_finals_retry(monkeypatch):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HifCore.__new__(hif.HifCore)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    monkeypatch.setattr(
        pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", {"kind": "final", "rank": 2, "outcome": "敗退"})
    )
    from hiro.vision.ocr import Box, Text

    btn = Text("再挑戦", Box(290, 1090, 310, 1110))
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [btn])
    st._click_retry = lambda label, b, texts, **kw: "free"
    st._retry_took_effect = lambda label, **kw: True  # 考试重开的核对另有测试（test_produce_over_detection）
    assert st._finals_retry(None, "r2") is True and st.exam_restarted is True


def test_declined_retry_not_reopened_on_same_page(monkeypatch):
    """Windows 第七局：取消再挑戦（要花ジュエル / 到上限）后，收尾循环每轮又点开确认框、再取消，一直没点「次へ」，空转看门狗停机。
    同一张结算页决定不再挑戦后，不再点开确认框；换了一张结算页（分数不同）照常判断。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HifCore.__new__(hif.HifCore)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    page = {"kind": "final", "rank": 3, "outcome": "敗退", "score": 756342, "round_score": 158038}
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", page))
    from hiro.vision.ocr import Box, Text

    btn = Text("再挑戦", Box(290, 1090, 310, 1110))
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [btn])
    calls = []
    st._click_retry = lambda label, b, texts: calls.append(label) or None
    assert st._finals_retry(None, "r2") is False and st._finals_retry(None, "r2") is False and len(calls) == 1
    page.update(score=900000, round_score=300000)
    assert st._finals_retry(None, "r2") is False and len(calls) == 2


def test_retries_left_from_screen_overrides_trace_count():
    """「あと N回」以画面为准：通信エラー期间 trace 里多记的「再挑戦」不该把剩余次数吃掉。"""
    from types import SimpleNamespace as NS

    from hiro.produce.hif import screen_retries_left

    assert screen_retries_left([NS(text="あと3回"), NS(text="再挑戦")]) == 3
    assert screen_retries_left([NS(text="あと 2 回")]) == 2
    assert screen_retries_left([NS(text="次へ")]) is None


def test_comm_error_after_retry_not_counted(monkeypatch):
    """点再挑戦后弹的是通信エラー：返回 None（不记成免费再挑戦）。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    st = hif.HifCore.__new__(hif.HifCore)
    notes = []
    st.s = NS(note=notes.append, trace=NS(dir="/nonexistent/x"), notes=[])
    st._retries_so_far = lambda label: 0
    monkeypatch.setattr(hif, "device", NS(click=lambda *a: None, screenshot=lambda: None))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(
        hif, "screen_texts", lambda *a, **k: [NS(text="通信エラー"), NS(text="リトライしますか?"), NS(text="はい")]
    )
    from hiro.vision.ocr import Box, Text

    btn = Text("再挑戦", Box(290, 1090, 310, 1110))
    assert st._click_retry("r2", btn, [btn]) is None and any("通信エラー" in n for n in notes)


def _finals_strategy(tmp_path, notes_by_seg):
    """本战策略替身：trace 基名 main-x 的各段 steps.jsonl 里放给定说明（模拟之前各段的再挑戦记录）。"""
    import json
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    for name, notes in notes_by_seg.items():
        d = tmp_path / name
        d.mkdir()
        with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
            for n in notes:
                f.write(json.dumps(n if isinstance(n, dict) else {"seq": 1, "notes": [n]}, ensure_ascii=False) + "\n")
    cur = tmp_path / "main-x-r9"
    cur.mkdir()
    st = hif.HifCore.__new__(hif.HifCore)
    st.s = NS(note=lambda *a: None, notes=[], trace=NS(dir=str(cur)))
    st.scenario = "main"
    st.exam_count = 1
    return st


def test_retry_cap_ignores_label(tmp_path):
    """DMM dmm2：R2 四次挑战标签是 lesson-1 / lesson-2 / r2 / lesson-3。不管标签，本局累计 3 次「本战…使用再挑戦」就到上限。"""
    notes = ["本战敗退（第 3 名，总分 374041），使用再挑戦（free）", "本战敗退（第 3 名，总分 414205），使用再挑戦（ticket）",
             "本战敗退（第 3 名，总分 423062），使用再挑戦（ticket）"]  # fmt: skip
    st = _finals_strategy(tmp_path, {"main-x": notes})
    for label in ("lesson-3", "r2", "lesson-9"):
        assert st._retries_so_far(label) >= st.MAX_RETRIES_PER_EXAM


def test_r2_label_after_r1_result_ignores_scene(tmp_path, monkeypatch):
    """R1 结算过（trace 里有 HIF_FINALS_RANKING round_result）之后的考试一律 r2，不看那一刻的场景（DMM 过场里不是 HIF_EXAM）。"""
    import hiro.vision.scene as scene

    r1 = {"seq": 147, "observation": {"page": {"name": "HIF_FINALS_RANKING"}, "fields": {"kind": "round_result"}}}
    st = _finals_strategy(tmp_path, {"main-x": [r1]})
    monkeypatch.setattr(scene, "classify_current", lambda: (_ for _ in ()).throw(AssertionError("不该再看场景")))
    assert st._battle_label(None) == "r2"


def test_exam_scene_settles_before_lesson(tmp_path, monkeypatch):
    """没有 R1 结算记录、开头那一刻认不出：连看几秒，认出 HIF_EXAM 就不当レッスン。"""
    from types import SimpleNamespace as NS

    import hiro.vision.scene as scene

    st = _finals_strategy(tmp_path, {})
    seq = iter(["IDLE", "IDLE", "HIF_EXAM"])
    monkeypatch.setattr(scene, "classify_current", lambda: NS(scene=next(seq, "HIF_EXAM")))
    t = [0.0]
    assert st._settle_exam_scene("IDLE", clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)) == "HIF_EXAM"
    seq2 = iter(["IDLE"] * 100)
    monkeypatch.setattr(scene, "classify_current", lambda: NS(scene=next(seq2)))
    t[0] = 0.0
    assert st._settle_exam_scene("IDLE", clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)) == "IDLE"
    assert t[0] >= st.EXAM_SCENE_SETTLE_S


def test_fourth_finals_retry_refused_without_screen_count(tmp_path, monkeypatch):
    """画面上没读到「あと N回」时按 trace 累计：三次标签各不相同的「本战…使用再挑戦」之后，第 4 次（标签又变了）必须拒绝、不点按钮。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    notes = ["lesson-1 本战敗退（第 3 名），使用再挑戦（free）", "lesson-2 本战敗退（第 3 名），使用再挑戦（ticket）",
             "r2 本战敗退（第 3 名），使用再挑戦（ticket）"]  # fmt: skip
    st = _finals_strategy(tmp_path, {"main-x": notes})
    clicks = []
    monkeypatch.setattr(hif, "device", NS(click=lambda *a: clicks.append(a)))
    from hiro.vision.ocr import Box, Text

    btn = Text("再挑戦", Box(290, 1090, 310, 1110))
    assert st._click_retry("lesson-3", btn, [btn]) is None and clicks == []


def _final_page(me_total, me_round, first_total):
    return {"kind": "final", "rank": 2, "outcome": "敗退", "score": me_total, "round_score": me_round,
            "entries": [{"rank": 1, "name": "十王星南", "score": first_total, "me": False},
                        {"rank": 2, "name": "篠澤広", "score": me_total, "round_score": me_round, "me": True}]}  # fmt: skip


def _gap_strategy(monkeypatch, page):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages
    from hiro.vision.ocr import Box, Text

    st = hif.HifCore.__new__(hif.HifCore)
    notes, calls = [], []
    st.s = NS(note=notes.append)
    st.rec = NS(log=lambda *a, **k: None)
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", page))
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [Text("再挑戦", Box(290, 1090, 310, 1110))])
    st._click_retry = lambda label, b, texts: calls.append(label) or "ticket"
    st._retry_took_effect = lambda label, **kw: True
    return st, notes, calls


def test_finals_retry_skipped_when_far_behind_first(monkeypatch):
    """mainz4（11-18）R2：第 2 名 846,075（本轮 362,089），第 1 名 十王星南 1,080,506，差 23.4 万 = 本轮分的 65%；
    四次 R2 本轮分只在 33–38 万之间，重打追不上，还把更好的一次（866,880）覆盖掉、多花 3 张券。用户 11-18：差距很大就不要重打。"""
    st, notes, calls = _gap_strategy(monkeypatch, _final_page(846075, 362089, 1080506))
    assert st._finals_retry(None, "r2") is False and calls == []
    assert any("差距" in n and "不再挑戦" in n for n in notes)


def test_finals_retry_kept_when_close_to_first(monkeypatch):
    """mainverify4（11-17）：离第 1 约 9 千，照常重打。"""
    st, _notes, calls = _gap_strategy(monkeypatch, _final_page(1071491, 400000, 1080506))
    assert st._finals_retry(None, "r2") is True and calls == ["r2"]


def test_finals_retry_gap_unreadable_keeps_old_rule(monkeypatch):
    page = _final_page(846075, None, 1080506)  # 本轮分没读到：算不了差距，按原规则重打
    st, _notes, _calls = _gap_strategy(monkeypatch, page)
    assert st._finals_retry(None, "r2") is True


def test_finals_retry_gap_threshold_env(monkeypatch):
    monkeypatch.setenv("HIRO_RETRY_GAP_MAX", "1.0")  # 放宽到 100%：65% 的差距也重打
    st, _notes, _calls = _gap_strategy(monkeypatch, _final_page(846075, 362089, 1080506))
    assert st._finals_retry(None, "r2") is True


def test_retry_cap_env(tmp_path, monkeypatch):
    """用户 11-20：考试失败后最多重试 1 次，别把再挑戦用光。HIRO_RETRY_MAX 只收紧我们的上限，画面「あと N回」照游戏的 3 次算已用。"""
    st = _finals_strategy(tmp_path, {"main-x": []})
    monkeypatch.delenv("HIRO_RETRY_MAX", raising=False)
    assert st.retry_cap() == 3
    for v, want in (("1", 1), ("0", 0), ("9", 3), ("x", 3)):
        monkeypatch.setenv("HIRO_RETRY_MAX", v)
        assert st.retry_cap() == want


def _cap1(tmp_path, monkeypatch, left, notes_before=()):
    st = _finals_strategy(tmp_path, {"main-x": []})
    monkeypatch.setenv("HIRO_RETRY_MAX", "1")
    import hiro.produce.hif as hif

    monkeypatch.setattr(hif, "screen_retries_left", lambda texts: left)
    from types import SimpleNamespace as NS

    notes = list(notes_before)
    st.s = NS(note=notes.append, notes=notes, trace=st.s.trace)
    return st, notes, hif


def test_retry_cap_one_blocks_second_retry(tmp_path, monkeypatch):
    """上限 1：这一场本局已经用过 1 次（trace 记录）→ 不再点再挑戦（不碰按钮）。"""
    st, notes, _ = _cap1(tmp_path, monkeypatch, 2, ["qualify-2 使用再挑戦（免费）"])
    assert st._click_retry("qualify-2", object(), []) is None
    assert "上限 1" in notes[-1]


def test_retry_cap_one_ignores_daily_shared_count(tmp_path, monkeypatch):
    """用户 11-21：「あと N回」是当天全局共用的。画面あと1回、这一场本局没重试过 → 照样重试一次（会去点按钮）。"""
    st, _notes, hif = _cap1(tmp_path, monkeypatch, 1)

    class Clicked(Exception):
        pass

    monkeypatch.setattr(
        hif, "device", type("D", (), {"click": staticmethod(lambda *a: (_ for _ in ()).throw(Clicked()))})
    )
    btn = type("B", (), {"rect": type("R", (), {"center": (360, 1100)})()})()
    with pytest.raises(Clicked):
        st._click_retry("qualify-2", btn, [])


def test_retry_cap_one_stops_when_daily_pool_empty(tmp_path, monkeypatch):
    st, notes, _ = _cap1(tmp_path, monkeypatch, 0)
    assert st._click_retry("qualify-2", object(), []) is None
    assert "用完" in notes[-1]
