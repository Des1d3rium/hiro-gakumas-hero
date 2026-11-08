"""H.I.F 培育中随机候选的生成器（v1：资格过滤与价格按 master/社区规则；抽样分布未校准）。

服务器决定的候选（课程后选卡、礼物、商店货架、饮料奖励）本地没有生成公式。这里先用
「确定的资格过滤 + 均匀随机」生成，输出格式与 GrowthSession.receive_offer / open_market 的输入一致，
来源统一标记为 local-fixture / RANDOM_REFERENCE，便于和实机观测区分。之后用实机记录逐项校准。

确定的规则（master 字段或社区明文规则，已在实机看到）：
- 卡牌计划只能是 共通 或 偶像本人的计划（ProduceCard.planType）；
- 不出：麻烦卡（眠気等 Trouble）、开局卡（isInitial / isInitialDeckProduceCard）、偶像固有卡（originIdolCardId）、
  支援卡事件卡（originSupportCardId）、图鉴隐藏（libraryHidden）、制作人等级未解锁（unlockProducerLevel）；
- 「重複不可」（noDeckDuplication）的卡已持有时不再出；
- 被禁用（取得禁止）的卡本次培育不再出；禁用后补一张新候选；禁用、刷新次数有限（run2：禁用 2、刷新 3）；
- 饮料：计划只能是 共通 或本人计划，图鉴隐藏不出；礼物的饮料只有 SR/SSR（社区规则）；
- 普通商店不卖未强化的 R 卡（社区规则，market.py 已据此拒绝）。

价格、轮间、外出、授業、礼物等以社区规则为准（community_rules.py，来源快照见其模块说明）。

仍未校准（ASSUMED，社区也没有，待实机修正）：
- 候选在合格池里均匀抽取，不按稀有度加权；SP 课程奖励是否必带 + 版未知（参数 upgrade 由调用方给）；
- 相談货架几张卡、几瓶饮料（默认 4 卡 2 饮料）；
- 禁用/刷新次数默认值取自 run2 记录，账号与模式可能不同（实机以画面显示为准）。
"""
from __future__ import annotations
from dataclasses import dataclass,field
import random

from .catalog import Catalog
from . import community_rules as rules
from .market import sale_step,effect_step

RANDOM_REFERENCE='random-offers-v1（资格过滤、价格以社区规则为准；抽样分布未校准）'
PROVENANCE=dict(kind='local-fixture',reference=RANDOM_REFERENCE)


def _discounted(price,sale=0,effects=()):
    """客户端折扣顺序：先 SALE，再逐项效果折扣，每层截断（market.current_price 的同一算法）。"""
    if not sale and not effects:return price
    value=sale_step(price,sale)
    for rate in effects:value=effect_step(value,rate)
    return value


def _plan(catalog,idol_card_id):
    return catalog.one('IdolCard',idol_card_id)['planType']


def card_eligible(row,plan,producer_level=None):
    """单张卡是否可能作为候选出现（只看 master 字段，不看持有情况）。"""
    if row['planType'] not in ('ProducePlanType_Common',plan):return False
    if row['category']=='ProduceCardCategory_Trouble':return False
    if row['isInitial'] or row['isInitialDeckProduceCard']:return False
    if row['originIdolCardId'] or row['originSupportCardId']:return False
    if row['libraryHidden']:return False
    if producer_level is not None and row['unlockProducerLevel']>producer_level:return False
    return True


@dataclass
class OfferState:
    """一次培育里的候选状态：已持有、已禁用、剩余禁用/刷新次数（默认值取自 run2 记录，ASSUMED）。"""
    idol_card_id:str
    owned:list=field(default_factory=list)  # 当前持有卡的 card_id（含重复）
    banned:set=field(default_factory=set)
    bans_left:int=2
    rerolls_left:int=3
    producer_level:int|None=None
    upgrades_done:int=0   # 本局相談强化次数（价格递增）
    deletes_done:int=0    # 本局相談删除次数
    hif_card_discount_level:int=0  # H.I.F 加成「相談のスキルカードをN%割引」等级 0–6


