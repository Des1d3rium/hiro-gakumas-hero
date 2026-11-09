"""Pinned hand-to-grave redraw and fixed-one specified DeckRandom creation.

Mid-turn MoveCard does not reset support grades or apply end-turn-lost. The
random insertion callback requests [0, current deck count), not count + 1.
Ordered generated-card and off-hand-upgrade posteriors are never resampled as
an independent uniform deck. Owned execution/chance worlds retain exact order.
"""
from .core import Unsupported

GENERATED_PRIOR = 'generated-before-bottom:remaining-order-posterior-required'
POOLED_UPGRADE_PRIOR = 'pooled-support-upgrade:physical-version-posterior-required'


def targets(value):
    if isinstance(value, dict):
        if value.get('op') == 'create_deck_random':
            yield value['definition_id']
        for item in value.values():
            yield from targets(item)
    elif isinstance(value, list):
        for item in value:
            yield from targets(item)


def pool_candidates(value):
    """create_hand_pool 的候选（不进 targets：候选按计划筛选、未移植的跳过，不要求全部可执行）。"""
    if isinstance(value, dict):
        if value.get('op') == 'create_hand_pool':
            yield from value['candidates']
        for item in value.values():
            yield from pool_candidates(item)
    elif isinstance(value, list):
        for item in value:
            yield from pool_candidates(item)


def eligible_pool(battle, candidates):
    """对局里实际可生成的候选：组装文档时已按计划筛选并编译进 definitions 的那些。"""
    return [c for c in candidates if c['definition_id'] in battle.definitions]


def admit_pool_candidates(adapter, definitions, roots, plan):
    """把 create_hand_pool 的候选编译进 definitions。

    plan：偶像计划（ProducePlanType_*）；只收 Common 与该计划的卡（原生 GetSearchCardList 的计划筛选）。
    plan=None（中立沙盒，没有偶像）时收全部计划。
    近似：引擎还不支持的候选直接跳过，不参与抽选，相当于在可执行候选里重新按 ratio 抽——
    实机抽到未移植卡时预演会偏离（本模拟只用于预演/比较，不宣称逐帧一致）。"""
    pending = list(pool_candidates(roots))
    seen = set()
    while pending:
        candidate = pending.pop()
        key = candidate['definition_id']
        if key in seen or key in definitions:
            seen.add(key)
            continue
        seen.add(key)
        if plan is not None and candidate['plan'] not in ('ProducePlanType_Common', plan):
            continue
        identity, grade = key.rsplit('@', 1)
        try:
            admitted = {key: adapter.card(identity, int(grade))}
            nested = list(targets(admitted[key]))
            while nested:
                target = nested.pop()
                if target in definitions or target in admitted:
                    continue
                name, level = target.rsplit('@', 1)
                admitted[target] = adapter.card(name, int(level))
                nested.extend(targets(admitted[target]))
        except Unsupported:
            continue
        definitions.update(admitted)
        pending.extend(pool_candidates(list(admitted.values())))
    return definitions


def closure(definitions, roots, extra=()):
    pending = list(roots) + list(targets(extra))
    pending += [c['definition_id'] for c in pool_candidates(extra) if c['definition_id'] in definitions]
    result = set()
    while pending:
        key = pending.pop()
        if key in result:
            continue
        if key not in definitions:
            raise Unsupported('Missing generated-card definition: ' + str(key))
        result.add(key)
        pending.extend(targets(definitions[key]['effects']))
        pending.extend(c['definition_id'] for c in pool_candidates(definitions[key]['effects']) if c['definition_id'] in definitions)
    return result


