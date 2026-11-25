"""测试分层：没有截图样例 / 没装实机依赖 / 依赖的层还没在仓库里时自动跳过，CI 只跑不读截图的部分。

- 识别层很多用例读 tests/fixtures/ 下的实机截图。截图属于游戏画面，仓库不带；把自己的截图按同样的相对路径放进
  tests/fixtures/（或设 HIRO_FIXTURES 指向一个同结构目录，会软链过来）就能跑。没有样例目录时这些用例跳过。
- 识别层要可选组 play 的依赖（pip install -e '.[play]'：RapidOCR、faiss、ddddocr 等）。没装时跳过。
- 个别用例引到操作层 / 策略层（hiro.produce、hiro.brain.*）或不开源的模拟器（hiro.sim）；
  这些模块不在时，整个文件跳过。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
os.environ.setdefault("HIRO_GUIDES_FETCH", "0")  # 测试不联网下载偶像卡攻略包（hiro.brain.guides.refresh_if_stale）
if not FIXTURES.exists() and os.environ.get("HIRO_FIXTURES") and Path(os.environ["HIRO_FIXTURES"]).is_dir():
    try:
        FIXTURES.symlink_to(Path(os.environ["HIRO_FIXTURES"]).resolve(), target_is_directory=True)
    except OSError:  # Windows 建符号链接要管理员 / 开发者模式（WinError 1314）：改建目录联接（junction），不需要权限
        import _winapi  # noqa: PLC0415  只有 Windows 有

        _winapi.CreateJunction(str(Path(os.environ["HIRO_FIXTURES"]).resolve()), str(FIXTURES))
HAVE_FIXTURES = FIXTURES.is_dir() and any(FIXTURES.iterdir())  # 空目录 / 断链都算没有样例

OPTIONAL_LAYERS = ("hiro.produce", "hiro.brain.state", "hiro.brain.cards",
                   "hiro.brain.deciders", "hiro.brain.rules", "hiro.brain.replay", "hiro.sim")  # fmt: skip
PLAY_USERS = re.compile(r"hiro\.(?:vision|perception)(?:\.(\w+))?")
PLAY_FREE = {"assets", "layout", "items", "region_cache"}  # 不要 play 组依赖的识别层模块


def _needs_play(src: str) -> bool:
    """测试源码引用了要 play 组依赖的识别层模块（只引用 PLAY_FREE 里的模块不算）。"""
    return any(m.group(1) not in PLAY_FREE for m in PLAY_USERS.finditer(src))


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


PLAY_DEPS = ("rapidocr_onnxruntime", "faiss", "ddddocr")  # 可选组 play 的代表（pyproject.toml）


def _play_ok() -> bool:
    return all(_has_module(m) for m in PLAY_DEPS)


def _top_level(src: str) -> str:
    """模块顶层语句（不缩进的行），导入时就会执行的部分。"""
    return "\n".join(
        line for line in src.splitlines() if line and not line[0].isspace() and not line.startswith(("#", '"', "'"))
    )


_TOP_PATH = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(Path\(__file__\)\S.*?)\s*(?:#.*)?$", re.M)
_TOP_READ = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\.read_text\(")


def _missing_top_files(f: Path, top: str) -> list[Path]:
    """模块顶层就要读的文件（`X = Path(__file__)…` 常量拼出来的 `(…).read_text(encoding="utf-8")`）哪些不在。
    按具体文件判断：tests/fixtures 是空目录、或只放了部分样例时，缺 action/truth.json 的 test_action.py 不收集，别的照常。
    只对路径表达式求值（不给 builtins），算不出来的读法不管（交给 pytest 正常报错）。"""
    ns: dict = {"Path": Path, "__file__": str(f)}
    for m in _TOP_PATH.finditer(top):
        try:
            ns[m.group(1)] = eval(m.group(2), {"__builtins__": {}}, ns)  # noqa: S307 只有测试文件里的路径常量
        except Exception:  # noqa: BLE001
            continue
    missing = []
    for m in _TOP_READ.finditer(top):
        try:
            path = eval(m.group(1), {"__builtins__": {}}, ns)  # noqa: S307
        except Exception:  # noqa: BLE001
            continue
        if isinstance(path, Path) and not path.is_file():
            missing.append(path)
    return missing


def _collect_ignore() -> list[str]:
    """导入时就会失败的测试文件（顶层导入了不在仓库里的层 / 顶层读样例截图），不收集。"""
    out = []
    for f in sorted(TESTS.glob("test_*.py")):
        top = _top_level(f.read_text(encoding="utf-8"))
        if (
            any(
                re.search(rf"^(from|import) {re.escape(m)}\b", top, re.M) and not _has_module(m)
                for m in OPTIONAL_LAYERS
            )
            or _missing_top_files(f, top)
            or _top_import_missing_optional(top)
        ):
            out.append(f.name)
    return out


OPTIONAL_DEPS = (
    *PLAY_DEPS,
    "PIL",
    "onnxruntime",
    "Vision",
    "Quartz",
    "AppKit",
)  # 可选组 play / macOS 系统框架（pyobjc）


def _body_needs_optional(item) -> bool:
    """用例函数体里导入的 hiro 模块本机导入不了，且缺的是 play 组依赖 / macOS 框架。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    lines = "\n".join(line.strip() for line in body.splitlines())
    return _top_import_missing_optional(lines)


