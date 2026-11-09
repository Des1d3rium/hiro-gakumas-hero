"""VEIL 包成考试插件：VeilScreen（ExamStateSource，带账本）+ VeilAdvisor（ExamAdvisor）。

核对：两个类满足插件协议；决策要的账局面来源不提供时开考前报错；VEIL 路线没另外指定时用自己的缺省、指定了用使用者的；
插件单例每轮按 setup 从头初始化；经 commit 钩子记账与旧的 VeilBrain.commit 逐字段相同；连打只接循环出牌。
"""

import json
from collections import Counter

import pytest

from hiro import plugins
from hiro.brain.battle import BattleBrain
from hiro.brain.rules import Decision
from hiro.brain.veil import CYCLE_MARK, DRIVE, ENTERTAINER, REBOUND, TEACH, VeilAdvisor
from hiro.defaults.exam_state import ScreenExamStateSource

from test_cycle_tap import decide, start


@pytest.fixture(autouse=True)
def _clean_plugins(monkeypatch):
    for kind in plugins.KINDS:
        monkeypatch.delenv(plugins.KINDS[kind][1], raising=False)
    plugins.reset()
    yield
    plugins.reset()


def test_veil_classes_satisfy_plugin_protocols():
    from hiro.produce.veil_exam import VeilScreen

    assert isinstance(VeilScreen(), plugins.ExamStateSource)
    assert isinstance(VeilAdvisor(), plugins.ExamAdvisor)


def test_advisor_needs_veil_ledger_from_source():
    from hiro.produce.veil_exam import VeilScreen

    with pytest.raises(TypeError, match="veil-ledger"):
        plugins.check_exam_pair(ScreenExamStateSource(), VeilAdvisor())
    plugins.check_exam_pair(VeilScreen(), VeilAdvisor())
    plugins.check_exam_pair(VeilScreen(), object())  # 不要账的决策器配带账的来源没问题


def test_route_default_uses_veil_unless_user_overrides(monkeypatch):
    from hiro.produce.veil_exam import VeilScreen, veil_plugins

    source, advisor = veil_plugins()
    assert type(source) is VeilScreen and type(advisor) is VeilAdvisor
    assert veil_plugins()[0] is source  # 进程内单例

    plugins.reset()
    monkeypatch.setenv("HIRO_EXAM_ADVISOR", "hiro.defaults.advisor:RuleLLMAdvisor")
    source, advisor = veil_plugins()
    assert type(source) is VeilScreen and type(advisor).__name__ == "RuleLLMAdvisor"


def test_begin_with_setup_reinitializes_singleton():
    from hiro.produce.veil_exam import VeilScreen

    screen = VeilScreen()
    screen.begin({"setup": {"deck": {DRIVE: 1}, "once": set(), "ledger_drinks": ["初星湯"], "fill": 2}})
    screen.data = {"veil_deck": {"残り": 1}}
    screen.position_retries = 3
    screen.begin({"setup": {"deck": {TEACH: 2}, "once": {TEACH}}, "rec": "rec"})
    assert screen.deck == {TEACH: 2} and screen.once == {TEACH}
    assert screen.ledger_drinks == [] and screen.fill == 0
    assert screen.data is None and screen.position_retries == 0 and screen.rec == "rec"
    assert screen.copies == Counter({TEACH.rstrip("+"): 2})


def test_begin_without_setup_keeps_constructor_ledger():
    from hiro.produce.veil_exam import VeilScreen

    screen = VeilScreen({DRIVE: 1}, set(), fill=1)
    screen.begin({"rec": None})
    assert screen.deck == {DRIVE: 1} and screen.fill == 1


def _dump(memory):
    return json.loads(json.dumps(memory.to_dict(), default=str))


