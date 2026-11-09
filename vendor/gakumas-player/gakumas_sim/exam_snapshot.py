"""Public, order-independent projection of an explicitly decoded exam save.

Acquisition does not imply the state is admitted by Battle. Native RNG, hidden
deck order, future NPC scores and private logs never enter this projection.
"""
from copy import deepcopy
import hashlib
import json

from .core import Unsupported

ZONE_FIELDS = {'hand': 'handList', 'deck': 'deckList', 'grave': 'graveList', 'lost': 'lostList',
               'hold': 'holdList', 'future': 'futureDeckList', 'past': 'pastDeckList', 'removed': 'removedCardList'}
SCALARS = ('examType', 'settingId', 'characterId', 'produceId', 'idolCardId', 'stepType', 'mainEffectType',
           'displayMainEffectType', 'planType', 'phase', 'currentTurn', 'limitTurn', 'remainTurn', 'extraTurn',
           'stamina', 'maxStamina', 'parameter', 'block', 'producePoint', 'isTurnCardPlayEnd', 'turnCardPlayCount',
           'examCardPlayCount', 'isTurnCardGrave', 'isTurnCardLost', 'currentTurnTotalConsumeStamina',
           'currentTurnTotalAddParameter', 'currentTurnTotalBlock', 'totalConsumeStamina', 'totalDrawCardCount',
           'vocalBonusPermil', 'danceBonusPermil', 'visualBonusPermil', 'vocalConfigParameter',
           'danceConfigParameter', 'visualConfigParameter')
CARD_FIELDS = ('_baseUpgradeCount', '_tmpUpgradeCount', '_supportUpgradeIdList', '_statusEffect',
               '_growEffectExamStartAfterList', '_affectGrowEffectIdList', '_playCount',
               '_isMoveProduceExamEffectUseInTurn', '_staminaConsumptionSpecifyEffectList')
STATUS_FIELDS = ('_isTurnLimited', '_turn', '_isPassingTurnStart', '_uid', '_value', '_overrideType',
                 '_isItemDirectEnchant', '_startEnchantOriginId', '_startEnchantOriginLevel',
                 '_startEnchantOwnerId', '_startEnchantOriginType', '_trigger', '_effectList', '_turnCount',
                 '_phaseCountDictionary', '_limitCount', '_limitCountInTurn', '_limitCountInTurnRemain',
                 '_statusEnchantId', '_fromExamEffectIdList', '_descriptionReactiveDataTypeList',
                 '_descriptionReactiveDataTextList', '_originType', '_isFromEnchantEffect', '_isEncoreEnchant',
                 '_count',  # _count：AntiDebuffStatusEffect 剩余层数；SearchPlayCardStaminaConsumptionChange 剩余次数
                 '_searchId', '_fromExamEffectId', '_descriptions')  # SearchPlayCardStaminaConsumptionChangeStatusEffect（3.4.1 元数据）
