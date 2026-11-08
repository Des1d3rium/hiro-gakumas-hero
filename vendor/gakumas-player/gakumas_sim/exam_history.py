"""Past UI history and a bounded proof of a recent ordinary-draw refill.

Entering-hand UI lists include targeted moves. They are not all random draws.
No native seed, hidden order, owner ID or future turn list is accepted/output.
"""
from collections import Counter
from copy import deepcopy
import hashlib

from .core import Unsupported
from .exam_snapshot import canonical,project
from .exam_resume import compile_runtime,MainPhaseSession
from .catalog import Catalog
from .master_adapter import MasterAdapter

FIELDS={'schema_version','scope','source','public_sha256','through_turn','headers','events','history_sha256'}
PRIOR='exchangeable shuffle permutations + independent future chances; not native shared-RNG conditional posterior'


def digest(packet):
    return hashlib.sha256(canonical(packet).encode()).hexdigest()


def cards(row,prefix):
    names=['_'+prefix+'Card'+s for s in ('List','UpgradeList','CustomizeList')]
    if any(k not in row or not isinstance(row[k],list) for k in names):
        raise Unsupported('Missing UI card arrays')
    if len({len(row[k]) for k in names})!=1:raise Unsupported('UI card arrays are not aligned')
    result=[]
    for key,grade,custom in zip(*(row[k] for k in names)):
        if (not isinstance(key,str) or not key or type(grade) is not int or grade<0
                or not isinstance(custom,dict) or set(custom)!={'_customizeList'}
                or not isinstance(custom['_customizeList'],list)
                or any(type(v) is not int or v<0 for v in custom['_customizeList'])):
            raise Unsupported('Invalid public UI card version')
        result.append(dict(card_id=key,upgrade=grade,customize_counts=list(custom['_customizeList'])))
    return result


def extract(document,*,client_version,master_commit):
    observed=project(document,client_version=client_version,master_commit=master_commit)
    rows=document.get('userPlayLogList')
    if not isinstance(rows,list) or not rows:raise Unsupported('Past UI log list required')
    headers=[];events=[];turn=0;through=observed['scalars']['currentTurn']
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or type(row.get('_cellType')) is not int or row['_cellType'] not in range(15):
            raise Unsupported('Unknown UI log cell')
        kind=row['_cellType']
        if kind==1:
            current=row.get('_currentTurn')
            if type(current) is not int or current>through:raise Unsupported('Future UI header not admitted')
            if current!=turn+1:raise Unsupported('Past UI headers must be consecutive from turn one')
            turn=current
            headers.append(dict(turn=turn,index=index,incoming=cards(row,'draw'),played=cards(row,'play')))
        elif type(row.get('_currentTurn')) is not int or row['_currentTurn'] not in (0,turn):
            raise Unsupported('Future or unbound UI event not admitted')
        details=row.get('_detailList')
        if not isinstance(details,list) or not isinstance(row.get('_triggerId'),str):
            raise Unsupported('Incomplete UI log detail/source')
        moves=[]
        for detail in details:
            if type(detail.get('_detailType')) is not int or not isinstance(detail.get('_detailLineList'),list):
                raise Unsupported('Invalid UI detail')
            for line in detail['_detailLineList']:
                if type(line.get('_detailLineType')) is not int:raise Unsupported('Unknown UI detail line')
                if line['_detailLineType'] not in (6,7):continue
                required=['_targetId','_targetSubscriptionNumber','_cardFromPosition','_targetMovePosition']
                if any(k not in line for k in required):raise Unsupported('Incomplete UI movement')
                if not isinstance(line['_targetId'],str) or not line['_targetId'] or any(type(line[k]) is not int or line[k]<0 for k in required[1:]):
                    raise Unsupported('Invalid UI movement target')
                moves.append(dict(detail_type=detail['_detailType'],kind='move' if line['_detailLineType']==6 else 'draw',
                    card_id=line['_targetId'],upgrade=line['_targetSubscriptionNumber'],
                    origin=line['_cardFromPosition'],destination=line['_targetMovePosition']))
        events.append(dict(index=index,turn=turn,cell_type=kind,source_id=row['_triggerId'],movements=moves))
    if turn!=through:raise Unsupported('UI history does not reach current turn')
    result=dict(schema_version=1,scope='public-hif-past-hand-entry-history',source=deepcopy(observed['source']),
        public_sha256=observed['public_sha256'],through_turn=through,headers=headers,events=events)
    result['history_sha256']=digest(result)
    return result


