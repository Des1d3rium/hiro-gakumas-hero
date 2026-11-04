# 来源：digital-hiro src/hiro/perception/lesson.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""课程/考试出牌画面 → 结构化状态。

全部识别都基于传入的截图（720x1280 逻辑分辨率），不读设备，便于离线回放与单测。
识别能力直接复用 KAA：HUD 数字用 KAA 的 ddddocr 数字识别，手牌用 KAA 的卡面图像库比对，卡牌信息查 KAA 的 game.db。
"""

from dataclasses import asdict, dataclass, field

from cv2.typing import MatLike
from kaa.tasks import R
from kaa.tasks.produce.new.play_cards.page import _digits_from_image
from kaa.tasks.produce.new.play_cards.ui import locate_cards

from hiro.vision.cards import full_text


@dataclass
class HandCard:
    slot: int
    name: str | None
    card_id: str | None
    upgraded: bool
    available: bool
    plan_type: str | None
    category: str | None
    rarity: str | None
    genki_cost: int | None
    hp_cost: int | None
    extra_cost: dict | None
    effect: str
    box: list[int]


@dataclass
class LessonState:
    remaining_turns: int | None
    hp: int | None
    genki: int | None
    hand: list[HandCard] = field(default_factory=list)
    unrecognized_cards: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _read_int(img: MatLike, box) -> int | None:
    crop = img[box.y1 : box.y2, box.x1 : box.x2]
    nums = _digits_from_image(crop)
    return nums[0] if nums else None


def read_hand(img: MatLike) -> tuple[list[HandCard], int]:
    hand, missing = [], 0
    for slot, obj in enumerate(locate_cards(img)):
        if obj is None or obj.card is None:
            missing += 1
            continue
        card = obj.card
        extra = {"type": card.cost_type, "value": card.cost_value} if card.cost_value else None
        hand.append(
            HandCard(
                slot=slot,
                name=card.name,
                card_id=card._id,
                upgraded=bool(card.upgrade_count),
                available=obj.available,
                plan_type=card.plan_type,
                category=card.category,
                rarity=card.rarity,
                genki_cost=card.stamina or None,
                hp_cost=card.force_stamina or None,
                extra_cost=extra,
                effect=full_text(card),
                box=list(obj.rect.xywh),
            )
        )
    return hand, missing


def read_lesson(img: MatLike) -> LessonState:
    boxes = R.InProduce.InLesson
    hand, missing = read_hand(img)
    return LessonState(
        remaining_turns=_read_int(img, boxes.BoxRemainingTurns),
        hp=_read_int(img, boxes.BoxHp),
        genki=_read_int(img, boxes.BoxGenki),
        hand=hand,
        unrecognized_cards=missing,
    )
