"""状态不变量（docs/state-events.md §4）：规则单测 + 实机 trace 回放验收（trace 不在仓库里时跳过）。"""

from pathlib import Path

import pytest

from hiro.contracts import Check, GameState, Observation, PageObs
from hiro.loop.reduce import reduce
from hiro.state.invariants import check_invariants, filter_observation

TRACES = Path(__file__).resolve().parents[1] / "traces"


def obs(seq: int, page: str, **fields) -> Observation:
    return Observation(seq=seq, t=0.0, page=PageObs(page, "produce"), fields=fields)


def state_after(*observations: Observation) -> GameState:
    s = GameState()
    for o in observations:
        s = reduce(s, o)
    return s


def rules(checks, verdict="read_error"):
    return {(c.field, c.rule) for c in checks if c.verdict == verdict}


ACTION = dict(stamina=20, max_stamina=33, produce_point=380, star=345, param_cap=3000)


def test_param_drop_leading_digit():
    """第六局 #290：Da 902 → 70（画面 1070），param_monotonic 拦下，filter 后决策看到的仍是 902。"""
    prev = state_after(obs(1, "ACTION_SELECT", params={"Vo": 827, "Da": 902, "Vi": 486}, **ACTION))
    new = obs(2, "ACTION_SELECT", params={"Vo": 910, "Da": 70, "Vi": 486}, **ACTION)
    checks = check_invariants(prev, new)
    assert ("produce.params.Da", "param_monotonic") in rules(checks)
    assert not any(c.field.endswith(".Vo") for c in checks)  # Vo 910 正常上涨不报
    s = reduce(prev, filter_observation(new, checks, prev))
    assert s.produce["params"] == {"Vo": 910, "Da": 902, "Vi": 486}


def test_digit_count_on_param():
    prev = state_after(obs(1, "ACTION_SELECT", params={"Vo": 1227, "Da": 1312, "Vi": 591}, **ACTION))
    checks = check_invariants(prev, obs(2, "ACTION_SELECT", params={"Vo": 27, "Da": 1312, "Vi": 591}, **ACTION))
    assert ("produce.params.Vo", "digit_count") in rules(checks)


def test_range_and_star():
    prev = state_after(obs(1, "ACTION_SELECT", params={"Vo": 500, "Da": 500, "Vi": 500}, **ACTION))
    checks = check_invariants(prev, obs(2, "ACTION_SELECT", params={"Vo": 3500, "Da": 500, "Vi": 500},
                                        **{**ACTION, "stamina": 40, "star": 34}))  # fmt: skip
    assert {("produce.params.Vo", "range"), ("produce.stamina", "range"), ("produce.star", "star_monotonic"),
            ("produce.star", "digit_count")} <= rules(checks)  # fmt: skip


def test_produce_point_spend_is_not_digit_loss():
    """P 点正常花费（105 → 5）不算掉位；只有去掉末位（380 → 38）才算。"""
    prev = state_after(obs(1, "ACTION_SELECT", params={"Vo": 1, "Da": 1, "Vi": 1}, **{**ACTION, "produce_point": 105}))
    assert not check_invariants(prev, obs(2, "ACTION_SELECT", **{**ACTION, "produce_point": 5}))
    prev = state_after(obs(1, "ACTION_SELECT", **ACTION))
    assert ("produce.produce_point", "digit_count") in rules(check_invariants(prev, obs(2, "ACTION_SELECT", **{**ACTION, "produce_point": 38})))


def exam(seq, score, rank, npcs, turns):
    return obs(seq, "HIF_EXAM", score=score, my_rank_index=rank, npc_scores=npcs, remaining_turns=turns)


def test_score_monotonic_and_digit_count():
    """局3：13987 → 1403（画面 14031）。"""
    prev = state_after(exam(1, 13987, 1, [5287, 2180, 2150, 2113], 2))
    checks = check_invariants(prev, exam(2, 1403, 1, [5287, 2180, 2150, 2113], 1))
    assert {("exam.score", "score_monotonic"), ("exam.score", "rank_consistency")} <= rules(checks)