def test_commit_hook_matches_old_brain_commit_for_a_whole_turn():
    """同一回合逐手打完：旧 VeilBrain.commit 与 BattleBrain + VeilScreen.commit 钩子，出牌顺序相同，
    每手之后的记忆（含 veil_* 账、撤回快照）逐字段相同。"""
    from hiro.brain.state import BattleState
    from hiro.brain.veil import norm
    from hiro.produce.veil_exam import VeilBrain, VeilScreen, read_position
    from test_cycle_tap import card

    names = ["眠気", TEACH, DRIVE, ENTERTAINER, REBOUND]

    def play(commit):
        state, memory = start(names)
        order, dumps = [], []
        while True:
            _, d = decide(state, memory)
            if d.kind != "play":
                break
            order.append(norm(state.hand[d.slot].name))
            commit(state, memory, d)
            dumps.append(_dump(memory))
            rest = [c for c in state.hand if c.slot != d.slot]
            state = BattleState("r1", 1, 8, 40, 44, 0, [card(i, c.name) for i, c in enumerate(rest)], statuses=[],
                                pitem_charges=None, piles={"deck": [], "grave": [], "next_draw": 5},
                                actions_left=memory.actions_left)
            assert read_position(state, memory, Counter(c.name for c in state.hand), lambda: None) is None
            dumps.append(_dump(memory))
        return order, dumps

    old = play(VeilBrain(None, None).commit)
    brain, screen = BattleBrain(None, None), VeilScreen()
    new = play(lambda s, m, d: plugins.exam_commit(screen, brain.commit, s, m, d))
    assert old[0] == [DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"]
    assert old == new


def test_default_source_commit_is_plain_brain_commit():
    state, memory = start([TEACH, DRIVE, ENTERTAINER, REBOUND])
    calls = []
    plugins.exam_commit(ScreenExamStateSource(), lambda s, m, d: calls.append(d.action), state, memory,
                        Decision("play:0", "x", "rule"))
    assert calls == ["play:0"]


def test_burst_only_for_cycle_decisions():
    adv = VeilAdvisor()
    assert adv.burst_ok(Decision("play:0", f"{CYCLE_MARK}：エンターテイナー+", "rule"))
    assert not adv.burst_ok(Decision("play:0", "清牌：眠気", "rule"))


def test_run_burst_needs_advisor_burst_hook(monkeypatch):
    """决策器没有 burst_ok（如默认 RuleLLMAdvisor）：不连打，回整帧读取。"""
    from hiro.exec import cycle_tap
    from hiro.produce.veil_exam import VeilScreen

    monkeypatch.setenv("VEIL_CYCLE_TAP", "1")
    state, memory = start([DRIVE, REBOUND, TEACH, ENTERTAINER])
    _, d = decide(state, memory)
    assert cycle_tap.run_burst(state, memory, d, brain=BattleBrain(None, None), source=VeilScreen(), advisor=object(),
                               exam="r1") == 0


def test_run_exam_checks_pair_before_touching_game():
    from hiro.exec.exam import run_exam

    class Rec:
        def log(self, *a, **k):
            raise AssertionError("不该开考")

    with pytest.raises(TypeError, match="veil-ledger"):
        run_exam(BattleBrain(None, None), "r1", Rec(), lambda: True, source=ScreenExamStateSource(), advisor=VeilAdvisor())


def test_trace_meta_names_the_veil_plugins_actually_used():
    """run_round 先取插件再写 meta：meta 记实际用的类，不是取之前的「default:通用实现」。"""
    from hiro.produce.veil_exam import veil_plugins

    assert plugins.source("exam_state_source").startswith("default:")  # 取之前
    source, advisor = veil_plugins()
    used = {"exam_state_source": source, "exam_advisor": advisor}
    meta = {k: f"{type(o).__module__}:{type(o).__name__}（{plugins.source(k)}）" for k, o in used.items()}
    assert meta == {"exam_state_source": "hiro.produce.veil_exam:VeilScreen（register）",
                    "exam_advisor": "hiro.brain.veil:VeilAdvisor（register）"}
