"""开局编成方案（2026-11-06）：支援卡主属性 / 逆练、回忆流派表与 A/B 栏、方案文件、开局按方案用编成号并核对。"""

import json
from pathlib import Path

from hiro.brain import formation_plan as fp
from hiro.brain import support_select as ss
from hiro.brain.formation import select_memories
from hiro.produce.formation_check import check_memory_step3, check_support_step2, memory_set_number
from hiro.state.context import catalog
from hiro.state.memory_priority import PROFILES, profile_for_idol

import cv2
import pytest

FIX = Path(__file__).with_name("fixtures") / "support"
HIRO = "i_card-shro-3-007"


def _have_master() -> bool:
    try:
        catalog().one("IdolCard", HIRO)
        return True
    except Exception:  # noqa: BLE001  没下载主数据（scripts/setup.sh 会下载）
        return False


pytestmark = pytest.mark.skipif(not _have_master(), reason="需要主数据（scripts/setup.sh 下载）")


def _need(*names):
    return pytest.mark.skipif(
        not all((FIX / n).is_file() for n in names), reason="需要实机截图 tests/fixtures/support/" + "、".join(names)
    )


def _max(card_id):
    c = catalog()
    card = c.one("SupportCard", card_id)
    lim = sorted(c.by_id("SupportCardLevelLimit", card["supportCardLevelLimitId"]), key=lambda r: r["levelLimit"])[-1]
    return {"support_card_id": card_id, "level": lim["levelLimit"], "limit_rank": lim["rank"]}


def _pool(attr, n, plan="ProducePlanType_Plan2"):
    out = []
    for r in catalog().rows("SupportCard"):
        if (
            r["rarity"] == "SupportCardRarity_Ssr"
            and ss.ATTR_OF.get(r["type"]) == attr
            and r["planType"] in ("ProducePlanType_Common", plan)
        ):
            out.append(_max(r["id"]))
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------- 支援卡


def test_using_restores_and_default_matches_original_tables():
    before = (ss.MAIN_ATTR, ss.PLAN, dict(ss.GROWTH_BASE), dict(ss.ROUTE["lessons"]), ss.MAIN_COUNT)
    with ss.using("Da", "ProducePlanType_Plan2"):
        assert (dict(ss.GROWTH_BASE), dict(ss.ROUTE["lessons"])) == (before[2], before[3])  # 默认值下重排结果和原表一样
    with ss.using("Vo", "センス", 3):
        assert ss.MAIN_ATTR == "Vo" and ss.PLAN == "ProducePlanType_Plan1" and ss.MAIN_COUNT == 3
        assert (
            ss.ROUTE["lessons"]["Vo"] == ss.MAIN_LESSONS
            and ss.ROUTE["lessons"]["Da"] == 0
            and ss.GROWTH_BASE["Vo"] == ss.GROWTH_MAIN
        )
    assert (ss.MAIN_ATTR, ss.PLAN, dict(ss.GROWTH_BASE), dict(ss.ROUTE["lessons"]), ss.MAIN_COUNT) == before


def _fake_choose(values):
    def choose(inv, size=6):
        v = values[ss.MAIN_ATTR]
        return {"error": "凑不出"} if v is None else {"main_attr": ss.MAIN_ATTR, "value": v, "own": [], "counts": {}}

    return choose


@pytest.mark.parametrize(("values", "default", "pick"), [
    ({"Vo": 1050, "Da": 1000, "Vi": 900}, "Da", "Da"),  # 只高 50：不逆练 [ep02]
    ({"Vo": 1150, "Da": 1000, "Vi": 900}, "Da", "Vo"),  # 高 150：逆练
    ({"Vo": 1150, "Da": None, "Vi": 900}, "Da", "Vo"),  # 默认凑不出：用最高的
    ({"Vo": 900, "Da": 1000, "Vi": 1200}, None, "Vi"),  # 没有默认：最高
])  # fmt: skip
def test_choose_main_reverse_rule(monkeypatch, values, default, pick):
    monkeypatch.setattr(ss, "choose", _fake_choose(values))
    r = ss.choose_main({}, default_attr=default)
    assert r["main_attr"] == pick and set(r["main_attr_candidates"]) == {"Vo", "Da", "Vi"} and r["main_attr_why"]
    assert any("[ep08]" in n for n in r["notes"])