def verify(history,observation):
    if not isinstance(history,dict) or set(history)!=FIELDS:
        raise Unsupported('Exact public history fields required')
    packet=deepcopy(history);claimed=packet.pop('history_sha256')
    if digest(packet)!=claimed:raise Unsupported('Public history fingerprint mismatch')
    if (history['schema_version']!=1 or history['scope']!='public-hif-past-hand-entry-history'
        or history['source']!=observation['source'] or history['public_sha256']!=observation['public_sha256']
        or history['through_turn']!=observation['scalars']['currentTurn']):
        raise Unsupported('Public history does not match this observation')
    headers=history['headers'];events=history['events']
    if not isinstance(headers,list) or len(headers)!=history['through_turn'] or [h.get('turn') for h in headers]!=list(range(1,history['through_turn']+1)):
        raise Unsupported('Incomplete consecutive public history')
    if not isinstance(events,list) or [e.get('index') for e in events]!=list(range(len(events))):
        raise Unsupported('Incomplete event sequence')
    turn=0
    for event in events:
        if set(event)!={'index','turn','cell_type','source_id','movements'} or type(event['cell_type']) is not int or event['cell_type'] not in range(15) or not isinstance(event['source_id'],str):
            raise Unsupported('Unknown history event fields')
        if event['cell_type']==1:
            turn+=1
            if headers[turn-1]['index']!=event['index']:raise Unsupported('Header event position mismatch')
        if event['turn']!=turn or turn>history['through_turn']:raise Unsupported('Future or misordered public event')
        for move in event['movements']:
            if (set(move)!={'detail_type','kind','card_id','upgrade','origin','destination'}
                or move['kind'] not in ('move','draw') or not isinstance(move['card_id'],str)
                or any(type(move[k]) is not int or move[k]<0 for k in ('detail_type','upgrade','origin','destination'))):
                raise Unsupported('Unknown public movement schema')
    for header in headers:
        if set(header)!={'turn','index','incoming','played'}:raise Unsupported('Unknown public header fields')
        for key in ('incoming','played'):
            if not isinstance(header[key],list):raise Unsupported('Invalid public card entries')
            for card in header[key]:
                if (set(card)!={'card_id','upgrade','customize_counts'} or not isinstance(card['card_id'],str)
                    or not card['card_id'] or type(card['upgrade']) is not int or card['upgrade']<0
                    or not isinstance(card['customize_counts'],list)
                    or any(type(v) is not int or v<0 for v in card['customize_counts'])):
                    raise Unsupported('Invalid public history card')


