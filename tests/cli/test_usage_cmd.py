"""hiro usage …：插件和对话助手读用量的接口（设计稿 §4）。"""

import json

from hiro.usage import ledger


def _row(**kw):
    base = {"v": 1, "id": kw.pop("id"), "ts": "2026-11-25T07:00:00.000+08:00", "source": "decision", "job": "J1",
            "thread": None, "provider": "deepseek", "model": "deepseek-flash", "input": 1000, "output": 100,
            "cache_read": 0, "cache_write": 0, "cost": None, "currency": None, "cost_source": None, "ok": True}  # fmt: skip
    base.update(kw)
    return base


def _seed():
    for r in (_row(id="a"), _row(id="b", ts="2026-11-26T07:00:00.000+08:00", job=None, source="chat", thread="t1"),
              _row(id="c", job="J2", model="deepseek-v4-pro")):  # fmt: skip
        ledger.record(r)


def test_usage_daily_cmd(run_cli):
    _seed()
    code, env = run_cli("usage", "daily", "--breakdown")
    assert code == 0 and env["ok"]
    data = env["data"]
    assert [r["key"] for r in data["rows"]] == ["2026-11-25", "2026-11-26"]
    assert data["rows"][0]["calls"] == 2 and data["rows"][0]["models"]
    assert data["totals"]["calls"] == 3 and data["prices_checked"]


def test_usage_daily_since_until(run_cli):
    _seed()
    _, env = run_cli("usage", "daily", "--since", "2026-11-26")
    assert [r["key"] for r in env["data"]["rows"]] == ["2026-11-26"]
    _, env = run_cli("usage", "monthly", "--until", "2026-11-25")
    assert env["data"]["rows"][0]["calls"] == 2


def test_usage_job_cmd_reads_usage_json(run_cli, hiro_home):
    _seed()
    d = hiro_home / "jobs" / "J9"
    d.mkdir(parents=True)
    (d / "usage.json").write_text(json.dumps({"job": "J9", "calls": 7, "total": 1}), encoding="utf-8")
    _, env = run_cli("usage", "job", "J9")
    assert env["data"]["calls"] == 7  # 有 usage.json 就用它（局结束时定下来的）
    _, env = run_cli("usage", "job", "J1")
    assert env["data"]["calls"] == 1 and env["data"]["items"]  # 没有就现算


def test_usage_chat(run_cli):
    _seed()
    _, env = run_cli("usage", "chat")
    assert [r["key"] for r in env["data"]["rows"]] == ["t1"]


def test_usage_recent_lists_jobs(run_cli, hiro_home):
    for jid, n in (("20261125-010000-aaaa", 3), ("20261125-020000-bbbb", 5)):
        d = hiro_home / "jobs" / jid
        d.mkdir(parents=True)
        (d / "job.json").write_text(
            json.dumps({"id": jid, "kind": "hajime", "summary": f"初 {n}", "params": {}}), encoding="utf-8"
        )
        (d / "usage.json").write_text(
            json.dumps({"job": jid, "calls": n, "total": n * 10, "cost": {"CNY": 0.1}}), encoding="utf-8"
        )
    _, env = run_cli("usage", "recent", "--limit", "1")
    (j,) = env["data"]["jobs"]
    assert (
        j["job"] == "20261125-020000-bbbb" and j["kind"] == "hajime" and j["calls"] == 5 and j["cost"] == {"CNY": 0.1}
    )


def test_usage_in_tools_list(run_cli):
    _, env = run_cli("tools")
    names = {t["name"]: t for t in env["data"]["tools"]}
    for n in ("usage daily", "usage monthly", "usage job", "usage chat", "usage recent"):
        assert n in names and names[n]["risk"] == "read"


def test_usage_bad_date_is_error_not_empty(run_cli):
    """--since / --until 写成不完整的日期（11-25）时报错，不悄悄筛成空表（对话助手实测踩过）。"""
    _seed()
    code, env = run_cli("usage", "daily", "--until", "11-25")
    assert code != 0 and not env["ok"] and "11-25" in env["error"]["message"] and "YYYY-MM-DD" in env["error"]["hint"]
    code, env = run_cli("usage", "monthly", "--since", "2026-11")
    assert code == 0 and env["data"]["totals"]["calls"] == 3  # 只写年月也认（从这个月 1 号起）
