"""RapidOCR 的 PP-OCRv3 日文 / 英文识别模型自己托管（去 KAA 第 2 批）：不再从 kaa 包里找，
缺了就提示跑 tools/assets/rapid_models.py（从本仓库 GitHub Release 下载、校验 SHA256）。"""

import hashlib
import importlib.util
import io
from pathlib import Path

from hiro.vision import ocr

import pytest

pytestmark = [pytest.mark.synthetic, pytest.mark.play_free]

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("rapid_models", ROOT / "tools/assets/rapid_models.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_model_points_to_download_script_not_kaa(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "RAPID_DIR", tmp_path)
    real = importlib.util.find_spec

    def no_kaa(name, *a, **kw):
        assert name != "kaa", "不该再去 kaa 包里找模型"
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", no_kaa)
    with pytest.raises(RuntimeError, match=r"rapid_models\.py"):
        ocr._rapid_model("ja")
    (tmp_path / ocr.RAPID_MODELS["ja"]).write_bytes(b"x")
    assert ocr._rapid_model("ja") == tmp_path / ocr.RAPID_MODELS["ja"]


def test_download_checks_sha256(tmp_path, monkeypatch):
    tool = _tool()
    assert set(tool.MODELS) == set(ocr.RAPID_MODELS.values())
    good = b"onnx-bytes"
    models = {name: (f"https://example/{name}", hashlib.sha256(good).hexdigest()) for name in tool.MODELS}
    monkeypatch.setattr(tool, "MODELS", models)
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(good))
    assert tool.fetch(tmp_path) == sorted(models)
    assert all((tmp_path / n).read_bytes() == good for n in models)
    assert tool.fetch(tmp_path) == []  # 已有且校验对：不重复下载

    (tmp_path / next(iter(models))).write_bytes(b"broken")
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(b"tampered"))
    with pytest.raises(SystemExit, match="校验不对"):
        tool.fetch(tmp_path)


def test_release_urls_and_hashes_are_pinned():
    tool = _tool()
    for name, (url, sha) in tool.MODELS.items():
        assert url.startswith("https://github.com/matto49/hiro-gakumas-hero/releases/download/") and url.endswith(
            "/" + name
        )
        assert len(sha) == 64
