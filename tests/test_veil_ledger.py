"""VEIL 考试记账：全力値预留（VEIL_FP_RESERVE）、全力値账本重建 / exam 外出牌核对 / 増加量増加、
牌库不确定（veil_uncertain）的原因与清除、决策 trace 字段与 exam_brain_end 退出原因。"""

import json
from dataclasses import replace

from collections import Counter

import pytest

from hiro.brain.cards import CardView, DrinkView, parse_tags
from hiro.brain.state import BattleState, ExamMemory
from hiro.brain.veil import DRIVE, REBOUND, VeilAdvisor
from hiro.contracts import Action

ANDANTE = "アンダンテ+"
IDOL_P = "アイドルになります+"
TEXT = {
    DRIVE: "強気に変更パラメータ+4（2回）全力値+2スキルカード使用数追加+1眠気を山札のランダムな位置に生成次のターン、温存に変更",
    REBOUND: "全力値消費1温存に変更熱意追加+6すべてのスキルカードのコスト値減少-1レッスン中1回",
    ANDANTE: "全力値消費2強気に変更パラメータ+18熱意増加+10%レッスン中1回",
    IDOL_P: "全力値消費3元気+4温存に変更スキルカード使用数追加+1手札のパラメータ上昇回数増加+1重複不可 レッスン中1回",
    "トレーニングの成果+": "全力値+4パラメータ+28（累積全力値の200%分、パラメータ上昇量増加）以降1回まで、全力を解除後、非全力状態の場合、温存2段階目に変更レッスン中1回",
    "リスキーチャンス+": "全力値+3全力値増加量増加+50%（4ターン）スキルカード使用数追加+1スキルカードを2枚引く眠気を山札のランダムな位置に生成重複不可 レッスン中1回",
    "立ち回りの基本": "温存に変更元気+5レッスン中1回",
    "その他+": "パラメータ+10レッスン中1回",
    "待ち望んだ瞬間": "温存2段階目に変更以降、直接効果で全力値が増加後、温存の場合、全力値+1重複不可 レッスン中1回",
}



@pytest.fixture(autouse=True)
def _old_defaults_for_2026_11_08(monkeypatch):
    """FP_RESERVE / CLEAR_FIRST / MINOR_HP 2026-11-08 起默认开（验证局 1-c R1 剩 3）。这里的老用例按当时的默认（关）写的：
    没显式设开关时固定成关；要测新默认的用例自己 setenv / delenv。"""
    import os

    for name in ("FP_RESERVE", "CLEAR_FIRST", "MINOR_HP"):
        if f"VEIL_{name}" not in os.environ and f"HIRO_VEIL_{name}" not in os.environ:
            monkeypatch.setenv(f"VEIL_{name}", "0")

def card(slot, name, *, available=True):
    text = TEXT.get(name, "")
    return CardView(slot, name, name, 1, available, "active", 0, 0, None, text, parse_tags(name, text, once="レッスン中1回" in text))


def case(*cards, status="温存", fp=None, deck=None, remaining=8):
    state = BattleState("r2", 1, remaining, 40, 44, 0, list(cards), statuses=[{"name": status}], pitem_charges=2)
    mem = ExamMemory("r2", turn=3)
    if fp is not None:
        mem.strategy_data["veil_fp"] = fp
    if deck is not None:
        mem.strategy_data["veil_deck"] = deck
    return state, mem


def choose(state, mem):
    legal = [Action.from_legal(a) for a in state.legal_actions()]
    return VeilAdvisor().decide(state, mem, None, legal)


# ---------------------------------------------------------------- 1. 全力値预留（VEIL_FP_RESERVE，默认关）
@pytest.fixture
def reserve(monkeypatch):
    monkeypatch.setenv("VEIL_FP_RESERVE", "1")


def test_reserve_off_keeps_old_choice(monkeypatch):
    monkeypatch.setenv("VEIL_FP_RESERVE", "0")
    state, mem = case(card(0, ANDANTE), fp=4)
    assert choose(state, mem).action.legal_key() == "play:0"


def test_reserve_blocks_spender_that_leaves_less_than_three(reserve):
    """R2 剩 10 回合：账本 4，打 アンダンテ+ 后剩 2 < 3 → 不打（实机这一张让剩 5 回合时 アイドルになります+ 打不出）。"""
    state, mem = case(card(0, ANDANTE), fp=4)
    d = choose(state, mem)
    assert d.action.legal_key() == "skip" and "预留全力値" in d.reason
    state, mem = case(card(0, ANDANTE), card(1, "その他+"), fp=4)
    assert choose(state, mem).action.legal_key() == "play:1"  # 只拦花全力値的牌，别的照打
    state, mem = case(card(0, REBOUND), fp=4)
    assert choose(state, mem).action.legal_key() == "play:0"  # 4−1=3：够


@pytest.mark.parametrize("kw", [
    {"fp": None},  # 账本不知道：不限制
    {"fp": 2, "deck": {IDOL_P: 0, ANDANTE: 1}},  # アイドルになります+ 已打掉：不用留
    {"fp": 2, "remaining": 1},  # 最后一回合：不留
])
def test_reserve_not_applied(reserve, kw):
    state, mem = case(card(0, ANDANTE), **kw)
    assert choose(state, mem).action.legal_key() == "play:0"


