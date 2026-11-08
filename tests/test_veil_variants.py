"""HIRO_VEIL_SIGNATURE=1：同名卡不同版本（一次性 / 客制化）分开记账，手里分不出时点按一次读效果文字。合成数据。"""

from collections import Counter
from types import SimpleNamespace

import pytest

from hiro.brain.cards import CardView, parse_tags
from hiro.brain.rules import Decision
from hiro.brain.state import BattleState, ExamMemory
from hiro.brain.veil import CORE, DRIVE, ENTERTAINER, REBOUND, TEACH
from hiro.produce import veil_exam as V

pytestmark = pytest.mark.synthetic

ONCE_KEY, CORE_KEY = f"{REBOUND}〔一次性〕", f"{REBOUND}〔常驻·制限削除〕"
INSTANCES = [{"name": ENTERTAINER, "once": False, "customs": None}, {"name": DRIVE, "once": False, "customs": None},
             {"name": TEACH, "once": False, "customs": None},
             {"name": REBOUND, "once": False, "customs": ["制限削除"]}, {"name": REBOUND, "once": True, "customs": []}]


def deck_json():
    return {"cards": [i["name"] for i in INSTANCES], "once": [], "rebound_repeatable": True, "custom": [],
            "instances": INSTANCES, "evidence": "合成"}


def card(slot, name, text=""):
    return CardView(slot, name, name, 1, True, "mental", 0, 0, None, text, parse_tags(name, text, once="レッスン中1回" in text))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("HIRO_VEIL_SIGNATURE", "1")


def test_keys_only_split_conflicting_names(on):
    keys = V.instance_keys(INSTANCES)
    assert set(keys) == {ENTERTAINER, DRIVE, TEACH, ONCE_KEY, CORE_KEY}
    assert keys[ONCE_KEY]["once"] and not keys[CORE_KEY]["once"] and V.name_of(CORE_KEY) == REBOUND
    assert V.read_variants(deck_json()) == keys
    same = [{"name": "x", "once": True, "customs": []}, {"name": "x", "once": True, "customs": None}]
    assert V.instance_keys(same) == {"x": {"name": "x", "once": True, "customs": [], "count": 2}}


def test_read_variants_off_or_without_conflict(monkeypatch):
    assert V.read_variants(deck_json()) is None  # 开关关着
    monkeypatch.setenv("HIRO_VEIL_SIGNATURE", "1")
    assert V.read_variants({**deck_json(), "instances": INSTANCES[:4]}) is None  # 没有同名不同版本：照旧按卡名


def test_validate_deck_allows_extra_once_rebound_only_with_variants(on, monkeypatch):
    V.validate_deck(deck_json())
    two_core = [*INSTANCES[:4], {"name": REBOUND, "once": False, "customs": []}]
    with pytest.raises(ValueError):
        V.validate_deck({**deck_json(), "instances": two_core})
    monkeypatch.delenv("HIRO_VEIL_SIGNATURE")
    with pytest.raises(ValueError):
        V.validate_deck(deck_json())


def test_match_variant_by_once_text(on):
    keys = V.instance_keys(INSTANCES)
    assert V.match_variant("元気+5 レッスン中1回", [ONCE_KEY, CORE_KEY], keys) == ONCE_KEY
    assert V.match_variant("元気+5", [ONCE_KEY, CORE_KEY], keys) == CORE_KEY
    assert V.match_variant("", [ONCE_KEY, CORE_KEY], keys) is None


def _screen(probe_texts):
    keys = V.instance_keys(INSTANCES)
    seen = []

    def probe(frame, c):
        seen.append(c.slot)
        return probe_texts.get(c.slot)

    src = V.VeilScreen({k: v["count"] for k, v in keys.items()}, {k for k, v in keys.items() if v["once"]},
                       variants=keys, probe=probe)
    return src, seen


def _state(*cards):
    return BattleState("r1", 1, 8, 40, 44, 0, list(cards), statuses=[{"name": "温存"}], pitem_charges=2)


def test_assign_keys_probes_once_then_uses_cache(on):
    src, seen = _screen({1: "元気 レッスン中1回", 3: "元気"})
    state = _state(card(0, TEACH), card(1, REBOUND), card(2, DRIVE), card(3, REBOUND))
    mem = ExamMemory("r1", turn=1)
    mem.strategy_data["veil_deck"] = {k: v["count"] for k, v in V.instance_keys(INSTANCES).items()}
    assert src.assign_keys(state, mem, SimpleNamespace()) is None  # 点按了：这一帧作废
    assert seen == [1, 3]
    keys = src.assign_keys(state, mem, SimpleNamespace())
    assert keys == {0: TEACH, 1: ONCE_KEY, 2: DRIVE, 3: CORE_KEY} and seen == [1, 3]  # 第二帧用缓存，不再点
    src.apply_variants(state, keys)
    assert state.hand[1].tags.once and not state.hand[3].tags.once


def test_assign_keys_skips_probe_when_only_one_version_left(on):
    src, seen = _screen({})
    state = _state(card(0, REBOUND))
    mem = ExamMemory("r1", turn=1)
    mem.strategy_data["veil_deck"] = {CORE_KEY: 1, ONCE_KEY: 0}  # 一次性那张已打出除外
    assert src.assign_keys(state, mem, SimpleNamespace()) == {0: CORE_KEY} and seen == []


def test_assign_keys_gives_up_and_marks_uncertain(on):
    src, seen = _screen({})  # 点按读不到字
    state = _state(card(0, REBOUND))
    mem = ExamMemory("r1", turn=1)
    mem.strategy_data["veil_deck"] = {CORE_KEY: 1, ONCE_KEY: 1}
    for _ in range(V.PROBE_RETRIES):
        assert src.assign_keys(state, mem, SimpleNamespace()) is None
    assert src.assign_keys(state, mem, SimpleNamespace()) == {0: CORE_KEY}  # 按第一个可能的版本记
    assert mem.strategy_data["veil_uncertain"] and "分不出" in mem.strategy_data["veil_uncertain_why"][-1]["why"]


def test_commit_removes_only_the_once_version(on):
    keys = V.instance_keys(INSTANCES)
    state = _state(card(0, REBOUND, "レッスン中1回"), card(1, REBOUND))
    mem = ExamMemory("r1", turn=1)
    data = mem.strategy_data
    data["veil_deck"] = {k: v["count"] for k, v in keys.items()}
    data["veil_initial_once"] = [ONCE_KEY]
    data["veil_slot_keys"] = {"0": ONCE_KEY, "1": CORE_KEY}
    data["veil_hand_keys"] = {"turn": 1, "by_name": {REBOUND: {"0": ONCE_KEY, "1": CORE_KEY}}}
    data["veil_piles"] = {"turn": 1, "hand": [ONCE_KEY, CORE_KEY, None, None, None], "deck": {}, "grave": {}, "unlocated": {}}
    V.VeilBrain(None, None).commit(state, mem, Decision("play:0", "清牌", "rule"))
    assert data["veil_deck"][ONCE_KEY] == 0 and data["veil_deck"][CORE_KEY] == 1
    assert data["veil_pending_play"]["card"] == ONCE_KEY and data["veil_pending_play"]["count"] == 1
    assert data["veil_hand_keys"]["by_name"][REBOUND] == {"1": CORE_KEY}
    V.VeilBrain(None, None).commit(state, mem, Decision("play:1", "循环", "rule"))
    assert data["veil_deck"][CORE_KEY] == 1 and data["veil_piles"]["grave"] == {CORE_KEY: 1}