def _top_import_missing_optional(top: str) -> bool:
    """顶层导入的 hiro 模块在导入时就要 play 组依赖 / macOS 框架，而本机没装：整个文件不收集（CI 不装 play 组）。"""
    import importlib

    names = []
    for m in re.finditer(r"^(?:from|import) (hiro(?:\.\w+)*)(?: import \(?([\w ,]+))?", top, re.M):
        names.append(m.group(1))
        # from hiro.produce import ops, shop：被导入的名字可能是子模块，也要试
        names += [f"{m.group(1)}.{n.strip().split(' as ')[0]}" for n in (m.group(2) or "").split(",") if n.strip()]
    for name in names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            if (e.name or "").split(".")[0] in OPTIONAL_DEPS:
                return True
        except Exception:  # noqa: BLE001 其他导入错误交给 pytest 正常报出来
            pass
    return False


collect_ignore = _collect_ignore()


_SRC: dict[Path, str] = {}


def _source(path: Path) -> str:
    if path not in _SRC:
        _SRC[path] = path.read_text(encoding="utf-8")
    return _SRC[path]


def _ocr_engine() -> str:
    try:
        from hiro.vision.ocr import engine

        return engine()
    except Exception:  # noqa: BLE001  识别层不可用时按非 Vision 处理
        return "unknown"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "user_assets(*kinds): 需要自建素材（HIRO_USER_ASSETS）；kinds 见 hiro.vision.assets.USER_ASSETS"
    )
    config.addinivalue_line("markers", "synthetic: 只用合成图（源码里的 .png 是自己画的），不按「读样例截图」跳过")
    config.addinivalue_line(
        "markers", "fixtures: 要读 tests/fixtures 的截图样例（经类 / 多层辅助函数间接读，自动判断看不出来时显式标）"
    )
    config.addinivalue_line(
        "markers", "play_free: 明确不要 play 组依赖：按源码猜「要 play」的规则（引用了 hiro.vision 等）对它不算"
    )
    config.addinivalue_line(
        "markers",
        "play: 要可选组 play 的依赖（OCR 等）：运行时才导入、自动判断看不出来时显式标",
    )
    config.addinivalue_line(
        "markers", "master: 要主数据库（hiro.state.master_db，经决策器 / 识别层间接查，自动判断看不出来时显式标）"
    )
    config.addinivalue_line(
        "markers", "sim_master: 要真跑模拟器（vendor/gakumas-player 的主数据 catalog.sqlite 已构建）"
    )
    config.addinivalue_line(
        "markers",
        "lowres_sample(why): 样例是低分辨率截图（360x640 / 540x960 放大），只在 Vision OCR 下断言；"
        "RapidOCR（Windows / DMM 默认）读这类放大图错字多，DMM 实机帧 1081x1921 另有回归集",
    )
    config.addinivalue_line(
        "markers",
        "vision_only(why): 测的是以 Apple Vision 为裁判的路径，非 Vision OCR 下跳过（写明为什么实机不走这条路）",
    )
    config.addinivalue_line(
        "markers", "bench_on: 不关 bench 出招（其余测试一律 HIRO_BENCH_ADVISOR=0，免得装了 gkbench 时去下载模型）"
    )


