"""装了 GKMS 就默认启用（spec 4.4）与日程列表探查自动打开（spec 4.7）。用假的 GKMS 插件，不需要本机装 GKMS。"""

from hiro import plugins, strategy
from hiro.gkms import advisor

import pytest


class FakeGkms:
    id = "gkms"
    version = "test"
    contracts = "1"
    pages = advisor.GkmsProduceAdvisor.pages

    def begin(self, context): ...
    def decide(self, page, state, *, n_options, kw):
        return None

    def end(self, result): ...


class Broken:
    def __init__(self):
        raise RuntimeError("没找到 GKMS（测试）")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    plugins.reset()
    strategy.reset()
    monkeypatch.delenv("HIRO_STRATEGY_FILE", raising=False)
    monkeypatch.delenv("HIRO_SCHEDULE_PROBE", raising=False)
    monkeypatch.delenv("HIRO_GKMS", raising=False)
    yield
    plugins.reset()
    strategy.reset()


def use(factory, monkeypatch):
    monkeypatch.setitem(plugins.BUNDLED, ("produce_advisor", "gkms"), factory)


def test_default_on_when_available(monkeypatch):
    use(FakeGkms, monkeypatch)
    s = strategy.current()
    assert s.produce_for("SELECT_CARD") == "gkms"
    assert s.produce_for("ACTION_SELECT") == "gkms"
    assert s.produce_for("STUDY") is None  # 不在默认页面里
    assert "produce.SELECT_CARD" in s.mapping()["defaults"]


def test_explicit_builtin_turns_page_off(monkeypatch, tmp_path):
    use(FakeGkms, monkeypatch)
    f = tmp_path / "strategy.json"
    f.write_text('{"version": 1, "produce": {"SELECT_CARD": "builtin"}}')
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(f))
    s = strategy.current()
    assert s.produce_for("SELECT_CARD") is None
    assert s.produce_for("SKILL_CARD_REMOVAL") == "gkms"


def test_explicit_other_plugin_wins(monkeypatch, tmp_path):
    use(FakeGkms, monkeypatch)
    f = tmp_path / "strategy.json"
    f.write_text('{"version": 1, "produce": {"SELECT_CARD": "uinet"}}')
    monkeypatch.setenv("HIRO_STRATEGY_FILE", str(f))
    assert strategy.current().produce_for("SELECT_CARD") == "uinet"


def test_hiro_gkms_0_turns_all_off(monkeypatch):
    use(FakeGkms, monkeypatch)
    monkeypatch.setenv("HIRO_GKMS", "0")
    assert strategy.current().produce_for("SELECT_CARD") is None
    assert strategy.current().mapping().get("defaults") in (None, [])


def test_not_installed_means_builtin(monkeypatch):
    use(Broken, monkeypatch)
    assert strategy.current().produce_for("SELECT_CARD") is None
    info = {i.id: i for i in plugins.available("produce_advisor")}
    assert not info["gkms"].ok and "没找到 GKMS" in info["gkms"].error


def test_bundled_listed(monkeypatch):
    use(FakeGkms, monkeypatch)
    info = {i.id: i for i in plugins.available("produce_advisor")}
    assert info["gkms"].ok and info["gkms"].origin == "bundled"


def test_schedule_probe_follows_gkms(monkeypatch):
    from hiro.produce import schedule_probe

    use(FakeGkms, monkeypatch)
    assert schedule_probe.enabled() is True
    monkeypatch.setenv("HIRO_SCHEDULE_PROBE", "0")
    assert schedule_probe.enabled() is False


def test_schedule_probe_off_without_gkms(monkeypatch):
    from hiro.produce import schedule_probe

    use(Broken, monkeypatch)
    assert schedule_probe.enabled() is False
    monkeypatch.setenv("HIRO_SCHEDULE_PROBE", "1")
    assert schedule_probe.enabled() is True
