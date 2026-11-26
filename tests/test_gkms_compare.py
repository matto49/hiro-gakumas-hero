"""离线对比工具（spec 4.10）：用假 client 跑两步小样例，核对统计和报告。"""

import copy
import json
import sys
from pathlib import Path

from gkms_fakes import FakeClient, values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gkms_compare as gc  # noqa: E402

from hiro.gkms.advisor import GkmsProduceAdvisor  # noqa: E402

RAW = json.loads((Path(__file__).parent / "data" / "gkms-hajime-pro-w11.json").read_text())
ROUTE = {"plan": {"route": [[11, "event_school"]], "rating": 8271.1, "end": {}},
         "decisions": [{"step": 11, "plan": "event_school",
                        "options": [{"choice": "refresh", "vs_best": -289.8}, {"choice": "event_school", "vs_best": 0.0},
                                    {"choice": "shop", "vs_best": -289.8}, {"choice": "event_activity", "vs_best": -289.8}]}],
         "from_step": 11}  # fmt: skip


def rows():
    a = copy.deepcopy(RAW)
    a["produce"]["idol_card_id"] = "i_card-x"
    act = {"seq": 9, "state": a, "decision": {"action": {"kind": "ProduceNext", "args": {"option": "rest"}}, "source": "rule", "reason": "休息"}}
    b = copy.deepcopy(RAW)
    b["page"]["name"] = "SELECT_CARD"
    b["page_fields"] = {"candidates": [{"index": 0, "card_id": "p_card-a"}, {"index": 1, "card_id": "p_card-b"}], "rerolls_left": 0}
    card = {"seq": 12, "state": b, "decision": {"action": {"kind": "ActivateEffect", "args": {"pick_indexes": [0]}}, "source": "rule", "reason": "拿 a"}}
    return [act, card]


def test_compare_steps_and_report(tmp_path):
    t = tmp_path / "hajime-test"
    t.mkdir()
    (t / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows()) + "\n")
    adv = GkmsProduceAdvisor(client=FakeClient(route=ROUTE, values=values({"p_card-a": 0.2, "p_card-b": 0.6})))
    res = gc.compare([t], adv)
    by = {r["page"]: r for r in res}
    assert by["ACTION_SELECT"]["gkms"] == "event_school" and by["ACTION_SELECT"]["same"] is False
    assert by["ACTION_SELECT"]["recorded_gap"] == -289.8  # 实机选的休む在 GKMS 模型里比最优差多少
    assert by["SELECT_CARD"]["same"] is False and by["SELECT_CARD"]["v_recorded"] == 0.2 and by["SELECT_CARD"]["v_gkms"] == 0.6
    md = gc.report(res, title="测试")
    assert "| produce-002 | ACTION_SELECT | 1 | 0 | 0 | 1 |" in md
    assert "GKMS 自己的模型" in md  # 报告开头写明偏向


def test_deferred_counted_with_reason(tmp_path):
    t = tmp_path / "hajime-test"
    t.mkdir()
    r = rows()[0]
    r["state"]["produce"]["step"] = None
    (t / "steps.jsonl").write_text(json.dumps(r, ensure_ascii=False) + "\n")
    adv = GkmsProduceAdvisor(client=FakeClient(route=ROUTE))
    (res,) = gc.compare([t], adv)
    assert res["gkms"] is None and "第几周" in res["deferred"]
