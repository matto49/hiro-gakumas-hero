"""HIF-only objectives, master-rule evidence and event-to-skill dispatch.

Snapshot NPC intervals are envelopes, NOT a probability distribution. Calendar
routing does not settle event rewards or implement a full growth simulator.
"""
from copy import deepcopy
from gakumas_exam.catalog import Catalog
from gakumas_exam.errors import Unsupported
from .schedule import HIFSchedule


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise Unsupported(name+' must be an integer >= '+str(minimum))
    return value


def settled_score(score, round_number):
    if not isinstance(score, dict) or set(score) != {'value', 'kind'}:
        raise Unsupported('Score requires exactly value and kind (raw/settled)')
    value=integer(score['value'], 'score')
    if type(round_number) is not int or round_number not in (1,2):raise Unsupported('HIF round must be 1 or 2')
    if score['kind']=='settled':return value
    if score['kind']!='raw':raise Unsupported('Unknown score kind')
    return value*6//5 if round_number==1 else value


def round2_budget(round1, opponents):
    """Opponents are per-person FINAL settled envelopes, not per-round maxima."""
    first=settled_score(round1,1)
    if not opponents:raise Unsupported('Need observed or explicitly forecast opponent totals')
    ids=[];normalized=[]
    for row in opponents:
        identity=row['id']
        if not isinstance(identity,str) or not identity:raise Unsupported('Opponent needs stable identity')
        low=integer(row['min'],'opponent min');high=integer(row['max'],'opponent max')
        if high<low:raise Unsupported('Reversed opponent interval')
        ids.append(identity);normalized.append(dict(id=identity,min=low,max=high))
    if len(ids)!=len(set(ids)):raise Unsupported('Duplicate opponent identity')
    low=max(r['min'] for r in normalized);high=max(r['max'] for r in normalized)
    return dict(round1_settled=first,opponents=normalized,
                opponent_best_total=dict(min=low,max=high),
                round2_needed=dict(optimistic=max(0,low-first),conservative=max(0,high-first)),
                tie_rule='player-first',probability=None,
                scope='arithmetic conditional on supplied settled opponent envelopes; not a calibrated win probability')


class HIFPlanner:
    def __init__(self,idol_card_id='i_card-shro-3-000',catalog=None):
        self.catalog=catalog or Catalog();self.idol_card_id=idol_card_id
        self.idol=self.catalog.one('IdolCard',idol_card_id)

    def configuration(self,mode,position):
        schedule=HIFSchedule(mode,self.catalog)
        result=schedule.exam_options(position,self.idol_card_id)
        if len(result['configurations'])!=1:
            raise Unsupported('Need an observed difficulty number; multiple matching exam configurations')
        row=deepcopy(result['configurations'][0])
        group=row['difficulty']['produceExamBattleNpcGroupId']
        npcs=self.catalog.by_id('ProduceExamBattleNpcGroup',group)
        if not npcs:raise Unsupported('Missing NPC group')
        identities=[]
        for npc in npcs:
            character=npc['characterId'];mob=npc['produceExamBattleNpcMobId']
            identity=('character:'+character) if character else ('mob:'+mob)
            if not character and not mob:raise Unsupported('NPC identity missing')
            npc['identity']=identity
            integer(npc['scoreMin'],'NPC scoreMin');integer(npc['scoreMax'],'NPC scoreMax')
            if npc['scoreMin']>npc['scoreMax']:raise Unsupported('Reversed NPC bounds')
            identities.append(identity)
        if len(identities)!=len(set(identities)):raise Unsupported('Ambiguous NPC identities')
        row['npcs']=npcs;row['calendar']=schedule.stage(position)
        row['master_commit']=self.catalog.info['commit']
        return row

    def selection(self,number):
        if type(number) is not int or number not in (1,2,3):raise Unsupported('Selection number must be 1..3')
        position={1:7,2:13,3:20}[number]
        row=self.configuration('produce-007',position)
        k=integer(row['difficulty']['rankThreshold'],'rankThreshold',1)
        def threshold(field):
            values=sorted((r[field] for r in row['npcs']),reverse=True)
            return values[k-1] if k<=len(values) else 0
        return dict(selection=number,position=position,turns=row['battle_config']['turn'],
                    rank_threshold=k,pass_score_envelope=dict(min=threshold('scoreMin'),max=threshold('scoreMax')),
                    strategy_goals={strategy:dict(target_score=threshold('scoreMax')+1,
                        stop_at_target=strategy=='qualification') for strategy in ('score','qualification')},
                    strategy_goal_scope='Strictly above pinned NPC upper envelope; avoids assuming selection tie rules. Conditional local target, not server qualification.',
                    base_score_reference=row['difficulty']['baseScore'],
                    star_reference=row['difficulty']['starScoreBonusBaseLine'],
                    objective='pass before preserving resources or maximizing growth rewards',
                    evidence=row,scope='snapshot NPC envelope; live NPC scores and modifiers take precedence')

    def finals(self,round1=None,observed_round1_npcs=None):
        a=self.configuration('produce-008',7);b=self.configuration('produce-008',9)
        left={r['identity']:r for r in a['npcs']};right={r['identity']:r for r in b['npcs']}
        if set(left)!=set(right):raise Unsupported('Cannot join NPC identities across rounds')
        if observed_round1_npcs is not None and set(observed_round1_npcs)!=set(left):
            raise Unsupported('Observed round1 NPCs must cover exact participant identities')
        totals=[]
        for identity in sorted(left):
            x,y=left[identity],right[identity]
            if observed_round1_npcs is None:
                lo=settled_score(dict(value=x['scoreMin'],kind='raw'),1)
                hi=settled_score(dict(value=x['scoreMax'],kind='raw'),1)
            else:
                lo=hi=settled_score(observed_round1_npcs[identity],1)
            totals.append(dict(id=identity,min=lo+y['scoreMin'],max=hi+y['scoreMax']))
        result=dict(idol_card=self.idol_card_id,master_commit=self.catalog.info['commit'],
                    rounds=[a,b],opponent_totals=totals,
                    objective='maximize probability of aggregate first place, not S4+ rating',
                    assumptions=['snapshot raw NPC score bounds; round1 floor(raw*6/5)',
                                 'round2 NPC bounds unchanged by unmodelled runtime/event modifiers'],
                    observed_round1_npcs=observed_round1_npcs,
                    scope='conditional snapshot forecast, not a server-verified guarantee',probability=None)
        if round1 is not None:result['budget']=round2_budget(round1,totals)
        return result