def analyze(history,observation,boundary):
    """Prove only the bounded actual-history shape; reject unexplained moves."""
    runtime=compile_runtime(observation,boundary);verify(history,observation)
    c=Catalog()
    try:
        adapter=MasterAdapter(c)
        def rule(card):
            raw=c.card(card['card_id'],card['upgrade']);counts=card['customize_counts'];keys=raw['produceCardCustomizeIds']
            if len(counts) not in (0,len(keys)):raise Unsupported('History customization branch mismatch')
            return adapter.card(card['card_id'],card['upgrade'],[dict(id=k,count=n) for k,n in zip(keys,counts) if n])
        for header in history['headers']:
            for card in header['incoming']+header['played']:rule(card)
        flat=[(event,move) for event in history['events'] for move in event['movements']]
        creations=[];lost_moves=[];targeted_hand=[];redraw_discards=[]
        for event,move in flat:
            if move['kind']=='draw':
                if (move['origin'],move['destination'])!=(0,1):raise Unsupported('Unknown UI draw shape')
                continue
            pair=(move['origin'],move['destination'])
            if pair==(0,4):
                if event['turn']<1 or event['cell_type']!=3 or move['detail_type']!=0:
                    raise Unsupported('Creation origin/timing not ported')
                played=[r for r in history['headers'][event['turn']-1]['played'] if r['card_id']==event['source_id']]
                if len(played)!=1 or dict(op='create_deck_random',definition_id=move['card_id']+'@'+str(move['upgrade'])) not in rule(played[0])['effects']:
                    raise Unsupported('Creation does not match a visible card body')
                creations.append(move['card_id'])
            elif pair==(3,6):lost_moves.append(move['card_id'])
            elif pair==(4,1):targeted_hand.append(move['card_id'])
            elif pair==(2,5):redraw_discards.append(move['card_id'])
            else:raise Unsupported('Unported historical movement shape')
        # Replay the broad zone-conservation facts before relying on last-turn counts.
        all_plays=[r for h in history['headers'] for r in h['played']]
        if len(all_plays)!=observation['scalars']['examCardPlayCount']:
            raise Unsupported('UI/native cumulative play count mismatch')
        for header in history['headers']:
            actions=[e for e in history['events'] if e['turn']==header['turn'] and e['cell_type']==3]
            sources=[e['source_id'] for e in actions]
            if sources!=[r['card_id'] for r in header['played']]:raise Unsupported('UI played cards/events disagree')
            for card,event in zip(header['played'],actions):
                effects=rule(card)['effects']
                redraws=[e for e in effects if e['op']=='discard_redraw']
                if redraws:
                    if len(redraws)!=1:raise Unsupported('Multiple historical redraws need a full replay')
                    body=[m for m in event['movements'] if m['detail_type']==0]
                    discarded=[m for m in body if m['kind']=='move' and (m['origin'],m['destination'])==(2,5)]
                    actual_draw=sum(m['kind']=='draw' for m in body)
                    explicit_draw=sum(e['value'] for e in effects if e['op']=='draw')
                    if actual_draw!=len(discarded)+explicit_draw:
                        raise Unsupported('Historical redraw discard/draw counts disagree')
                    for moved in discarded:
                        c.card(moved['card_id'],moved['upgrade'])
                        if moved['card_id'] not in {r['card_id'] for r in observation['cards'].values()}:
                            raise Unsupported('Discarded card absent from exam inventory')
                        if moved['card_id']==card['card_id'] and sum(r['card_id']==card['card_id'] for r in observation['cards'].values())==1:
                            raise Unsupported('Playing card cannot be discarded by its own redraw')
        expected_created=Counter()
        for card in all_plays:
            effects=rule(card)['effects']
            for e in effects:
                if e['op']=='create_deck_random':expected_created[e['definition_id'].split('@')[0]]+=1
                elif e['op']=='conditional':
                    from .card_mutations import targets
                    if list(targets(e)):raise Unsupported('Conditional historical generation needs a full replay')
        if expected_created!=Counter(creations):raise Unsupported('Historical generated-card count differs from card bodies')
        for event,move in flat:
            if move['kind']=='move' and (move['origin'],move['destination'])==(4,1):
                if event['cell_type']!=4:raise Unsupported('Targeted HandAdd source not ported')
                effects=adapter.drink(event['source_id'])['effects']
                fetch=[e for e in effects if e['op']=='fetch_tag']
                if len(fetch)!=1 or adapter.card(move['card_id'],move['upgrade'])['search_tag']!=fetch[0]['tag']:
                    raise Unsupported('Targeted HandAdd does not match its drink rule')
        # The UI records ordinary distribution plus effect draws AND targeted
        # HandAdd. The movement's grade can precede a queued support upgrade.
        setting=c.one('ExamSetting',observation['scalars']['settingId']);draw=setting['turnStartDistribute']
        grade_changes=[]
        for header in history['headers']:
            later=[m for e,m in flat if e['turn']==header['turn'] and (m['kind']=='draw' or m['destination']==1)]
            tail=header['incoming'][draw:]
            if len(header['incoming'])<draw or [r['card_id'] for r in tail]!=[r['card_id'] for r in later]:
                raise Unsupported('Hand-entry history does not reconcile with ordinary distribution and effects')
            for card,move in zip(tail,later):
                delta=card['upgrade']-move['upgrade']
                if not 0<=delta<=len(observation['supports']):raise Unsupported('Historical HandAdd version cannot follow support upgrades')
                for grade in range(move['upgrade'],card['upgrade']+1):c.card(card['card_id'],grade)
                if delta:grade_changes.append(dict(turn=header['turn'],card_id=card['card_id'],before=move['upgrade'],after=card['upgrade'],movement_kind=move['kind']))
        once=Counter(r['card_id'] for r in all_plays if rule(r)['once'])
        if any(rule(r)['end_turn_lost'] for r in all_plays):raise Unsupported('Historical end-turn-loss model not admitted')
        lost=Counter(observation['cards'][i]['card_id'] for i in observation['zones']['lost'])
        if once+Counter(lost_moves)!=lost:raise Unsupported('Observed Lost composition is not conserved')
        inventory=Counter(r['card_id'] for r in observation['cards'].values())
        if Counter(creations)-inventory:raise Unsupported('Created-card inventory mismatch')
        initial=inventory-Counter(creations)
        if observation['zones']['removed']:raise Unsupported('Removed inventory not restored')
        current=history['headers'][-1]
        visible=[dict(card_id=observation['cards'][i]['card_id'],upgrade=observation['cards'][i]['effective_upgrade'],
                      customize_counts=observation['cards'][i]['customize_counts']) for i in observation['zones']['hand']]
        if current['incoming']!=visible or current['played'] or observation['scalars']['turnCardPlayCount']:
            raise Unsupported('Need the current post-deal, pre-play hand')
        if len(visible)!=draw or any(e['movements'] for e in history['events'] if e['turn']==current['turn']):
            raise Unsupported('Current deal has additional movement/draw history')
        if len(history['headers'])<2:raise Unsupported('Previous turn required for refill proof')
        previous=history['headers'][-2]
        if any(e['movements'] for e in history['events'] if e['turn']==previous['turn']):
            raise Unsupported('Previous-turn draw/move effects need a full replay')
        # Only resource builders with no card-moving operations in the final prior turn.
        allowed={'motivation','impression','extra_action','shield_score'}
        if any(rule(r)['once'] or any(e['op'] not in allowed for e in rule(r)['effects']) for r in previous['played']):
            raise Unsupported('Previous turn is outside the bounded grave-return proof')
        if any(rule(r)['end_turn_lost'] for r in previous['incoming']):raise Unsupported('Previous hand may leave the exam at turn end')
        before=Counter(r['card_id'] for r in previous['incoming']);used=Counter(r['card_id'] for r in previous['played'])
        if used-before:raise Unsupported('Previous played cards absent from hand entries')
        # Played reusable cards and the unplayed hand all return to Grave.
        minimum_grave=len(previous['incoming'])
        if minimum_grave<1 or observation['zones']['grave']:
            raise Unsupported('Empty-Grave refill witness absent')
        for i in observation['zones']['deck']:
            raw=observation['cards'][i]
            definition=rule(dict(card_id=raw['card_id'],upgrade=raw['effective_upgrade'],customize_counts=raw['customize_counts']))
            if raw['native']['_supportUpgradeIdList'] or raw['native']['_tmpUpgradeCount'] or definition['order']:
                raise Unsupported('Hidden version or fixed order cannot use this exchangeable tail model')
        if any(h['remaining_turns_max']==0 or h['remaining_turns_max']>=runtime['remaining_turns'] for h in runtime['values']['hooks'] if h['phase']=='turn_start'):
            raise Unsupported('Active turn-start effects require explicit scheduling before refill admission')
        return dict(schema_version=1,public_sha256=observation['public_sha256'],history_sha256=history['history_sha256'],
            incoming_entries=sum(len(h['incoming']) for h in history['headers']),played_entries=len(all_plays),
            effect_draw_lines=sum(m['kind']=='draw' for _,m in flat),targeted_hand_entries=len(targeted_hand),
            ordinary_distribution_entries=draw*len(history['headers']),hand_add_grade_changes=grade_changes,
            created=sorted(Counter(creations).items()),once_played=sum(once.values()),explicit_lost_moves=len(lost_moves),
            initial_card_id_composition=dict(sorted(initial.items())),initial_total=sum(initial.values()),
            initial_scope='Card-ID multiset after exam-start initialization; not original physical grades/customizations',
            last_refill=dict(turn=current['turn'],previous_turn=previous['turn'],
                previous_incoming=len(previous['incoming']),previous_played=len(previous['played']),
                previous_unplayed=dict(sorted((before-used).items())),minimum_grave_before_draw=minimum_grave,
                current_grave_count=0,current_deck_count=len(observation['zones']['deck']),normal_draw=draw,
                old_deck_count_upper_bound=draw-1,proved=True,
                argument='At least one card returned to Grave. Without exhausting Deck, ordinary draw leaves Grave nonempty. Current Grave is empty, so this deal refilled from Grave.'),
            exchangeable_tail_model=dict(admitted=True,prior=PRIOR,
                reason='A fresh exchangeable shuffle has an exchangeable remaining tail conditional on observed draws and remaining multiset; native shared-RNG conditioning is not asserted.'),
            native_posterior_admitted=False,full_exam_admitted=False,actual_clear=False)
    finally:c.db.close()


