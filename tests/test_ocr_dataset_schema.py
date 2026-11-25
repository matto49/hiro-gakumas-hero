"""OCR 测试集的标注格式与数据仓库校验（tools.ocr_dataset.schema，规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §5）。"""

import json
import subprocess
import sys
from pathlib import Path

from tools.ocr_dataset import schema

import pytest

pytestmark = pytest.mark.synthetic  # 图片是测试里自己写的字节，不读 tests/fixtures

ROOT = Path(__file__).resolve().parents[1]


def _frame(img_name="dmm-run1-00001.jpg", sha="0" * 64, **kw):
    d = {
        "schema": 1,
        "id": "dmm-run1-00001",
        "image": img_name,
        "sha256": sha,
        "source": {"run": "run1", "seq": 1, "contributor": "matto", "captured": "2026-11-10"},
        "platform": {"kind": "dmm", "native": None, "stored": [720, 1280], "device": None, "note": None},
        "page": "ACTION_SELECT",
        "boxes": [
            {
                "id": 0,
                "box": [10, 20, 110, 60],
                "text": "編成",
                "status": "agreed",
                "answers": {"vision": "編成", "llm": "編成", "lexicon": "編成"},
                "tags": {"field": None, "type": "button"},
                "ignore": False,
            }
        ],
    }
    d.update(kw)
    return d


def test_valid_frame_has_no_errors():
    assert schema.validate_frame(_frame()) == []


def test_frame_errors_name_the_problem():
    bad = _frame()
    del bad["boxes"][0]["box"]
    assert any("box" in e for e in schema.validate_frame(bad))
    bad = _frame()
    bad["boxes"][0]["status"] = "maybe"
    assert any("status" in e for e in schema.validate_frame(bad))
    bad = _frame()
    bad["boxes"][0]["box"] = [100, 20, 10, 60]  # x2 < x1
    assert any("box" in e for e in schema.validate_frame(bad))
    bad = _frame()
    bad["boxes"][0]["tags"]["type"] = "headline"
    assert any("type" in e for e in schema.validate_frame(bad))


def test_frame_id_is_platform_run_seq():
    assert schema.frame_id("dmm", "maindmm1m-20261110-205150", 51) == "dmm-maindmm1m-20261110-205150-00051"


