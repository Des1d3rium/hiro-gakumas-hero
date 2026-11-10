"""账本侧的附魔驱动（hiro.brain.pitem_engine，结算交给 hiro.brain.play.enchants；开关 VEIL_ENCHANT_RUNTIME）：
装道具、各触发时机、条件判不了返回 None、发动次数、Common 牌规则、开关关着时账本逐位不变。没有主数据时跳过。"""

import json
from collections import Counter

import pytest

from hiro.brain import pitem_engine as E
from hiro.brain import pitems as P
from hiro.brain.state import BattleState, ExamMemory

VEIL = "i_card-skin-hmsz-3-016"
PARKA = "img_general_pitem_3-258"
PARKA_WARM = "pitem_00-3-258-0"  # センスのスキルカード使用後、温存に変更（レッスン内5回）
PARKA_VEIL = "pitem_3-258-research-event-008"  # センスのスキルカード使用後、パラメータ上昇量増加10%（实机 VEIL 带的那版）
ROD = "img_general_pitem_3-270"  # H.I.F応援棒（青）：考试开始补牌


@pytest.fixture(autouse=True)
def master():
    try:
        ok = P.load_pitem(PARKA_WARM) is not None
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        pytest.skip("没有主数据")


def _view(name):
    from hiro.brain import card_fx
    from hiro.brain.cards import CardView, description_text, parse_tags
    from hiro.state.catalog import catalog

    row = catalog().db.execute("SELECT id, upgradeCount, produceDescriptions FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    text = description_text(row[2])
    return CardView(0, name, row[0], row[1], True, "active", 0, 0, None, text, parse_tags(name, text),
                    fx=card_fx.card_fx(row[0], row[1]))


def _engine(*items):
    st = E._new_state(None)
    for i in items:
        E._install(st, i, "test")
    return {E.KEY: st}, st


def _fired(st):
    return {k: v["fired"] for k, v in st["insts"].items() if v.get("fired")}


def _eng():
    from hiro.brain.play import anomaly  # noqa: F401
    from hiro.brain.play import enchants

    return enchants


def _state(remaining=8, genki=0, hp=30, max_hp=40):
    s = BattleState("r1", 1, remaining, hp, max_hp, 0, [], statuses=[], pitem_charges=None)
    s.genki = genki
    return s


# ---------------------------------------------------------------- 装道具


def test_observe_installs_ring_and_column_items():
    data = {}
    st = E.observe(data, [{"asset": PARKA}, {"asset": ROD}], VEIL, {PARKA: PARKA_VEIL})
    assert st["ring"] == P.FLOWER_ITEM  # 夜空を従える花+：画面道具列里没有，按偶像卡找
    assert [e["item"] for e in st["items"]] == [PARKA_VEIL, "pitem_03-3-270-0"]
    assert st["unsure"] == []


def test_ambiguous_asset_is_not_installed():
    """ゼミパーカー 图标对应 7 种效果：没有档案提示就不装、记进 unsure（asset 写法 pitem_3-258 也认）。"""
    data = {}
    st = E.observe(data, [{"asset": "pitem_3-258", "name": "ゼミパーカー"}], VEIL, {})
    assert st["items"] == [] and st["unsure"][0]["asset"] == PARKA and st["unsure"][0]["signatures"] > 1
    assert E.resolve(PARKA, {PARKA: "pitem_03-3-270-0"})[0] is None  # 提示写错（不是这个图标的道具）也不装


def test_profile_hint_maps_parka():
    assert E.profile_hints().get(PARKA) == PARKA_VEIL
    assert E.signature(PARKA_VEIL) == E.signature("pitem_3-258-research-event-010")


def test_flower_in_column_is_left_to_ring_and_late_items_marked():
    data = {}
    st = E.observe(data, [], VEIL)
    assert st["items"] == [] and st["ring"] == P.FLOWER_ITEM
    E.observe(data, [{"asset": "img_general_pitem_3-303"}], VEIL)
    assert st["items"] == [] and st["unsure"] == []  # 夜空两版都是偶像专属：ring 已管
    st["boundary"] = {"turn": 1}
    E.observe(data, [{"asset": ROD}], VEIL)
    assert st["items"][0].get("late") is True


def test_ring_matches_hardcoded_flower():
    """夜空按偶像找到的道具就是原来写死的那个；按通用引擎判「算不算这一手」和 pitems 原规则一致。"""
    from hiro.brain.play import sim

    assert E.ring_item(VEIL) == P.FLOWER_ITEM and sim._counter_spec().key == P.FLOWER_ITEM
    eng = _eng()
    trig = sim._counter_spec().trigger
    for name in ("オーバードライブ+", "ひと呼吸+", "仕切り直し+"):
        card = _view(name)
        for stance in ("強気", "温存", "のんびり", "指針なし", "指針解除", "全力"):
            ok = eng.conditions(trig, eng.Event(eng.CARD_PLAY_AFTER, stance=stance, current=stance))
            got = eng.search_matches(trig.search, P.view_profile(card)) if ok else ok
            assert got == P.flower_counts(card, stance), (name, stance)


def test_unsupported_phase_recorded():
    _, st = _engine("pitem_tower_001-exam_concentration-1-stage_019")  # 直接効果で指針を変更するたび（StanceChangeCountInterval）
    assert st["unsupported"] and "StanceChangeCountInterval" in st["unsupported"][0]["why"]


# ---------------------------------------------------------------- Common 牌 / 出牌后


def test_common_card_does_not_trigger_plan_search(monkeypatch):  # noqa: D103
    """ゼミパーカー（搜索 Plan1）：实机通用牌 138 次不发动、Plan1 牌 10 次都发动（2026-11-09 trace 核对）。"""
    data, st = _engine(PARKA_WARM)
    mem = ExamMemory("r1", turn=2)
    common = _view("仕切り直し+")
    assert P.card_search("p_card_search-plan1-target").matches(P.view_profile(common)) is True  # pitems 原规则：Common 算
    assert E.after_play(data, mem, _state(), common, "強気", "強気", 1) == ("強気", 1)
    assert _fired(st) == {}
    monkeypatch.setattr(_eng(), "COMMON_PLAN_MATCHES", None)  # 未核对模式：只因 Common 才匹配 → 不知道，不改账本
    assert E.after_play(data, mem, _state(), common, "強気", "強気", 1) == ("強気", 1)
    assert _fired(st) == {} and st["log"][-1]["what"] == "判断不了"


def test_plan1_card_triggers_warm_parka_with_limit():
    data, st = _engine(PARKA_WARM)
    mem = ExamMemory("r1", turn=2)
    sense = _view("ひと呼吸+")
    assert E.after_play(data, mem, _state(), sense, "強気", "強気", 2) == ("温存", 1)
    assert E.after_play(data, mem, _state(), sense, "のんびり", "のんびり", None) == ("のんびり", None)  # のんびり 覆盖温存
    assert E.after_play(data, mem, _state(), sense, "全力", "全力", None) == ("全力", None)  # 全力 下切不了
    assert _fired(st)[PARKA_WARM] == 3
    for _ in range(2):
        E.after_play(data, mem, _state(), sense, "強気", "強気", 1)
    assert _fired(st)[PARKA_WARM] == 5
    assert E.after_play(data, mem, _state(), sense, "強気", "強気", 1) == ("強気", 1)  # レッスン内5回 用完
    assert _fired(st)[PARKA_WARM] == 5 and st["insts"][PARKA_WARM]["left"] == 0


def test_veil_parka_fires_without_changing_ledger():
    data, st = _engine(PARKA_VEIL)
    mem = ExamMemory("r1", turn=2)
    assert E.after_play(data, mem, _state(), _view("ひと呼吸+"), "強気", "強気", 1) == ("強気", 1)
    assert _fired(st) == {PARKA_VEIL: 1} and st["log"][-1]["score"]


def test_card_play_after_adds_actions():
    data, st = _engine("pitem_00-3-017-0")  # アクティブスキルカード使用後、好調1ターンスキルカード使用数追加+1（1回）
    mem = ExamMemory("r1", turn=2)
    mem.actions_left = 0
    E.after_play(data, mem, _state(), _view("オーバードライブ+"), "強気", "強気", 1)
    assert mem.actions_left == 1
    E.after_play(data, mem, _state(), _view("オーバードライブ+"), "強気", "強気", 1)
    assert mem.actions_left == 1  # 一场 1 次
    data, _ = _engine("pitem_00-3-017-0")
    E.after_play(data, mem, _state(), _view("巻き返し+"), "強気", "温存", 1)  # メンタル：不算
    assert mem.actions_left == 1


def test_card_play_condition_unsupported_is_unknown():
    data, st = _engine("pitem_00-3-018-0")  # スキルカード使用時、集中が5以上の場合（集中：账本没有）
    mem = ExamMemory("r1", turn=2)
    mem.actions_left = 0
    E.after_play(data, mem, _state(), _view("オーバードライブ+"), "強気", "強気", 1)
    assert mem.actions_left == 0 and _fired(st) == {} and st["log"][-1]["what"] == "判断不了"


def test_turn_count_interval_and_timer_next_stance():
    """全力の場合、ターン内にスキルカードを3回使用するごとに…次のターン、温存2段階目に変更：回合内计数、换回合归零。"""
    data, st = _engine("pitem_03-3-162-1")
    mem = ExamMemory("r1", turn=3)
    card = _view("オーバードライブ+")
    for _ in range(2):
        E.after_play(data, mem, _state(), card, "全力", "全力", None)
    assert _fired(st) == {}
    E.after_play(data, mem, _state(), card, "全力", "全力", None)
    assert list(_fired(st).values()) == [1] and st["next_stance"] == "温存2段階目"
    mem.turn = 4
    assert E.start_turn(data, mem, _state(), "強気", 1) == ("温存2段階目", 2, True)
    E.after_play(data, mem, _state(), card, "全力", "全力", None)
    assert st["insts"]["pitem_03-3-162-1"]["count"] == 1  # 换回合从 0 数


# ---------------------------------------------------------------- 回合开始 / 结束 / 考试开始 / 每 N 回合


def test_start_turn_remaining_condition_and_count():
    data, st = _engine("pitem_00-0-004-0-003")  # 残り4ターン以内のターン開始時、スキルカード使用数追加+1（4回）
    mem = ExamMemory("r1", turn=1)
    E.start_turn(data, mem, _state(remaining=6), "強気", 1)
    assert _fired(st) == {}
    for turn, rem in ((2, 4), (3, 3), (4, 2), (5, 1), (6, 1)):
        mem.turn = turn
        E.start_turn(data, mem, _state(remaining=rem), "強気", 1)
    assert _fired(st) == {"pitem_00-0-004-0-003": 4}
    mem.turn = 7
    E.start_turn(data, mem, _state(remaining=None), "強気", 1)
    assert _fired(st)["pitem_00-0-004-0-003"] == 4


def test_start_turn_idempotent_within_turn():
    data, st = _engine("pitem_00-0-004-0-003")
    mem = ExamMemory("r1", turn=5)
    st["boundary"] = {"turn": 4}
    E.start_turn(data, mem, _state(remaining=2), "強気", 1)
    E.start_turn(data, mem, _state(remaining=2), "強気", 1)  # read_position 早退后重读：不重复发动
    assert _fired(st) == {"pitem_00-0-004-0-003": 1}


def test_lesson_type_restricted_is_unknown():
    data, st = _engine("pitem_00-3-010-0")  # 【ボーカルレッスン・ボーカルターンのみ】
    mem = ExamMemory("r1", turn=2)
    E.start_turn(data, mem, _state(), "強気", 1)
    assert _fired(st) == {}


def test_end_turn_warm_unless_full_power():
    data, st = _engine("pitem_03-0-007-0-001")  # ターン終了時、非全力状態の場合、温存に変更全力値+3（3回）
    mem = ExamMemory("r1", turn=3)
    assert E.end_turn(data, mem, _state(), 2, "強気", 2) == ("温存", 1)
    assert E.end_turn(data, mem, _state(), 3, "全力", None) == ("全力", None)
    assert E.end_turn(data, mem, _state(), 4, None, None) == (None, None)  # 姿态不明：不知道
    assert _fired(st) == {"pitem_03-0-007-0-001": 1}
    assert E.end_turn(data, mem, _state(), 5, "強気", 1) == ("温存", 1)
    assert E.end_turn(data, mem, _state(), 5, "強気", 1) == ("温存", 1)  # 重读同一回合结束：不重复发动
    assert _fired(st) == {"pitem_03-0-007-0-001": 2}


def test_end_turn_interval_and_start_exam():
    data, st = _engine("pitem_02-3-302-0", "pitem_02-3-299-0")
    mem = ExamMemory("r1", turn=1)
    E.start_turn(data, mem, _state(), None, None)  # 第 1 回合：考试开始
    assert _fired(st) == {"pitem_02-3-299-0": 1}
    for t in (1, 2, 3, 4, 5, 6):
        E.end_turn(data, mem, _state(), t, "強気", 1)
    assert _fired(st)["pitem_02-3-302-0"] == 2  # 3ターンごと：第 3、6 回合结束


def test_start_exam_not_fired_when_installed_mid_exam():
    data, st = _engine("pitem_02-3-299-0")
    mem = ExamMemory("r1", turn=4)
    E.start_turn(data, mem, _state(), "強気", 1)
    assert _fired(st) == {}


def test_stance_change_trigger_condition_unknown_after_play():
    """直接効果で強気になった時、全力値が5以上の場合：出牌后全力値账本还没结算 → 不知道、不发动。"""
    data, st = _engine("pitem_03-3-298-0")
    mem = ExamMemory("r1", turn=2)
    E.after_play(data, mem, _state(), _view("オーバードライブ+"), "温存", "強気", 1)
    assert _fired(st) == {}


def test_condition_fields():
    eng = _eng()
    c = lambda f, v=None, neg=False: P.Condition(neg, "ProduceExamFieldStatusType_" + f, v, None)  # noqa: E731
    ev = eng.Event(eng.START_TURN, stance="温存", level=2, remaining_turns=3,
                   fields={"genki": 5, "hp": 10, "hp_max": 40, "fp": 4})
    cond = lambda f, *a: eng.condition(c(f, *a), ev)  # noqa: E731
    assert cond("RemainingTurn", 4) is True and cond("RemainingTurn", 2) is False
    assert cond("PreservationUp", 2) is True and cond("ConcentrationUp") is False
    assert cond("BlockUp", 31, True) is True and cond("NoBlock") is False
    assert cond("StaminaLessMultiple", 500) is True and cond("StaminaUpMultiple", 500) is False
    assert cond("FullPowerPointUp", 10, True) is True
    assert cond("NoStance", None, True) is True
    unknown = eng.Event(eng.START_TURN)
    for f in ("RemainingTurn", "BlockUp", "NoBlock", "StaminaUpMultiple", "FullPowerPointUp", "NoStance", "PreservationUp",
              "LessonBuffUp", "ReviewUp", "FullPowerPointGetSumUp", "StanceChangeCountUp"):
        assert eng.condition(c(f, 1), unknown) is None, f
    assert eng.condition(c("PreservationUp", 2), eng.Event(eng.START_TURN, stance="のんびり")) is None


def test_played_card_enchant_installed_in_ledger():
    """打出的牌装上的「以降…」附魔进账本（同推演）；一番星除外（veil_star 记）。"""
    from hiro.brain.play.profile import STAR

    data, st = _engine()
    mem = ExamMemory("r1", turn=2)
    E.after_play(data, mem, _state(), _view("頂点へ+"), "強気", "強気", 1)
    assert [c["key"].split("@")[0] for c in st["cards"]] == ["頂点へ+"]
    E.after_play(data, mem, _state(), _view(STAR), "強気", "強気", 1)
    assert len(st["cards"]) == 1


# ---------------------------------------------------------------- 接线：开关关着逐位不变；开着、VEIL 道具不改账本


def _strip(x):
    """去掉引擎状态（含撤回快照里的那份）。"""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k != E.KEY}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _dump(memory):
    return _strip(json.loads(json.dumps(memory.to_dict(), default=str)))


