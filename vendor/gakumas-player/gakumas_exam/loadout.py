"""编成换算：支援卡（id + 等级 / 突破）、回忆能力 → new_exam 要的 supports 与 skills。

    from gakumas_exam.loadout import loadout
    kw = loadout(m, supports=[('s_card-3-0010', 60), ('s_card-2-0007', {'rank': 4})],
                 memory_abilities=['memory_ability-…-001', …], audition='final', rng=random.Random(1))
    ex = new_exam(None, m, seed, deck=…, stamina=…, turn_types=…, **kw)

    loadout(m, supports=['Da/SSR/4', 'Da/SSR/4', 'Vo/SR/2', 'assist/SSR/4'])    # 通用描述：属性 / 稀有度 / 突破
    loadout(m, idol='i_card-shro-3-007')                                         # 不给 supports：社区默认编成（见 community_supports）

考试只看支援卡的「属性 + 稀有度 + 突破（等级）」：同稀有度同属性的卡强化概率曲线相同，检索范围都是手牌，所以不必绑定账号里的具体卡。
通用描述会分配互不相同的代表卡 id（考试里「本回合已成功的支援不再参与」按 id 判，重复 id 会算错）。

考试里用到的只有两样（主数据里会进考试的只有这两类，P 偶像卡的技能都在培育阶段生效）：
1. 支援卡的考试内强化（ExamSupportCardData）：_filterParameterType = SupportCard.produceCardUpgradeLessonParameterType，
   _cardSearchId = upgradeProduceCardSearchId，_produceCardUpgradePermil = floor(基础 × (1000 + 技能值) / 1000)。
   基础 = SupportCard.produceCardUpgradePermil（SSR 37 / SR 28 …）；技能值 = 该等级已解锁的
   「SupportCardProduceCardUpgradeProbabilityUp」技能（SupportCardProduceSkillLevel* 表，等级 → 技能等级 → ProduceEffect 值）。
   客户端不算这个数（ExamTransitionParam 直接读服务器下发的 UserProduceProgressSupportCard），公式与取整是从实机存档反推的：
   19 份存档 12 张支援卡的值全部落在突破满级上（61 = SSR 40 级 = 55 级 = 60 级、SR 56 = 50 级），
   只有向下取整三者同时成立（四舍五入 55 级得 71，向上取整 40 级得 62）。
2. 回忆能力里的考试附魔（ProduceSkill 效果类型 60，全部是 p_memory_skill）：MemoryAbility.skillId → 技能。
   触发看 produceTriggerId1：start_audition_mid1 / mid2 / final、start_audition-for_hif_memory（H.I.F）、start_lesson-*（课程）。
   activationRatePermil1 > 0 的按概率发动（培育侧掷，考试只收结果）：给 rng 就掷一次，不给就全部当作发动，
   也可以用 chosen 显式指定。produceGroupIds 非空时只在这些培育组里生效（produce_group）；isUniqueActivation 同技能只生效一次。
"""
from .master import E

ATTR = {'vo': 1, 'da': 2, 'vi': 3, 'assist': 0}
RARITY = {'r': 1, 'sr': 2, 'ssr': 3}
GROWTH = {1: 'produceVocalGrowthRatePermil', 2: 'produceDanceGrowthRatePermil', 3: 'produceVisualGrowthRatePermil'}
ATTR_NAME = {1: 'Vo', 2: 'Da', 3: 'Vi'}

SKILL_TABLES = ('SupportCardProduceSkillLevelVocal', 'SupportCardProduceSkillLevelDance',
                'SupportCardProduceSkillLevelVisual', 'SupportCardProduceSkillLevelAssist')
AUDITION_TRIGGER = {
    'mid1': 'p_trigger-start_audition_mid1', 'mid2': 'p_trigger-start_audition_mid2', 'final': 'p_trigger-start_audition_final',
    'hif': 'p_trigger-start_audition-for_hif_memory',
}


def _support(m, sid):
    row = next((r for r in m.table('SupportCard') if r['id'] == sid), None)
    if row is None:
        raise KeyError(f'没有这张支援卡：{sid}')
    return row


def level_cap(m, sid, rank):
    """突破数（0–4）→ 等级上限（SupportCardLevelLimit）。"""
    lim = _support(m, sid)['supportCardLevelLimitId']
    rows = [r for r in m.table('SupportCardLevelLimit') if r['id'] == lim and r['rank'] <= rank]
    return max(r['levelLimit'] for r in rows)