def test_choose_main_real_cards_and_dict_inventory():
    cards = _pool("Da", 5) + _pool("Vo", 3) + _pool("Vi", 2)
    inv = {
        "cards": {e["support_card_id"]: {k: v for k, v in e.items() if k != "support_card_id"} for e in cards}
    }  # tools.support_inventory 格式
    r = ss.choose_main(inv, plan="ProducePlanType_Plan2", default_attr="Da", size=5, main_count=3)
    assert "error" not in r and len(r["own"]) == 5 and r["counts"][r["main_attr"]] == 3


# ---------------------------------------------------------------- 回忆流派


def test_profile_for_idol():
    assert profile_for_idol(HIRO) == ("yaruki", None)
    assert profile_for_idol("i_card-skin-amao-1-001")[0] == "review_smile"
    assert profile_for_idol("i_card-amao-3-001")[0] == "sense_spotlight"
    prof, caveat = profile_for_idol("i_card-amao-3-009")
    assert prof == "anomaly_tsuyoki" and caveat  # 全力型借用强气表，要标出来


def test_profile_tables_resolve_in_master():
    names = {r["id"]: r["name"] for r in catalog().rows("ProduceCard") if r["upgradeCount"] == 0}
    for key, prof in PROFILES.items():
        for e in prof["priority"]:
            assert names.get(e["card_id"]) == e["name"].rstrip("+"), (key, e["card_id"])
        if prof["ab_target"]:
            assert {e["slot"] for e in prof["priority"]} == {"A", "B"} and sum(prof["ab_target"]) == 4


def _m(i, card_id, **kw):
    return dict(id=i, card_id=card_id, upgrade=kw.pop("upgrade", 1), **kw)


SENSE = {"memories": [_m(1, "p_card-01-men-2_037"), _m(2, "p_card-01-act-3_185"), _m(3, "p_card-01-act-2_001"), _m(4, "p_card-01-men-2_104"),
                      _m(5, "p_card-00-men-3_005"), _m(6, "p_card-00-men-2_012")]}  # fmt: skip


def test_sense_profile_prefers_two_a_two_b():
    r = select_memories(SENSE, plan="ProducePlanType_Plan1", profile="sense_spotlight")
    slots = sorted(c["slot"] for c in r["chosen"])
    assert slots == ["A", "A", "B", "B"]  # [いおん09] A 2 : B 2
    assert {c["card_id"] for c in r["chosen"]} >= {"p_card-01-men-2_037", "p_card-01-act-3_185"}
    assert r["profile"] == "sense_spotlight"


def test_ab_penalty_when_column_empty():
    only_b = {"memories": SENSE["memories"][:4]}
    r = select_memories(only_b, plan="ProducePlanType_Plan1", profile="sense_spotlight")
    assert any("A 栏 0 张" in n for n in r["notes"])


def test_upgrade_matters_for_must_upgrade_cards():
    inv = {"memories": [_m(1, "p_card-01-act-3_185", upgrade=0), _m(2, "p_card-01-act-3_185", upgrade=1)]}
    r = select_memories(inv, plan="ProducePlanType_Plan1", profile="sense_spotlight")
    assert r["chosen"][0]["id"] == 2  # 脚光要强化 [いおん09]


def test_yaruki_default_unchanged():
    inv = {"memories": [_m(1, "p_card-02-men-2_004", timing="start"), _m(2, "p_card-02-act-1_037", timing="start"),
                        _m(3, "p_card-02-act-3_039"), _m(4, "p_card-00-men-3_005")]}  # fmt: skip
    assert [c["card_id"] for c in select_memories(inv)["chosen"]] == [
        c["card_id"] for c in select_memories(inv, profile="yaruki")["chosen"]
    ]


# ---------------------------------------------------------------- 方案文件