def redraw(battle):
    cards = list(battle.zones['hand'])
    virtual = battle.unknown_draws if battle.known_only else 0
    fetched = getattr(battle,'fetch_uncertain_hand_slots',0) if battle.known_only else 0
    # Generic MoveCard-to-Grave is not ResetHand/MovePlayCard: support IDs and
    # temporary exam grades remain on discarded physical cards.
    battle.zones['hand'].clear()
    battle.zones['grave'].extend(cards)
    if battle.known_only:
        from .tag_fetch import returned_visible
        for card in cards:
            returned_visible(battle, card)
            key = battle.original_card_id(card)
            battle.hidden_card_counts[key] = battle.hidden_card_counts.get(key, 0) + 1
        battle.hidden_count += len(cards) + virtual
        battle.unknown_draws = 0
        # An unresolved fetch reserved a hand slot and a possible removal,
        # without reducing hidden_count or identifying a card. Returning it
        # cancels that reservation, not a guessed physical card identity.
        if fetched>battle.possible_hidden_removals:
            raise Unsupported('Inconsistent unresolved fetch reservation')
        battle.possible_hidden_removals-=fetched
        battle.fetch_uncertain_hand_slots=0
    requested=len(cards)+virtual+fetched
    received = battle._draw(requested)
    battle.card_mutation_events.append(dict(kind='discard-redraw', discarded=cards,
                                           virtual_discarded=virtual+fetched, requested=requested,
                                           drawn=list(received or [])))


def create(battle, definition_id):
    if definition_id not in battle.definitions:
        raise Unsupported('Missing generated-card definition')
    key = 'created-' + str(len(battle.cards))
    while key in battle.cards:
        key += '-copy'
    count = len(battle.zones['deck'])
    if not battle.known_only and any(battle.definition(c).get('order',0)>0 for c in battle.zones['deck']):
        raise Unsupported('Fixed-order random insertion path not restored')
    if battle.known_only:
        count = battle.hidden_count
        index = None
    else:
        # Equal bounds still consume one native RNG state; fixed quantity one
        # does not consume a separate count draw in CardCreateIdEffectExecutor.
        index = battle._random_insert_index(count)
    battle.cards[key] = definition_id
    battle.support_bases[key] = definition_id
    if battle.known_only:
        battle.zones['deck'].append(key)
        battle.hidden_count += 1
        original = battle.original_card_id(key)
        battle.hidden_card_counts[original] = battle.hidden_card_counts.get(original, 0) + 1
        if hasattr(battle, 'fetch_projection_piles'):
            battle.fetch_projection_piles['deck'].append((definition_id, key))
            battle.fetch_projection_uncertain = True
            battle.fetch_projection_prefix = []
        battle.known_deck_prefix = []
    else:
        battle.zones['deck'].insert(index, key)
        battle.known_deck_prefix = list(battle.zones['deck']) if count <= 1 else []
    if count > 1:
        battle.deck_prior_issue = GENERATED_PRIOR
    battle.card_mutation_events.append(dict(kind='create-deck-random', instance=key,
                                           definition_id=definition_id, deck_count_before=count,
                                           index=index, projection=battle.known_only))


def create_hand(battle, candidates):
    """create_hand_pool：从可生成候选按 ratio 随机取 1 张，生成到手牌。

    - 抽选只用对局 RNG 按权重取一个下标，不复刻原生两遍 signed 排序键的 RNG 消耗（实机局面恢复时山札本就重洗，
      不追求与原生随机序列对齐）。
    - 手牌满时按引擎里检索进手牌的约定（tag_fetch.apply、原生 InsertCardTop）放到山札最上面。
    - 进手牌时和检索一样走支援卡 HandAdd 判定（support_upgrades.process_batch）；这一点没有实机日志核对。"""
    if battle.known_only:
        raise Unsupported('Random-pool hand creation would reveal a hidden outcome in visible-only search')
    pool = eligible_pool(battle, candidates)
    if not pool:
        raise Unsupported('No executable random-pool candidate admitted')
    index = battle._pool_pick_index([c['weight'] for c in pool])
    definition_id = pool[index]['definition_id']
    key = 'created-' + str(len(battle.cards))
    while key in battle.cards:
        key += '-copy'
    battle.cards[key] = definition_id
    battle.support_bases[key] = definition_id
    if len(battle.zones['hand']) < battle.hand_limit:
        battle.zones['hand'].append(key)
        from .support_upgrades import process_batch
        process_batch(battle, [key])
        destination = 'hand'
    else:
        battle.zones['deck'].insert(0, key)
        battle.known_deck_prefix.insert(0, key)
        destination = 'deck-front'
    battle.card_mutation_events.append(dict(kind='create-hand-pool', instance=key, definition_id=definition_id,
                                           candidates=len(pool), destination=destination))
