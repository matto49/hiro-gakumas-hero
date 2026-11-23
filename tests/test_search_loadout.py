"""任务 ②：这一局的编成 → 合成开局的 P 道具 / 支援卡 / 回忆附魔。"""

from hiro.search import loadout as lo

import pytest

pytestmark = pytest.mark.sim_master


def test_memory_ability_text_maps_to_hif_skill():
    sid = next(s for s, d in lo.hif_memory_skills().items() if "デイドリーミング使用後" in d)
    ocr = "試験ごとに、以降1回まで、デイドリーミング 使用後、ランダムな山札か捨札の眠気を除外に移動 H.I.F専用 重複発動不可"
    assert lo.skills_from_abilities([ocr, "初期ダンス上昇+20", ocr]) == [sid]


def test_run_loadout_from_plan_and_ledger():
    plan = {
        "support": {
            "own": [{"support_card_id": "s_card-3-0029", "level": 60}],
            "rental": {"support_card_id": "s_card-3-0010", "level": 60},
        },
        "memory": {"chosen": [{"card_id": "x", "abilities": ["以降1回まで、デイドリーミング使用後、眠気を除外"]}]},
    }
    got, notes = lo.run_loadout({"items": ["pitem_02-3-062-0"]}, plan)
    assert got["items"] == ("pitem_02-3-062-0",)
    assert [s["_supportCardId"] for s in got["supports"]] == ["s_card-3-0029", "s_card-3-0010"]
    assert len(got["skills"]) == 1 and not notes


def test_missing_sources_are_empty_not_template():
    got, notes = lo.run_loadout({}, None)
    assert got == {"items": (), "supports": (), "skills": ()} and len(notes) == 2
