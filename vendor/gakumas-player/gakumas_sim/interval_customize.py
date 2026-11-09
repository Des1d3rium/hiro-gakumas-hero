"""Current interval customization offers and per-card (not per-click) limits.

Account eligibility/discounted prices are explicit progress inputs. Permanent
branch semantics still come from the pinned master and bounded card adapter.
"""
from copy import deepcopy
from .core import Unsupported
from .growth_scaling import checked


def exact(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise Unsupported(name + ' has missing or unknown fields')


def validate_service(service, positions, state, expected=None, allowed_drops=()):
    exact(service, {'active_instances', 'offers'}, 'Customize service')
    active = service['active_instances']
    if (not isinstance(active, list) or any(not isinstance(i, str) for i in active)
        or len(active) != len(set(active)) or set(active) - set(state['cards'])):
        raise Unsupported('Customize active instances must be current unique physical cards')
    if expected is not None and (set(active) - set(expected) or set(expected) - set(active) - set(allowed_drops)):
        raise Unsupported('Customize active cards changed without a service, affected-card progress or refresh')
    exact(service['offers'], positions, 'Customize per-product offers')
    for offers in service['offers'].values():
        if not isinstance(offers, list): raise Unsupported('Customize offers must be a list')
        seen = set()
        for offer in offers:
            exact(offer, {'instance', 'id', 'level', 'price'}, 'Customize branch offer')
            if not isinstance(offer['instance'], str) or offer['instance'] not in state['cards']:
                raise Unsupported('Customize offer needs a current physical card')
            if not isinstance(offer['id'], str) or not offer['id'].strip():
                raise Unsupported('Customize offer needs a branch id')
            checked(offer['level'], 'customize branch level', 2147483647)
            if offer['level'] < 1: raise Unsupported('Customize level must be positive')
            checked(offer['price'], 'customize final price', 2147483647)
            key = (offer['instance'], offer['id'])
            if key in seen: raise Unsupported('Duplicate customize branch offer')
            seen.add(key)


def select_target(catalog, adapter, service, position, instance, branch, old, *, card_limit=None):
    offers = service['offers'].get(str(position), [])
    matches = [o for o in offers if o['instance'] == instance and o['id'] == branch]
    if len(matches) != 1: raise Unsupported('Branch is outside the current customize selector')
    offer = matches[0]
    limit = (catalog.one('ProduceSetting', 'p_setting-8')['stepIntervalCustomizeProduceCardCount']
             if card_limit is None else checked(card_limit, 'current customize card limit'))
    active = list(service['active_instances'])
    if instance not in active:
        if len(active) >= limit: raise Unsupported('No additional card can be customized in current service')
        active.append(instance)
    new = deepcopy(old)
    existing = next((p for p in new['customizations'] if p['id'] == branch), None)
    level = 1 if existing is None else existing['count'] + 1
    if offer['level'] != level: raise Unsupported('Customize offer does not match next branch level')
    if existing is None: new['customizations'].append(dict(id=branch, count=1))
    else: existing['count'] = level
    # Includes branch availability, total slots, exact grow operations, use
    # conditions and executable card body. Raw catalog rows stay immutable.
    adapter.card(new['card_id'], new['upgrade'], new['customizations'])
    rows = [r for r in catalog.by_id('ProduceCardCustomize', branch) if r['customizeCount'] == level]
    if len(rows) != 1: raise Unsupported('Missing exact customization master level')
    return new, active, offer['price'], dict(instance=instance, id=branch, level=level,
        before=deepcopy(old), after=deepcopy(new), base_price=rows[0]['producePoint'],
        quoted_final_price=offer['price'], active_card_count=len(active), card_limit=limit)
