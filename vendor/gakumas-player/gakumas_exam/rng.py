"""客户端 System.Random 替身：xorshift32（来源：gakumas_sim.core.NativeRandom，已与原生指令对照）。

存档字段 ExamSaveData.random 是当前状态（uint32）。"""


class NativeRandom:
    __slots__ = ('state', 'calls')

    def __init__(self, state):
        if type(state) is not int:
            raise ValueError('RNG state must be int')
        self.state = state & 0xffffffff
        self.calls = 0

    def _advance(self):
        old = self.state
        x = (old ^ (old << 13)) & 0xffffffff
        x = (x ^ (x >> 17)) & 0xffffffff
        self.state = (x ^ (x << 5)) & 0xffffffff
        self.calls += 1
        return old

    def integer(self, low, high):
        """[low, high) 均匀整数：乘法取高 32 位。"""
        if not low <= high:
            raise ValueError('Invalid RNG interval')
        return low + ((self._advance() * (high - low)) >> 32)

    def signed(self):
        return self._advance() - 2 ** 31

    def shuffle(self, values):
        """Fisher–Yates，从尾到头。"""
        for n in range(len(values), 1, -1):
            j = self.integer(0, n)
            values[j], values[n - 1] = values[n - 1], values[j]

    def copy(self):
        r = NativeRandom(self.state)
        r.calls = self.calls
        return r
