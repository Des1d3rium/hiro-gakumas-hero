"""tools/batch_hif.route_tap：VEIL 批量的固定路线（授業蓝、分支第一项、确认强气换卡）。用合成 OCR 框，不需要截图。"""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("kaa")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_hif as B  # noqa: E402
import hiro.vision.action as action  # noqa: E402


def box(text, cy, x=360):
    return (text, 1.0, (x - 60, cy - 15, x + 60, cy + 15))


TOP = [box("授業", 63, 112)]
ROWS = [box("-5", 663), box("各レッスンは適度に", 700), box("-5", 763), box("入念にストレッチ", 801), box("メンタルケア", 902)]


@pytest.fixture
def options(monkeypatch):
    opts = [NS(kind="class", attr=a, center=(x, 1010)) for a, x in (("vo", 197), ("da", 360), ("vi", 522))]
    monkeypatch.setattr(action, "_options", lambda img: opts)


def test_action_page_partial_prompt_still_taps_da(options):
    """OCR 只读出「を選択してください」时也要认出行动选择页，点蓝（Da）授業。"""
    boxes = [box("H.I.F本戦まで", 63, 113), box("を選択してください", 865)]
    assert B.route_tap(None, boxes, action_page=True) == ("study_da", (360, 1010))


def test_action_page_other_selection_prompt_stops(options):
    boxes = [box("H.I.F本戦まで", 63, 113), box("スキルカードを選択してください", 865)]
    assert B.route_tap(None, boxes, action_page=True) is None


def test_study_branch_first_tap():
    assert B.route_tap(None, TOP + ROWS, action_page=False) == ("study_first", (360, 698))


def test_study_branch_confirms_strong_card_change():
    detail = [box("トラブルカード以外のスキルカードを選択し", 518), box("て異なる強気関係のスキルカードにセレクト", 559), box("チェンジ", 600)]
    assert B.route_tap(None, TOP + detail + ROWS, action_page=False) == ("study_first_confirm", (360, 698))


def test_study_branch_other_reward_left_to_agent():
    detail = [box("スキルカードを選択してセレクトチェンジ", 559)]
    assert B.route_tap(None, TOP + detail + ROWS, action_page=False) is None


def test_trainer_line_after_lesson_taps_message():
    boxes = TOP + [box("+180", 184), box("ダンストレーナー", 928, 159), box("全力を出すのは我慢して、", 996, 229),
                   box("本戦で思いっきりかましてこい！", 1032, 274)]
    assert B.route_tap(None, boxes, action_page=False) == ("study_line", (360, 1000))


def test_study_page_without_options_or_line_stops():
    assert B.route_tap(None, TOP + [box("+180", 184)], action_page=False) is None


def test_card_change_page_still_stops():
    boxes = TOP + [box("チェンジで獲得するスキルカードを選んでください", 309)] + ROWS
    assert B.route_tap(None, boxes, action_page=False) is None


def test_hold_reason_on_choice_pages():
    assert B.hold_reason(["チェンジで獲得するスキルカードを選んでください"]).startswith("换卡阶段 1")
    assert B.hold_reason(["受け取る Pドリンクを", "選んでください"]).startswith("差し入れ 选饮料")
    assert B.hold_reason(["受け取るスキルカードを選んでください"]).startswith("差し入れ 选技能卡")


# 全局回放（两局实机 305 帧）里旧规则点了角落、现在必须停下的页面（OCR 原文，含误读）
@pytest.mark.parametrize("texts, reason", [
    (["残りターツ", "4", "Lst", "強気"], "考试中"),
    (["相談", "持っているスキルカードの強化や削除ができます。", "強化", "削除"], "相談 / 商店"),
    (["インターバル", "Pポイントで利用するものを選んでください"], "选择页"),
    (["交換確認", "Pポイントを使用して交換しますか？", "キャンセル", "交換"], "相談 / 商店"),
    (["Pドリンク詳細", "初星ホエイプロテイン", "キャンセル", "使う", "捨てる"], "饮料栏操作"),
    (["Vo.公開レッスン", "烏龍茶", "元気+7", "受け取る"], "领取选择"),
    (["チェンジするスキルカードを選択してください", "キャンセル", "チェンジ"], "选择页"),
    (["カスタマイズメニュー", "成長追加"], "轮间客制化"),
    (["H.I.F本戦", "ラウンド1結果", "現在順位", "再挑戦", "次へ"], "考试结果"),
    (["2.開始確認", "選択中の選抜試験メモリー"], "首页 / 开局"),
])
def test_hold_reason_global_replay_pages(texts, reason):
    assert B.hold_reason(texts).startswith(reason)


@pytest.mark.parametrize("texts", [
    ["授業", "立ち回りの基本＋を忍耐力+にチェンジ", "しました"],
    ["NOW LOADING...."],
    ["スター性獲得", "+225"],
    ["H.I.F本戦", "ラウンド1", "H.I.F応援棒（青）", "試験開始時、"],
    [],
])
def test_hold_reason_passive_pages_keep_tapping(texts):
    assert B.hold_reason(texts) is None


def _decide(texts, *, page=None, fallback=None):
    import numpy as np

    img = np.zeros((1280, 720, 3), np.uint8)
    boxes = [(t, 1.0, (100, 300, 200, 330)) for t in texts]
    return B.auto_decide(img, ocr=lambda _: boxes, quick=lambda _: page, fallback=lambda _: fallback, header=lambda _: None)


def test_auto_decide_holds_shop_misread_as_acquire():
    page = {"page": "ACQUIRE", "dispatch": False, "tap": (360, 1100), "fields": {}}
    with pytest.raises(B.HoldPage, match="相談"):
        _decide(["相談", "初星ホエイプロテイン", "強化や削除ができます"], page=page)


def test_auto_decide_taps_real_animation_and_corner():
    page = {"page": "ACQUIRE", "dispatch": False, "tap": (360, 1100), "fields": {}}
    assert _decide(["H.I.F応援棒（青）"], page=page) == ("animation", (360, 1100))
    assert _decide(["秦谷美鈴"]) == ("corner", (12, 12))


def test_auto_decide_fallback_page_holds():
    with pytest.raises(B.HoldPage, match="fallback"):
        _decide(["something"], fallback="SHOP")
