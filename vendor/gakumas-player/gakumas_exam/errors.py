"""引擎对外的两种异常：Unsupported（规则 / 数据不支持，调用方应放弃这条路径）、IllegalAction（动作不合法）。"""


class Unsupported(ValueError):
    pass


class IllegalAction(ValueError):
    pass