def test_reserve_never_blocks_idol_itself(reserve, monkeypatch):
    """アイドルになります+ 自己花 3 不受预留限制（它的打出条件：手里另有两张 エンターテイナー+／オーバードライブ+／教えてあげる+）。"""
    import hiro.brain.veil as veil

    from hiro.brain.veil import ENTERTAINER, TEACH

    seen = []
    real = veil.fp_delta
    monkeypatch.setattr(veil, "fp_delta", lambda c: seen.append(c.name) or real(c))
    state, mem = case(card(0, IDOL_P), card(1, ENTERTAINER), card(2, TEACH), fp=0, deck={IDOL_P: 1})
    d = choose(state, mem)
    assert d.action.legal_key().startswith("play:") and "预留" not in d.reason and IDOL_P in seen


# ---------------------------------------------------------------- 5c. 全力値増加量増加 / 饮料 / 每回合 +N
def test_fp_effect_reads_boost_and_ignores_later_effects():
    from hiro.brain.veil import fp_effect

    assert fp_effect(card(0, "リスキーチャンス+")) == {"gain": 3, "cost": 0, "boost": (50, 4)}
    assert fp_effect(card(0, "トレーニングの成果+"))["gain"] == 4
    assert fp_effect(card(0, "待ち望んだ瞬間"))["gain"] == 0  # 「以降…全力値+1」是常驻触发，打出时不记


def test_boost_multiplies_gain_and_expires():
    from hiro.produce.veil_exam import commit_full_power, track_full_power

    mem = ExamMemory("r2", turn=1)
    data = mem.strategy_data
    track_full_power(data, mem)
    commit_full_power(data, mem, card(0, "リスキーチャンス+"))  # +3（増加量増加在它之后才挂上）
    assert data["veil_fp"] == 3 and data["veil_fp_boosts"] == [{"pct": 50, "until": 4}]
    commit_full_power(data, mem, card(1, DRIVE))  # 2 × 1.5 = 3
    assert data["veil_fp"] == 6 and data["veil_fp_log"][-1]["boost_pct"] == 50
    mem.turn = 5
    track_full_power(data, mem)
    assert data["veil_fp_boosts"] == []  # （4ターン）= 第 1–4 回合
    commit_full_power(data, mem, card(1, DRIVE))
    assert data["veil_fp"] == 8


def test_drinks_enter_ledger():
    from hiro.produce.veil_exam import commit_full_power, track_full_power

    mem = ExamMemory("r2", turn=1)
    data = mem.strategy_data
    track_full_power(data, mem)
    commit_full_power(data, mem, DrinkView(0, "ほうじ茶", "d", "温存に変更全力値+2"), "喝")
    commit_full_power(data, mem, DrinkView(1, "厳選初星チャイ", "d", "以降、ターン開始時、全力値+1"), "喝")
    assert data["veil_fp"] == 2
    mem.turn = 2
    assert track_full_power(data, mem) == 3  # 每回合开始 +1
    assert data["veil_log"] == [[1, "喝", "ほうじ茶"], [1, "喝", "厳選初星チャイ"]]


# ---------------------------------------------------------------- 5a. 续跑旧记忆：按记录重建
R2_HISTORY = ["T1 打 トレーニングの成果+", "T1 打 その他+", "T2 跳过回合", "T3 打 アンダンテ+", "T5 喝 初星ホエイプロテイン",
              "T6 打 巻き返し+", "T8 打 巻き返し+"]


def _seen(mem):
    """本场见过的牌面（veil_fp_cards），测试里不依赖主数据。"""
    from hiro.produce.veil_exam import _fp_record

    mem.strategy_data["veil_fp_cards"] = {f"打 {n}": _fp_record(card(0, n)) for n in TEXT}
    mem.strategy_data["veil_fp_cards"]["喝 初星ホエイプロテイン"] = _fp_record(DrinkView(0, "x", "x", "スキルカード使用数追加+1"))


def test_rebuild_from_complete_history():
    """codex-20261107-1 第 1 局 R2 的旧记忆没有账本：history 从 T1 起、不满 30 条 → 重建，T9 开局 = 4−2−1−1 = 0。"""
    from hiro.produce.veil_exam import track_full_power

    mem = ExamMemory("r2", turn=9, history=list(R2_HISTORY))
    _seen(mem)
    assert track_full_power(mem.strategy_data, mem) == 0
    assert mem.strategy_data["veil_fp_log"][-1]["op"] == "按记录重建"


def test_rebuild_refuses_truncated_history_but_uses_veil_log():
    from hiro.produce.veil_exam import track_full_power

    mem = ExamMemory("r2", turn=9, history=[f"T{1 + i // 4} 打 その他+" for i in range(30)])  # 30 条：被截过
    _seen(mem)
    assert track_full_power(mem.strategy_data, mem) is None
    assert "history 不完整" in mem.strategy_data["veil_fp_gap"]

    mem = ExamMemory("r2", turn=9, history=[f"T{1 + i // 4} 打 その他+" for i in range(30)])
    _seen(mem)
    mem.strategy_data["veil_log"] = [[1, "打", "トレーニングの成果+"], [3, "打", ANDANTE]]  # 不截断的出牌记录优先
    assert track_full_power(mem.strategy_data, mem) == 2


def test_rebuild_unknown_card_text_stays_unknown(monkeypatch):
    import hiro.produce.veil_exam as ve

    monkeypatch.setattr(ve, "_master_fp", lambda kind, name: None)
    mem = ExamMemory("r2", turn=3, history=["T1 打 謎のカード"])
    assert ve.track_full_power(mem.strategy_data, mem) is None
    assert "謎のカード" in mem.strategy_data["veil_fp_gap"]