def support_skills(m, sid, level):
    """该等级已解锁的支援技能：{produceSkillId: 技能等级}。"""
    out = {}
    for t in SKILL_TABLES:
        for r in m.table(t):
            if r['supportCardId'] == sid and r['supportCardLevel'] <= level:
                out[r['produceSkillId']] = max(out.get(r['produceSkillId'], 0), r['produceSkillLevel'])
    return out


def _skill_row(m, sid, level=1):
    return next((r for r in m.table('ProduceSkill') if r['id'] == sid and r['level'] == level), None)


def upgrade_permil(m, sid, level):
    """考试内强化概率（‰）。"""
    PE = E('ProduceEffectType')['SupportCardProduceCardUpgradeProbabilityUp']
    base = _support(m, sid)['produceCardUpgradePermil']
    add = 0
    for skid, lv in support_skills(m, sid, level).items():
        sk = _skill_row(m, skid, lv)
        for k in ('produceEffectId1', 'produceEffectId2', 'produceEffectId3'):
            pe = m.get('ProduceEffect', sk.get(k)) if sk and sk.get(k) else None
            if pe and pe['produceEffectType'] == PE:
                add += pe['effectValueMin']
    return base * (1000 + add) // 1000


def support_entry(m, sid, level):
    """ExamSaveData.supportCardList 的一项。level 可给整数等级，或 {'rank': 突破数}（取该突破的满级）。"""
    if isinstance(level, dict):
        level = level_cap(m, sid, level['rank'])
    row = _support(m, sid)
    return {'_supportCardId': sid, '_filterParameterType': row['produceCardUpgradeLessonParameterType'],
            '_cardSearchId': row['upgradeProduceCardSearchId'], '_produceCardUpgradePermil': upgrade_permil(m, sid, level)}


def parse_spec(spec):
    """'Da/SSR/4' / ('Da', 'SSR', 4) / {'attr': 'Da', 'rarity': 'SSR', 'rank': 4} → (属性 0–3, 稀有度 1–3, 突破 0–4)。"""
    if isinstance(spec, str):
        spec = spec.split('/')
    if isinstance(spec, dict):
        spec = (spec.get('attr'), spec.get('rarity', 'SSR'), spec.get('rank', 4))
    attr, rarity, rank = (list(spec) + ['SSR', 4])[:3]
    attr = ATTR[str(attr).lower()] if not isinstance(attr, int) else attr
    rarity = RARITY[str(rarity).lower()] if not isinstance(rarity, int) else rarity
    return attr, rarity, int(rank)


def _generic_avoiding(m, specs, taken):
    out, used = [], set(taken)
    for spec in specs:
        attr, rarity, rank = parse_spec(spec)
        pool = sorted((r for r in m.table('SupportCard') if r['rarity'] == rarity and r['produceCardUpgradeLessonParameterType'] == attr),
                      key=lambda r: r['id'])
        card = next((r for r in pool if r['id'] not in used), None)
        if card is None:
            raise ValueError(f'没有足够的支援卡对应 {spec}')
        used.add(card['id'])
        out.append(support_entry(m, card['id'], {'rank': rank}))
    return out


def generic_entries(m, specs):
    """通用描述 → supportCardList 项。每个描述取一张符合（稀有度、属性；assist = 不限属性）的代表卡，同一编成里不重复。"""
    return _generic_avoiding(m, specs, set())


def attr_order(m, idol_card_id, main_attr=None):
    """偶像卡 → 属性顺序（主属性在前）。主属性缺省取成长率最高的属性（社区做法；数字小广对 Campus 広 另定 Da，由调用方传 main_attr）。"""
    cid = idol_card_id.replace('i_card-skin-', 'i_card-')
    row = next(r for r in m.table('IdolCard') if r['id'] == cid)
    growth = {a: row.get(k) or 0 for a, k in GROWTH.items()}
    main = ATTR[main_attr.lower()] if isinstance(main_attr, str) else (main_attr or max(growth, key=lambda a: growth[a]))
    return [main] + sorted((a for a in growth if a != main), key=lambda a: -growth[a])


