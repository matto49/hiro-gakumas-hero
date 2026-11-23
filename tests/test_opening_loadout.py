"""任务 ②：合成开局换成这一局的编成（P 道具 / 支援卡 / 回忆附魔），不带模板那一局的（任务 ① 查到的污染）。"""

import pytest

pytestmark = pytest.mark.sim_master

DECK = [
    ("p_card-02-men-1_032", 1),
    ("p_card-02-act-2_062", 1),
    ("p_card-02-men-3_040", 0),
    ("p_card-02-ido-3_105", 1),
] * 3


def _env(**kw):
    from hiro.sim import interval as iv

    return iv._env(
        iv.R2Setup(deck=DECK, drinks=[], hp=20, max_hp=33, permil={"Vo": 37490, "Da": 36100, "Vi": 22860}, **kw), 1
    )


def _ids(env):
    import json

    d = json.loads(env.rx.export_json()) if hasattr(env, "rx") else None
    return [x[0] for x in d["items"]], [x[0] for x in d["supports"]]


def test_template_loadout_kept_when_not_given():
    items, sups = _ids(_env())
    assert "pitem_02-3-076-0" in items and "s_card-3-0010" in sups  # 模板那一局的


def test_run_loadout_replaces_template():
    from hiro.sim.engine import master_commit

    from gakumas_exam.loadout import support_entry
    from gakumas_exam.master import shared

    m = shared(master_commit())
    sup = (support_entry(m, "s_card-3-0029", 60),)
    items, sups = _ids(
        _env(
            items=("pitem_02-3-062-0", "customize_pitem-02-04-3-003_00-04-2-003_02-02-1-001_00-04-4-001"),
            supports=sup,
            skills=(),
        )
    )
    assert items == ["pitem_02-3-062-0", "customize_pitem-02-04-3-003_00-04-2-003_02-02-1-001_00-04-4-001"]
    assert sups == ["s_card-3-0029"]


def test_custom_item_reactions_use_master_cap():
    from hiro.sim import interval as iv

    # リボンくま（赤）：「以降の試験開始時、好印象+3・やる気+3（プロデュース中2回）」→ 本战时按触发满 2 次叠层（验证 2 真值）
    assert iv._custom_reactions("customize_pitem-02-04-3-001_00-04-2-001_02-02-1-001_00-04-4-002") == 2
    assert iv._custom_reactions("pitem_02-3-062-0") == 0
