"""任务在 HIRO_HOME/work 里跑：主数据、自建素材、编成方案的路径要明确传过去（这些工具缺省按当前目录找）。"""

from pathlib import Path  # noqa: F401


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


def test_job_calib_frames_go_to_job_dir(hiro_home, monkeypatch):
    """Mac 11-24 16:15：写编成失败时提示「截图在 reports/live/calib/…-编成菜单」，那是相对任务工作目录的路径，
    测试用的数据目录没有 work/，截图跟着丢了。初星助手的任务把排查截图存进任务目录 calib/，和每一步的画面放一起。"""
    from hiro.cli.jobs import KINDS, Job

    monkeypatch.delenv("HIRO_CALIB_DIR", raising=False)
    job = Job(hiro_home, KINDS["write-formation"], {})
    assert job.child_env()["HIRO_CALIB_DIR"] == str(job.dir / "calib")


def test_calib_root_follows_env(monkeypatch, tmp_path):
    from hiro.imgio import calib_root

    monkeypatch.delenv("HIRO_CALIB_DIR", raising=False)
    assert calib_root() == Path("reports/live/calib")
    monkeypatch.setenv("HIRO_CALIB_DIR", str(tmp_path / "c"))
    assert calib_root() == tmp_path / "c"