def community_supports(m, idol=None, main_attr=None, pattern='4-1-1', rarity='SSR', rank=4):
    """社区默认支援编成（H.I.F / 初 通用）：6 张（自有 5 + 租借 1），主属性 4 张，按 4-1-1 或 4-2-0 分配，SSR 满突。
    依据：いおん H.I.F 系列 ep08（四张同属性支援对应的 SP 让一次 SP 同时触发多张支援；稳妥属性线一 2000 / 二 1400 / 三 1100），
    digital-hiro docs/hif-playbook.md、docs/formation-plan.md。主属性：给 idol 时取成长率最高的属性，或用 main_attr 指定。
    顺序：主属性在前（实机顺序是编成格顺序，影响抽牌强化的掷骰顺序；要和某一局对齐时请按实机格子顺序显式给 supports）。"""
    if idol is not None:
        order = attr_order(m, idol, main_attr)
    else:
        main = ATTR[main_attr.lower()] if isinstance(main_attr, str) else main_attr
        if not main:
            raise ValueError('需要 idol 或 main_attr')
        order = [main] + [a for a in (1, 2, 3) if a != main]
    counts = [int(x) for x in pattern.split('-')]
    specs = [(a, rarity, rank) for a, n in zip(order, counts) for _ in range(n)]
    return generic_entries(m, specs)


def memory_exam_skills(m, ability_ids, audition, produce_group=None):
    """回忆能力 → 这场考试可能挂上的考试附魔技能 [(skill_id, 发动率‰ 或 None=必定)]，按能力顺序，同技能（isUniqueActivation）去重。
    audition：'mid1' / 'mid2' / 'final' / 'hif'，或直接给 ProduceTrigger id。"""
    trig = AUDITION_TRIGGER.get(audition, audition)
    out, seen = [], set()
    for aid in ability_ids:
        ab = next((r for r in m.table('MemoryAbility') if r['id'] == aid), None)
        if ab is None:
            raise KeyError(f'没有这个回忆能力：{aid}')
        if ab.get('produceGroupIds') and produce_group is not None and produce_group not in ab['produceGroupIds']:
            continue
        sk = _skill_row(m, ab['skillId'], ab.get('level', 1))
        if sk is None or sk.get('produceTriggerId1') != trig:
            continue
        pe = m.get('ProduceEffect', sk.get('produceEffectId1'))
        if not pe or pe['produceEffectType'] != 60:
            continue
        if ab.get('isUniqueActivation') and sk['id'] in seen:
            continue
        seen.add(sk['id'])
        rate = sk.get('activationRatePermil1') or 0
        out.append((sk['id'], rate if rate > 0 else None))
    return out


def loadout(m, supports=None, memory_abilities=(), audition='final', produce_group=None, rng=None, chosen=None,
            idol=None, main_attr=None, pattern='4-1-1'):
    """返回 new_exam 的关键字参数 {'supports': [...], 'skills': [...]}。
    supports：None = 社区默认（需要 idol 或 main_attr，否则无支援）；[] = 不带支援；列表项可以是
    (支援卡 id, 等级 或 {'rank': n}) / 通用描述 'Da/SSR/4' / 现成的 supportCardList 项（dict 带 _supportCardId）。
    概率发动的回忆技能：chosen（技能 id 集合）优先；否则有 rng 就各掷一次 rng.random()*1000 < 发动率；都没有则全部发动。"""
    if supports is None:
        sup = community_supports(m, idol, main_attr, pattern) if (idol or main_attr) else []
    else:
        sup, generic = [], []
        for it in supports:
            if isinstance(it, dict) and '_supportCardId' in it:
                sup.append(dict(it))
            elif isinstance(it, (tuple, list)) and len(it) == 2 and str(it[0]).startswith('s_card-'):
                sup.append(support_entry(m, it[0], it[1]))
            else:
                sup.append(None)
                generic.append(it)
        if generic:
            taken = {s['_supportCardId'] for s in sup if s}
            gen = iter(_generic_avoiding(m, generic, taken))
            sup = [s if s is not None else next(gen) for s in sup]
    skills = []
    for skid, rate in memory_exam_skills(m, memory_abilities, audition, produce_group):
        if rate is None:
            skills.append(skid)
        elif chosen is not None:
            if skid in chosen:
                skills.append(skid)
        elif rng is not None:
            if rng.random() * 1000 < rate:
                skills.append(skid)
        else:
            skills.append(skid)
    return {'supports': sup, 'skills': skills}
