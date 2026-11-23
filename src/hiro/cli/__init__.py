"""工具层：`hiro` 命令行（设计稿 docs/ryugi/specs/2026-11-17-hiro-console-design.md §4、§9）。

桌面程序、内置 agent 和外部 agent 都只通过这里干活。每条命令输出一个 JSON 信封：
    {"ok": true, "data": {...}}
    {"ok": false, "error": {"code": "...", "message": "...", "hint": "..."}}
长任务（开局、扫描）的进度走事件流（hiro run events），每行一个 JSON。
"""
