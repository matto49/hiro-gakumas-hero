"""空转看门狗的进展上报（hiro.loop.repeat_guard.progress）。

原来这里还回放 D38 第三十三局 KAA 培育准备（选偶像、切编成、租借、选回忆）各步报进展的时间线，
2026-11-24 随 KAA 主循环一起删了；新主循环的开局准备在 hiro.runtime.opening 里，由 Runtime 的看门狗管。
"""

from hiro.loop import repeat_guard


def test_progress_without_session_is_noop(monkeypatch):
    monkeypatch.setitem(repeat_guard.ACTIVE, "session", None)
    repeat_guard.progress("没有会话")  # 不报错