def dispatch(mode,position,event,*,hp=None,max_hp=None,catalog=None):
    """Route an ACTUALLY OFFERED event, not synthesize it after a prior action.

The event producer remains responsible for observed/native legal candidates and
reward settlement. This function returns a module, never executes a UI action.
"""
    owned=catalog is None;c=catalog or Catalog()
    try:
        stage=HIFSchedule(mode,c).stage(position)
        kind=event.get('kind');source=event.get('source_action')
        actions=set(stage['options'])
        if source=='rest':
            if not stage.get('rest_when_wounded'):raise Unsupported('Rest unavailable in this stage')
            if hp is None or max_hp is None:raise Unsupported('Rest needs observed HP')
            integer(hp,'hp');integer(max_hp,'max_hp',1)
            if hp>=max_hp:raise Unsupported('Rest requires wounded state')
        elif source not in actions:raise Unsupported('Event source is not an open calendar action')
        if kind=='action_choice':module='hif_route'
        elif kind=='training_choice' and source=='training':module='training'
        elif kind=='class_choice' and source=='class':module='training'
        elif kind=='card_offer' and source in {'class','outing','gift','shop','interval','exam','round1','round2'}:module='card_pick'
        elif kind=='card_change' and source in {'class','shop','interval'}:module='card_remove'
        elif kind=='drink_offer' and source in {'shop','gift','outing','interval'}:module='drinks'
        elif kind=='battle_action' and source in {'exam','round1','round2'}:module='play_cards+drinks'
        elif kind=='shop_action' and source in {'shop','interval'}:module='hif_shop'
        elif kind=='customize' and source in {'special_guidance','interval','exam','round1','round2'}:module='hif_customize'
        elif kind=='custom_item_choice' and mode=='produce-007' and source=='exam':module='hif_custom_item'
        elif kind=='settlement':module='verify_outcome'
        else:raise Unsupported('No verified HIF route for offered event')
        if kind not in {'action_choice','settlement'} and not event.get('candidates'):
            raise Unsupported('Need actual nonempty event candidates/legal actions')
        return dict(mode=mode,position=position,day=stage['day'],stage_kind=stage['kind'],
                    module=module,event=deepcopy(event),
                    scope='event dispatch only; does not create candidates, settle rewards or advance calendar')
    finally:
        if owned:c.db.close()
