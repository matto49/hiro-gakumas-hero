"""用量汇总与花费（hiro.usage.report）：价格表、按日 / 月 / 局 / 对话分组、订阅单列、每局 usage.json。"""

import json

from hiro.usage import ledger, report

import pytest

PRICES = report.load_prices()


def row(**kw):
    base = {"v": 1, "id": kw.pop("id", None) or f"r{len(kw)}{id(kw)}", "ts": "2026-11-25T07:03:33.000+08:00",
            "source": "decision", "job": "J1", "thread": None, "provider": "deepseek", "model": "deepseek-flash",
            "input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write": 0,
            "cost": None, "currency": None, "cost_source": None, "ok": True}  # fmt: skip
    base.update(kw)
    return base


def test_priced_cost_deepseek_offpeak():
    # 周三 07:03 北京时间：空闲时段（缓存命中 0.02 + 未命中 1 + 输出 4 元 / 百万）
    assert report.row_cost(row(), PRICES) == (pytest.approx(5.02), "CNY", "priced")


def test_priced_cost_deepseek_peak_doubles():
    # 周三 10:00 北京时间：高峰时段单价翻倍；时区不同也按北京时间判
    assert report.row_cost(row(ts="2026-11-25T02:00:00.000+00:00"), PRICES)[0] == pytest.approx(10.04)


def test_weekend_is_offpeak():
    assert report.row_cost(row(ts="2026-11-28T10:00:00.000+08:00"), PRICES)[0] == pytest.approx(5.02)  # 周六


def test_claude_reported_is_subscription_not_in_total():
    r = row(provider="claude-cli", model="sonnet", cost=0.0123, currency="USD", cost_source="reported")
    assert report.row_cost(r, PRICES) == (0.0123, "USD", "subscription")
    (g,) = report.summarize([r, row(id="b")], key=None)
    assert g["cost"] == {"CNY": pytest.approx(5.02)} and g["subscription_usd"] == pytest.approx(0.0123)


def test_unknown_model_cost_none_counted_unpriced():
    r = row(provider="kimi", model="kimi-k2")
    assert report.row_cost(r, PRICES) == (None, None, "unpriced")
    (g,) = report.summarize([r], key=None)
    assert g["cost"] == {} and g["unpriced"] == 1 and g["calls"] == 1


def test_no_usage_row_counts_call():
    r = row(provider="codex-cli", model=None, input=None, output=None, cache_read=None, cache_write=None)
    (g,) = report.summarize([r], key=None)
    assert g["calls"] == 1 and g["no_usage"] == 1 and g["total"] == 0


def test_daily_groups_and_breakdown():
    rows = [
        row(id="a"),
        row(id="b", ts="2026-11-26T08:00:00.000+08:00", model="deepseek-v4-pro"),
        row(id="c", model="deepseek-v4-pro"),
    ]
    days = report.summarize(rows, key="day", breakdown=True)
    assert [d["key"] for d in days] == ["2026-11-25", "2026-11-26"]
    assert days[0]["calls"] == 2 and days[0]["total"] == 6_000_000
    assert {m["model"] for m in days[0]["models"]} == {"deepseek-flash", "deepseek-v4-pro"}
    months = report.summarize(rows, key="month")
    assert [m["key"] for m in months] == ["2026-11"] and months[0]["calls"] == 3


def test_job_usage_by_source_and_calls():
    rows = [row(id="a"), row(id="b", source="rescue"), row(id="c", job="J2")]
    u = report.job_usage("J1", rows)
    assert u["job"] == "J1" and u["calls"] == 2
    assert set(u["by_source"]) == {"decision", "rescue"} and len(u["items"]) == 2
    assert report.job_usage("J9", rows)["calls"] == 0  # 没调用：空结果，不报错


