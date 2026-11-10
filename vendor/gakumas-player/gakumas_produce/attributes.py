"""Client 3.4.1 attribute arithmetic; HIF stars and turn-order RNG are separate.

The pure functions preserve native floating-point operation order. Catalog
binding is an attribute-only experiment, not a complete HIF exam prediction.
"""
from copy import deepcopy
import math
from gakumas_exam.errors import Unsupported
from gakumas_exam.examconfig import f32

ATTRIBUTES = ('Vo', 'Da', 'Vi')
COLUMNS = ('vocalPermil', 'dancePermil', 'visualPermil')
CONFIG_COLUMNS = ('vocal', 'dance', 'visual')


def checked(value, name, maximum=10000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise Unsupported(name + ' must be an integer in 0..' + str(maximum))
    return value


def interpolate(value, lower, upper, low_bonus, high_bonus):
    # CalcPermil and GetPermil's three inlined copies use double.
    return low_bonus + math.ceil(((high_bonus-low_bonus)/(upper-lower))*(value-lower))


def penalty(value, baseline, minimum, maximum):
    # Raw GetPenaltyPermil helper. Calculate first bypasses this helper's
    # formula when the attribute meets its baseline (otherwise baseline ->100).
    return math.trunc((minimum/1000 + ((maximum/1000-minimum/1000)/baseline)*(baseline-value))*1000)


def normalize_bonus(component, common):
    product = component*common
    if not -2**31 <= product < 2**31:
        raise Unsupported('Native Int32 attribute product overflow')
    value = 1000 + (abs(product)//1000)*(1 if product >= 0 else -1)
    return math.trunc(f32(math.ceil(f32(f32(value)/f32(10)))*f32(10)))


ASSESSMENT = ('Bad', 'Normal', 'Good', 'Great')  # AuditionBattleStartEvaluationType 0..3


def assessment(value, requirement, upper_permil, lower_permil):
    """考试前每项属性的审查评定（界面图标 exam_battle_parameter_{type}），只用于显示，不进入得分计算。

    ProduceUtility.GetAssessmentType（3.4.1）：float32，
      value >  requirement：value >= requirement*(1+upper/1000) → Great，否则 Good；
      value <= requirement：value >= requirement*(1-lower/1000) → Normal，否则 Bad。
    upper/lower 取 ProduceSetting.produceAuditionTrendAssessmentPermilUpper/Lower（H.I.F p_setting-7/8 均为 110）。
    注意：正好等于要求值时评定为 Normal，但得分惩罚为 0（惩罚只在 value < requirement 时生效）。"""
    p, b = f32(value), f32(requirement)
    if value > requirement:
        return 'Great' if f32(f32(f32(upper_permil * requirement) / f32(1000)) + b) <= p else 'Good'
    return 'Normal' if f32(b + f32(f32(lower_permil * requirement) / f32(-1000))) <= p else 'Bad'


def battle_bonus_score(amount, bonus_permil):
    checked(amount, 'score amount', 2**63-1)
    checked(bonus_permil, 'bonus permil', 2**31-1)
    # CalcCurrentTurnBattleBonus. The epsilon ADD is
    # single precision too; subtracting epsilon in Python double differs.
    multiplier = f32(f32(bonus_permil)/f32(1000))
    value=f32(f32(multiplier*f32(amount)) + f32(-0.0001))
    if not math.isfinite(value) or value >= 2**63:
        raise Unsupported('Native Int64 score result overflow is outside supported arithmetic')
    return math.ceil(value)


def calculate(config, rows, stats, minimum, maximum):
    if set(stats) != set(ATTRIBUTES):
        raise Unsupported('Need exactly Vo/Da/Vi attributes')
    checked(minimum, 'penalty minimum', 1000);checked(maximum, 'penalty maximum', 1000)
    if minimum > maximum:
        raise Unsupported('Reversed penalty settings')
    ordered = sorted(rows, key=lambda r:r['parameter'])
    if not ordered or ordered[0]['parameter'] != 0 or len({r['parameter'] for r in ordered}) != len(ordered):
        raise Unsupported('Need unique score-table knots starting at zero')
    for row in ordered:
        checked(row['parameter'], 'score-table parameter')
        for column in COLUMNS:checked(row[column], column, 1000000)
    components = {};penalties = {}
    for attr, column, config_column in zip(ATTRIBUTES, COLUMNS, CONFIG_COLUMNS):
        value=checked(stats[attr], attr);baseline=checked(config[config_column], config_column)
        low=max((r for r in ordered if r['parameter'] <= value), key=lambda r:r['parameter'])
        high=next((r for r in ordered if r['parameter'] > value), None)
        components[attr] = low[column] if high is None else interpolate(value,low['parameter'],high['parameter'],low[column],high[column])
        penalties[attr] = 0 if value >= baseline else penalty(value,baseline,minimum,maximum)
    common = 1000-sum(penalties.values())
    bonuses = {a:normalize_bonus(components[a],common) for a in ATTRIBUTES}
    if min(bonuses.values()) < 0:
        raise Unsupported('Negative derived battle bonus')
    return dict(component_permil=components,penalty_permil=penalties,common_permil=common,
                parameter_bonus_permil=bonuses)


def hif_preview(mode, position, stats, idol_card_id='i_card-shro-3-000', catalog=None):
    from gakumas_exam.catalog import Catalog
    from .hif import HIFPlanner
    owned = catalog is None;c = catalog or Catalog()
    try:
        row=HIFPlanner(idol_card_id,c).configuration(mode,position)
        settings=list(c.rows('Setting'))
        if len(settings)!=1:raise Unsupported('Expected one global Setting row')
        setting=settings[0]
        minimum=setting['produceExamBattleScorePenaltyMinPermil']
        maximum=setting['produceExamBattleScorePenaltyMaxPermil']
        result=calculate(row['battle_config'],row['score_config_rows'],stats,minimum,maximum)
        return dict(schema_version=1,mode=mode,position=position,idol_card_id=idol_card_id,
                    stats=deepcopy(stats),master_commit=c.info['commit'],**result,
                    turns=row['battle_config']['turn'],
                    source=dict(battle_config=row['battle_config'],score_config_rows=row['score_config_rows'],
                                penalty_min_permil=minimum,penalty_max_permil=maximum),
                    scope='attribute-only; stars, gimmicks and other runtime modifiers excluded',
                    star_bonus_permil=None,turn_attributes=None)
    finally:
        if owned:c.db.close()




