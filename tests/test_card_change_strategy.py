# 来源：digital-hiro tests/test_card_change_strategy.py（c3fc27f）
"""HiroHifStrategy.on_skill_card_change：按决策的 card_id 换出；决策取消时传 cancel；阶段 1 候选未识别时换入第 0 张。"""

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, ExecutionResult, GameState
from hiro.produce.hif import HiroHifStrategy

DAYDREAM, HIGH, BASIC = "p_card-02-act-1_037", "p_card-00-men-2_014", "p_card-02-act-0_037"


class StubSession:
    def __init__(self, fields_by_page):
        self.fields_by_page, self.state, self.records, self.notes = fields_by_page, GameState(), [], []

    def observe(self, scene, **_):
        self.state = GameState(page_fields=dict(self.fields_by_page.get(scene, {})))
        return scene

    def verify(self, action, *, page_before, **_):
        return ExecutionResult(action=action, ok=True, page_before=page_before)

    def record(self, obs, **kw):
        self.records.append((obs, kw))

    def note(self, text):
        self.notes.append(text)


class StubCtx:
    def __init__(self, stage):
        self.stage, self.calls = stage, []

    def commit_stage1(self, index):
        self.calls.append(("s1", index))
        return True

    def commit_stage2(self, index, card_id=None, cancel=False, remove_index=None, remove_upgrade=None):
        self.calls.append(("s2", card_id, cancel))
        self.stage2_args = (remove_index, remove_upgrade)
        return not cancel


def _strategy(fields):
    st = HiroHifStrategy.__new__(HiroHifStrategy)
    st.s = StubSession(fields)
    st.deciders = ProduceDeciders(CAMPUS_HIRO, None, change_controlled=True)
    return st


def test_change_removes_decided_card_not_slot0():
    grid = [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}, {"index": 1, "card_id": HIGH, "upgrade": 0}, {"index": 2, "card_id": BASIC, "upgrade": 0}]
    st, ctx = _strategy({"SKILL_CARD_CHANGE_2": {"cards": grid}}), StubCtx(stage=1)
    st.on_skill_card_change(ctx)
    assert ctx.calls[0] == ("s1", 0)  # 阶段 1 候选未识别 → 第 0 张
    assert ctx.calls[1] == ("s2", HIGH, False)  # 冲突卡先换出（用户 2026-11-03：冲突牌 > 初始卡），デイドリーミング 不动
    assert ctx.stage2_args == (1, 0)  # 决策的格子序号 / 强化次数一起交给阶段 2（第十九局：同名卡多张时只按 card_id 点错了）
    assert len(st.s.records) == 2 and all(kw["execution"].ok for _, kw in st.s.records)


def test_change_cancels_when_deck_unread():
    st, ctx = _strategy({}), StubCtx(stage=2)
    st.CHANGE_OBSERVE_TRIES = 1
    st.on_skill_card_change(ctx)
    assert ctx.calls == [("s2", None, True)]
    assert st.s.records[0][1]["decision"].action.kind is ActionKind.CHANGE_CARD


def test_change_cancel_then_return_forces_lowest_value():
    """取消后又回到换卡页（阶段 1 无取消键，main8 死循环）：按 choose_card_to_change 换出价值最低的非保护卡；没有可换的才停下。"""
    import pytest

    from hiro.produce.hif import UnhandledPage

    grid = [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}, {"index": 1, "card_id": BASIC, "upgrade": 0}]
    st = _strategy({"SKILL_CARD_CHANGE_2": {"cards": grid}})
    st.CHANGE_OBSERVE_TRIES = 1
    st.s.recover = lambda *a: None
    st.deciders.decide = lambda page, state, **kw: __import__("hiro.contracts", fromlist=["Decision"]).Decision(
        __import__("hiro.contracts", fromlist=["Action"]).Action(ActionKind.CHANGE_CARD, {"cancel": True}), "不换", "rule", 0.7)
    ctx = StubCtx(stage=2)
    st.on_skill_card_change(ctx)
    st.on_skill_card_change(ctx)
    assert ctx.calls == [("s2", None, True), ("s2", BASIC, False)]
    st2 = _strategy({"SKILL_CARD_CHANGE_2": {"cards": [{"index": 0, "card_id": DAYDREAM, "upgrade": 1}]}})
    st2.CHANGE_OBSERVE_TRIES = 1
    st2.s.recover = lambda *a: None
    st2.deciders.decide = st.deciders.decide
    st2._change_cancels = 1
    with pytest.raises(UnhandledPage):
        st2.on_skill_card_change(StubCtx(stage=2))


def test_stage1_pick_mapped_to_column_by_center():
    """识别多读一张时，决策的候选序号按卡面中心 x 换算成列（qualify6 r2 #29：#3 せのびの魔法 在第 2 列）。"""
    from hiro.contracts import Action, ActionKind, Decision

    cands = [{"index": 0, "center": [223, 899]}, {"index": 1, "center": [310, 860]}, {"index": 2, "center": [362, 899]},
             {"index": 3, "center": [500, 898]}]  # fmt: skip
    st = _strategy({"SKILL_CARD_CHANGE_1": {"candidates": cands}})
    st.s.observe("SKILL_CARD_CHANGE_1")
    d = Decision(Action(ActionKind.CHANGE_CARD, {"stage": 1, "pick_index": 3}), "", "rule", 1.0)
    assert st._stage1_column(3, d) == 2 and st._stage1_column(0, d) == 0