def test_rebuild_converts_at_ten_between_turns():
    from hiro.produce.veil_exam import rebuild_full_power

    rec = {"gain": 11, "cost": 0, "boost": None, "per_turn": 0}
    got, _ = rebuild_full_power([(1, "打", "x")], 3, lambda k, n: rec)
    assert got[0] == 1  # T1 结束 11 → T2 开始扣 10 → 1（T2 结束 <10，T3 不再扣）


# ---------------------------------------------------------------- 5b. exam 外出牌：下一次 exam 启动时核对
def _committed(mem, hand, remaining=8, ended=False):
    from hiro.produce.veil_exam import track_full_power

    _seen(mem)
    mem.strategy_data.update(veil_fp=3, veil_fp_turn=mem.turn, veil_fp_boosts=[], veil_fp_per_turn=0)
    mem.strategy_data["veil_last_commit"] = {"turn": mem.turn, "remaining": remaining, "kind": "play", "hand": hand, "ended": ended}
    mem.history = ["T1 打 その他+"]
    return track_full_power


@pytest.mark.parametrize("screen,remaining,ended,unknown", [
    ([REBOUND], 8, False, False),  # 同一回合手牌没少
    ([], 8, False, True),  # 同一回合 巻き返し+ 不见了：exam 外打了 → 不知道
    ([REBOUND], 7, True, False),  # 我们自己跳过回合后过了 1 回合
    ([], 7, False, True),  # 没跳过就离开，手里有动全力値的牌
    ([], 6, True, True),  # exam 外过了 2 回合
])
def test_outside_plays_invalidate_ledger(screen, remaining, ended, unknown):
    from hiro.produce.veil_exam import check_outside_plays

    mem = ExamMemory("r2", turn=3)
    _committed(mem, [REBOUND], ended=ended)
    state, _ = case(*(card(i, n) for i, n in enumerate(screen)), remaining=remaining)
    why = check_outside_plays(mem.strategy_data, mem, state, before_remaining=8)
    assert (why is not None) == unknown
    assert (mem.strategy_data["veil_fp"] is None) == unknown
    if unknown:
        assert mem.strategy_data["veil_fp_gap"] == why


def test_outside_play_without_fp_effect_keeps_ledger():
    from hiro.produce.veil_exam import check_outside_plays

    mem = ExamMemory("r2", turn=3)
    _committed(mem, ["その他+", REBOUND])
    state, _ = case(card(0, REBOUND))
    assert check_outside_plays(mem.strategy_data, mem, state, before_remaining=8) is None
    assert mem.strategy_data["veil_fp"] == 3 and mem.strategy_data["veil_fp_log"][-1]["cards"] == ["その他+"]


def test_note_commit_records_hand_and_turn_end():
    from types import SimpleNamespace as NS

    from hiro.produce.veil_exam import note_commit

    state, mem = case(card(0, REBOUND), card(1, "その他+"))
    mem.actions_left = 0
    note_commit(mem.strategy_data, mem, state, NS(kind="play", slot=0))
    last = mem.strategy_data["veil_last_commit"]
    assert last["hand"] == ["その他+"] and last["ended"] and last["remaining"] == 8


# ---------------------------------------------------------------- N10. veil_uncertain：原因、自愈、核对后清除
def test_added_once_card_is_adopted_not_uncertain():
    """codex-20261107-1 第 1 局 R2：T3 手里出现牌组外的 立ち回りの基本（一次性），T5 打出后原来会置 veil_uncertain 且清不掉。"""
    from collections import Counter

    from hiro.produce.veil_exam import adopt_added_cards

    state, mem = case(card(0, "立ち回りの基本"), card(1, REBOUND))
    mem.strategy_data["veil_deck"] = {REBOUND: 1}
    hand = Counter(["立ち回りの基本", REBOUND])
    deck = adopt_added_cards(mem.strategy_data, mem, state, hand, Counter({REBOUND: 1}))
    assert deck["立ち回りの基本"] == 1 and not (hand - deck)
    assert mem.strategy_data["veil_added"] == [{"turn": 3, "cards": {"立ち回りの基本": 1}}]


def test_added_card_upgraded_in_exam_is_renamed_not_doubled():
    """1-f R2：ティーパーティ+ 收进账本后在考试里被强化成 ティーパーティ++，不能再收一张（幽灵牌卡住五卡循环）。"""
    from collections import Counter

    from hiro.produce.veil_exam import adopt_added_cards

    state, mem = case(card(0, "ティーパーティ++"), card(1, REBOUND))
    state.hand[0] = CardView(0, "ティーパーティ++", "ティーパーティ++", 1, True, "active", 0, 0, None, "レッスン中1回", parse_tags("ティーパーティ++", "レッスン中1回", once=True))
    mem.strategy_data["veil_deck"] = {REBOUND: 1, "ティーパーティ+": 1}
    mem.strategy_data["veil_piles"] = {"deck": {"ティーパーティ+": 1}, "grave": {}, "unlocated": {}}
    hand = Counter(["ティーパーティ++", REBOUND])
    deck = adopt_added_cards(mem.strategy_data, mem, state, hand, Counter(mem.strategy_data["veil_deck"]))
    assert deck["ティーパーティ++"] == 1 and deck["ティーパーティ+"] == 0 and +deck == Counter({REBOUND: 1, "ティーパーティ++": 1})
    assert mem.strategy_data["veil_piles"]["deck"]["ティーパーティ+"] == 0
    assert mem.strategy_data["veil_added"][-1]["upgraded"] == {"ティーパーティ+": "ティーパーティ++"}


