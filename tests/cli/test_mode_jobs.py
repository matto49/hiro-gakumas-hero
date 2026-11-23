"""各模式开局任务的命令行和参数。"""

from pathlib import Path

from hiro.cli.jobs import KINDS, Job


def argv(kind, params, d=Path("/j/20261120-000000-abcd")):
    return KINDS[kind].argv(params, d)


def test_hif_passes_main_attr_and_sets_formations_via_env(hiro_home):
    a = argv("hif", {"command": "new", "mode": "qualify", "idol": "i_card-skin-shro-3-007", "main_attr": "Da"})
    assert a[1:4] == ["-m", "hiro.run_hif", "new"] and "--main-attr" in a and a[a.index("--main-attr") + 1] == "Da"
    job = Job(hiro_home, KINDS["hif"], {"support_set": 4, "memory_set": 5})
    env = job.child_env()
    assert env["HIRO_SUPPORT_SET"] == "4" and env["HIRO_MEMORY_SET"] == "5"


def test_hajime_job(hiro_home):
    d = Path("/j/J1")
    a = argv(
        "hajime",
        {"command": "new", "idol": "i_card-skin-shro-3-007", "difficulty": "pro", "support_set": 3, "memory_set": 7},
        d,
    )
    assert a[1:4] == ["-m", "hiro.runtime.hajime", "new"]
    for k, v in (
        ("--idol", "i_card-skin-shro-3-007"),
        ("--difficulty", "pro"),
        ("--support-set", "3"),
        ("--memory-set", "7"),
        ("--trace", "J1"),
        ("--out", str(d / "frames")),
    ):
        assert a[a.index(k) + 1] == v, k
    assert "初" in KINDS["hajime"].summary({"difficulty": "pro"}) and KINDS["hajime"].pausable


def test_nia_job_defaults_to_pro(hiro_home):
    a = argv("nia", {"command": "resume", "idol": "i_card-skin-shro-3-007"})
    assert a[1:4] == ["-m", "hiro.runtime.nia_sample", "resume"] and a[a.index("--difficulty") + 1] == "pro"
    assert "N.I.A" in KINDS["nia"].summary({})


def test_daily_items(hiro_home):
    assert argv("daily", {"items": "presents,mission_reward"})[2:] == ["--only", "presents,mission_reward", "--own"]
    assert argv("daily", {"items": "contest"})[2:] == ["--only", "contest"]
    assert argv("daily", {})[2:] == []


def test_run_start_accepts_new_params(run_cli):
    code, env = run_cli(
        "run", "start", "hajime", "--idol", "i_card-skin-shro-3-007", "--difficulty", "master", "--support-set", "2"
    )
    assert code == 3 and "MASTER" in env["data"]["pending"]["summary"].upper()
    code, env = run_cli("run", "start", "daily", "--items", "presents,nonsense")
    assert env["ok"] is False and env["error"]["code"] == "bad_args"


def test_mode_jobs_require_an_idol_for_new(run_cli):
    for kind in ("hajime", "nia"):
        _code, env = run_cli("run", "start", kind)
        assert env["ok"] is False and env["error"]["code"] == "bad_args", kind


def test_summary_uses_idol_card_name_from_catalog(hiro_home):
    """开局摘要给玩家看：资料库里有这张偶像卡就写卡名，不写内部编号；没有资料库时退回编号。"""
    import json

    from hiro.cli.catalog import catalog_dir

    assert "i_card-skin-shro-3-007" in KINDS["hif"].summary({"idol": "i_card-skin-shro-3-007"})
    d = catalog_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "catalog.ja.json").write_text(
        json.dumps({"idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広"}}}), encoding="utf-8"
    )
    for kind in ("hif", "hajime", "nia"):
        s = KINDS[kind].summary({"idol": "i_card-skin-shro-3-007"})
        assert "【Campus mode!!】篠澤広" in s and "i_card" not in s, s
