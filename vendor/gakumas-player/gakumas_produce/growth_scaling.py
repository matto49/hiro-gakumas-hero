"""Community/historical growth arithmetic, separate from exam multipliers.

These are source-checked growth rules, not a native server implementation.
All coefficients are already-applied TOTAL permils; do not add them twice.
"""
from copy import deepcopy
from gakumas_exam.errors import Unsupported
from gakumas_exam.customize import nondefault

ATTRS = ('Vo', 'Da', 'Vi')
INT_MAX = 2**31 - 1
NEUTRAL = dict(parameter_bonus_permil=dict.fromkeys(ATTRS, 1000), star_bonus_permil=1000,
               stamina_cost_permil=1000, stamina_recovery_permil=1000, point_gain_permil=1000,
               school_cost_permil=1000, before_exam_recovery_permil=1000)


def checked(value, name, maximum=INT_MAX):
    if type(value) is not int or not 0 <= value <= maximum:
        raise Unsupported(name + ' requires a nonnegative bounded integer')
    return value


def scale_floor(value, total_permil):
    checked(value, 'base value'); checked(total_permil, 'total permil', 1000000)
    result = value * total_permil // 1000
    return checked(result, 'scaled result')


def pre_exam_recovery(max_hp, base_permil, bonus_permil):
    return scale_floor(scale_floor(max_hp, base_permil), bonus_permil)


def validate_modifiers(modifiers):
    if not isinstance(modifiers, dict) or set(modifiers) != set(NEUTRAL):
        raise Unsupported('Need exact growth modifier fields')
    weights = modifiers['parameter_bonus_permil']
    if not isinstance(weights, dict) or set(weights) != set(ATTRS):
        raise Unsupported('Need total parameter permils for Vo/Da/Vi')
    for name, value in weights.items(): checked(value, name + ' growth permil', 1000000)
    for name, value in modifiers.items():
        if name != 'parameter_bonus_permil': checked(value, name, 1000000)
    for name in ('stamina_cost_permil', 'stamina_recovery_permil', 'point_gain_permil', 'school_cost_permil'):
        if modifiers[name] != 1000:
            raise Unsupported('Growth modifier not ported: ' + name)


def parameter_caps(state, catalog, mode='produce-008'):
    base = catalog.one('Produce', mode)['idolCardParameterGrowthLimit']
    values = state.get('parameter_caps', dict.fromkeys(ATTRS, base))
    if not isinstance(values, dict) or set(values) != set(ATTRS):
        raise Unsupported('Need exact current parameter caps')
    for name, value in values.items():
        checked(value, name + ' cap')
        if value < base: raise Unsupported('Lowered parameter caps are not ported')
    return deepcopy(values)


def star_gain(state, base):
    return scale_floor(base, state.get('star_gain_permil', 1000))


def exact_level(catalog, table, key, level):
    rows=[r for r in catalog.by_id(table,key) if r['level']==level]
    if len(rows)!=1:raise Unsupported('Need exact '+table+' level')
    return rows[0]


def dearness_projection(catalog, character_id, level):
    """Read one CURRENT-level skill snapshot; report other traits explicitly."""
    checked(level, 'dearness level')
    rows = [r for r in catalog.rows('CharacterDearnessLevel')
            if r['characterId'] == character_id and r['dearnessLevel'] == level]
    if len(rows) != 1: raise Unsupported('Need exact existing character/dearness level')
    row = rows[0]; refs = [dict(table='CharacterDearnessLevel', row=deepcopy(row))]
    result = dict(star_gain_permil=1000, audition_effect_parameter_bonus_permil=0,
                  drink_capacity_addition=0)
    mapping = {'ProduceEffectType_StarPermilUp': 'star_gain_permil',
               'ProduceEffectType_AuditionParameterBonusMultiple': 'audition_effect_parameter_bonus_permil',
               'ProduceEffectType_ProduceDrinkPossessLimitUp': 'drink_capacity_addition'}
    other = []
    for entry in row['produceSkills']:
        skill = exact_level(catalog,'ProduceSkill',entry['id'],entry['level'])
        refs.append(dict(table='ProduceSkill', row=deepcopy(skill)))
        for index in (1, 2, 3):
            key = skill['produceEffectId' + str(index)]
            if not key: continue
            effect = catalog.one('ProduceEffect', key)
            trigger = catalog.one('ProduceTrigger', skill['produceTriggerId' + str(index)])
            refs.extend([dict(table='ProduceEffect', row=deepcopy(effect)), dict(table='ProduceTrigger', row=deepcopy(trigger))])
            field = mapping.get(effect['produceEffectType'])
            if field is None:
                other.append(dict(skill_id=entry['id'], level=entry['level'], effect=deepcopy(effect)))
                continue
            if (trigger['phaseType'] != 'ProducePhaseType_ProduceStart'
                or skill['activationCount'] != 1 or skill['activationRatePermil' + str(index)]
                or effect['effectValueMin'] != effect['effectValueMax']
                or any(nondefault(v) for k, v in effect.items() if k not in
                       {'id', 'produceEffectType', 'effectValueMin', 'effectValueMax'})):
                raise Unsupported('Dearness projection shape not ported')
            result[field] += checked(effect['effectValueMin'], 'dearness effect')
    return dict(character_id=character_id, level=level, totals=result, other_effects=other,
                references=refs, master_commit=catalog.info['commit'],
                scope='Projection of current-level grants; not account initialization. Other traits are reported, not applied. Already-applied totals must not be granted again.')


def hif_panel_projection(catalog, before_exam_level=0, cap_level=0):
    values = dict(before_exam_recovery_permil=1000); refs = []
    for level, key, effect_type, target in [
            (before_exam_level, 'produce_growth_panel_sheet-hif-05', 'ProduceEffectType_BeforeAuditionRefreshStaminaUp', 'before_exam_recovery_permil'),
            (cap_level, 'produce_growth_panel_sheet-hif-06', 'ProduceEffectType_ParameterLimitUp', 'additional_cap')]:
        checked(level, 'panel level', 6)
        if not level: continue
        root = exact_level(catalog,'ProduceGrowthPanel',key,level)
        if len(root['produceEffectIds']) != 1: raise Unsupported('Panel effect shape changed')
        leaf = catalog.one('ProduceEffect', root['produceEffectIds'][0])
        if (leaf['produceEffectType'] != effect_type or leaf['effectValueMin'] != leaf['effectValueMax']
            or any(nondefault(v) for k, v in leaf.items() if k not in {'id', 'produceEffectType', 'effectValueMin', 'effectValueMax'})):
            raise Unsupported('Panel effect semantics changed')
        values[target] = values.get(target, 0) + checked(leaf['effectValueMin'], 'panel effect')
        refs.extend([dict(table='ProduceGrowthPanel', row=deepcopy(root)), dict(table='ProduceEffect', row=deepcopy(leaf))])
    base = catalog.one('Produce', 'produce-008')['idolCardParameterGrowthLimit']
    values['parameter_caps'] = dict.fromkeys(ATTRS, base + values.pop('additional_cap', 0))
    return dict(totals=values, references=refs, master_commit=catalog.info['commit'],
                scope='Explicit enabled panel levels, 0 means disabled; not current account ownership or automatic reapplication')
