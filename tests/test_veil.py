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
def test_cycle_order_and_resume(flower, order, monkeypatch):
    monkeypatch.setenv("VEIL_CYCLE_SODA", "0")  # 这条测循环顺序；满手不喝ソーダ 见 test_cycle_soda_waits_for_hand_room
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


def test_clear_flower_trigger_enables_gatagoto_and_closes_isshin(monkeypatch):
    # 合成局面的 強気 没写段階，体力闸按 2 段階每张另扣 3 估，会改排序；这条只测夜空触发
    monkeypatch.setenv("VEIL_HP_GUARD", "0")
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


def test_position_tracks_flower_override_and_delayed_stance(monkeypatch):
    from collections import Counter

    monkeypatch.setenv("HIRO_VEIL_FLOWER_SCREEN", "0")  # 只测账本推算；画面校正见 test_position_flower_follows_screen

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


# ---------------------------------------------------------------- 保留温存 / 再动（VEIL_KEEP_WARM）
TEACH_TEXT = "強気に変更パラメータ+6熱意追加+2（2ターン）次のターン、スキルカードを2枚引く 重複不可"
ENT_TEXT = "体力消費5強気に変更パラメータ+12"


@pytest.fixture
def keep(monkeypatch):
    monkeypatch.setenv("VEIL_KEEP_WARM", "1")


def _extra(mem, start=False, zeroed=False):
    mem.strategy_data["veil_extra"] = {"turn": mem.turn, "start": start, "zeroed": zeroed}


def test_keep_warm_clears_one_time_card_before_reusable_strong(keep):
    """温存下：一次性牌（頂点へ）先打，不让教えてあげる 先用掉温存的再动（第一局 R1、上一批 R1 seq 30）。"""
    top = card(0, "頂点へ+", "以降、ターン開始時、いずれかの指針の場合…", once=True)
    teach = card(1, TEACH, TEACH_TEXT)
    state, mem = case(top, teach, actions_left=1)
    _extra(mem)
    assert choose(state, mem) == "play:0"


def test_keep_warm_off_keeps_old_priority(monkeypatch):
    monkeypatch.delenv("VEIL_KEEP_WARM", raising=False)
    top = card(0, "頂点へ+", "以降、ターン開始時、いずれかの指針の場合…", once=True)
    teach = card(1, TEACH, TEACH_TEXT)
    state, mem = case(top, teach, actions_left=1)
    assert choose(state, mem) == "play:1"


def test_keep_warm_reusable_strong_ok_when_gain_carries(keep):
    """回合开始时没有再动、只剩基础 1 次：教えてあげる 把温存换成一层能留到下回合的再动，不亏，照打。"""
    state, mem = case(card(0, TEACH, TEACH_TEXT), actions_left=1)
    _extra(mem)
    assert choose(state, mem) == "play:0"


def test_keep_warm_skips_reusable_strong_with_carried_stack(keep):
    """已有一层可保留的再动：打 教えてあげる 要用掉温存和这一层、只换回一层 → 跳过。"""
    state, mem = case(card(0, TEACH, TEACH_TEXT), actions_left=2)
    _extra(mem)
    assert choose(state, mem) == "skip"


def test_keep_warm_decaying_stack_can_be_converted(keep):
    """开局就有一层（会衰退）：这一张把层数用到 0 再获得 → 新的一层能保留，温存换再动不亏。"""
    state, mem = case(card(0, TEACH, TEACH_TEXT), actions_left=2)
    _extra(mem, start=True)
    assert choose(state, mem) == "play:0"


def test_keep_warm_strong_stance_reusable_only_on_base_action(keep):
    """強気下的 エンターテイナー：只剩基础 1 次时照打；还有可保留的再动层时跳过，把再动留到下回合。"""
    state, mem = case(card(0, ENTERTAINER, ENT_TEXT), status="強気", actions_left=1)
    _extra(mem)
    assert choose(state, mem) == "play:0"
    state, mem = case(card(0, ENTERTAINER, ENT_TEXT), status="強気", actions_left=2)
    _extra(mem)
    assert choose(state, mem) == "skip"


@pytest.mark.parametrize("start, zeroed, left, carried", [
    (False, False, 2, 1),  # 开局没有、回合内获得 → 保留
    (True, False, 3, 1),   # 开局已有、没用完又获得 → 衰退减 1
    (True, True, 2, 1),    # 开局已有、用到 0 再获得 → 保留
    (False, False, 0, 0),  # 次数用完结束回合
])
def test_track_extra_carry_rules(start, zeroed, left, carried):
    from hiro.produce.veil_exam import track_extra

    mem = ExamMemory("r2", turn=3)
    mem.strategy_data["veil_extra"] = {"turn": 2, "start": start, "zeroed": zeroed, "left": left}
    ex = track_extra(mem.strategy_data, mem, keep=True)
    assert ex["carried"] == carried and mem.actions_left == 1 + carried and ex["start"] == (carried > 0)
    assert track_extra(mem.strategy_data, mem, keep=True) is ex  # 同一回合不重复加


def test_note_extra_marks_zeroed():
    from types import SimpleNamespace as NS

    from hiro.produce.veil_exam import note_extra

    mem = ExamMemory("r2", turn=3)
    mem.strategy_data["veil_extra"] = {"turn": 3, "start": True, "zeroed": False, "left": 3}
    mem.actions_left = 2
    note_extra(mem.strategy_data, mem, NS(kind="play"), before=3)
    assert not mem.strategy_data["veil_extra"]["zeroed"] and mem.strategy_data["veil_extra"]["left"] == 2
    note_extra(mem.strategy_data, mem, NS(kind="play"), before=2)
    assert mem.strategy_data["veil_extra"]["zeroed"]


# ---------------------------------------------------------------- 体力闸（VEIL_HP_GUARD）
def costly(slot, name, text, cost, *, once=True, category="active", hp_cost=0):
    return CardView(slot, name, name, 1, True, category, cost, hp_cost, None, text, parse_tags(name, text, once=once))


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setenv("VEIL_HP_GUARD", "1")
    monkeypatch.delenv("HIRO_VEIL_HP_FLOOR", raising=False)


def test_hp_loss_uses_stance_and_isshin_from_master_data():
    from hiro.brain.veil import hp_loss

    drive = costly(0, DRIVE, "強気に変更パラメータ+4", 4)
    state, _ = case(drive)
    state.statuses = [{"name": "強気", "value": 1}, {"name": "一心不乱", "value": None}]
    assert hp_loss(state, drive, "強気") == 22  # (4+7)×2：trace 6-2-exam2 元気 0 时一张牌 25→4
    state.genki = 10
    assert hp_loss(state, drive, "強気") == 12  # 先扣元気
    state.genki = 0
    state.statuses = [{"name": "温存", "value": 1}, {"name": "一心不乱"}]
    assert hp_loss(state, drive, "温存") == 6  # 11×0.5 向上取整
    state.statuses = [{"name": "のんびり"}, {"name": "一心不乱"}]
    assert hp_loss(state, drive, "のんびり") == 0
    state.statuses = [{"name": "強気", "value": 2}]
    assert hp_loss(state, drive, "強気") == 9  # 4×2 + 2 段階每张 1
    ent = costly(1, ENTERTAINER, "体力消費5強気に変更", 0, once=False, hp_cost=5)
    state.statuses = [{"name": "のんびり"}]
    assert hp_loss(state, ent, "のんびり") == 5  # 体力消費不减（保守）


def test_hp_guard_off_keeps_old_strong_priority(monkeypatch):
    monkeypatch.setenv("VEIL_HP_GUARD", "0")
    strong = costly(0, "強気牌+", "強気に変更", 4)
    warm = costly(1, "温存牌+", "温存に変更", 3)
    state, mem = case(strong, warm, status="強気")
    assert choose(state, mem) == "play:0"


def test_hp_guard_prefers_warm_switch_in_strong(guard):
    strong = costly(0, "強気牌+", "強気に変更", 4)
    warm = costly(1, "温存牌+", "温存に変更", 3)
    state, mem = case(strong, warm, status="強気")
    assert choose(state, mem) == "play:1"  # 強気に変更 的牌留到温存下打


def test_hp_guard_blocks_card_that_breaks_floor(guard):
    strong = costly(0, "強気牌+", "強気に変更", 4)
    state, mem = case(strong, status="強気")
    state.statuses.append({"name": "一心不乱"})
    state.hp = 25  # (4+7)×2 = 22 → 剩 3 < 10
    assert choose(state, mem) == "skip"
    state.hp = 40
    assert choose(state, mem) == "play:0"
    state.hp, state.remaining_turns = 25, 1  # 最后一回合不限
    assert choose(state, mem) == "play:0"


def test_hp_guard_never_blocks_free_cards(guard):
    free = costly(0, "強気牌+", "強気に変更", 4)
    state, mem = case(free, status="のんびり")  # のんびり 下消耗为 0
    state.hp = 4  # 已经在保底线下
    assert choose(state, mem) == "play:0"


# ---------------------------------------------------------------- 温存解除的出牌次数（VEIL_RELEASE_GAIN）
def _flower_one_hand():
    # 实机 codex-20261107-1 第 1 局 R2 剩 8 回合：温存、夜空剩 1、手里 エンターテイナー+ 和 巻き返し+、有ホエイ
    ent = card(0, ENTERTAINER, "体力消費5強気に変更パラメータ+12")
    reb = card(1, REBOUND, "全力値消費1温存に変更熱意追加+6", once=True)
    return case(ent, reb, status="温存", flower=1, drinks=(WHEY,))


