"""VEIL's route must not be replaced by generic legal-action fallbacks."""

import pytest

from hiro.brain.cards import CardView, DrinkView, parse_tags
from hiro.brain.state import BattleState, ExamMemory
from hiro.brain.veil import BOOST, CORE, DRIVE, ENTERTAINER, REBOUND, SODA, TEACH, WHEY, VeilAdvisor
from hiro.contracts import Action
from hiro.loop.errors import ResumeBlocked


def card(slot, name, text="", *, once=False):
    return CardView(slot, name, name, 1, True, "mental", 0, 0, None, text, parse_tags(name, text, once=once))


def case(*cards, exam="r1", status="温存", flower=2, deck=None, grave=(), drinks=(), **kw):
    state = BattleState(exam, 1, 8, 40, 44, 0, list(cards), drinks=[DrinkView(i, name, name, "") for i, name in enumerate(drinks)],
                        statuses=[{"name": status}], pitem_charges=flower, piles=None if deck is None else {
                            "deck": list(deck), "grave": list(grave), "next_draw": 3,
                        }, **kw)
    return state, ExamMemory(exam, turn=1)


def choose(state, memory):
    return VeilAdvisor().decide(state, memory, None, [Action.from_legal(a) for a in state.legal_actions()]).action.legal_key()


def test_clear_order_and_forbidden_cards():
    boost = card(0, "再动+", "強気に変更スキルカード使用数追加+1", once=True)
    strong = card(1, "強気牌+", "強気に変更", once=True)
    warm = card(2, "温存牌+", "温存に変更", once=True)
    state, mem = case(warm, strong, boost, card(3, DRIVE))
    assert choose(state, mem) == "play:0"
    state.statuses = [{"name": "強気"}]
    state.hand.remove(boost)
    assert choose(state, mem) == "play:1"
    state.hand.remove(strong)
    assert choose(state, mem) == "play:2"
    state.hand = [card(0, DRIVE), card(1, "アイドルになります+")]
    assert choose(state, mem) == "skip"


def test_clear_priority_and_drinks():
    normal = card(0, "その他+", once=True)
    draw = card(1, "抽牌+", "スキルカードを2枚引く")
    state, mem = case(normal, draw, drinks=(WHEY, SODA), actions_left=3)
    assert choose(state, mem) == "play:1"
    state.hand.remove(draw)
    assert choose(state, mem) == "drink:1"
    state.hand.append(card(2, "ひと呼吸+", once=True))
    state.actions_left = 1
    assert choose(state, mem) == "drink:0"
    state.drinks.clear()
    assert choose(state, mem) == "play:0"


@pytest.mark.parametrize("flower,order", [(2, [DRIVE, REBOUND, TEACH, ENTERTAINER]), (1, [DRIVE, TEACH, REBOUND, ENTERTAINER])])
def test_cycle_order_and_resume(flower, order):
    cards = [card(i, name) for i, name in enumerate([*order, "眠気"])]
    state, mem = case(*cards, flower=flower, deck=[], drinks=(SODA, BOOST, WHEY))
    for slot in (2, 1, 0):
        assert choose(state, mem) == f"drink:{slot}"
        state.drinks = [d for d in state.drinks if d.slot != slot]
    for c in cards:
        assert choose(state, mem) == f"play:{c.slot}"
        mem.this_turn.append(f"打 {c.name}")
        mem = ExamMemory.from_dict(mem.to_dict())
        state.hand.remove(c)
    assert choose(state, mem) == "skip"