def test_rank_consistency_without_previous_value():
    """局3 续跑段第一帧：2231（画面 22311），第 2 名应在 11845..40816 之间——没有上一帧也能抓。"""
    checks = check_invariants(GameState(), exam(13, 2231, 2, [40816, 11845, 10906, 8096], 2))
    assert ("exam.score", "rank_consistency") in rules(checks)


def test_rank_conflict_blames_npc_when_player_is_continuous():
    """第六局 seq 331：玩家 41261 正确，对手 32450 吞进「9」读成 324509——判对手，不丢玩家分数。"""
    prev = state_after(exam(330, 25233, 2, [28397, 8233, 5749, 5094], 5))
    checks = check_invariants(prev, exam(331, 41261, 1, [324509, 9518, 6661, 6044], 4))
    assert rules(checks) == {("exam.npc_scores", "rank_consistency")}


def test_npc_order():
    checks = check_invariants(GameState(), exam(1, 0, 1, [0, 1, 0, 0], 12))
    assert ("exam.npc_scores", "rank_consistency") in rules(checks)


def test_new_exam_is_not_a_score_drop():
    """上一场最后 14031（剩 1 回合）→ 下一场 0（剩 12 回合）：新的一场，不报。"""
    prev = state_after(exam(1, 14031, 1, [5287, 2180, 2150, 2113], 1))
    assert not check_invariants(prev, exam(2, 0, 1, [0, 0, 0, 0], 12))


def test_turn_step():
    prev = state_after(exam(1, 100, 1, [50, 40, 30, 20], 5))
    assert ("exam.remaining_turns", "turn_step") in rules(check_invariants(prev, exam(2, 200, 1, [60, 50, 40, 30], 6)), "model_gap")
    assert ("exam.remaining_turns", "turn_step") in rules(check_invariants(prev, exam(2, 200, 1, [60, 50, 40, 30], 2)), "model_gap")
    assert not check_invariants(prev, exam(2, 200, 1, [60, 50, 40, 30], 4))


def test_counters_and_drinks():
    prev = state_after(obs(1, "SELECT_CARD", rerolls_left=1, excludes_left=0))
    assert ("page_fields.rerolls_left", "counters") in rules(check_invariants(prev, obs(2, "SELECT_CARD", rerolls_left=2, excludes_left=0)))
    prev = state_after(exam(1, 100, 1, [50, 40, 30, 20], 5))
    assert ("exam.drinks", "drink_capacity") in rules(check_invariants(prev, obs(2, "HIF_EXAM", drinks=[{"name": "烏龍茶"}] * 5, remaining_turns=5)))


def test_p_delta_with_events():
    prev = state_after(obs(1, "ACTION_SELECT", **ACTION))
    events = [{"kind": "SHOP_BUY", "args": {"price": 50}}]
    assert not check_invariants(prev, obs(2, "ACTION_SELECT", **{**ACTION, "produce_point": 330}), events=events)
    assert ("produce.produce_point", "p_delta") in rules(
        check_invariants(prev, obs(2, "ACTION_SELECT", **{**ACTION, "produce_point": 300}), events=events), "model_gap")


def test_debug_lists_consistent_checks():
    prev = state_after(obs(1, "ACTION_SELECT", params={"Vo": 500, "Da": 500, "Vi": 500}, **ACTION))
    checks = check_invariants(prev, obs(2, "ACTION_SELECT", params={"Vo": 510, "Da": 500, "Vi": 500}, **ACTION), debug=True)
    assert checks and all(isinstance(c, Check) and c.verdict == "consistent" and not c.problems() for c in checks)


# ------------------------------------------------------------ 实机 trace 回放验收（H1）
CASES = [
    ("lead35-qualify2-20261031-115639", "lead35-qualify2-20261031-115639-r3", 48, "exam.score", 3771),
    ("lead35-qualify2-20261031-115639", "lead35-qualify2-20261031-115639-r2", 125, "produce.params.Da", 73),
    ("lead35-qualify3-20261031-142043", "lead35-qualify3-20261031-142043", 200, "exam.score", 1403),
    ("lead35-qualify3-20261031-142043", "lead35-qualify3-20261031-142043-r2", 13, "exam.score", 2231),
    ("lead35-qualify6-20261031-232418", "lead35-qualify6-20261031-232418", 290, "produce.params.Da", 70),
]


