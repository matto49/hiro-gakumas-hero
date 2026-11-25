from hiro import plugins

import pytest


class Advisor:
    def on_round_start(self, round_no, context): ...
    def decide(self, state, memory, previews, legal):
        return legal[0]

    def on_round_end(self, result): ...


class Broken:
    def decide(self, state, memory, previews, legal): ...


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugins.reset()
    for _, env, _ in plugins.KINDS.values():
        if env:
            monkeypatch.delenv(env, raising=False)
    yield
    plugins.reset()


def test_register_wins_and_is_cached():
    plugins.register("exam_advisor", Advisor)
    a = plugins.get("exam_advisor")
    assert a is plugins.get("exam_advisor")
    assert a.decide(None, None, None, ["x"]) == "x"
    assert plugins.source("exam_advisor") == "register"


def test_env_spec(monkeypatch):
    monkeypatch.setenv("HIRO_EXAM_ADVISOR", f"{__name__}:Advisor")
    assert isinstance(plugins.get("exam_advisor"), Advisor)
    assert plugins.source("exam_advisor").startswith("env:")


def test_missing_methods_rejected():
    plugins.register("exam_advisor", Broken)
    with pytest.raises(TypeError, match="缺少方法"):
        plugins.get("exam_advisor")


def test_unknown_kind():
    with pytest.raises(KeyError):
        plugins.register("nope", Advisor)


def test_bad_spec(monkeypatch):
    monkeypatch.setenv("HIRO_EXAM_ADVISOR", "no_colon")
    with pytest.raises(ValueError):
        plugins.get("exam_advisor")


# ---- 多实例、id、契约版本（2026-11-25 插件架构，spec 4.1）


class ProduceA:
    id = "pa"
    version = "1.0"
    contracts = "1"
    pages = frozenset({"ACTION_SELECT"})

    def begin(self, context): ...
    def decide(self, page, state, *, n_options, kw):
        return None

    def end(self, result): ...


class ProduceB(ProduceA):
    id = "pb"
    version = "2.0"


class ProduceFuture(ProduceA):
    id = "future"
    contracts = "9"


def test_register_by_id_keeps_several():
    plugins.register("produce_advisor", ProduceA, id="pa")
    plugins.register("produce_advisor", ProduceB, id="pb")
    assert isinstance(plugins.get_by_id("produce_advisor", "pa"), ProduceA)
    b = plugins.get_by_id("produce_advisor", "pb")
    assert isinstance(b, ProduceB) and b is plugins.get_by_id("produce_advisor", "pb")
    assert plugins.label(b) == "pb@2.0"
    assert {i.id for i in plugins.available("produce_advisor")} == {"pa", "pb"}


def test_entry_point_kind_dot_id(monkeypatch):
    class EP:
        def __init__(self, name):
            self.name = name

        def load(self):
            return ProduceB

    monkeypatch.setattr(plugins, "entry_points", lambda group: [EP("produce_advisor.pb"), EP("exam_advisor")])
    assert isinstance(plugins.get_by_id("produce_advisor", "pb"), ProduceB)
    infos = plugins.available("produce_advisor")
    assert [(i.id, i.origin, i.ok) for i in infos] == [("pb", "entry_point", True)]


def test_contract_major_mismatch_unavailable():
    plugins.register("produce_advisor", ProduceFuture, id="future")
    assert plugins.get_by_id("produce_advisor", "future") is None
    (info,) = plugins.available("produce_advisor")
    assert info.ok is False and "契约" in info.error


def test_builtin_id_reserved():
    with pytest.raises(ValueError):
        plugins.register("produce_advisor", ProduceA, id=plugins.BUILTIN)
    assert plugins.get_by_id("produce_advisor", plugins.BUILTIN) is None


def test_get_by_id_unknown_is_none():
    assert plugins.get_by_id("produce_advisor", "nope") is None


def test_new_kinds_have_no_default():
    assert plugins.get("produce_advisor") is None
    assert plugins.get("observation_source") is None
