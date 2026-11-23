"""第二十五局：上次中断时开始确认页已选好 1554/2505/830，首读没认全 → 进「変更」列表翻 12 页找不到（已选中的不在列表里）
→ 不开局。首读最多读 3 次；列表找不到也要重读本页。"""

import hiro  # noqa: F401
import hiro.loop.kaa_patches as kp


def test_page_read_retries_until_values_seen(monkeypatch):
    monkeypatch.setattr(kp, "EXPECT_MEMORY", ("1554", "2505", "830"))
    frames = iter([["1554", "25"], ["1554", "2505", "830"]])
    cur = {"t": []}
    assert (
        kp._memory_missing_on_page(
            shoot=lambda: cur.__setitem__("t", next(frames)), texts=lambda img: cur["t"], sleep=lambda s: None
        )
        == []
    )


def test_check_rereads_page_after_failed_list_search(monkeypatch):
    monkeypatch.setattr(kp, "EXPECT_MEMORY", ("1554", "2505", "830"))
    calls = iter([["2505"], []])  # 首读缺 2505，列表没找到后重读：页面上就是这份
    monkeypatch.setattr(kp, "_memory_missing_on_page", lambda **k: next(calls))
    searched = []
    monkeypatch.setattr(kp, "select_memory", lambda expect, **k: searched.append(expect) or False)
    ran = []
    kp._check_memory_then(lambda *a, **k: ran.append(1))()
    assert searched and ran == [1]
