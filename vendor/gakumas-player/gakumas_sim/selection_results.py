"""Explicit settled result gains and mandatory selection custom-item menus."""
from copy import deepcopy
from .catalog import Catalog
from .core import Unsupported
from .finals import exact,source,text
from .growth_scaling import checked
from .growth_items import compile_item,ITEM_FIELDS,HISTORY_FIELD
from .market import validate_rewards


def exam_rewards(rewards):
    if not isinstance(rewards,list):raise Unsupported('Selection rewards require a list')
    for r in rewards:
        if not isinstance(r,dict):raise Unsupported('Selection reward must be an object')
        kind=r.get('kind')
        if kind=='settled_parameter':
            exact(r,{'kind','attribute','value'},'Settled parameter reward')
            if r['attribute'] not in ('Vo','Da','Vi'):raise Unsupported('Unknown settled attribute')
            checked(r['value'],'Settled parameter gain')
        elif kind=='settled_star':
            exact(r,{'kind','value'},'Settled star reward');checked(r['value'],'Settled star gain')
        else:validate_rewards([r])


class SelectionResultMixin:
    def custom_item_options(self):
        if self.custom_item_offer is None:return []
        c=Catalog();options=[]
        try:
            for key in self.custom_item_offer['candidates']:
                try:
                    compile_item(c,key,self.state['idol_card_id']);options.append(dict(id=key,executable=True))
                except Unsupported as error:options.append(dict(id=key,executable=False,reason=str(error)))
        finally:c.db.close()
        return options

    def receive_custom_item_offer(self,offer):
        if self.phase!='await_custom_item_offer' or self.custom_item_offer is not None:
            raise Unsupported('No current selection custom-item menu is pending')
        exact(offer,{'provenance','state_digest','position','id','candidates','unmodelled_effects'},'Current custom-item menu')
        source(offer['provenance']);text(offer['id'],'Custom-item menu ID')
        if type(offer['position']) is not int or offer['position']!=self.position or offer['state_digest']!=self.state_digest():
            raise Unsupported('Custom-item menu belongs to another date/state')
        candidates=offer['candidates']
        if (offer['unmodelled_effects']!=[] or not isinstance(candidates,list) or len(candidates)!=3
            or any(not isinstance(i,str) or not i for i in candidates) or len(set(candidates))!=3):
            raise Unsupported('Need three explicit custom-item candidates and no unknown effects')
        c=Catalog()
        try:
            plan=c.one('IdolCard',self.state['idol_card_id'])['planType'];items=self.state.get('customize_items',[])
            if self.position==7:
                if items:raise Unsupported('First selection custom item already exists')
                legal={r['id'] for r in c.rows('ProduceCustomizeItem') if r['isBase'] and r['planType']==plan}
            elif self.position in (13,20):
                if len(items)!=1:raise Unsupported('Need the one current custom item for selection customization')
                parent=c.one('ProduceCustomizeItem',items[0]['id'])
                if parent['isTerminal'] or bool(parent['isBase'])!=(self.position==13):
                    raise Unsupported('Current custom item does not match this selection stage')
                legal={r['childProduceCustomizeItemId'] for r in c.rows('ProduceCustomizeItemRelationship')
                       if r['parentProduceCustomizeItemId']==parent['id']}
            else:raise Unsupported('No custom-item choice on this date')
            if set(candidates)-legal:raise Unsupported('Candidate is outside the current direct custom-item path')
        finally:c.db.close()
        self.custom_item_offer=deepcopy(offer);self.phase='choose_custom_item'
        self.events.append(dict(kind='selection_custom_item_offer',input=deepcopy(offer)))

    def settle_custom_item(self,receipt):
        if self.phase!='choose_custom_item' or self.custom_item_offer is None:
            raise Unsupported('No current custom-item selection awaits a result')
        exact(receipt,{'provenance','state_digest','position','offer_id','selected_id','after_item','unmodelled_effects'},'Custom-item selection result')
        source(receipt['provenance'])
        if (type(receipt['position']) is not int or receipt['position']!=self.position
            or receipt['state_digest']!=self.state_digest() or receipt['offer_id']!=self.custom_item_offer['id']
            or self.custom_item_offer['state_digest']!=self.state_digest()):
            raise Unsupported('Stale custom-item selection result')
        if receipt['unmodelled_effects']!=[] or receipt['selected_id'] not in self.custom_item_offer['candidates']:
            raise Unsupported('Need a selected current custom item and no unknown effects')
        after=receipt['after_item'];exact(after,ITEM_FIELDS,'Result custom item')
        checked(after['number'],'Result custom item number')
        if after['id']!=receipt['selected_id']:raise Unsupported('Selected item differs from result')
        c=Catalog()
        try:compile_item(c,after['id'],self.state['idol_card_id'])
        finally:c.db.close()
        trial=deepcopy(self)
        if self.position==7:
            if after['trigger_count']!=0 or after['reaction_count']!=0:
                raise Unsupported('New base item requires explicit zero initial counters')
            trial.state['customize_items']=[deepcopy(after)]
        else:
            old=trial.state['customize_items'][0]
            if after['number']!=old['number']:raise Unsupported('Custom-item physical number changed on upgrade')
            trial.state.setdefault(HISTORY_FIELD,[]).append(dict(deepcopy(old),replaced_by=after['id']))
            trial.state['customize_items'][0]=deepcopy(after)
        trial._validate(trial.state)
        trial.custom_item_offer=None;trial.exam_result_passed=None;trial._advance_schedule()
        trial.events.append(dict(kind='selection_custom_item_result',input=deepcopy(receipt),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)
