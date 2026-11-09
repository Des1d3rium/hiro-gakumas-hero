"""P 道具通用加载器：把主数据里的 P 道具编译成「触发器 + 效果」，不靠说明文字的正则。

主数据链（2026-11-09 摸清，以 夜空を従える花+ 为例）：
  ProduceItem.produceItemEffectIds → ProduceItemEffect
    · effectType = ExamStatusEnchant → ProduceExamStatusEnchant（produceExamTriggerId + produceExamEffectIds）
    · effectType = ProduceEffect     → 培育中的效果（考试外），这里只记 id
  ProduceExamTrigger：phaseTypes / phaseValues（何时：打牌后、回合开始、每 N 张…）、
    fieldStatusCheckTypes / fieldStatusTypes / fieldStatusValues（条件，Not = 取反）、produceCardSearchId（数哪些牌）
  ProduceCardSearch：按 effectGroupIds / 卡 id / 种类 / 稀有度 / 强化次数 / 客制化 筛牌
  ProduceExamEffect：effectType / effectValue1/2 / effectCount / effectTurn …

一张牌属于哪些效果组：ProduceCard.effectGroupIds，加上已做客制化的 ProduceCardGrowEffect.effectGroupIds
（インフルエンサー+ 本身没有「強気」组，客制化「強気追加」后才有）。

判断不了的字段如实标成未支持，matches / holds 返回 None（不知道），不猜；调用方遇到 None 退回手写规则。

已接进 VEIL 账本（hiro.brain.veil，开关都默认开、=0 退回手写规则）：
  · 夜空を従える花 推不推计数（flower_counts，VEIL_PITEM_FLOWER）；
  · 夜空计数到了的效果、一番星「回合开始非温存就切のんびり」（flower_trigger_outcome / card_start_turn_enchant +
    start_turn_fire，VEIL_ENCHANT_ENGINE）。目前只用 Outcome 里的姿态，元気 / 熱意 等不进账本。
  · 回忆能力表（hiro.brain.memory_ability）也由这里编译。
通用引擎在 hiro.brain.pitem_engine（VEIL_PITEM_ENGINE，默认关）：按持有的 P 道具（画面道具列 + 偶像专属道具）装全部附魔，
  补了其余触发时机、条件、发动次数；开着时夜空道具按偶像找，不用这里写死的 FLOWER_ITEM。还差的见 TODO.md「通用 P 道具引擎」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

UNKNOWN_SUFFIX = "_Unknown"

# ProduceExamFieldStatusType → 只认这些；其余条件 holds() 返回 None
NO_STANCE_NAMES = frozenset({None, "指針なし", "指針解除"})  # 没有指针不算指针（使用者 2026-11-09）

LESSON_OF = {"ProduceStepLessonType_LessonVocal": "Vo", "ProduceStepLessonType_LessonDance": "Da",
             "ProduceStepLessonType_LessonVisual": "Vi"}

PLAY_COUNT_PHASES = frozenset({
    "ProduceExamPhaseType_ExamPlayCountInterval",
    "ProduceExamPhaseType_ExamPlayCountIntervalAfter",
})


def _db():
    from hiro.state.catalog import catalog

    return catalog().db


def _json(v, default=None):
    if v is None or v == "":
        return default if default is not None else []
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except ValueError:
        return default if default is not None else []


def _row(table: str, key: str) -> dict | None:
    if not key:
        return None
    r = _db().execute(f'SELECT * FROM "{table}" WHERE id = ?', (key,)).fetchone()
    return dict(r) if r is not None else None


def _set(v) -> bool:
    """主数据字段是否「设了值」：空串 / [] / 0 / *_Unknown 都算没设。"""
    if v in (None, "", 0, False, "[]"):
        return False
    if isinstance(v, str) and v.endswith(UNKNOWN_SUFFIX):
        return False
    if isinstance(v, list):
        return bool(v)
    return True


# ---------------------------------------------------------------- 牌的画像


@dataclass(frozen=True)
class CardProfile:
    card_id: str
    upgrade: int
    name: str
    category: str | None
    rarity: str | None
    plan: str | None
    cost_type: str | None
    groups: frozenset[str]
    customized: bool


@lru_cache(maxsize=None)
def _custom_groups(card_id: str, upgrade: int, desc: str) -> frozenset[str]:
    """这张卡的某项客制化（按描述，如「強気追加」）带来的效果组；一项多级时取所有级的并集。"""
    row = _db().execute("SELECT produceCardCustomizeIds FROM ProduceCard WHERE id = ? AND upgradeCount = ?",
                        (card_id, upgrade)).fetchone()
    out = set()
    for cid in _json(row[0] if row else None):
        for (grow_ids,) in _db().execute("SELECT produceCardGrowEffectIds FROM ProduceCardCustomize WHERE id = ? AND description = ?",
                                         (cid, desc)).fetchall():
            for gid in _json(grow_ids):
                g = _row("ProduceCardGrowEffect", gid)
                if g:
                    out.update(_json(g.get("effectGroupIds")))
    return frozenset(out)


def card_profile(card_id: str, upgrade: int = 0, customs: tuple[str, ...] | list[str] = ()) -> CardProfile | None:
    """主数据里这张牌（含客制化）的画像；查不到返回 None。customs 是客制化描述（账本 customs，如「強気追加」）。"""
    r = _db().execute("SELECT * FROM ProduceCard WHERE id = ? AND upgradeCount = ?", (card_id, int(upgrade or 0))).fetchone()
    if r is None:
        return None
    r = dict(r)
    groups = set(_json(r.get("effectGroupIds")))
    for desc in customs or ():
        groups |= _custom_groups(card_id, int(upgrade or 0), desc)
    return CardProfile(card_id, int(upgrade or 0), r.get("name"), r.get("category"), r.get("rarity"), r.get("planType"),
                       r.get("costType"), frozenset(groups), bool(customs))


def card_profile_by_name(name: str, customs=()) -> CardProfile | None:
    r = _db().execute("SELECT id, upgradeCount FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    return card_profile(r[0], r[1], customs) if r else None


# ---------------------------------------------------------------- 牌搜索


@dataclass(frozen=True)
class CardSearch:
    id: str
    effect_groups: frozenset[str] = frozenset()
    card_ids: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    rarities: frozenset[str] = frozenset()
    upgrades: frozenset[int] = frozenset()
    plan: str | None = None
    cost_type: str | None = None
    customized: bool | None = None
    unsupported: tuple[str, ...] = ()  # 设了值、但这里判断不了的字段

    def matches(self, card: CardProfile | None) -> bool | None:
        """这张牌符不符合；有判断不了的字段、或牌画像未知时返回 None。"""
        if card is None or self.unsupported:
            return None
        if self.effect_groups and not (self.effect_groups & card.groups):
            return False
        if self.card_ids and card.card_id not in self.card_ids:
            return False
        if self.categories and card.category not in self.categories:
            return False
        if self.rarities and card.rarity not in self.rarities:
            return False
        if self.upgrades and card.upgrade not in self.upgrades:
            return False
        if self.plan and card.plan not in (self.plan, "ProducePlanType_Common"):
            return False
        if self.cost_type and card.cost_type != self.cost_type:
            return False
        if self.customized is not None and card.customized != self.customized:
            return False
        return True


# 只影响「从哪里挑 / 挑几张」的字段：判断「一张打出的牌符不符合」时不需要
_PICK_ONLY = {"cardPositionType", "orderType", "limitCount"}
_HANDLED = {"id", "effectGroupIds", "produceCardIds", "cardCategories", "cardRarities", "upgradeCounts", "planType",
            "costType", "isCustomized", "produceDescriptions"}


@lru_cache(maxsize=None)
def card_search(search_id: str) -> CardSearch | None:
    r = _row("ProduceCardSearch", search_id)
    if r is None:
        return None
    unsupported = tuple(sorted(k for k, v in r.items() if k not in _HANDLED | _PICK_ONLY and _set(v)))
    return CardSearch(
        id=search_id,
        effect_groups=frozenset(_json(r.get("effectGroupIds"))),
        card_ids=frozenset(_json(r.get("produceCardIds"))),
        categories=frozenset(_json(r.get("cardCategories"))),
        rarities=frozenset(_json(r.get("cardRarities"))),
        upgrades=frozenset(int(x) for x in _json(r.get("upgradeCounts"))),
        plan=r.get("planType") if _set(r.get("planType")) else None,
        cost_type=r.get("costType") if _set(r.get("costType")) else None,
        customized=bool(r["isCustomized"]) if r.get("isCustomized") else None,
        unsupported=unsupported,
    )


# ---------------------------------------------------------------- 触发器 / 效果


@dataclass(frozen=True)
class Condition:
    negate: bool
    field: str
    value: int | None
    search: CardSearch | None

    def holds(self, stance: str | None = None) -> bool | None:
        """按局面判断；目前只认「有没有指针」（NoStance），其余返回 None（不知道）。"""
        if self.field == "ProduceExamFieldStatusType_NoStance":
            hit = stance in NO_STANCE_NAMES
            return (not hit) if self.negate else hit
        return None


@dataclass(frozen=True)
class Trigger:
    id: str
    phases: tuple[tuple[str, int | None], ...]
    conditions: tuple[Condition, ...]
    search: CardSearch | None
    upper: int | None = None
    lower: int | None = None
    lesson: str | None = None  # 只在这个属性的回合触发（支援卡道具【ダンスターンのみ】）：Vo / Da / Vi

    @property
    def play_interval(self) -> int | None:
        """「每打出 N 张符合的牌」类触发的 N；不是这类返回 None。"""
        for phase, value in self.phases:
            if phase in PLAY_COUNT_PHASES and value:
                return int(value)
        return None

    def counts_play(self, card: CardProfile | None, stance: str | None) -> bool | None:
        """这一手（打出前的姿态 stance）算不算进计数；判断不了返回 None。"""
        if self.play_interval is None:
            return False
        for cond in self.conditions:
            ok = cond.holds(stance)
            if ok is None:
                return None
            if not ok:
                return False
        if self.search is None:
            return True
        return self.search.matches(card)


@lru_cache(maxsize=None)
def trigger(trigger_id: str) -> Trigger | None:
    r = _row("ProduceExamTrigger", trigger_id)
    if r is None:
        return None
    phases = _json(r.get("phaseTypes"))
    values = _json(r.get("phaseValues"))
    checks = _json(r.get("fieldStatusCheckTypes"))
    fields = _json(r.get("fieldStatusTypes"))
    fvalues = _json(r.get("fieldStatusValues"))
    fsearch = _json(r.get("fieldStatusProduceCardSearchIds"))
    conds = []
    for i, f in enumerate(fields):
        conds.append(Condition(
            negate=(checks[i] if i < len(checks) else "").endswith("_Not"),
            field=f,
            value=fvalues[i] if i < len(fvalues) else None,
            search=card_search(fsearch[i]) if i < len(fsearch) and fsearch[i] else None,
        ))
    return Trigger(
        id=trigger_id,
        phases=tuple((p, values[i] if i < len(values) else None) for i, p in enumerate(phases)),
        conditions=tuple(conds),
        search=card_search(r.get("produceCardSearchId")) if r.get("produceCardSearchId") else None,
        upper=r.get("upperSearchCount") or None,
        lower=r.get("lowerSearchCount") or None,
        lesson=LESSON_OF.get(r.get("lessonType") or ""),
    )


@dataclass(frozen=True)
class Effect:
    id: str
    type: str
    value1: int = 0
    value2: int = 0
    count: int = 0
    turn: int = 0
    search: CardSearch | None = None
    chain: tuple[str, ...] = ()
    text: str = ""


@lru_cache(maxsize=None)
def exam_effect(effect_id: str) -> Effect | None:
    from hiro.brain.cards import description_text

    r = _row("ProduceExamEffect", effect_id)
    if r is None:
        return None
    chain = [r.get("chainProduceExamEffectId")] + _json(r.get("chainProduceExamEffectIds"))
    return Effect(
        id=effect_id, type=r.get("effectType"), value1=r.get("effectValue1") or 0, value2=r.get("effectValue2") or 0,
        count=r.get("effectCount") or 0, turn=r.get("effectTurn") or 0,
        search=card_search(r.get("produceCardSearchId")) if r.get("produceCardSearchId") else None,
        chain=tuple(c for c in chain if c), text=description_text(r.get("produceDescriptions")),
    )


@dataclass(frozen=True)
class Enchant:
    id: str
    text: str
    trigger: Trigger | None
    effects: tuple[Effect, ...]
    turns: int = -1  # ProduceItemEffect.effectTurn：-1 = 整场
    limit: int | None = None  # 次数上限（P 道具「レッスン内N回」= ProduceItemEffect.effectCount；牌「以降N回まで」）；None = 不限
    missing: tuple[str, ...] = ()  # 主数据里引用了、却查不到的 id


@dataclass(frozen=True)
class PItem:
    id: str
    name: str
    text: str
    fire_limit: int
    fire_interval: int
    enchants: tuple[Enchant, ...] = ()
    produce_effects: tuple[str, ...] = ()  # 考试外（培育中）的效果 id，这里不解析
    missing: tuple[str, ...] = ()

    @property
    def play_counters(self) -> list[Enchant]:
        return [e for e in self.enchants if e.trigger is not None and e.trigger.play_interval]


@lru_cache(maxsize=None)
def load_pitem(item_id: str) -> PItem | None:
    """P 道具 id → 编译好的结构；查不到返回 None。"""
    from hiro.brain.cards import description_text

    r = _row("ProduceItem", item_id)
    if r is None:
        return None
    enchants, produce, missing = [], [], []
    for eid in _json(r.get("produceItemEffectIds")):
        ie = _row("ProduceItemEffect", eid)
        if ie is None:
            missing.append(eid)
            continue
        if ie.get("effectType") == "ProduceItemEffectType_ExamStatusEnchant":
            en = _row("ProduceExamStatusEnchant", ie.get("produceExamStatusEnchantId"))
            if en is None:
                missing.append(ie.get("produceExamStatusEnchantId"))
                continue
            effects, lost = [], []
            for x in _json(en.get("produceExamEffectIds")):
                e = exam_effect(x)
                (effects if e is not None else lost).append(e if e is not None else x)
            tid = en.get("produceExamTriggerId")
            tr = trigger(tid) if tid else None
            if tid and tr is None:
                lost.append(tid)
            enchants.append(Enchant(en["id"], description_text(en.get("produceDescriptions")), tr, tuple(effects),
                                    turns=ie.get("effectTurn") if ie.get("effectTurn") is not None else -1,
                                    limit=ie.get("effectCount") if (ie.get("effectCount") or 0) > 0 else None,
                                    missing=tuple(lost)))
        else:
            produce.append(ie.get("produceEffectId") or eid)
    return PItem(
        id=item_id, name=r.get("name"), text=description_text(r.get("produceDescriptions")),
        fire_limit=r.get("fireLimit") or 0, fire_interval=r.get("fireInterval") or 0,
        enchants=tuple(enchants), produce_effects=tuple(produce), missing=tuple(missing),
    )


def find_pitems(name: str) -> list[PItem]:
    """按名字找（同名可能有多个 id，例如不同偶像的同名道具）。"""
    rows = _db().execute("SELECT id FROM ProduceItem WHERE name = ?", (name,)).fetchall()
    return [p for (i,) in rows if (p := load_pitem(i)) is not None]


# ---------------------------------------------------------------- 计数推演


@dataclass
class PlayCounter:
    """「每打出 N 张符合的牌触发一次」的计数器。remaining = 画面道具旁「N回」：还差几张触发。"""
    interval: int
    trigger: Trigger
    played: int = 0
    unknown: bool = False  # 有一手判断不了算不算
    log: list = field(default_factory=list)

    @property
    def remaining(self) -> int | None:
        return None if self.unknown else self.interval - self.played % self.interval

    def play(self, card: CardProfile | None, stance: str | None) -> bool | None:
        """记一手；返回这一手是否触发（判断不了返回 None，之后 remaining 也是 None）。"""
        hit = self.trigger.counts_play(card, stance)
        self.log.append((card.name if card else None, stance, hit))
        if hit is None:
            self.unknown = True
            return None
        if not hit:
            return False
        self.played += 1
        return self.played % self.interval == 0


def play_counter(item: PItem) -> PlayCounter | None:
    for en in item.play_counters:
        return PlayCounter(en.trigger.play_interval, en.trigger)
    return None


# ---------------------------------------------------------------- 考试里的手牌（CardView）


@lru_cache(maxsize=None)
def _added_effect_groups(effect_id: str) -> frozenset[str]:
    """客制化「追加」的效果（ProduceCardGrowEffect.playProduceExamEffectId）带的效果组。"""
    out = set()
    for (groups,) in _db().execute("SELECT effectGroupIds FROM ProduceCardGrowEffect WHERE playProduceExamEffectId = ?",
                                   (effect_id,)).fetchall():
        out.update(_json(groups))
    return frozenset(out)


@lru_cache(maxsize=None)
def _base_effects(card_id: str, upgrade: int) -> frozenset[str]:
    r = _db().execute("SELECT playEffects FROM ProduceCard WHERE id = ? AND upgradeCount = ?", (card_id, upgrade)).fetchone()
    return frozenset(e.get("produceExamEffectId") for e in _json(r[0] if r else None) if e.get("produceExamEffectId"))


def view_profile(card) -> CardProfile | None:
    """考试手牌（CardView）的画像：主数据这一级 + 客制化追加的效果组（取自 CardView.fx 里多出来的效果）。
    考试里的临时强化（支援卡，可到 ++ / +++）主数据没有那一级时往下找最近的一级（效果组不随强化变）。
    没有 card_id、主数据查不到时返回 None。"""
    card_id = getattr(card, "card_id", None)
    if not card_id:
        return None
    upgrade = int(getattr(card, "upgrade", 0) or 0)
    prof = None
    for up in range(upgrade, -1, -1):
        prof = card_profile(card_id, up)
        if prof is not None:
            upgrade = up
            break
    if prof is None:
        return None
    fx = getattr(card, "fx", None)
    extra = set()
    if isinstance(fx, dict):
        base = _base_effects(card_id, upgrade)
        for _, eid in fx.get("effects") or []:
            if eid and eid not in base:
                extra |= _added_effect_groups(eid)
    if not extra:
        return prof
    return CardProfile(prof.card_id, prof.upgrade, prof.name, prof.category, prof.rarity, prof.plan, prof.cost_type,
                       prof.groups | extra, True)


FLOWER_ITEM = "pitem_03-3-303-1"  # 夜空を従える花+（未强化版触发条件相同）


def flower_counts(card, stance: str | None) -> bool | None:
    """VEIL 夜空を従える花：打出这张牌（打出前姿态 stance）推不推进计数。判断不了返回 None（调用方退回旧规则）。"""
    try:
        item = load_pitem(FLOWER_ITEM)
    except Exception:  # noqa: BLE001  没有主数据
        return None
    if item is None or not item.play_counters:
        return None
    try:
        return item.play_counters[0].trigger.counts_play(view_profile(card), stance)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 状态附魔（P 道具 / 牌打出后装上的「以降…」效果）与效果执行器
#
# 一个附魔 = 触发器 + 效果列表（+ 次数上限）。P 道具的附魔考试开始就装上；牌的附魔在打出时装上
# （ProduceExamEffect.effectType = ExamStatusEnchant，effectCount = 以降N回まで）。
# fire(事件) 判触发 → apply_effects 把效果落到局面（Position）上。认不出的效果类型只记进 Outcome.unknown，不动局面。

STANCE_OF_EFFECT = {
    "ProduceExamEffectType_ExamConcentration": "強気",
    "ProduceExamEffectType_ExamPreservation": "温存",
    "ProduceExamEffectType_ExamOverPreservation": "のんびり",
    "ProduceExamEffectType_ExamFullPower": "全力",
}
WARM_STANCES = frozenset({"温存", "温存1段階目", "温存2段階目", "のんびり"})
FIELD_STANCE = {
    "ProduceExamFieldStatusType_PreservationUp": WARM_STANCES,  # のんびり 也算温存（一番星「非温存状態」不含のんびり，和手写规则一致）
    "ProduceExamFieldStatusType_ConcentrationUp": frozenset({"強気"}),
    "ProduceExamFieldStatusType_FullPowerUp": frozenset({"全力"}),
}
PHASE_START_TURN = "ProduceExamPhaseType_ExamStartTurn"


def holds(cond: Condition, stance: str | None) -> bool | None:
    """条件按姿态判（没有指针 / 温存 / 強気 / 全力）；其余字段判断不了 None。姿态不明（None 且不是指針なし 场合）由调用方处理。"""
    if cond.field in FIELD_STANCE:
        if stance is None:
            return None
        hit = stance in FIELD_STANCE[cond.field]
        return (not hit) if cond.negate else hit
    return cond.holds(stance)


@dataclass
class Outcome:
    stance: str | None = None  # 效果要切到的姿态（None = 不切）
    level: int | None = None
    actions: int = 0  # 追加出牌次数
    draws: int = 0
    fp: int = 0  # 全力値
    genki: int = 0
    unknown: list = field(default_factory=list)


def apply_effects(effects, current: str | None) -> Outcome:
    """效果列表 → 局面变化。全力 下切不了姿态（游戏规则；使用者 2026-11-07）。"""
    out = Outcome()
    for e in effects:
        if e is None:
            continue
        if e.type in STANCE_OF_EFFECT:
            if current != "全力" or e.type == "ProduceExamEffectType_ExamFullPower":
                out.stance = STANCE_OF_EFFECT[e.type]
                out.level = e.value1 if e.value1 in (1, 2) and out.stance in ("強気", "温存") else None
        elif e.type == "ProduceExamEffectType_ExamPlayableValueAdd":
            out.actions += e.count or 1
        elif e.type == "ProduceExamEffectType_ExamCardDraw":
            out.draws += e.value1
        elif e.type == "ProduceExamEffectType_ExamFullPowerPoint":
            out.fp += e.value1
        elif e.type == "ProduceExamEffectType_ExamBlock":
            out.genki += e.value1
        else:
            out.unknown.append(e.type)
    return out


@lru_cache(maxsize=None)
def enchant(enchant_id: str) -> Enchant | None:
    from hiro.brain.cards import description_text

    en = _row("ProduceExamStatusEnchant", enchant_id)
    if en is None:
        return None
    tid = en.get("produceExamTriggerId")
    effects = tuple(e for x in _json(en.get("produceExamEffectIds")) if (e := exam_effect(x)) is not None)
    return Enchant(enchant_id, description_text(en.get("produceDescriptions")), trigger(tid) if tid else None, effects)


def card_enchants(card) -> list[tuple[Enchant, int | None]]:
    """打出这张牌会装上的附魔 [(附魔, 次数上限 / None=不限)]；按 CardView.fx 的效果列表（含客制化追加的）。"""
    fx = getattr(card, "fx", None)
    out = []
    for _, eid in (fx or {}).get("effects") or []:
        e = exam_effect(eid) if eid else None
        if e is None or e.type != "ProduceExamEffectType_ExamStatusEnchant":
            continue
        r = _row("ProduceExamEffect", eid)
        en = enchant((r or {}).get("produceExamStatusEnchantId") or "")
        if en is not None:
            out.append((en, e.count or None))
    return out


def start_turn_fire(en: Enchant, stance: str | None) -> Outcome | None:
    """回合开始：这个附魔触发就返回效果，不触发 None；判断不了也 None（调用方退回手写规则）。"""
    tr = en.trigger
    if tr is None or not any(p == PHASE_START_TURN for p, _ in tr.phases):
        return None
    for cond in tr.conditions:
        ok = holds(cond, stance)
        if not ok:
            return None
    return apply_effects(en.effects, stance)


def card_start_turn_enchant(name: str) -> tuple[Enchant, int | None] | None:
    """按卡名找它的「回合开始」附魔（一番星：以降3回まで、ターン開始時、非温存状態の場合、のんびりに変更）。"""
    r = _db().execute("SELECT id, upgradeCount FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    if r is None:
        return None
    from hiro.brain import card_fx

    class _V:  # 只要 fx
        fx = card_fx.card_fx(r[0], r[1])

    for en, count in card_enchants(_V):
        if en.trigger is not None and any(p == PHASE_START_TURN for p, _ in en.trigger.phases):
            return en, count
    return None


def flower_trigger_outcome(current: str | None) -> Outcome | None:
    """夜空を従える花 计数到了时的效果（切のんびり、熱意、元気）。"""
    try:
        item = load_pitem(FLOWER_ITEM)
    except Exception:  # noqa: BLE001
        return None
    if item is None or not item.play_counters:
        return None
    return apply_effects(item.play_counters[0].effects, current)
