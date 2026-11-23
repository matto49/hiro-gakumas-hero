"""python -m hiro.bench_bridge setup | check

setup：准备考试出牌算法要的东西（下载并校验 UINet 模型、onnxruntime 旧时转换、导出主数据），失败时退出码 1。
check：只检查（不下载），打印现在能不能用、缺什么。
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "check"
    from hiro.bench_bridge import advisor as A

    if not A.enabled():
        print("HIRO_BENCH_ADVISOR=0：考试出牌用社区规则 + LLM")
    try:
        algo = A.load_algo(download=(cmd == "setup"))
    except Exception as e:  # noqa: BLE001
        print(f"考试出牌算法 {A.algo_spec()} 不能用：{type(e).__name__}: {e}")
        if (where := _where(e)) is not None:
            print(f"  出错位置：{where}")
        print(f"  {_hint(e)}")
        print("考试出牌会退回社区规则（不调 LLM）")
        return 1
    print(f"考试出牌算法 {A.algo_spec()} 可用：{algo.describe()}")
    return 0


def _where(e: BaseException) -> str | None:
    """最里层出错的那一行（文件:行号 函数）。"""
    import traceback

    frames = traceback.extract_tb(e.__traceback__)
    if not frames:
        return None
    f = frames[-1]
    return f"{f.filename}:{f.lineno} {f.name}"


def _hint(e: BaseException) -> str:
    """按异常类型给一句该怎么查（以前一律写「网络？」，Windows 上真实原因是 gakumas_exam 没进 sys.path）。"""
    import urllib.error

    if isinstance(e, ModuleNotFoundError):
        return f'缺 Python 模块 {e.name}：确认 .[bench] 装上了（uv pip install -e ".[bench]"）、考试引擎在 vendor/gakumas-player'
    if isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError)):
        return "下载失败：检查网络 / 代理（HTTPS_PROXY），或手动把模型放进 ~/.cache/hiro/bench（HIRO_BENCH_CACHE）"
    if isinstance(e, ValueError) and "sha256" in str(e).lower():
        return "模型校验不对：删掉 ~/.cache/hiro/bench 里的模型文件重下"
    return "原因见上一行；修好后重跑 python -m hiro.bench_bridge setup"


if __name__ == "__main__":
    raise SystemExit(main())
