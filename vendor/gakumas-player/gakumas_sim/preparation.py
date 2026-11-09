"""Local decisions for observed HIF selection/finals preparation events.

No screenshot acquisition, input synthesis, candidate generation or LLM calls.
The UI adapter supplies complete current candidates and committed state tokens.
"""
from copy import deepcopy
import json
import sys

from .catalog import Catalog
from .core import Unsupported
from .hif import dispatch, integer
from .policies import SkillPolicy
from .schedule import HIFSchedule
from .selection_start import project_memory, bind_start


LOCAL_KINDS = {
    'outing_choice': ('training', {'outing'}),
    'card_upgrade': ('hif_upgrade', {'class', 'training', 'outing', 'gift', 'shop', 'interval', 'exam'}),
    'card_reroll': ('hif_reroll', {'class', 'outing', 'gift', 'shop', 'interval', 'exam'}),
    'guidance_choice': ('hif_guidance', {'special_guidance'}),
}
SKILL_MODULES = {'training', 'card_pick', 'card_remove', 'drinks'}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise Unsupported('Missing ' + name)
    return value


def _candidates(event):
    rows = event.get('candidates')
    if not isinstance(rows, list) or not rows:
        raise Unsupported('Need complete observed candidates, including decline/end if offered')
    ids = [_text(r.get('id'), 'candidate ID') for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)):
        raise Unsupported('Candidate IDs must be unique')
    if event.get('candidates_complete') is not True:
        raise Unsupported('Candidate completeness is not confirmed')
    return {r['id']: r for r in rows}


def _binding(profile, mode, position, event):
    bindings = profile.get('bindings', [])
    if not isinstance(bindings, list) or any(not isinstance(r, dict) for r in bindings):
        raise Unsupported('Expected profile binding objects')
    matches = [r for r in bindings
               if (r.get('mode'), r.get('position'), r.get('kind'), r.get('source_action')) ==
               (mode, position, event['kind'], event['source_action'])]
    if len(matches) > 1:
        raise Unsupported('Ambiguous local profile binding')
    return matches[0] if matches else None


