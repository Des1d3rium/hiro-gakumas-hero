"""培育状态。字段命名对齐客户端协议 Transaction.UserProduceProgress
（gakumas-player mechanics/培育状态与操作（客户端协议）.md），只收决策需要、实机能观测到的部分。"""

from dataclasses import asdict, dataclass, field

ATTRS = ("Vo", "Da", "Vi")
SELECTION = "produce-007"
FINALS = "produce-008"


@dataclass
class DeckCard:
    """UserProduceProgressProduceCard：produceCardId / upgradeCount / customizes。"""

    card_id: str
    upgrade: int = 0
    customized: bool = False


@dataclass
class SupportCard:
    """UserProduceProgressSupportCard：supportCardId / level。属性类型从 master SupportCard.type 查。"""

    support_card_id: str
    level: int | None = None


@dataclass
class LessonOption:
    """日程页可见的课程信息：副属性开局就定，SP 当天才知道（None = 未知）。"""

    main: str
    sub: str | None = None  # None = 没读到（预演按社区统计取最可能的副属性）
    sp: bool | None = None


@dataclass
class ProduceState:
    idol_card_id: str
    mode: str  # produce-007 选拔 / produce-008 本战
    step: int  # stepNumber：日程位置（本战 7/8/9 = R1/轮间/R2）
    stamina: int
    max_stamina: int
    produce_point: int
    params: dict  # vocal/dance/visual → {"Vo","Da","Vi"}
    growth_permil: dict  # *GrowthRatePermil 合计（1000 + 加成），例如 Vo 1449
    star: int
    star_permil: int = 1000  # starPermil：星星获得合计（亲爱度 37 为 1500）
    param_cap: int = 3000  # 3000 + *AdditionalLimit
    deck: list[DeckCard] = field(default_factory=list)
    drinks: list[str] = field(default_factory=list)  # produceDrinkIds
    drink_capacity: int = 4  # H.I.F 实测 4 格（第六局 PDRINK_MAX 持有 4 瓶、考试饮料栏 4 格）
    items: list[str] = field(default_factory=list)  # produceItems
    wappen_left: int = 20  # H.I.F ワッペン剩余触发次数
    rerolls_left: int = 0  # produceCardRemainSelectRerollCount
    excludes_left: int = 0  # produceCardRemainExcludeCount
    excluded: list[str] = field(default_factory=list)  # excludeProduceCardIds
    upgrades_done: int = 0
    deletes_done: int = 0
    lessons: dict = field(default_factory=dict)  # step → [LessonOption]（日程页可见的未来课程）
    supports: list[SupportCard] = field(default_factory=list)  # 本局支援卡编成（KAA 自动编成时由识别读出）
    main_attr: str | None = None  # 显式指定的主属性（运行配置）；None 时按支援卡编成推出，再不行按成长率推测

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProduceState":
        d = dict(d)
        d["deck"] = [c if isinstance(c, DeckCard) else DeckCard(**c) for c in d.get("deck", [])]
        d["supports"] = [c if isinstance(c, SupportCard) else SupportCard(**c) for c in d.get("supports", [])]
        d["lessons"] = {
            int(k): [o if isinstance(o, LessonOption) else LessonOption(**o) for o in v]
            for k, v in d.get("lessons", {}).items()
        }  # fmt: skip
        return cls(**d)