class OfferGenerator:
    def __init__(self,state:OfferState,seed=0,catalog=None):
        self.state=state;self.rng=random.Random(seed)
        own=catalog is None;c=catalog or Catalog()
        try:
            self.plan=_plan(c,state.idol_card_id)
            self.cards={r['id']:r for r in c.rows('ProduceCard') if r['upgradeCount']==0 and card_eligible(r,self.plan,state.producer_level)}
            self.drinks={r['id']:r for r in c.rows('ProduceDrink')
                         if r['planType'] in ('ProducePlanType_Common',self.plan) and not r['libraryHidden']}
            self.upgradable={r['id'] for r in c.rows('ProduceCard') if r['upgradeCount']==1}
            idol=c.one('IdolCard',state.idol_card_id)
            self.recommended={k for k,r in self.cards.items() if rules.is_recommended(r,idol)}
        finally:
            if own:c.db.close()

    # ---- 选卡 ----
    def card_pool(self):
        owned=set(self.state.owned)
        return sorted(k for k,r in self.cards.items()
                      if k not in self.state.banned and not (r['noDeckDuplication'] and k in owned))

    def card_offer(self,k=3,upgrade=0,effect_id='random',state_digest=None,recommended_only=False):
        """课程后/活动的选卡候选（GrowthSession.receive_offer 格式）。
        upgrade='random'：每张候选的强化状态随机（外出的普通获得，社区规则）；recommended_only：只出おすすめ効果相关卡。"""
        pool=self.card_pool()
        if recommended_only:pool=[x for x in pool if x in self.recommended]
        if upgrade==1:pool=[x for x in pool if x in self.upgradable]
        picks=self.rng.sample(pool,min(k,len(pool)))
        def up(x):
            if upgrade!='random':return upgrade
            return self.rng.choice((0,1)) if x in self.upgradable else 0
        return dict(provenance=dict(PROVENANCE),state_digest=state_digest,effect_id=effect_id,
                    candidates=[dict(id=f'card-{i}',spec=dict(card_id=x,upgrade=up(x),customizations=[])) for i,x in enumerate(picks)])

    def outing_offer(self,option,state_digest=None):
        """外出（社区规则：内容固定，所选档位的全部效果都获得；卡只出おすすめ効果相关，普通获得的强化状态随机）。
        option 为 community_rules.OUTING_OPTIONS 的下标。返回 dict(cost, recover_permil, offers=[饮料候选, 卡候选...])。"""
        o=rules.OUTING_OPTIONS[option];offers=[]
        for gain in o['gains']:
            if gain=='pick_drink':
                offers.append(self.drink_offer(rarities=tuple(self._drink_rarities()),effect_id=f'outing-{option}-drink',state_digest=state_digest))
            elif gain=='pick_card_upgraded':
                offers.append(self.card_offer(upgrade=1,effect_id=f'outing-{option}-card-upgraded',state_digest=state_digest,recommended_only=True))
            elif gain=='pick_card':
                offers.append(self.card_offer(upgrade='random',effect_id=f'outing-{option}-card',state_digest=state_digest,recommended_only=True))
        return dict(cost=dict(o['cost']),recover_permil=rules.OUTING_RECOVER_PERMIL,offers=offers)

    def _drink_rarities(self):
        return sorted({r['rarity'] for r in self.drinks.values()})

    def reroll(self,offer,k=None):
        if self.state.rerolls_left<=0:raise ValueError('没有刷新次数')
        self.state.rerolls_left-=1
        up=offer['candidates'][0]['spec']['upgrade'] if offer['candidates'] else 0
        return self.card_offer(k or len(offer['candidates']),up,offer['effect_id'],offer['state_digest'])  # 随机强化状态的候选刷新后按第一张的状态（简化）

    def ban(self,offer,candidate_id):
        """取得禁止：该卡本次培育不再出现，并在原位置补一张新候选。"""
        if self.state.bans_left<=0:raise ValueError('没有禁用次数')
        target=next(c for c in offer['candidates'] if c['id']==candidate_id)
        self.state.bans_left-=1;self.state.banned.add(target['spec']['card_id'])
        shown={c['spec']['card_id'] for c in offer['candidates']}
        pool=[x for x in self.card_pool() if x not in shown]
        if target['spec']['upgrade']:pool=[x for x in pool if x in self.upgradable]
        if pool:target['spec']=dict(card_id=self.rng.choice(pool),upgrade=target['spec']['upgrade'],customizations=[])
        else:offer['candidates'].remove(target)
        return offer

    # ---- 饮料 ----
    def drink_offer(self,k=3,rarities=('ProduceDrinkRarity_Sr','ProduceDrinkRarity_Ssr'),effect_id='random',state_digest=None):
        """礼物等场合的饮料候选。默认只出 SR/SSR（礼物规则）。"""
        pool=sorted(k2 for k2,r in self.drinks.items() if r['rarity'] in rarities)
        picks=self.rng.sample(pool,min(k,len(pool)))
        return dict(provenance=dict(PROVENANCE),state_digest=state_digest,effect_id=effect_id,
                    candidates=[dict(id=f'drink-{i}',drink_id=x) for i,x in enumerate(picks)])

    # ---- 商店 ----
    def shop_products(self,cards=4,drinks=2,services=('delete','upgrade')):
        """相談货架（market 的 product 字段）。标价、SALE、强化/删除递增价、H.I.F 购卡折扣按社区规则；货架张数是本地假设。"""
        products=[];pos=0
        hif=rules.HIF_CARD_DISCOUNT_PERMIL[self.state.hif_card_discount_level]
        pool=[x for x in self.card_pool() if self.cards[x]['rarity'] in ('ProduceCardRarity_R','ProduceCardRarity_Sr','ProduceCardRarity_Ssr')]
        for i,card_id in enumerate(self.rng.sample(pool,min(cards,len(pool)))):
            r=self.cards[card_id]
            if r['rarity']=='ProduceCardRarity_R':
                if card_id not in self.upgradable:continue
                up=1
            else:up=self.rng.choice((0,1)) if card_id in self.upgradable else 0
            sale=rules.SALE_PERMIL if i==0 else 0
            products.append(_product(pos:=pos+1,'ProduceResourceType_ProduceCard',card_id,up,rules.SHOP_CARD_PRICE[(r['rarity'],up)],
                                     sale=sale,effects=[hif] if hif else []))
        for i,drink_id in enumerate(self.rng.sample(sorted(self.drinks),min(drinks,len(self.drinks)))):
            sale=rules.SALE_PERMIL if i==0 else 0
            products.append(_product(pos:=pos+1,'ProduceResourceType_ProduceDrink',drink_id,0,rules.DRINK_PRICE[self.drinks[drink_id]['rarity']],sale=sale))
        kinds={'delete':'ProduceResourceType_CardDelete','upgrade':'ProduceResourceType_CardUpgrade'}
        done={'delete':self.state.deletes_done,'upgrade':self.state.upgrades_done}
        for s in services:
            products.append(_product(pos:=pos+1,kinds[s],'',0,rules.service_price(done[s])))
        return products

    def interval_products(self,refreshes_done=0,upgrades_done=0):
        """轮间货架（社区规则：换卡 4 格、セレクトチェンジ 2 格、饮料 2 格；不适用 H.I.F 折扣）。
        返回 dict(cards, changes, drinks, services)；changes 只给目标卡与价格，换出哪张由玩家选。"""
        prices=rules.INTERVAL_CARD_PRICE
        pool=[x for x in self.card_pool() if any((self.cards[x]['rarity'],u) in prices for u in (0,1))]
        cards=[];pos=0
        for card_id in self.rng.sample(pool,min(rules.INTERVAL_SLOTS['card'],len(pool))):
            rar=self.cards[card_id]['rarity']
            ups=[u for u in (1,0) if (rar,u) in prices and (u==0 or card_id in self.upgradable)]
            if not ups:continue
            up=ups[0] if len(ups)==1 else self.rng.choice(ups)
            cards.append(_product(pos:=pos+1,'ProduceResourceType_ProduceCard',card_id,up,prices[(rar,up)]))
        cpool=[x for x in self.card_pool() if self.cards[x]['rarity'] in rules.INTERVAL_CHANGE_PRICE]
        changes=[dict(positionNumber=(pos:=pos+1),resourceId=x,upgradeCount=0,price=rules.INTERVAL_CHANGE_PRICE[self.cards[x]['rarity']])
                 for x in self.rng.sample(cpool,min(rules.INTERVAL_SLOTS['change'],len(cpool)))]
        drinks=[_product(pos:=pos+1,'ProduceResourceType_ProduceDrink',d,0,rules.DRINK_PRICE[self.drinks[d]['rarity']])
                for d in self.rng.sample(sorted(self.drinks),min(rules.INTERVAL_SLOTS['drink'],len(self.drinks)))]
        services=dict(upgrade=rules.interval_upgrade_price(upgrades_done),refresh=rules.interval_refresh_price(refreshes_done),
                      recover_per_2hp=10,upgrades_per_cycle=rules.INTERVAL_UPGRADES_PER_CYCLE,customizes_per_cycle=rules.INTERVAL_CUSTOMIZES_PER_CYCLE)
        return dict(cards=cards,changes=changes,drinks=drinks,services=services)


def _product(position,resource_type,resource_id,upgrade,price,sale=0,effects=()):
    return dict(positionNumber=position,purchased=False,resourceType=resource_type,resourceId=resource_id,upgradeCount=upgrade,
                price=price,discountPermil=sale,effectDiscountPermils=list(effects),effectResourceDiscountPermils=[],nextPrice=price,
                lock=False,highScoreReward=False,quoted_price=_discounted(price,sale,effects))