def _hiro_plan(tmp_path=None):
    cards = _pool("Da", 5) + _pool("Vo", 3) + _pool("Vi", 3)
    mem = {"memories": [_m(1, "p_card-02-men-2_004", timing="start"), _m(2, "p_card-02-act-1_037", timing="start"),
                        _m(3, "p_card-02-act-3_039"), _m(4, "p_card-02-act-3_186"), _m(5, "p_card-00-men-3_005")]}  # fmt: skip
    return fp.build(HIRO, support_inventory={"cards": cards}, memory_inventory=mem, support_set=20, memory_set=20)


def test_build_plan_for_hiro(tmp_path):
    plan = _hiro_plan()
    assert plan["profile"] == "yaruki" and plan["idol"]["default_main"] == "Da"
    sup, mem = plan["support"], plan["memory"]
    # 没有实机租借列表：租借格按满级 SSR 估，6 格一起评估（主属性 4 张，第 6 格只能是租借，协调 11-17）
    assert "error" not in sup and len(sup["own"]) == 5 and sup["counts"][plan["main_attr"]] == 4
    assert sup["rental"] and sup["rental"]["rental"] and sup["rental"]["level"] == 60 and sup["rental_estimated"]
    assert len(mem["cards"]) == 4 and "p_card-02-men-2_004" in mem["cards"]
    text = fp.format_report(plan)
    assert "编成 20" in text and "回忆" in text
    fp.write(plan, tmp_path / "plan.json", tmp_path / "plan.md")
    assert fp.load(tmp_path / "plan.json")["memory"]["cards"] == mem["cards"] and (tmp_path / "plan.md").is_file()
    assert fp.load(tmp_path / "missing.json") is None


def test_build_without_inventories_reports_errors():
    plan = fp.build(HIRO, support_inventory=None, memory_inventory=None)
    assert (
        plan["support"]["error"]
        and plan["memory"]["error"]
        and plan["main_attr"] == "Da"
        and plan["memory"]["cards"] == []
    )
    fp.format_report(plan)


def test_kaa_env_applies_plan(tmp_path, monkeypatch):
    from hiro import kaa_env

    for k, v in (
        ("SUPPORT_SET", None),
        ("MEMORY_SET", None),
        ("MEMORY_CARDS", ()),
        ("ACTIVE_PLAN", None),
        ("PLAN_MAIN_ATTR", None),
    ):
        monkeypatch.setattr(kaa_env, k, v)  # 相当于没设 HIRO_SUPPORT_SET / HIRO_MEMORY_SET / HIRO_MEMORY_CARDS
    monkeypatch.delenv("HIRO_FORMATION_PLAN", raising=False)
    plan = _hiro_plan()
    path = tmp_path / "plan.json"
    fp.write(plan, path, None)
    assert kaa_env.apply_formation_plan("i_card-skin-amao-1-001", str(path)) is None  # 别的偶像：不用
    assert kaa_env.ACTIVE_PLAN is None and kaa_env.SUPPORT_SET is None
    assert kaa_env.apply_formation_plan("i_card-skin-shro-3-007", str(path)) is not None
    assert (kaa_env.SUPPORT_SET, kaa_env.MEMORY_SET) == (20, 20) and tuple(
        plan["memory"]["cards"]
    ) == kaa_env.MEMORY_CARDS
    assert plan["main_attr"] == kaa_env.PLAN_MAIN_ATTR
    monkeypatch.setattr(kaa_env, "SUPPORT_SET", 4)  # 环境变量写了编成号：以环境变量为准
    kaa_env.apply_formation_plan(None, str(path))
    assert kaa_env.SUPPORT_SET == 4
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")
    assert kaa_env.apply_formation_plan(None, str(path)) is None and kaa_env.ACTIVE_PLAN is None
    assert kaa_env.apply_formation_plan(None, str(tmp_path / "none.json")) is None


# ---------------------------------------------------------------- 开局核对


def _step2():
    from hiro.vision.support_screens import read_support_formation

    return read_support_formation(cv2.imread(str(FIX / "04-step2-support.png")))


def _plan_from(read, main="Da"):
    own = [{"support_card_id": s["support_card_id"], "name": s["name"], "attr": s["attr"]} for s in read["slots"]]
    return {"main_attr": main, "support": {"set": read["number"], "own": own}}


