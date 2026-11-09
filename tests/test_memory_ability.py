"""H.I.F 回忆能力：主数据分类、每场次数、打出触发后取否，以及 VEIL 记账（再动 / 删眠気）和清牌估值。"""

from types import SimpleNamespace as NS

import pytest

from hiro.brain import memory_ability as M

pytestmark = pytest.mark.synthetic

ABILITIES = [{"card_id": "a", "card": "ラッキー♪", "kind": "draw", "count": 1},
             {"card_id": "b", "card": "達成感", "kind": "sleep", "count": 1},
             {"card_id": "c", "card": "準備運動", "kind": "play", "count": 2}]  # fmt: skip


def test_classify_master_texts():
    assert M.classify("ラッキー♪使用後、スキルカード使用数追加+1・スキルカードを引く") == "draw"
    assert M.classify("準備運動使用後、スキルカード使用数追加<nobr>+1</nobr>") == "play"
    assert M.classify("達成感使用後、ランダムな山札か捨札の眠気を除外に移動") == "sleep"


def test_trigger_counts_down_and_matches_upgraded_name():
    state = M.fresh(ABILITIES)
    assert M.trigger(state, "達成感+")["kind"] == "sleep"
    assert M.trigger(state, "達成感+") is None  # 用完取否
    assert M.trigger(state, "準備運動")["left"] == 1 and M.trigger(state, "準備運動")["left"] == 0
    assert M.trigger(state, "準備運動") is None and M.trigger(state, "オーバードライブ+") is None


def test_pending_does_not_consume():
    state = M.fresh(ABILITIES)
    assert M.pending(state, "ラッキー♪+")["kind"] == "draw" and state[0]["left"] == 1


def _mem():
    return NS(actions_left=1, turn=3)


def test_apply_ability_extra_action_and_sleep_removal():
    pytest.importorskip("kaa")
    from hiro.produce.veil_exam import apply_ability

    data = {"veil_abilities": M.fresh(ABILITIES), "veil_deck": {"眠気": 2},
            "veil_piles": {"deck": {"眠気": 1}, "grave": {}, "unlocated": {}}}  # fmt: skip
    mem = _mem()
    assert apply_ability(data, mem, "ラッキー♪")["kind"] == "draw" and mem.actions_left == 2
    assert apply_ability(data, mem, "ラッキー♪") is None and mem.actions_left == 2  # 一次性
    apply_ability(data, mem, "達成感+")
    assert data["veil_deck"]["眠気"] == 1 and data["veil_piles"]["deck"]["眠気"] == 0


def test_sleep_removal_in_both_piles_marks_unlocated():
    pytest.importorskip("kaa")
    from hiro.produce.veil_exam import apply_ability

    data = {"veil_abilities": M.fresh(ABILITIES), "veil_deck": {"眠気": 2},
            "veil_piles": {"deck": {"眠気": 1}, "grave": {"眠気": 1}, "unlocated": {}}}  # fmt: skip
    apply_ability(data, _mem(), "達成感")
    assert data["veil_deck"]["眠気"] == 1 and data["veil_piles"]["unlocated"]["眠気"] == 1
    assert "眠気" not in data["veil_piles"]["deck"] and "眠気" not in data["veil_piles"]["grave"]


def test_veil_clear_prefers_card_with_pending_extra_action():
    pytest.importorskip("kaa")
    import importlib.util, pathlib

    spec = importlib.util.spec_from_file_location("veil_helpers", pathlib.Path(__file__).with_name("test_veil.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    card, case, choose = helpers.card, helpers.case, helpers.choose

    plain = card(0, "その他+", once=True)
    lucky = card(1, "ラッキー♪", once=True)
    state, mem = case(plain, lucky)
    mem.strategy_data["veil_abilities"] = M.fresh(ABILITIES)
    assert choose(state, mem) == "play:1"
    mem.strategy_data["veil_abilities"][0]["left"] = 0  # 用掉后不再加分
    state.hand = [card(0, "その他+", once=True)]
    assert choose(state, mem) == "play:0"


def test_loader_table_matches_master_texts():
    """表由通用加载器编译（效果类型分类、卡 id 认触发卡、enc02 次数），和说明文字逐项一致；没有主数据时跳过。"""
    M.table.cache_clear()
    try:
        from hiro.state.context import catalog

        rows = catalog().db.execute("SELECT id FROM ProduceExamStatusEnchant WHERE id LIKE ?", (M.PREFIX + "%",)).fetchall()
    except Exception:  # noqa: BLE001
        pytest.skip("没有主数据")
    loaded = M._from_loader([r[0] for r in rows])
    table = M.table()
    assert len(loaded) == len(table) == 115
    for cid, e in table.items():
        assert loaded[cid]["kind"] is not None, cid
        assert loaded[cid]["kind"] == M.classify(e["text"]), cid
        assert M.norm(loaded[cid]["card"]) == M.norm(e["text"].split("使用後")[0]), cid
        assert e["count"] == loaded[cid]["count"] and e["count"] >= 1, cid
    assert {e["kind"] for e in table.values()} == {"draw", "play", "sleep"}
