"""Ordered DeckGrave tag retrieval; public projections never sample identities.

Native search/filter/order/limit and hand spill evidence:
research/hif-herbal-20261016. Only fixed-one, order-zero retrieval is admitted.
"""
from .core import Unsupported


def has_fetch(definitions):
    def walk(effects):
        return any(e['op']=='fetch_tag' or e['op']=='conditional' and walk(e['effects']) for e in effects)
    return any(walk(d['effects']) for d in definitions)


def matches(battle,tag):
    result=[]
    for zone in ('deck','grave'):
        for card in battle.zones[zone]:
            definition=battle.definition(card)
            if 'search_tag' not in definition:raise Unsupported('Tag retrieval requires known search_tag for every source card')
            if definition['search_tag']==tag:result.append((zone,card))
    return result


def normalize(battle):
    battle.known_deck_prefix=[c for c in battle.known_deck_prefix if c in battle.zones['deck']]
    if battle.zones['deck'][:len(battle.known_deck_prefix)]!=battle.known_deck_prefix:
        raise Unsupported('Known deck prefix disagrees with actual order')
    if len(battle.zones['deck'])<2:battle.deck_prior_issue=None


def apply(battle,tag):
    if battle.known_only:return project(battle,tag)
    candidates=matches(battle,tag)
    if not candidates:return
    zone,card=candidates[0]
    deck_matches=[c for z,c in candidates if z=='deck']
    prefix_match=next((c for c in battle.known_deck_prefix if battle.definition(c)['search_tag']==tag),None)
    ambiguous=zone=='deck' and prefix_match is None and len({battle.cards[c] for c in deck_matches})>1
    if ambiguous:
        battle.deck_prior_issue='tag-first-match:remaining-order-posterior-required'
    battle.zones[zone].remove(card)
    battle.known_deck_prefix=[c for c in battle.known_deck_prefix if c!=card]
    if len(battle.zones['hand'])<battle.hand_limit:
        battle.zones['hand'].append(card)
        from .support_upgrades import process_batch
        process_batch(battle,[card])
        destination='hand'
    else:
        battle.zones['deck'].insert(0,card)
        # Publish only a deducible definition, never an unknown hidden choice.
        if not ambiguous:battle.known_deck_prefix.insert(0,card)
        else:battle.known_deck_prefix=[]
        destination='deck-front'
    battle.fetch_events.append(dict(tag=tag,source=zone,instance=card,destination=destination))
    normalize(battle)


def prepare_projection(model,observation):
    public=observation['public_model']
    model.fetch_projection_piles={z:[(d,None) for d in public[z+'_definition_ids']] for z in ('deck','grave')}
    model.fetch_projection_prefix=list(public.get('known_deck_prefix_definition_ids',[]))
    tail=list(public['deck_definition_ids'])
    for definition in model.fetch_projection_prefix:tail.remove(definition)
    model.fetch_projection_piles['deck']=[(d,None) for d in model.fetch_projection_prefix+tail]
    model.fetch_projection_uncertain=bool(public.get('deck_prior_issue'))
    model.fetch_uncertain_hand_slots=0


def returned_visible(model,card):
    if hasattr(model,'fetch_projection_piles'):
        model.fetch_projection_piles['grave'].append((model.cards[card],card))


def project(model,tag):
    piles=model.fetch_projection_piles
    for rows in piles.values():
        if any('search_tag' not in model.definitions[d] for d,_ in rows):
            raise Unsupported('Public tag projection has unknown search_tag')
    matching={z:[(i,row) for i,row in enumerate(piles[z]) if model.definitions[row[0]]['search_tag']==tag] for z in piles}
    if not matching['deck'] and not matching['grave']:return
    free=model.hand_limit-len(model.zones['hand'])-model.unknown_draws-model.fetch_uncertain_hand_slots
    if model.fetch_projection_uncertain or model.unknown_draws or model.possible_hidden_removals:
        # A visible-only beam cannot identify prior unknown draws. Reserve a
        # possible hand slot, with no draw/card benefit or invented version.
        if free>0:
            model.fetch_uncertain_hand_slots+=1;model.possible_hidden_removals+=1
        model.fetch_projection_uncertain=True
        model.fetch_events.append(dict(tag=tag,projection='unresolved-no-card-materialized'))
        return
    source='deck' if matching['deck'] else 'grave'
    options=matching[source]
    if source=='deck':
        prefix=next((d for d in model.fetch_projection_prefix if model.definitions[d]['search_tag']==tag),None)
        if prefix is not None:options=[next(r for r in options if r[1][0]==prefix)]
        elif len({row[0] for _,row in options})>1:
            model.fetch_projection_uncertain=True
            return project(model,tag)
    index,(definition,known)=options[0]
    piles[source].pop(index)
    if source=='deck' and definition in model.fetch_projection_prefix:
        model.fetch_projection_prefix.remove(definition)
    if known is not None:
        model.zones[source].remove(known)
    if free<=0:
        piles['deck'].insert(0,(definition,known))
        model.fetch_projection_prefix.insert(0,definition)
        if known is not None:model.zones['deck'].insert(0,known)
        model.fetch_events.append(dict(tag=tag,projection='known-definition-deck-front'))
        return
    card=known
    if card is None:
        card='visible-fetch-'+str(len(model.fetch_events))
        while card in model.cards:card+='-copy'
        model.cards[card]=definition;model.support_bases[card]=definition
    model.zones['hand'].append(card);model.hidden_count-=1
    original=model.original_card_id(card)
    model.hidden_card_counts[original]=max(0,model.hidden_card_counts.get(original,0)-1)
    # Same convention as unknown-draw projection: no future support RNG is
    # guessed. Actual Battle and sampled worlds execute the HandAdd trials.
    model.fetch_events.append(dict(tag=tag,projection='deducible-base-version-no-future-support-rolls',instance=card))
