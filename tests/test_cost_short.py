"""考试可打判断：状态代价不够时不算可打（hiro.bench_bridge.screen.cost_short）。"""


def test_cost_short_blocks_unaffordable_status_cost(monkeypatch):
    """11-24 mao3：立ち位置チェック+ 要集中 3、只有 2 → 不算可打；没读到集中时照旧可打。"""
    from hiro.bench_bridge import screen

    class Cat:
        def card(self, cid, u):
            return {"costType": "ExamCostType_ExamLessonBuff", "costValue": 3}

    import hiro.state.context as ctx

    monkeypatch.setattr(ctx, "catalog", lambda: Cat())
    assert screen.cost_short("x", 1, {"lesson_buff": 2.0})
    assert screen.cost_short("x", 1, {"lesson_buff": 3.0}) is None
    assert screen.cost_short("x", 1, {}) is None
