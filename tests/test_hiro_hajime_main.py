"""Campus 広 的默认主属性按模式分开（用户 11-20）：初 Vo（初 LEGEND 排行榜 89% Vo 最高），H.I.F Da（决策-07），N.I.A 先照旧 Da。"""

import pytest

HIRO = "i_card-shro-3-007"


def test_default_main_by_mode():
    from hiro.brain.formation_plan import default_main

    assert default_main("i_card-skin-shro-3-007", "produce-002") == "Vo" and default_main(HIRO, "hajime") == "Vo"
    assert (
        default_main(HIRO, "produce-007") == "Da"
        and default_main(HIRO, "produce-004") == "Da"
        and default_main(HIRO) == "Da"
    )
    assert default_main("i_card-fktn-3-000", "produce-002") is None


@pytest.mark.master
def test_formation_plan_uses_mode():
    from hiro.brain.formation_plan import build, idol_info

    assert idol_info(HIRO, mode="produce-002")["default_main"] == "Vo"
    assert idol_info(HIRO)["default_main"] == "Da"
    plan = build(HIRO, support_inventory=None, memory_inventory=None, mode="produce-002")
    assert plan["mode"] == "produce-002" and plan["main_attr"] == "Vo"


def test_hajime_runtime_main_attr():
    """初没给 --main-attr：为初生成的编成方案用它的；为 H.I.F 生成的（主 Da）不用，按项目默认 Vo。"""
    from hiro.runtime.hajime import hajime_main_attr

    assert hajime_main_attr(HIRO, "produce-002", None) == "Vo"
    assert hajime_main_attr(HIRO, "produce-002", {"main_attr": "Da"}) == "Vo"  # 老方案没记 mode：当 H.I.F 的
    assert hajime_main_attr(HIRO, "produce-002", {"main_attr": "Da", "mode": "produce-007"}) == "Vo"
    assert hajime_main_attr(HIRO, "produce-002", {"main_attr": "Vi", "mode": "produce-001"}) == "Vi"
    assert hajime_main_attr("i_card-fktn-3-000", "produce-002", None) is None


def test_hiro_main_attr_entries_by_mode():
    from hiro.brain import guides_hiro

    es = {e["scope"].get("mode"): e for e in guides_hiro.main_attr_entries()}
    assert (
        es["hajime"]["content"]["prefer"] == ["vo"]
        and es["hif"]["content"]["prefer"] == ["da"]
        and es["nia"]["content"]["prefer"] == ["da"]
    )
    assert {"kind": "decision", "ref": "用户 2026-11-20"} in es["hajime"]["evidence"]
