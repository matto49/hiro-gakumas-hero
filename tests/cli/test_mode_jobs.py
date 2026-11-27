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


DAILY_DEFAULTS = [
    "--contest-pick",
    "lowest",
    "--shop",
    "off",
    "--shop-items",
    "",
    "--shop-ap",
    "",
    "--shop-refresh",
    "off",
    "--coin-gacha",
    "off",
]


def test_daily_items(hiro_home):
    assert argv("daily", {"items": "presents,mission_reward"})[2:] == [
        "--only",
        "presents,mission_reward",
        *DAILY_DEFAULTS,
    ]
    assert argv("daily", {"items": "contest"})[2:] == ["--only", "contest", *DAILY_DEFAULTS]
    # 花钱的选项总是显式传（不继承环境里的 HIRO_SHOP 等，CodeRabbit #138）
    assert argv("daily", {})[2:] == DAILY_DEFAULTS


def test_daily_shop_items_only_with_shop_on(hiro_home):
    """「另外要买的」只在买推荐 / 全部时传（#122 review：不买时被静默忽略、确认卡却写着另外买）。"""
    a = argv("daily", {"shop": "off", "shop_items": "再挑戦"})
    assert a[a.index("--shop-items") + 1] == ""
    a = argv("daily", {"shop": "recommended", "shop_items": "再挑戦"})
    assert a[a.index("--shop-items") + 1] == "再挑戦"


def test_daily_summary_lists_spending(hiro_home):
    """确认摘要写出会花什么；不买不抽也写明（CodeRabbit #122）。"""
    s = KINDS["daily"].summary({})
    assert "商店不买" in s and "硬币扭蛋不抽" in s
    s = KINDS["daily"].summary({"shop": "all", "shop_ap": "retry", "coin_gacha": "friend:2"})
    assert "能买的都买" in s and "再挑战券" in s and "友情 2 次" in s and "花硬币" in s
    s = KINDS["daily"].summary({"items": "presents", "shop": "all"})
    assert "商店" not in s and "扭蛋" not in s


def test_run_start_accepts_new_params(run_cli):
    code, env = run_cli(
        "run",
        "start",
        "hajime",
        "--idol",
        "i_card-skin-shro-3-007",
        "--difficulty",
        "master",
        "--support-set",
        "2",
        "--memory-set",
        "1",
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


def test_final_rank_only_for_hajime():
    a = argv("hajime", {"command": "new", "idol": "i_card-jsna-3-006", "difficulty": "pro", "final_rank": 1})
    assert a[a.index("--final-rank") + 1] == "1"
    assert "--final-rank" not in argv(
        "nia", {"command": "new", "idol": "i_card-jsna-3-006", "difficulty": "pro", "final_rank": 1}
    )
    assert "--final-rank" not in argv("hajime", {"command": "new", "idol": "i_card-jsna-3-006", "difficulty": "pro"})


def test_play_prefs_from_settings_override_env(hiro_home, monkeypatch):
    """设置页「培育」：每一项都盖过启动环境；没设过的按程序缺省（11-27，CodeRabbit #197 / #198）。"""
    import json

    monkeypatch.setenv("HIRO_LIVE", "pick")
    monkeypatch.setenv("HIRO_UNREAD_STORY", "stop")
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HIF", "final")
    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(
        json.dumps({"live": "play_pick", "retry_max": "1", "retry_handoff": True}),
        encoding="utf-8",
    )
    env = Job(hiro_home, KINDS["hajime"], {"command": "resume"}).child_env()
    assert env["HIRO_LIVE"] == "play" and env["HIRO_PHOTO_PICK"] == "stop"
    assert env["HIRO_RETRY_MAX"] == "1" and env["HIRO_RETRY_HANDOFF"] == "1"
    assert env["HIRO_UNREAD_STORY"] == "skip"  # 设置里没有：按缺省，不沿用启动环境（页面上显示的就是实际用的）
    assert env["HIRO_HIF_GUIDES"] == "0" and env["HIRO_EXAM_SCHEDULE_PROBE"] == "0"
    (hiro_home / "settings.json").unlink()
    env = Job(hiro_home, KINDS["hajime"], {"command": "resume"}).child_env()
    assert env["HIRO_LIVE"] == "play" and env["HIRO_PHOTO_PICK"] == "auto" and env["HIRO_RETRY_MAX"] == "3"


def test_dmm_settle_default(hiro_home, monkeypatch):
    """DMM 上助手开的局也要 HIRO_SETTLE_MIN_S=1.5（原来只有 play.ps1 设）；自己设了就用自己的。"""
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    monkeypatch.delenv("HIRO_SETTLE_MIN_S", raising=False)
    assert Job(hiro_home, KINDS["hajime"], {}).child_env()["HIRO_SETTLE_MIN_S"] == "1.5"
    monkeypatch.setenv("HIRO_SETTLE_MIN_S", "2")
    assert Job(hiro_home, KINDS["hajime"], {}).child_env()["HIRO_SETTLE_MIN_S"] == "2"
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    monkeypatch.delenv("HIRO_SETTLE_MIN_S")
    assert "HIRO_SETTLE_MIN_S" not in Job(hiro_home, KINDS["hajime"], {}).child_env()


def test_daily_shop_refresh_only_with_shop_on(hiro_home):
    """免费刷新只在买东西时传 on（不买时刷新没意义）；确认摘要写出来。"""
    a = argv("daily", {"shop": "off", "shop_refresh": "on"})
    assert a[a.index("--shop-refresh") + 1] == "off"
    a = argv("daily", {"shop": "recommended", "shop_refresh": "on"})
    assert a[a.index("--shop-refresh") + 1] == "on"
    assert "免费刷新" in KINDS["daily"].summary({"shop": "recommended", "shop_refresh": "on"})
