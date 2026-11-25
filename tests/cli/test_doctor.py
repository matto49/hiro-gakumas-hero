def items(env):
    return {i["id"]: i for i in env["data"]["items"]}


def test_doctor_on_fresh_home(run_cli):
    code, env = run_cli("doctor")
    assert code == 0
    it = items(env)
    assert list(it) == ["python", "platform", "sound", "game_settings", "model", "secrets", "catalog", "scan", "ocr"]
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


def test_doctor_ocr_item_follows_hiro_ocr_and_platform(monkeypatch, tmp_path):
    from hiro.cli import cmd_doctor
    from hiro.vision import simd_deps

    monkeypatch.delenv("HIRO_OCR", raising=False)
    monkeypatch.setenv("HIRO_SIMD_DEPS", str(tmp_path / "deps"))
    monkeypatch.setattr(cmd_doctor.sys, "platform", "linux")
    it = cmd_doctor._ocr()
    assert it["id"] == "ocr" and it["ok"] is True and it["optional"] is True and it["detail"] == "RapidOCR"
    monkeypatch.setenv("HIRO_OCR", "rapidocr")
    assert "HIRO_OCR=rapidocr" in cmd_doctor._ocr()["detail"]

    # Windows：SIMD 没装好 → 说明原因并给出再装一次的办法；装好了 → 写 SIMD。不真的启动辅助进程
    monkeypatch.delenv("HIRO_OCR")
    monkeypatch.setattr(cmd_doctor.sys, "platform", "win32")
    it = cmd_doctor._ocr()
    assert it["ok"] is False and it["optional"] is True
    assert it["detail"].startswith("RapidOCR（SIMD 没装好：缺少") and "重装运行环境" in it["next"]
    monkeypatch.setattr(simd_deps, "unusable_reason", lambda deps: None)
    it = cmd_doctor._ocr()
    assert it["ok"] is True and it["detail"].startswith("SIMD PaddleOCR")


def test_doctor_simd_missing_is_optional_and_never_blocks_ready(run_cli, monkeypatch, tmp_path):
    from hiro.cli import cmd_doctor

    monkeypatch.delenv(
        "HIRO_OCR", raising=False
    )  # 不被本机环境带偏：不固定后端、依赖目录指向空目录（本机装过 SIMD 也一样）
    monkeypatch.setenv("HIRO_SIMD_DEPS", str(tmp_path / "no-deps"))
    monkeypatch.setattr(cmd_doctor.sys, "platform", "win32")
    _, env = run_cli("doctor")
    ocr = items(env)["ocr"]
    assert ocr["ok"] is False and ocr["optional"] is True
    blockers = [i["id"] for i in env["data"]["items"] if i["ok"] is False and not i["optional"] and i["id"] != "scan"]
    assert "ocr" not in blockers