@_need("04-step2-support.png")
def test_check_support_step2_on_real_frame():
    read = _step2()
    assert all(s["support_card_id"] for s in read["slots"])
    assert check_support_step2(read, _plan_from(read)) == ([], [])
    other = _plan_from(read)
    other["support"]["own"][0] = {"support_card_id": "x", "name": "别的卡", "attr": "Vo"}
    problems, _ = check_support_step2(read, other)
    assert any("方案外的卡" in p for p in problems) and any("缺方案的卡" in p for p in problems)
    wrong_set = _plan_from(read)
    wrong_set["support"]["set"] = (read["number"] or 0) + 1
    assert any("编成号" in p for p in check_support_step2(read, wrong_set)[0])


@_need("04-step2-support.png")
def test_check_support_unknown_tile_only_warns():
    read = _step2()
    plan = _plan_from(read)
    read["slots"][2] = dict(read["slots"][2], support_card_id=None, name=None)
    problems, warnings = check_support_step2(read, plan)
    assert problems == [] and warnings
    assert check_support_step2(None, plan)[0] == []  # 没认出页面：不拦


@_need("step3-memory.jpg")
def test_memory_set_number_on_real_frame():
    from hiro.produce.ops import ocr_boxes

    img = cv2.imread(str(FIX / "step3-memory.jpg"))
    assert memory_set_number([(t, b) for t, _, b in ocr_boxes(img)]) == 1  # 左侧「1/20」；右上「親愛度 37/37」不算


def test_memory_set_check():
    plan = {"memory": {"set": 5, "cards": ["a"]}}
    n = 1
    assert check_memory_step3(n, plan)[0] and check_memory_step3(5, plan) == ([], [])
    assert check_memory_step3(None, plan) == ([], ["第 3 步编成号没读出来，跳过核对"])
    assert memory_set_number(["37/37", "12/20"]) == 12


def test_plan_file_is_json_serializable():
    json.dumps(_hiro_plan(), ensure_ascii=False)


def test_scan_entries_keep_inventory_position():
    """hiro.memory_scan 扫出来的记录（card 是 dict、stats 是 dict、带 slot / cell）：方案里选中的回忆要带上 cell 和 key，
    开局自动编成靠它们找回这份回忆。以前 normalize_memory 先转格式再取位置，cell / key 全是 None（11-16 Mac 实机：
    「方案里的回忆没有库存位置（旧版方案）」不开局）。"""

    def entry(i, card_id, timing="start"):
        return {"card": {"card_id": card_id, "name": card_id, "upgrade": 1}, "timing": timing, "timing_text": "",
                "abilities": [{"text": "初期ダンス上昇+20", "disabled": False}], "stats": {"dance": 20}, "card_text": "",
                "effect": "", "page": 0, "slot": i, "cell": [i // 3, i % 3]}  # fmt: skip

    inv = {"entries": [entry(0, "p_card-02-men-2_004"), entry(1, "p_card-02-act-1_037"), entry(2, "p_card-02-act-3_039", "mid"),
                       entry(3, "p_card-02-men-3_002", "mid"), entry(4, "p_card-00-men-3_005", "mid")]}  # fmt: skip
    chosen = select_memories(inv)["chosen"]
    assert chosen and all(c.get("cell") is not None and c.get("key") for c in chosen)


def test_support_step2_title_ocr_bo_variant():
    """第 2 步标题「2.サポート選択」被 OCR 读成「2.サボート選択」（11-16 Mac 账号 2）：也要认成 step2 页。"""
    from hiro.vision.support_screens import read_support_formation

    import numpy as np

    img = np.zeros((1280, 720, 3), np.uint8)
    boxes = [
        ("2.サボート選択", (56, 39, 201, 64)),
        ("20/20", (40, 650, 130, 680)),
        ("サボートカード編成 20", (30, 980, 300, 1010)),
    ]
    page = read_support_formation(img, boxes)
    assert page is not None and page["view"] == "step2" and page["name"] == "サポートカード編成20"