def test_added_persistent_card_is_not_adopted():
    from collections import Counter

    from hiro.produce.veil_exam import adopt_added_cards

    state, mem = case(card(0, DRIVE))
    mem.strategy_data["veil_deck"] = {}
    deck = adopt_added_cards(mem.strategy_data, mem, state, Counter([DRIVE]), Counter())
    assert not deck and "veil_added" not in mem.strategy_data


def _memory_file(tmp_path, uncertain=True):
    mem = ExamMemory("r2", turn=9)
    mem.strategy_data.update(veil_deck={DRIVE: 1, REBOUND: 1, "教えてあげる+": 1, "エンターテイナー+": 1, IDOL_P: 1, "眠気": 0},
                             veil_uncertain=uncertain, veil_piles={"turn": 2})
    if uncertain:
        mem.strategy_data["veil_uncertain_why"] = [{"turn": 5, "why": "打出的一次性牌不在牌组账本里：立ち回りの基本"}]
    path = tmp_path / "exam_memory.json"
    path.write_text(json.dumps({"label": "r2", "saved_at": 0, "finished": False, "memory": mem.to_dict()}, ensure_ascii=False))
    return path


def test_clear_uncertain_requires_matching_counts(tmp_path):
    from hiro.produce.veil_exam import clear_uncertain

    path = _memory_file(tmp_path)
    before = path.read_text()
    with pytest.raises(ValueError, match="对不上"):
        clear_uncertain(tmp_path, hand=5, deck=1, grave=0, evidence="帧 00036")
    with pytest.raises(ValueError, match="账本外"):
        clear_uncertain(tmp_path, hand=1, deck=4, grave=0, evidence="帧 00036", hand_cards=["立ち回りの基本"])
    assert path.read_text() == before  # 对不上不改文件


def test_clear_uncertain_writes_backup_and_evidence(tmp_path):
    from hiro.produce.veil_exam import clear_uncertain, main

    path = _memory_file(tmp_path)
    assert main(["clear-uncertain", str(tmp_path), "--hand", "5", "--deck", "0", "--grave", "0", "--removed", "18",
                 "--evidence", "所持スキルカード 帧 00036", "--dry-run"]) == 0
    assert json.loads(path.read_text())["memory"]["strategy_data"]["veil_uncertain"]
    out = clear_uncertain(tmp_path, hand=5, deck=0, grave=0, removed=18, evidence="所持スキルカード 帧 00036")
    data = json.loads(path.read_text())["memory"]["strategy_data"]
    assert data["veil_uncertain"] is False and "veil_piles" not in data and data["veil_legacy_piles"]
    assert data["veil_uncertain_cleared"][0]["why_was"][0]["why"].startswith("打出的一次性牌")
    assert "依据：所持スキルカード 帧 00036" in data["veil_notes"][-1]
    assert json.loads((tmp_path / out["backup"].split("/")[-1]).read_text())["memory"]["strategy_data"]["veil_uncertain"]


def test_mark_uncertain_keeps_reason():
    from hiro.produce.veil_exam import mark_uncertain

    mem = ExamMemory("r2", turn=4)
    mark_uncertain(mem.strategy_data, mem, "测试")
    assert mem.strategy_data["veil_uncertain"] and mem.strategy_data["veil_uncertain_why"] == [{"turn": 4, "why": "测试"}]


# ---------------------------------------------------------------- N7. 决策 trace 字段、exam_brain_end 退出原因
def test_trace_context_has_flower_stance_and_fp():
    from hiro.produce.veil_exam import VeilScreen

    state, mem = case(card(0, REBOUND), status="温存", fp=3)
    state.statuses = [{"name": "温存", "value": 2}]
    mem.strategy_data["veil_position"] = {"turn": 3, "stance": "温存", "flower": 2, "next_stance": "強気"}
    ctx = VeilScreen({}, set()).trace_context(state, mem)
    assert ctx["flower"] == 2 and ctx["stance"] == "温存" and ctx["stance_level"] == 2 and ctx["next_stance"] == "強気"
    assert ctx["fp"] == 3 and ctx["uncertain"] is False and ctx["remaining"] == 8
    json.dumps(ctx, ensure_ascii=False)


class _Rec:
    def __init__(self):
        self.events = []

    def log(self, kind, **kw):
        self.events.append((kind, kw))


