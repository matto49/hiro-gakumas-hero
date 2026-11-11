# 来源：digital-hiro hiro/loop/manifest.py（c3fc27f）；开源版：素材 / 主数据改从插件（hiro.plugins）取
"""ResourceManifest：运行前核对识别素材、主数据、布局校准、契约版本，记进 trace 的 meta；有问题只报警，不拦开局。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hiro.contracts import VERSION as CONTRACT_VERSION

VISION = Path(__file__).resolve().parents[1] / "vision"


def _sha1(path: Path) -> str | None:
    # 行尾统一成 LF：Windows 上 Git（core.autocrlf=true）检出成 CRLF，指纹和 Mac 不同，跨平台对比 trace 时会误以为素材不一样
    return hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12] if path.exists() else None


@dataclass
class ResourceManifest:
    asset_source: str | None = None  # AssetSource 插件从哪来（register / env / entry point / default）
    exam_state_source: str | None = None
    exam_advisor: str | None = None
    master_db: bool = False  # 主数据（game.db）能不能打开
    user_assets: str | None = None  # 使用者自建素材目录（没有时识别层只用 KAA 公开数据和随仓库素材包）
    bundled_assets: str | None = None  # 随仓库的素材包目录 assets/pack（HIRO_BUNDLED_ASSETS=0 关掉时为 None）
    layout_calib: str | None = None  # layout_calib.json 内容哈希
    signatures: str | None = None  # signatures.json 内容哈希
    contracts_version: str = CONTRACT_VERSION
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def collect(*, check_master: bool = True) -> ResourceManifest:
    from hiro import plugins

    m = ResourceManifest(layout_calib=_sha1(VISION / "layout_calib.json"), signatures=_sha1(VISION / "signatures.json"))
    for kind in ("asset_source", "exam_state_source", "exam_advisor"):
        try:
            setattr(m, kind, plugins.source(kind))
        except Exception as e:  # noqa: BLE001
            m.problems.append(f"插件 {kind} 不可用：{e!r}")
    try:
        assets = plugins.get("asset_source")
        user = getattr(assets, "user", None)
        m.user_assets = str(user) if user is not None and Path(user).is_dir() else None
        bundled = getattr(assets, "bundled", None)
        m.bundled_assets = str(bundled) if bundled is not None and Path(bundled).is_dir() else None
        if check_master:
            m.master_db = assets.master_db() is not None
            if not m.master_db:
                m.problems.append("主数据不可用（AssetSource.master_db() 为空；KAA 公开数据 resources/game_data/game.db 或 HIRO_KAA_GAME_DATA）")
    except Exception as e:  # noqa: BLE001
        m.problems.append(f"识别素材不可用：{e!r}")
    return m


def main() -> int:
    m = collect()
    print(json.dumps(m.to_dict(), ensure_ascii=False, indent=1))
    return 1 if m.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
