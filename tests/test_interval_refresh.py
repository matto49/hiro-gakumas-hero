"""轮间货架没有复制 / 爆发饮料时先刷新（开关 HIRO_INTERVAL_REFRESH，默认开，=0 关）。字段取自 Windows 第七局 seq173。"""

import copy

import pytest

from hiro.brain import deciders as dmod
from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

WIN7 = {"stamina": 17, "max_stamina": 33, "produce_point": 580, "complete": True, "dialog": None, "interval": {
    "cards": [{"index": 0, "price": 50, "purchased": False, "card_id": "p_card-02-men-2_056", "name": "止められない想い+", "upgrade": 1}],
    "drinks": [{"index": 6, "price": 100, "purchased": False, "drink_id": "pdrink_00-3-001", "name": "初星ホエイプロテイン"},
               {"index": 7, "price": 100, "purchased": False, "drink_id": "pdrink_00-3-005", "name": "初星スペシャル青汁"}],
    "upgrade": {"price": 100, "available": True, "left": 1}, "recover": {"available": True, "price_per_2hp": 10},
    "refresh": {"price": 10, "available": True}}}  # fmt: skip


ROTATE = ["p_card-02-men-2_056", "p_card-02-men-2_008", "p_card-02-men-2_054", "p_card-00-men-3_012"]  # 第七局轮间货架上的卡


def _decide(fields, d=None):
    d = d or ProduceDeciders(CAMPUS_HIRO, None)
    return d, d.interval(GameState(page_fields=copy.deepcopy(fields)))


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_off_buys_drink(monkeypatch):
    monkeypatch.setattr(dmod, "INTERVAL_REFRESH", False)
    _, dec = _decide(WIN7)
    assert dec.action.kind is ActionKind.INTERVAL_BUY and dec.action.args.get("index") in (6, 7)


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_on_refreshes_without_copy_or_burst(monkeypatch):
    monkeypatch.setattr(dmod, "INTERVAL_REFRESH", True)
    d, dec = _decide(WIN7)
    assert dec.action.kind is ActionKind.REROLL and dec.action.args == {"scope": "interval"}
    # 货架没变（没刷成）：不再刷，照常买
    _, dec2 = _decide(WIN7, d)
    assert dec2.action.kind is ActionKind.INTERVAL_BUY


@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_on_stops_after_max(monkeypatch):
    monkeypatch.setattr(dmod, "INTERVAL_REFRESH", True)
    d = ProduceDeciders(CAMPUS_HIRO, None)
    kinds = []
    for i in range(dmod.INTERVAL_REFRESH_MAX + 1):
        f = copy.deepcopy(WIN7)
        f["produce_point"] -= 10 * i
        f["interval"]["cards"][0].update(card_id=ROTATE[i % len(ROTATE)], index=i)  # 每次刷完货架都不一样
        kinds.append(_decide(f, d)[1].action.kind)
    assert kinds[:-1] == [ActionKind.REROLL] * dmod.INTERVAL_REFRESH_MAX and kinds[-1] is ActionKind.INTERVAL_BUY


@pytest.mark.parametrize("patch", [
    lambda f: f["interval"]["drinks"][0].update(drink_id="pdrink_02-3-010", name="特製ハツボシエキス"),  # 已有复制
    lambda f: f["interval"]["drinks"][0].update(purchased=True),  # 已买过饮料
    lambda f: f.update(produce_point=200),  # 刷完不够买两瓶
    lambda f: f["interval"]["drinks"][1].update(drink_id=None),  # 有一格没认出
])  # fmt: skip
@pytest.mark.master  # 决策器 / 状态机经 catalog 查主数据 game.db；CI 没有主数据时跳过
def test_on_no_refresh(monkeypatch, patch):
    monkeypatch.setattr(dmod, "INTERVAL_REFRESH", True)
    f = copy.deepcopy(WIN7)
    patch(f)
    assert _decide(f)[1].action.kind is not ActionKind.REROLL



@pytest.mark.parametrize("env,expect", [(None, "True"), ("0", "False"), ("1", "True")])
def test_default_on(env, expect):
    import os
    import subprocess
    import sys

    e = {k: v for k, v in os.environ.items() if k != "HIRO_INTERVAL_REFRESH"}
    if env is not None:
        e["HIRO_INTERVAL_REFRESH"] = env
    out = subprocess.run([sys.executable, "-c", "from hiro.brain import deciders; print(deciders.INTERVAL_REFRESH)"],
                         env=e, capture_output=True, text=True, check=True).stdout.strip()
    assert out.splitlines()[-1] == expect
