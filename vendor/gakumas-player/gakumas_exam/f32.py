"""float32 运算：客户端考试计算全部是单精度（ARM64 s 寄存器）。

两个 float32 的 + - * / 在 double 里算完再舍入回 float32 等于单精度直接运算（double 有效位 53 ≥ 2×24+2，无双重舍入误差），
所以每一步运算后调用 f() 即可。fcvtps / fcvtms 对应 ceil / floor 取整到 int，±inf 时原生返回 int.MinValue。
"""
import math
import struct

_S = struct.Struct('<f')
_CACHE = {}
INT_MIN = -2 ** 31
INT_MAX = 2 ** 31 - 1
EPS = -0.0001  # 原生常量


def f(x):
    """舍入到 float32。"""
    return _S.unpack(_S.pack(x))[0]


EPS32 = f(EPS)
NEG_EPS32 = f(0.0001)
ONE = 1.0
THOUSAND = 1000.0


def i2f(i):
    """scvtf：int → float32。"""
    return f(float(i))


def add(a, b):
    return f(a + b)


def sub(a, b):
    return f(a - b)


def mul(a, b):
    return f(a * b)


def div(a, b):
    return f(a / b)


def ceil_i(x):
    """fcvtps：向正无穷取整到 int32；inf / nan → int.MinValue（原生 csel 分支）。"""
    if math.isinf(x) or math.isnan(x):
        return INT_MIN
    return int(math.ceil(x))


def floor_i(x):
    """fcvtms。"""
    if math.isinf(x) or math.isnan(x):
        return INT_MIN
    return int(math.floor(x))


def trunc_i(x):
    """fcvtzs。"""
    if math.isinf(x) or math.isnan(x):
        return INT_MIN
    return int(x)


def permil(p):
    """permil / 1000.0f（原生到处出现的 scvtf + fdiv 1000）。"""
    return div(i2f(p), THOUSAND)


def ratio_int(value, ratio, is_ceil=True):
    """ExamEffectUtility.GetRatioEffectIntValue(value, ratio, isCeil=true)。

    value < 1 → 0；r = ratio/1000f；ceil(r*value + (-0.0001f)) 或 floor(r*value + 0.0001f)。value 用 ucvtf（无符号）。"""
    if value < 1:
        return 0
    key = (value, ratio, is_ceil)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    r = permil(ratio)
    x = mul(r, f(float(value)))
    out = ceil_i(add(x, EPS32)) if is_ceil else floor_i(add(x, NEG_EPS32))
    if len(_CACHE) > 100000:
        _CACHE.clear()
    _CACHE[key] = out
    return out
