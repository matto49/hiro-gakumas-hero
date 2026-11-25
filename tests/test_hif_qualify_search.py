"""H.I.F 选拔考试走 GNRPA（HIRO_EXAM_SEARCH_HIF，缺省关）：开关、场次换算、倍率要三项都读到。"""

from hiro.bench_bridge import advisor as A


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HIF", raising=False)
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    assert A.hif_qualify_search_rounds() == set()
    assert A.exam_search_algo_spec("qualify-3") == ""
    assert not A.searched_label("qualify-3")


def test_switch_values(monkeypatch):
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HIF", "final")
    assert A.hif_qualify_search_rounds() == {"qualify-3"}
    assert A.exam_search_algo_spec("qualify-3") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("qualify-1") == ""
    assert A.exam_search_algo_spec("r2") == ""  # 本战不归它管
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HIF", "all")
    assert A.hif_qualify_search_rounds() == {"qualify-1", "qualify-2", "qualify-3"}
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HIF", "1,3")
    assert A.hif_qualify_search_rounds() == {"qualify-1", "qualify-3"}
    assert A.searched_label("hajime-final") and A.searched_label("qualify-3") and not A.searched_label("qualify-2")


def test_round_key():
    assert [A.round_key(x) for x in ("qualify-1", "qualify-2", "qualify-3")] == ["mid1", "mid2", "final"]
    assert A.round_key("hajime-final") == "final" and A.round_key("nia-mid2#audition-3") == "mid2"


def _adv(label, seen, calc):
    a = A.BenchAdvisor.__new__(A.BenchAdvisor)
    a.label, a.exam, a._seen_pm, a._calc_pm = label, {}, seen, calc
    return a


def test_hif_permils_need_all_three():
    calc = {"Vo": 4990, "Da": 6390, "Vi": 3150}
    pm, why = _adv("qualify-3", {"Vo": 26000}, calc)._exam_permils(quiet=True)
    assert pm is None and "三项" in why  # 初 / N.I.A 会按比例推，H.I.F 不推
    pm, _ = _adv("qualify-3", {"Vo": 26000, "Da": 23000, "Vi": 11610}, calc)._exam_permils(quiet=True)
    assert pm == {"Vo": 26000, "Da": 23000, "Vi": 11610}
    pm, _ = _adv("hajime-final", {"Vo": 26000}, calc)._exam_permils(quiet=True)
    assert pm is not None and pm["Vo"] == 26000  # 初照旧按比例推
