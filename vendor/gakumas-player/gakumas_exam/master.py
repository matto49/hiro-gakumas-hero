"""主数据访问：gakumasu-diff 固定快照（gakumas_exam.catalog），枚举名统一转成客户端整数值（enums.json 由 客户端）。

主数据 YAML 里枚举写成 "ProduceExamEffectType_ExamReview"；客户端运行时与存档里是整数（42 等）。这里把所有
"<枚举名>_<成员>" 形式的字符串按 enums.json 转换，未知枚举保持字符串。
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENUMS = json.loads((Path(__file__).parent / 'enums.json').read_text())
_ENUM_RE = re.compile(r'^([A-Z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$')


def enum_value(name, member):
    return ENUMS[name][member]


def E(name):
    """枚举成员表：E('ProduceExamEffectType')['ExamReview'] → 31。"""
    return ENUMS[name]


def convert(v):
    if isinstance(v, str):
        m = _ENUM_RE.match(v)
        if m and m.group(1) in ENUMS:
            members = ENUMS[m.group(1)]
            if m.group(2) in members:
                return members[m.group(2)]
        return v
    if isinstance(v, list):
        return [convert(x) for x in v]
    if isinstance(v, dict):
        return {k: convert(x) for k, x in v.items()}
    return v


class Master:
    """按表与主键取行（已转换枚举）。同一实例内缓存。commit=None 用 data/master/current.json。"""

    def __init__(self, commit=None):
        if commit:
            os.environ['GAKUMAS_MASTER_COMMIT'] = commit
        from .catalog import Catalog
        self.catalog = Catalog()
        if commit:
            os.environ.pop('GAKUMAS_MASTER_COMMIT', None)
        self.commit = self.catalog.info['commit']
        self._one = {}
        self._tables = {}

    def one(self, table, key, variant=None):
        k = (table, key, variant)
        hit = self._one.get(k)
        if hit is None:
            hit = self._one[k] = convert(self.catalog.one(table, key, variant))
        return hit

    def get(self, table, key, variant=None):
        try:
            return self.one(table, key, variant)
        except Exception:  # noqa: BLE001  主键不存在
            return None

    def table(self, table):
        hit = self._tables.get(table)
        if hit is None:
            hit = self._tables[table] = [convert(r) for r in self.catalog.rows(table)]
        return hit

    def __reduce__(self):
        """序列化只存主数据提交号，读回时取进程内共享的同一份（主数据只读；带 sqlite 连接，不能也不必复制）。"""
        return (shared, (self.commit,))

    # 常用表
    def card(self, card_id, upgrade=0):
        return self.one('ProduceCard', card_id, upgrade)

    def effect(self, effect_id):
        return self.one('ProduceExamEffect', effect_id)

    def trigger(self, trigger_id):
        return self.get('ProduceExamTrigger', trigger_id) if trigger_id else None

    def enchant(self, enchant_id):
        return self.one('ProduceExamStatusEnchant', enchant_id)

    def search(self, search_id):
        return self.one('ProduceCardSearch', search_id)

    def drink(self, drink_id):
        return self.one('ProduceDrink', drink_id)

    def drink_effect(self, key):
        return self.one('ProduceDrinkEffect', key)

    def item(self, item_id):
        return self.one('ProduceItem', item_id)

    def setting(self, setting_id='p_exam_setting-1'):
        return self.one('ExamSetting', setting_id)

    def customize(self, customize_id, count):
        return self.one('ProduceCardCustomize', customize_id, count)

    def customize(self, customize_id, count):
        """ProduceCardCustomize(id, customizeCount)。"""
        idx = self._tables.get('_customize_idx')
        if idx is None:
            idx = self._tables['_customize_idx'] = {(r['id'], r['customizeCount']): r for r in self.table('ProduceCardCustomize')}
        return idx.get((customize_id, count))

    def grow_effect(self, key):
        return self.one('ProduceCardGrowEffect', key)


@lru_cache(maxsize=4)
def load(commit=None):
    return Master(commit)


_SHARED = {}


def shared(commit):
    """按提交号共享的 Master（pickle / 多处构造共用）。"""
    m = _SHARED.get(commit)
    if m is None:
        m = _SHARED[commit] = Master(commit)
    return m
