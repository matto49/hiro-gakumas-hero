"""卡牌定义：决策层（digital-hiro）看卡用的效果格式（op 列表），以及从主数据编译它的 MasterAdapter。

原在旧引擎 gakumas_sim（core.py 的定义格式与校验、master_adapter.py 的编译器、motivation_shield.py 的元气公式），
旧引擎删掉后搬到这里，内容不变。考试规则本身由 gakumas_exam 执行，不读这些定义。
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import struct

from .catalog import Catalog
from .customize import resolve, nondefault
from .errors import IllegalAction, Unsupported  # noqa: F401  旧代码从这里取异常
from copy import deepcopy
import math
import struct





_F32 = struct.Struct('<f')


def f32(value):
    return _F32.unpack(_F32.pack(value))[0]


def _ratio(value, permil):
    # Ceil branch in the prior GetRatioEffectIntValue/battle-bonus research.
    return math.ceil(f32(f32(value) * f32(f32(permil) / f32(1000))) - 0.0001)


_RATIO_CACHE = {}


def ratio(value, permil):
    # 提速（2026-11-04）：纯函数，整数参数按值缓存（体力消耗 / 倍率的组合很少），结果与 _ratio 相同
    if type(value) is int and type(permil) is int:
        key = (value, permil)
        r = _RATIO_CACHE.get(key)
        if r is None:
            if len(_RATIO_CACHE) > 200_000:
                _RATIO_CACHE.clear()
            r = _RATIO_CACHE[key] = _ratio(value, permil)
        return r
    return _ratio(value, permil)



EFFECT_FIELDS = {
    'score': {'op', 'value', 'focus_scale'},
    'shield': {'op', 'value', 'motivation_scale'},
    'shield_motivation_multiple': {'op','value','motivation_bonus_permil'},
    'motivation': {'op', 'value'}, 'focus': {'op', 'value'},
    'draw': {'op', 'value'}, 'extra_action': {'op', 'value'},
    'recover': {'op', 'value'},
    'shield_score': {'op', 'permil', 'consume_permil'},
    'resource_score': {'op','resource','permil','consume_permil'},
    'score_boost': {'op', 'permil', 'turns'},
    'timed_score_boost': {'op','permil','turns'},
    'end_turn_motivation': {'op','value'},
    'start_play_draw': {'op','value','turns'},  # 之后 turns 次（-1 永久）ターン開始後抽牌
    'status_change_effects': {'op','status','effects','turns'},  # 「N ターンの間、好印象 / 元気 / やる気 / 集中が増加した時、…」（ExamStatusChange 附魔）；turns=-1 永久
    'end_turn_effects': {'op','effects','turns'},  # 「以降 / N ターンの間、ターン終了時、…」附魔：每个回合末执行 effects，turns=-1 永久（厳選初星ティー）
    'shield_tick': {'op', 'permil', 'ticks'},
    'remove_trouble': {'op', 'value'},
    'next_turn_draw': {'op', 'value'},
    'mental_motivation': {'op', 'value'},
    'card_play_score': {'op', 'category', 'resource', 'permil'},
    'card_play_bonus': {'op', 'category', 'effects'},  # 出牌附魔多条正文（定制輝くキミへ）
    'impression': {'op','value'},
    'impression_growth': {'op','permil'},
    'impression_boost': {'op','permil','turns'},
    'block_restriction': {'op','turns'},
    'impression_score_boost': {'op','permil','turns'},
    'delayed_effects': {'op','delay','effects'},  # 「N ターン後、…」（ExamEffectTimer 通用形）：第 turn+delay 回合开始时执行  # 好印象強化 +permil‰（turns 回合，-1 永久）：回合末好印象得分加成  # 元気増加無効 turns 回合（-1 永久）：ハイテンション  # 好印象増加量増加 +permil‰（turns 回合，-1 为考试结束前）：星屑センセーション
    'extra_turn': {'op','value'},
    'permanent_score_boost': {'op','permil'},
    'impression_tick': {'op','permil','ticks'},
    'shield_gain_tick': {'op','value','motivation_scale','ticks'},
    'delayed_shield_score': {'op','permil','consume_permil','delay'},
    'conditional': {'op','requires','effects'},
    'stamina_down': {'op','permil','turns'},
    'stamina_up': {'op','permil','turns'},
    'anti_debuff': {'op','value'},
    'stamina_down_fix': {'op','value'},  # 消費体力削減 N（永久）：卡的体力费用在倍率之后再减 N  # 低下状態無効 N 层：每次施加低下状態（本引擎为消費体力増加）消耗 1 层并抵消
    'hp_loss': {'op','value'},
    'repeat_card': {'op','category','rarities','extra','uses','turns'},
    'remove_random': {'op','card_id'},
    'upgrade_hand': {'op'},
    'next_turn_upgrade_hand': {'op'},
    'fetch_tag': {'op','tag'},
    'discard_redraw': {'op'},
    'create_deck_random': {'op','definition_id'},
    'free_stamina': {'op','search','uses'},  # 之后 uses 次打出符合 search 的卡体力消耗为 0（勝利をつかめ！）
    'create_hand_pool': {'op','candidates'},  # 随机池生成到手牌（初星スペシャル青汁）：candidates=[{definition_id, plan, weight}]
    # Campus 広 extensions (feat_matto_campus_sim)
    'recover_permil': {'op','permil'},          # ExamStaminaRecoverMultiple: ceil(max_hp*permil)
    'hp_loss_permil': {'op','permil'},          # ExamStaminaReduce: floor(max_hp*permil), live-calibrated
    'conditional_tick': {'op','requires','effects','ticks'},  # turn-start enchant; ticks -1 = whole exam
    'play_interval': {'op','interval','effects'},  # ExamPlayCountInterval
}
# 回合末附魔正文允许的即时数值效果（不含抽牌 / 追加回合 / 生成牌：回合末手牌随后弃掉，追加回合时点未核对）
END_TURN_OPS = {'score','shield','motivation','impression','focus','recover','resource_score','shield_score','impression_growth',
                'hp_loss','recover_permil','hp_loss_permil','anti_debuff'}
# ExamStatusChange 附魔监听的状态：好印象（ExamReview）/ 元気（ExamBlock，带やる気加成的那类）/ やる気 / 集中
STATUS_CHANGE_KINDS = ('impression','shield','motivation','focus')
RARITIES = {'ProduceCardRarity_'+r for r in ('N','R','Sr','Ssr','Legend')}
CONDITIONS = {'hp_min', 'shield_min', 'focus_min', 'motivation_min', 'hand_trouble_min','impression_min',
              'hp_permil_max','hp_permil_min','trouble_not_lost_min',
              'turn_progress_min','impression_below','no_shield','shield_below','motivation_below','focus_below'}  # N ターン目以降（已过回合数 ≥ N）；好印象が N-1 以下（Not ReviewUp N）  # trouble_not_lost_min：除外以外的麻烦卡张数（がむしゃら）
LESSONS = ('vocal','dance','visual')
ATTRIBUTE_LESSON = {'Vo':'vocal','Da':'dance','Vi':'visual'}



def meets(values, requires):
    """Requirement check. hp_permil_* compare HP to max HP exactly (StaminaLess/UpMultiple, 以下/以上)."""
    for k, v in requires.items():
        if k == 'hp_permil_max':
            if values['hp_min']*1000 > v*values['max_hp']:return False
        elif k == 'hp_permil_min':
            if values['hp_min']*1000 < v*values['max_hp']:return False
        elif k == 'lesson':
            if values.get('lesson') != v:return False
        elif k == 'no_shield':
            if values.get('shield_min') is None or values['shield_min'] != 0:return False
        elif k.endswith('_below'):
            # X が v 未満（Not XxxUp v）：好印象が8以下 = impression_below 9
            current=values.get(k[:-len('_below')]+'_min')
            if current is None or current >= v:return False
        elif values.get(k) is None or values[k] < v:return False  # 取值方没提供的条件按不满足（预测里保守）
    return True
DEFINITION_FIELDS = {'name', 'role', 'once', 'initial', 'order', 'trouble',
                     'cost', 'requires', 'effects', 'provenance', 'end_turn_lost','category','rarity','search_tag'}


def validate_requirements(requirements):
    if not isinstance(requirements,dict) or set(requirements)-CONDITIONS-{'lesson'}:
        raise Unsupported('Unknown condition')
    if 'lesson' in requirements and requirements['lesson'] not in LESSONS:
        raise Unsupported('Unknown lesson condition')
    if any(type(v) is not int or v<0 for k,v in requirements.items() if k!='lesson'):
        raise Unsupported('Conditions require nonnegative integers')
    if any(requirements.get(k,0)>1000 for k in ('hp_permil_max','hp_permil_min')):
        raise Unsupported('HP permil condition above 1000')


def validate_effects(effects,depth=0):
    if not isinstance(effects,list) or depth>8:raise Unsupported('Invalid/deep effect list')
    for effect in effects:
        if not isinstance(effect,dict) or effect.get('op') not in EFFECT_FIELDS or set(effect)-EFFECT_FIELDS[effect['op']]:
            raise Unsupported('Unknown effect: '+repr(effect))
        if effect['op']=='conditional':
            if set(effect)!={'op','requires','effects'}:raise Unsupported('Conditional effect needs requirements and body')
            validate_requirements(effect['requires']);validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='conditional_tick':
            if set(effect)!=EFFECT_FIELDS['conditional_tick'] or type(effect['ticks']) is not int or not (effect['ticks']==-1 or effect['ticks']>=1):
                raise Unsupported('Conditional turn enchant needs requirements, body and ticks (-1 or positive)')
            validate_requirements(effect['requires']);validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='start_play_draw':
            if (set(effect)!=EFFECT_FIELDS['start_play_draw'] or type(effect['value']) is not int or effect['value']<0
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Start-play draw needs a nonnegative count and turns (-1 or positive)')
            continue
        if effect['op']=='status_change_effects':
            if (set(effect)!=EFFECT_FIELDS['status_change_effects'] or effect['status'] not in STATUS_CHANGE_KINDS
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)
                or not isinstance(effect['effects'],list) or not effect['effects']
                or any(not isinstance(e,dict) or e.get('op') not in END_TURN_OPS for e in effect['effects'])):
                raise Unsupported('Status-change enchant needs a status, turns (-1 or positive) and an immediate-effect body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='end_turn_effects':
            if (set(effect)!=EFFECT_FIELDS['end_turn_effects'] or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)
                or not isinstance(effect['effects'],list) or not effect['effects']
                or any(not isinstance(e,dict) or e.get('op') not in END_TURN_OPS for e in effect['effects'])):
                raise Unsupported('End-turn enchant needs turns (-1 or positive) and an immediate-effect body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='play_interval':
            if set(effect)!=EFFECT_FIELDS['play_interval'] or type(effect['interval']) is not int or effect['interval']<1:
                raise Unsupported('Play-count interval enchant needs positive interval and body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op'] in ('upgrade_hand','next_turn_upgrade_hand','discard_redraw'):continue
        if effect['op']=='free_stamina':
            search=effect.get('search')
            if (set(effect)!=EFFECT_FIELDS['free_stamina'] or type(effect['uses']) is not int or effect['uses']<1
                or not isinstance(search,dict) or set(search)!={'category','card_ids'} or search['category'] not in ('any','active','mental')
                or not isinstance(search['card_ids'],list) or not all(isinstance(c,str) and c for c in search['card_ids'])):
                raise Unsupported('Free-stamina play needs a search and positive uses')
            continue
        if effect['op']=='create_hand_pool':
            candidates=effect['candidates'] if set(effect)==EFFECT_FIELDS['create_hand_pool'] else None
            if (not isinstance(candidates,list) or not candidates
                or any(not isinstance(c,dict) or set(c)!={'definition_id','plan','weight'} or not isinstance(c['definition_id'],str)
                       or not c['definition_id'] or not isinstance(c['plan'],str) or type(c['weight']) is not int or c['weight']<1 for c in candidates)):
                raise Unsupported('Random-pool creation needs weighted candidate definitions')
            continue
        if effect['op']=='create_deck_random':
            if set(effect)!={'op','definition_id'} or not isinstance(effect['definition_id'],str) or not effect['definition_id']:
                raise Unsupported('Specified creation needs one definition ID')
            continue
        if effect['op']=='fetch_tag':
            if set(effect)!={'op','tag'} or not isinstance(effect['tag'],str) or not effect['tag'].strip():
                raise Unsupported('Tag retrieval needs one explicit nonblank tag')
            continue
        if effect['op']=='shield_motivation_multiple':
            if set(effect)!=EFFECT_FIELDS['shield_motivation_multiple']:
                raise Unsupported('Scaled motivation shield needs base and bonus')
            _motivation_shield(effect['value'],0,effect['motivation_bonus_permil'])
            continue
        if effect['op']=='card_play_bonus':
            if set(effect)!=EFFECT_FIELDS['card_play_bonus'] or effect['category'] not in ('mental','any') or not isinstance(effect['effects'],list) or not effect['effects']:
                raise Unsupported('Invalid card-play bonus enchant')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='card_play_score':
            if (set(effect)!=EFFECT_FIELDS['card_play_score'] or effect['category'] not in ('mental','any')
                or effect['resource'] not in ('impression','motivation') or type(effect['permil']) is not int or not 0<=effect['permil']<2**31):
                raise Unsupported('Invalid card-play score enchant')
            continue
        if effect['op']=='resource_score':
            if (set(effect)!=EFFECT_FIELDS['resource_score'] or effect['resource'] not in ('impression','motivation')
                or type(effect['permil']) is not int or not 0<=effect['permil']<2**31
                or type(effect['consume_permil']) is not int or not 0<=effect['consume_permil']<=1000):
                raise Unsupported('Invalid resource-dependent score')
            continue
        if effect['op']=='repeat_card':
            if set(effect)!=EFFECT_FIELDS['repeat_card'] or effect['category'] not in ('active','mental','any'):
                raise Unsupported('Repeat needs explicit category, rarities, extra, uses and turns')
            rarities=effect['rarities']
            if (not isinstance(rarities,list) or any(not isinstance(r,str) or r not in RARITIES for r in rarities)
                or len(set(rarities))!=len(rarities)):
                raise Unsupported('Invalid repeat rarity filter')
            if any(type(effect[k]) is not int or effect[k]<1 for k in ('extra','uses')):
                raise Unsupported('Repeat counts must be positive integers')
            if type(effect['turns']) is not int or effect['turns'] not in (-1,) and effect['turns']<1:
                raise Unsupported('Repeat duration must be positive or -1')
            continue
        if effect['op']=='block_restriction':
            if set(effect)!=EFFECT_FIELDS['block_restriction'] or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1):
                raise Unsupported('Block restriction needs turns (-1 or positive)')
            continue
        if effect['op']=='delayed_effects':
            if (set(effect)!=EFFECT_FIELDS['delayed_effects'] or type(effect['delay']) is not int or effect['delay']<1
                or not isinstance(effect['effects'],list) or not effect['effects']):
                raise Unsupported('Delayed effects need a positive delay and a body')
            validate_effects(effect['effects'],depth+1)
            continue
        if effect['op']=='impression_score_boost':
            if (set(effect)!=EFFECT_FIELDS['impression_score_boost'] or type(effect['permil']) is not int or not 0<effect['permil']<2**31
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Impression score boost needs positive permil and turns (-1 or positive)')
            continue
        if effect['op']=='impression_boost':
            if (set(effect)!=EFFECT_FIELDS['impression_boost'] or type(effect['permil']) is not int or not 0<effect['permil']<2**31
                or type(effect['turns']) is not int or not (effect['turns']==-1 or effect['turns']>=1)):
                raise Unsupported('Impression gain boost needs positive permil and turns (-1 or positive)')
            continue
        if effect['op']=='remove_random':
            if set(effect)!={'op','card_id'} or not isinstance(effect['card_id'],str) or not effect['card_id']:
                raise Unsupported('Random removal needs one explicit original card ID')
            continue
        for key,value in effect.items():
            if key!='op' and (type(value) is not int or value<0):raise Unsupported('Effects require nonnegative integer resolved coefficients')
        required={'shield_score':'permil','score_boost':'permil','timed_score_boost':'permil','shield_tick':'permil',
                  'impression_growth':'permil','impression_tick':'permil',
                  'permanent_score_boost':'permil','delayed_shield_score':'permil',
                  'stamina_down':'permil','stamina_up':'permil',
                  'recover_permil':'permil','hp_loss_permil':'permil'}.get(effect['op'],'value')
        if required not in effect:raise Unsupported('Missing effect coefficient: '+required)
        if effect.get('consume_permil',0)>1000:raise Unsupported('Invalid shield consumption')
        timer={'score_boost':'turns','timed_score_boost':'turns','start_play_draw':'turns','shield_tick':'ticks','impression_tick':'ticks','shield_gain_tick':'ticks','delayed_shield_score':'delay','stamina_down':'turns','stamina_up':'turns'}.get(effect['op'])
        if timer and effect.get(timer,0)<1:raise Unsupported('Positive explicit timer required')


def validate_definition(definition):
    if set(definition) - DEFINITION_FIELDS:
        raise Unsupported('Unknown card fields: ' + repr(set(definition) - DEFINITION_FIELDS))
    if 'effects' not in definition or 'provenance' not in definition:
        raise Unsupported('Every definition needs effects and provenance')
    if 'search_tag' in definition and not isinstance(definition['search_tag'],str):
        raise Unsupported('search_tag must be an explicitly known string')
    if definition.get('category','other') not in ('active','mental','trouble','other'):
        raise Unsupported('Unknown card category')
    if 'rarity' in definition and definition['rarity'] not in RARITIES:
        raise Unsupported('Unknown card rarity')
    if set(definition.get('cost', {})) - {'hp', 'shieldable','focus','motivation','impression'}:
        raise Unsupported('Unsupported cost channel')
    validate_requirements(definition.get('requires',{}))
    if type(definition.get('order', 0)) is not int or not -2**31 <= definition.get('order', 0) < 2**31:
        raise Unsupported('Card order must be int32')
    validate_effects(definition['effects'])
    for value in definition.get('cost', {}).values():
        if type(value) is not int or value < 0:
            raise Unsupported('Invalid cost')


def public_definition(definition):
    """Resolved gameplay rules, without raw master/debug provenance payloads."""
    rule={k:deepcopy(v) for k,v in definition.items() if k in DEFINITION_FIELDS and k!='provenance'}
    source=definition.get('provenance')
    rule['provenance']=dict(kind='public-rule-projection')
    if isinstance(source,dict) and 'card_id' in source:rule['provenance']['card_id']=source['card_id']
    return rule




def _motivation_shield(base,motivation,extra_permil):
    for name,value,minimum,maximum in [('base',base,1,2**31-1),
            ('motivation',motivation,0,2**31-1),('extra permil',extra_permil,0,1000000)]:
        if type(value) is not int or not minimum<=value<=maximum:
            raise Unsupported('Invalid scaled motivation shield '+name)
    scale=f32(f32(f32(extra_permil)/f32(1000))+f32(1))
    scaled=f32(f32(motivation)*scale)
    if not math.isfinite(scaled) or scaled>=2**31:
        raise Unsupported('Scaled motivation shield Int32 overflow not ported')
    result=base+math.ceil(scaled)
    if result>=2**31:raise Unsupported('Scaled motivation shield Int32 sum overflow not ported')
    return result




def suffix(value):return value.split('_',1)[-1]


DELAYABLE={'score','shield','motivation','impression','focus','recover','resource_score','shield_score','impression_growth','anti_debuff'}


class MasterAdapter:
    def __init__(self,catalog=None):self.catalog=catalog or Catalog()

    def effect(self,key,overrides=None,seen=()):
        if key in seen:raise Unsupported('Cyclic effect reference')
        row=(overrides or {}).get(key) or self.catalog.one('ProduceExamEffect',key)
        kind=suffix(row['effectType'])
        metadata={'id','effectType','effectValue1','effectValue2','effectCount','effectTurn',
                  'produceDescriptions','customizeProduceDescriptions','effectGroupIds'}
        if kind=='ExamStatusEnchant':metadata.add('produceExamStatusEnchantId')
        if kind=='ExamEffectTimer':metadata.add('chainProduceExamEffectId')
        if kind=='ExamCardSearchEffectPlayCountBuff':metadata|={'produceCardSearchId','pickRangeType'}
        if kind=='ExamCardMove':metadata|={'produceCardSearchId','movePositionType','pickRangeType','pickCountMin','pickCountMax'}
        if kind=='ExamCardUpgrade':metadata|={'produceCardSearchId','pickRangeType'}
        if kind=='ExamCardCreateId':metadata|={'targetProduceCardId','targetUpgradeCount','movePositionType','pickCountMin','pickCountMax'}
        if kind=='ExamCardCreateSearch':metadata|={'produceCardSearchId','movePositionType','pickRangeType','pickCountMin','pickCountMax'}
        if kind=='ExamSearchPlayCardStaminaConsumptionChange':metadata|={'produceCardSearchId','pickRangeType'}
        for name,value in row.items():
            if name not in metadata and nondefault(value):
                raise Unsupported('effect semantic field:'+name)
        value=row['effectValue1'];count=row['effectCount']
        if kind=='ExamHandGraveCountCardDraw':
            if value or count or row['effectValue2'] or row['effectTurn']:
                raise Unsupported('Only whole-hand zero-filter discard/redraw is ported')
            return [dict(op='discard_redraw')]
        if kind=='ExamCardCreateId':
            if (value or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
                or row['movePositionType']!='ProduceCardMovePositionType_DeckRandom'):
                raise Unsupported('Only fixed-one specified DeckRandom creation is ported')
            target=self.catalog.card(row['targetProduceCardId'],row['targetUpgradeCount'])
            return [dict(op='create_deck_random',definition_id=target['id']+'@'+str(target['upgradeCount']))]
        if kind=='ExamCardCreateSearch':return self.create_hand_pool(row)
        if kind=='ExamSearchPlayCardStaminaConsumptionChange':
            # 勝利をつかめ！（p_card-00-sup-3_158）「次に使用したスキルカードの消費体力を0にする（1回）」：
            # 之后 N 次打出符合检索的卡，体力消耗为 0。存档状态类 SearchPlayCardStaminaConsumptionChangeStatusEffect
            # （3.4.1 元数据字段 _count / _searchId / _fromExamEffectId）。
            if value or row['effectValue2'] or count<1 or row['effectTurn']!=-1 or row['pickRangeType']!='ProducePickRangeType_All':
                raise Unsupported('Free-stamina play contract')
            return [dict(op='free_stamina',search=self.free_stamina_search(row['produceCardSearchId']),uses=count)]
        if kind=='ExamCardUpgrade':
            if value or count or row['effectValue2'] or row['effectTurn'] or row['pickRangeType']!='ProducePickRangeType_All':
                raise Unsupported('Only unparameterized all-hand exam upgrade is ported')
            search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
            if (search['cardPositionType']!='ProduceCardPositionType_Hand'
                or any(nondefault(v) for k,v in search.items() if k not in {'id','cardPositionType','produceDescriptions'})):
                raise Unsupported('Exam upgrade needs a plain Hand search')
            return [dict(op='upgrade_hand')]
        if kind=='ExamCardMove':
            if (not value and not count and not row['effectValue2'] and not row['effectTurn']
                and row['pickCountMin']==0 and row['pickCountMax']==0
                and row['movePositionType']=='ProduceCardMovePositionType_Hand'
                and row['pickRangeType']=='ProducePickRangeType_All'):
                search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
                allowed={'id','produceDescriptions','cardPositionType','cardSearchTag','limitCount'}
                if (search['cardPositionType']!='ProduceCardPositionType_DeckGrave' or search['cardSearchTag']!='idol-unique'
                    or search['limitCount']!=1 or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                    raise Unsupported('Hand retrieval needs exact order-zero one-card DeckGrave idol-unique search')
                setting=self.catalog.one('ExamSetting','p_exam_setting-1')
                if setting['fixMoveCardShuffleDeckEnable'] is not True:
                    raise Unsupported('Legacy MoveCard end reshuffle not ported')
                return [dict(op='fetch_tag',tag=search['cardSearchTag'])]
            if (value or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
                or row['movePositionType']!='ProduceCardMovePositionType_Lost' or row['pickRangeType']!='ProducePickRangeType_Random'):
                raise Unsupported('Only fixed-one random removal to Lost is ported')
            search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
            allowed={'id','produceCardIds','cardPositionType','produceDescriptions'}
            if (search['cardPositionType']!='ProduceCardPositionType_DeckGrave' or len(search['produceCardIds'])!=1
                or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                raise Unsupported('Random removal needs DeckGrave and one card ID filter')
            return [dict(op='remove_random',card_id=search['produceCardIds'][0])]
        if kind=='ExamCardSearchEffectPlayCountBuff':
            if (value<1 or count<1 or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1
                or row['pickRangeType']!='ProducePickRangeType_All'):
                raise Unsupported('Repeat effect count/duration/range')
            category,rarities=self.repeat_search(row['produceCardSearchId'])
            return [dict(op='repeat_card',category=category,rarities=rarities,extra=value,uses=count,turns=row['effectTurn'])]
        if kind=='ExamStatusEnchant':
            if value or row['effectValue2'] or count:raise Unsupported('Enchant coefficient/count')
            enchant=self.catalog.one('ProduceExamStatusEnchant',row['produceExamStatusEnchantId'])
            if any(nondefault(v) for k,v in enchant.items() if k not in {'id','assetId','produceExamTriggerId','produceExamEffectIds','produceDescriptions'}):
                raise Unsupported('Enchant fields')
            trigger=self.catalog.one('ProduceExamTrigger',enchant['produceExamTriggerId'])
            conditional=self.turn_enchant(row,enchant,trigger,overrides,seen+(key,))
            if conditional is not None:return conditional
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamPlayCountInterval']:
                plain={'id','phaseTypes','phaseValues','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
                if any(nondefault(v) for k,v in trigger.items() if k not in plain) or len(trigger['phaseValues'])!=1 or trigger['phaseValues'][0]<1:
                    raise Unsupported('Play-count interval trigger fields')
                if row['effectTurn']!=-1:raise Unsupported('Play-count interval duration not ported')
                body=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen+(key,))]
                return [dict(op='play_interval',interval=trigger['phaseValues'][0],effects=body)]
            multi_play=len(enchant['produceExamEffectIds'])>1 and trigger['phaseTypes']==['ProduceExamPhaseType_ExamCardPlay']
            if len(enchant['produceExamEffectIds'])!=1 and not multi_play:raise Unsupported('Multiple enchant effects not ported')
            leaf=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen+(key,))]
            plain={'id','phaseTypes','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
            if trigger['phaseTypes'] in (['ProduceExamPhaseType_ExamEndTurn'],['ProduceExamPhaseType_StartPlay']):
                if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional drink phase enchant')
                duration=row['effectTurn']
                if duration!=-1 and duration<1:raise Unsupported('End/play-start enchant duration not ported')
                if trigger['phaseTypes']==['ProduceExamPhaseType_ExamEndTurn']:
                    # 回合末附魔：永久 / N 回合，正文为即时数值效果（可多条，按 master 顺序）。厳選初星ティー = 永久 好印象+1
                    if duration==-1 and len(leaf)==1 and leaf[0]['op']=='motivation':
                        return [dict(op='end_turn_motivation',value=leaf[0]['value'])]
                    if leaf and all(e['op'] in END_TURN_OPS for e in leaf):
                        return [dict(op='end_turn_effects',effects=leaf,turns=duration)]
                    raise Unsupported('End-turn enchant payload not ported')
                if len(leaf)==1 and leaf[0]['op']=='draw':
                    return [dict(op='start_play_draw',value=leaf[0]['value'],turns=duration)]
                raise Unsupported('Play-start enchant payload not ported')
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamStatusChange']:
                # 「N ターンの間、好印象が増加した時、…」（びしっとキメ顔）：无条件的状态增加触发器
                kinds={'ProduceExamEffectType_ExamReview':'impression','ProduceExamEffectType_ExamBlock':'shield',
                       'ProduceExamEffectType_ExamCardPlayAggressive':'motivation','ProduceExamEffectType_ExamLessonBuff':'focus'}
                if (any(nondefault(v) for k,v in trigger.items() if k not in plain|{'effectTypes'}) or len(trigger['effectTypes'])!=1
                    or trigger['effectTypes'][0] not in kinds):
                    raise Unsupported('Conditional status-change enchant')
                duration=row['effectTurn']
                if (duration!=-1 and duration<1) or not leaf or any(e['op'] not in END_TURN_OPS for e in leaf):
                    raise Unsupported('Status-change enchant payload/duration not ported')
                return [dict(op='status_change_effects',status=kinds[trigger['effectTypes'][0]],effects=leaf,turns=duration)]
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamStartTurn']:
                if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional turn enchant')
                if row['effectTurn']<1 or len(leaf)!=1:
                    raise Unsupported('Turn enchant type/duration not ported')
                if leaf[0]['op']=='shield_score' and not leaf[0].get('consume_permil',0):
                    return [dict(op='shield_tick',permil=leaf[0]['permil'],ticks=row['effectTurn'])]
                if leaf[0]['op']=='impression_growth':return [dict(op='impression_tick',permil=leaf[0]['permil'],ticks=row['effectTurn'])]
                if leaf[0]['op']=='shield':return [dict(op='shield_gain_tick',value=leaf[0]['value'],motivation_scale=leaf[0].get('motivation_scale',0),ticks=row['effectTurn'])]
                raise Unsupported('Turn enchant payload not ported')
            if trigger['phaseTypes']==['ProduceExamPhaseType_ExamCardPlay']:
                permitted=plain|{'produceCardSearchId','lowerSearchCount'}
                if any(nondefault(v) for k,v in trigger.items() if k not in permitted) or trigger['lowerSearchCount']!=1:
                    raise Unsupported('Card-play enchant filter')
                search=self.catalog.one('ProduceCardSearch',trigger['produceCardSearchId'])
                allowed={'id','cardCategories','cardPositionType','produceDescriptions'}
                # 检索条件：打出中的卡（Playing），类别为メンタル或不限（輝くキミへ：p_card_search-playing）
                if (search['cardCategories'] not in (['ProduceCardCategory_MentalSkill'],[]) or search['cardPositionType']!='ProduceCardPositionType_Playing'
                    or any(nondefault(v) for k,v in search.items() if k not in allowed)):
                    raise Unsupported('Card-play enchant search')
                mental=search['cardCategories']==['ProduceCardCategory_MentalSkill']
                if row['effectTurn']!=-1:raise Unsupported('Card-play enchant type/duration')
                if len(leaf)>1:
                    # 定制后的輝くキミへ（p_card_custom-100 → enchant enc03）：「スキルカード使用時、好印象の50%分パラメータ上昇・好印象+1」
                    # 多条正文按 master 顺序在同一时点（正文之前）执行；只收不消耗的按资源得分与直接加好印象
                    if any(not (e['op']=='resource_score' and not e['consume_permil'] or e['op']=='impression') for e in leaf):
                        raise Unsupported('Card-play enchant multi-effect payload not ported')
                    return [dict(op='card_play_bonus',category='mental' if mental else 'any',effects=leaf)]
                if len(leaf)!=1:raise Unsupported('Card-play enchant type/duration')
                if mental and leaf[0]['op']=='motivation':return [dict(op='mental_motivation',value=leaf[0]['value'])]
                if leaf[0]['op']=='resource_score' and not leaf[0]['consume_permil']:
                    # 輝くキミへ：「以降、スキルカード使用時、好印象の30%分パラメータ上昇」
                    return [dict(op='card_play_score',category='mental' if mental else 'any',resource=leaf[0]['resource'],permil=leaf[0]['permil'])]
                raise Unsupported('Card-play enchant type/duration')
            raise Unsupported('Enchant phase not ported')
        if kind=='ExamEffectTimer':
            if value<1 or count!=1 or row['effectValue2'] or row['effectTurn']:raise Unsupported('Effect timer delay not ported')
            leaf=self.effect(row['chainProduceExamEffectId'],overrides,seen+(key,))
            if len(leaf)==1 and leaf[0]['op']=='draw' and value==1:return [dict(op='next_turn_draw',value=leaf[0]['value'])]
            if len(leaf)==1 and leaf[0]['op']=='upgrade_hand' and value==1:return [dict(op='next_turn_upgrade_hand')]
            if len(leaf)==1 and leaf[0]['op']=='shield_score':
                return [dict(op='delayed_shield_score',delay=value,permil=leaf[0]['permil'],consume_permil=leaf[0].get('consume_permil',0))]
            # 通用形：N 回合后回合开始时执行（与 next_turn_draw / delayed_shield_score 同一 reservations 时点）。
            # 只收不依赖手牌 / 行动数的即时数值效果；存档里尚未到期的计时器如何还原未核对。
            if leaf and all(e['op'] in DELAYABLE for e in leaf):return [dict(op='delayed_effects',delay=value,effects=leaf)]
            raise Unsupported('Effect timer payload not ported')
        if kind=='ExamExtraTurn':
            if value or count or row['effectTurn'] or row['effectValue2']:raise Unsupported('Extra turn contract')
            return [dict(op='extra_turn',value=1)]
        if kind=='ExamLessonValueMultiple' and row['effectTurn']==-1:
            if value<0 or count or row['effectValue2']:raise Unsupported('Permanent score boost contract')
            return [dict(op='permanent_score_boost',permil=value)]
        if kind=='ExamLessonValueMultiple' and row['effectTurn']>0:
            if value<0 or count or row['effectValue2']:raise Unsupported('Timed score boost contract')
            return [dict(op='timed_score_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamReviewMultiple':
            # 好印象強化 +N‰（effectTurn 回合；-1 永久）：回合末好印象得分 × (1 + Σ/1000)。びしっとキメ顔、究極スマイル 等
            if value<1 or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Impression score boost contract')
            return [dict(op='impression_score_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamBlockRestriction':
            # 元気増加無効 N ターン（ハイテンション p_card-00-men-2_014 等）：期间 ExamBlock 类元気增加为 0
            # （3.4.1 ExamEffectUtility.CalculateAddBlock IsBlockRestriction → 0）
            if value or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Block restriction contract')
            return [dict(op='block_restriction',turns=row['effectTurn'])]
        if kind=='ExamReviewAdditive':
            # 好印象増加量増加 +N‰（effectTurn 回合；-1 为考试结束前）。星屑センセーション（p_card-02-men-3_004）等
            if value<1 or count or row['effectValue2'] or row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Impression gain boost contract')
            return [dict(op='impression_boost',permil=value,turns=row['effectTurn'])]
        if kind=='ExamReviewValueMultiple':
            if value<0 or count or row['effectTurn'] or row['effectValue2']:raise Unsupported('Impression multiplier contract')
            return [dict(op='impression_growth',permil=value)]
        if kind in ('ExamStaminaConsumptionDown','ExamStaminaConsumptionAdd'):
            if value or count or row['effectValue2'] or row['effectTurn']<1:raise Unsupported('Timed stamina modifier contract')
            setting=self.catalog.one('ExamSetting','p_exam_setting-1')
            if kind=='ExamStaminaConsumptionDown':
                if setting['examStaminaConsumptionDownPermil']!=500:raise Unsupported('Unverified stamina reduction setting')
                return [dict(op='stamina_down',permil=500,turns=row['effectTurn'])]
            if setting['examStaminaConsumptionAddPermil']!=1000:raise Unsupported('Unverified stamina increase setting')
            return [dict(op='stamina_up',permil=2000,turns=row['effectTurn'])]
        if kind=='ExamBlockAddMultipleAggressive':
            if value<1 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000000:
                raise Unsupported('Scaled motivation shield coefficient/count')
            return [dict(op='shield_motivation_multiple',value=value,motivation_bonus_permil=row['effectValue2'])]
        if kind=='ExamLessonDependBlock':
            if value<0 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000:
                raise Unsupported('shield score coefficient/count')
            return [dict(op='shield_score',permil=value,consume_permil=row['effectValue2'])]
        if kind in ('ExamLessonDependExamReview','ExamLessonDependExamCardPlayAggressive'):
            if value<0 or row['effectTurn'] or count!=1 or not 0<=row['effectValue2']<=1000:
                raise Unsupported('Resource-dependent score coefficient/count')
            return [dict(op='resource_score',resource='impression' if kind=='ExamLessonDependExamReview' else 'motivation',
                         permil=value,consume_permil=row['effectValue2'])]
        if kind in ('ExamStaminaRecoverMultiple','ExamStaminaReduce'):
            if not 0<value<=1000 or row['effectTurn'] or row['effectValue2'] or count:raise Unsupported('HP-percent effect fields')
            return [dict(op='recover_permil' if kind=='ExamStaminaRecoverMultiple' else 'hp_loss_permil',permil=value)]
        if kind=='ExamStaminaConsumptionDownFix':
            if value<1 or row['effectTurn']!=-1 or row['effectValue2'] or count:raise Unsupported('Only permanent stamina fix reduction is ported')
            return [dict(op='stamina_down_fix',value=value)]
        if kind=='ExamAntiDebuff':
            if value or row['effectTurn'] or row['effectValue2'] or count<1:raise Unsupported('Anti-debuff contract')
            return [dict(op='anti_debuff',value=count)]
        if kind=='ExamPlayableValueAdd':
            if value or row['effectTurn'] or row['effectValue2'] or count<1:raise Unsupported('Action-add field contract')
            return [dict(op='extra_action',value=count)]
        if value<0 or row['effectTurn'] or row['effectValue2']:
            raise Unsupported('effect timer/secondary coefficient:'+kind)
        ops={'ExamLesson':'score','ExamLessonFix':'score','ExamBlock':'shield','ExamBlockFix':'shield',
             'ExamLessonBuff':'focus','ExamCardPlayAggressive':'motivation','ExamCardDraw':'draw',
             'ExamPlayableValueAdd':'extra_action','ExamStaminaRecoverFix':'recover','ExamReview':'impression',
             'ExamStaminaReduceFix':'hp_loss'}
        if kind not in ops:raise Unsupported('effect type:'+kind)
        if kind in ('ExamLesson','ExamLessonFix'):
            if not 1<=count<=20:raise Unsupported('score hit count:'+str(count))
        elif count not in (0,1):raise Unsupported('effect count:'+kind)
        effect=dict(op=ops[kind],value=value)
        if kind=='ExamLesson':effect['focus_scale']=1
        if kind=='ExamBlock':effect['motivation_scale']=1
        return [deepcopy(effect) for _ in range(count if kind in ('ExamLesson','ExamLessonFix') else 1)]

    def repeat_search(self,search_id):
        """「次に使用した…スキルカードの効果をもう1回発動」的检索条件 → (category, rarities)。存档还原 PlayCountBuffStatusEffect 的 _searchId 也用它。"""
        search=self.catalog.one('ProduceCardSearch',search_id)
        allowed={'id','cardCategories','cardRarities','cardPositionType','produceDescriptions'}
        if (search['cardPositionType']!='ProduceCardPositionType_Playing'
            or any(nondefault(v) for k,v in search.items() if k not in allowed)):
            raise Unsupported('Repeat search needs Playing with only category/rarity filters')
        categories=search['cardCategories']
        if categories not in ([],['ProduceCardCategory_ActiveSkill'],['ProduceCardCategory_MentalSkill']):
            raise Unsupported('Repeat category combination not ported')
        category='any' if not categories else ('active' if categories[0].endswith('_ActiveSkill') else 'mental')
        return category,list(search['cardRarities'])

    def free_stamina_search(self,search_id):
        """消費体力を0にする 的检索条件：DeckAll（任何位置）+ 可选类别 / 指定卡 ID。存档还原也用它把 _searchId 编译成条件。"""
        search=self.catalog.one('ProduceCardSearch',search_id)
        allowed={'id','cardCategories','produceCardIds','cardPositionType','produceDescriptions'}
        if search['cardPositionType']!='ProduceCardPositionType_DeckAll' or any(nondefault(v) for k,v in search.items() if k not in allowed):
            raise Unsupported('Free-stamina search shape not ported')
        categories={(): 'any',('ProduceCardCategory_ActiveSkill',):'active',('ProduceCardCategory_MentalSkill',):'mental'}
        if tuple(search['cardCategories']) not in categories:raise Unsupported('Free-stamina category combination')
        return dict(category=categories[tuple(search['cardCategories'])],card_ids=list(search['produceCardIds']))

    def create_hand_pool(self,row):
        """「ランダムな強化済みスキルカード（SSR）を、手札に生成」（初星スペシャル青汁 pdrink_00-3-005 等）。

        只移植固定生成 1 张、从 RandomPool 随机取 1 张进手牌的形状。候选来自 ProduceCardPool（按 ratio 份展开：名字里的 RandomPool 实际读 ProduceCardPool）。
        原生 ExamEffectUtility.GetSearchCardList 在取出 RandomPool 候选后逐张检查计划：卡的 PlanType 为 Common，
        或等于考试 ExamParameterModel.PlanType 才保留（3.4.1 客户端）。计划在这里不知道，
        编译结果保留全部候选与各自计划，由组装对局文档时按偶像计划筛选（card_mutations.admit_pool_candidates）。
        假设：计划筛选在 limitCount=1 截断之前（否则可能生成 0 张，与实机「一定得到一张」不符）。"""
        value,count=row['effectValue1'],row['effectCount']
        if (value!=1 or count or row['effectValue2'] or row['effectTurn'] or row['pickCountMin']!=1 or row['pickCountMax']!=1
            or row['movePositionType']!='ProduceCardMovePositionType_Hand' or row['pickRangeType']!='ProducePickRangeType_Random'):
            raise Unsupported('Only fixed-one random-pool creation to Hand is ported')
        search=self.catalog.one('ProduceCardSearch',row['produceCardSearchId'])
        allowed={'id','orderType','cardPositionType','produceCardRandomPoolId','produceCardPoolId','limitCount','produceDescriptions'}
        if (search['cardPositionType']!='ProduceCardPositionType_RandomPool' or search['orderType']!='ProduceCardOrderType_Random'
            or search['limitCount']!=1 or not search['produceCardPoolId'] or search['produceCardRandomPoolId']!=search['produceCardPoolId']
            or any(nondefault(v) for k,v in search.items() if k not in allowed)):
            raise Unsupported('Random-pool creation search shape not ported')
        pool=self.catalog.one('ProduceCardPool',search['produceCardPoolId'])
        if set(pool)!={'id','produceCardRatios'} or not pool['produceCardRatios']:raise Unsupported('Unsupported ProduceCardPool shape')
        candidates=[]
        for entry in pool['produceCardRatios']:
            if set(entry)!={'id','upgradeCount','ratio'} or type(entry['ratio']) is not int or entry['ratio']<1:
                raise Unsupported('Invalid random-pool row')
            card=self.catalog.card(entry['id'],entry['upgradeCount'])
            candidates.append(dict(definition_id=entry['id']+'@'+str(entry['upgradeCount']),plan=card['planType'],weight=entry['ratio']))
        return [dict(op='create_hand_pool',candidates=candidates)]

    HP_FIELDS={'ProduceExamFieldStatusType_StaminaLessMultiple':'hp_permil_max',
               'ProduceExamFieldStatusType_StaminaUpMultiple':'hp_permil_min'}

    STATUS_FIELDS={'ProduceExamFieldStatusType_ReviewUp':'impression','ProduceExamFieldStatusType_BlockUp':'shield',
                   'ProduceExamFieldStatusType_CardPlayAggressiveUp':'motivation','ProduceExamFieldStatusType_LessonBuffUp':'focus'}

    def field_requirements(self,trigger,allow_status=False):
        """HP-ratio trigger fields → requires.

        allow_status（道具钩子用）：好印象 / 元気 / やる気 / 集中 が N 以上（XxxUp），以及 Not 检查（「元気が30以下」= Not BlockUp 31）。"""
        types,values=trigger['fieldStatusTypes'],trigger['fieldStatusValues']
        checks=trigger.get('fieldStatusCheckTypes') or []
        if len(types)!=len(values):raise Unsupported('Trigger field status not ported')
        if checks and (not allow_status or len(checks)!=len(types) or any(c!='ProduceExamTriggerCheckType_Not' for c in checks)):
            raise Unsupported('Trigger field check not ported')
        result={}
        for t,v in zip(types,values):
            if t in self.HP_FIELDS and not checks:
                if type(v) is not int or not 0<v<=1000:raise Unsupported('HP-ratio trigger value')
                result[self.HP_FIELDS[t]]=v
            elif allow_status and t in self.STATUS_FIELDS:
                if type(v) is not int or v<0:raise Unsupported('Status trigger value')
                result[self.STATUS_FIELDS[t]+('_below' if checks else '_min')]=v
            else:raise Unsupported('Trigger field status not ported')
        return result

    def turn_enchant(self,row,enchant,trigger,overrides,seen):
        """Turn-start enchant with HP-ratio condition and/or several payloads (エウレカ！ etc.).

        Plain single-payload ExamStartTurn enchants keep their earlier specialised ops; this only takes
        the shapes that were previously rejected."""
        if trigger['phaseTypes']!=['ProduceExamPhaseType_ExamStartTurn']:return None
        if not trigger['fieldStatusTypes'] and len(enchant['produceExamEffectIds'])==1 and row['effectTurn']!=-1:return None
        plain={'id','phaseTypes','fieldStatusTypes','fieldStatusValues','produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions'}
        if any(nondefault(v) for k,v in trigger.items() if k not in plain):raise Unsupported('Conditional turn enchant fields')
        if row['effectTurn']==0 or row['effectTurn']<-1:raise Unsupported('Conditional turn enchant duration')
        body=[e for k in enchant['produceExamEffectIds'] for e in self.effect(k,overrides,seen)]
        allowed={'score','shield','shield_score','recover','recover_permil','hp_loss','hp_loss_permil','motivation','impression','draw'}
        if any(e['op'] not in allowed or e['op']=='shield_score' and e.get('consume_permil') for e in body):
            raise Unsupported('Conditional turn enchant payload not ported')
        return [dict(op='conditional_tick',requires=self.field_requirements(trigger),effects=body,ticks=row['effectTurn'])]

    def requirement(self,key,play_effect=False):
        if not key:return {}
        row=self.catalog.one('ProduceExamTrigger',key)
        permitted={'id','phaseTypes','fieldStatusTypes','fieldStatusValues','lessonType',
                   'produceDescriptions','playProduceDescriptions','playEffectProduceDescriptions','fieldStatusProduceCardSearchIds',
                   'fieldStatusCheckTypes','phaseValues'}
        if row.get('phaseValues') not in (None,[],[0]):raise Unsupported('card use trigger compound:'+key)
        negated=row.get('fieldStatusCheckTypes') or []
        if negated and negated!=['ProduceExamTriggerCheckType_Not']:raise Unsupported('card use trigger compound:'+key)
        if row.get('fieldStatusProduceCardSearchIds') and row['fieldStatusProduceCardSearchIds']!=['p_card_search-trouble-not_lost']:
            raise Unsupported('card use trigger compound:'+key)
        phases=[['ProduceExamPhaseType_None']]
        if play_effect:phases.append(['ProduceExamPhaseType_ExamCardPlay'])
        elif row['phaseTypes']==['ProduceExamPhaseType_ExamStartTurn'] and row['fieldStatusTypes'] in (
                ['ProduceExamFieldStatusType_StaminaUpMultiple'],['ProduceExamFieldStatusType_StaminaLessMultiple'],
                ['ProduceExamFieldStatusType_TurnProgressUp'],['ProduceExamFieldStatusType_NoBlock']):
            # 「…の場合、使用可」（ご指導ご鞭撻、ストレッチ談議、おアツイ視线）的使用条件 trigger 带 ExamStartTurn 阶段；
            # 假设：使用条件按出牌时的当前状态判定，阶段只是 trigger 复用的元数据（未用实机核对）
            phases.append(['ProduceExamPhaseType_ExamStartTurn'])
        if row['phaseTypes'] not in phases or suffix(row['lessonType'])!='Unknown':
            raise Unsupported('card use trigger phase:'+key)
        if any(nondefault(value) for name,value in row.items() if name not in permitted):
            raise Unsupported('card use trigger compound:'+key)
        mapping={'ProduceExamFieldStatusType_LessonBuffUp':'focus_min',
                 'ProduceExamFieldStatusType_CardPlayAggressiveUp':'motivation_min',
                 'ProduceExamFieldStatusType_BlockUp':'shield_min','ProduceExamFieldStatusType_ReviewUp':'impression_min'}
        if row['fieldStatusTypes']==['ProduceExamFieldStatusType_CardSearchCountUp'] and len(row['fieldStatusValues'])==1:
            # 除外以外にあるトラブルカードが N 枚以上（がむしゃら）；检索条件已在上面限定为 p_card_search-trouble-not_lost
            if row.get('fieldStatusProduceCardSearchIds')!=['p_card_search-trouble-not_lost']:raise Unsupported('card use trigger status:'+key)
            return {'trouble_not_lost_min':row['fieldStatusValues'][0]}
        if row['fieldStatusTypes']==['ProduceExamFieldStatusType_NoBlock'] and not row['fieldStatusValues'] and not negated:
            return {'no_shield':0}  # おアツイ視線「元気が0の場合」
        if len(row['fieldStatusTypes'])!=1 or len(row['fieldStatusValues'])!=1:raise Unsupported('card use trigger status:'+key)
        kind,value=row['fieldStatusTypes'][0],row['fieldStatusValues'][0]
        if negated:
            # ロマンチック「好印象が8以下の場合」= Not(ReviewUp 9)
            if kind!='ProduceExamFieldStatusType_ReviewUp':raise Unsupported('card use trigger status:'+key)
            return {'impression_below':value}
        if kind in self.HP_FIELDS:
            # 「体力が50%以上の場合」（ファーストステップ、執念キャッチャー）：按出牌前（付费前）的体力判定，与道具 HP 条件同口径
            if type(value) is not int or not 0<value<=1000:raise Unsupported('HP-ratio trigger value')
            return {self.HP_FIELDS[kind]:value}
        if kind=='ProduceExamFieldStatusType_TurnProgressUp':return {'turn_progress_min':value}
        if kind=='ProduceExamFieldStatusType_NoBlock':return {'no_shield':0}  # おアツイ視線「元気が0の場合」  # 頭上に注意！「3ターン目以降」= 已过 2 回合
        if kind not in mapping:
            raise Unsupported('card use trigger status:'+key)
        return {mapping[row['fieldStatusTypes'][0]]:row['fieldStatusValues'][0]}

    @staticmethod
    def cost(row):
        cost=dict(hp=row['forceStamina'],shieldable=row['stamina'])
        status_cost={'ExamLessonBuff':'focus','ExamCardPlayAggressive':'motivation','ExamReview':'impression'}
        kind=suffix(row['costType'])
        if kind in status_cost:cost[status_cost[kind]]=row['costValue']
        elif kind!='Unknown' or row['costValue']:raise Unsupported('status resource cost:'+kind)
        return cost

    def card(self,card_id,upgrade=0,customizations=None):
        row,overrides,custom=resolve(self.catalog,card_id,upgrade,customizations)
        # Card.order is a 64-bit library display key, NOT ExamCardPool.order.
        # It must not enter deterministic deck sorting.
        unsupported_flags=['isRestrict','produceCardStatusEnchantId',
                           'moveProduceExamEffectIds','moveProduceExamTriggerIds']
        for key in unsupported_flags:
            if row.get(key):raise Unsupported('card semantic field:'+key)
        if suffix(row['moveEffectTriggerType'])!='Unknown':raise Unsupported('card move trigger')
        requirements=self.requirement(row['playProduceExamTriggerId'])
        cost=self.cost(row)
        destination=suffix(row['playMovePositionType'])
        if destination not in ('Lost','Grave'):raise Unsupported('card move destination:'+destination)
        effects=[]
        for entry in row['playEffects']:
            if entry['isOncePlayEffect']:raise Unsupported('once-only sub-effect')
            if set(entry)-{'produceExamTriggerId','produceExamEffectId','isOncePlayEffect','hideIcon'}:
                raise Unsupported('unknown play effect fields')
            body=self.effect(entry['produceExamEffectId'],overrides)
            condition=self.requirement(entry['produceExamTriggerId'],play_effect=True)
            if condition:effects.append(dict(op='conditional',requires=condition,effects=body))
            else:effects.extend(body)
        role='bridge'
        if any(e['op'] in ('draw','extra_action','extra_turn') for e in effects):role='engine'
        elif any(e['op'] in ('focus','motivation','shield','impression','permanent_score_boost') for e in effects):role='builder'
        elif any(e['op'] in ('score','shield_score','resource_score') for e in effects):role='finisher'
        if suffix(row['category'])=='Trouble':role='trouble'
        definition=dict(name=row['name'],role=role,once=destination=='Lost',initial=row['isInitial'],order=0,
                        category={'ActiveSkill':'active','MentalSkill':'mental','Trouble':'trouble'}.get(suffix(row['category']),'other'),
                        rarity=row['rarity'],search_tag=row['searchTag'],
                        trouble=role=='trouble',end_turn_lost=row['isEndTurnLost'],
                        cost=cost,requires=requirements,effects=effects,
                        provenance=dict(kind='master',commit=self.catalog.info['commit'],table='ProduceCard',
                                        card_id=card_id,upgrade=upgrade,customize_ids=[r['id'] for r in custom['customizations']],
                                        customization=custom))
        validate_definition(definition)
        return definition

    def drink(self,drink_id):
        row=self.catalog.one('ProduceDrink',drink_id);effects=[]
        for key in row['produceDrinkEffectIds']:
            entry=self.catalog.one('ProduceDrinkEffect',key)
            if entry['produceEffectId']:raise Unsupported('out-of-battle drink effect')
            effects.extend(self.effect(entry['produceExamEffectId']))
        definition=dict(name=row['name'],role=('fetch' if any(e['op']=='fetch_tag' for e in effects) else 'draw' if any(e['op']=='draw' for e in effects) else 'ordinary'),effects=effects,
                        provenance=dict(kind='master',commit=self.catalog.info['commit'],table='ProduceDrink',drink_id=drink_id))
        validate_definition(definition)
        return definition

