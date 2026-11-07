"""第二十九局：考试里喝饮料只改 exam.drinks，produce.drinks 一直停在开局 4 瓶（seq 115 起 pending 旧值），轮间按它决策。
离开考试（进本战排名 / 轮间 / 结算）时用考试最后读到的饮料栏覆盖 produce.drinks。"""

from hiro.contracts import GameState
from hiro.loop.reduce import reduce
from hiro.vision.observe import observation

FOUR = ["pdrink_00-2-008", "pdrink_00-2-010", "pdrink_00-3-001", "pdrink_02-3-010"]


def _obs(seq, page, fields, source="screen"):
    return observation(seq, float(seq), page, fields, area="produce", source=source, ready=True)


def _run(exam_drinks, after_page="HIF_FINALS_RANKING"):
    s = GameState(mode="produce", produce={"drinks": list(FOUR)})
    s = reduce(s, _obs(1, "ACTION_SELECT", {}))
    s = reduce(s, _obs(2, "HIF_EXAM", {"drinks": exam_drinks}, source="save"))
    for seq in (3, 4, 5):  # 不在转移图里的跳转要连续几帧才接受
        s = reduce(s, _obs(seq, after_page, {}))
    return s


def test_leaving_exam_syncs_drinks():
    s = _run([{"slot": 0, "name": "ブーストエキス", "drink_id": "pdrink_00-2-008"}])
    assert s.produce["drinks"] == ["pdrink_00-2-008"] and s.field_status["produce.drinks"] == "observed"
    assert s.sources["produce.drinks"].origin == "save"


def test_all_drunk_becomes_empty():
    s = _run([], after_page="HIF_ROUND_INTERVAL")
    assert s.produce["drinks"] == []


def test_unrecognized_exam_drink_keeps_old():
    s = _run([{"slot": 0, "name": None, "drink_id": None}])
    assert s.produce["drinks"] == FOUR


def test_inside_exam_no_sync():
    s = GameState(mode="produce", produce={"drinks": list(FOUR)})
    s = reduce(s, _obs(1, "ACTION_SELECT", {}))
    s = reduce(s, _obs(2, "HIF_EXAM", {"drinks": []}, source="save"))
    assert s.produce["drinks"] == FOUR  # 还在考试里：不动


def _run_screen(exam_fields, after_page="HIF_ROUND_INTERVAL"):
    s = GameState(mode="produce", produce={"drinks": list(FOUR)})
    s = reduce(s, _obs(1, "ACTION_SELECT", {}))
    s = reduce(s, _obs(2, "HIF_EXAM", exam_fields))
    for seq in (3, 4, 5):
        s = reduce(s, _obs(seq, after_page, {}))
    return s


def _slot(i, state, drink_id=None):
    return {"slot": i, "state": state, "drink_id": drink_id}


def test_screen_unknown_slot_keeps_old():
    """画面识别：认不出的格在 exam.drinks 里被略掉，和空格分不开；drink_slots 有 unknown 时不能当成喝掉了。"""
    drinks = [{"slot": 0, "name": "ブーストエキス", "drink_id": FOUR[0]}]
    slots = [_slot(0, "drink", FOUR[0]), _slot(1, "unknown"), _slot(2, "empty"), _slot(3, "empty")]
    s = _run_screen({"drinks": drinks, "drink_slots": slots})
    assert s.produce["drinks"] == FOUR


def test_screen_all_sure_syncs_in_slot_order():
    drinks = [{"slot": 2, "name": None, "drink_id": FOUR[3]}, {"slot": 0, "name": None, "drink_id": FOUR[1]}]
    slots = [_slot(0, "drink", FOUR[1]), _slot(1, "empty"), _slot(2, "drink", FOUR[3]), _slot(3, "empty")]
    s = _run_screen({"drinks": drinks, "drink_slots": slots})
    assert s.produce["drinks"] == [FOUR[1], FOUR[3]]  # 按格号从左到右，不按 id 排序
