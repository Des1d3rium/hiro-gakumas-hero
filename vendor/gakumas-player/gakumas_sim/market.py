"""Current, source-normalized HIF presents and ordinary shop progress.

No server inventory generator, reward distribution or implicit response refresh.
"""
from copy import deepcopy
import math

from .catalog import Catalog
from .core import Unsupported,f32
from .finals import exact,source,text
from .growth_scaling import checked
from .master_adapter import MasterAdapter
from .permanent_upgrade import upgrade_target

PRODUCT_FIELDS={'positionNumber','purchased','resourceType','resourceId','upgradeCount','price','discountPermil',
                'effectDiscountPermils','effectResourceDiscountPermils','nextPrice','lock','highScoreReward','quoted_price'}
COMMON={'id','provenance','state_digest','can_finish','unmodelled_effects'}
PRESENT_BASE_POINTS=80 # Pinned HIF community rule, cross-checked in run2 gift log.


def sale_step(price,discount):
    # GetDiscountPrice 25b69d4..25b69f8: divide first, multiply, truncate.
    return math.trunc(f32(f32(f32(f32(1000)-f32(discount))/f32(1000))*f32(price)))


def effect_step(price,discount):
    # 25b6b04..25b6b20 and resource-loop copy: multiply, divide, truncate.
    return math.trunc(f32(f32(f32(f32(1000)-f32(discount))*f32(price))/f32(1000)))


def current_price(product,c):
    settings=list(c.rows('Setting'))
    if len(settings)!=1 or settings[0]['produceStepShopDiscountRoundDownEnable'] is not True:
        raise Unsupported('Unported shop price setting branch')
    value=checked(product['price'],'Shop price',10000000)
    discount=checked(product['discountPermil'],'Sale discount',1000)
    groups=[]
    for key in ('effectDiscountPermils','effectResourceDiscountPermils'):
        values=product[key]
        if not isinstance(values,list):raise Unsupported('Shop discounts need ordered lists')
        for v in values:checked(v,'Shop effect discount',1000)
        groups.extend(values)
    # IsEffectDiscount: nonempty global list OR Any(resource rate > 0).
    if discount or product['effectDiscountPermils'] or any(v>0 for v in product['effectResourceDiscountPermils']):
        value=sale_step(value,discount)
        for rate in groups:value=effect_step(value,rate)
    return checked(value,'Final shop price',10000000)


def validate_rewards(rewards):
    if not isinstance(rewards,list):raise Unsupported('Explicit normalized reward list required')
    c=Catalog()
    try:
        adapter=MasterAdapter(c)
        for reward in rewards:
            if not isinstance(reward,dict):raise Unsupported('Reward must be object')
            kind=reward.get('kind')
            if kind=='point':exact(reward,{'kind','value'},'P reward');checked(reward['value'],'P reward')
            elif kind=='card':
                exact(reward,{'kind','spec'},'Card reward');exact(reward['spec'],{'card_id','upgrade','customizations'},'Reward spec')
                adapter.card(reward['spec']['card_id'],reward['spec']['upgrade'],reward['spec']['customizations'])
            elif kind=='drink':exact(reward,{'kind','drink_id'},'Drink reward');adapter.drink(reward['drink_id'])
            else:raise Unsupported('Normalized market reward not ported')
    finally:c.db.close()


def validate_present_drinks(rewards):
    c=Catalog()
    try:
        for r in rewards:
            if r['kind']=='drink' and c.one('ProduceDrink',r['drink_id'])['rarity'] not in ('ProduceDrinkRarity_Sr','ProduceDrinkRarity_Ssr'):
                raise Unsupported('Pinned HIF present drinks require SR or SSR')
    finally:c.db.close()