def test_release_gain_off_keeps_old_whey(monkeypatch):
    monkeypatch.setenv("VEIL_RELEASE_GAIN", "0")
    state, mem = _flower_one_hand()
    assert choose(state, mem) == "drink:0"


def test_release_gain_plays_entertainer_instead_of_whey(monkeypatch):
    monkeypatch.setenv("VEIL_RELEASE_GAIN", "1")
    state, mem = _flower_one_hand()
    assert choose(state, mem) == "play:0"  # 先结算牌：温存→強気 解除 +1，夜空再把強気改成のんびり


def _nonbiri_teach_hand():
    # 实机 codex-20261107-1-i 第 1 局 R2 剩 10 回合（T3）：のんびり、夜空剩 1、出牌 1 次；手里 教えてあげる+、演出計画++（一次性）、
    # アイドルになります++（全力値不够、灰）；开关关时喝了ホエイ，打完两张还剩 1 次出牌跳过——ホエイ白喝
    from dataclasses import replace as _replace

    teach = card(0, "教えてあげる+", "強気に変更パラメータ+6熱意追加+2（2ターン）次のターン、スキルカードを2枚引く")
    plan = card(1, "演出計画++", "絶好調5ターン以降、アクティブスキルカード使用時、固定元気+2", once=True)
    idol = _replace(card(2, "アイドルになります++", "全力値消費3元気+9温存に変更スキルカード使用数追加+1", once=True), available=False)
    return case(teach, plan, idol, status="のんびり", flower=1, drinks=(WHEY,))


def test_release_gain_off_drinks_whey_in_nonbiri(monkeypatch):
    monkeypatch.setenv("VEIL_RELEASE_GAIN", "0")
    state, mem = _nonbiri_teach_hand()
    assert choose(state, mem) == "drink:0"  # 旧行为（实机 1-i 的问题）


def test_release_gain_plays_strong_card_before_whey_in_nonbiri(monkeypatch):
    monkeypatch.setenv("VEIL_RELEASE_GAIN", "1")
    state, mem = _nonbiri_teach_hand()
    assert choose(state, mem) == "play:0"  # のんびり 下打強気牌：夜空触发、回到のんびり、+1 出牌，不用ホエイ


# ---------------------------------------------------------------- 全力値账本（账本优先，画面只作参考）
def _fp_cards():
    # 牌面文字取主数据原文（去掉 <nobr>）
    drive = card(0, DRIVE, "強気に変更パラメータ+4（2回）全力値+2スキルカード使用数追加+1眠気を山札のランダムな位置に生成次のターン、温存に変更")
    reb = card(1, REBOUND, "全力値消費1温存に変更熱意追加+6すべてのスキルカードのコスト値減少-1", once=True)
    idol = card(2, "アイドルになります+", "全力値消費3元気+4温存に変更スキルカード使用数追加+1", once=True)
    seika = card(3, "トレーニングの成果+", "全力値+4パラメータ+28（累積全力値の200%分、パラメータ上昇量増加）", once=True)
    return drive, reb, idol, seika


def test_fp_delta_reads_master_text():
    from hiro.produce.veil_exam import fp_delta

    drive, reb, idol, seika = _fp_cards()
    assert [fp_delta(c) for c in (drive, reb, idol, seika)] == [(2, 0), (0, 1), (0, 3), (4, 0)]


def test_fp_ledger_replays_r2_and_converts_at_ten():
    # codex-20261107-1 第 1 局 R2：成果+ +4 → アンダンテ+ −2 → 巻き返し+ −1 ×2 → オーバードライブ+ +2 = 2，巻き返し+ 付得起
    from hiro.produce.veil_exam import commit_full_power, track_full_power

    drive, reb, idol, seika = _fp_cards()
    andante = card(4, "アンダンテ+", "全力値消費2強気に変更パラメータ+18熱意増加+10%", once=True)
    mem = ExamMemory("r2", turn=1)
    data = mem.strategy_data
    assert track_full_power(data, mem) == 0
    for c in (seika, andante, reb, reb, drive):
        commit_full_power(data, mem, c)
    assert data["veil_fp"] == 2
    data["veil_fp"] = 11
    mem.turn = 2
    assert track_full_power(data, mem) == 1  # 上回合结束 ≥10：本回合开始扣 10（进全力）


def test_fp_unknown_on_resumed_memory():
    from hiro.produce.veil_exam import track_full_power

    mem = ExamMemory("r2", turn=5)
    mem.history.append("T1 打 何か")
    assert track_full_power(mem.strategy_data, mem) is None


def test_fp_grey_by_stale_frame_only_for_fullpower_cards():
    from dataclasses import replace

    from hiro.produce.veil_exam import fp_grey_by_stale_frame

    drive, reb, idol, seika = _fp_cards()
    reb, idol = replace(reb, available=False), replace(idol, available=False)
    isshin = replace(card(5, "一心不乱+", "強気の場合、使用可温存に変更"), available=False)
    state, _ = case(drive, reb, idol, isshin)
    assert fp_grey_by_stale_frame(state, 2) == [reb]  # 账本 2：巻き返し+ 付得起，アイドル 要 3 不够；条件牌不碰
    assert fp_grey_by_stale_frame(state, 3) == [reb, idol]
    assert fp_grey_by_stale_frame(state, None) == []  # 账本不知道：照画面


def test_draw_last_plays_plain_extra_action_before_drawing_one(monkeypatch):
    """使用者 2026-11-07（验证局 R1 剩 7 回合）：满手时先打不抽牌的再动牌（みずたまりスキップ），带抽牌的（フルスロットル+）留后。"""
    full = card(0, "フルスロットル+", "温存に変更スキルカード使用数追加+1スキルカードを3枚引く", once=True)
    puddle = card(1, "みずたまりスキップ+", "体力消費1元気+3スキルカード使用数追加+1", once=True)
    hand = [full, puddle, card(2, "頂点へ", once=True), card(3, "心・技・体", "強気2段階目に変更", once=True),
            card(4, "ひと呼吸+", once=True)]
    state, mem = case(*hand)
    monkeypatch.setenv("VEIL_DRAW_LAST", "0")
    assert choose(state, mem) == "play:0"  # 开关关：旧顺序，抽牌的先打
    monkeypatch.setenv("VEIL_DRAW_LAST", "1")
    assert choose(state, mem) == "play:1"
    state.hand.remove(puddle)
    assert choose(state, mem) == "play:3"  # 心・技・体：温存→強気 解除 +1，也是不抽牌的「再动」，总次数不变
    state.hand = [c for c in state.hand if c.slot != 3]
    assert choose(state, mem) == "play:0"  # 没有不抽牌的再动牌了：照旧


def test_draw_last_allows_stance_change_when_total_gain_kept(monkeypatch):
    """验证局 R2 剩 11 回合：のんびり、满手。オーバードライブ 是循环牌（清牌不打）；心・技・体（切強気 = 温存解除 +1）
    先打、再打 フルスロットル+（再动、温存、抽 3），总出牌次数一样，抽的牌不溢出。"""
    monkeypatch.setenv("VEIL_DRAW_LAST", "1")
    full = card(4, "フルスロットル+", "温存に変更スキルカード使用数追加+1スキルカードを3枚引く", once=True)
    drive = card(3, DRIVE, "強気に変更パラメータ+4全力値+2スキルカード使用数追加+1次のターン、温存に変更")
    hand = [card(0, "頂点へ", once=True), card(1, "一心不乱", "強気の場合、使用可温存に変更", once=True),
            card(2, "心・技・体", "強気2段階目に変更", once=True), drive, full]
    state, mem = case(*hand, status="のんびり", flower=3)
    assert choose(state, mem) == "play:2"


def test_free_extra_in_transition_covered_by_clear_first(monkeypatch):
    """验证局 R2 剩 8 回合：过渡阶段该等下回合时，みずたまりスキップ+（不改姿态、不抽牌、补回次数）先打掉。
    原 VEIL_FREE_EXTRA 已删（2026-11-07 回放：唯一一步与 CLEAR_FIRST 过渡分支决定相同），由 CLEAR_FIRST 覆盖。"""
    monkeypatch.setenv("VEIL_DRAW_ALL", "0")  # 合成牌库 5 张、下回合抽 4：DRAW_ALL 会先预约抽牌，这条测别的
    puddle = card(4, "みずたまりスキップ+", "体力消費1元気+8スキルカード使用数追加+1", once=True)
    cards = [card(i, n) for i, n in enumerate((DRIVE, REBOUND, TEACH, ENTERTAINER))] + [puddle]
    state, mem = case(*cards, deck=[], status="次のターン開始時、温存に変更", flower=2)
    state.piles["next_draw"] = 4
    monkeypatch.delenv("VEIL_CLEAR_FIRST", raising=False)
    monkeypatch.setenv("VEIL_FREE_EXTRA", "1")  # 旧开关不再起作用
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    assert choose(state, mem) == "play:4"


def test_draw_last_penalises_draw_among_extra_actions(monkeypatch):
    """都是再动（加次数相同）时，立即抽牌减分：不抽牌的先打。"""
    monkeypatch.setenv("VEIL_DRAW_LAST", "1")
    draw = card(0, "仕切り直し+", "スキルカード使用数追加+1スキルカードを2枚引く", once=True)
    plain = card(1, "みずたまりスキップ+", "体力消費1元気+8スキルカード使用数追加+1", once=True)
    state, mem = case(draw, plain, card(2, "その他+", once=True))
    assert choose(state, mem) == "play:1"


