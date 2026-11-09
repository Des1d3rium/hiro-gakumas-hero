"""hiro.brain.play 的通用部分：回合推演（sim）、序列搜索（planner）、循环顺序求解（cycle.solve_orders）。"""

import pytest

from hiro.brain.cards import CardView, parse_tags
from hiro.brain.play import cycle, planner, sim
from hiro.brain.play.profile import CORE, ORDERS

pytestmark = pytest.mark.synthetic


def card(name, text, *, slot=0, once=False):
    return CardView(slot, name, name, 1, True, "mental", 0, 0, None, text, parse_tags(name, text, once=once))


def _master(name):
    from hiro.brain import card_fx
    from hiro.brain.cards import description_text
    from hiro.state.catalog import catalog

    try:
        row = catalog().db.execute(
            "SELECT id, upgradeCount, produceDescriptions FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        pytest.skip("没有主数据")
    text = description_text(row[2])
    return CardView(0, name, row[0], row[1], True, "active", 0, 0, None, text, parse_tags(name, text),
                    fx=card_fx.card_fx(row[0], row[1]))


def test_release_modes(monkeypatch):
    """温存解除：dest 看牌自己切強気；after 看夜空覆盖后的姿态（夜空 1 → のんびり 就不算）。"""
    monkeypatch.setenv("VEIL_PITEM_FLOWER", "0")
    strong = card("強気牌", "強気に変更")
    assert sim.gain(strong, "温存", 1, mode="dest") == 1
    assert sim.gain(strong, "温存", 1, mode="after") == 0
    assert sim.gain(strong, "温存", 2, mode="after") == 1
    assert sim.gain(strong, "強気", 2, mode="dest") == 0  # 不在温存没有解除
    assert sim.gain(strong, "温存", 2, ability="play") == 2  # 回忆能力再动


def test_turnstate_step_and_new_turn(monkeypatch):
    monkeypatch.setenv("VEIL_PITEM_FLOWER", "0")
    drive = card("オーバードライブ+", "強気に変更スキルカード使用数追加+1全力値+2次のターン、温存に変更")
    ts = sim.TurnState(stance="温存", flower=2, acts=1, fp=0).step(drive)
    assert (ts.stance, ts.flower, ts.acts, ts.fp, ts.next_stance) == ("強気", 1, 2, 2, "温存")
    nt = ts.new_turn()
    assert (nt.stance, nt.acts, nt.next_stance, nt.turn) == ("温存", 1, None, 1)


def test_resolve_matches_after_play(monkeypatch):
    monkeypatch.setenv("VEIL_PITEM_FLOWER", "0")
    strong = card("強気牌", "強気に変更")
    assert sim.resolve("温存", 1, 1, strong)[0::2] == sim.after_play("温存", 1, strong)


def test_planner_best_keeps_first_on_ties():
    top = planner.best([1, 2, 3], lambda o: (len(o),), prefixes=True, max_len=2)
    assert top == ((2,), (1, 2))
    assert planner.best([1, 2], lambda o: None) is None


def test_solve_orders_contains_user_orders():
    """只用主数据推演、不看得分：使用者给的 VEIL 循环顺序（夜空 1 / 2）都在「回到同一开局状态、还剩 1 次出牌」的顺序里。"""
    cores = [_master(name) for name in sorted(CORE)]
    found = cycle.solve_orders(cores, fp=4)
    for f, order in ORDERS.items():
        assert order in [o for acts, o in found[f] if acts >= 1], (f, found[f])


def test_fp_condition_uses_play_time_stance(monkeypatch):
    """涙の思い出「温存に変更…強気の場合、全力値+3」：条件看出牌时的姿态（实机 11-09：強気 打 5 次都 +3，温存 / のんびり 都 +0）。
    VEIL_FP_PLAY_STANCE 关时照旧按切完的姿态（温存）算，永远 0。"""
    tear = _master("涙の思い出")
    if not (tear.fx or {}).get("fp_cond"):
        pytest.skip("主数据里没有条件全力値")
    monkeypatch.setenv("VEIL_FP_PLAY_STANCE", "1")
    assert sim.fp_delta(tear, "強気")[0] >= 3
    assert sim.fp_delta(tear, "温存")[0] == 0
    monkeypatch.setenv("VEIL_FP_PLAY_STANCE", "0")
    assert sim.fp_delta(tear, "強気")[0] == 0


# ---------------------------------------------------------------- 通用附魔引擎（VEIL_ENCHANT_RUNTIME）

def _item_id(name):
    from hiro.state.catalog import catalog

    try:
        row = catalog().db.execute("SELECT id FROM ProduceItem WHERE name = ? AND isUpgraded = 0 LIMIT 1", (name,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    if row is None:
        pytest.skip("没有主数据")
    return row[0]


@pytest.mark.parametrize("before, flower, want", [
    ("温存", 1, ("のんびり", 3)),  # 计数到了：夜空切のんびり、回到 3
    ("温存", 2, ("強気", 1)),
    ("指針なし", 2, ("強気", 2)),  # 没有指针不计数
    ("全力", 1, ("全力", 3)),  # 全力 下切不了，计数照样走
])
def test_runtime_flower_matches_legacy(monkeypatch, before, flower, want):
    """夜空走引擎和原来的规则结果相同。"""
    strong = _master("オーバードライブ+")  # 強気に変更
    monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "0")
    legacy = sim.after_play(before, flower, strong)
    monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1")
    assert sim.after_play(before, flower, strong) == legacy == want


def test_runtime_star(monkeypatch):
    monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1")
    if sim._start_turn_spec() is None:
        pytest.skip("没有主数据")
    assert sim.start_turn("強気", 3) == ("のんびり", 2)
    assert sim.start_turn("温存", 3) == ("温存", 3)  # 温存状態：不触发、不扣次数
    star = _master(sim.STAR)
    ts = sim.TurnState(stance="強気", flower=3, acts=2, fp=0).step(star)
    assert ts.star == 3 and ts.enchants == ()  # 打出一番星：装上回合开始附魔，记进 star
    assert ts.new_turn().stance == "のんびり"


def test_support_item_lesson_and_limit():
    """支援卡道具：只在对应属性的回合触发、次数用完不再触发（天川ラーメン巡り：ボーカルターンのみ、ターン開始時、使用数追加+1、1回）。"""
    from hiro.brain.play import enchants as eng

    specs = eng.pitem_specs(_item_id("天川ラーメン巡り"), "support")
    assert specs and specs[0].trigger.lesson == "Vo" and specs[0].limit == 1
    insts = (eng.install(specs[0]),)
    _, fired = eng.dispatch(insts, eng.Event(eng.START_TURN, turn=1, attribute="Da"))
    assert fired == []
    insts, fired = eng.dispatch(insts, eng.Event(eng.START_TURN, turn=1, attribute="Vo"))
    assert fired[0][1].delta["actions"] == 1 and insts[0].left == 0
    _, fired = eng.dispatch(insts, eng.Event(eng.START_TURN, turn=2, attribute="Vo"))
    assert fired == []
    _, fired = eng.dispatch((eng.install(specs[0]),), eng.Event(eng.START_TURN, turn=1))
    assert fired[0][1] is None  # 属性不知道：判断不了，不猜


def test_turnstate_runs_installed_enchants(monkeypatch):
    """TurnState 里装上的附魔在回合开始触发，结果落到出牌次数上。"""
    from hiro.brain.play import enchants as eng

    monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1")
    spec = eng.pitem_specs(_item_id("天川ラーメン巡り"), "support")[0]
    spec = eng.Spec(spec.key, spec.source, sim.replace(spec.trigger, lesson=None), spec.effects, spec.limit)
    ts = sim.TurnState(stance="強気", flower=3, acts=1, fp=0, enchants=(eng.install(spec),))
    nt = ts.new_turn()
    assert nt.acts == 2 and nt.enchants[0].left == 0
    assert nt.new_turn().acts == 1


# ---------------------------------------------------------------- 全力过渡按规划打完（VEIL_FULL_PLAN，1-q R2）

def _full_case():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).resolve().parents[1] / "examples/cases/veil-1q-r2-full.json").read_text(encoding="utf-8"))


