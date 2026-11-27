"""11-27 用户：开局页按场次选「快速 / 搜索」出牌。各家族的开关能写场次，--exam-search 变成对应的环境变量。"""

from hiro.bench_bridge import advisor as A
from hiro.cli.jobs import KINDS, Job, exam_search_env
from hiro.search import advisor as S


def test_hajime_rounds(monkeypatch):
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)  # 开发机设了它会盖掉场次（CodeRabbit #198）
    monkeypatch.delenv("HIRO_EXAM_SEARCH_HAJIME", raising=False)
    assert A.hajime_search_rounds() == {"mid1", "final", "lesson"}  # 缺省全搜（原来的行为）
    assert A.exam_search_algo_spec("hajime-lesson-vo-sp") == A.EXAM_DEFAULT_ALGO
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "0")
    assert A.hajime_search_rounds() == set() and not A.hajime_search_on()  # 旧写法照样
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "2")
    assert A.hajime_search_rounds() == {"mid1", "final", "lesson"}  # 认不出场次：照缺省，不悄悄变成全关
    monkeypatch.setenv("HIRO_EXAM_SEARCH_HAJIME", "final")
    assert A.exam_search_algo_spec("hajime-final") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("hajime-mid1") == ""
    assert A.exam_search_algo_spec("hajime-lesson-da") == ""


def test_nia_rounds(monkeypatch):
    monkeypatch.delenv("HIRO_EXAM_SEARCH_ALGO", raising=False)  # 开发机设了它会盖掉场次（CodeRabbit #198）
    monkeypatch.delenv("HIRO_EXAM_SEARCH_NIA", raising=False)
    assert A.nia_search_rounds() == set()  # 缺省不搜
    monkeypatch.setenv("HIRO_EXAM_SEARCH_NIA", "1")
    assert A.nia_search_rounds() == {"mid1", "mid2", "final"}
    monkeypatch.setenv("HIRO_EXAM_SEARCH_NIA", "mid2")
    assert A.exam_search_algo_spec("nia-mid2#audition-3") == A.EXAM_DEFAULT_ALGO
    assert A.exam_search_algo_spec("nia-final") == ""


def test_main_rounds(monkeypatch):
    monkeypatch.delenv("HIRO_EXAM_SEARCH_MAIN", raising=False)
    assert S.main_search_rounds() == {"r1", "r2"}
    monkeypatch.setenv("HIRO_EXAM_SEARCH_MAIN", "r2")
    assert S.main_search_rounds() == {"r2"}
    monkeypatch.setenv("HIRO_EXAM_SEARCH_MAIN", "off")
    assert S.main_search_rounds() == set()


def test_exam_search_param_to_env():
    assert exam_search_env("hajime", None) == {}
    assert exam_search_env("hajime", "mid1,final") == {"HIRO_EXAM_SEARCH_HAJIME": "mid1,final"}
    assert exam_search_env("hajime", "none") == {"HIRO_EXAM_SEARCH_HAJIME": "off"}
    assert exam_search_env("nia", "final") == {"HIRO_EXAM_SEARCH_NIA": "final"}
    env = exam_search_env("hif", "q3,r2", {})
    assert env["HIRO_EXAM_SEARCH_HIF"] == "3" and env["HIRO_EXAM_SEARCH_MAIN"] == "r2"
    assert env["HIRO_EXAM_STATE_SOURCE"].endswith("SimScreenExamStateSource") and env["HIRO_SIM_WORKERS"] == "1"
    env = exam_search_env("hif", "q1", {})
    assert env == {"HIRO_EXAM_SEARCH_HIF": "1", "HIRO_EXAM_SEARCH_MAIN": "off"}  # 本战不搜：不补模拟器
    env = exam_search_env("hif", "r1", {"HIRO_EXAM_STATE_SOURCE": "my.plugin:Src"})
    assert "HIRO_EXAM_STATE_SOURCE" not in env  # 自己设了局面来源就不动
    assert "HIRO_EXAM_STATE_SOURCE" not in exam_search_env("hif", "r1", {"HIRO_SIM": "0"})  # 关了模拟器不补
    assert exam_search_env("daily", "mid1") == {}


def test_job_env_and_summary(hiro_home):
    job = Job(hiro_home, KINDS["hajime"], {"command": "new", "idol": "x", "exam_search": "final"})
    assert job.child_env()["HIRO_EXAM_SEARCH_HAJIME"] == "final"
    assert "搜索出牌：最終" in KINDS["hajime"].summary({"exam_search": "final"})
    assert "全部快速出牌" in KINDS["nia"].summary({"exam_search": "none"})
    assert "搜索出牌：选拔 3、R2" in KINDS["hif"].summary({"command": "resume", "exam_search": "q3,r2"})


def test_run_start_rejects_unknown_rounds(run_cli):
    """#197 review：写错的场次（hajime 写了 r1）原来静默变成全部快速，现在直接报参数错误。"""
    _code, env = run_cli("run", "start", "hajime", "--command", "resume", "--exam-search", "r1")
    assert env["ok"] is False and env["error"]["code"] == "bad_args" and "r1" in env["error"]["message"]
    _code, env = run_cli("run", "start", "hajime", "--command", "resume", "--exam-search", "none")
    assert env["ok"] is True or env["error"]["code"] != "bad_args"
