"""找 GKMS（spec 2026-11-26 4.2）：目录顺序、必需模块、Python、HIRO_GKMS=0。"""

import sys

import pytest

from hiro.gkms import locate

MODULES = ("hajime_route.py", "nia_route.py", "pickv.py")


def fake_gkms(root, *, missing=()):
    prod = root / "GakumasCLI" / "produce"
    prod.mkdir(parents=True)
    (root / "GakumasCLI" / "__init__.py").write_text("")
    for m in MODULES:
        if m not in missing:
            (prod / m).write_text("")
    (root / "GakumasCLI" / "ro" / "master" / "tables").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ("HIRO_GKMS", "HIRO_GKMS_HOME", "HIRO_GKMS_PYTHON", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(locate, "DEFAULT_HOMES", [tmp_path / "nowhere"])


def test_home_from_env(monkeypatch, tmp_path):
    home = fake_gkms(tmp_path / "g")
    monkeypatch.setenv("HIRO_GKMS_HOME", str(home))
    inst = locate.find()
    assert inst is not None and inst.home == home
    assert inst.python == sys.executable  # 没有自带 Python / venv 时用 hiro 自己的


def test_missing_module_is_not_installed(monkeypatch, tmp_path):
    home = fake_gkms(tmp_path / "g", missing=("pickv.py",))
    monkeypatch.setenv("HIRO_GKMS_HOME", str(home))
    assert locate.find() is None
    assert "pickv.py" in (locate.problem() or "")


def test_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HIRO_GKMS_HOME", str(fake_gkms(tmp_path / "g")))
    monkeypatch.setenv("HIRO_GKMS", "0")
    assert locate.find() is None
    assert "HIRO_GKMS=0" in (locate.problem() or "")


def test_windows_install_one_level_down(monkeypatch, tmp_path):
    fake_gkms(tmp_path / "Programs" / "GKMS" / "app")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    inst = locate.find()
    assert inst is not None and inst.home == tmp_path / "Programs" / "GKMS" / "app"


def test_bundled_python_preferred(monkeypatch, tmp_path):
    home = fake_gkms(tmp_path / "g")
    py = home / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setenv("HIRO_GKMS_HOME", str(home))
    assert locate.find().python == str(py)


def test_nothing_found(tmp_path):
    assert locate.find() is None
    assert "没找到" in (locate.problem() or "")
