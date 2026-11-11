"""测试分层：没有截图样例 / 没装实机依赖 / 依赖的层还没在仓库里时自动跳过，CI 只跑不读截图的部分。

- 识别层很多用例读 tests/fixtures/ 下的实机截图。截图属于游戏画面，仓库不带；把自己的截图按同样的相对路径放进
  tests/fixtures/（或设 HIRO_FIXTURES 指向一个同结构目录，会软链过来）就能跑。没有样例目录时这些用例跳过。
- 识别层依赖 KAA（可选组 play：pip install -e '.[play]'，子模块 vendor/kaa）。导入不了 kotonebot / kaa 时跳过。
- 个别用例引到操作层 / 策略层（hiro.loop.controller、hiro.produce、hiro.brain.*）或不开源的模拟器（hiro.sim）；
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
if not FIXTURES.exists() and os.environ.get("HIRO_FIXTURES") and Path(os.environ["HIRO_FIXTURES"]).is_dir():
    try:
        FIXTURES.symlink_to(Path(os.environ["HIRO_FIXTURES"]).resolve(), target_is_directory=True)
    except OSError:  # Windows 建符号链接要管理员 / 开发者模式（WinError 1314）：改建目录联接（junction），不需要权限
        import _winapi  # noqa: PLC0415  只有 Windows 有

        _winapi.CreateJunction(str(Path(os.environ["HIRO_FIXTURES"]).resolve()), str(FIXTURES))
HAVE_FIXTURES = FIXTURES.is_dir() and any(FIXTURES.iterdir())  # 空目录 / 断链都算没有样例

if os.environ.get("HIRO_KAA_GAME_DATA"):
    try:  # 先把 KAA 的数据目录指过去（hiro.vision 导入时做）：别的测试模块先导入 KAA 会在当前目录建空的 resources/game_data
        import hiro.vision  # noqa: F401
    except Exception:  # noqa: BLE001 没装识别依赖：照常收集
        pass

OPTIONAL_LAYERS = ("hiro.loop.controller", "hiro.loop.kaa_patches", "hiro.produce", "hiro.brain.state", "hiro.brain.cards",
                   "hiro.brain.deciders", "hiro.brain.rules", "hiro.brain.replay", "hiro.sim")  # fmt: skip
KAA_USERS = re.compile(r"hiro\.(?:vision|perception)(?:\.(\w+))?")
KAA_FREE = {"assets", "layout", "items", "region_cache"}  # 导入时不碰 KAA 的识别层模块（items 只在读饮料时才导入 KAA）


def _needs_kaa(src: str) -> bool:
    """测试源码引用了要 KAA 的识别层模块（只引用 KAA_FREE 里的模块不算）。"""
    return any(m.group(1) not in KAA_FREE for m in KAA_USERS.finditer(src))


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _kaa_ok() -> bool:
    try:
        import kotonebot  # noqa: F401
        import kaa  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 没装 play 组，或 macOS 上缺 hiro.compat 的补丁
        return False


def _top_level(src: str) -> str:
    """模块顶层语句（不缩进的行），导入时就会执行的部分。"""
    return "\n".join(line for line in src.splitlines() if line and not line[0].isspace() and not line.startswith(("#", '"', "'")))


_TOP_PATH = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(Path\(__file__\)\S.*?)\s*(?:#.*)?$", re.M)
_TOP_READ = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\.read_text\(")


def _missing_top_files(f: Path, top: str) -> list[Path]:
    """模块顶层就要读的文件（`X = Path(__file__)…` 常量拼出来的 `(…).read_text()`）哪些不在。
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
        if any(re.search(rf"^(from|import) {re.escape(m)}\b", top, re.M) and not _has_module(m) for m in OPTIONAL_LAYERS):
            out.append(f.name)
        elif _missing_top_files(f, top):
            out.append(f.name)
        elif _top_import_missing_optional(top):
            out.append(f.name)
    return out


OPTIONAL_DEPS = ("kaa", "kotonebot", "Vision", "Quartz", "AppKit")  # 可选组 play / macOS 系统框架（pyobjc）


