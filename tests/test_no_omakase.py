# 来源：digital-hiro tests/test_no_omakase.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""支援卡和回忆默认不用「おまかせ」（用户 2026-11-02）：开源版要求写编成号，确实想用おまかせ写 auto。"""

from types import SimpleNamespace as NS

import pytest

from hiro import kaa_env
from hiro.loop import kaa_patches


def _reload_env(monkeypatch, **env):
    import importlib

    for k in ("HIRO_SUPPORT_SET", "HIRO_MEMORY_SET", "HIRO_MEMORY_CARDS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(kaa_env)


def test_kaa_env_reads_sets_from_env(monkeypatch):
    env = _reload_env(monkeypatch, HIRO_SUPPORT_SET="4", HIRO_MEMORY_SET="5", HIRO_MEMORY_CARDS="a, b,")
    assert (env.SUPPORT_SET, env.MEMORY_SET, env.MEMORY_CARDS) == (4, 5, ("a", "b"))
    env = _reload_env(monkeypatch, HIRO_SUPPORT_SET="auto")
    assert env.SUPPORT_SET == env.AUTO and env.MEMORY_SET is None and env.MEMORY_CARDS == ()
    with pytest.raises(ValueError):
        _reload_env(monkeypatch, HIRO_SUPPORT_SET="四")
    _reload_env(monkeypatch)


def test_init_kaa_new_requires_sets(monkeypatch):
    env = _reload_env(monkeypatch, HIRO_MEMORY_SET="5")
    with pytest.raises(env.SetupMissing, match="HIRO_SUPPORT_SET"):
        env.init_kaa(None)  # 缺编成号：在碰 KAA 配置之前就停
    _reload_env(monkeypatch)


def test_rent_step2_refuses_omakase_unless_explicit(monkeypatch):
    monkeypatch.setattr(kaa_env, "SUPPORT_SET", None)
    with pytest.raises(RuntimeError):
        kaa_patches._rent_step2(lambda **k: True)(set_number=1, auto_set=True)
    monkeypatch.setattr(kaa_env, "SUPPORT_SET", kaa_env.AUTO)
    seen = []
    assert kaa_patches._rent_step2(lambda **k: seen.append(k) or True)(set_number=None, auto_set=True)
    assert seen == [{"set_number": None, "auto_set": True}]


def test_default_rental_pick_uses_main_attr(monkeypatch):
    from hiro.produce import rental

    from hiro import kaa_env

    got, scorers = [], []
    monkeypatch.setattr(rental, "pick_rental", lambda attr, scorer=None, **k: got.append(attr) or scorers.append(scorer) or True)
    monkeypatch.setattr(kaa_env, "ACTIVE_PLAN", None)
    monkeypatch.setattr(kaa_patches, "RENTAL_ATTR", "Vi")
    assert kaa_patches._default_rental_pick() and got == ["Vi"] and scorers == [None]  # 没有方案：主属性、等级最高
    monkeypatch.setattr(kaa_patches, "RENTAL_ATTR", None)
    assert kaa_patches._default_rental_pick() and got[-1] == rental.WANT_ATTR
    monkeypatch.setattr(kaa_env, "ACTIVE_PLAN", {"support": {"own": [{"support_card_id": "s_card-3-0099", "level": 60}]}, "idol": {}})
    assert kaa_patches._default_rental_pick() and callable(scorers[-1])  # 有方案：按编成价值打分


def _fake_R(next_enabled: bool):
    btn = NS(q=lambda **k: NS(exists=lambda: next_enabled))
    return NS(Produce=NS(TextStepIndicator2=NS(exists=lambda: True), Step2=NS(ButtonNext=btn)))


def test_rent_step2_stops_when_rental_slot_empty(monkeypatch):
    import sys

    import kaa.tasks

    monkeypatch.setattr(kaa.tasks, "R", _fake_R(False))
    monkeypatch.setitem(sys.modules, "kaa.tasks.produce.prepare", NS(_select_set=lambda n: None))
    monkeypatch.setattr(kaa_patches, "device", NS(screenshot=lambda: None), raising=False)
    import kotonebot

    monkeypatch.setattr(kotonebot, "device", NS(screenshot=lambda: None))
    monkeypatch.setattr(kaa_patches, "RENTAL_PICK", None)
    with pytest.raises(kaa_patches.RentalPending):
        kaa_patches._rent_step2(lambda **k: True)(set_number=1, auto_set=False)


def test_rent_step2_passes_when_set_full(monkeypatch):
    import sys

    import kaa.tasks
    import kotonebot

    monkeypatch.setattr(kaa.tasks, "R", _fake_R(True))
    monkeypatch.setitem(sys.modules, "kaa.tasks.produce.prepare", NS(_select_set=lambda n: None))
    monkeypatch.setattr(kotonebot, "device", NS(screenshot=lambda: None))
    assert kaa_patches._rent_step2(lambda **k: True)(set_number=1, auto_set=False) is True


def test_best_row_picks_highest_level_da():
    from hiro.produce.rental import best_row

    rows = [{"card": {"attr": "Vo", "level": 60, "breaks": 4}}, {"card": {"attr": "Da", "level": 50, "breaks": 4}},
            {"card": {"attr": "Da", "level": 60, "breaks": 3}}, {"card": None, "covered": True}]  # fmt: skip
    assert best_row(rows)["card"]["level"] == 60 and best_row(rows)["card"]["attr"] == "Da"
    assert best_row([{"card": {"attr": "Vo", "level": 60}}]) is None


def test_best_row_excludes_plan_mismatch():
    from hiro.produce.rental import best_row

    rows = [{"card": {"attr": "Da", "level": 60, "breaks": 4, "name": "なぜこんなところにッ！？"}},
            {"card": {"attr": "Da", "level": 60, "breaks": 4, "name": "いつまでも続けばいいのに"}}]  # fmt: skip
    assert best_row(rows, exclude={"なぜこんなところにッ！？"})["card"]["name"] == "いつまでも続けばいいのに"


@pytest.mark.lowres_sample("租借页样例 step2-rental-mismatch 是 540x960 截图；DMM 租借页待编成库存扫描（任务单第 6 步）取实机帧补进回归集")
def test_rental_mismatch_reads_real_frame(monkeypatch):
    """第十七局实机帧：租借格（右下）标「プラン不一致」。"""
    from pathlib import Path

    import cv2

    from hiro.produce import rental

    img = cv2.resize(cv2.imread(str(Path(__file__).parent / "fixtures/support/step2-rental-mismatch.png")), (720, 1280))
    monkeypatch.setattr(rental, "device", NS(screenshot=lambda: img))
    assert rental.rental_mismatch()
