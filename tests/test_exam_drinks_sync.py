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
