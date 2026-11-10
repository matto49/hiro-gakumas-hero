"""开新局：按客户端开考流程从「开考前」状态走到第 1 回合主阶段。

    ex = new_exam(template_save, master, seed=123, deck=[('p_card-02-act-3_039', 1, [1, 0]), ...], items=['pitem_02-3-057-0'])

模板存档（任一实机开局存档）提供考试设定：体力 / 上限、回合数与属性、参数加成、NPC、饮料、支援卡，以及
SetUpExam 挂上的开考附魔（偶像卡 / 支援卡 / 回忆，存档里带 _startEnchantOriginId 的附魔；回合计数清零）。
其余状态（第 1 回合开始后产生的）全部丢弃，P 道具附魔按 items 重新挂。

流程（原生 ExamStandBy → TurnStart 1）：
  [对战考试] 生成回合属性（消耗随机数，可选）→ P 道具附魔（ItemList 顺序；ProduceItemEffect.effectTurn / effectCount，每回合上限 -1）
  → 洗牌（Fisher–Yates，len−1 次 integer(0,n)）→ ExamStartExam(6) 附魔 + 卡附魔 → 第 1 回合开始
  （SpendInitialTurn：计时 / 间隔附魔 TurnCount+1；ExamStartTurn；SetInitialCard；StartExamPlay / StartPlay / 计时）。

限制：模板存档里只含 ExamStartExam / StartExamPlay 阶段的开考附魔在第 1 回合主阶段前已被移除，无法从存档恢复；
需要时用 start_enchants 显式给出附魔 id 列表。定制 P 道具：SetUpExam 直接执行其永久附魔（produceEffectType 127）的效果。
"""
from .cards import ExamCard
from .exam import Ctx
from .master import E
from .newgame import generate_turn_types
from .rng import NativeRandom
from .save import load
from .status import TriggerEffect
from . import difftrig, triggers

PH = E('ProduceExamPhaseType')


def item_enchants(master, item_id):
    """ProduceItem.skills → ProduceItemEffect(effectType 2 考试附魔) → (turn, count, 附魔行)。"""
    rows = [r for r in master.table('ProduceItem') if r['id'] == item_id]
    if not rows:
        return None
    out = []
    for sk in rows[0].get('skills') or []:
        eff = master.get('ProduceItemEffect', sk.get('produceItemEffectId'))
        if eff and eff.get('produceExamStatusEnchantId'):
            out.append((eff.get('effectTurn', 0), eff.get('effectCount', 0), master.enchant(eff['produceExamStatusEnchantId'])))
    return rows[0], out


def permanent_enchants(master, item_id):
    """定制 P 道具（ProduceCustomizeItem）里 produceEffectType 127（exam_permanent_audition_status_enchant）的附魔。"""
    out = []
    for r in master.table('ProduceCustomizeItem'):
        if r['id'] != item_id:
            continue
        for pid in r.get('produceEffectIds') or []:
            pe = master.get('ProduceEffect', pid)
            if pe and pe.get('produceEffectType') == 127 and pe.get('produceExamStatusEnchantId'):
                out.append(master.enchant(pe['produceExamStatusEnchantId']))
    return out


COUNTERS = ('fullPowerPointGetSumCount', 'blockConsumptionSumCount', 'reviewConsumptionSumCount', 'stanceChangeCount',
            'stanceFullPowerChangeCount', 'stanceConcentrationChangeCount', 'stancePreservationChangeCount', 'totalConsumeStamina',
            'totalDrawCardCount', 'currentTurnTotalAddParameter', 'currentTurnTotalConsumeStamina', 'currentTurnTotalBlock')