def test_cycle_next_card_greyed_skips_turn_instead_of_blocking():
    """验证局 1-c R2：循环下一张 巻き返し+ 在手里但全力値不够发灰，原来停机；现在跳过本回合。"""
    order = [DRIVE, REBOUND, TEACH, ENTERTAINER]
    cards = [card(i, name) for i, name in enumerate(order)]
    state, mem = case(*cards, deck=[])
    assert choose(state, mem) == "play:0"
    mem.this_turn = [f"打 {DRIVE}"]
    state.hand = cards[1:]
    cards[1].available = False
    assert choose(state, mem) == "skip"


def test_fp_cap_avoids_filling_full_power_in_clear(monkeypatch):
    """验证局 1-c R2：清牌把全力値加到 11，T4 自动全力白费。开 VEIL_FP_CAP 后有别的牌就不打加满的那张。"""
    big = card(0, "プライド", "強気に変更全力値+4", once=True)
    other = card(1, "その他+", once=True)
    state, mem = case(big, other)
    mem.strategy_data["veil_fp"] = 7
    monkeypatch.setenv("VEIL_FP_CAP", "1")
    assert choose(state, mem) == "play:1"
    state.hand.remove(other)
    assert choose(state, mem) == "play:0"  # 只剩加满的牌：照打，不空转
    mem.strategy_data["veil_fp"] = 2
    state.hand = [big, other]
    big_first = choose(state, mem)
    monkeypatch.setenv("VEIL_FP_CAP", "0")
    assert big_first == choose(state, mem)  # 加不满时与关开关一致


def test_position_flower_keeps_ledger_and_logs_screen():
    """账本优先（使用者 2026-11-07）：画面夜空和账本不一致只记 veil_flower_log，不改账本。"""
    from collections import Counter

    from hiro.produce.veil_exam import read_position

    state, mem = case(card(0, "インフルエンサー+", "スキルカード使用数追加+1スキルカードを引く"), status="強気", flower=2)
    mem.strategy_data["veil_position"] = {"turn": mem.turn, "stance": "強気", "flower": 2, "next_stance": None}
    assert read_position(state, mem, Counter(), lambda: 1) is None
    assert state.pitem_charges == 2 and mem.strategy_data["veil_flower_log"][-1] == {"turn": 1, "ledger": 2, "screen": 1}


def test_customized_influencer_switches_strong_and_ticks_flower():
    """1-d R2：インフルエンサー+ 客制化「強気追加」后打出切強気、夜空 -1；账本按 customize 记录补进牌面。"""
    from hiro.brain.veil import after_play, destination
    from hiro.produce.veil_exam import apply_customs

    inf = card(0, "インフルエンサー+", "メンタルスキルカードの元気値増加+6スキルカード使用数追加+1スキルカードを引く")
    assert destination(inf) is None
    hand = apply_customs([inf, card(1, TEACH, "強気に変更")], [{"card": "インフルエンサー+", "item": "強気追加"}])
    assert destination(hand[0]) == "強気" and after_play("強気", 2, hand[0]) == ("強気", 1)
    assert hand[1].text == "強気に変更"
    assert apply_customs(hand, [{"card": "インフルエンサー+", "item": "強気追加"}])[0].text == hand[0].text  # 不重复加


def test_new_turn_stance_unreadable_falls_back_to_ledger(monkeypatch):
    """1-e R1 第 2 回合开头读不出姿态就停机：新回合画面读不出时按账本（预约姿态 / 上回合姿态）接着走。"""
    from collections import Counter

    from hiro.brain.veil import stance
    from hiro.produce.veil_exam import read_position

    monkeypatch.setenv("HIRO_VEIL_FLOWER_SCREEN", "0")
    state, mem = case(card(0, TEACH, "強気に変更"), status="強気", flower=2)
    mem.strategy_data["veil_position"] = {"turn": mem.turn - 1, "stance": "強気", "flower": 2, "next_stance": None}
    state.statuses = []
    assert read_position(state, mem, Counter(), lambda: 2) is None and stance(state) == "強気"
    mem.turn += 1
    mem.strategy_data["veil_position"] = {"turn": mem.turn - 1, "stance": "強気", "flower": 2, "next_stance": "温存"}
    state.statuses = []
    assert read_position(state, mem, Counter(), lambda: 2) is None and stance(state) == "温存"


def test_hp_guard_strong_defers_costly_non_stance_cards(monkeypatch):
    """1-e R2 T1：強気下（体力 ×2）不改姿态的掉血牌（ひと呼吸+ / モチベ+）不打、也不为它们喝ホエイ，跳过等温存。"""
    monkeypatch.setenv("VEIL_HP_GUARD", "1")
    breath = card(0, "ひと呼吸+", "好調4ターン集中+5重複不可", once=True)
    breath = breath.__class__(**{**breath.__dict__, "cost": 7})
    motive = card(1, "モチベ+", "全力値+2", once=True)
    motive = motive.__class__(**{**motive.__dict__, "cost": 2})
    state, mem = case(breath, motive, status="強気", drinks=(WHEY,))
    state.hp, state.genki = 25, 0
    assert choose(state, mem) == "skip"
    first = card(2, "第一印象+", "強気に変更以降、強気効果のスキルカード使用時", once=True)
    first = first.__class__(**{**first.__dict__, "cost": 4})
    state.hand.append(first)
    assert choose(state, mem) in ("play:2", "drink:0")  # 改姿态的牌照打（缺次数时可喝ホエイ）


