def items(env):
    return {i["id"]: i for i in env["data"]["items"]}


def test_doctor_on_fresh_home(run_cli):
    code, env = run_cli("doctor")
    assert code == 0
    it = items(env)
    assert list(it) == ["python", "platform", "sound", "game_settings", "model", "secrets", "catalog", "scan"]
    assert it["python"]["ok"] is True
    assert it["catalog"]["ok"] is False and "hiro catalog sync" in it["catalog"]["next"]
    assert it["scan"]["ok"] is False and "扫描" in it["scan"]["detail"]
    assert it["sound"]["ok"] is None  # 要用户自己确认
    assert it["secrets"]["optional"] is True
    for i in env["data"]["items"]:
        assert i["title"] and i["detail"]
    assert env["data"]["ready"] is False


def test_doctor_sees_catalog_and_scans(run_cli, hiro_home):
    import json

    (hiro_home / "catalog").mkdir(parents=True)
    (hiro_home / "catalog" / "catalog.ja.json").write_text(json.dumps({"cards": {"a": {}}}), encoding="utf-8")
    from pathlib import Path

    run_cli("info", "import", "--kind", "supports", str(Path(__file__).parent / "data" / "support-inventory.json"))
    _, env = run_cli("doctor")
    it = items(env)
    assert it["catalog"]["ok"] is True
    assert it["scan"]["ok"] is True and "支援卡" in it["scan"]["detail"]


def test_doctor_reports_model_from_settings(run_cli, hiro_home):
    import json

    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(json.dumps({"provider": "deepseek", "has_key": True}), encoding="utf-8")
    _, env = run_cli("doctor")
    assert items(env)["model"]["ok"] is True and "DeepSeek" in items(env)["model"]["detail"]


def test_doctor_defaults_to_deepseek_and_asks_for_the_key(run_cli, hiro_home):
    _, env = run_cli("doctor")
    m = items(env)["model"]
    assert m["ok"] is False and "DeepSeek" in m["detail"] and "Key" in m["next"]


def test_doctor_reports_no_model_when_chosen(run_cli, hiro_home):
    import json

    hiro_home.mkdir(parents=True, exist_ok=True)
    (hiro_home / "settings.json").write_text(json.dumps({"provider": "none"}), encoding="utf-8")
    _, env = run_cli("doctor")
    assert "不用模型" in items(env)["model"]["detail"]