def test_transition_checks_whole_deck_and_known_counter():
    names = [DRIVE, REBOUND, TEACH, ENTERTAINER]
    state, mem = case(*(card(i, n) for i, n in enumerate(names)), deck=[], flower=3)
    assert choose(state, mem) == "play:2"
    state.piles = None
    with pytest.raises(ResumeBlocked):
        choose(state, mem)
    state.piles = {"deck": [], "grave": ["未知的常驻牌"], "next_draw": 3}
    assert choose(state, mem) != "play:0"
    state.piles = {"deck": [], "grave": [], "next_draw": 4}
    state.statuses = [{"name": "強気"}]
    state.pitem_charges = 2
    assert choose(state, mem) == "play:1"
    state.statuses = [{"name": "次のターン開始時、温存に変更"}]
    assert choose(state, mem) == "skip"


@pytest.mark.parametrize("exam,limit", [("r1", 700_000), ("r2", 1_400_000)])
def test_threshold_is_strict_and_survives_missing_score(exam, limit):
    state, mem = case(card(0, "任意牌", once=True), exam=exam, score=limit)
    assert choose(state, mem) == "play:0"
    state.score = limit + 1
    assert choose(state, mem) == "skip"
    state.score = None
    mem.last_score = limit + 1
    assert choose(state, mem) == "skip"


def test_transition_satisfies_all_three_in_one_turn():
    cards = [card(i, n) for i, n in enumerate((DRIVE, REBOUND, TEACH, ENTERTAINER))]
    state, mem = case(*cards, deck=[], flower=3, status="強気", actions_left=2)
    assert choose(state, mem) == "play:2"
    state.hand.remove(cards[2])
    state.piles["grave"].append(TEACH)
    state.piles["next_draw"] = 5
    state.pitem_charges = 2
    mem.this_turn.append(f"打 {TEACH}")
    assert choose(state, mem) == "play:1"
    state.hand.remove(cards[1])
    state.piles["grave"].append(REBOUND)
    state.statuses = [{"name": "温存"}]
    mem.this_turn.append(f"打 {REBOUND}")
    assert choose(state, mem) == "skip"


def test_cycle_rechecks_fifth_card_after_core_and_does_not_skip_if_unavailable():
    order = [DRIVE, REBOUND, TEACH, ENTERTAINER]
    cards = [card(i, name) for i, name in enumerate(order)]
    extra = card(4, "一心不乱+", once=True)
    extra.available = False
    state, mem = case(*cards, extra, deck=[])
    assert choose(state, mem) == "play:0"
    mem.this_turn = [f"打 {name}" for name in order]
    state.hand = [cards[-1], extra]
    with pytest.raises(ResumeBlocked, match="仍在手中"):
        choose(state, mem)
    state.hand = [extra]
    with pytest.raises(ResumeBlocked, match="第五张尚未打出"):
        choose(state, mem)
    extra.available = True
    assert choose(state, mem) == "play:4"


def test_clear_flower_trigger_enables_gatagoto_and_closes_isshin():
    strong = card(0, "強気牌+", "強気に変更", once=True)
    extra = card(1, "其他一次性牌+", once=True)
    extra.badge_score = 10
    gatagoto = card(2, "ガタゴトすやすや+", once=True)
    isshin = card(3, "一心不乱++", once=True)
    gatagoto.available = False
    state, mem = case(strong, extra, gatagoto, isshin, status="強気", flower=1, actions_left=2)
    assert choose(state, mem) == "play:3"
    state.hand.remove(isshin)
    state.pitem_charges = 2
    assert choose(state, mem) == "play:1"
    state.pitem_charges = 1
    assert choose(state, mem) == "play:0"
    state.hand.remove(strong)
    state.statuses = [{"name": "のんびり"}]
    state.pitem_charges = 3
    gatagoto.available = True
    assert choose(state, mem) == "play:2"


def test_clear_flower_trigger_does_not_rank_strong_as_isshin_setup():
    strong = card(0, "強気牌+", "強気に変更", once=True)
    extra = card(1, "再动+", "スキルカード使用数追加+1", once=True)
    isshin = card(2, "一心不乱+", once=True)
    isshin.available = False
    state, mem = case(strong, extra, isshin, flower=2)
    assert choose(state, mem) == "play:0"
    state.pitem_charges = 1
    assert choose(state, mem) == "play:1"