def test_clear_first_plays_once_card_before_reusable(monkeypatch):
    """使用者 2026-11-07（验证局 1-f R1 剩 3）：温存、1 次出牌，手里 教えてあげる+ / エンターテイナー（复用，切強気 +1 次）和
    頂点へ+（一次性）。开关关：先打复用牌；VEIL_CLEAR_FIRST=1：先打 頂点へ+。"""
    teach = card(0, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    ent = card(1, ENTERTAINER, "強気に変更パラメータ+10")
    peak = card(2, "頂点へ+", "すべてのスキルカードのパラメータ値増加+6", once=True)
    state, mem = case(teach, ent, peak)
    monkeypatch.delenv("VEIL_CLEAR_FIRST", raising=False)
    assert choose(state, mem) in ("play:0", "play:1")
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    assert choose(state, mem) == "play:2"


def test_clear_first_hitokokyu_beats_reusable_only(monkeypatch):
    """ひと呼吸 只让给别的一次性牌：手里只剩复用牌时先打 ひと呼吸。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    breath = card(0, "ひと呼吸+", "好調4ターン集中+5", once=True)
    ent = card(1, ENTERTAINER, "強気に変更パラメータ+10")
    state, mem = case(breath, ent)
    assert choose(state, mem) == "play:0"
    state.hand.append(card(2, "頂点へ+", "すべてのスキルカードのパラメータ値増加+6", once=True))
    assert choose(state, mem) == "play:2"


def test_drive_fp_breaks_zero_fullpower_deadlock(monkeypatch):
    """使用者 2026-11-07（1-f R2 剩 7–1）：全力値 0，アイドルになります+ / 巻き返し+ 灰着，清牌又不打 オーバードライブ（唯一来源）
    → 死循环。VEIL_DRIVE_FP=1：先打 オーバードライブ 攒全力値。"""
    drive = card(0, DRIVE, "強気に変更パラメータ+4全力値+2スキルカード使用数追加+1次のターン、温存に変更")
    rebound = card(1, REBOUND, "全力値消費1温存に変更熱意追加+6", once=True)
    rebound.available = False
    idol = card(2, "アイドルになります+", "全力値消費3元気+4温存に変更スキルカード使用数追加+1", once=True)
    idol.available = False
    from dataclasses import replace

    ent = replace(card(3, ENTERTAINER, "体力消費5強気に変更パラメータ+12"), hp_cost=5)
    state, mem = case(drive, rebound, idol, ent, status="強気")
    state.statuses[0]["value"] = 1
    state.hp = 8
    mem.strategy_data["veil_fp"] = 0
    monkeypatch.setenv("VEIL_HP_GUARD", "1")
    monkeypatch.setenv("VEIL_DRIVE_FP", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_DRIVE_FP", "1")
    assert choose(state, mem) == "play:0"
    mem.strategy_data["veil_fp"] = 3
    assert choose(state, mem) != "play:0"


def test_clear_first_no_warm_jump_with_single_action(monkeypatch):
    """1-f R2 剩 8：強気、1 次出牌、夜空剩 1（教えてあげる+ 打完会变のんびり）。体力闸的「切回温存先打」只在还有下一张时有用，
    这里不该压过一次性的 ひと呼吸+。"""
    for k in ("VEIL_HP_GUARD", "VEIL_CLEAR_FIRST"):
        monkeypatch.setenv(k, "1")
    breath = card(1, "ひと呼吸+", "好調4ターン集中+5重複不可 レッスン中1回", once=True)
    teach = card(2, TEACH, "強気に変更パラメータ+6熱意追加+2（2ターン）次のターン、スキルカードを2枚引く")
    state, mem = case(breath, teach, status="強気", flower=1)
    state.statuses[0]["value"] = 1
    state.genki = 33
    assert choose(state, mem) == "play:1"


def test_clear_first_no_reusable_pad_without_overflow(monkeypatch):
    """1-f R2 T1：温存、2 次出牌、手牌 4 张，インフルエンサー 只抽 1 张不会溢出：不先打 教えてあげる+ 垫（用掉温存）。"""
    for k in ("VEIL_DRAW_LAST", "VEIL_CLEAR_FIRST"):
        monkeypatch.setenv(k, "1")
    hand = [card(0, "ひと呼吸++", "好調4ターン集中+5", once=True),
            card(1, "インフルエンサー", "スキルカード使用数追加+1スキルカードを引く", once=True),
            card(2, "盛り上げ上手", "次のターン、スキルカードを引く", once=True),
            card(3, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")]
    state, mem = case(*hand, actions_left=2)
    assert choose(state, mem) == "play:1"
    monkeypatch.delenv("VEIL_CLEAR_FIRST")
    assert choose(state, mem) == "play:3"


def test_hp_loss_counts_cost_mods_from_played_cards():
    """1-e R2 T1（使用者 2026-11-07）：強気 2 段階每张 +1 没错，多出来的是 インフルエンサー+「メンタル…コスト値増加+1」：
    モチベ+ (2+1)×2+1 = 7、ひと呼吸+ (7+1)×2+1 = 17；巻き返し+ 让所有牌 −1。"""
    from dataclasses import replace

    from hiro.brain.veil import cost_mods, hp_loss

    texts = {"インフルエンサー+": "メンタルスキルカードの元気値増加+6・コスト値増加+1スキルカード使用数追加+1",
             "巻き返し+": "全力値消費1温存に変更熱意追加+6すべてのスキルカードのコスト値減少-1レッスン中1回"}
    mods = cost_mods([[1, "打", "インフルエンサー+"]], texts.get)
    motive = replace(card(0, "モチベ+", "全力値+2"), cost=2)
    breath = replace(card(1, "ひと呼吸+", "好調4ターン集中+5"), cost=7)
    state, _ = case(motive, breath, status="強気")
    state.statuses = [{"name": "強気", "value": 2}]
    state.genki = 0
    assert hp_loss(state, motive, "強気", mods) == 7
    assert hp_loss(state, breath, "強気", mods) == 17
    mods = cost_mods([[1, "打", "インフルエンサー+"], [2, "打", "巻き返し+"]], texts.get)
    assert hp_loss(state, motive, "強気", mods) == 5


def test_transition_drives_fp_when_rebound_starved(monkeypatch):
    """实机 1-g R1 剩 5→1 回合：牌组缩到五张、全力値 0、巻き返し+ 灰着，过渡只打 教えてあげる+（強気），五卡循环一直进不去。
    开 VEIL_DRIVE_FP 后过渡先打 オーバードライブ+（全力値+2、补次数、下回合温存）。"""
    teach = card(0, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    breath = card(1, "ひと呼吸+", "好調4ターン集中+5", once=True)
    rebound = card(2, REBOUND, "全力値消費1温存に変更熱意追加+6", once=True)
    rebound.available = False
    drive = card(3, DRIVE, "強気に変更パラメータ+4全力値+2スキルカード使用数追加+1次のターン、温存に変更")
    state, mem = case(teach, breath, rebound, drive, deck=[ENTERTAINER], status="強気", flower=1)
    mem.strategy_data["veil_fp"] = 0
    monkeypatch.setenv("VEIL_DRIVE_FP", "0")
    assert choose(state, mem) == "play:0"
    monkeypatch.setenv("VEIL_DRIVE_FP", "1")
    assert choose(state, mem) == "play:3"
    mem.strategy_data["veil_fp"] = 2  # 全力値够：巻き返し+ 发灰是别的原因，不乱打
    assert choose(state, mem) == "play:0"
    # 下回合会自动のんびり（星々を見下ろす一番星 还剩次数）：不用切温存，OD 生成眠気、优先级很低 → 照常 教えて
    mem.strategy_data["veil_fp"] = 0
    state.statuses.append({"name": "星々を見下ろす一番星", "value": 2})
    assert choose(state, mem) == "play:0"
    # アイドルになります+ 因全力値不够发灰：OD 攒全力値
    idol = card(4, "アイドルになります+", "全力値消費3元気+4温存に変更スキルカード使用数追加+1", once=True)
    idol.available = False
    state.hand.append(idol)
    assert choose(state, mem) == "play:3"


def test_transition_sheds_harmless_once_card(monkeypatch):
    """使用者 2026-11-07（1-g R1）：过渡该做的做完、本来等下回合时，用剩下的次数清掉不碍事的一次性牌，五卡缩成四卡。"""
    monkeypatch.setenv("VEIL_DRAW_ALL", "0")  # 合成牌库 5 张、下回合抽 4：DRAW_ALL 会先预约抽牌，这条测别的
    breath = card(4, "ひと呼吸+", "好調4ターン集中+5", once=True)
    cards = [card(i, n) for i, n in enumerate((DRIVE, REBOUND, TEACH, ENTERTAINER))] + [breath]
    state, mem = case(*cards, deck=[], status="次のターン開始時、温存に変更", flower=2)
    state.piles["next_draw"] = 4
    monkeypatch.delenv("VEIL_CLEAR_FIRST", raising=False)
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    assert choose(state, mem) == "play:4"
    # アイドルになります+（全力値消費3、温存に変更、+1 次）是 buff 牌，过渡里也打
    state.hand[4] = card(4, "アイドルになります+", "全力値消費3元気+4温存に変更スキルカード使用数追加+1", once=True)
    assert choose(state, mem) == "play:4"
    # 会动下回合条件的一次性牌不打：切強気（推夜空计数）、下回合切強気；只生成眠気、不补次数的（清一张塞一张）
    for text in ("強気に変更パラメータ+5", "次のターン、強気に変更", "眠気を山札のランダムな位置に生成"):
        state.hand[4] = card(4, "その他+", text, once=True)
        assert choose(state, mem) == "skip", text


def test_transition_full_throttle_and_soda_before_teach(monkeypatch):
    """实机 1-g R2 剩 6（本回合已先打 アイドルになります+）：过渡先打 教えてあげる+ 切強気、带着 フルスロットル+ Skip。
    CLEAR_FIRST：不占次数的 フルスロットル+ 先打；ソーダ 过渡不喝（留到下回合开局抽 2），也就不用为抽牌打 教えて。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    drive = card(0, DRIVE, "強気に変更パラメータ+4全力値+2スキルカード使用数追加+1眠気を山札のランダムな位置に生成次のターン、温存に変更")
    full = card(1, "フルスロットル+", "温存に変更スキルカード使用数追加+1スキルカードを3枚引く眠気を山札のランダムな位置に生成", once=True)
    teach = card(2, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    state, mem = case(drive, full, teach, deck=[REBOUND, ENTERTAINER], flower=2, drinks=(SODA, SODA))
    mem.this_turn = ["打 アイドルになります+"]
    assert choose(state, mem) == "play:1"
    state.hand.remove(full)
    state.hand.append(card(3, "眠気"))
    assert choose(state, mem) == "play:3"  # 不喝ソーダ、不打 教えて；眠気 清掉 → 牌组只剩 4 张核心
    monkeypatch.delenv("VEIL_CLEAR_FIRST")
    state.hand = [drive, full, teach]
    assert choose(state, mem) == "play:2"  # 关开关：原行为


def test_cycle_start_drinks_soda_to_complete_core(monkeypatch):
    """使用者 2026-11-07：抽牌不够留到下回合开局喝ソーダ（当场抽 2），补齐核心再进循环。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    state, mem = case(card(0, DRIVE), card(1, TEACH), card(2, ENTERTAINER), deck=[REBOUND], flower=2, drinks=(SODA,))
    assert choose(state, mem) == "drink:0"
    state.hand.append(card(3, REBOUND))
    state.drinks = []
    state.piles["deck"] = []
    assert choose(state, mem) == "play:0"  # 核心齐了：五卡循环第一张 オーバードライブ

def test_draw_last_prefers_small_draw_and_no_soda_on_full_hand(monkeypatch):
    """实机 1-g R2 再挑戦 T1：满手 5 张先打了 全身全霊（再动、抽 3，溢出），インフルエンサー（再动、抽 1）留后；
    之后満手又连喝三瓶センブリソーダ（抽 2 全作废）。"""
    for k in ("VEIL_DRAW_LAST", "VEIL_CLEAR_FIRST"):
        monkeypatch.setenv(k, "1")
    hand = [card(0, DRIVE, "強気に変更パラメータ+4全力値+2スキルカード使用数追加+1眠気を山札のランダムな位置に生成次のターン、温存に変更"),
            card(1, "ブランディングの基本", "強気2段階目に変更パラメータ+12", once=True),
            card(2, "全身全霊", "強気に変更次に使用したスキルカードの消費体力を0にする（2回）スキルカード使用数追加+1スキルカードを3枚引く", once=True),
            card(3, "インフルエンサー", "メンタルスキルカードの元気値増加+5・コスト値増加+1スキルカード使用数追加+1スキルカードを引く", once=True),
            card(4, "頂点へ+", "すべてのスキルカードのパラメータ値増加+6", once=True)]
    hand[1].available = False  # 指針を変更した回数が3回以上の場合、使用可：T1 发灰
    state, mem = case(*hand, status="温存", flower=3)
    assert choose(state, mem) == "play:3"
    # 满手、可打的牌比出牌次数少：不喝ソーダ（抽的牌放不下）
    sleepy = card(4, "眠気")
    sleepy.tags.is_sleep = True
    state, mem = case(card(0, DRIVE), card(1, "その他+", once=True), card(2, "その他2+", once=True), card(3, "その他3+", once=True),
                      sleepy, drinks=(SODA,), actions_left=6)
    assert not choose(state, mem).startswith("drink:")
    state.hand = state.hand[:2]
    assert choose(state, mem).startswith("drink:")  # 手牌放得下照喝


def test_clear_skips_reusable_when_no_once_card_follows(monkeypatch):
    """实机 1-g R2 再挑戦 剩 10：全力値 0、巻き返し+ 灰着，手里只剩复用牌 エンターテイナー+ 也打了（白用温存）。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    rebound = card(1, REBOUND, "全力値消費1温存に変更", once=True)
    rebound.available = False
    state, mem = case(card(0, ENTERTAINER, "強気に変更パラメータ+5"), rebound, status="のんびり", flower=3)
    state.remaining_turns = 10
    assert choose(state, mem) == "skip"
    state.remaining_turns = 1  # 最后一回合照打
    assert choose(state, mem) == "play:0"
    state.remaining_turns = 10
    state.hand.append(card(2, "頂点へ+", "パラメータ値増加+6", once=True))  # 后面有一次性牌可接：照常
    assert choose(state, mem) != "skip"
    monkeypatch.delenv("VEIL_CLEAR_FIRST")
    state.hand = state.hand[:2]
    assert choose(state, mem) == "play:0"


def test_cycle_keeps_warm_instead_of_last_reusable(monkeypatch):
    """实机 1-g R2 再挑戦 剩 7：アイドルになります+ 先打，OD → 教えて → 巻き返し 进温存后又打 エンターテイナー+，
    补的次数没牌接、温存白丢。CLEAR_FIRST：温存下最后一张复用牌后面没牌可接时 Skip。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    state, mem = case(card(0, ENTERTAINER, "強気に変更パラメータ+5"), deck=[], status="温存", flower=2)
    state.remaining_turns = 7
    mem.turn = state.turn
    mem.strategy_data["veil_cycle"] = {"turn": state.turn, "flower": 1, "fifth": "アイドルになります+", "idol_first": True}
    mem.this_turn = [f"打 {n}" for n in ("アイドルになります+", DRIVE, TEACH, REBOUND)]
    assert choose(state, mem) == "skip"
    state.remaining_turns = 1  # 最后一回合照打
    assert choose(state, mem) == "play:0"
    monkeypatch.delenv("VEIL_CLEAR_FIRST")
    state.remaining_turns = 7
    assert choose(state, mem) == "play:0"


def test_transition_cycle_entry_first_then_shed(monkeypatch):
    """使用者 2026-11-07：下回合能进循环第一优先，一次性牌不抢过渡牌的次数；预判下回合仍是过渡时先清一次性牌。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    teach = card(0, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    breath = card(1, "ひと呼吸+", "好調4ターン集中+5", once=True)
    rebound = card(2, REBOUND, "全力値消費1温存に変更熱意追加+6", once=True)
    # 夜空 3、温存：教えて → 強気（夜空 2），巻き返し 切回温存，2 次出牌够 → 下回合能进循环：先打 教えて，不先清 ひと呼吸
    state, mem = case(teach, breath, rebound, deck=[DRIVE, ENTERTAINER], status="温存", flower=3, actions_left=2)
    assert choose(state, mem) == "play:0"
    # 只有 1 次出牌：教えて 后切不回温存 → 下回合仍是过渡，先清 ひと呼吸
    state.actions_left = 1
    assert choose(state, mem) == "play:1"
    # 夜空 1：教えて 会变のんびり、夜空回到 3 → 下回合仍是过渡，先清
    state, mem = case(teach, breath, rebound, deck=[DRIVE, ENTERTAINER], status="温存", flower=1, actions_left=2)
    assert choose(state, mem) == "play:1"


def test_clear_order_search_keeps_warm_for_big_extra(monkeypatch):
    """使用者 2026-11-07（1-g R2 再挑戦2 剩 10）：のんびり、1 次出牌，第一印象（強気）/ 達成感（温存、+1）/ 全身全霊（強気、+2、抽 3）。
    第一印象 → 達成感 → 全身全霊（温存进全身全霊，解除 +1）比 達成感 先打多 1 次出牌。"""
    for k in ("VEIL_DRAW_LAST", "VEIL_CLEAR_FIRST"):
        monkeypatch.setenv(k, "1")
    hand = [card(0, "第一印象+", "強気に変更以降、強気効果のスキルカード使用時、すべてのスキルカードのパラメータ値増加+3", once=True),
            card(1, "達成感+", "温存に変更スキルカード使用数追加+1", once=True),
            card(2, "全身全霊+", "強気に変更スキルカード使用数追加+2スキルカードを3枚引く", once=True)]
    state, mem = case(*hand, status="のんびり", flower=3)
    assert choose(state, mem) == "play:0"


def test_remap_hand_keeps_customization_after_master_text():
    """实机 1-g R2 再挑戦3 剩 8：インフルエンサー 读成不带「+」，按牌组改名换主数据牌面后，客制化「強気追加」被冲掉 →
    账本当它不切強気，夜空少数一次。换完要再补客制化。"""
    from hiro.produce.veil_exam import remap_hand
    from hiro.brain.veil import destination

    seen = card(0, "インフルエンサー", "メンタルスキルカードの元気値増加+5スキルカード使用数追加+1スキルカードを引く", once=True)
    from types import SimpleNamespace

    seen.upgrade = 0
    master = SimpleNamespace(name="インフルエンサー+", _id=seen.card_id, upgrade_count=1,
                             produce_descriptions=None, play_move_position_type="Lost", cost_type=None,
                             cost_value=0, stamina=4, force_stamina=0, category="ProduceCardCategory_MentalSkill",
                             effect_display_text="メンタルスキルカードの元気値増加+6スキルカード使用数追加+1スキルカードを引く")
    out = remap_hand([seen], {"インフルエンサー": ("インフルエンサー+",)}, [{"card": "インフルエンサー+", "item": "強気追加"}],
                     cards={(seen.card_id, 1): master})
    assert out[0].name == "インフルエンサー+" and destination(out[0]) == "強気"


def test_nonbiri_overrides_warm():
    """使用者 2026-11-07：のんびり 覆盖温存：温存 → のんびり 可以，のんびり 下「温存に変更」仍是のんびり（1-g R2 再挑戦3 剩 9）。"""
    from hiro.brain.veil import after_play

    idol = card(0, "アイドルになります+", "全力値消費3元気+4温存に変更スキルカード使用数追加+1", once=True)
    assert after_play("のんびり", 3, idol) == ("のんびり", 3)
    assert after_play("強気", 2, idol) == ("温存", 2)
    assert after_play("のんびり", 3, card(1, ENTERTAINER, "強気に変更"))[0] == "強気"


def test_drinks_extra_turn_soup(monkeypatch):
    """使用者 2026-11-07：1-g R2 三次再挑戦都没喝初星湯（ターン追加+1）。进循环时喝；最后一回合还在也喝。"""
    from hiro.brain.veil import EXTRA_TURN

    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    cards = [card(i, n) for i, n in enumerate((DRIVE, REBOUND, TEACH, ENTERTAINER))]
    state, mem = case(*cards, deck=[], drinks=(EXTRA_TURN,))
    assert choose(state, mem) == "drink:0"  # 进循环
    state, mem = case(card(0, "その他+", once=True), drinks=(EXTRA_TURN,))
    state.remaining_turns = 1
    assert choose(state, mem) == "drink:0"  # 最后一回合
    state.remaining_turns = 5
    assert choose(state, mem) == "play:0"  # 清牌中不喝
    monkeypatch.delenv("VEIL_CLEAR_FIRST")
    state.remaining_turns = 1
    assert choose(state, mem) == "play:0"


def test_clear_order_ends_turn_warm(monkeypatch):
    """使用者 2026-11-07（1-h R1 T1）：喝ホエイ 后 2 次出牌，プライド+（温存）、心・技・体（強気）。先打心・技・体 再打プライド+，
    回合末留温存到下回合（下回合第一次切強気 +1）；不要温存→強気 拿一次用不掉的解除。第 1 回合还没切过姿态也照样推演。
    补充：一番星还有次数时下回合开局本来就会切のんびり，停在強気也行；能这样排是因为喝了ホエイ 多 1 次。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    pride = card(0, "プライド+", "レッスン開始時手札に入る温存に変更元気+8全力値+3", once=True)
    heart = card(1, "心・技・体", "強気2段階目に変更パラメータ+7全力値+3消費体力減少2ターン", once=True)
    state, mem = case(pride, heart, status=None, flower=3, actions_left=2)
    state.statuses = []
    assert choose(state, mem) == "play:1"
    state, mem = case(pride, heart, status="強気", flower=3, actions_left=2)
    assert choose(state, mem) == "play:1"
    # 一番星还有次数：下回合开局本来就会切のんびり，两种顺序一样好，不为留温存换顺序
    state, mem = case(pride, heart, status=None, flower=3, actions_left=2)
    state.statuses = []
    mem.strategy_data["veil_star"] = 3
    assert choose(state, mem) == "play:0"
    # 预约下回合温存也一样；预约 + 一番星同时有也只算一次（仍打平，不换）
    state, mem = case(pride, heart, status=None, flower=3, actions_left=2)
    state.statuses = [{"name": "次のターン開始時、温存に変更"}]
    assert choose(state, mem) == "play:0"
    mem.strategy_data["veil_star"] = 3
    assert choose(state, mem) == "play:0"


def test_warm_next_turn_sources_count_once():
    from hiro.brain.veil import warm_next_turn

    state, mem = case(card(0, "その他+", once=True), status="強気")
    assert not warm_next_turn(state, mem)
    state.statuses.append({"name": "次のターン開始時、温存に変更"})
    state.statuses.append({"name": "星々を見下ろす一番星", "value": 2})
    assert warm_next_turn(state, mem) is True
    state, mem = case(card(0, "その他+", once=True), status="強気")
    mem.strategy_data["veil_star"] = 1
    assert warm_next_turn(state, mem)


def test_star_turns_nonbiri_at_turn_start():
    """1-h R1 第 2 回合：一番星（以降 3 回、回合开始非温存就切のんびり），账本还记強気。"""
    from hiro.brain.veil import NEUTRAL, after_play, start_turn

    assert start_turn("強気", 3) == ("のんびり", 2)
    assert start_turn("温存", 3) == ("温存", 3)
    assert start_turn("のんびり", 2) == ("のんびり", 2)
    assert start_turn(NEUTRAL, 1) == ("のんびり", 0)
    assert start_turn("強気", 0) == ("強気", 0)
    assert start_turn("強気", None) == ("強気", None)
    heart = card(1, "心・技・体", "強気2段階目に変更パラメータ+7", once=True)
    assert after_play(NEUTRAL, 3, heart) == ("強気", 2)


def test_hand_swap_card_played_last(monkeypatch):
    """1-h R2 剩 10：強気、3 次出牌，仕切り直し+（手札をすべて入れ替える、+1、抽 2）先打，把一次性牌 頂点へ+ 换回牌库。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    monkeypatch.setenv("VEIL_HP_GUARD", "0")  # 合成 強気 没写段階，体力闸会把两张都当掉血牌留到温存
    reset = card(0, "仕切り直し+", "手札をすべて入れ替える消費体力減少4ターンスキルカード使用数追加+1スキルカードを2枚引く", once=True)
    peak = card(1, "頂点へ+", "すべてのスキルカードのパラメータ値増加+6以降、ターン開始時、いずれかの指針の場合、すべてのスキルカードのパラメータ値増加+4", once=True)
    state, mem = case(reset, peak, status="強気", flower=1, actions_left=3)
    assert choose(state, mem) == "play:1"


def test_full_power_transition_turn(monkeypatch):
    """使用者 2026-11-07（1-h R2 剩 2）：循环里进全力切不了姿态，多过渡一回合。夜空 2：教えて → 一张 OD 以外的牌（眠気）→ 结束；
    夜空 1：OD → 教えて → 眠気 → 结束。全力下強気效果照推夜空：2→1，1→3→2。"""
    from hiro.brain.veil import after_play

    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    names = ("眠気", ENTERTAINER, REBOUND, DRIVE, TEACH)
    hand = [card(i, n, "強気に変更" if n in (ENTERTAINER, DRIVE, TEACH) else "") for i, n in enumerate(names)]
    state, mem = case(*hand, status="全力", flower=2, deck=[], actions_left=3)
    state.remaining_turns = 3
    assert choose(state, mem) == "play:4"
    mem.this_turn.append(f"打 {TEACH}")
    state.hand = [c for c in state.hand if c.name != TEACH]
    assert choose(state, mem) == "play:0"
    mem.this_turn.append("打 眠気")
    state.hand = [c for c in state.hand if c.name != "眠気"]
    assert choose(state, mem) == "skip"

    state, mem = case(*hand, status="全力", flower=1, deck=[], actions_left=3)
    state.remaining_turns = 3
    assert choose(state, mem) == "play:3"
    assert after_play("全力", 1, hand[3]) == ("全力", 3)
    assert after_play("全力", 3, hand[4]) == ("全力", 2)
    assert after_play("全力", 2, hand[0]) == ("全力", 2)


def test_extra_turn_soup_starts_new_turn_at_same_remaining():
    """1-h R1 最后一回合喝初星湯：多出的回合画面剩余回合仍是 1，记忆没换回合，沿用上回合的循环记录停机。"""
    from hiro.brain.state import ExamMemory

    mem = ExamMemory("r1")
    assert mem.observe_turn(1) and mem.turn == 1
    mem.record_drink(DrinkView(0, "初星湯", "初星湯", "ターン追加+1体力消費2消費体力増加1ターン"))
    assert not mem.observe_turn(1)  # 回合还没结束
    mem.record_skip()
    assert mem.observe_turn(1) and mem.turn == 2 and mem.this_turn == []
    assert not mem.observe_turn(1)  # 只加一回合
    mem2 = ExamMemory("r1")
    mem2.observe_turn(1)
    mem2.record_drink(DrinkView(0, "初星湯", "初星湯", "ターン追加+1"))
    mem2.actions_left = 0  # 出牌次数用完
    mem2.expected_hand = ["眠気"]
    assert not mem2.observe_turn(1, ["眠気"])  # 手牌没变：账本可能少记次数，不算换回合
    assert not mem2.observe_turn(1)
    assert mem2.observe_turn(1, ["オーバードライブ+", TEACH]) and mem2.turn == 2
    mem3 = ExamMemory("r1")
    mem3.observe_turn(3)
    mem3.record_drink(DrinkView(0, "初星湯", "初星湯", "ターン追加+1"))
    assert not mem3.observe_turn(4)  # 剩余回合变大：加的回合已体现
    mem3.record_skip()
    assert not mem3.observe_turn(4)
    assert mem3.observe_turn(3)


@pytest.mark.parametrize("name, level, hp, cost, expected_loss, expected", [
    ("強気", 2, 14, 2, 5, "skip"),
    ("強気", 1, 14, 2, 4, "play:0"),
    ("強気", None, 14, 2, 5, "skip"),
    ("温存", 2, 11, 4, 1, "play:0"),
    ("温存", None, 11, 4, 2, "skip"),
    ("温存2段階目", None, 11, 4, 1, "play:0"),  # 识别给的是带段階的名字、没有层数
])
def test_stance_level_reaches_hp_guard(guard, name, level, hp, cost, expected_loss, expected):
    from collections import Counter

    from hiro.brain.veil import hp_loss, stance
    from hiro.produce.veil_exam import read_position

    warm = costly(0, "温存牌+", "温存に変更", cost)
    state, mem = case(warm, status=name)
    state.hp = hp
    state.statuses[0]["value"] = level
    assert read_position(state, mem, Counter(), lambda: 2) is None
    assert mem.strategy_data["veil_position"]["stance_level"] == (2 if name == "温存2段階目" else level)
    assert hp_loss(state, warm, stance(state)) == expected_loss
    assert choose(state, mem) == expected


def test_stance_level_updates_after_verified_play(guard):
    from collections import Counter

    from hiro.brain.rules import Decision
    from hiro.brain.veil import hp_loss, stance
    from hiro.produce.veil_exam import VeilBrain, read_position

    change = costly(0, "切换牌+", "強気2段階目に変更", 0)
    warm = costly(1, "温存牌+", "温存に変更", 2)
    state, mem = case(change, warm, status="温存")
    state.statuses[0]["value"] = 1
    mem.strategy_data["veil_deck"] = {change.name: 1, warm.name: 1}
    assert read_position(state, mem, Counter({change.name: 1, warm.name: 1}), lambda: 2) is None
    VeilBrain(None, None).commit(state, mem, Decision("play:0", "test", "rule"))
    state.hand, state.statuses, state.hp = [warm], [], 14
    mem.actions_left = 1
    assert read_position(state, mem, Counter({warm.name: 1}), lambda: 1) is None
    assert (stance(state), state.statuses[0]["value"]) == ("強気", 2)
    assert hp_loss(state, warm, stance(state)) == 5
    assert choose(state, mem) == "skip"


def test_remap_rebuilds_upgrade_and_action_count_without_guessing():
    from dataclasses import replace
    from types import SimpleNamespace

    from hiro.brain.rules import Decision
    from hiro.produce.veil_exam import VeilBrain, remap_hand

    seen = replace(costly(1, "全身全霊", "スキルカード使用数追加+1", 7),
                   upgrade=0, available=False, badge_score=123)
    master = SimpleNamespace(name="全身全霊+", _id=seen.card_id, upgrade_count=1,
                             produce_descriptions=None, play_move_position_type="Lost", cost_type="Focus",
                             cost_value=2, stamina=4, force_stamina=3, category="ProduceCardCategory_ActiveSkill",
                             effect_display_text="スキルカード使用数追加+2")
    cards = {(seen.card_id, 1): master}
    corrected, = remap_hand([seen], {"全身全霊": ("全身全霊+",)}, [], cards=cards)
    assert (corrected.name, corrected.upgrade, corrected.cost, corrected.hp_cost) == ("全身全霊+", 1, 4, 3)
    assert corrected.extra_cost == {"type": "Focus", "value": 2}
    assert corrected.text == master.effect_display_text and corrected.tags.extra_actions == 2
    assert (corrected.slot, corrected.available, corrected.badge_score) == (1, False, 123)
    state, mem = case(replace(corrected, available=True))
    mem.strategy_data["veil_deck"] = {corrected.name: 1}
    VeilBrain(None, None).commit(state, mem, Decision("play:1", "test", "rule"))
    assert mem.actions_left == 2 and mem.used_once == [corrected.name]
    assert remap_hand([seen], {"全身全霊": ("全身全霊", "全身全霊+")}, [], cards=cards) == [seen]
    # 考试中临时强化（画面比账本多「+」）：名字对回账本那一张，牌面效果、费用、强化等级保持画面那一级，不降级
    temporary = replace(corrected, name="全身全霊++", upgrade=2, text="スキルカード使用数追加+3", cost=2,
                        tags=replace(corrected.tags, extra_actions=3))
    kept, = remap_hand([temporary], {"全身全霊": ("全身全霊+",)}, [], cards=cards)
    assert kept == replace(temporary, name="全身全霊+")
    # 主数据查不到：只把名字对回账本（不让牌库记账对不上手牌）
    renamed, = remap_hand([seen], {"全身全霊": ("全身全霊+",)}, [], cards={})
    assert renamed == replace(seen, name="全身全霊+")


@pytest.mark.parametrize("turn, remaining, action", [(1, 9, "打"), (1, 9, "喝"), (4, 6, "打")])
def test_round_attempt_resumes_without_resetting_resources(tmp_path, turn, remaining, action):
    from hiro.exec.exam import save_exam_memory
    from hiro.produce.veil_exam import round_attempt

    directory = tmp_path / "exam"
    directory.mkdir()
    memory = ExamMemory("r1", turn=turn, last_remaining=remaining, history=[f"T{turn} {action} synthetic"],
                        actions_left=2, used_once=["spent-card"],
                        strategy_data={"veil_bar": ["remaining-drink"], "veil_deck": {"spent-card": 0}, "veil_fp": 4})
    save_exam_memory(directory, memory)
    selected, retry, restored = round_attempt(directory, "r1", first_turn=remaining == 9)
    assert selected == directory and retry is None
    assert restored[0].to_dict() == memory.to_dict()
    assert list(tmp_path.iterdir()) == [directory]


def test_round_attempt_starts_once_from_finished_checkpoint(tmp_path):
    from hiro.exec.exam import save_exam_memory
    from hiro.produce.veil_exam import round_attempt

    directory = tmp_path / "exam"
    directory.mkdir()
    previous = directory.with_name("exam-r1")
    previous.mkdir()
    save_exam_memory(directory, ExamMemory("r2", turn=1))
    save_exam_memory(previous, ExamMemory("r2", turn=12, actions_left=0), finished=True)
    with pytest.raises(ResumeBlocked):
        round_attempt(directory, "r2", first_turn=False)
    attempt, retry, restored = round_attempt(directory, "r2", first_turn=True)
    assert (attempt.name, retry, restored) == ("exam-attempt1", 1, None)
    attempt.mkdir()
    assert round_attempt(directory, "r2", first_turn=True) == (attempt, 1, None)
    new = ExamMemory("r2", turn=1, last_remaining=12, history=["T1 喝 synthetic"],
                     strategy_data={"veil_bar": []})
    save_exam_memory(attempt, new)
    assert round_attempt(directory, "r2", first_turn=True) == (attempt, 1, (new, attempt))
    assert directory.is_dir() and previous.is_dir()


def test_custom_master_flag_off_keeps_keyword_table(monkeypatch):
    from hiro.produce.veil_exam import apply_customs

    monkeypatch.delenv("HIRO_VEIL_CUSTOM_MASTER", raising=False)
    inf = card(0, "インフルエンサー+", "メンタルスキルカードの元気値増加+6")
    lookup = lambda name: ((("温存追加", ("次のターン、温存に変更",)),),)  # noqa: E731  开关关着时不该查
    out, = apply_customs([inf], [{"card": "インフルエンサー+", "item": "温存追加"}], lookup=lookup)
    assert out == inf
    out, = apply_customs([inf], [{"card": "インフルエンサー+", "item": "強気追加"}], lookup=lookup)
    assert out.text == "強気に変更" + inf.text


def test_custom_master_adds_delayed_stance_after_text(monkeypatch):
    """合成主数据：「温存追加」= 次のターン、温存に変更（加在原文后，不挤掉立即的強気）；OCR 菜单名带杂字也认得出。"""
    from hiro.brain.veil import destination, next_stance
    from hiro.produce.veil_exam import apply_customs

    monkeypatch.setenv("HIRO_VEIL_CUSTOM_MASTER", "1")
    table = {"インフルエンサー+": ((("強気追加", ("強気に変更",)),), (("温存追加", ("次のターン、温存に変更",)),))}
    lookup = lambda name: table.get(name, ())  # noqa: E731
    inf = card(0, "インフルエンサー+", "メンタルスキルカードの元気値増加+6スキルカード使用数追加+1")
    customs = [{"card": "インフルエンサー+", "item": "温存追加 70P"}, {"card": "インフルエンサー+", "item": "強気追加"}]
    out, = apply_customs([inf], customs, lookup=lookup)
    assert out.text == "強気に変更" + inf.text + "次のターン、温存に変更"
    assert destination(out) == "強気" and next_stance(out) == "温存"
    assert apply_customs([out], customs, lookup=lookup) == [out]  # 不重复加
    # 主数据查不到这张卡：退回关键词表
    other = card(1, "未知カード+", "パラメータ+3")
    got, = apply_customs([other], [{"card": "未知カード+", "item": "強気追加"}], lookup=lookup)
    assert got.text == "強気に変更パラメータ+3"


def test_custom_master_reads_real_master_data(monkeypatch):
    """真实主数据（有 game.db 时）：インフルエンサー+ 的「強気追加 / 温存追加」、全身全霊+ 的「元気追加」；数值类不收。"""
    from hiro.produce.veil_exam import master_custom_texts, master_customs

    master_customs.cache_clear()
    got = master_custom_texts("インフルエンサー+")
    if not got:
        pytest.skip("没有主数据")
    assert got == {"強気追加": ("強気に変更",), "温存追加": ("次のターン、温存に変更",)}
    assert master_custom_texts("全身全霊+") == {"元気追加": ("元気+9",)}
    assert master_custom_texts("全身全霊") == {}


GROW = ((("成長追加", ("すべてのスキルカードのパラメータ値増加+1",)), ("成長+", ("すべてのスキルカードのパラメータ値増加+2",)),
         ("成長+", ("すべてのスキルカードのパラメータ値増加+5",))),
        (("制限削除", ()),))


@pytest.mark.parametrize("records, expected", [
    (["成長追加"], "+1"),
    (["成長追加", "成長+"], "+2"),                       # 覆盖：第 2 级是 +2，不是 1+2
    (["成長追加", "成長+ 40P", "成長+"], "+5"),
    (["成長追加", "制限削除", "成長+"], "+2"),             # 中间夹别的项不打断这条链
    (["成長追加", "成長追加"], "+1"),                     # 同名两张、都做到第 1 级
    (["成長追加", "成長追加", "成長+"], None),            # 同名两张、不知道升的是哪张：不猜
    (["成長+"], None),                                   # 没开头就升级：不猜
    (["成長追加", "成長+", "成長+", "成長+"], None),      # 超过主数据级数
])
def test_custom_master_repeatable_levels_overwrite(monkeypatch, records, expected):
    from hiro.produce.veil_exam import apply_customs

    monkeypatch.setenv("HIRO_VEIL_CUSTOM_MASTER", "1")
    rebound = card(0, "巻き返し+", "全力値+4")
    out, = apply_customs([rebound], [{"card": "巻き返し+", "item": r} for r in records], lookup=lambda name: GROW)
    if expected is None:
        assert out == rebound
    else:
        assert out.text == "すべてのスキルカードのパラメータ値増加" + expected + "全力値+4"


def test_custom_master_real_repeatable_levels(monkeypatch):
    from hiro.produce.veil_exam import master_customs, resolve_customs

    master_customs.cache_clear()
    items = master_customs("巻き返し+")
    if not items:
        pytest.skip("没有主数据")
    monkeypatch.setenv("HIRO_VEIL_CUSTOM_MASTER", "1")
    grow = [lv for lv in items if lv[0][0] == "成長追加"]
    assert [d for d, _ in grow[0]] == ["成長追加", "成長+", "成長+"]
    records = [{"card": "巻き返し+", "item": i} for i in ("成長追加", "成長+")]
    assert resolve_customs(records)["巻き返し"] == ["すべてのスキルカードのパラメータ値増加+2"]
    records = [{"card": "トレーニングの成果+", "item": i} for i in ("成長追加", "成長+")]
    assert resolve_customs(records)["トレーニングの成果"] == ["手札のパラメータ値増加+8"]


def test_scheduled_stance_level_survives_turn_boundary(guard):
    from collections import Counter

    from hiro.brain.rules import Decision
    from hiro.brain.veil import stance
    from hiro.produce.veil_exam import VeilBrain, read_position

    schedule = costly(0, "予約牌+", "次のターン開始時、強気2段階目に変更", 0)
    warm = costly(0, "温存牌+", "温存に変更", 2)
    state, mem = case(schedule, status="温存")
    mem.strategy_data["veil_deck"] = {schedule.name: 1, warm.name: 1}
    assert read_position(state, mem, Counter({schedule.name: 1}), lambda: 2) is None
    VeilBrain(None, None).commit(state, mem, Decision("play:0", "synthetic", "rule"))
    state.statuses = []
    assert read_position(state, mem, Counter(), lambda: 2) is None
    mem.turn += 1
    mem.actions_left = 1
    state.statuses, state.hand, state.hp = [], [warm], 14
    assert read_position(state, mem, Counter({warm.name: 1}), lambda: 2) is None
    assert (stance(state), state.statuses[0]["value"]) == ("強気", 2)
    assert choose(state, mem) == "skip"


def test_custom_master_same_name_copies_only_shared_items(monkeypatch):
    """同名两张、只有一张做了某项：不知道手里是哪张，这项不补并记「版本不确定」；两张都做了才补。开关关时照旧。"""
    from hiro.produce.veil_exam import apply_customs

    table = {"インフルエンサー+": ((("強気追加", ("強気に変更",)),), (("温存追加", ("次のターン、温存に変更",)),))}
    lookup = lambda name: table.get(name, ())  # noqa: E731
    inf = card(0, "インフルエンサー+", "元気+6")
    one = [{"card": "インフルエンサー+", "item": "強気追加"}]
    monkeypatch.setenv("HIRO_VEIL_CUSTOM_MASTER", "1")
    uncertain = []
    out, = apply_customs([inf], one, lookup=lookup, copies={"インフルエンサー": 2}, uncertain=uncertain)
    assert out == inf and uncertain == ["インフルエンサー+:強気追加"]
    out, = apply_customs([inf], one * 2, lookup=lookup, copies={"インフルエンサー": 2})
    assert out.text == "強気に変更元気+6"
    out, = apply_customs([inf], one, lookup=lookup, copies={"インフルエンサー": 1})
    assert out.text == "強気に変更元気+6"
    monkeypatch.delenv("HIRO_VEIL_CUSTOM_MASTER")
    out, = apply_customs([inf], one, lookup=lookup, copies={"インフルエンサー": 2})
    assert out.text == "強気に変更元気+6"  # 开关关：关键词表，行为不变


@pytest.mark.parametrize("env, expected", [
    ({"VEIL_DRAW_LAST": "1"}, True),
    ({"HIRO_VEIL_DRAW_LAST": "1"}, True),                              # 旧名仍认
    ({"VEIL_DRAW_LAST": "0", "HIRO_VEIL_DRAW_LAST": "1"}, False),     # 两个都设：新名为准
    ({"HIRO_VEIL_DRAW_LAST": "0"}, False),                             # 旧名也能关
    ({}, True),                                                         # 默认开（2026-11-07 回放后）
])
def test_strategy_flags_drop_hiro_prefix(monkeypatch, env, expected):
    from hiro.brain import veil

    for k in ("VEIL_DRAW_LAST", "HIRO_VEIL_DRAW_LAST"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert veil.draw_last() is expected


def test_strategy_flag_defaults(monkeypatch):
    """2026-11-07 回放后：HP_GUARD / RELEASE_GAIN / DRAW_LAST / DRIVE_FP / FP_CAP 默认开；CLEAR_FIRST / FP_RESERVE / KEEP_WARM 默认关。"""
    from hiro.brain import veil

    for name in veil.STRATEGY_FLAGS:
        monkeypatch.delenv(f"VEIL_{name}", raising=False)
        monkeypatch.delenv(f"HIRO_VEIL_{name}", raising=False)
    on = {f.__name__ for f in (veil.hp_guard, veil.release_gain, veil.draw_last, veil.drive_fp, veil.fp_cap,
                               veil.draw_all, veil.last_turn_play, veil.cycle_soda, veil.stuck_soda) if f()}
    # 2026-11-08 复盘后 DRAW_ALL / LAST_TURN / CYCLE_SODA / STUCK_SODA 也默认开
    assert on == {"hp_guard", "release_gain", "draw_last", "drive_fp", "fp_cap",
                  "draw_all", "last_turn_play", "cycle_soda", "stuck_soda"}
    assert not (veil.clear_first() or veil.fp_reserve() or veil.keep_warm() or veil.hp_net())
    assert not hasattr(veil, "free_extra")


def test_release_gain_yields_to_clear_first_without_whey(monkeypatch):
    """CLEAR_FIRST 开、温存 1 次出牌、没有ホエイ：一次性 頂点へ+ 先打，不让 教えてあげる+ 切強気插队；有ホエイ 时照旧先打加次数的牌。"""
    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    teach = card(0, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    peak = card(1, "頂点へ+", "すべてのスキルカードのパラメータ値増加+6", once=True)
    state, mem = case(teach, peak)
    assert choose(state, mem) == "play:1"
    state, mem = case(teach, peak, drinks=(WHEY,))
    assert choose(state, mem) == "play:0"


# ---------------------------------------------------------------- 2026-11-08 trusted trace 复盘（使用者确认的错误决策）
def test_draw_all_schedules_teach_when_next_draw_short_of_deck(monkeypatch):
    """3-c 第 2 局 R2 T6：牌库 5 张（核心 4 + ティーパーティ+），ソーダ 让下回合抽 4 张，夜空 2、のんびり。
    原来抽 4 张就算够、连空 4 回合；VEIL_DRAW_ALL=1：抽不满牌库就先打 教えてあげる+ 预约多抽。"""
    tea = card(0, "ティーパーティ+", "スキルカード使用数追加+1スキルカードを引く", once=True)
    teach = card(1, TEACH, "強気に変更パラメータ+6次のターン、スキルカードを2枚引く")
    hand = [tea, teach, card(2, DRIVE), card(3, REBOUND)]
    state, mem = case(*hand, deck=[ENTERTAINER], status="のんびり", flower=2)
    state.piles["next_draw"] = 4
    monkeypatch.setenv("VEIL_DRAW_ALL", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_DRAW_ALL", "1")
    assert choose(state, mem) == "play:1"
    state.piles["next_draw"] = 5  # 抽得满：不用预约
    assert choose(state, mem) == "skip"


def test_last_turn_plays_instead_of_waiting(monkeypatch):
    """3 第 2 局 R1 attempt2 T9（剩 1）：过渡里手有 エンターテイナー+、1 次出牌却「等待下回合」。VEIL_LAST_TURN=1：照打。"""
    rebound = card(2, REBOUND, "全力値消費1温存に変更")
    rebound.available = False
    hand = [card(0, ENTERTAINER, "強気に変更パラメータ+12"), card(1, "眠気"), rebound]
    state, mem = case(*hand, deck=[DRIVE], grave=[TEACH], status="強気", flower=2)
    state.remaining_turns = 1
    monkeypatch.setenv("VEIL_LAST_TURN", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_LAST_TURN", "1")
    assert choose(state, mem) != "skip"
    state.hand = [card(1, DRIVE, "強気に変更パラメータ+4")]  # 只剩 オーバードライブ+：最后一回合也打
    assert choose(state, mem) == "play:1"


def test_cycle_soda_waits_for_hand_room(monkeypatch):
    """满 5 张进循环：VEIL_CYCLE_SODA=1 时不喝ソーダ（抽的 2 张作废），ホエイ / ブースト 照喝；最后一回合照喝。"""
    cards = [card(i, n) for i, n in enumerate([DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"])]
    state, mem = case(*cards, deck=[], drinks=(SODA, BOOST))
    monkeypatch.setenv("VEIL_CYCLE_SODA", "1")
    assert choose(state, mem) == "drink:1"
    state.drinks = [d for d in state.drinks if d.name == SODA]
    assert choose(state, mem) == "play:0"
    state.remaining_turns = 1
    assert choose(state, mem) == "drink:0"


def test_stuck_soda_draws_when_nothing_playable(monkeypatch):
    """3-3 第 1 局 R1 attempt2 T3：手牌空、1 次出牌、有ソーダ 却跳过。VEIL_STUCK_SODA=1：喝ソーダ 抽 2 继续清。"""
    state, mem = case(card(0, DRIVE), drinks=(SODA,), status="のんびり", flower=3, actions_left=1)
    monkeypatch.setenv("VEIL_STUCK_SODA", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_STUCK_SODA", "1")
    assert choose(state, mem) == "drink:0"
    state.hand = [card(i, f"一次性{i}", once=True) for i in range(4)]
    for c in state.hand:
        c.available = False
    assert choose(state, mem) == "skip"  # 手牌放不下 2 张：不喝


def test_hp_net_lets_genki_card_and_core_through(guard, monkeypatch):
    """使用者 2026-11-08：体力闸太保守。みずたまりスキップ+（体力消費1、元気+8）加的元気比花的多，可用体力不减；
    核心打分牌（エンターテイナー+）也不拦。打完体力至少剩 1。"""
    skip_card = costly(0, "みずたまりスキップ+", "体力消費1元気+8スキルカード使用数追加+1", 0, hp_cost=1)
    state, mem = case(skip_card, status="強気")
    state.statuses[0]["value"] = 2
    state.hp = 11  # 強気 2：1×2 + 1 = 3 → 剩 8 < 10
    monkeypatch.setenv("VEIL_HP_NET", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_HP_NET", "1")
    assert choose(state, mem) == "play:0"
    ent = costly(1, ENTERTAINER, "体力消費5強気に変更パラメータ+12", 0, once=False, hp_cost=5)
    state, mem = case(ent, status="温存")
    state.statuses[0]["value"] = 2
    state.hp = 12
    monkeypatch.setenv("VEIL_HP_NET", "0")
    assert choose(state, mem) == "skip"
    monkeypatch.setenv("VEIL_HP_NET", "1")
    assert choose(state, mem) == "play:1"
    state.hp = 5  # 打完 0：仍拦
    assert choose(state, mem) == "skip"
