"""示例数据来源：从 HIRO_EXAMPLE_JSON 指的 JSON 文件读「页面 → 字段」，假装是实时数据（不是回放对局记录）。只用于演示接口。

文件形如 {"ACTION_SELECT": {"produce_point": 77}, "hidden": {"seed": 1}}；hidden 是画面上看不到的信息，
框架照收，但用过的对局会打上 uses_hidden 标记。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hiro.plugins import SourceRead


class JsonFileSource:
    id = "example_json"
    version = "0.1.0"
    contracts = "1"
    trust = "screen"  # 和画面同档：画面读到的字段以画面为准，插件只补画面没读到的
    pages = None  # 所有页面都问

    def start(self, context: dict[str, Any]) -> None:
        path = os.environ.get("HIRO_EXAMPLE_JSON")
        self.data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}

    def read(self, page: str, frame_t: float, state: Any) -> SourceRead | None:
        fields = self.data.get(page)
        if not fields:
            return None
        # 真的数据来源要给数据对应的本机时间；这里是固定的 JSON，直接用截图时间
        return SourceRead(t=frame_t, fields=dict(fields), hidden=dict(self.data.get("hidden") or {}))

    def stop(self) -> None:
        pass