class MarketMixin:
    def _validate_market_snapshot(self,kind,snapshot,first):
        extra={'groups','points_received'} if kind=='present' else {'products','eligible','reroll_available','reroll_price'}
        if kind=='shop' and (self.state.get('custom_shop_discounts') or 'custom_discount_bindings' in snapshot):
            extra=extra|{'custom_discount_bindings'}
        exact(snapshot,COMMON|extra,'Current '+kind+' progress');source(snapshot['provenance']);text(snapshot['id'],'Progress ID')
        if snapshot['state_digest']!=self.state_digest():raise Unsupported('Market progress belongs to another state')
        if type(snapshot['can_finish']) is not bool or snapshot['unmodelled_effects']!=[]:
            raise Unsupported('Need explicit completion status and no unported market effects')
        if not first and snapshot['id'] in self.market['seen']:raise Unsupported('Stale market progress ID')
        positions=set();consumed=set() if first or self.market['reset_allowed'] else set(self.market['consumed'])
        if kind=='present':
            checked(snapshot['points_received'],'Present P receipt',PRESENT_BASE_POINTS)
            if not first and snapshot['points_received']!=self.market['points_received']:
                raise Unsupported('Present P receipt ledger changed without a result')
            if not isinstance(snapshot['groups'],list):raise Unsupported('Present groups must be explicit')
            for group in snapshot['groups']:
                exact(group,{'position','received','display_type','pick_count','candidates'},'Present group')
                pos=checked(group['position'],'Present position')
                if pos in positions or type(group['received']) is not bool:raise Unsupported('Duplicate/invalid present group')
                positions.add(pos)
                if pos in consumed and not group['received']:raise Unsupported('Received present reappeared')
                if group['display_type'] not in ('Itself','Choice') or type(group['pick_count']) is not int or group['pick_count']!=1:
                    raise Unsupported('Present display/pick shape not ported')
                if not isinstance(group['candidates'],list) or not group['candidates']:raise Unsupported('Need current present candidates')
                if group['display_type']=='Itself' and len(group['candidates'])!=1:raise Unsupported('Itself present cannot have choices')
                for option in group['candidates']:
                    exact(option,{'rewards'},'Present candidate');validate_rewards(option['rewards']);validate_present_drinks(option['rewards'])
        else:
            checked(snapshot['reroll_price'],'Shop reroll price')
            if type(snapshot['reroll_available']) is not bool or not isinstance(snapshot['products'],list) or not isinstance(snapshot['eligible'],dict):
                raise Unsupported('Need explicit shop products, service selectors and reroll availability')
            services=set();c=Catalog()
            try:
                for p in snapshot['products']:
                    exact(p,PRODUCT_FIELDS,'Shop product');pos=checked(p['positionNumber'],'Shop position')
                    if pos in positions:raise Unsupported('Duplicate shop position')
                    positions.add(pos)
                    for key in ('purchased','lock','highScoreReward'):
                        if type(p[key]) is not bool:raise Unsupported('Shop flags must be boolean')
                    for key in ('upgradeCount','nextPrice','quoted_price'):checked(p[key],key)
                    if not isinstance(p['resourceId'],str) or not isinstance(p['resourceType'],str):raise Unsupported('Shop resource fields must be strings')
                    if current_price(p,c)!=p['quoted_price']:raise Unsupported('Quoted price differs from pinned native price arithmetic')
                    if p['resourceType']=='ProduceResourceType_ProduceCard' and p['upgradeCount']==0 and c.card(p['resourceId'],0)['rarity']=='ProduceCardRarity_R':
                        raise Unsupported('Pinned HIF ordinary shop does not offer unupgraded R cards')
                    if pos in consumed and not p['purchased']:raise Unsupported('Purchased product revived without reroll')
                    if p['resourceType'] in ('ProduceResourceType_CardDelete','ProduceResourceType_CardUpgrade'):services.add(str(pos))
                exact(snapshot['eligible'],services,'Actual service selectors')
                for values in snapshot['eligible'].values():
                    if not isinstance(values,list) or any(not isinstance(k,str) for k in values) or len(values)!=len(set(values)) or set(values)-set(self.state['cards']):
                        raise Unsupported('Service selector differs from current physical cards')
            finally:c.db.close()
            from .custom_discounts import validate_bindings
            validate_bindings(snapshot,self.state)

    def begin_custom_shop(self,binding):
        if 'shop' not in self._stage()['options'] or self.pending or getattr(self,'current_event',None) is not None:
            raise Unsupported('Custom shop start is not the current calendar choice')
        exact(binding,{'provenance','position','state_digest','unmodelled_effects'},'Custom shop start')
        source(binding['provenance'])
        if (type(binding['position']) is not int or binding['position']!=self.position
            or binding['state_digest']!=self.state_digest() or binding['unmodelled_effects']!=[]):
            raise Unsupported('Stale or unmodelled custom shop start')
        trial=deepcopy(self);trial._supports('StartShop');c=Catalog()
        try:
            from .growth_items import phase_effects
            effects,fired=phase_effects(trial.state,c,'StartShop')
        finally:c.db.close()
        trial.custom_market_start=dict(kind='shop',position=self.position,fired=fired)
        trial.pending=effects;trial.continuation='custom_market_start';trial._drain();trial._validate(trial.state)
        trial.events.append(dict(kind='custom_shop_begin',input=deepcopy(binding),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def _market_start_trial(self,kind,start_receipt=None):
        if kind not in ('present','shop'):raise Unsupported('Unknown market entry')
        if self.phase=='shop_await_snapshot' and self.custom_market_start is not None:
            if kind!='shop' or start_receipt is not None or self.pending or self.custom_market_start['position']!=self.position:
                raise Unsupported('Prepared custom shop cannot replay start effects')
            return deepcopy(self)
        stage=self._stage();action='gift' if kind=='present' else 'shop'
        if action not in stage['options']:raise Unsupported('Market is not offered by actual calendar')
        if kind=='shop':
            c=Catalog()
            try:
                from .growth_items import phase_pending
                if phase_pending(self.state,c,'StartShop'):raise Unsupported('Resolve custom shop start before requesting a quote')
            finally:c.db.close()
        trial=deepcopy(self)
        if start_receipt is not None:
            exact(start_receipt,{'provenance','state_digest_before','rewards','unmodelled_effects'},'Market Start receipt');source(start_receipt['provenance'])
            if start_receipt['state_digest_before']!=self.state_digest() or start_receipt['unmodelled_effects']!=[]:
                raise Unsupported('Unported or stale market Start receipt')
            validate_rewards(start_receipt['rewards'])
            if any(r['kind']!='point' for r in start_receipt['rewards']):raise Unsupported('Non-P market Start effects are not ported')
            received=sum(r['value'] for r in start_receipt['rewards'])
            trial.state['produce_points']+=received
        trial._supports('StartPresent' if kind=='present' else 'StartShop')
        return trial

    def preview_market_start(self,kind,start_receipt=None):
        trial=self._market_start_trial(kind,start_receipt)
        return dict(state=deepcopy(trial.state),state_digest=trial.state_digest(),support_trace=deepcopy(trial.support_trace))

    def open_market(self,kind,snapshot,start_receipt=None):
        trial=self._market_start_trial(kind,start_receipt)
        if start_receipt is not None and kind=='present' and sum(r['value'] for r in start_receipt['rewards'])>snapshot.get('points_received',0):
            raise Unsupported('Start P credit missing from present receipt ledger')
        trial._validate_market_snapshot(kind,snapshot,True);trial._validate(trial.state)
        trial.market=dict(kind=kind,snapshot=deepcopy(snapshot),seen=[snapshot['id']],consumed=[],reset_allowed=False)
        trial.custom_market_start=None
        if kind=='present':trial.market['points_received']=snapshot['points_received']
        rows=snapshot['groups'] if kind=='present' else snapshot['products']
        trial.market['consumed']=([r['position'] for r in rows if r['received']] if kind=='present'
                                  else [r['positionNumber'] for r in rows if r['purchased']])
        trial.phase=kind;trial.events.append(dict(kind='market_open',market_kind=kind,input=deepcopy(snapshot)))
        if start_receipt is not None:trial.events[-1]['start_receipt']=deepcopy(start_receipt)
        self.__dict__.update(trial.__dict__)

    def install_market(self,snapshot):
        if self.market is None or self.phase!=self.market['kind']+'_await_progress':raise Unsupported('No market update is pending')
        kind=self.market['kind'];self._validate_market_snapshot(kind,snapshot,False);trial=deepcopy(self)
        if trial.market['reset_allowed']:trial.market['consumed']=[]
        trial.market['reset_allowed']=False;trial.market['snapshot']=deepcopy(snapshot);trial.market['seen'].append(snapshot['id'])
        rows=snapshot['groups'] if kind=='present' else snapshot['products']
        found=[r['position'] for r in rows if r['received']] if kind=='present' else [r['positionNumber'] for r in rows if r['purchased']]
        trial.market['consumed']=sorted(set(trial.market['consumed'])|set(found));trial.phase=kind
        trial.events.append(dict(kind='market_progress',input=deepcopy(snapshot)));self.__dict__.update(trial.__dict__)

    def _queue_market_rewards(self,rewards,ending=False):
        validate_rewards(rewards)
        if self.market['kind']=='present':
            validate_present_drinks(rewards)
            points=self.market['points_received']+sum(r['value'] for r in rewards if r['kind']=='point')
            if points>PRESENT_BASE_POINTS or ending and points!=PRESENT_BASE_POINTS:
                raise Unsupported('Present base80P would be duplicated or omitted; extra effects are not ported')
            self.market['points_received']=points
        self.pending=[];self.continuation=self.market['kind']+('_end' if ending else '')
        for i,r in enumerate(rewards):
            kind={'point':'point','card':'grant_card','drink':'grant_drink'}[r['kind']]
            effect={k:deepcopy(v) for k,v in r.items() if k!='kind'}
            self.pending.append(dict(effect,kind=kind,source_id='market:'+self.market['snapshot']['id']+':'+str(i)))
        self._drain()

    def _market_rewards_finished(self):
        ending=self.continuation.endswith('_end');kind=self.market['kind'];self.continuation=None
        if ending:
            self.market=None;self._advance_schedule()
        else:self.market['snapshot']=None;self.phase=kind+'_await_progress'

    def choose_present(self,progress_id,position,candidate_index):
        if self.phase!='present' or self.market is None:raise Unsupported('No current present is open')
        snapshot=self.market['snapshot']
        if progress_id!=snapshot['id']:raise Unsupported('Stale present action')
        checked(position,'Present position');checked(candidate_index,'Present choice index')
        groups=[r for r in snapshot['groups'] if r['position']==position]
        if len(groups)!=1 or groups[0]['received'] or position in self.market['consumed']:raise Unsupported('Present is absent or already received')
        if candidate_index>=len(groups[0]['candidates']):raise Unsupported('Present choice outside current candidates')
        trial=deepcopy(self);trial.market['consumed'].append(position)
        trial._queue_market_rewards(groups[0]['candidates'][candidate_index]['rewards']);trial._validate(trial.state)
        trial.events.append(dict(kind='present_choice',progress_id=progress_id,position=position,candidate_index=candidate_index,after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def transact_shop(self,action):
        if self.phase!='shop' or self.market is None:raise Unsupported('No current ordinary shop is open')
        q=self.market['snapshot']
        if not isinstance(action,dict) or action.get('quote_id')!=q['id']:raise Unsupported('Stale shop action')
        trial=deepcopy(self);detail={};rewards=[]
        if action.get('kind')=='reroll':
            exact(action,{'kind','quote_id'},'Shop reroll')
            if not q['reroll_available']:raise Unsupported('Shop reroll is not currently available')
            price=q['reroll_price'];trial.market['reset_allowed']=True
        elif action.get('kind')=='buy':
            checked(action.get('position'),'Shop position');found=[p for p in q['products'] if p['positionNumber']==action['position']]
            if len(found)!=1:raise Unsupported('Product is not currently offered')
            p=found[0];service=p['resourceType'] in ('ProduceResourceType_CardDelete','ProduceResourceType_CardUpgrade')
            exact(action,{'kind','quote_id','position','instance'} if service else {'kind','quote_id','position'},'Shop action')
            if p['purchased'] or p['lock'] or p['highScoreReward'] or p['positionNumber'] in trial.market['consumed']:
                raise Unsupported('Product is sold/locked or its special condition is not ported')
            price=p['quoted_price'];resource=p['resourceType']
            if price>trial.state['produce_points']:raise Unsupported('Insufficient shop P points')
            if resource=='ProduceResourceType_ProduceCard':
                rewards=[dict(kind='card',spec=dict(card_id=p['resourceId'],upgrade=p['upgradeCount'],customizations=[]))]
                trial.market['consumed'].append(p['positionNumber'])
            elif resource=='ProduceResourceType_ProduceDrink':
                c=Catalog()
                try:capacity=c.one('ProduceSetting','p_setting-8')['produceDrinkPossessLimit']+trial.state['drink_capacity_addition']
                finally:c.db.close()
                if len(trial.state['drinks'])>=capacity:raise Unsupported('Shop drink inventory is full')
                if p['upgradeCount']:raise Unsupported('Drink product has unexpected upgrade')
                rewards=[dict(kind='drink',drink_id=p['resourceId'])];trial.market['consumed'].append(p['positionNumber'])
            elif service:
                if p['resourceId'] or p['upgradeCount']:raise Unsupported('Unexpected service payload')
                instance=action['instance']
                if instance not in q['eligible'][str(p['positionNumber'])]:raise Unsupported('Card is outside current service selector')
                old=deepcopy(trial.state['cards'][instance])
                if resource=='ProduceResourceType_CardDelete':
                    del trial.state['cards'][instance];trial.state['card_order'].remove(instance)
                else:
                    c=Catalog()
                    try:trial.state['cards'][instance]=upgrade_target(c,old)
                    finally:c.db.close()
                detail=dict(instance=instance,before_card=old,after_card=deepcopy(trial.state['cards'].get(instance)))
                trial._supports('DeleteProduceCard' if resource=='ProduceResourceType_CardDelete' else 'UpgradeProduceCard',dict(card=old))
            else:raise Unsupported('Ordinary shop resource/service not ported: '+resource)
            if resource=='ProduceResourceType_ProduceCard':trial._supports('BuyShopItemProduceCard')
            if resource=='ProduceResourceType_ProduceDrink':trial._supports('BuyShopItemProduceDrink')
        else:raise Unsupported('Unknown ordinary shop action')
        if price>trial.state['produce_points']:raise Unsupported('Insufficient shop P points')
        trial.state['produce_points']-=price;trial._queue_market_rewards(rewards);trial._validate(trial.state)
        trial.events.append(dict(kind='shop_action',action=deepcopy(action),price=price,details=detail,after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def end_market(self,result):
        if self.market is None or self.phase!=self.market['kind']:raise Unsupported('No settled market can finish')
        q=self.market['snapshot']
        exact(result,{'provenance','progress_id','state_digest','rewards','unmodelled_effects'},'Market End result');source(result['provenance'])
        if not q['can_finish'] or result['progress_id']!=q['id'] or result['state_digest']!=self.state_digest():raise Unsupported('Market End status or binding mismatch')
        if result['unmodelled_effects']!=[]:raise Unsupported('Unported market End effects')
        trial=deepcopy(self);kind=trial.market['kind'];trial._queue_market_rewards(result['rewards'],ending=True);trial._validate(trial.state)
        trial.events.append(dict(kind='market_end',market_kind=kind,input=deepcopy(result),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)