@pytest.mark.parametrize(("run", "segment", "seq", "field", "value"), CASES)
def test_trace_read_errors_caught(run, segment, seq, field, value):
    if not (TRACES / run).exists():
        pytest.skip("trace 不在本机")
    from hiro.state.invariants_replay import replay

    res = replay(TRACES / run)
    hits = [c for c in res["checks"] if c["segment"] == segment and c["seq"] == seq and c["field"] == field]
    assert hits and all(c["verdict"] == "read_error" and c["observed"] == value for c in hits)
    if field.startswith("produce.params"):
        attr = field.rsplit(".", 1)[1]
        seen = [d["params"][attr] for d in res["decisions"] if d["segment"] == segment and d["seq"] == seq]
        assert seen and all(v != value for v in seen)  # 读错不进入决策


# ------------------------------------------------------------ 与识别侧事件（hiro.vision.events，basis=observe_only）对接
def ev(kind, basis="action", **args):
    return {"kind": kind, "basis": basis, "args": args}


def test_acquire_popup_confirms_action_not_extra_card():
    """相談买卡：动作事件 SHOP_BUY + 交换弹窗 SHOP_BUY + 获得弹窗 CARD_ACQUIRE，是同一张卡。"""
    from hiro.state.invariants import deck_delta, p_spend

    events = [ev("SHOP_BUY", card_id="c1", upgrade=0), ev("SHOP_BUY", "observe_only", card_id="c1", upgrade=0, price=120),
              ev("CARD_ACQUIRE", "observe_only", card_id="c1", upgrade=0)]  # fmt: skip
    assert deck_delta(events) == 1
    assert p_spend(events) == 120  # 动作事件没价格，用画面读到的


def test_two_purchases_of_same_card_count_twice():
    from hiro.state.invariants import deck_delta

    events = [ev("CARD_ACQUIRE", "observe_only", card_id="c1", upgrade=0)] * 2
    assert deck_delta(events) == 2


def test_drink_purchase_and_delete_with_price():
    """买饮料不进牌组；CARD_DELETE（识别侧）带 price，算 P 点花费。"""
    from hiro.state.invariants import deck_delta, p_spend

    events = [ev("SHOP_BUY", "observe_only", drink_id="d1", price=60), ev("CARD_DELETE", "observe_only", card_id="c2", upgrade=1, price=50)]
    assert deck_delta(events) == -1
    assert p_spend(events) == 110


# ------------------------------------------------------------ 倍率与考前说明页（exam_session.brief_permil）
def brief_state():
    s = GameState()
    s.exam_session.brief_permil = {"Vo": 14810, "Da": 12470, "Vi": 10850}
    return s


def mult(seq, attr, permil):
    return obs(seq, "HIF_EXAM", attr=attr, multiplier_permil=permil, remaining_turns=8)


def test_multiplier_matches_brief():
    assert not check_invariants(brief_state(), mult(1, "Vo", 14810))


def test_multiplier_first_digit_covered():
    """Vi 实际 1481%，回合转盘箭头盖住首位读成 7481%；1085% 多读一位成 11085%。"""
    assert ("exam.multiplier_permil", "attr_multiplier") in rules(check_invariants(brief_state(), mult(1, "Vo", 74810)))
    assert ("exam.multiplier_permil", "attr_multiplier") in rules(check_invariants(brief_state(), mult(1, "Vi", 110850)))


def test_attr_misread_when_multiplier_belongs_to_other_attr():
    assert rules(check_invariants(brief_state(), mult(1, "Vi", 12470))) == {("exam.attr", "attr_multiplier")}


def test_no_brief_no_check():
    assert not check_invariants(GameState(), mult(1, "Vo", 74810))


def test_bad_multiplier_not_used_after_filter():
    """说明页 → 进考试读到 7481%：过滤后决策看到的仍是说明页的 1481%。"""
    s = state_after(obs(1, "HIF_EXAM_BRIEFING", multiplier_pct={"Vo": 1481, "Da": 1247, "Vi": 1085}))
    assert s.exam_session.brief_permil == {"Vo": 14810, "Da": 12470, "Vi": 10850}
    o = mult(2, "Vo", 74810)
    checks = check_invariants(s, o)
    s = reduce(s, filter_observation(o, checks, s))
    assert s.exam["multiplier_permil"] == 14810