def test_write_job_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_HOME", str(tmp_path))
    monkeypatch.setenv("HIRO_JOB_ID", "J1")
    ledger.record(row(id="x"))
    (tmp_path / "jobs" / "J1").mkdir(parents=True)
    u = report.write_job_usage(tmp_path, "J1")
    saved = json.loads((tmp_path / "jobs" / "J1" / "usage.json").read_text(encoding="utf-8"))
    assert saved == u and u["calls"] == 1


def test_exited_writes_usage_json_and_report_without_exams(tmp_path, monkeypatch):
    """对局结束：没有考试成绩也写 usage.json、发 report 事件（带 usage 段）。"""
    from hiro.cli import report as cli_report
    from hiro.cli import service as svc
    from hiro.cli.jobs import KINDS, Job

    monkeypatch.setenv("HIRO_HOME", str(tmp_path))
    job = Job(tmp_path, KINDS["hajime"], {"command": "new", "idol": "i"})
    monkeypatch.setenv("HIRO_JOB_ID", job.id)
    ledger.record(row(id="y", job=job.id))
    monkeypatch.setattr(cli_report, "segments", lambda traces, jid: [])  # 没有对局记录、没有成绩
    s = svc.Manager.__new__(svc.Manager)
    s.home = tmp_path
    s._exited(job)
    evs = [json.loads(line) for line in (job.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = [e for e in evs if e["type"] == "report"]
    assert len(rep) == 1 and rep[0]["usage"]["calls"] == 1 and rep[0]["exams"] == []
    assert (job.dir / "usage.json").is_file()


def test_peak_pricing_falls_back_when_tz_database_missing(monkeypatch, caplog):
    # N46：Windows 的 Python 没有时区数据库，ZoneInfo 找不到时区；用量统计不能崩，按固定偏移（北京时间 +8）算高峰
    def boom(name):
        raise report.ZoneInfoNotFoundError(f"No time zone found with key {name}")

    monkeypatch.setattr(report, "ZoneInfo", boom)
    monkeypatch.setattr(report, "_warned_tz", set())
    with caplog.at_level("WARNING", logger=report.log.name):
        peak = report.row_cost(row(ts="2026-11-25T02:00:00.000+00:00"), PRICES)  # 周三北京时间 10:00：高峰
        off = report.row_cost(row(ts="2026-11-25T07:03:33.000+08:00"), PRICES)  # 周三 07:03：空闲
        weekend = report.row_cost(row(ts="2026-11-28T10:00:00.000+08:00"), PRICES)  # 周六
    assert peak[0] == pytest.approx(10.04)
    assert off[0] == pytest.approx(5.02)
    assert weekend[0] == pytest.approx(5.02)
    assert len([r for r in caplog.records if "Asia/Shanghai" in r.getMessage()]) == 1  # 只警告一次


def test_unknown_tz_name_falls_back_to_beijing_offset(monkeypatch):
    monkeypatch.setattr(report, "ZoneInfo", lambda name: (_ for _ in ()).throw(KeyError(name)))
    monkeypatch.setattr(report, "_warned_tz", set())
    entry = {"peak": {"tz": "Mars/Olympus", "weekdays": [2], "hours": [[9, 12]], "multiplier": 3.0}}
    assert report._multiplier(entry, "2026-11-25T10:00:00.000+08:00") == 3.0
    assert report._multiplier(entry, "2026-11-25T13:00:00.000+08:00") == 1.0


def test_summaries_survive_missing_tz_database(monkeypatch):
    # 用量页读的 daily / monthly / job 汇总都要过价格表，不能因为时区数据库缺失整页崩
    monkeypatch.setattr(report, "ZoneInfo", lambda name: (_ for _ in ()).throw(report.ZoneInfoNotFoundError(name)))
    monkeypatch.setattr(report, "_warned_tz", set())
    rows = [row(id="a", ts="2026-11-25T02:00:00.000+00:00"), row(id="b")]
    (day,) = report.summarize(rows, key="day")
    assert day["cost"]["CNY"] == pytest.approx(10.04 + 5.02)
    assert report.job_usage("J1", rows)["cost"]["CNY"] == pytest.approx(10.04 + 5.02)
