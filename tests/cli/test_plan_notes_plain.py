"""编成方案的备注给人看：不露内部决策键（formation.support）和卡号（s_card-…），换成「支援卡」和卡名（11-26 真窗口：编成方案卡上直接显示卡号）。"""


def test_summarize_notes_use_labels_and_card_names(monkeypatch):
    from hiro.cli import cmd_plan, reason_text

    names = {"s_card-3-0001": "一起走吧！", "s_card-3-0076": "放学后的约定"}
    monkeypatch.setattr(reason_text, "card_name", lambda cid: names.get(cid))
    plan = {"notes": ["formation.support：攻略推荐、库存里有但方案没选的：s_card-3-0001、s_card-3-0076（按估值没选，人看一下）",
                      "formation.memory：攻略推荐、库存里有但方案没选的：p_card-9-xxx（按估值没选，人看一下）",
                      "支援卡按攻略推荐加分"]}  # fmt: skip
    notes = cmd_plan.summarize(plan)["notes"]
    assert (
        notes[0] == "支援卡：攻略推荐、库存里有但方案没选的：「一起走吧！」、「放学后的约定」（按估值没选，人看一下）"
    )
    assert notes[1].startswith("回忆：") and "formation." not in notes[1]
    assert notes[2] == "支援卡按攻略推荐加分"
