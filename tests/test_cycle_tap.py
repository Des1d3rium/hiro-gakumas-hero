"""循环回合连打（VEIL_CYCLE_TAP）：按 n 张布局推点击点；连打的账本和整帧路径逐张记账一致；执行器按偏移点对格子。"""

from collections import Counter

import pytest

from hiro.brain.cards import CardView, parse_tags
from hiro.brain.rules import Decision
from hiro.brain.state import BattleState, ExamMemory
from hiro.brain.veil import DRIVE, ENTERTAINER, REBOUND, TEACH, VeilAdvisor, norm, stance
from hiro.contracts import Action
from hiro.exec.cycle_tap import CARD_H, CARD_W, is_cycle_play, tap_point
from hiro.vision.hif import HAND_LAYOUT

TEXT = {
    DRIVE: "強気に変更スキルカード使用数追加+1次のターン、温存に変更",
    REBOUND: "温存に変更スキルカード使用数追加+1",
    TEACH: "強気に変更スキルカード使用数追加+1",
    ENTERTAINER: "強気に変更スキルカード使用数追加+1",
    "眠気": "",
}


def card(slot, name):
    return CardView(slot, name, name, 1, True, "mental", 0, 0, None, TEXT[name], parse_tags(name, TEXT[name], once=name == "眠気"))


def start(names, *, flower=2):
    from hiro.produce.veil_exam import read_position

    state = BattleState("r1", 1, 8, 40, 44, 0, [card(i, n) for i, n in enumerate(names)], statuses=[{"name": "温存"}],
                        pitem_charges=flower, piles={"deck": [], "grave": [], "next_draw": 5})
    memory = ExamMemory("r1", turn=1)
    memory.strategy_data["veil_deck"] = dict(Counter(names))
    assert read_position(state, memory, Counter(names), lambda: flower, calibrate=True) is None
    return state, memory


def decide(state, memory):
    contract = VeilAdvisor().decide(state, memory, None, [Action.from_legal(a) for a in state.legal_actions()])
    return contract, contract.to_battle()


@pytest.fixture
def tap_on(monkeypatch):
    monkeypatch.setenv("VEIL_CYCLE_TAP", "1")


def test_tap_point_matches_kaa_card_center_for_every_hand_size():
    """布局里的首字母框中心 x → KAA 卡面框（字母中心往左 57、上缘往上 148）的中心。"""
    from kotonebot.primitives import Rect

    top = 1012
    for n, xs in HAND_LAYOUT.items():
        for i, x in enumerate(xs):
            rect = Rect.from_xyxy(x - CARD_W, top - CARD_H, x, top)
            px, py = tap_point(n, i, top)
            assert abs(px - rect.center.x) <= 1 and abs(py - rect.center.y) <= 1


