"""GKMS 插件测试用的假 client（不起子进程）。"""

import copy

HELLO = {"format": "gkms.pickv.v1", "algo": 2, "tau": 0.05, "reroll_gain": 0.1}


class FakeClient:
    def __init__(self, **replies):
        self.replies = {"hello": HELLO, **replies}
        self.calls = []

    def call(self, op, *, timeout=None, **args):
        self.calls.append((op, args))
        r = self.replies.get(op)
        if isinstance(r, Exception):
            raise r
        return (r(args) if callable(r) else copy.deepcopy(r)) or {}  # 真 client 同样把空结果当 {}

    def close(self):
        pass


def values(take, reroll_gain=None, held=None, up=None, state="ok"):
    return {"state": state, "take": take, "held": held or {}, "up": up or {}, "reroll_gain": reroll_gain}
