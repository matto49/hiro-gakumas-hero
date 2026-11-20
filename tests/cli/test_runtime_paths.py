"""任务在 HIRO_HOME/work 里跑：主数据、自建素材、编成方案的路径要明确传过去（这些工具缺省按当前目录找）。"""

from pathlib import Path


def test_jobs_get_data_and_plan_paths(hiro_home, monkeypatch, tmp_path):
    from hiro.cli import paths
    from hiro.cli.jobs import KINDS, Job

    repo = tmp_path / "repo"
    (repo / "resources" / "game_data").mkdir(parents=True)
    monkeypatch.setattr(paths, "REPO", repo)
    monkeypatch.delenv("HIRO_KAA_GAME_DATA", raising=False)
    monkeypatch.delenv("HIRO_FORMATION_PLAN", raising=False)
    env = Job(hiro_home, KINDS["hif"], {}).child_env()
    assert env["HIRO_KAA_GAME_DATA"] == str(repo / "resources" / "game_data")
    assert env["HIRO_USER_ASSETS"] == str(repo / "resources" / "user_assets")
    assert env["HIRO_FORMATION_PLAN"] == str(hiro_home / "formation-plan.json")


def test_explicit_env_wins(hiro_home, monkeypatch, tmp_path):
    from hiro.cli.jobs import KINDS, Job

    monkeypatch.setenv("HIRO_KAA_GAME_DATA", "/somewhere/game_data")
    env = Job(hiro_home, KINDS["hif"], {}).child_env()
    assert env["HIRO_KAA_GAME_DATA"] == "/somewhere/game_data"
