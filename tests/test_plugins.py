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