def _dataset(tmp_path):
    root = tmp_path / "ds"
    schema.init(root)
    img = root / "core" / "dmm-run1-00001.jpg"
    img.write_bytes(b"fake image")
    (root / "core" / "dmm-run1-00001.json").write_text(
        json.dumps(_frame(sha=schema.sha256(img)), ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_init_then_validate_is_clean(tmp_path):
    root = _dataset(tmp_path)
    for p in (
        "README.md",
        "CONTRIBUTING.md",
        "schema/frame.schema.json",
        "tools/validate.py",
        ".github/workflows/validate.yml",
        "core",
        "hard",
        "inbox",
        "disputed.json",
    ):
        assert (root / p).exists(), p
    assert schema.validate_dir(root) == []


def test_validate_dir_catches_sha_orphans_and_duplicates(tmp_path):
    root = _dataset(tmp_path)
    (root / "core" / "dmm-run1-00001.jpg").write_bytes(b"changed")
    errs = schema.validate_dir(root)
    assert any("sha256" in e for e in errs)
    root = _dataset(tmp_path / "b")
    (root / "core" / "orphan.png").write_bytes(b"x")
    assert any("orphan.png" in e for e in schema.validate_dir(root))
    root = _dataset(tmp_path / "c")
    (root / "hard").mkdir(exist_ok=True)
    for ext in ("jpg", "json"):
        src = root / "core" / f"dmm-run1-00001.{ext}"
        (root / "hard" / src.name).write_bytes(src.read_bytes())
    assert any("重复" in e for e in schema.validate_dir(root))


def test_skeleton_validator_runs_standalone(tmp_path):
    """数据仓库 CI 只有 Python 标准库：骨架里的 tools/validate.py 不 import hiro / 主仓库也能跑。"""
    root = _dataset(tmp_path)
    r = subprocess.run(
        [sys.executable, "-I", str(root / "tools" / "validate.py"), str(root)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    (root / "core" / "orphan.png").write_bytes(b"x")
    r = subprocess.run(
        [sys.executable, "-I", str(root / "tools" / "validate.py"), str(root)], capture_output=True, text=True
    )
    assert r.returncode == 1 and "orphan.png" in r.stdout


def test_cli_init_and_validate(tmp_path):
    env_py = [sys.executable, "-m", "tools.ocr_dataset"]
    r = subprocess.run([*env_py, "init", str(tmp_path / "x")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([*env_py, "validate", str(tmp_path / "x")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and "0 个问题" in r.stdout, r.stdout + r.stderr


def test_init_ignores_local_work_dir(tmp_path):
    root = schema.init(tmp_path / "ds")
    assert ".work/" in (root / ".gitignore").read_text(encoding="utf-8").split()


def test_init_ships_scenarios_next_to_validator(tmp_path):
    root = schema.init(tmp_path / "ds")
    assert (root / "tools" / "scenarios.json").is_file() and (root / "tools" / "validate.py").is_file()


def _scenario_frame(**kw):
    d = {
        "schema": 3, "id": "dmm-run1-00001", "image": "dmm-run1-00001.jpg", "sha256": "0" * 64,
        "source": {"run": "run1", "seq": 1}, "platform": {"kind": "dmm", "native": None, "stored": [720, 1280]},
        "page": "HIF_EXAM", "scenario": "exam", "truth": {}, "item_status": {}, "buttons": [],
    }  # fmt: skip
    d.update(kw)
    return d


@pytest.mark.parametrize("ver", [True, 3.0, 1.0, "3", None])
def test_schema_version_must_be_a_real_int(ver):
    """schema: true 会被当 v1、3.0 会被当 v3 检查（PR #104）：要拒绝，不是悄悄按别的版本过。"""
    assert any("schema" in e for e in schema.validate_frame(_frame(schema=ver)))


@pytest.mark.parametrize("bid", [[], {}, "a", None, True])
def test_bad_box_id_is_reported_not_crashing(bid):
    """框 id 是 [] 时以前 seen.add 直接 TypeError，后面的诊断和文件都不查了。"""
    bad = _frame()
    bad["boxes"][0]["id"] = bid
    bad["boxes"].append({**bad["boxes"][0], "id": 1})
    assert any("id" in e for e in schema.validate_frame(bad))


def test_duplicate_int_box_id_still_caught():
    bad = _frame()
    bad["boxes"].append({**bad["boxes"][0]})
    assert any("不重复" in e for e in schema.validate_frame(bad))


@pytest.mark.parametrize("buttons", [True, {}, "x", 3])
def test_buttons_must_be_a_list(buttons):
    errs = schema.validate_frame(_scenario_frame(buttons=buttons))
    assert any("buttons" in e for e in errs)


def test_buttons_missing_or_empty_list_is_fine():
    assert schema.validate_frame(_scenario_frame(buttons=[])) == []
    fr = _scenario_frame()
    del fr["buttons"]
    assert schema.validate_frame(fr) == []


def test_validate_dir_missing_root_fails(tmp_path):
    """目录输错了不能报「0 个问题」。"""
    errs = schema.validate_dir(tmp_path / "nope")
    assert errs and "不存在" in errs[0]
    ds = schema.init(tmp_path / "empty")  # 初始化好但没有帧的仓库照样通过
    assert schema.validate_dir(ds) == []


def test_validate_dir_main_image_must_be_this_partitions_same_name(tmp_path):
    """标注的 image 指到别的目录（哈希还对得上）会让贡献者拿到缺主图的数据集（PR #104）。"""
    root = _dataset(tmp_path)
    p = root / "core" / "dmm-run1-00001.json"
    fr = json.loads(p.read_text(encoding="utf-8"))
    (root / "hard").mkdir(exist_ok=True)
    (root / "hard" / "other.jpg").write_bytes((root / "core" / fr["image"]).read_bytes())
    for bad in ("../hard/other.jpg", "other.jpg", "dmm-run1-00001.png"):
        fr2 = {**fr, "image": bad}
        p.write_text(json.dumps(fr2), encoding="utf-8")
        assert any("图片" in e for e in schema.validate_dir(root)), bad
    (root / "hard" / "other.jpg").unlink()
    p.write_text(json.dumps(fr), encoding="utf-8")
    assert schema.validate_dir(root) == []
