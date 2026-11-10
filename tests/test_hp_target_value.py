"""HP_TARGET_VALUE（默认关）：轮间回体力目标按价值表每 P 回报逐段往上（主仓库 1b16f1b，线 400 分 / P）。"""


def test_hp_target_value(monkeypatch):
    from hiro.sim import interval as iv

    iv._hp_table.cache_clear()
    assert iv.value_hp_target(33, 17) == 17  # 默认关
    monkeypatch.setattr(iv, "HP_TARGET_VALUE", True)
    monkeypatch.setattr(iv, "_hp_table", lambda: (17, 33, {17: 0.0, 19: 5_289, 24: 23_103, 33: 33_254}))
    assert iv.value_hp_target(33, 17) == 24  # 17→19 约 530、19→24 约 590、24→33 约 250 分 / P
    assert iv.value_hp_target(40, 21) == 21  # 最大体力和价值表不一致：不用
    monkeypatch.setattr(iv, "HP_VALUE_LINE", 200)
    assert iv.value_hp_target(33, 17) == 33
    monkeypatch.setattr(iv, "HP_VALUE_LINE", 600)
    assert iv.value_hp_target(33, 17) == 17
    assert iv.value_hp_target(33, 25) == 25  # 角色目标更高时取角色目标


def test_hp_table_reads_expert():
    from hiro.sim import interval as iv

    iv._hp_table.cache_clear()
    tab = iv._hp_table()
    assert tab is not None and tab[0] == 17 and tab[1] == 33 and {19, 24, 33} <= set(tab[2])