def _body_needs_optional(item) -> bool:
    """用例函数体里导入的 hiro 模块（操作层大多在导入时就要 KAA）本机导入不了，且缺的是 KAA / macOS 框架。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    lines = "\n".join(line.strip() for line in body.splitlines())
    return _top_import_missing_optional(lines)


def _top_import_missing_optional(top: str) -> bool:
    """顶层导入的 hiro 模块在导入时就要 KAA / macOS 框架，而本机没装：整个文件不收集（CI 不装 play 组）。"""
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
    config.addinivalue_line("markers", "user_assets(*kinds): 需要自建素材（HIRO_USER_ASSETS）；kinds 见 hiro.vision.assets.USER_ASSETS")
    config.addinivalue_line("markers", "synthetic: 只用合成图（源码里的 .png 是自己画的），不按「读样例截图」跳过")
    config.addinivalue_line("markers", "fixtures: 要读 tests/fixtures 的截图样例（经类 / 多层辅助函数间接读，自动判断看不出来时显式标）")
    config.addinivalue_line("markers", "master: 要主数据 game.db（经决策器 / 识别层间接查，自动判断看不出来时显式标）")
    config.addinivalue_line("markers", "sim_master: 要真跑模拟器（vendor/gakumas-player 的主数据 catalog.sqlite 已构建）")
    config.addinivalue_line("markers", "lowres_sample(why): 样例是低分辨率截图（360x640 / 540x960 放大），只在 Vision OCR 下断言；"
                                       "RapidOCR（Windows / DMM 默认）读这类放大图错字多，DMM 实机帧 1081x1921 另有回归集")
    config.addinivalue_line("markers", "vision_only(why): 测的是以 Apple Vision 为裁判的路径，非 Vision OCR 下跳过（写明为什么实机不走这条路）")


def _missing_user_assets(kinds) -> list[str]:
    try:
        from hiro.vision import assets
    except Exception:  # noqa: BLE001
        return list(kinds)
    return [k for k in kinds if not assets.available(k)]


def pytest_collection_modifyitems(config, items):
    kaa_ok = None
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
        if _needs_kaa(src) or _body_needs_optional(item):
            if kaa_ok is None:
                kaa_ok = _kaa_ok()
            if not kaa_ok:
                item.add_marker(pytest.mark.skip(reason="需要 KAA（pip install -e '.[play]'）"))
                continue
        low = item.get_closest_marker("lowres_sample")
        if low is not None and _ocr_engine() != "vision":
            why = low.args[0] if low.args else ""
            item.add_marker(pytest.mark.skip(reason=f"低分辨率样例只在 Vision OCR 下断言（当前 {_ocr_engine()}）：{why}"))
        only = item.get_closest_marker("vision_only")
        if only is not None and _ocr_engine() != "vision":
            item.add_marker(pytest.mark.skip(reason=f"只在 Vision OCR 下有意义（当前 {_ocr_engine()}）：{only.args[0] if only.args else ''}"))
        if item.get_closest_marker("sim_master") is not None and not _sim_master_ok():
            item.add_marker(pytest.mark.skip(reason="需要模拟器主数据（python vendor/gakumas-player/scripts/fetch_master.py）"))
            continue
        if item.get_closest_marker("master") is not None and not _master_ok():
            item.add_marker(pytest.mark.skip(reason="需要主数据 game.db（KAA 公开数据，HIRO_KAA_GAME_DATA）"))
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
                item.add_marker(pytest.mark.skip(reason="需要主数据 game.db（KAA 公开数据，HIRO_KAA_GAME_DATA）"))
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
    helpers = [m.group(1) for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^@|^class |\Z)", src, re.M | re.S) if EXAM_OBS.search(m.group(0))]
    return any(re.search(rf"\b{h}\(", body) for h in helpers) and '"HIF_EXAM"' in src


MASTER_HINT = re.compile(r"\b(master_offers|card_from_name|split_card_names|card_version|card_catalog|drink_catalog|pitem_catalog)\(")


def _master_ok() -> bool:
    """主数据（KAA game.db）在不在：PublicAssetSource 的 master_db()。"""
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
