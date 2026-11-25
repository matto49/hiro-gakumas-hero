"""`hiro plugins list`：装了哪些插件、能不能用，以及 strategy.json 生效的对应表（2026-11-25 插件架构 spec 4.6）。"""

from __future__ import annotations

from dataclasses import asdict

from hiro.cli.registry import Risk, command

KINDS = ("observation_source", "produce_advisor", "exam_advisor")


def config_path():
    from hiro.cli.home import ensure_home

    return ensure_home() / "strategy.json"


def summary() -> dict:
    from hiro import plugins, strategy

    path = config_path()
    cfg = strategy.load(path)
    installed = [asdict(i) for k in KINDS for i in plugins.available(k)]
    return {
        "plugins": installed,
        "config": {
            "file": str(path),
            "exists": path.is_file(),
            "mapping": cfg.mapping(),
            "warnings": list(cfg.warnings),
        },
    }


@command(
    "plugins list",
    Risk.READ,
    "列出已装的插件（数据来源 / 育成 / 考试出牌）：id、版本、能接哪些页面、能不能用；以及 HIRO_HOME/strategy.json 里每个决策点配了哪个插件。",
)
def _list(a: dict) -> dict:
    return summary()