def test_position_tracks_flower_override_and_delayed_stance():
    from collections import Counter

    from hiro.brain.rules import Decision
    from hiro.brain.veil import stance
    from hiro.produce.veil_exam import VeilBrain, read_position

    drive = card(0, DRIVE, "強気に変更スキルカード使用数追加+1次のターン、温存に変更")
    teach = card(1, TEACH, "強気に変更")
    state, mem = case(drive, teach, status="のんびり", flower=1)
    mem.strategy_data["veil_deck"] = {DRIVE: 1, TEACH: 1}
    before = Counter(c.name for c in state.hand)
    brain = VeilBrain(None, None)

    def unexpected_read():
        pytest.fail("已校准的同一回合不应重新读夜空")

    assert read_position(state, mem, before, lambda: 1) is None
    brain.commit(state, mem, Decision("play:0", "循环", "rule"))
    assert read_position(state, mem, before, unexpected_read) == "veil_play_unverified"
    assert mem.strategy_data["veil_position"]["flower"] == 1

    mem = ExamMemory.from_dict(mem.to_dict())
    state.hand = [teach]
    state.statuses = []
    assert read_position(state, mem, Counter({TEACH: 1}), unexpected_read) is None
    assert (state.pitem_charges, stance(state)) == (3, "のんびり")

    brain.commit(state, mem, Decision("play:1", "循环", "rule"))
    state.statuses = []
    assert read_position(state, mem, Counter(), unexpected_read) is None
    assert (state.pitem_charges, stance(state)) == (2, "強気")
    mem.turn += 1
    state.statuses = []
    assert read_position(state, mem, Counter(), unexpected_read) is None
    assert (state.pitem_charges, stance(state)) == (2, "温存")
    assert mem.strategy_data["veil_position"]["next_stance"] is None


def test_position_same_stance_strong_still_ticks_flower():
    from hiro.brain.veil import after_play

    strong = card(0, TEACH, "強気に変更")
    assert after_play("強気", 2, strong) == ("強気", 1)
    assert after_play("強気", 1, strong) == ("のんびり", 3)


@pytest.mark.parametrize("flower,order", [(2, [DRIVE, REBOUND, TEACH, ENTERTAINER]), (1, [DRIVE, TEACH, REBOUND, ENTERTAINER])])
def test_cycle_plays_idol_fifth_first(flower, order):
    """第五张是アイドルになります+：最后打时核心牌都已打出、禁打条件永远成立 → 改为循环第一张，其余顺序不变。"""
    idol = card(4, "アイドルになります+", "温存に変更スキルカード使用数追加+1", once=True)
    cards = [card(i, name) for i, name in enumerate(order)]
    state, mem = case(*cards, idol, flower=flower, deck=[])
    assert choose(state, mem) == "play:4"
    mem.this_turn.append(f"打 {idol.name}")
    state.hand.remove(idol)
    for c in cards:
        mem = ExamMemory.from_dict(mem.to_dict())
        assert choose(state, mem) == f"play:{c.slot}"
        mem.this_turn.append(f"打 {c.name}")
        state.hand.remove(c)
    assert choose(state, mem) == "skip"


def test_cycle_idol_not_playable_first_keeps_old_end_rule():
    """开局打不了（例如全力値不够、不可用）：照旧排在最后，最后受禁打条件限制而跳过，不卡住循环。"""
    order = [DRIVE, REBOUND, TEACH, ENTERTAINER]
    idol = card(4, "アイドルになります+", "温存に変更", once=True)
    idol.available = False
    cards = [card(i, name) for i, name in enumerate(order)]
    state, mem = case(*cards, idol, deck=[])
    assert choose(state, mem) == "play:0"
    mem.this_turn = [f"打 {name}" for name in order]
    state.hand = [idol]
    idol.available = True
    assert choose(state, mem) == "skip"