def _full_decide(case, step):
    from hiro.brain.state import BattleState, ExamMemory
    from hiro.brain.veil import VeilAdvisor
    from hiro.contracts import Action

    hand = [card(n, "", slot=i) for i, n in enumerate(step["hand"])]
    state = BattleState("r2", case["turn"], case["remaining"], 40, 44, 0, hand, statuses=[{"name": case["stance"]}],
                        pitem_charges=step["flower"],
                        piles={"deck": list(step["deck"]), "grave": list(step["grave"]), "next_draw": 3})
    memory = ExamMemory("r2", turn=case["turn"])
    memory.this_turn = [f"打 {n}" for n in step["played"]]
    memory.strategy_data["veil_full"] = dict(case["full"])
    memory.strategy_data["veil_cycle"] = {"turn": case["turn"] - 1, "flower": 1, "fifth": "眠気"}
    d = VeilAdvisor().decide(state, memory, None, [Action.from_legal(a) for a in state.legal_actions()])
    key = d.action.legal_key()
    return (hand[int(key.split(":")[1])].name if key.startswith("play:") else key), d.reason


@pytest.mark.parametrize("i", range(3))
def test_full_power_transition_keeps_plan(i, monkeypatch):
    """1-q R2 剩 2：全力过渡（夜空 1，OD → 教えてあげる+ → 眠気）打完 OD 生成眠気，牌库 6 张、小牌组不成立。
    VEIL_FULL_PLAN 关：照旧（落到清牌打眠気 / 按张数收尾打巻き返し+）；开：接着按规划打 教えてあげる+、再收尾眠気。"""
    case = _full_case()
    step = case["steps"][i]
    monkeypatch.setenv("VEIL_FULL_PLAN", "0")
    if step["actual"] is not None:
        assert _full_decide(case, step)[0] == step["actual"]
    monkeypatch.setenv("VEIL_FULL_PLAN", "1")
    name, reason = _full_decide(case, step)
    assert name == step["want"] and reason.startswith("全力过渡"), reason


def test_full_plan_done_counts_plan_steps_by_name():
    from hiro.brain.play.profile import DRIVE, TEACH

    plan = [DRIVE, TEACH, None]
    assert cycle.full_plan_done(plan, []) == 0
    assert cycle.full_plan_done(plan, [DRIVE, "眠気"]) == 1  # 眠気 是被清牌打掉的，不是规划里的一步
    assert cycle.full_plan_done(plan, [DRIVE, "眠気", TEACH]) == 2
    assert cycle.full_plan_done(plan, [DRIVE, TEACH, "眠気"]) == 3
    assert cycle.full_plan_done([TEACH, None], [TEACH, DRIVE]) == 1  # 收尾不算 OD
