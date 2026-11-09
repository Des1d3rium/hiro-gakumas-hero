"""2026-11-08 trusted trace 决策审查（以进入循环的目标回合为准）新增的 8 个 VEIL 开关。

每条用例对应审查里的一处实机决策：开关关着时保持原行为，打开后改成能更早进循环（或不掉出循环）的选择。
牌面文字取自实机 trace（steps.jsonl 的 hand[].text）。"""

import dataclasses

import pytest

from hiro.brain.cards import CardView, parse_tags
from hiro.brain.veil import DRIVE, ENTERTAINER, REBOUND, TEACH, WHEY, hp_loss
from test_veil import card, case, choose

DRIVE_TEXT = "強気に変更パラメータ+4（2回）全力値+2スキルカード使用数追加+1眠気を山札のランダムな位置に生成次のターン、温存に変更"
TEACH_TEXT = "強気に変更パラメータ+6熱意追加+2（2ターン）次のターン、スキルカードを2枚引く"
ENT_TEXT = "強気に変更パラメータ+12"
REBOUND_TEXT = "全力値消費1温存に変更熱意追加+6すべてのスキルカードのコスト値減少-1"
IDOL_TEXT = "全力値消費3元気+4温存に変更スキルカード使用数追加+1手札のパラメータ上昇回数増加+1重複不可 レッスン中1回"
BREATH_TEXT = "好調4ターン集中+5レッスン中1回"


def core(slot, name):
    return card(slot, name, {DRIVE: DRIVE_TEXT, TEACH: TEACH_TEXT, ENTERTAINER: ENT_TEXT, REBOUND: REBOUND_TEXT}[name])


def grey(c):
    return dataclasses.replace(c, available=False)


@pytest.fixture
def on(monkeypatch):
    """当前默认开关（CLEAR_FIRST 等 2026-11-08 起默认开）+ 体力闸关（合成用例不写体力细节）。"""
    for name in ("CLEAR_FIRST", "FP_RESERVE", "MINOR_HP", "HP_NET"):
        monkeypatch.setenv(f"VEIL_{name}", "1")
    monkeypatch.setenv("VEIL_HP_GUARD", "0")

    def flag(name, value=True):
        monkeypatch.setenv(f"VEIL_{name}", "1" if value else "0")

    return flag


def test_idol_clear_plays_last_once_card_despite_two_of_three(on):
    """3-1 第 1 局 R1 attempt1 剩 6：強気、手里只剩 OD + アイドルになります+（不灰），原来三选二挡住 → Skip。"""
    idol = card(1, "アイドルになります+", IDOL_TEXT, once=True)
    state, mem = case(core(0, DRIVE), idol, status="強気", flower=2,
                      deck=["一心不乱+", "眠気", "頂点へ+", ENTERTAINER, TEACH, REBOUND])
    mem.strategy_data["veil_fp"] = 4
    on("STUCK_SODA", False)
    on("IDOL_CLEAR", False)
    assert choose(state, mem) == "skip"
    on("IDOL_CLEAR")
    assert choose(state, mem) == "play:1"


def test_stance_screen_takes_costlier_stance_for_hp():
    """3-c 第 1 局 R2 attempt1 T1：账本のんびり、画面強気1，頂点へ+（コスト 8）按 0 消耗打出，体力 8→1。"""
    import os

    peak = CardView(0, "頂点へ+", "頂点へ+", 1, True, "mental", 8, 0, None, "パラメータ値増加+6", parse_tags("頂点へ+", "", once=True))
    state, _ = case(peak, status="のんびり")
    state.genki = 0
    state.statuses.append({"name": "_screen_stance", "value": "強気", "level": 1})
    os.environ.pop("VEIL_STANCE_SCREEN", None)
    try:
        assert hp_loss(state, peak, "のんびり") == 0
        os.environ["VEIL_STANCE_SCREEN"] = "1"
        assert hp_loss(state, peak, "のんびり") == 16
        assert hp_loss(state, peak, "温存") == 4  # 不是账本姿态的推演（规划里的假设姿态）不受影响
    finally:
        os.environ.pop("VEIL_STANCE_SCREEN", None)


