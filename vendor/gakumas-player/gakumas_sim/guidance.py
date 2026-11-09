"""HIF selection special guidance with explicit current branch offers."""
from copy import deepcopy
from .catalog import Catalog
from .core import Unsupported
from .finals import exact,source,text
from .growth_scaling import checked
from .interval_customize import validate_service,select_target
from .master_adapter import MasterAdapter


class GuidanceMixin:
    def _guidance_binding(self,binding,fields):
        exact(binding,{'provenance','position','state_digest','unmodelled_effects'}|fields,'Special-guidance binding')
        source(binding['provenance'])
        if type(binding['position']) is not int or binding['position']!=self.position or binding['state_digest']!=self.state_digest():
            raise Unsupported('Special guidance belongs to another date/state')
        if binding['unmodelled_effects']!=[]:
            raise Unsupported('Unmodelled special-guidance effects')

    def open_guidance(self,binding):
        stage=self._stage()
        if 'special_guidance' not in stage['options'] or self.current_event is not None or self.pending:
            raise Unsupported('Special guidance is not the current calendar event')
        self._guidance_binding(binding,set())
        trial=deepcopy(self)
        trial._supports('StartCustomize')
        trial._validate(trial.state)
        trial.guidance=dict(snapshot=None,seen=[],active_instances=None,additional_card_count=None)
        trial.phase='guidance_await_progress'
        trial.events.append(dict(kind='guidance_open',input=deepcopy(binding),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def install_guidance(self,snapshot):
        if self.phase!='guidance_await_progress' or self.guidance is None:
            raise Unsupported('No special-guidance progress is pending')
        self._guidance_binding(snapshot,{'id','can_finish','additional_card_count','service'})
        text(snapshot['id'],'Guidance progress ID')
        if snapshot['id'] in self.guidance['seen'] or type(snapshot['can_finish']) is not bool:
            raise Unsupported('Stale or invalid guidance progress')
        extra=checked(snapshot['additional_card_count'],'Additional customize card count')
        if self.guidance['additional_card_count'] is not None and extra!=self.guidance['additional_card_count']:
            raise Unsupported('Additional guidance slots changed without a modeled effect')
        c=Catalog()
        try:
            setting=c.one('ProduceSetting',c.one('Produce',self.produce_id)['produceSettingId'])
            limit=checked(setting['customizeProduceCardCount']+extra,'Total guidance card count')
            validate_service(snapshot['service'],{'0'},self.state,self.guidance['active_instances'])
            if len(snapshot['service']['active_instances'])>limit:
                raise Unsupported('Active guidance cards exceed current limit')
        finally:c.db.close()
        trial=deepcopy(self)
        trial.guidance.update(snapshot=deepcopy(snapshot),active_instances=list(snapshot['service']['active_instances']),
                              additional_card_count=extra,card_limit=limit)
        trial.guidance['seen'].append(snapshot['id']);trial.phase='guidance'
        trial.events.append(dict(kind='guidance_progress',input=deepcopy(snapshot)))
        self.__dict__.update(trial.__dict__)

    def customize_guidance(self,action):
        if self.phase!='guidance' or self.guidance is None:
            raise Unsupported('No current special-guidance selector')
        exact(action,{'progress_id','instance','customize_id'},'Guidance action')
        snapshot=self.guidance['snapshot']
        if action['progress_id']!=snapshot['id'] or snapshot['state_digest']!=self.state_digest():
            raise Unsupported('Stale guidance action')
        text(action['instance'],'Guidance card');text(action['customize_id'],'Guidance branch')
        if action['instance'] not in self.state['cards']:raise Unsupported('Guidance card is not owned')
        trial=deepcopy(self);old=deepcopy(trial.state['cards'][action['instance']]);c=Catalog()
        try:
            new,active,price,detail=select_target(c,MasterAdapter(c),snapshot['service'],0,
                action['instance'],action['customize_id'],old,card_limit=trial.guidance['card_limit'])
        finally:c.db.close()
        if price>trial.state['produce_points']:raise Unsupported('Not enough produce points')
        trial.state['produce_points']-=price;trial.state['cards'][action['instance']]=new
        trial._supports('CustomizeProduceCard',dict(card=old));trial._validate(trial.state)
        trial.guidance['active_instances']=active;trial.guidance['snapshot']=None
        trial.phase='guidance_await_progress'
        trial.events.append(dict(kind='guidance_customize',action=deepcopy(action),price=price,details=detail,after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)

    def end_guidance(self,binding):
        if self.phase!='guidance' or self.guidance is None:
            raise Unsupported('No current special guidance can end')
        self._guidance_binding(binding,{'progress_id'})
        snapshot=self.guidance['snapshot']
        if (binding['progress_id']!=snapshot['id'] or not snapshot['can_finish']
            or snapshot['state_digest']!=self.state_digest()):
            raise Unsupported('Guidance completion is not currently allowed')
        trial=deepcopy(self);trial.guidance=None;trial._advance_schedule()
        trial.events.append(dict(kind='guidance_end',input=deepcopy(binding),after=trial.state_digest()))
        self.__dict__.update(trial.__dict__)


def apply_guidance_event(env,event):
    kind=event['kind']
    if kind=='guidance_open':env.open_guidance(event['input'])
    elif kind=='guidance_progress':env.install_guidance(event['input'])
    elif kind=='guidance_customize':env.customize_guidance(event['action'])
    elif kind=='guidance_end':env.end_guidance(event['input'])
    else:raise Unsupported('Unknown special-guidance event')