def test_flag_default_off(monkeypatch):
    from hiro.brain.veil import cycle_tap
    from hiro.produce.veil_exam import VeilBrain, VeilScreen

    monkeypatch.delenv("VEIL_CYCLE_TAP", raising=False)
    monkeypatch.delenv("HIRO_VEIL_CYCLE_TAP", raising=False)
    assert not cycle_tap()
    state, memory = start([DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"])
    _, d = decide(state, memory)
    VeilBrain(None, None).commit(state, memory, d)
    assert VeilScreen({}, set()).burst_begin(state, memory, d) is None


def test_burst_ledger_matches_full_read_path(tap_on):
    """同一回合：整帧路径（每张记账后按真实剩余手牌 read_position 核对）与连打（burst_begin + burst_settle）出牌顺序、账本一致。"""
    from hiro.produce.veil_exam import VeilBrain, VeilScreen, read_position

    names = ["眠気", TEACH, DRIVE, ENTERTAINER, REBOUND]
    brain, screen = VeilBrain(None, None), VeilScreen({}, set())

    # 整帧路径
    state, memory = start(names)
    full = []
    while True:
        _, d = decide(state, memory)
        if d.kind != "play":
            break
        full.append(norm(state.hand[d.slot].name))
        brain.commit(state, memory, d)
        rest = [c for c in state.hand if c.slot != d.slot]
        state = BattleState("r1", 1, 8, 40, 44, 0, [card(i, c.name) for i, c in enumerate(rest)], statuses=[],
                            pitem_charges=None, piles={"deck": [], "grave": [], "next_draw": 5},
                            actions_left=memory.actions_left)
        assert read_position(state, memory, Counter(c.name for c in state.hand), lambda: None) is None  # 画面夜空读不到：只按账本
    expected = (full, memory.strategy_data["veil_position"], memory.actions_left, list(memory.this_turn))

    # 连打：第一张整帧，之后只推局面
    state, memory = start(names)
    burst = []
    _, d = decide(state, memory)
    burst.append(norm(state.hand[d.slot].name))
    brain.commit(state, memory, d)
    st = screen.burst_begin(state, memory, d)
    while st is not None:
        assert screen.burst_settle(st, memory, [False] * len(st.hand))
        contract, d = decide(st, memory)
        if d.kind != "play":
            break
        assert "五卡循环" in contract.reason and is_cycle_play(contract)
        burst.append(norm(st.hand[d.slot].name))
        brain.commit(st, memory, d)
        if memory.actions_left <= 0:
            break
        st = screen.burst_begin(st, memory, d)
    assert full == [DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"]
    assert (burst, memory.strategy_data["veil_position"], memory.actions_left, list(memory.this_turn)) == expected


def test_burst_settle_greys_and_stance(tap_on):
    from hiro.produce.veil_exam import VeilBrain, VeilScreen

    state, memory = start([DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"])
    _, d = decide(state, memory)
    VeilBrain(None, None).commit(state, memory, d)
    screen = VeilScreen({}, set())
    st = screen.burst_begin(state, memory, d)
    assert [c.slot for c in st.hand] == [0, 1, 2, 3] and [c.name for c in st.hand] == [REBOUND, TEACH, ENTERTAINER, "眠気"]
    assert not screen.burst_settle(st, memory, [False] * 3)  # 张数对不上：不结算
    assert "veil_pending_play" in memory.strategy_data
    assert screen.burst_settle(st, memory, [True, False, False, False])
    assert "veil_pending_play" not in memory.strategy_data
    assert stance(st) == "強気" and not st.hand[0].available
    # 下一张（巻き返し+）画面发灰：决策不再是循环出牌 → 执行器会退回整帧读取
    contract, nxt = decide(st, memory)
    assert not (nxt.kind == "play" and is_cycle_play(contract))


def test_burst_not_started_when_card_draws_or_turn_spent(tap_on):
    from hiro.produce.veil_exam import VeilBrain, VeilScreen

    state, memory = start([DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"])
    _, d = decide(state, memory)
    VeilBrain(None, None).commit(state, memory, d)
    screen = VeilScreen({}, set())
    memory.expected_hand = None  # 打出的牌会抽牌：手牌推不出
    assert screen.burst_begin(state, memory, d) is None
    memory.expected_hand = []
    memory.actions_left = 0
    assert screen.burst_begin(state, memory, d) is None


def test_run_burst_taps_layout_offsets(tap_on, monkeypatch):
    """执行器：第一张（オーバードライブ+，第 3 格）整帧打出后，剩下四张按 4 / 3 / 2 / 1 张布局点对格子。"""
    from hiro.exec import cycle_tap
    from hiro.exec import exam as ex
    from hiro.produce.veil_exam import VeilBrain, VeilScreen

    top = 1012
    taps = []
    monkeypatch.setattr(cycle_tap, "wait_hand", lambda n, remaining: ((object(), [(x, top, False) for x in HAND_LAYOUT[n]]), None))
    monkeypatch.setattr(cycle_tap, "_wait_change", lambda before: True)
    monkeypatch.setattr(ex, "_lite_sig", lambda img: None)
    monkeypatch.setattr(ex, "_play_missed", lambda frame, confirmed: False)
    monkeypatch.setattr(ex, "_play_not_landed", lambda frame, x, name=None: None)

    def play(x, y, img=None, name=None):
        taps.append((x, y, name))
        return [[x, y]]

    monkeypatch.setattr(ex, "_play_card", play)
    names = ["眠気", TEACH, DRIVE, ENTERTAINER, REBOUND]
    state, memory = start(names)
    brain = VeilBrain(None, None)
    _, d = decide(state, memory)
    assert d.action == "play:2"
    brain.commit(state, memory, d)
    done = cycle_tap.run_burst(state, memory, d, brain=brain, source=VeilScreen({}, set()), advisor=VeilAdvisor(), exam="r1")
    assert done == 4
    want = [(4, 3, REBOUND), (3, 1, TEACH), (2, 1, ENTERTAINER), (1, 0, "眠気")]
    assert taps == [(*tap_point(n, i, top), name) for n, i, name in want]
    assert [a for a in memory.this_turn] == [f"打 {n}" for n in (DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気")]


def test_run_burst_stops_when_hand_count_differs(tap_on, monkeypatch):
    """画面张数和推出来的不一样（打出后多抽了牌）：一张都不点，pending 留给整帧读取核对。"""
    from hiro.exec import cycle_tap
    from hiro.exec import exam as ex
    from hiro.produce.veil_exam import VeilBrain, VeilScreen

    monkeypatch.setattr(cycle_tap, "wait_hand", lambda n, remaining: (None, "count:5"))
    monkeypatch.setattr(ex, "_play_card", lambda *a, **k: pytest.fail("不该点"))
    state, memory = start([DRIVE, REBOUND, TEACH, ENTERTAINER, "眠気"])
    brain = VeilBrain(None, None)
    _, d = decide(state, memory)
    brain.commit(state, memory, d)
    assert cycle_tap.run_burst(state, memory, d, brain=brain, source=VeilScreen({}, set()), advisor=VeilAdvisor(), exam="r1") == 0
    assert memory.strategy_data["veil_pending_play"]["card"] == DRIVE


def test_cycle_play_is_marked_by_phase_not_reason():
    """连打只认决策器写的阶段（Decision.plan[0]["phase"] == "cycle"），理由文字里有「五卡循环」不算。"""
    from hiro.contracts import Decision

    act = Action.from_legal("play:0")
    assert is_cycle_play(Decision(act, "随便写的理由", "rule", plan=[{"phase": "cycle"}]))
    assert not is_cycle_play(Decision(act, "五卡循环：エンターテイナー+", "rule"))
    assert not is_cycle_play(Decision(act, "清牌", "rule", plan=[{"phase": "clear"}]))