def test_cycle_plan_teach_instead_of_entertainer(on):
    """3-c-3 第 1 局 R1 attempt2 剩 4：のんびり、夜空 2、手里 4 核心 + 眠気，山札只剩 ひと呼吸+，一番星还有次数。
    原来打 エンタ（下回合抽 3 拿不齐）→ 剩 1 才进循环；教えて（预约抽 2）→ 眠気，下回合抽 5 张全到手、のんびり、夜空 1。"""
    hand = [core(0, ENTERTAINER), core(1, REBOUND), card(2, "眠気"), core(3, TEACH), core(4, DRIVE)]
    hand[2] = dataclasses.replace(hand[2], tags=dataclasses.replace(hand[2].tags, is_sleep=True))
    state, mem = case(*hand, status="のんびり", flower=2, deck=["ひと呼吸+"])
    state.remaining_turns = 4
    state.piles["once"] = ["ひと呼吸+"]
    mem.strategy_data.update(veil_fp=6, veil_star=1, veil_known_once=["ひと呼吸+"])
    on("CYCLE_PLAN")
    assert choose(state, mem) == "play:3"
    # 教えて 打完（のんびり→強気、夜空 1、解除 +1）：接着清 眠気
    mem.this_turn.append(f"打 {TEACH}")
    state.hand = [c for c in hand if c.name != TEACH]
    state.statuses = [{"name": "強気"}]
    state.pitem_charges = 1
    state.piles["next_draw"] = 5
    assert choose(state, mem) == "play:2"


def test_cycle_plan_no_flower_push_without_way_back_to_warm(on):
    """3-3 第 3 局 R2 剩 5：過渡里 教えて 在夜空 1 打出（→のんびり、夜空 3），又打 エンタ 推夜空 → 強気、巻き返し 不在手、
    一番星用完，下回合強気开局，过渡作废三回合。推夜空后切不回温存就留到下回合。"""
    # 山札还有 ひと呼吸+：OD 再生成一张眠気就是六张，规划找不到下回合进循环的打法，落到原来的过渡规则
    state, mem = case(core(0, DRIVE), core(1, ENTERTAINER), status="のんびり", flower=3, deck=[REBOUND, TEACH, "ひと呼吸+"])
    state.remaining_turns = 5
    state.piles["next_draw"] = 5
    state.piles["once"] = ["ひと呼吸+"]
    mem.this_turn.append(f"打 {TEACH}")
    mem.strategy_data.update(veil_fp=0, veil_star=0)
    on("CYCLE_PLAN", False)
    assert choose(state, mem) == "play:1"
    on("CYCLE_PLAN")
    assert choose(state, mem) == "skip"
    mem.strategy_data["veil_star"] = None  # 一番星次数不明：可能下回合自动のんびり，不拦
    assert choose(state, mem) == "play:1"


def test_strong_teach_when_only_reusable_and_not_warm(on):
    """1-c 第 1 局 R2 attempt1 剩 8：強気、夜空 2、只剩复用牌 → 原来「温存留到下回合」跳过；強気 下没有温存可留，
    打 教えて 推夜空、预约下回合多抽 2。"""
    state, mem = case(core(0, TEACH), core(1, ENTERTAINER), status="強気", flower=2,
                      deck=["アイドルになります+", "嬉しい誤算", REBOUND, DRIVE, "アンダンテ+"])
    on("STRONG_TEACH", False)
    assert choose(state, mem) == "skip"
    on("STRONG_TEACH")
    assert choose(state, mem) == "play:0"
    state.statuses = [{"name": "温存"}]  # 温存下照旧留温存
    assert choose(state, mem) == "skip"


def test_cycle_whey_not_with_four_card_hand(on):
    """3-c 第 1 局 R1 attempt2 剩 4：进循环时手里 4 张（第五张在山札），喝ホエイ 多出的次数没牌打。"""
    hand = [core(0, TEACH), core(1, ENTERTAINER), core(2, REBOUND), core(3, DRIVE)]
    state, mem = case(*hand, status="温存", flower=1, deck=["ひと呼吸+"], drinks=(WHEY,))
    state.remaining_turns = 4
    state.piles["once"] = ["ひと呼吸+"]
    mem.strategy_data["veil_fp"] = 4
    on("CYCLE_WHEY", False)
    assert choose(state, mem) == "drink:0"
    on("CYCLE_WHEY")
    assert choose(state, mem) == "play:3"  # 循环第一张 OD
    state.remaining_turns = 1  # 最后一回合照喝
    mem.strategy_data.pop("veil_cycle", None)
    assert choose(state, mem) == "drink:0"