def test_ledger_identical_with_veil_items(monkeypatch):
    """同一回合逐手打完：开关关 vs 开（装上实机 VEIL 的 ゼミパーカー / 応援棒 / 夜空），记忆除引擎状态外逐字段相同。"""
    from hiro.brain.battle import BattleBrain
    from hiro.brain.veil import DRIVE, ENTERTAINER, REBOUND, TEACH, norm
    from hiro.produce.veil_exam import VeilBrain, read_position
    from test_cycle_tap import card, decide, start

    names = ["眠気", TEACH, DRIVE, ENTERTAINER, REBOUND]

    def play(on):
        monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1" if on else "0")
        state, memory = start(names)
        if on:
            E.observe(memory.strategy_data, [{"asset": PARKA}, {"asset": ROD}], VEIL, E.profile_hints())
        dumps = []
        while True:
            _, d = decide(state, memory)
            if d.kind != "play":
                break
            VeilBrain(None, None).commit(state, memory, d)
            dumps.append(_dump(memory))
            rest = [c for c in state.hand if c.slot != d.slot]
            state = BattleState("r1", 1, 8, 40, 44, 0, [card(i, c.name) for i, c in enumerate(rest)], statuses=[],
                                pitem_charges=None, piles={"deck": [], "grave": [], "next_draw": 5},
                                actions_left=memory.actions_left)
            assert read_position(state, memory, Counter(c.name for c in state.hand), lambda: None) is None
            dumps.append(_dump(memory))
        memory.turn += 1
        state.statuses = []
        assert read_position(state, memory, Counter(c.name for c in state.hand), lambda: None) is None
        dumps.append(_dump(memory))
        return dumps

    assert play(False) == play(True)
    assert BattleBrain  # noqa: B018