def plan_preparation(document, profile=None, *, policy=None, catalog=None):
    """Return one local action or a typed handoff. Never advance the calendar."""
    if not isinstance(document, dict) or profile is not None and not isinstance(profile, dict):
        return dict(schema_version=1, status='needs_review', reason='Expected observation/profile objects', llm_requests=0)
    profile = profile or {}
    result = {'schema_version': 1, 'llm_requests': 0,
              'run_id': document.get('run_id'), 'state_token': document.get('state_token')}
    own = catalog is None
    c = catalog or Catalog()
    try:
        _text(document.get('run_id'), 'run ID')
        _text(document.get('state_token'), 'committed state token')
        mode = document.get('mode')
        position = integer(document.get('position'), 'position', 1)
        event = document.get('event', {})
        if not isinstance(event, dict): raise Unsupported('Expected event object')
        _text(event.get('id'), 'event ID')
        kind = _text(event.get('kind'), 'event kind')
        result.update(mode=mode, position=position, event_id=event['id'])
        if kind == 'memory_transfer':
            if mode != 'produce-007' or position != 21:
                raise Unsupported('Memory transfer belongs after selection day20')
            projection = project_memory(document['memory_document'], catalog=c)
            progress = document.get('start_progress')
            bound = bind_start(document['memory_document'], progress) if progress is not None else None
            return {**result, 'status': 'memory_handoff', 'projection': projection,
                    'validated_start': bound, 'requires_start_progress': progress is None}

        stage = HIFSchedule(mode, c).stage(position)
        source = _text(event.get('source_action'), 'event source')
        resources = document.get('resources', {})
        if not isinstance(resources, dict): raise Unsupported('Expected observed resource object')
        if kind in LOCAL_KINDS:
            module, allowed = LOCAL_KINDS[kind]
            if source not in stage['options'] or source not in allowed:
                raise Unsupported('Nested event source is not open here')
        elif (kind in {'card_offer', 'drink_offer'} and source == 'training'
              and event.get('origin') == 'support' and source in stage['options']):
            module = 'card_pick' if kind == 'card_offer' else 'drinks'
        else:
            module = dispatch(mode, position, event, hp=resources.get('hp'),
                              max_hp=resources.get('max_hp'), catalog=c)['module']
        result.update(day=stage['day'], module=module)
        if module == 'play_cards+drinks' or (kind == 'action_choice' and stage['kind'] in {'exam', 'round1', 'round2'}):
            return {**result, 'status': 'battle_handoff', 'exam_number': stage.get('exam_number'),
                    'source_action': source}
        if module == 'verify_outcome':
            return {**result, 'status': 'settlement_handoff', 'source_action': source}

        candidates = _candidates(event)
        if kind == 'action_choice':
            for row in candidates.values():
                route = row.get('source_action')
                if route == 'rest':
                    if (not stage.get('rest_when_wounded') or
                        not 0 <= integer(resources.get('hp'), 'hp') < integer(resources.get('max_hp'), 'max_hp', 1)):
                        raise Unsupported('Rest needs observed wounded state')
                elif route not in stage['options']:
                    raise Unsupported('Route candidate is not open on this date')
        binding = _binding(profile, mode, position, event)
        reserve = integer((binding or {}).get('reserve_p', 0), 'reserved P')
        money = source in {'shop', 'interval'} and kind != 'action_choice'
        if money:
            balance = integer(resources.get('p_points'), 'observed P balance')
            for row in candidates.values():
                integer(row.get('cost'), 'current quoted cost')

        observation = document.get('policy_observation')
        if observation is not None:
            if not isinstance(observation, dict): raise Unsupported('Expected local policy observation object')
            if module not in SKILL_MODULES or observation.get('mode') != 'decision' or observation.get('module') != module:
                raise Unsupported('Policy observation module differs from current event')
            if document.get('policy_state_token') != document['state_token']:
                raise Unsupported('Policy input is bound to another committed state')
            options = observation.get('options', [])
            ids = [r['id'] for r in options]
            if len(ids) != len(set(ids)) or set(ids) != set(candidates) - {'skip'}:
                raise Unsupported('Policy options differ from observed candidates')
            if any(row != candidates[row['id']].get('policy_option') for row in options):
                raise Unsupported('Policy effects differ from observed candidate binding')
            for field in ('hp', 'max_hp', 'p_points'):
                if field in resources and observation.get('state', {}).get(field) != resources[field]:
                    raise Unsupported('Policy resources differ from current observed resources')
            if money:
                if observation.get('state', {}).get('p_points') != balance:
                    raise Unsupported('Policy balance differs from observed balance')
                if any(row.get('cost') != candidates[row['id']]['cost'] for row in options):
                    raise Unsupported('Policy quote differs from current quote')
            legal = [dict(kind='choose', id=key) for key in candidates]
            action, reason = (policy or SkillPolicy()).choose(deepcopy(observation), legal)
            if action not in legal:
                raise Unsupported('Local skill returned an unobserved action')
            key = action['id']
        elif len(candidates) == 1:
            key = next(iter(candidates)); reason = 'single-observed-legal-candidate'
        elif binding is not None:
            priority = binding.get('priority')
            if not isinstance(priority, list) or not priority or any(not isinstance(k, str) for k in priority):
                raise Unsupported('Need an explicit local candidate priority')
            affordable = [k for k in priority if k in candidates and
                          (not money or candidates[k]['cost'] == 0 or candidates[k]['cost'] + reserve <= balance)]
            if not affordable:
                raise Unsupported('No configured candidate is currently offered and affordable')
            key = affordable[0]; reason = 'explicit-local-profile'
        else:
            raise Unsupported('No local rule for offered event; supply a profile or bound existing-skill input')
        if money and candidates[key]['cost'] > 0 and candidates[key]['cost'] + reserve > balance:
            raise Unsupported('Selected action exceeds current P budget or reserve')
        return {**result, 'status': 'action', 'candidate_id': key, 'reason': reason,
                'source_action': candidates[key]['source_action'] if kind == 'action_choice' else source,
                'event_source_action': source, 'quoted_cost': candidates[key].get('cost')}
    except (Unsupported, KeyError, TypeError, ValueError, AttributeError) as error:
        return {**result, 'status': 'needs_review', 'reason': str(error)}
    finally:
        if own:
            c.db.close()


def main():
    """One NDJSON request/reply per event; reuse policy and read-only catalog."""
    c = Catalog()
    policy = SkillPolicy()
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict): raise ValueError('Expected request object')
                result = plan_preparation(request['observation'], request.get('profile'), policy=policy, catalog=c)
            except (ValueError, KeyError, TypeError) as error:
                result = dict(status='needs_review', reason=str(error), llm_requests=0)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        c.db.close()


if __name__ == '__main__':
    main()
