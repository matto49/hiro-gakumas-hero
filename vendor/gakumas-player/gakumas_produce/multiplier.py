"""考试属性倍率：属性 → 基础倍率 × 亲爱度加成 1.5 × スター性（星星）倍率。

已确认：
- 客户端合成（反编译 + 原生执行 4,312 组）：final = ceil(base × (1+育成效果) × (1+投票+星星) / 10) × 10；
- 属性 → 基础倍率：attributes.hif_preview（插值 + 属性审查基准惩罚：某项低于本场配置值时三项共同扣分）；
- 社区 wiki（strategy/evidence/hif-wiki-20261016-v65.json）：基础倍率 × 亲爱度加成 1.5（小数截断）× 星星倍率；
  星星审查基准 选拔① 50 / ② 200 / ③ 400 / 本战 R1 600 / R2 800（= master starScoreBonusBaseLine）；
  未达基准约 1.85~2 倍，正好达到 2 倍，超过后 2~3 倍、3 倍封顶；
- 夺冠局：R1 星星 916 ≥ 600 → 已封顶 3 倍，1.5×3=4.5，Vo/Vi 还原到 10 位精度；R2 1096/800 → 星星倍率 2.911。

社区核对（2026-10-30，strategy/evidence/hif-community-20261030.json）：seesaawiki / wikiwiki / 攻略文均无精确公式，
只有上面的边界与「基准差 1 约 1.99 倍」；下面的曲线满足这些边界，形状仍是本地拟合。

假设（ASSUMED，待更多考试数据）：
- 超过基准后按「星星/基准」线性增长：factor = 2 + STAR_SLOPE×(星星/基准-1)，STAR_SLOPE 由 R2 一个点拟合；
  若改为按星星绝对差线性，R1 需要 ≥925 星才封顶（记录 916），所以暂取比值模型；
- 未达基准：factor = 1.85 + 0.15×(星星/基准)。
每打一场考试，就往 simulator/fixtures/multiplier-observations.json 补一条（存档读倍率，画面读属性与星星）。
"""
from __future__ import annotations
import json
import math
from pathlib import Path

from .attributes import hif_preview

OBSERVATIONS=Path(__file__).resolve().parents[1]/'simulator'/'fixtures'/'multiplier-observations.json'
ATTRS=('Vo','Da','Vi')


def load_observations(path=OBSERVATIONS):
    return json.loads(Path(path).read_text())['observations']


def base_permil(mode,position,stats,idol_card_id):
    return hif_preview(mode,position,stats,idol_card_id=idol_card_id)['parameter_bonus_permil']


AFFECTION=1.5
STAR_CAP=3.0
STAR_SLOPE=(4.3668/AFFECTION-2.0)/(1096/800-1)  # 由 R2 拟合，约 2.46（ASSUMED 线性）


def star_baseline(mode,position,idol_card_id,catalog=None):
    from gakumas_exam.catalog import Catalog
    from .hif import HIFPlanner
    own=catalog is None;c=catalog or Catalog()
    try:return HIFPlanner(idol_card_id,c).configuration(mode,position)['difficulty']['starScoreBonusBaseLine']
    finally:
        if own:c.db.close()


def star_factor(stars,baseline):
    r=stars/baseline
    if r<1:return 1.85+0.15*r
    return min(STAR_CAP,2.0+STAR_SLOPE*(r-1))


def exam_bonus(mode,position,stats,stars,idol_card_id):
    """预测本场三项最终倍率（permil）。"""
    m=AFFECTION*star_factor(stars,star_baseline(mode,position,idol_card_id))
    return predict(mode,position,stats,idol_card_id,m)


def implied_multiplier(observation):
    """M = 实际 / 基础，逐属性给出；属性记录可信时三项应几乎相同。"""
    base=base_permil(observation['mode'],observation['position'],observation['stats'],observation['idol_card_id'])
    return {a:observation['actual_permil'][a]/base[a] for a in ATTRS},base


def predict(mode,position,stats,idol_card_id,multiplier):
    """给定观众乘数 M 预测最终倍率（与客户端一样最后向上对齐到 10）。"""
    base=base_permil(mode,position,stats,idol_card_id)
    return {a:math.ceil(base[a]*multiplier/10-1e-9)*10 for a in ATTRS}
