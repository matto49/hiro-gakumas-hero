import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("check_settings_tool", REPO / "tools" / "check_settings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_settings_json_output(tmp_path, monkeypatch):
    mod = _tool()
    found = {k: "ON" for k in mod.WANT}
    found["支援事件剧情跳过"] = "OFF"
    monkeypatch.setattr(mod, "check", lambda save_dir=None: found)
    out = tmp_path / "out.json"
    code = mod.main(["--json", str(out)])
    d = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and d["ok"] is False and d["items"]["支援事件剧情跳过"] == "OFF" and "OFF" in d["verdict"]


def test_ingest_and_info_and_doctor(run_cli, tmp_path):
    inv = {"items": {"未読早送り": "ON", "支援事件剧情跳过": "OFF", "开始说明剧情跳过": "ON", "定期公演剧情跳过": "ON", "親愛度映像跳过": "ON"},
           "verdict": "设置被重置：1 项 OFF", "ok": False}
    p = tmp_path / "s.json"
    p.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
    _, env = run_cli("doctor")
    gs = {i["id"]: i for i in env["data"]["items"]}["game_settings"]
    assert gs["ok"] is None and "检查" in gs["next"]
    run_cli("info", "import", "--kind", "game_settings", str(p))
    _, env = run_cli("info", "game-settings")
    assert env["data"]["verdict"] == "设置被重置：1 项 OFF"
    assert {"item": "支援事件剧情跳过", "state": "OFF"} in env["data"]["items"]
    _, env = run_cli("doctor")
    gs = {i["id"]: i for i in env["data"]["items"]}["game_settings"]
    assert gs["ok"] is False and "支援事件剧情跳过" in gs["detail"]
