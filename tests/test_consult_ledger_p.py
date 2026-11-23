"""选拔相談记账按 P 差（DMM 第 12 天）：入场 480，买 パワフル漢方ドリンク 实扣 75（标价误读 15）、刷新、删卡实扣 100、
在过期货架上买 ホットコーヒー 失败（P 不变）。以前「本次已花」记成 65（15 + 失败的 50，删卡没记上），现在是 175。"""

from types import SimpleNamespace as NS

from hiro.brain.deciders import ProduceDeciders
from hiro.contracts import GameState


def _visit(d, p, last=None, shelf=(), services=None):
    if last is not None:
        d._consult_state["last"] = last
    return d._consult_visit(GameState(), NS(mode="selection", step=12, produce_point=p), list(shelf), services or {})


def test_spent_follows_p_delta():
    d = ProduceDeciders.__new__(ProduceDeciders)
    v = _visit(d, 480)
    assert (v["entry_p"], v["spent"], v["p"]) == (480, 0, 480)
    v = _visit(d, 405, {"label": "买 パワフル漢方ドリンク", "cost": 15, "index": 7})
    assert v["spent"] == 75 and v["log"] == ["买 パワフル漢方ドリンク（75P）"]
    v = _visit(d, 405)  # 刷新：不记账
    v = _visit(d, 305, {"label": "删 眠気", "cost": 100, "service": "delete", "price_before": 100})
    assert v["spent"] == 175 and v["log"][-1] == "删 眠気（100P）"
    v = _visit(d, 305, {"label": "买 ホットコーヒー", "cost": 50, "index": 7}, shelf=[])  # 买失败：P 没变
    assert (v["spent"], v["p"], len(v["log"])) == (175, 305, 2)


def test_failed_service_not_reopened():
    d = ProduceDeciders.__new__(ProduceDeciders)
    _visit(d, 305)
    v = _visit(d, 305, {"label": "强化 きらきら紙吹雪", "cost": 100, "service": "upgrade", "price_before": 100})
    assert v["spent"] == 0 and "upgrade" in v["no_target"]


def test_without_p_reading_falls_back_to_shelf():
    d = ProduceDeciders.__new__(ProduceDeciders)
    _visit(d, 480)
    v = _visit(
        d,
        None,
        {"label": "买 烏龍茶", "cost": 50, "index": 4},
        shelf=[{"index": 4, "drink_id": "x", "purchased": True}],
    )
    assert (v["spent"], v["p"]) == (50, 430)
    v = _visit(d, None, {"label": "买 ホットコーヒー", "cost": 50, "index": 7}, shelf=[])  # 货架读空不算买到
    assert v["spent"] == 50