def next_batch_probability(history,observation,boundary,*,target_card_id,use_paper=False):
    """Admit the existing two-pile formula from the actual fresh-tail witness.

    This is a next-hand identity calculation, not a full-turn/score forecast.
    Returned hand cards stay in Grave until the current Deck is exhausted.
    """
    if type(use_paper) is not bool:raise Unsupported('Explicit boolean Paper action required')
    proof=analyze(history,observation,boundary)
    if not isinstance(target_card_id,str) or not target_card_id:raise Unsupported('Target card ID required')
    session=MainPhaseSession(observation,boundary)
    if use_paper:
        candidates=[i for i in observation['zones']['hand'] if observation['cards'][i]['card_id']=='p_card-02-act-2_062']
        if len(candidates)!=1:raise Unsupported('A single actual Paper card is required')
        session.step(dict(kind='play',id=candidates[0]))
    session.step(dict(kind='end_turn'))
    b=session.model
    due=[t for t in b.reservations if t['start']==1]
    if any(any(e['op']!='draw' for e in t['effects']) for t in due):raise Unsupported('Unknown next-deal reservation')
    c=Catalog()
    try:draw=c.one('ExamSetting',observation['scalars']['settingId'])['turnStartDistribute']
    finally:c.db.close()
    draw=min(b.hand_limit,draw+sum(e['value'] for t in due for e in t['effects']))
    returned=b.zones['grave']
    from .draw_risk import draw_probability
    result=draw_probability(deck_count=len(observation['zones']['deck']),
        deck_targets=sum(observation['cards'][i]['card_id']==target_card_id for i in observation['zones']['deck']),
        grave_count=len(observation['zones']['grave']),
        grave_targets=sum(observation['cards'][i]['card_id']==target_card_id for i in observation['zones']['grave']),
        returned_count=len(returned),returned_targets=sum(b.original_card_id(i)==target_card_id for i in returned),
        draw_count=draw,prior='uniform-pile-order')
    return dict(target_card_id=target_card_id,use_paper=use_paper,draw_count=draw,
        deck_count=len(observation['zones']['deck']),returned_count=len(returned),
        deck_targets=sum(observation['cards'][i]['card_id']==target_card_id for i in observation['zones']['deck']),
        prior=proof['exchangeable_tail_model']['prior'],result=result,
        input_public_sha256=observation['public_sha256'],history_sha256=history['history_sha256'],
        full_exam_admitted=False,native_posterior_admitted=False,win_probability=False)