def new_exam(save, master, seed, deck=None, items=None, start_enchants=None, randomize_turns=False, stamina=None, gimmicks=None,
             keep_start_enchants=True, turn_types=None, supports=None, bonus_permil=None, config_weights=None, drinks=None,
             plan_type=None, exam_type=1, setting_id='p_exam_setting-1', skills=None, max_stamina=None, contest_stage=None):
    """save=None 时不用模板：考试设定全部由参数给出（turn_types 或 config_weights+randomize_turns 定回合，bonus_permil 回合倍率，
    stamina、deck、items、drinks、supports、start_enchants（偶像 / 支援 / 回忆的开考附魔 id）、gimmicks）。
    contest_stage：比赛赛季关卡 id（gakumas_exam/contest_stage.py，目前 190–192）。"""
    if save is None:
        from .exam import Exam
        ex = Exam(master, setting_id=setting_id)
        ex.exam_type = exam_type
        ex.plan_type = plan_type or 0
        ex.counters = {k: 0 for k in COUNTERS}
        ex.drinks = list(drinks or [])
        ex.supports = list(supports or [])
        if bonus_permil:
            ex.bonus_permil = dict(bonus_permil)
        if turn_types is None and not (randomize_turns and config_weights):
            raise ValueError('不用模板时需要 turn_types，或 randomize_turns + config_weights')
        if turn_types is None:
            ex.limit_turn = config_weights.pop('turns')
        save = {'userPlayLogList': [], 'vocalConfigParameter': (config_weights or {}).get(1, 0),
                'danceConfigParameter': (config_weights or {}).get(2, 0), 'visualConfigParameter': (config_weights or {}).get(3, 0)}
        keep_start_enchants = False
        if stamina is None:
            raise ValueError('不用模板时需要 stamina')
    else:
        ex = load(save, master)
        if drinks is not None:
            ex.drinks = list(drinks)
        if bonus_permil:
            ex.bonus_permil = dict(bonus_permil)
        if plan_type is not None:
            ex.plan_type = plan_type
    sc = ex.status
    # ---- 回到开考前
    keep = []
    for s in sc.effects:
        if keep_start_enchants and isinstance(s, TriggerEffect) and s.source.get('origin') and not s.source.get('item'):
            s.turn_count, s.phase_count, s.passing = 0, {}, False
            keep.append(s)
    sc.effects = keep
    for sid in skills or []:   # ProduceSkill（回忆 / 支援 / 偶像）里的考试附魔（produceEffectType 60）= SetUpExam 的开考附魔
        sk = master.get('ProduceSkill', sid) or next((r for r in master.table('ProduceSkill') if r['id'] == sid), None)
        for k in ('produceEffectId1', 'produceEffectId2', 'produceEffectId3'):
            pe = master.get('ProduceEffect', sk.get(k)) if sk and sk.get(k) else None
            if pe and pe.get('produceEffectType') == 60 and pe.get('produceExamStatusEnchantId'):
                en = master.enchant(pe['produceExamStatusEnchantId'])
                sc.TryAddTriggerEffectStatus(master.trigger(en['produceExamTriggerId']), [master.effect(i) for i in en['produceExamEffectIds']],
                                             1, -1, -1, -1, en['id'], source={'origin': sid})
    for eid in start_enchants or []:
        en = master.enchant(eid)
        sc.TryAddTriggerEffectStatus(master.trigger(en['produceExamTriggerId']), [master.effect(i) for i in en['produceExamEffectIds']],
                                     1, -1, -1, -1, en['id'], source={'origin': eid})
    sc.idol_type, sc.idol_step = 0, 0
    ex.score = 0
    ex.block = 0
    if stamina is not None:   # 开考体力（缺省上限同值）
        ex.stamina = ex.max_stamina = stamina
    if max_stamina is not None:
        ex.max_stamina = max_stamina
    ex.counters = {k: 0 for k in ex.counters}
    if turn_types is not None:   # 回合属性（1 Vo / 2 Da / 3 Vi），回合数 = 长度
        ex.turn_types = list(turn_types)
        ex.limit_turn = len(turn_types)
    ex.current_turn = 0
    ex.extra_turn = 0
    ex.remain_turn = ex.limit_turn
    ex.exam_card_play_count = ex.turn_card_play_count = 0
    ex.turn_used_supports = []
    if supports is not None:   # 支援卡（抽牌时概率强化）：模板存档里的，或显式给定（[] = 无）
        ex.supports = list(supports)
    ex.play_log = []
    ex.events = []
    ex.ended = False
    ex.pending_start = False
    # 牌组：给定 (id, 强化, 定制次数列表)；缺省用模板全部卡（清掉考试中的强化 / 成长 / 附魔进度）
    if deck is None:
        cards = [ExamCard(master, c.card_id, c.base_upgrade, c.customize_counts) for c in ex.all_cards()]
    else:
        cards = [ExamCard(master, *d) if isinstance(d, (tuple, list)) else d for d in deck]
    ex.zones = {z: [] for z in ex.zones}
    ex.zones['deck'] = cards
    ex.playing = None
    ex.rng = NativeRandom(seed & 0xffffffff)
    if gimmicks is not None:   # 机关组 id 列表（ProduceExamGimmickEffectGroup）
        ex.setup_gimmicks([r for g in gimmicks for r in master.table('ProduceExamGimmickEffectGroup') if r['id'] == g])   # 一组多行
    base = Ctx(ex)
    if randomize_turns:
        ex.turn_types = generate_turn_types(ex.limit_turn, {1: save['vocalConfigParameter'], 2: save['danceConfigParameter'],
                                                            3: save['visualConfigParameter']}, ex.rng)
    # ---- SetUpExam：定制 P 道具的永久附魔（produceEffectType 127）效果直接执行，不发触发
    if items is None:
        items = [it['_id'] for it in ex.items]
    old = {it['_id']: it for it in ex.items}
    fired = {}   # 模板第 1 回合已发动的考试类道具次数（日志 cell 7）
    for c in save.get('userPlayLogList', []):
        if c.get('_cellType') == 7:
            fired[c.get('_triggerId')] = fired.get(c.get('_triggerId'), 0) + 1
    ex.items = []
    given = {i['_id']: i for i in items if isinstance(i, dict)}   # items 可给 dict：{'_id', '_reactionCount'（定制道具育成中触发次数）, ...}
    items = [i['_id'] if isinstance(i, dict) else i for i in items]
    for iid in items:
        it = dict(old.get(iid, {'_id': iid, '_fireCount': 0, '_reactionCount': 0, '_itemType': 1}))
        it.update(given.get(iid, {}))
        it['_fireCount'] = max(0, it.get('_fireCount', 0) - fired.get(iid, 0))
        ex.items.append(it)
    # 开考附魔重新编号（SetUpExam 最先创建）
    sc.create_count = 0
    for st in sc.effects:
        sc.create_count += 1
        st.uid = sc.create_count
    for it in ex.items:
        # 定制道具的永久附魔按育成中的触发次数（_reactionCount）叠加
        for en in permanent_enchants(master, it['_id']) * max(1, it.get('_reactionCount', 1)):
            ex.log('customize_item', item=it['_id'])
            ex.run([('effect', master.effect(e), Ctx(ex, item=it['_id'])) for e in en['produceExamEffectIds']])
    # ---- ExamStandBy：P 道具附魔（ItemList 顺序）→ 洗牌 → ExamStartExam
    for iid in items:
        got = item_enchants(master, iid)
        if got is None:
            continue
        for turn, count, en in got[1]:
            sc.TryAddTriggerEffectStatus(master.trigger(en['produceExamTriggerId']), [master.effect(i) for i in en['produceExamEffectIds']],
                                         1, turn if turn > 0 else -1, count if count > 0 else -1, -1, en['id'], source={'item': iid})
    ex.rng.shuffle(ex.zones['deck'])
    ex.run(ex.fire(ex.phase_enchants(PH['ExamStartExam'], base))
           + difftrig.card_enchant_cmds(ex, lambda c, tr, se: triggers.phase_trigger(ex, tr, PH['ExamStartExam'], base)))
    # ---- 第 1 回合开始
    ex.initial_deal = True
    ex.start_turn()
    ex.remaining_actions = 1
    if contest_stage is not None:
        from .contest_stage import attach
        attach(ex, contest_stage)
    return ex