def test_drive_check_no_overdrive_that_cannot_unlock(on):
    """3-2 第 2 局 R1 attempt1 剩 2：全力値 0，OD +2 也不够 アイドルになります+ 的 3，白打 OD 多一张眠気。"""
    idol = grey(card(1, "アイドルになります+", IDOL_TEXT, once=True))
    state, mem = case(core(0, DRIVE), idol, core(2, ENTERTAINER), status="温存", flower=1,
                      deck=["一心不乱+", REBOUND, TEACH])
    mem.strategy_data["veil_fp"] = 0
    on("DRIVE_CHECK", False)
    assert choose(state, mem) == "play:0"
    on("DRIVE_CHECK")
    assert choose(state, mem) != "play:0"
    mem.strategy_data["veil_fp"] = 1  # 1 + 2 = 3 够了，但 OD 离手后只剩 エンタ 一张，三选二仍挡住
    assert choose(state, mem) != "play:0"
    on("IDOL_CLEAR")  # 三选二放开后 OD 有用，照打
    assert choose(state, mem) == "play:0"


def test_drive_check_not_for_rebound_in_full_power():
    """3-c-3 第 1 局 R1 attempt1 剩 5：全力下为 巻き返し+ 攒全力値打 OD；全力切不了姿态，攒了也没用。"""
    import os

    from hiro.brain.veil import drive_useful

    rebound = grey(core(1, REBOUND))
    os.environ["VEIL_DRIVE_CHECK"] = "1"
    try:
        assert not drive_useful(core(0, DRIVE), [rebound], 0, "全力", {DRIVE})
        assert drive_useful(core(0, DRIVE), [rebound], 0, "強気", {DRIVE})
    finally:
        os.environ.pop("VEIL_DRIVE_CHECK", None)


def test_stance_later_extra_action_or_whey_first(on):
    """3-3 第 3 局 R2 剩 9 / 3-1 第 1 局 R1 剩 6：強気、只剩 1 次，一心不乱+（切温存、不加次数）先打就结束回合。
    先打不改姿态的再动一次性牌；没有就先喝ホエイ。"""
    isshin = card(0, "一心不乱+", "強気の場合、使用可温存に変更強気効果のスキルカードのパラメータ値増加+45・コスト値増加+7", once=True)
    party = card(1, "ティーパーティ+", "スキルカード使用数追加+1スキルカードを引く手札をすべてレッスン中強化", once=True)
    state, mem = case(isshin, party, status="強気", flower=1, deck=["頂点へ+", REBOUND, TEACH, ENTERTAINER, DRIVE])
    on("STANCE_LATER", False)
    assert choose(state, mem) == "play:0"
    on("STANCE_LATER")
    assert choose(state, mem) == "play:1"
    other = card(1, "達成感+", "温存に変更スキルカード使用数追加+1", once=True)  # 切走強気：一心不乱 就打不了，不先打
    state, mem = case(isshin, other, card(2, "眠気"), status="強気", flower=1, drinks=(WHEY,),
                      deck=["頂点へ+", REBOUND, TEACH, ENTERTAINER, DRIVE])
    assert choose(state, mem) == "drink:0"


def test_hp_zero_status_makes_next_card_free(monkeypatch):
    """3-3 第 1 局 R1 attempt2 剩 9：全身全霊+ 的「次に使用したスキルカードの消費体力を0にする（2回）」还有次数，
    インフルエンサー++ 被当成掉血牌留到温存。"""
    infl = CardView(0, "インフルエンサー++", "x", 2, True, "mental", 5, 0, None, "スキルカード使用数追加+1",
                    parse_tags("インフルエンサー++", "スキルカード使用数追加+1", once=True))
    state, _ = case(infl, status="強気")
    state.genki = 0
    state.statuses = [{"name": "強気", "value": 1}, {"name": "消費体力変化", "value": 2}]
    monkeypatch.setenv("VEIL_HP_ZERO", "0")
    assert hp_loss(state, infl, "強気") == 10
    monkeypatch.setenv("VEIL_HP_ZERO", "1")
    assert hp_loss(state, infl, "強気") == 0
