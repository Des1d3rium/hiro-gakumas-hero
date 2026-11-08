"""tools/batch_ledger：回忆栏 → 本局账本，变卡 / 拿卡 / 强化 / 饮料栏 1–4 的记账规则。纯函数。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_ledger as L  # noqa: E402

pytestmark = pytest.mark.synthetic  # 只用合成数据，不读截图

MEMORY = {"memory": "veil-a", "cards": ["巻き返し+", "眠気", "タフネス+", "タフネス+"], "once": ["タフネス+"],
          "rebound_repeatable": True, "evidence": "deck.png"}  # fmt: skip


@pytest.fixture
def ledger():
    return L.new_ledger(MEMORY, ["ブーストエキス", "センブリソーダ"])


def test_new_ledger_pads_slots_and_rejects_gaps(ledger):
    assert ledger["drinks"] == ["ブーストエキス", "センブリソーダ", None, None]
    with pytest.raises(ValueError):
        L.new_ledger(MEMORY, [None, "ブーストエキス"])


def test_change_card_moves_once_flag(ledger):
    L.change_card(ledger, "タフネス+", "忍耐力", True, why="授業")  # 换入继承换出那张的强化：忍耐力+
    assert ledger["cards"].count("タフネス+") == 1 and "タフネス+" in ledger["once"]  # 还剩一张，仍是一次性
    L.remove_card(ledger, "タフネス+")
    assert "タフネス+" not in ledger["once"] and "忍耐力+" in ledger["once"]
    L.change_card(ledger, "忍耐力+", "アンダンテ", True, inherit=False)
    assert "アンダンテ" in ledger["cards"]


def test_record_change_inherits_upgrade_of_removed_card():
    # 实机（codex-20261107-1 第 1 局）：换出「立ち回りの基本+」后，阶段 1 读到的「オープニングアクト」进牌组是「オープニングアクト+」
    led = L.new_ledger({"memory": "m", "cards": ["立ち回りの基本+", "巻き返し+"], "once": [], "evidence": "x"})
    todo = L.record_change(led, {"name": "立ち回りの基本", "upgrade": 1}, {"name": "オープニングアクト", "upgrade": 0, "once": True})
    assert todo is None
    assert "オープニングアクト+" in led["cards"] and "オープニングアクト+" in led["once"]
    assert "立ち回りの基本+" not in led["cards"] and led["pending_add"] is None


def test_record_change_matches_removed_card_without_plus():
    # 账本少记了一个「+」（旧版记账），扫描读到 upgrade=1：按去掉「+」的名字仍能删掉那张
    led = L.new_ledger({"memory": "m", "cards": ["オープニングアクト", "巻き返し+"], "once": ["オープニングアクト"], "evidence": "x"})
    led["pending_add"] = {"name": "アンダンテ", "upgrade": 0, "once": True}
    assert L.record_change(led, {"name": "オープニングアクト", "upgrade": 1}, led["pending_add"]) is None
    assert led["cards"] == ["巻き返し+", "アンダンテ+"] and led["once"] == ["アンダンテ+"]
    assert led["log"][-2]["read"] == "オープニングアクト+"


def test_record_change_failure_keeps_ledger_and_returns_todo():
    led = L.new_ledger({"memory": "m", "cards": ["タフネス", "タフネス+"], "once": [], "evidence": "x"})
    before = {k: list(led[k]) for k in ("cards", "once", "log")}
    pending = {"name": "アンダンテ", "upgrade": 0, "once": True}
    todo = L.record_change(led, {"name": "タフネス", "upgrade": 2}, pending)  # X 和 X+ 都有：不猜
    assert todo and "ledger-edit --change" in todo and "アンダンテ++" in todo
    assert {k: led[k] for k in before} == before and led["pending_add"] is None  # pending 由调用方保留
    assert L.record_change(led, {"name": None}, pending).startswith("换出或换入的卡没认出")


def test_change_card_is_atomic(ledger):
    with pytest.raises(ValueError):
        L.change_card(ledger, "巻き返し+", "タフネス+", None)  # 换入没记一次性：不能只删不加
    assert ledger["cards"] == MEMORY["cards"] and len(ledger["deck"]) == len(MEMORY["cards"])


def test_remove_card_is_strict_unless_loose(ledger):
    with pytest.raises(ValueError):
        L.remove_card(ledger, "巻き返し")
    L.remove_card(ledger, "巻き返し", loose=True)
    assert "巻き返し+" not in ledger["cards"]


def test_add_requires_once_and_consistency(ledger):
    with pytest.raises(ValueError):
        L.add_card(ledger, "忍耐力", None)
    L.add_card(ledger, "タフネス+", False)  # 同名卡一次性状态不一致：逐张记，照收
    assert "タフネス+" not in ledger["once"] and "タフネス+" in L.summary(ledger)["conflicts"]


def test_upgrade_keeps_once(ledger):
    L.add_card(ledger, "忍耐力", True)
    L.upgrade_card(ledger, "忍耐力")
    assert "忍耐力+" in ledger["cards"] and "忍耐力+" in ledger["once"] and "忍耐力" not in ledger["cards"]


def test_drinks_fill_left_and_shift_on_remove(ledger):
    assert L.drink_add(ledger, "初星ホエイプロテイン") == 2
    assert L.drink_remove(ledger, 0) == "ブーストエキス"
    assert ledger["drinks"] == ["センブリソーダ", "初星ホエイプロテイン", None, None]


def test_drink_add_full_refuses():
    ledger = L.new_ledger(MEMORY, ["a", "b", "c", "d"])
    with pytest.raises(ValueError):
        L.drink_add(ledger, "e")


def test_after_exam_drops_consumed_and_compacts(ledger):
    exam = [{"state": "consumed", "name": "ブーストエキス"}, {"state": "drink", "name": "センブリソーダ"},
            {"state": "empty"}, None]  # fmt: skip
    L.after_exam(ledger, exam, why="R1")
    assert ledger["drinks"] == ["センブリソーダ", None, None, None]


def test_compare_drinks_skips_unknown(ledger):
    observed = [{"slot": 0, "state": "unknown"}, {"slot": 1, "state": "drink", "name": "ブーストエキス"},
                {"slot": 2, "state": "empty"}, {"slot": 3, "state": "empty"}]  # fmt: skip
    assert L.compare_drinks(ledger, observed) == ["第 2 格：账本 センブリソーダ，画面 ブーストエキス"]


def test_to_deck_and_card_name(ledger):
    assert L.card_name("タフネス", 1) == "タフネス+" and L.card_name("忍耐力", 0) == "忍耐力"
    deck = L.to_deck(ledger)
    assert deck["cards"] == ledger["cards"] and deck["rebound_repeatable"] is True and deck["evidence"]


def test_memory_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "MEMORY_DIR", tmp_path)
    L.save_memory("veil-a", MEMORY, "i_test")
    assert L.load_memory("veil-a")["cards"] == MEMORY["cards"]
    with pytest.raises(ValueError):
        L.save_memory("veil-a", {**MEMORY, "cards": ["眠気"]}, "i_test")  # 不同牌组不覆盖
    with pytest.raises(ValueError):
        L.memory_path("../x")


def test_sync_drinks_overwrites_readable_slots(monkeypatch):
    """饮料懒同步：画面读清的格覆盖账本，读不清的格留账本；一致时不写日志。"""
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    led = L.new_ledger({"memory": "m", "cards": [], "evidence": "t"}, ["初星湯", None, None, None])
    obs = [{"slot": 0, "state": "drink", "name": "初星湯"}, {"slot": 1, "state": "drink", "name": "初星ホエイプロテイン"},
           {"slot": 2, "state": "unknown"}, {"slot": 3, "state": "empty"}]
    diff = L.sync_drinks(led, obs, why="shop-auto")
    assert diff and led["drinks"] == ["初星湯", "初星ホエイプロテイン", L.UNKNOWN_DRINK, None]  # 认不出 = 有饮料
    n = len(led["log"])
    assert L.sync_drinks(led, obs, why="shop-auto") == [] and len(led["log"]) == n


def test_sync_drinks_unread_slot_takes_unmatched_ledger_drink(monkeypatch):
    """栏位挤动后读不清的格：补账本里没在画面出现的那瓶，而不是同格号的旧值（验证局 1-c 商店前）。"""
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    led = L.new_ledger({"memory": "m", "cards": [], "evidence": "t"},
                       ["ジンジャーエール", "初星ホエイプロテイン", "ブーストエキス", None])
    obs = [{"slot": 0, "state": "drink", "name": "初星湯"}, {"slot": 1, "state": "drink", "name": "ジンジャーエール"},
           {"slot": 2, "state": "unknown"}, {"slot": 3, "state": "drink", "name": "ブーストエキス"}]
    L.sync_drinks(led, obs, why="shop-auto")
    assert led["drinks"] == ["初星湯", "ジンジャーエール", "初星ホエイプロテイン", "ブーストエキス"]


def test_record_change_inherits_booked_upgrade_when_scan_misses_plus():
    """阶段 2 把「立ち回りの基本+」读成没强化：换入的仍按账本里的「+」记（使用者 2026-11-07）。"""
    led = L.new_ledger({"memory": "m", "cards": ["立ち回りの基本+", "巻き返し+"], "once": [], "evidence": "x"})
    assert L.record_change(led, {"name": "立ち回りの基本", "upgrade": 0}, {"name": "涙の思い出", "upgrade": 0, "once": True}) is None
    assert "涙の思い出+" in led["cards"] and "立ち回りの基本+" not in led["cards"]


def test_sync_drinks_unknown_slot_over_empty_ledger_is_occupied(monkeypatch):
    """验证局 1-d：账本漏记两瓶ホエイ（画面认不出），以为有空格去买失败 → 认不出的格记占位，算有饮料。"""
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    led = L.new_ledger({"memory": "m", "cards": [], "evidence": "t"}, ["ジンジャーエール", "ブーストエキス", None, None])
    obs = [{"slot": 0, "state": "unknown"}, {"slot": 1, "state": "drink", "name": "ジンジャーエール"},
           {"slot": 2, "state": "drink", "name": "ブーストエキス"}, {"slot": 3, "state": "unknown"}]
    assert L.sync_drinks(led, obs, why="shop-auto")
    assert led["drinks"] == [L.UNKNOWN_DRINK, "ジンジャーエール", "ブーストエキス", L.UNKNOWN_DRINK] and None not in led["drinks"]


def test_to_deck_carries_customize_log():
    led = L.new_ledger({"memory": "m", "cards": ["インフルエンサー+"], "once": [], "evidence": "x"})
    L._log(led, "customize", card="インフルエンサー+", item="強気追加")
    assert L.to_deck(led)["custom"] == [{"card": "インフルエンサー+", "item": "強気追加"}]


def test_acquire_card_adds_new_and_flags_same_name(monkeypatch):
    """第一层：获得演出看到的卡——账本没有同名的记一张；已有同名（可能是强化）只记 card? 留给考前对账。"""
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    led = L.new_ledger({"memory": "m", "cards": ["巻き返し+"], "once": [], "evidence": "x"})
    assert L.acquire_card(led, "ティーパーティ+", True, why="事件") == "added" and "ティーパーティ+" in led["once"]
    assert L.acquire_card(led, "ティーパーティ+", True, why="事件") == "dup"
    assert L.acquire_card(led, "巻き返し++", False, why="事件") == "ambiguous" and led["cards"].count("巻き返し+") == 1


def test_sync_deck_adds_missing_fixes_upgrade_keeps_extra(monkeypatch):
    """第二层：考前按牌组一览对账——漏记的补、强化差「+」的改、账本多出的只记不删。"""
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    led = L.new_ledger({"memory": "m", "cards": ["巻き返し+", "モチベ", "眠気"], "once": ["モチベ"], "evidence": "x"})
    notes = L.sync_deck(led, [("巻き返し+", False), ("モチベ+", True), ("ティーパーティ+", True)], why="R1 前")
    assert sorted(led["cards"]) == sorted(["巻き返し+", "眠気", "モチベ+", "ティーパーティ+"])
    assert "モチベ+" in led["once"] and "ティーパーティ+" in led["once"] and "モチベ" not in led["once"]
    assert any("强化 モチベ→モチベ+" in n for n in notes) and any("账本多出 眠気" in n for n in notes)


def test_mark_full_fills_empty_slots_with_placeholder(ledger):
    """领取页说栏满、账本有空格：空格记占位（1-e 公開レッスン 白送的 2 瓶漏记）。"""
    assert L.mark_full(ledger, why="P道具饮料页") == 2
    assert ledger["drinks"] == ["ブーストエキス", "センブリソーダ", L.UNKNOWN_DRINK, L.UNKNOWN_DRINK]
    assert L.mark_full(ledger, why="再来一次") == 0


@pytest.mark.parametrize("with_ledger, explicit, retry", [
    (True, True, None), (True, False, None), (False, True, None), (True, True, 1),
])
def test_exam_sync_with_optional_explicit_deck(monkeypatch, tmp_path, with_ledger, explicit, retry):
    import json
    from types import SimpleNamespace

    import batch_hif as batch
    from hiro.produce import veil_exam

    monkeypatch.setattr(L, "MEMORY_DIR", tmp_path / "memories")
    ledger = None
    if with_ledger:
        L.save_memory(MEMORY["memory"], MEMORY, batch.VEIL_IDOL)
        ledger = L.new_ledger(MEMORY, ["ブーストエキス", "センブリソーダ"])
    active = {"run": 1, "phase": "playing", "ledger": ledger}
    if retry:
        active["exam_start_drinks"] = {"1": list(ledger["drinks"])}
        L.after_exam(ledger, [], why="上一次考试喝光（合成）")
    state = {"active": active, "preflight": {"idol": batch.VEIL_IDOL}}
    deck = "synthetic.json" if explicit else None
    args = SimpleNamespace(command="exam", batch="synthetic", run=1, round=1, deck=deck)
    ability = {"card_id": "synthetic", "card": "synthetic", "kind": "draw", "count": 1}
    saved = tmp_path / "state.json"

    def run_round(round_no, selected, trace, **kwargs):
        assert selected == (deck if explicit else L.to_deck(ledger))
        if retry:
            assert kwargs["retry_drinks"] == ["ブーストエキス", "センブリソーダ", None, None]
        return {"ok": True, "retry": retry, "drinks": [{"state": "drink", "name": "センブリソーダ"}],
                "abilities_source": "screen", "abilities": [ability]}

    monkeypatch.setattr(veil_exam, "run_round", run_round)
    monkeypatch.setattr(batch, "exam_drink_sync", lambda ledger, round_no: [])  # 画面与账本一致
    out = batch.change(state, args, persist=lambda: saved.write_text(json.dumps(state)))
    assert out["ok"]
    if with_ledger:
        persisted = json.loads(saved.read_text())["active"]["ledger"]
        assert persisted["drinks"] == ["センブリソーダ", None, None, None]
        assert persisted["abilities"] == L.load_memory(MEMORY["memory"])["abilities"] == [ability]
        assert any("再挑戦" in (log.get("why") or "") for log in persisted["log"]) == bool(retry)
    else:
        assert "ledger_drinks" not in out and not saved.exists()


def _exam_state(tmp_path, monkeypatch, drinks):
    from types import SimpleNamespace

    import batch_hif as batch

    monkeypatch.setattr(L, "MEMORY_DIR", tmp_path / "memories")
    L.save_memory(MEMORY["memory"], MEMORY, batch.VEIL_IDOL)
    ledger = L.new_ledger(MEMORY, drinks)
    state = {"active": {"run": 1, "phase": "playing", "ledger": ledger}, "preflight": {"idol": batch.VEIL_IDOL}}
    return state, SimpleNamespace(command="exam", batch="synthetic", run=1, round=1, deck=None)


def _slots(*names):
    return [{"slot": i, "state": "drink" if n else "empty", "name": n} for i, n in enumerate(names)]


def test_exam_reads_drink_bar_before_first_attempt(monkeypatch, tmp_path):
    """验证局 1-i：公開レッスン 白送的ブーストエキス 没记上。每轮第一次 exam 先按画面改账本，开考快照和 run_round 都用改后的。"""
    import batch_hif as batch
    from hiro.produce import veil_exam

    state, args = _exam_state(tmp_path, monkeypatch, ["ブーストエキス", "初星ホエイプロテイン"])
    screen = _slots("ブーストエキス", "ブーストエキス", "初星ホエイプロテイン", None)
    reads, seen, real = [], {}, batch.exam_drink_sync

    def sync(ledger, round_no):
        reads.append(round_no)
        return real(ledger, round_no, read=lambda: screen)

    def run_round(round_no, selected, trace, **kw):
        seen["drinks"] = list(kw["drinks"])
        return {"ok": True, "retry": None, "drinks": None}

    monkeypatch.setattr(batch, "exam_drink_sync", sync)
    monkeypatch.setattr(veil_exam, "run_round", run_round)
    batch.change(state, args, persist=lambda: None)
    want = ["ブーストエキス", "ブーストエキス", "初星ホエイプロテイン", None]
    assert seen["drinks"] == want == state["active"]["exam_start_drinks"]["1"]
    assert "R1 开考前读饮料栏" in state["active"]["ledger"]["log"][-1]["why"]
    batch.change(state, args, persist=lambda: None)  # 同一轮续跑：不再读（中途喝掉的由考试记忆管）
    assert reads == [1]


def test_exam_drink_sync_keeps_ledger_for_unreadable_slot():
    led = {"drinks": ["ブーストエキス", "初星ホエイプロテイン", None, None], "log": []}
    screen = _slots("ブーストエキス", None, None, None)
    screen[1]["state"] = "unknown"
    import batch_hif as batch

    batch.exam_drink_sync(led, 2, read=lambda: screen)
    assert led["drinks"] == ["ブーストエキス", "初星ホエイプロテイン", None, None]


def test_complete_keeps_ledger_in_record(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import batch_hif as batch

    state, _ = _exam_state(tmp_path, monkeypatch, ["ブーストエキス"])
    state["total"], state["completed"] = 1, []
    args = SimpleNamespace(command="complete", batch="synthetic", run=1, result="R1 1 第1")
    batch.change(state, args, persist=lambda: None)
    rec = state["completed"][-1]
    assert rec["ledger"]["drinks"][0] == "ブーストエキス" and rec["ledger"]["log"]


# ------------------------------------------------------------------ 逐张记账（观察流：同名「巻き返し+」一张一次性、一张常驻）
def _rebound_pair():
    led = L.new_ledger({"memory": "m", "cards": ["巻き返し+", "立ち回りの基本+"], "once": [], "rebound_repeatable": True,
                        "evidence": "x"})
    L.change_card(led, "立ち回りの基本+", "巻き返し", True, why="授業换卡")  # 换入继承「+」，一次性
    return led


def test_same_name_cards_keep_their_own_once_flag():
    led = _rebound_pair()
    cards = [c for c in led["deck"] if c["name"] == "巻き返し+"]
    assert sorted(c["once"] for c in cards) == [False, True]
    assert led["rebound_repeatable"] is True and "巻き返し+" not in led["once"]  # 旧视图：混着时不列一次性
    assert "巻き返し+" in L.summary(led)["conflicts"]
    deck = L.to_deck(led)
    assert sorted(i["once"] for i in deck["instances"] if i["name"] == "巻き返し+") == [False, True]


def test_ambiguous_same_name_needs_id_or_where():
    led = _rebound_pair()
    with pytest.raises(L.AmbiguousCard) as e:
        L.remove_card(led, "巻き返し+")
    assert "--id" in str(e.value)
    gone = L.remove_card(led, "巻き返し+", where={"once": True})
    assert gone["origin"]["kind"] == "change" and led["cards"].count("巻き返し+") == 1 and led["rebound_repeatable"] is True


def test_record_change_uses_panel_once_to_pick_copy():
    led = _rebound_pair()
    led["pending_add"] = {"name": "形勢逆転", "upgrade": 0, "once": True}
    out = {"name": "巻き返し", "upgrade": 1, "customized": False, "once": True}
    assert L.record_change(led, out, led["pending_add"]) is None
    left = [c for c in led["deck"] if c["name"] == "巻き返し+"]
    assert len(left) == 1 and left[0]["once"] is False and "形勢逆転+" in led["cards"]
    new = next(c for c in led["deck"] if c["name"] == "形勢逆転+")
    assert new["origin"] == {"kind": "change", "from": "巻き返し+", "from_id": next(
        e["id"] for e in led["log"] if e["op"] == "remove" and e["card"] == "巻き返し+")}


def test_record_change_ambiguous_returns_todo_with_id_hint():
    led = _rebound_pair()
    todo = L.record_change(led, {"name": "巻き返し", "upgrade": 1}, {"name": "形勢逆転", "upgrade": 0, "once": True})
    assert todo and "--id" in todo and led["cards"].count("巻き返し+") == 2


def test_customize_limit_removal_makes_that_copy_repeatable():
    led = L.new_ledger({"memory": "m", "cards": ["インフルエンサー+"], "once": [], "evidence": "x"})
    L.add_card(led, "巻き返し+", True)
    card = L.customize_card(led, "巻き返し+", "制限削除")
    assert card["once"] is False and card["customs"] == ["制限削除"] and led["rebound_repeatable"] is True
    assert L.to_deck(led)["custom"] == [{"card": "巻き返し+", "item": "制限削除"}]


def test_legacy_ledger_migrates_and_keeps_customize_log():
    led = {"memory": "m", "cards": ["インフルエンサー+", "眠気"], "once": [], "rebound_repeatable": None, "drinks": [None] * 4,
           "log": [{"t": "1", "op": "customize", "card": "インフルエンサー+", "item": "強気追加"}]}
    deck = L.ensure_deck(led)
    assert [c["id"] for c in deck] == ["c01", "c02"] and deck[0]["customs"] == ["強気追加"] and deck[1]["customs"] is None
    led["cards"].append("その他+")  # 有人直接改旧视图：下次取时补齐，原有的卡对象不丢
    deck = L.ensure_deck(led)
    assert [c["id"] for c in deck] == ["c01", "c02", "c03"] and deck[0]["customs"] == ["強気追加"]


def test_upgrade_keeps_identity():
    led = _rebound_pair()
    card = L.upgrade_card(led, "巻き返し+", where={"once": False})
    assert card["name"] == "巻き返し++" and card["once"] is False and card["origin"]["kind"] == "memory"


def _edit_args(**kw):
    from types import SimpleNamespace

    base = dict(add=None, once=None, remove=None, change=None, upgrade=None, customize=None, set_once=None,
                rebound_repeatable=None, drink_remove=None, drink_add=None, drink_set=None, id=None, where=None,
                no_inherit=False, origin=None, why="test")
    return SimpleNamespace(**{**base, **kw})


def test_ledger_edit_reports_diff_and_fails_on_ambiguity():
    import batch_hif as batch

    led = _rebound_pair()
    saved = []
    out = batch.ledger_edit(L, led, _edit_args(change=["巻き返し+", "形勢逆転"], once=True, where="once=1"), lambda: saved.append(1))
    assert out["ok"] and saved and out["diff"]["cards"]["removed"][0].startswith("c03 巻き返し+（一次性")
    assert out["diff"]["cards"]["added"][0].startswith("c04 形勢逆転+")
    with pytest.raises(L.AmbiguousCard):
        batch.ledger_edit(L, _rebound_pair(), _edit_args(remove="巻き返し+"), lambda: None)


def test_ledger_edit_noop_is_not_ok():
    import batch_hif as batch

    led = L.new_ledger(MEMORY, ["ブーストエキス"])
    out = batch.ledger_edit(L, led, _edit_args(), lambda: None)
    assert out["ok"] is False
    out = batch.ledger_edit(L, led, _edit_args(drink_set="ブーストエキス"), lambda: None)
    assert out["ok"] and out["diff"] == "账本没变（要的状态本来就是这样）"