STATUS_SOURCES = ('_triggerItem', '_triggerDrink', '_triggerCard', '_triggerGimmickGroup')
PUBLIC_FIELDS = {'schema_version', 'scope', 'source', 'scalars', 'cards', 'zones', 'active_statuses',
                 'idol_status', 'drinks', 'items', 'supports', 'turn_used_supports',
                 'current_parameter_type', 'engine_admitted', 'omitted', 'public_sha256'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def require(obj, keys, label):
    if not isinstance(obj, dict) or not set(keys) <= set(obj):
        raise Unsupported('Incomplete ' + label)


def card_record(card):
    require(card, ('_cardData', '_guid') + CARD_FIELDS, 'exam card')
    data = card['_cardData']
    require(data, ('_id', '_upgradeCount', '_customizeCountList'), 'card version')
    if not isinstance(card['_guid'], str) or not isinstance(data['_id'], str):
        raise Unsupported('Invalid physical card identity')
    if not card['_guid'] or not data['_id']:
        raise Unsupported('Empty card in a live zone')
    for k in ('_baseUpgradeCount', '_tmpUpgradeCount'):
        if type(card[k]) is not int or card[k] < 0:
            raise Unsupported('Invalid native card grade')
    if type(data['_upgradeCount']) is not int or data['_upgradeCount'] < 0:
        raise Unsupported('Invalid effective card grade')
    if not isinstance(data['_customizeCountList'], list) or any(type(v) is not int or v < 0 for v in data['_customizeCountList']):
        raise Unsupported('Invalid customization levels')
    # GUID identity is stable; its position in the hidden list is not exported.
    return dict(instance='card-' + hashlib.sha256(card['_guid'].encode()).hexdigest()[:24],
                card_id=data['_id'], effective_upgrade=data['_upgradeCount'],
                customize_counts=list(data['_customizeCountList']),
                native={k: deepcopy(card[k]) for k in CARD_FIELDS})


def active_statuses(document):
    require(document['status'], ('_effectList', '_effectCreateCount', '_idolStatusEffect'), 'native status collection')
    refs = document['references']
    require(refs, ('version', 'RefIds'), 'managed references')
    if refs['version'] != 2 or not isinstance(refs['RefIds'], list):
        raise Unsupported('Unknown managed reference format')
    by_id = {}
    for row in refs['RefIds']:
        require(row, ('rid', 'type', 'data'), 'managed reference')
        if type(row['rid']) is not int or row['rid'] in by_id:
            raise Unsupported('Duplicate/invalid managed reference')
        by_id[row['rid']] = row
    result = []
    for entry in document['status']['_effectList']:
        require(entry, ('rid',), 'active reference')
        if entry['rid'] not in by_id:
            raise Unsupported('Dangling active status reference')
        row = by_id[entry['rid']]
        if row['type'].get('ns') != 'Campus.InGame.Exam' or row['type'].get('asm') != 'Assembly-CSharp':
            raise Unsupported('Unknown active status provider')
        raw = row['data']
        unknown = set(raw) - set(STATUS_FIELDS) - set(STATUS_SOURCES)
        if unknown:
            raise Unsupported('Unprojected active status fields: ' + ','.join(sorted(unknown)))
        data = {k: deepcopy(raw[k]) for k in STATUS_FIELDS if k in raw}
        for key in STATUS_SOURCES:
            if key not in raw:
                continue
            source = raw[key]
            if key == '_triggerCard':
                if source.get('_guid') and source.get('_cardData', {}).get('_id'):
                    data[key] = card_record(source)
                else:
                    data[key] = None
            else:
                data[key] = deepcopy(source)
        result.append(dict(type=deepcopy(row['type']), data=data))
    return result


def project(document, *, client_version, master_commit):
    require(document, SCALARS + tuple(ZONE_FIELDS.values()) + ('playingCard', 'status', 'references',
            'drinkList', 'itemList', 'supportCardList', 'turnUseSupportCardIdList', 'turnStatusParameterTypeList'), 'exam snapshot')
    if document['produceId'] not in ('produce-007', 'produce-008'):
        raise Unsupported('This snapshot adapter is scoped to HIF selection/finals')
    string_fields = {'settingId', 'characterId', 'produceId', 'idolCardId'}
    boolean_fields = {'isTurnCardPlayEnd', 'isTurnCardGrave', 'isTurnCardLost'}
    for key in SCALARS:
        expected_type = str if key in string_fields else bool if key in boolean_fields else int
        if type(document[key]) is not expected_type:
            raise Unsupported('Invalid snapshot scalar: ' + key)
    if not 0 <= document['stamina'] <= document['maxStamina'] or document['maxStamina'] <= 0:
        raise Unsupported('Invalid snapshot stamina')
    if document['currentTurn'] < 1 or document['parameter'] < 0 or document['block'] < 0:
        raise Unsupported('Invalid snapshot turn/score/block')
    attributes = document['turnStatusParameterTypeList']
    if (not isinstance(attributes, list) or len(attributes) < document['currentTurn']
            or any(type(v) is not int or v not in (1, 2, 3) for v in attributes)):
        raise Unsupported('Invalid native turn attribute list')
    if document['futureDeckList'] or document['pastDeckList']:
        raise Unsupported('Future/past deck buffer visibility not yet restored')
    zones = {}
    cards = {}
    removed_rows = []
    for zone, key in ZONE_FIELDS.items():
        if not isinstance(document[key], list):
            raise Unsupported('Invalid native card zone')
        values = [card_record(c) for c in document[key]]
        if zone == 'removed':
            # removedCardList 不是物理牌区：实机存档里它存的是已离开 / 仍在其他牌区的牌的历史副本（同 guid，较早的
            # 出牌数 / 临时强化），跨回合一直留着，且与 grave / lost / hand / deck / playing 里的现役牌重叠。
            # 下面在所有牌区和 playing 都登记之后再处理：重叠的作为陈旧副本，不重复登记；没有现役牌的才算真正的除外牌。
            removed_rows = values
            zones[zone] = []
            continue
        for row in values:
            identity = row['instance']
            if identity in cards:
                raise Unsupported('Physical card occurs in multiple zones')
            cards[identity] = row
        # Hand order is visible. Hidden deck order is explicitly discarded.
        zones[zone] = [r['instance'] for r in values]
        if zone != 'hand':
            zones[zone].sort()
    playing = document['playingCard']
    if playing.get('_guid') or playing.get('_cardData', {}).get('_id'):
        row = card_record(playing)
        if row['instance'] in cards:
            raise Unsupported('Playing card also occurs in another zone')
        cards[row['instance']] = row
        zones['playing'] = [row['instance']]
    else:
        zones['playing'] = []
    for row in removed_rows:
        live = cards.get(row['instance'])
        if live is None:
            cards[row['instance']] = row
        elif live['card_id'] != row['card_id']:
            raise Unsupported('Removed-card copy disagrees with the live card')
        zones['removed'].append(row['instance'])
    zones['removed'].sort()
    result = dict(schema_version=1, scope='public-hif-exam-acquisition',
                  source=dict(client_version=client_version, master_commit=master_commit),
                  scalars={k: deepcopy(document[k]) for k in SCALARS},
                  cards={k: cards[k] for k in sorted(cards)}, zones=zones,
                  active_statuses=active_statuses(document),
                  idol_status=deepcopy(document['status']['_idolStatusEffect']),
                  drinks=deepcopy(document['drinkList']), items=deepcopy(document['itemList']),
                  supports=deepcopy(document['supportCardList']),
                  turn_used_supports=deepcopy(document['turnUseSupportCardIdList']),
                  current_parameter_type=attributes[document['currentTurn'] - 1],
                  engine_admitted=False,
                  omitted=['random', 'hidden-zone-order', 'native-future-attributes', 'NPC-future-scores', 'private-logs',
                           'current-turn-draw-history', 'raw-save-fingerprint'])
    # Fingerprint depends only on this explicitly public projection.
    result['public_sha256'] = hashlib.sha256(canonical(result).encode('utf8')).hexdigest()
    return result