def test_commit_applies_engine_stance_when_on(monkeypatch):
    """开关开、装了「温存に変更」那版 ゼミパーカー：強気下打 Plan1 牌，出牌记账的姿态变温存；关着照旧。"""
    from hiro.brain.rules import Decision
    from hiro.produce.veil_exam import VeilBrain

    def run(on):
        monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1" if on else "0")
        sense = _view("ひと呼吸+")
        state = BattleState("r1", 2, 8, 30, 40, 0, [sense], statuses=[{"name": "強気", "value": 1}], pitem_charges=3)
        memory = ExamMemory("r1", turn=2)
        memory.strategy_data["veil_deck"] = {"ひと呼吸+": 1}
        memory.strategy_data["veil_position"] = {"turn": 2, "stance": "強気", "stance_level": 1, "flower": 3}
        if on:
            data, _ = _engine(PARKA_WARM)
            memory.strategy_data.update(data)
        VeilBrain(None, None).commit(state, memory, Decision("play:0", "x", "rule"))
        p = memory.strategy_data["veil_pending_play"]
        return p["stance"], p["stance_level"]

    assert run(False) == ("強気", 1)
    assert run(True) == ("温存", 1)


def test_read_position_new_turn_runs_end_and_start_turn(monkeypatch):
    """新回合、画面读不出姿态：开关开时上回合结束的道具附魔（非全力→温存）进账本推演；关着照旧沿用上回合姿态。"""
    from hiro.brain.veil import stance
    from hiro.produce.veil_exam import read_position

    monkeypatch.setenv("HIRO_VEIL_FLOWER_SCREEN", "0")

    def run(on):
        monkeypatch.setenv("VEIL_ENCHANT_RUNTIME", "1" if on else "0")
        state = BattleState("r1", 2, 7, 30, 40, 0, [], statuses=[], pitem_charges=None)
        mem = ExamMemory("r1", turn=2)
        mem.strategy_data["veil_position"] = {"turn": 1, "stance": "強気", "stance_level": 1, "flower": 2, "next_stance": None}
        if on:
            mem.strategy_data.update(_engine("pitem_03-0-007-0-001")[0])
        assert read_position(state, mem, Counter(), lambda: 2) is None
        return stance(state), mem.strategy_data["veil_position"]["stance_level"]

    assert run(False) == ("強気", 1)
    assert run(True) == ("温存", 1)
