"""GKMS 子进程（spec 4.3）：协议、超时、挂了重启一次。真 GKMS 的用例在本机没装时跳过。"""

import json
import sys
import textwrap
from pathlib import Path

from hiro.gkms import client as gc
from hiro.gkms import locate

import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture
def real(monkeypatch):
    monkeypatch.delenv("HIRO_GKMS", raising=False)  # conftest 默认关
    inst = locate.find()
    if inst is None:
        pytest.skip(f"本机没装 GKMS：{locate.problem()}")
    c = gc.Client(inst)
    yield c
    c.close()


def test_hello(real):
    r = real.call("hello")
    assert r["format"] == "gkms.pickv.v1" and r["tau"] > 0


def test_route_hajime_pro(real):
    rec = json.loads((DATA / "gkms-route-record-hajime-pro.json").read_text())
    r = real.call("route", record=rec, from_step=11)
    assert r["plan"]["route"][0][0] == 11
    assert r["decisions"][0]["step"] == 11
    assert {o["choice"] for o in r["decisions"][0]["options"]} >= {"event_school", "refresh"}


def test_values_for_hiro(real):
    r = real.call("values", idol="i_card-shro-3-007", deck=["p_card-00-men-3_005"], offer=["p_card-00-men-3_005"],
                  upgrade=["p_card-00-men-3_005"], wait_s=15, timeout=30)  # fmt: skip
    if r["state"] != "ok":
        pytest.skip(f"选卡包没拿到：{r['state']}（没网？）")
    assert 0 <= r["take"]["p_card-00-men-3_005"] <= 1
    assert r["held"]["p_card-00-men-3_005"] >= r["take"]["p_card-00-men-3_005"]  # 第 2 张的频次不高于第 1 张


def test_unknown_op_is_error_not_crash(real):
    with pytest.raises(gc.GkmsError, match="KeyError"):
        real.call("nope")
    assert real.call("hello")["format"]  # worker 还活着


def fake_install(tmp_path, body: str) -> locate.Install:
    """假 worker：替换 worker 脚本，测超时和重启。"""
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(body))
    return locate.Install(home=tmp_path, python=sys.executable), script


def test_timeout_raises_and_kills(tmp_path, monkeypatch):
    inst, script = fake_install(
        tmp_path,
        """
        import sys, time, json
        for line in sys.stdin:
            time.sleep(5)
    """,
    )
    monkeypatch.setattr(gc, "WORKER", script)
    c = gc.Client(inst)
    with pytest.raises(gc.GkmsError, match="超时"):
        c.call("hello", timeout=0.5)
    c.close()


def test_restart_once_then_give_up(tmp_path, monkeypatch):
    inst, script = fake_install(
        tmp_path,
        """
        import sys
        sys.exit(3)
    """,
    )
    monkeypatch.setattr(gc, "WORKER", script)
    c = gc.Client(inst)
    for _ in range(2):
        with pytest.raises(gc.GkmsError):
            c.call("hello", timeout=2)
    assert c.dead
    with pytest.raises(gc.GkmsError, match="不再用"):
        c.call("hello", timeout=2)
    assert c.starts == 2


def test_i7_worker_runs_in_utf8_mode(tmp_path, monkeypatch):
    """I-7：-I 会让 PYTHONUTF8 失效，要用 -X utf8（Windows 上中文路径 / 报错不崩）。"""
    inst, script = fake_install(
        tmp_path,
        """
        import sys, json
        for line in sys.stdin:
            req = json.loads(line)
            print(json.dumps({"id": req["id"], "ok": True, "result": {"utf8": sys.flags.utf8_mode}}), flush=True)
    """,
    )
    monkeypatch.setattr(gc, "WORKER", script)
    c = gc.Client(inst)
    assert c.call("hello", timeout=5)["utf8"] == 1
    c.close()


def test_i8_pack_failure_cached(monkeypatch):
    """I-8：选卡包下不到时记一段时间，后面的决策不再每次等 wait_s。"""
    import importlib
    import time as _t

    w = importlib.import_module("hiro.gkms.worker")
    w._PACK_FAIL.clear()
    calls = []

    class PV:
        @staticmethod
        def pack_for(idol):
            calls.append(idol)
            return None, "loading"

    t0 = _t.monotonic()
    assert w._pack(PV, "i_card-x", 0.6) == (None, "loading")
    assert _t.monotonic() - t0 >= 0.5
    t1 = _t.monotonic()
    assert w._pack(PV, "i_card-x", 0.6)[1] == "loading"
    assert _t.monotonic() - t1 < 0.2
