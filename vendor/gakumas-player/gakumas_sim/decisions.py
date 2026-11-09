"""One-step out-of-battle environment. All deltas are explicit scenario data."""
from copy import deepcopy
from .core import Unsupported, IllegalAction, validate_definition


class Decision:
    def __init__(self, document):
        allowed_fields={'schema_version','id','provenance','module','state','context','definitions','options','expected_actions','allow_skip'}
        if document.get('schema_version')!=1 or set(document)-allowed_fields:
            raise Unsupported('Unknown decision schema/fields')
        if document.get('provenance', {}).get('kind') != 'synthetic':
            raise Unsupported('Out-of-battle fixtures must have explicit synthetic provenance')
        if document['module'] not in ('training', 'drinks', 'card_pick', 'card_remove'):
            raise Unsupported('Unknown decision module')
        self.document = deepcopy(document)
        ids = [o['id'] for o in document['options']]
        if len(set(ids)) != len(ids) or 'skip' in ids:
            raise Unsupported('Duplicate/reserved option ids')
        for definition in document.get('definitions', {}).values():
            validate_definition(definition)
        if any(key not in document.get('definitions', {}) for key in document['state'].get('owned', {}).values()):
            raise Unsupported('Missing owned card definition')
        allowed = {'id','kind','cost','hp_delta','stat_delta','gain_card','remove_card','gain_drink','sp'}
        for option in document['options']:
            if set(option)-allowed or option['kind'] not in ('train','lesson','pick','remove','swap','drink','recover'):
                raise Unsupported('Unknown decision option')
            if option.get('cost',0) < 0:
                raise Unsupported('Invalid option cost')
            if option.get('gain_card') not in (None, *document.get('definitions', {})):
                raise Unsupported('Missing offered card definition')
            if option.get('gain_drink') and 'drink_capacity' not in document.get('context', {}):
                raise Unsupported('Drink acquisition requires an explicit capacity')

    def reset(self, seed=1):
        self.state = deepcopy(self.document['state'])
        self.done, self.events = False, []
        return self.observe()

    def legal_actions(self):
        if self.done:
            return []
        options = self.document['options']
        result = [dict(kind='choose', id=o['id']) for o in options
                  if o.get('cost',0) <= self.state['p_points']
                  and self.state['hp']+o.get('hp_delta',0) >= 0
                  and (not o.get('gain_drink') or len(self.state.get('drinks', [])) < self.document['context']['drink_capacity'])
                  and (not o.get('remove_card') or o['remove_card'] in self.state.get('owned', {}))]
        if self.document.get('allow_skip', True):
            result.append(dict(kind='choose',id='skip'))
        return result

    def observe(self):
        # Teacher answers, private annotations and seed never enter this view.
        return dict(mode='decision', module=self.document['module'], state=deepcopy(self.state),
                    context=deepcopy(self.document.get('context', {})),
                    definitions=deepcopy(self.document.get('definitions', {})),
                    options=deepcopy(self.document['options']), done=self.done)

    def step(self, action, reason='external'):
        if action not in self.legal_actions():
            raise IllegalAction('Unknown/unaffordable decision')
        before, state, legal = self.observe(), deepcopy(self.state), self.legal_actions()
        if action['id'] != 'skip':
            option = next(o for o in self.document['options'] if o['id']==action['id'])
            state['p_points'] -= option.get('cost',0)
            state['hp'] = min(state['max_hp'], state['hp']+option.get('hp_delta',0))
            for key, value in option.get('stat_delta', {}).items():
                updated=state['stats'].get(key,0)+value
                cap = self.document.get('context', {}).get('caps', {}).get(key, updated)
                state['stats'][key] = min(cap, updated)
            if option.get('remove_card'):
                del state['owned'][option['remove_card']]
            if option.get('gain_card'):
                owned=state.setdefault('owned', {});identity='acquired-'+option['id']
                while identity in owned:identity+='-copy'
                owned[identity] = option['gain_card']
            if option.get('gain_drink'):
                state.setdefault('drinks', []).append(option['gain_drink'])
        self.state, self.done = state, True
        self.events.append(dict(before=before, legal=legal, action=deepcopy(action), reason=reason, after=self.observe()))
        return self.observe()