def _missing_user_assets(kinds) -> list[str]:
    try:
        from hiro.vision import assets
    except Exception:  # noqa: BLE001
        return list(kinds)
    return [k for k in kinds if not assets.available(k)]


def pytest_collection_modifyitems(config, items):
    play_ok = None
    for item in items:
        marker = item.get_closest_marker("user_assets")
        if marker is not None and (missing := _missing_user_assets(marker.args)):
            item.add_marker(pytest.mark.skip(reason=f"需要自建素材（HIRO_USER_ASSETS）：{', '.join(missing)}"))
            continue
        path = Path(str(item.fspath))
        src = _source(path)
        missing = [m for m in OPTIONAL_LAYERS if m in src and not _has_module(m)]
        if not missing and _needs_exam_obs(item, src) and not _has_module("hiro.brain.cards"):
            missing = ["hiro.brain.cards（考试画面观测打包成 CardView）"]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"依赖的层不在仓库里：{missing}"))
            continue
        if item.get_closest_marker("play_free") is None and (_needs_play(src) or _body_needs_optional(item)):
            if play_ok is None:
                play_ok = _play_ok()
            if not play_ok:
                item.add_marker(pytest.mark.skip(reason="需要可选组 play（pip install -e '.[play]'）"))
                continue
        low = item.get_closest_marker("lowres_sample")
        if low is not None and _ocr_engine() != "vision":
            why = low.args[0] if low.args else ""
            item.add_marker(
                pytest.mark.skip(reason=f"低分辨率样例只在 Vision OCR 下断言（当前 {_ocr_engine()}）：{why}")
            )
        only = item.get_closest_marker("vision_only")
        if only is not None and _ocr_engine() != "vision":
            item.add_marker(
                pytest.mark.skip(
                    reason=f"只在 Vision OCR 下有意义（当前 {_ocr_engine()}）：{only.args[0] if only.args else ''}"
                )
            )
        if item.get_closest_marker("sim_master") is not None and not _sim_master_ok():
            item.add_marker(
                pytest.mark.skip(reason="需要模拟器主数据（python vendor/gakumas-player/scripts/fetch_master.py）")
            )
            continue
        if item.get_closest_marker("play") is not None and not _play_ok():
            item.add_marker(pytest.mark.skip(reason="需要可选组 play（pip install -e '.[play]'）"))
        if item.get_closest_marker("master") is not None and not _master_ok():
            item.add_marker(
                pytest.mark.skip(reason="需要主数据库（python -m hiro.state.master_db，先构建模拟器主数据）")
            )
            continue
        if item.get_closest_marker("fixtures") is not None and not HAVE_FIXTURES:
            item.add_marker(pytest.mark.skip(reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）"))
            continue
        if MASTER_HINT.search(src) and not _master_ok():
            try:
                import inspect

                body = inspect.getsource(item.function)
            except (OSError, TypeError):
                body = ""
            if MASTER_HINT.search(body):
                item.add_marker(
                    pytest.mark.skip(reason="需要主数据库（python -m hiro.state.master_db，先构建模拟器主数据）")
                )
                continue
        if not HAVE_FIXTURES and not item.get_closest_marker("synthetic") and _needs_fixtures(item, src):
            item.add_marker(pytest.mark.skip(reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）"))


_HELPERS: dict[Path, set[str]] = {}
FIXTURE_HINT = re.compile(r"\bFIX\b|fixtures|\.jpg|\.png")


def _fixture_helpers(path: Path, src: str) -> set[str]:
    """模块里读样例的辅助函数名（函数体引用了 FIX / fixtures / 图片文件）。"""
    if path not in _HELPERS:
        names = set()
        for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^@|^class |\Z)", src, re.M | re.S):
            if FIXTURE_HINT.search(m.group(0)):
                names.add(m.group(1))
        for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)\s*=.*?(?=^\S|\Z)", src, re.M | re.S):  # 顶层常量（可跨行）引用了样例
            if FIXTURE_HINT.search(m.group(0)) and m.group(1) != "FIX":
                names.add(m.group(1))
        _HELPERS[path] = names
    return _HELPERS[path]


def _needs_fixtures(item, src: str) -> bool:
    """按用例判断：函数体、参数、或它调用的本模块辅助函数用到了样例截图。纯逻辑用例照跑。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return True
    params = str(getattr(getattr(item, "callspec", None), "params", ""))
    if FIXTURE_HINT.search(body) or FIXTURE_HINT.search(params):
        return True
    helpers = _fixture_helpers(Path(str(item.fspath)), src)
    return any(re.search(rf"\b{h}\b", body) for h in helpers)


EXAM_OBS = re.compile(r'observation\(.*"HIF_EXAM"|extract\(|read_hif_exam\(|battle_observation\(')


def _needs_exam_obs(item, src: str) -> bool:
    """用例（或它调用的本模块辅助函数）把考试画面打包成观测：要策略层的 CardView。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    if EXAM_OBS.search(body):
        return '"HIF_EXAM"' in src or "battle_observation" in body or "read_hif_exam" in body
    helpers = [
        m.group(1)
        for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^@|^class |\Z)", src, re.M | re.S)
        if EXAM_OBS.search(m.group(0))
    ]
    return any(re.search(rf"\b{h}\(", body) for h in helpers) and '"HIF_EXAM"' in src


MASTER_HINT = re.compile(
    r"\b(master_offers|card_from_name|split_card_names|card_version|card_catalog|drink_catalog|pitem_catalog)\("
)


def _master_ok() -> bool:
    """主数据库在不在：PublicAssetSource 的 master_db()（hiro.state.master_db 从模拟器主数据生成）。"""
    try:
        from hiro.defaults.assets import PublicAssetSource

        return PublicAssetSource().master_db() is not None
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 模拟器（hiro.sim，引擎在 vendor/gakumas-player）
def _sim_master_ok() -> bool:
    try:
        import hiro.sim

        return hiro.sim.master_ready()
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _sim_test_defaults(request, monkeypatch):
    """同主仓库：测试默认关掉培育里的饮料模拟（drinks.SIM）、轮间刷新估值的续打（drink_gains）；要测的用例自己打开 / monkeypatch。
    引用了 hiro.sim 的测试文件：决策器的「模拟器可用」按引擎能导入算（用例自己 monkeypatch 了方案 / 增益表，不需要主数据）；
    标了 sim_master 的用例要真跑模拟，没构建主数据（vendor/gakumas-player/scripts/fetch_master.py）时跳过。"""
    # 决策点补全的 LLM 路径缺省全开（用户 11-14）；别的用例给了 MockLLM 也只验原来的规则 / LLM 调用，这里统一关，要测的用例自己 setenv
    monkeypatch.setenv("HIRO_PRODUCE_LLM", "off")
    try:
        from hiro.brain import deciders, drinks
        from hiro.sim import interval as iv
    except Exception:  # noqa: BLE001  没有这些层：不用设
        yield
        return
    monkeypatch.setattr(drinks, "SIM", False)
    monkeypatch.setattr(drinks, "DEVIATIONS", "/nonexistent/deviations.jsonl", raising=False)

    def _raise(*a, **k):
        raise RuntimeError("测试里不跑刷新估值的续打")

    monkeypatch.setattr(iv, "drink_gains", _raise)
    if "hiro.sim" in _source(Path(str(request.node.fspath))):
        monkeypatch.setattr(deciders, "_sim_ready", lambda: True)
    else:
        monkeypatch.setattr(deciders, "_sim_ready", lambda: False)  # 别的决策器用例只验规则（本机建了主数据也不跑模拟）
    yield


@pytest.fixture(autouse=True)
def _no_real_hiro_home(monkeypatch):
    """不继承外面的 HIRO_HOME / HIRO_JOB_ID：模型调用会记流水账（hiro.usage.ledger），mock 调用不能写进开发者真的数据目录。
    要用数据目录的用例自己 setenv（tests/cli 的 hiro_home 夹具会设）。"""
    monkeypatch.delenv("HIRO_HOME", raising=False)
    monkeypatch.delenv("HIRO_JOB_ID", raising=False)


@pytest.fixture(autouse=True)
def _bench_advisor_off(monkeypatch, request):
    """缺省出招器是 bench 算法（hiro.bench_bridge）：装了 gkbench 时构造它会去下载 UINet 模型。
    测试里默认关掉（不联网、行为和以前的规则出招一致）；bench 中转层自己的测试（标 bench_on）再打开。"""
    if request.node.get_closest_marker("bench_on") is None:
        monkeypatch.setenv("HIRO_BENCH_ADVISOR", "0")


_PLAY_MISSING = re.compile(rf"No module named '(?:{'|'.join(OPTIONAL_DEPS)})\b")


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """没装 play 组（公开 CI 只装 dev 组）时，测试文件间接用到它的依赖（例如从别的测试文件导入的模块顶层 import 了 OCR 库）
    在收集阶段就报 ModuleNotFoundError。_collect_ignore 只看顶层的直接导入，拦不住这种；这里把缺 play 组依赖导致的收集错误改成跳过。
    装了 play 组时不改任何结果。"""
    outcome = yield
    rep = outcome.get_result()
    if (
        rep.failed
        and isinstance(collector, pytest.Module)
        and _PLAY_MISSING.search(str(rep.longrepr))
        and not _play_ok()
    ):
        rep.outcome = "skipped"
        rep.longrepr = (str(collector.path), 0, "Skipped: 要可选组 play（pip install -e '.[play]'）")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """同上，运行阶段：用例经操作层（hiro.produce 等）在运行时才调 OCR / 卡面库，没装 play 组时报 ModuleNotFoundError。
    原来这些模块顶层导入 KAA，没装 KAA 时整个文件跳过；去 KAA 后导入不再失败，这里照旧记成跳过。装了 play 组时不改任何结果。"""
    outcome = yield
    rep = outcome.get_result()
    if (
        rep.failed
        and call.excinfo is not None
        and call.excinfo.errisinstance(ModuleNotFoundError)
        and _PLAY_MISSING.search(str(call.excinfo.value))
        and not _play_ok()
    ):
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: 要可选组 play（{call.excinfo.value}）")


@pytest.fixture(autouse=True)
def _no_catalog_network(monkeypatch):
    """资料库缺了会自动下载（hiro.cli.catalog._ensure_synced，G1-F3）：测试里缺省指到本机关着的端口，立刻失败、不连外网；
    要下载的测试自己起本地服务器再设 HIRO_CATALOG_URL。每个测试前重置「刚试过」的冷却。"""
    monkeypatch.setenv("HIRO_CATALOG_URL", "http://127.0.0.1:9")
    try:
        from hiro.cli import catalog
    except ImportError:
        return
    monkeypatch.setattr(catalog, "_auto_sync_at", None)