@pytest.fixture
def exam_env(monkeypatch):
    pytest.importorskip("kaa")
    from types import SimpleNamespace as NS

    import hiro.exec.exam as ex

    t = [0.0]
    frame = NS(img="img", t=0.0, remaining=5, hp=20, genki=10, cards=[], drinks=[], signature=("sig",))
    monkeypatch.setattr(ex, "Loop", lambda interval: iter(range(20)))
    monkeypatch.setattr(ex, "device", NS(screenshot=lambda: "img", click=lambda *a: None))
    monkeypatch.setattr(ex, "_handle_dialogs", lambda img, slow=False: False)
    monkeypatch.setattr(ex, "_settle_fast", lambda img, **kw: frame)
    monkeypatch.setattr(ex, "_stable_frame", lambda **kw: frame)
    monkeypatch.setattr(ex, "_stale_before_tap", lambda f: None)
    monkeypatch.setattr(ex, "_execute", lambda d, f: [[100, 1010]])
    monkeypatch.setattr(ex, "_wait_change", lambda sig: True)
    monkeypatch.setattr(ex, "_play_missed", lambda f, c: False)
    monkeypatch.setattr(ex.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(ex, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    return ex


def _plugins(context=None, blow=None):
    from types import SimpleNamespace as NS

    from hiro.contracts import Decision as DC

    class Memory:
        turn, last_remaining, history, plan, blocked = 1, None, [], [], []

        def block(self, a):
            pass

    class Brain:
        profile = NS(idol_id="i_test")

        def new_memory(self, exam):
            return Memory()

        def commit(self, state, memory, d):
            pass

    class State:
        drinks, hand, remaining_turns = [], [], 5

        def legal_actions(self):
            return ["play:0", "skip"]

    class Source:
        def begin(self, ctx): pass
        def read(self, frame, memory):
            if blow:
                raise blow
            return State(), None
        def confidence(self): return "screen"
        def executed(self, a, r): pass
        def previews(self, s): return None
        def end(self, r): pass

    if context is not None:
        Source.trace_context = lambda self, state, memory: context

    class Advisor:
        def on_round_start(self, n, ctx): pass
        def decide(self, state, memory, previews, legal): return DC(Action.play_card(0), "测试", "rule")
        def on_round_end(self, r): pass

    return Brain(), Source(), Advisor()


def test_exam_brain_end_reason_and_decision_context(exam_env):
    rec = _Rec()
    brain, src, adv = _plugins(context={"flower": 2, "fp": 3})
    done = iter([False, True])
    exam_env.run_exam(brain, "r2", rec, lambda: next(done), source=src, advisor=adv)
    dec = next(kw for k, kw in rec.events if k == "battle_decision")
    end = next(kw for k, kw in rec.events if k == "exam_brain_end")
    assert dec["context"] == {"flower": 2, "fp": 3}
    assert end["reason"] == "ended" and end["context"] == {"flower": 2, "fp": 3} and end["remaining"] == 5


def test_exam_brain_end_reason_on_resume_blocked(exam_env):
    from hiro.loop.errors import ResumeBlocked

    rec = _Rec()
    brain, src, adv = _plugins(blow=ResumeBlocked("VEIL 循环下一张牌不可用：巻き返し+"))
    with pytest.raises(ResumeBlocked):
        exam_env.run_exam(brain, "r2", rec, lambda: False, source=src, advisor=adv)
    end = next(kw for k, kw in rec.events if k == "exam_brain_end")
    assert end["reason"].startswith("ResumeBlocked") and "巻き返し+" in end["reason"]
    assert "context" not in next((kw for k, kw in rec.events if k == "battle_decision"), {})


# ---- 没落地的出牌撤销（veil_play_unverified 自愈，验证局 codex-20261107-1-b）

def _pending(mem, card_name, hand_before):
    from collections import Counter

    from hiro.produce.veil_exam import undo_unlanded_play

    undo = json.loads(json.dumps(mem.to_dict()))
    mem.this_turn.append(f"打 {card_name}")
    mem.actions_left = 0
    mem.strategy_data["veil_deck"] = {"x": 0}
    mem.strategy_data["veil_pending_play"] = {"turn": mem.turn, "card": card_name, "count": 1, "stance": "温存",
                                              "flower": 3, "next_stance": None, "undo": undo, "hand": hand_before}
    return lambda hand: undo_unlanded_play(mem, Counter(hand))


def test_undo_unlanded_play_restores_memory():
    mem = ExamMemory("r2", turn=5, actions_left=1, this_turn=["打 A"])
    mem.strategy_data["veil_deck"] = {"x": 1}
    undo = _pending(mem, "タフネス+", ["A2", "タフネス+"])
    assert undo(["A2+", "タフネス+"]) == "タフネス+"  # 回合中加「+」不算变化
    assert mem.this_turn == ["打 A"] and mem.actions_left == 1 and mem.strategy_data["veil_deck"] == {"x": 1}
    assert "veil_pending_play" not in mem.strategy_data
    assert mem.strategy_data["veil_undo_log"][-1]["card"] == "タフネス+"
    assert not mem.strategy_data.get("veil_uncertain")


def test_undo_mis_tap_marks_uncertain():
    mem = ExamMemory("r2", turn=5)
    undo = _pending(mem, "タフネス+", ["OD", "タフネス+", "巻き返し+"])
    assert undo(["タフネス+", "巻き返し+"]) == "タフネス+"
    assert mem.strategy_data["veil_uncertain"] is True
    assert "OD" in mem.strategy_data["veil_uncertain_why"][-1]["why"]


def test_undo_refuses_when_hand_changed_more():
    mem = ExamMemory("r2", turn=5)
    undo = _pending(mem, "タフネス+", ["OD", "タフネス+", "巻き返し+"])
    assert undo(["タフネス+"]) is None  # 两张不见了：不知道发生了什么，照旧停
    assert undo(["タフネス+", "OD", "巻き返し+", "新"]) is None  # 多了牌
    assert "veil_pending_play" in mem.strategy_data


def test_wait_exam_page_waits_through_opening():
    from hiro.produce.veil_exam import wait_exam_page

    frames = iter(["anim", "anim", "exam"])
    t = {"now": 0.0}
    got = wait_exam_page(lambda: next(frames), lambda f: f == "exam", timeout=30, clock=lambda: t["now"],
                         sleep=lambda s: t.update(now=t["now"] + s))
    assert got == "exam"
    assert wait_exam_page(lambda: "anim", lambda f: f == "exam", timeout=3, clock=lambda: t["now"],
                          sleep=lambda s: t.update(now=t["now"] + s)) is None


def test_sync_bar_follows_ledger_and_only_logs_screen():
    """饮料账本优先（使用者 2026-11-07）：画面认错 / 认不出不改账本，只记 veil_drink_log；栏尾账本没有的按画面补。"""
    pytest.importorskip("kaa")
    from hiro.produce.veil_exam import sync_bar

    whey, boost = "初星ホエイプロテイン", "ブーストエキス"
    data = {}
    physical = [{"slot": 0, "state": "drink", "name": whey}, {"slot": 1, "state": "unknown"},
                {"slot": 2, "state": "drink", "name": boost}, {"slot": 3, "state": "drink", "name": boost}]
    drinks = sync_bar(data, [whey, whey, whey], physical, 3)
    assert data["veil_bar"] == [whey, whey, whey, boost]  # 第 3 格画面认成ブースト：按账本；第 4 格账本没有：按画面补
    assert [d.slot for d in drinks] == [0, 1, 2, 3]
    assert any("账本 初星ホエイプロテイン，画面 ブーストエキス" in n for n in data["veil_drink_log"][-1]["diff"])


def test_sync_bar_resumes_from_old_slot_record():
    pytest.importorskip("kaa")
    from hiro.produce.veil_exam import sync_bar

    data = {"veil_drinks": [{"state": "consumed", "name": "初星ホエイプロテイン"}, {"state": "drink", "name": "ブーストエキス"},
                            None, None]}
    sync_bar(data, ["初星ホエイプロテイン", "ブーストエキス"], [{"slot": 0, "state": "drink", "name": "ブーストエキス"}], 5)
    assert data["veil_bar"] == ["ブーストエキス"]


def test_swap_pending_waits_until_hand_replaced():
    """换手牌后画面还是旧手牌：不决策；手牌变了 / 换回合就放行（验证局 1-e R2 剩 8）。"""
    from collections import Counter
    from types import SimpleNamespace

    from hiro.produce.veil_exam import swap_pending

    data = {"veil_swap_wait": {"turn": 5, "before": ["アイドルになります+", "フルスロットル+", "達成感+", "トレーニングの成果+"]}}
    mem = SimpleNamespace(turn=5, strategy_data=data)
    old = Counter(["アイドルになります", "フルスロットル+", "達成感+", "トレーニングの成果++"])
    assert swap_pending(data, mem, old)
    assert swap_pending(data, mem, Counter(["頂点へ+", "巻き返し+", "タフネス+", "達成感+"])) is False
    assert "veil_swap_wait" not in data
    data["veil_swap_wait"] = {"turn": 4, "before": ["達成感+"]}
    assert swap_pending(data, mem, Counter(["達成感+"])) is False


# ---------------------------------------------------------------- 洗牌规则（HIRO_VEIL_STRICT_SHUFFLE）
def _piles(deck, grave, hand):
    return {"turn": 1, "hand": hand + [None] * (5 - len(hand)), "deck": dict.fromkeys(deck, 1), "grave": dict.fromkeys(grave, 1),
            "unlocated": {}}


@pytest.mark.parametrize("strict", [False, True])
def test_shuffle_after_deck_drawn_out(monkeypatch, strict):
    """牌堆只剩 D，新回合抽 3 张：先抽 D，再把弃牌堆（A + 上回合手牌 C）洗进来抽 A、C。开不开严格模式都认。"""
    from hiro.produce.veil_exam import track_piles

    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1" if strict else "0")
    data = {"veil_piles": _piles(["D"], ["A"], ["C"])}
    out = track_piles(data, 2, ["D", "A", "C", None, None], Counter())
    assert out == {"deck": [], "grave": [], "unlocated": []}


def test_strict_shuffle_rejects_undrawn_deck(monkeypatch):
    """牌堆还剩 B 没抽到，却抽到了弃牌堆的 A、C：原来照样当洗牌（B 留在牌堆）；严格模式不认，整副记成位置不明。"""
    from hiro.produce.veil_exam import track_piles

    hand = ["D", "A", "C", None, None]
    monkeypatch.delenv("HIRO_VEIL_STRICT_SHUFFLE", raising=False)
    data = {"veil_piles": _piles(["B", "D"], ["A"], ["C"])}
    assert track_piles(data, 2, hand, Counter({"B": 1})) == {"deck": ["B"], "grave": [], "unlocated": []}
    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1")
    data = {"veil_piles": _piles(["B", "D"], ["A"], ["C"])}
    assert track_piles(data, 2, hand, Counter({"B": 1})) == {"deck": [], "grave": [], "unlocated": ["B"]}


def test_hif_fill_count_only_r2_with_switch(monkeypatch):
    """H.I.F応援棒：本战 R2 开考时牌堆补到 22 张；R1 不补；开关关时不补。"""
    from hiro.produce.veil_exam import fill_count

    deck = {f"c{i}": 1 for i in range(21)}
    monkeypatch.delenv("HIRO_VEIL_HIF_FILL", raising=False)
    assert fill_count(2, deck) == 0
    monkeypatch.setenv("HIRO_VEIL_HIF_FILL", "1")
    assert fill_count(2, deck) == 1 and fill_count(1, deck) == 0
    assert fill_count(2, {**deck, "x": 1, "y": 1}) == 0


def test_hif_fill_placeholder_resolves_when_drawn(monkeypatch):
    """codex-20261108-3 第 1 局 R2：21 张牌组多出应援棒生成的 魅せ方の基本，T3 抽到后整副记成位置不明。
    占位「基本?」在牌堆里，抽到 魅せ方の基本 时改成真名，牌堆账照常接上。"""
    from hiro.produce.veil_exam import BASIC_SLOT, resolve_basic, track_piles

    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1")
    data = {"veil_deck": {"A": 1, "B": 1, BASIC_SLOT: 1}}
    track_piles(data, 1, ["A", None, None, None, None], Counter({"B": 1, BASIC_SLOT: 1}))
    hand = Counter({"B": 1, "魅せ方の基本": 1})
    assert resolve_basic(data, hand) == {"魅せ方の基本": 1}
    assert data["veil_deck"] == {"A": 1, "B": 1, BASIC_SLOT: 0, "魅せ方の基本": 1}
    deck = Counter({k: v for k, v in data["veil_deck"].items() if v > 0})
    out = track_piles(data, 2, ["B", "魅せ方の基本", None, None, None], deck - hand)
    assert out == {"deck": [], "grave": ["A"], "unlocated": []}
    assert resolve_basic(data, Counter({"アピールの基本": 1})) == {}  # 占位用完了：不再改名


def test_hif_fill_excluded_counts_resolved_basic():
    """打出离开的一次性「基本」牌（应援棒生成）进 trace 的 excluded，占位没抽到时不算离开。"""
    from hiro.produce.veil_exam import BASIC_SLOT, VeilScreen

    state = BattleState("r2", 1, 5, 40, 44, 0, [])
    mem = ExamMemory("r2", turn=3)
    mem.strategy_data.update(veil_initial={"A": 1}, veil_deck={"A": 1, BASIC_SLOT: 0, "魅せ方の基本": 0}, veil_fill=1,
                             veil_fill_log=[{"cards": {"魅せ方の基本": 1}}])
    assert VeilScreen({}, set()).trace_context(state, mem)["excluded"] == {"魅せ方の基本": 1}
    mem.strategy_data.update(veil_deck={"A": 1, BASIC_SLOT: 1}, veil_fill_log=[])
    assert VeilScreen({}, set()).trace_context(state, mem)["excluded"] is None


def test_generated_sleep_joins_deck_after_same_play_draws(monkeypatch):
    """codex-20261108-3 第 3 局 R1 T4：フルスロットル+ 先抽 3 张、再把眠気生成进山札。山札只剩 2 张，洗牌后抽到 ティーパーティ+。
    眠気先并进山札时严格洗牌判「山札没抽完」，整副位置不明；改成抽完再并进来。"""
    from hiro.produce.veil_exam import track_piles

    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1")
    piles = _piles(["E", "T"], ["G", "P"], ["M", None, "Z"])
    piles["born"] = {"眠気": 1}
    data = {"veil_piles": piles}
    hand = ["M", "Z", "T", "E", "P"]
    out = track_piles(data, 1, hand, Counter({"G": 1, "眠気": 1}))
    assert out == {"deck": ["G", "眠気"], "grave": [], "unlocated": []}
    assert "born" not in data["veil_piles"]
    # 跨回合：生成的眠気早已在山札里，可以被新回合抽到
    piles = _piles(["E"], ["G"], ["M"])
    piles["born"] = {"眠気": 1}
    data = {"veil_piles": piles}
    out = track_piles(data, 2, ["E", "眠気", None, None, None], Counter({"G": 1, "M": 1}))
    assert out == {"deck": [], "grave": ["G", "M"], "unlocated": []}


def _play(data, name, *, once=False):
    """模拟 _commit 对牌堆的处理：打出的牌离手，非一次性进捨札。"""
    piles = data["veil_piles"]
    piles["hand"][piles["hand"].index(name)] = None
    if not once:
        piles["grave"][name] = piles["grave"].get(name, 0) + 1


def test_swap_discards_whole_hand_before_drawing(monkeypatch):
    """codex-20261108-3-c 第 1 局 R1 T4：山札 3 张时打 ガタゴトすやすや+（手札をすべて入れ替える）换 4 张。
    原手牌整手先进捨札，抽完山札 3 张后洗牌，抽到刚丢的 フルスロットル+；它抽 2 张又抽到刚丢的 忍耐力+。
    1e7ac07 改成「先抽后丢」时这里整副位置不明。"""
    from hiro.produce.veil_exam import track_piles

    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1")
    grave = ["エンターテイナー+", "オーバードライブ+", "タフネス+", "一心不乱+", "教えてあげる+", "仕切り直し+", "頂点へ+"]
    old = ["フルスロットル+", "巻き返し+", "忍耐力+", "達成感+"]
    data = {"veil_piles": {**_piles(["アイドルになります+", "トレーニングの成果+", "第一印象+"], grave, old), "turn": 4, "swap": True}}
    new = ["トレーニングの成果+", "第一印象+", "アイドルになります+", "フルスロットル+"]
    rest = Counter([*grave, "巻き返し+", "忍耐力+", "達成感+"])
    out = track_piles(data, 4, [*new, None], rest)
    assert Counter(out["deck"]) == rest and out["grave"] == [] and out["unlocated"] == []
    assert "swap" not in data["veil_piles"]
    _play(data, "フルスロットル+", once=True)
    data["veil_piles"]["born"] = {"眠気": 1}
    hand = ["トレーニングの成果+", "第一印象+", "アイドルになります+", "忍耐力+", "仕切り直し+"]
    out = track_piles(data, 4, hand, rest - Counter(["忍耐力+", "仕切り直し+"]) + Counter(["眠気"]))
    assert out["unlocated"] == []


def test_two_swaps_then_shuffle_next_turn(monkeypatch):
    """codex-20261108-3-b 第 1 局 R2 T4：先打 仕切り直し+（也是换手牌，再抽 2），山札 2 张不够、洗牌；再打 ガタゴト 换 3 张。
    T5 抽 5：山札剩 2 张抽完、洗牌抽到 エンターテイナー+ 等。两次都按「先丢整手、再抽」记，T5 没有位置不明。"""
    from hiro.produce.veil_exam import track_piles

    monkeypatch.setenv("HIRO_VEIL_STRICT_SHUFFLE", "1")
    old = ["ひと呼吸+", "眠気", "アイドルになります+", "ガタゴトすやすや+"]
    data = {"veil_piles": {**_piles(["巻き返し+", "演出計画+"], ["エンターテイナー+", "オーバードライブ+", "一心不乱+", "教えてあげる+"], old),
                           "turn": 4, "swap": True}}
    hand = ["演出計画+", "巻き返し+", "眠気", "ガタゴトすやすや+", "ひと呼吸+"]
    out = track_piles(data, 4, hand, Counter(["エンターテイナー+", "オーバードライブ+", "一心不乱+", "教えてあげる+", "アイドルになります+"]))
    assert out["unlocated"] == [] and out["grave"] == []
    _play(data, "巻き返し+")
    out = track_piles(data, 4, [h if h != "巻き返し+" else None for h in hand],
                      Counter(["エンターテイナー+", "オーバードライブ+", "一心不乱+", "教えてあげる+", "アイドルになります+", "巻き返し+"]))
    _play(data, "ガタゴトすやすや+", once=True)
    data["veil_piles"]["swap"] = True
    new = ["教えてあげる+", "エンターテイナー+", "アイドルになります+", None, None]
    rest = Counter(["オーバードライブ+", "一心不乱+", "巻き返し+", "演出計画+", "眠気", "ひと呼吸+"])
    out = track_piles(data, 4, new, rest)
    assert Counter(out["deck"]) == Counter(["オーバードライブ+", "一心不乱+"]) and out["unlocated"] == []
    _play(data, "アイドルになります+", once=True)
    _play(data, "エンターテイナー+")
    _play(data, "教えてあげる+")
    t5 = ["一心不乱+", "オーバードライブ+", "エンターテイナー+", "巻き返し+", "眠気"]
    out = track_piles(data, 5, t5, Counter(["演出計画+", "ひと呼吸+", "教えてあげる+"]))
    assert out["unlocated"] == []


def test_fp_effect_drops_condition_broken_by_own_stance_change():
    """涙の思い出+「温存に変更 元気+4 強気の場合、全力値+4」：先变温存，強気条件不成立，不记 +4。
    没有先变姿态的条件（うたたねレポート+ 非全力状態の場合 +2）照旧算；「〜の場合、使用可」是打出条件，不受影响。"""
    from hiro.brain.veil import fp_effect

    assert fp_effect("温存に変更元気+4強気の場合、全力値+4レッスン中1回")["gain"] == 0
    assert fp_effect("強気に変更強気の場合、全力値+4")["gain"] == 4
    assert fp_effect("温存2段階目に変更非全力状態の場合、全力値+2")["gain"] == 2
    assert fp_effect("全力値+5非全力状態の場合、全力値+2全力の場合、手札のパラメータ値増加+7")["gain"] == 7
    assert fp_effect("全力値が5以上の場合、使用可全力値+5次のターン、手札のパラメータ値増加+8")["gain"] == 5


def test_undo_of_ledger_unlocked_card_drops_fp_ledger():
    """codex-20261108-3-b 第 1 局 R1 attempt2 T8：账本全力値 6、画面 3，按账本解灰打 アイドルになります+（消費3）打不出，
    撤销后又解灰，循环 2.5 分钟。撤销的正是这回合解灰的牌 → 账本作废，下一帧按画面补账。"""
    from types import SimpleNamespace

    from hiro.produce.veil_exam import distrust_fp_after_undo

    mem = SimpleNamespace(turn=8)
    data = {"veil_fp": 6, "veil_fp_log": [{"turn": 8, "op": "按账本解灰", "cards": ["アイドルになります+"], "fp": 6}]}
    assert distrust_fp_after_undo(data, mem, "巻き返し+") is False and data["veil_fp"] == 6
    assert distrust_fp_after_undo(data, mem, "アイドルになります+") is True
    assert data["veil_fp"] is None and "アイドルになります+" in data["veil_fp_gap"]
    data2 = {"veil_fp": 6, "veil_fp_log": [{"turn": 7, "op": "按账本解灰", "cards": ["アイドルになります+"]}]}
    assert distrust_fp_after_undo(data2, mem, "アイドルになります+") is False


def test_swap_card_still_in_hand_stops_waiting_early():
    """codex-20261108-1-b R1 T9：仕切り直し+ 没落地，一直在手里，按换手动画等满 12 帧（约 40 秒）。
    换手牌本身还在手里时只等 SWAP_FLOAT 帧，之后交给「上一手没落地」处理。"""
    from collections import Counter
    from types import SimpleNamespace

    from hiro.produce.veil_exam import SWAP_FLOAT, swap_pending

    data = {"veil_swap_wait": {"turn": 9, "card": "仕切り直し+", "before": ["アイドルになります+"]}}
    mem = SimpleNamespace(turn=9, strategy_data=data)
    hand = Counter(["仕切り直し+", "アイドルになります+"])
    for _ in range(SWAP_FLOAT):
        assert swap_pending(data, mem, hand)
    assert swap_pending(data, mem, hand) is False and "veil_swap_wait" not in data
    assert data["veil_swap_log"][-1]["op"].startswith("换手牌还在手里")
    # 换手牌不在手里（真在播动画）：照旧等
    data["veil_swap_wait"] = {"turn": 9, "card": "仕切り直し+", "before": ["アイドルになります+"]}
    for _ in range(SWAP_FLOAT + 2):
        assert swap_pending(data, mem, Counter(["アイドルになります+"]))
