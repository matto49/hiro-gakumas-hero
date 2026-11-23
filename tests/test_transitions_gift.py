"""礼物箱（差し入れ）有几个袋子：选完一个三选一会回到礼物箱开下一个（DMM 实机：GIFT → SELECT_DRINK → GIFT → SELECT_CARD）。"""

from hiro.loop.transitions import allowed


def test_gift_box_with_several_bags():
    seq = ["ACTION_SELECT", "GIFT", "SELECT_DRINK", "GIFT", "SELECT_CARD", "ACTION_SELECT"]
    assert all(allowed(a, b) for a, b in zip(seq, seq[1:]))  # noqa: RUF007
    for sel in ("SELECT_DRINK", "SELECT_CARD", "SELECT_PITEM"):
        assert allowed(sel, "GIFT")
