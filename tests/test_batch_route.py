"""tools/batch_hif.route_tap：VEIL 批量的固定路线（授業蓝、分支第一项、确认强气换卡）。用合成 OCR 框，不需要截图。"""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
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


def test_live_start_page_no_longer_holds():
    """横屏 LIVE 由 watch 自己处理（切全屏 / TAP TO START），「横画面で開始します」不再停；本戦開始 仍停。"""
    assert B.hold_reason(["横画面で開始します"]) is None
    assert B.hold_reason(["本戦開始"]) is not None


def test_ocr_gate_reuses_same_frame_and_blind_taps_while_moving():
    """watch 的 OCR 节流：同一画面不重读；画面在动、上一步是被动页时 1.5s 内不读直接点；停点后不盲点。"""
    import numpy as np

    t = [0.0]
    reads = []
    gate = B.OcrGate(lambda img: reads.append(1) or [], clock=lambda: t[0])
    frame = lambda v: np.full((1280, 720, 3), v, np.uint8)  # noqa: E731
    decide = lambda img, ocr: (ocr(img), ("corner", (12, 12)))[1]  # noqa: E731
    no_dialog = lambda img: None  # noqa: E731

    assert gate.decide(frame(10), decide, no_dialog) == ("corner", (12, 12)) and len(reads) == 1
    assert gate.decide(frame(10), decide, no_dialog) and len(reads) == 1  # 同一画面：沿用
    t[0] = 0.5
    assert gate.decide(frame(200), decide, no_dialog) == ("corner", (12, 12)) and len(reads) == 1  # 在动：盲点
    t[0] = 2.0
    gate.decide(frame(20), decide, no_dialog)
    assert len(reads) == 2  # 超过 BLIND_S：重读

    def hold(img, ocr):
        ocr(img)
        raise B.HoldPage("x")

    t[0] = 2.1
    assert gate.decide(frame(120), hold, no_dialog) == ("corner", (12, 12))  # 停点页在淡入：1.5s 内还会盲点一下
    t[0] = 3.6
    with pytest.raises(B.HoldPage):
        gate.decide(frame(130), hold, no_dialog)  # 到 BLIND_S 必读，认出停点
    t[0] = 3.7
    with pytest.raises(B.HoldPage):
        gate.decide(frame(250), hold, no_dialog)  # 停点之后画面在动也不盲点
    assert len(reads) == 4
    # 有弹窗不盲点
    gate.last = ("corner", (12, 12))
    t[0] = 2.3
    with pytest.raises(B.HoldPage):
        gate.decide(frame(5), hold, lambda img: (500, 540))


def test_open_same_frame_guard():
    """开局点之前重新截帧：画面在 OCR 期间变了（偶像页淡出成开始确认页）就不点。"""
    import numpy as np

    a = np.full((1280, 720, 3), 40, np.uint8)
    b = a.copy()
    assert B.same_frame(a, b)
    b[600:1280] = 200  # 下半屏换成了另一页
    assert not B.same_frame(a, b)
    assert not B.same_frame(a, None)


def _b(text, x, y):
    return (text, 1.0, (x - 60, y - 14, x + 60, y + 14))


START_PAGE = [_b("育成情報", 137, 1056), _b("プロデュース開始", 363, 1042), _b("B 32 → 12", 359, 1081), _b("サポート", 432, 636)]


def test_open_taps_production_start_on_clean_confirm_page(monkeypatch):
    monkeypatch.setattr(B, "AUTO_START", True)
    page, tap = B.flow_page(START_PAGE, "open", dialog=False)
    assert page == "start" and tap is not None and abs(tap[1] - 1042) < 30


@pytest.mark.parametrize("extra", ["別の端末で進行中のプロデュースがあります", "プロデュース再開", "ジュエルを消費"])
def test_open_never_starts_with_danger_or_dialog(extra, monkeypatch):
    monkeypatch.setattr(B, "AUTO_START", True)
    assert B.flow_page(START_PAGE + [_b(extra, 360, 600)], "open", dialog=False)[1] is None
    assert B.flow_page(START_PAGE, "open", dialog=True)[1] is None


def test_acquire_banner_over_receive_button_is_passive():
    """差し入れ领取后的获得演出：物品条 + 效果行盖在「受け取る」上，点角落；选择页本身照停。"""
    banner = [_b("初星スーパーソーダ", 364, 872), ("強気2段階目に変更", 1.0, (136, 917, 344, 943)),
              ("消費体力減少1ターン", 1.0, (136, 954, 365, 979)), _b("受け取る", 353, 1097)]
    assert B.acquire_banner(banner) and B.hold_reason([t for t, _, _ in banner]).startswith("领取选择")
    choice = [("受け取るPドリンクを選んでください。", 1.0, (149, 618, 562, 646)), ("初星水", 1.0, (195, 884, 247, 910)),
              _b("受け取る", 353, 1097)]
    assert not B.acquire_banner(choice)


def test_result_page_taps_next_once_but_retry_dialog_holds():
    page = [_b("ラウンド1結果", 117, 139), _b("現在順位", 71, 186), _b("再挑戦", 126, 1133), _b("→ 次へ", 335, 1134)]
    assert B.result_next(page, dialog=False) == (373, 1133)
    assert B.result_next(page, dialog=True) is None
    dialog = page + [_b("再挑戦確認", 140, 860), _b("プロデュース終了", 230, 1160)]
    assert B.result_next(dialog, dialog=False) is None


def test_memory_generate_page_holds():
    assert B.hold_reason(["MEMORY", "秦谷美鈴", "生成", "HIF", "初星アイドル"]).startswith("结算回忆生成")


def test_settle_taps_generate_on_memory_confirm():
    boxes = [_b("メモリー確定確認", 187, 902), _b("再生成前の内容でメモリーを生成します。", 282, 986),
             _b("キャンセル", 221, 1160), _b("生成する", 480, 1162)]
    page, tap = B.flow_page(boxes, "settle", dialog=True)
    assert page == "memory_confirm" and tap is not None and tap[0] > 360


def test_auto_start_only_on_repeat_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("HIRO_BATCH_AUTO_START", raising=False)
    monkeypatch.setenv("HIRO_BATCH_RULES", str(tmp_path / "missing.json"))  # 默认 repeat
    assert not B.start_allowed({"completed": []})  # 每批第一局：代理核对后手点
    assert B.start_allowed({"completed": [{"run": 1}]})
    monkeypatch.setattr(B, "AUTO_START", False)
    assert B.flow_page(START_PAGE, "open", dialog=False) == ("opening_confirmation", None) or \
        B.flow_page(START_PAGE, "open", dialog=False)[1] is None


def test_live_skip_always_taps_fixed_skip_then_confirms(monkeypatch):
    # 使用者 2026-11-07：进 LIVE 后所有点击都点 ⏭ 固定位置（屏幕 (0.9375, 0.10) → 1280x720 (1200,72)），直到确认框 → 「終了する」
    import batch_hif as B

    img = np.zeros((720, 1280, 3), np.uint8)
    now = [100.0]
    monkeypatch.setattr(B.time, "monotonic", lambda: now[0])
    live = {"started": 99.5}
    noop = lambda x, y: None  # noqa: E731
    assert B.live_skip_step(img, [], live, noop) is None  # 刚开始，等曲目页退场
    now[0] = 103.0
    assert B.live_skip_step(img, [], live, noop) == ("live_skip", (1200, 72))  # 没控制条也点 ⏭ 位置（第一下唤出）
    controls = [("00:20/01:32", 1.0, (36, 92, 186, 118))]
    assert B.live_skip_step(img, controls, live, noop) == ("live_skip", (1200, 72))
    for _ in range(6):
        assert B.live_skip_step(img, [], live, noop)[0] == "live_skip"  # 不限次数，直到退出
    dialog = [("ライブ終了確認", 1.0, (520, 430, 900, 460)), ("Pライブを終了しますか？", 1.0, (340, 520, 620, 550)),
              ("キャンセル", 1.0, (430, 660, 600, 700)), ("終了する", 1.0, (740, 660, 860, 700))]
    assert B.live_skip_step(img, dialog, live, noop) == ("live_end", (800, 680)) and not live.get("ended")
    assert B.live_skip_step(img, dialog, live, noop) == ("live_end", (800, 680))  # 框还在：再点
    assert B.live_skip_step(img, [], live, noop) is None and live["ended"]  # 框消失才算结束
    assert B.live_skip_step(img, dialog + [("ジュエル", 1.0, (0, 0, 10, 10))], {}, noop) is None


def test_commu_template_needs_ocr_bottom_bar():
    """清单 4：白字模板在授業分支页淡入帧上误中「剧情」，按 SKIP 点 (225,1200) = 饮料栏第 2 格。要 OCR 也认出底栏才点，且不盲点重复。"""
    page = {"page": "commu", "dispatch": None, "tap": (225, 1200), "fields": {}}
    img = np.zeros((1280, 720, 3), np.uint8)
    real = [("SKIP", 1.0, (190, 1185, 260, 1215)), ("ログ", 1.0, (320, 1185, 380, 1215)),
            ("AUTO", 1.0, (460, 1185, 530, 1215)), ("早送り", 1.0, (580, 1185, 660, 1215))]
    assert B.auto_decide(img, ocr=lambda _: real, quick=lambda _: page, fallback=lambda _: None,
                         header=lambda _: None) == ("commu", (225, 1200))
    assert "commu" not in B.OcrGate.PASSIVE
    fake = [("初星ホエイプロテイン", 1.0, (150, 1185, 300, 1215))]
    assert B.auto_decide(img, ocr=lambda _: fake, quick=lambda _: page, fallback=lambda _: None,
                         header=lambda _: None) == ("corner", (12, 12))
    assert B.animation_tap(img, ocr=lambda _: fake, blocked=lambda _: False, header=lambda _: None,
                           quick=lambda _: page, fallback=lambda _: None, corner=(12, 12)) is None


def test_pre_exam_ranking_page_taps_hint():
    """清单 5：考前「現在順位」页底部是「タップして次へ」（y≈1187），原来只认 y 1090–1180 的「次へ」按钮。"""
    page = [_b("現在順位", 71, 186), _b("タップして次へ", 360, 1187)]
    assert B.result_next(page, dialog=False) == (360, 1187)
    B.RANK_SEEN.update(at=None, last=0.0)
    assert B.result_next([_b("現在順位", 71, 186), _b("次へ", 360, 1260)], dialog=False) == "wait"


def test_ranking_page_waits_before_hold(monkeypatch):
    """「タップして次へ」淡入前先等 RANK_WAIT_S，之后才落到停点；一直在这页不会再变回 wait（不会误报 resume）。"""
    now = {"t": 100.0}
    monkeypatch.setattr(B.time, "monotonic", lambda: now["t"])
    B.RANK_SEEN.update(at=None, last=0.0)
    page = [_b("現在順位", 71, 186)]
    assert B.result_next(page, dialog=False) == "wait"
    for t in (102.0, 104.0):
        now["t"] = t
        assert B.result_next(page, dialog=False) == "wait"
    for t in (106.0, 108.0, 110.0, 112.0, 114.0, 116.0):
        now["t"] = t
        assert B.result_next(page, dialog=False) is None
    now["t"] = 140.0  # 离开 10s 后再来：重新计时
    assert B.result_next(page, dialog=False) == "wait"
    img = np.zeros((1280, 720, 3), np.uint8)
    now["t"] = 141.0
    assert B.auto_decide(img, ocr=lambda _: page, quick=lambda _: None, fallback=lambda _: None,
                         header=lambda _: None) is None


def test_deck_list_holds_even_with_tab_row_like_banner():
    """牌组一览底部「スキルカード(23)」像效果说明行，原来走 BANNER_PASS 点角落。"""
    img = np.zeros((1280, 720, 3), np.uint8)
    page = {"page": "DECK_LIST", "dispatch": None, "tap": (360, 1157), "fields": {}}
    boxes = [_b("所持スキルカード", 160, 55), ("スキルカード(23)", 1.0, (60, 1030, 300, 1065))]
    with pytest.raises(B.HoldPage, match="DECK_LIST"):
        B.auto_decide(img, ocr=lambda _: boxes, quick=lambda _: page, fallback=lambda _: None, header=lambda _: None)


def test_exam_briefing_waits_then_taps():
    bonus = [_b("優勝条件", 120, 630), _b("Vo", 300, 700)]
    assert B.briefing_step(bonus, dialog=False) == "wait"
    ready = bonus + [_b("タップして次へ", 360, 1200)]
    assert B.briefing_step(ready, dialog=False) == ("exam_briefing", (360, 300))
    assert B.briefing_step(ready, dialog=True) is None
    assert B.briefing_step(ready + [_b("残りターン", 80, 60)], dialog=False) is None
    img = np.zeros((1280, 720, 3), np.uint8)
    assert B.auto_decide(img, ocr=lambda _: bonus, quick=lambda _: None, fallback=lambda _: None, header=lambda _: None) is None


def test_acquired_drink_logged_once_and_not_after_choice(monkeypatch):
    """清单 10：事件 / 公開レッスン 直接给的饮料（只有获得演出）记进账本；同一演出多帧只记一次；选择页刚处理完的不记。"""
    import batch_ledger as L

    now = [100.0]
    monkeypatch.setattr(B.time, "monotonic", lambda: now[0])
    B.ACQUIRED.clear()
    B.CHOICE["at"] = -1e9
    ledger = {"drinks": ["センブリソーダ", None, None, None], "log": []}
    active, saved = {"ledger": ledger}, []
    page = {"page": "ACQUIRE", "dispatch": None, "tap": (12, 12), "fields": {"kind": "drink", "name": "ブーストエキス"}}
    img = np.zeros((1280, 720, 3), np.uint8)
    for _ in range(3):
        assert B.auto_decide(img, ocr=lambda _: [_b("ブーストエキス", 360, 880)], quick=lambda _: page,
                             fallback=lambda _: None, header=lambda _: None) == ("corner", (12, 12))
        now[0] += 0.4
    lines = B.drain_acquired({}, active, lambda: saved.append(1))
    assert [x["ledger"] for x in lines] == ["added"] and ledger["drinks"][1] == "ブーストエキス"
    assert B.drain_acquired({}, active, lambda: None) == []
    # 选择页刚处理完：那一次已由 prep / 代理记账
    now[0] += 30
    B.CHOICE["at"] = now[0] - 3
    ledger["log"].append({"t": "09:59:59", "op": "drink+", "slot": 2, "drink": "センブリソーダ"})  # prep 刚记过
    B.note_acquire("センブリソーダ")
    assert [x["ledger"] for x in B.drain_acquired({}, active, lambda: None)] == ["choice"]
    # 同一窗口里别的名字（1-f：换卡后支援卡事件给的 ティーパーティ+）照记
    B.note_acquire("ティーパーティ+", kind="card", card_id="p_card-00-sup-3_027")
    monkeypatch.setattr(L, "acquire_card", lambda *a, **k: "added")
    assert [x["ledger"] for x in B.drain_acquired({}, active, lambda: None)] == ["added"]
    # 别的进程刚记过同名（商店 / 选择页命令行）：账本按 log 时间去重
    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    assert L.acquire_drink(ledger, "初星湯", why="x") == "added"
    assert L.acquire_drink(ledger, "初星湯", why="x", now="10:00:20") == "dup"
    ledger["drinks"] = ["a", "b", "c", "d"]
    assert L.acquire_drink(ledger, "初星水", why="x", now="10:05:00") == "full" and ledger["log"][-1]["op"] == "drink?"


def test_live_exit_failure_message():
    assert "退出全屏失败" in B.live_fail_reason("exit", "failed") and "fn+shift+F" in B.live_fail_reason("exit", "failed")
    assert "进入全屏失败" in B.live_fail_reason("enter", "failed")


def test_ocr_gate_never_blind_taps_over_acquire_bar():
    """验证局（codex-20261107-1-b）：P 道具白送的饮料只有获得演出，盲点连点角落时一帧都没识别 → 账本漏 2 瓶。名称栏在就不盲点。"""
    t = [0.0]
    gate = B.OcrGate(lambda img: [], clock=lambda: t[0])
    decide = lambda img, ocr: (ocr(img), ("corner", (12, 12)))[1]  # noqa: E731
    gate.decide(np.full((1280, 720, 3), 10, np.uint8), decide, lambda img: None)
    popup = np.full((1280, 720, 3), 120, np.uint8)
    popup[870:891, 160:560] = 255  # 名称栏：白色横条 21 行
    assert B.acquire_bar(popup) and not B.acquire_bar(np.full((1280, 720, 3), 120, np.uint8))
    t[0] = 0.5
    assert gate.blind(popup, lambda img: None) is None


def test_exam_selected_card_text_holds_without_acquire_bar(monkeypatch):
    """出牌页选中一张牌：说明行落在 acquire_banner 那一带，但没有获得演出名称栏 → 停，不点角落（验证局 1-c）。"""
    img = np.zeros((1280, 720, 3), np.uint8)
    boxes = [_b("残りターン", 80, 60), ("温存に変更スキルカード使用数追加+1", 1.0, (60, 950, 400, 980))]
    kw = dict(ocr=lambda _: boxes, quick=lambda _: None, fallback=lambda _: None, header=lambda _: None)
    monkeypatch.setattr(B, "acquire_bar", lambda _img: False)
    with pytest.raises(B.HoldPage, match="考试中"):
        B.auto_decide(img, **kw)
    monkeypatch.setattr(B, "acquire_bar", lambda _img: True)
    assert B.auto_decide(img, **kw)[0] == "corner"


def test_live_end_tap_subtracts_top_white_bar(monkeypatch):
    """横屏帧顶部白条 35px：「終了する」按 OCR 帧坐标点时减去白条（点击按屏幕坐标）；⏭ 用屏幕固定坐标、不受白条影响。"""
    import batch_hif as B

    img = np.zeros((720, 1280, 3), np.uint8)
    img[:35] = 255
    assert B.top_bar(img) == 35 and B.top_bar(np.zeros((720, 1280, 3), np.uint8)) == 0
    monkeypatch.setattr(B.time, "monotonic", lambda: 200.0)
    assert B.live_skip_step(img, [], {"started": 100.0}, None) == ("live_skip", (1200, 72))
    dialog = [("ライブ終了確認", 1.0, (520, 430, 900, 460)), ("終了する", 1.0, (740, 660, 860, 700))]
    assert B.live_skip_step(img, dialog, {"started": 100.0}, None) == ("live_end", (800, 645))

def test_deck_sync_reconciles_ledger_from_deck_list(monkeypatch):
    """第二层：牌组一览整副读全 → 主数据卡名（带「+」）→ 账本对账；读不全不改账本。"""
    import batch_ledger as L
    import hiro.produce.deck_review as DR
    import hiro.state.catalog as C
    import hiro.state.knowledge as K

    monkeypatch.setattr(L, "_now", lambda: "10:00:00")
    names = {("a", 1): "巻き返し+", ("b", 1): "ティーパーティ+"}
    monkeypatch.setattr(C, "catalog", lambda: NS(card=lambda cid, up: {"name": names[(cid, up)]}))
    monkeypatch.setattr(K, "is_once", lambda cid: cid == "b")
    import hiro.vision.hif as H

    monkeypatch.setattr(H, "is_hif_exam", lambda img: False)
    # deck_sync 会把 DR.SAVE_DIR 设成 reports/live/deck-list；不还原的话后面 test_deck_review 的假黑帧都存进实机目录
    #（1-f 审计以为实机一览截到黑屏，其实是测试存的）
    monkeypatch.setattr(DR, "SAVE_DIR", None)
    deck = [{"card_id": "a", "upgrade": 1}, {"card_id": "b", "upgrade": 1}]
    monkeypatch.setattr(DR, "review_deck", lambda **kw: {"deck": deck, "deck_size": 2, "problems": [], "opened": True, "closed": True})
    led = L.new_ledger({"memory": "m", "cards": ["巻き返し+"], "once": [], "evidence": "x"})
    dev = NS(screenshot=lambda: None, click=None, swipe=None)
    out = B.deck_sync(dev, led, why="测试")
    assert out["ok"] and out["diff"] == ["补记 ティーパーティ+"] and "ティーパーティ+" in led["once"]
    monkeypatch.setattr(DR, "review_deck", lambda **kw: {"deck": None, "deck_size": None, "problems": ["没读全"], "opened": True, "closed": True})
    before = list(led["cards"])
    assert not B.deck_sync(dev, led, why="测试")["ok"] and led["cards"] == before
    monkeypatch.setattr(H, "is_hif_exam", lambda img: True)
    assert "考试页" in B.deck_sync(dev, led, why="测试")["why"]


# ---- 实机画面（tests/fixtures/walkthrough：使用者 2026-11-07 的 H.I.F 流程截图笔记）走一遍 auto_decide
# 上面的合成 OCR 用例覆盖同样的页面（授業三选一、分支第一项、选择页停点）；这里用真实整帧 OCR 再核一遍。
# 期望按笔记：授業 / 公開レッスン 走固定路线；选择、相談、商店、考试、再挑戦确认都停给代理；结果页点「次へ」不点「再挑戦」；
# 最終プロデュース評価 / プロデュース完了 点角落（笔记：live 结束后这一页 (12,12) 实测可行）。
WALK = Path(__file__).with_name("fixtures") / "walkthrough"


@pytest.mark.fixtures
@pytest.mark.parametrize(("name", "kind", "detail"), [
    ("04-lesson-three", "study_da", None),
    ("05-lesson-options", "study_first", (360, 698)),  # 最上面一项 = 強気（笔记）
    ("07-public-lesson", "public_vo", None),
    ("08-drink-select", "hold", "领取选择"),
    ("09-outing-supply", "hold", "行动页需要代理选择"),
    ("10-card-acquire", "hold", "差し入れ 选技能卡"),
    ("11-consult-action", "hold", "行动页需要代理选择"),
    ("12-consult-shop", "hold", "选择页"),
    ("14-interval-shop", "hold", "选择页"),
    ("15-r2-exam", "hold", "考试中"),
    ("13-r1-result", "result_next", (373, 1133)),
    ("16-r2-lost", "result_next", (373, 1133)),
    ("17-retry-confirm", "hold", "考试结果"),  # 绝不替使用者点「プロデュース終了」
    ("18-final-evaluation", "corner", (12, 12)),
    ("19-memory-generate", "hold", "结算回忆生成"),
    ("20-memory-result", "hold", "结算回忆"),
    ("21-produce-complete", "corner", (12, 12)),
])  # fmt: skip
def test_walkthrough_frames_auto_decide(name, kind, detail):
    import cv2

    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header
    from hiro.vision.quick import fallback_page, quick_page

    img = cv2.imread(str(WALK / f"{name}.png"))
    try:
        got = B.auto_decide(img, ocr=ocr_boxes, quick=quick_page, fallback=fallback_page, header=dialog_header)
    except B.HoldPage as e:
        assert kind == "hold" and str(e).startswith(detail), str(e)
        return
    assert got is not None and got[0] == kind, got
    if detail is not None:
        assert tuple(got[1]) == detail
    assert got[1] != (129, 1128)  # 再挑戦按钮


@pytest.mark.fixtures
@pytest.mark.parametrize(("name", "action", "kind", "tap"), [
    ("01-hif-top", "open", "main", (581, 898)),  # 本戦
    ("02-idol-select", "open", "remembered_idol", (335, 1087)),  # 次へ
    ("03-start-confirm", "open", "opening_confirmation", None),  # 開始確認：不自动点「プロデュース開始」
    ("18-final-evaluation", "settle", "result", (360, 1132)),  # TAPで次へ
    ("19-memory-generate", "settle", "generate", (363, 978)),
    ("20-memory-result", "settle", "regeneration_review", None),  # 再生成 / 次へ 交给代理
    ("21-produce-complete", "settle", "result", (351, 1159)),
])  # fmt: skip
def test_walkthrough_frames_flow_page(name, action, kind, tap):
    import cv2

    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header

    img = cv2.imread(str(WALK / f"{name}.png"))
    got = B.flow_page(ocr_boxes(img), action, dialog=dialog_header(img) is not None)
    assert got[0] == kind and (got[1] is None if tap is None else abs(got[1][0] - tap[0]) <= 15 and abs(got[1][1] - tap[1]) <= 15), got


def test_regenerate_needs_memory_page_context():
    assert B.hold_reason(["再生成"]) is None
    assert B.hold_reason(["メモリー", "再生成"]).startswith("结算回忆")
    assert B.hold_reason(["獲得可能スキルカード"]).startswith("结算回忆")


@pytest.mark.fixtures
def test_hif_top_hold_reason():
    import cv2

    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header
    from hiro.vision.quick import fallback_page, quick_page

    img = cv2.imread(str(WALK / "01-hif-top.png"))
    with pytest.raises(B.HoldPage) as e:
        B.auto_decide(img, ocr=ocr_boxes, quick=quick_page, fallback=fallback_page, header=dialog_header)
    assert str(e.value).startswith("首页")


@pytest.mark.fixtures
def test_home_bottom_bar_is_not_dialog():
    """2026-11-07 实机首页：底栏 y=1244–1280 被 dialog_header 认成弹窗标题条，open 把首页判成开局确认页。"""
    import cv2

    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header

    path = Path(__file__).parent / "fixtures" / "trace" / "home" / "home-bottom-bar.png"
    if not path.exists():
        pytest.skip("缺样例")
    img = cv2.imread(str(path))
    assert dialog_header(img) is None
    assert B.flow_page(ocr_boxes(img), "open", dialog=False)[0] == "home"


PHOTO_ERROR = ["エラー", "エラーによりフォトを保存できませんでした", "リトライしますか？",
               "※キャンセルすると撮影した画像を破棄し、テンプレー", "ト画像でメモリーを生成します", "Xキャンセル", "くリトライ"]


def test_photo_save_error_cancels_once_in_settle():
    """验证局 1-i 结算：照片保存失败框（正文含「破棄」= 拍下的照片）。settle 点左边キャンセル，其余动作仍当受保护页面。"""
    boxes = [(t, 1, b) for t, b in zip(PHOTO_ERROR, [(300, 520, 420, 560), (100, 640, 620, 670), (200, 690, 520, 720),
                                                     (60, 760, 680, 790), (60, 800, 500, 830), (120, 1145, 330, 1180),
                                                     (400, 1145, 580, 1180)])]  # fmt: skip
    kind, tap = B.flow_page(boxes, "settle", dialog=True)
    assert kind == "photo_error" and tap is not None and tap[0] < 360
    assert B.flow_page(boxes, "open", dialog=True)[0] in ("protected", "opening_confirmation")


def test_photo_error_with_danger_words_stays_protected():
    texts = PHOTO_ERROR + ["プロデュースデータを破棄"]
    assert not B.photo_save_error(texts)


# ---------------------------------------------------------------- debug：本战主动再挑戦攒 trace（使用者 2026-11-07）
RESULT_R1 = [_b("ラウンド1結果", 117, 139), _b("現在順位", 71, 186), _b("本日あと1回無料", 100, 1088), _b("再挑戦", 126, 1133),
             _b("→ 次へ", 335, 1134)]
RETRY_DIALOG = [_b("再挑戦確認", 140, 420), _b("再挑戦チケット", 300, 600), _b("所持 442 → 441", 360, 650),
                _b("キャンセル", 200, 860), _b("再挑戦する", 520, 860)]


@pytest.fixture
def debug_retry(monkeypatch, tmp_path):
    clock = {"t": 1000.0}
    monkeypatch.setattr(B, "DEBUG_XY_FILE", tmp_path / "xy.json")  # 学到的坐标不写进实机 reports/
    monkeypatch.setattr(B, "RETRY_FRAME_DIR", tmp_path / "frames")  # 敗退框存帧不写进 traces/
    monkeypatch.setitem(B.DEBUG_RETRY, "saved", None)
    monkeypatch.setitem(B.DEBUG_RETRY, "auto", False)
    monkeypatch.setitem(B.DEBUG_RETRY, "ending", None)
    monkeypatch.setitem(B.DEBUG_RETRY, "round", None)
    monkeypatch.setattr(B.time, "monotonic", lambda: clock["t"])
    monkeypatch.setitem(B.DEBUG_RETRY, "plan", {"1": 2, "2": 1})
    monkeypatch.setitem(B.DEBUG_RETRY, "used", {})
    monkeypatch.setitem(B.DEBUG_RETRY, "pending", None)
    return clock


def test_parse_debug_retries():
    assert B.parse_debug_retries("2,1") == {"1": 2, "2": 1}
    assert B.parse_debug_retries("off") is None and B.parse_debug_retries("0,0") is None
    for bad in ("2", "a,1", "4,0"):
        with pytest.raises(ValueError):
            B.parse_debug_retries(bad)


def test_debug_retry_taps_retry_then_confirm(debug_retry):
    from hiro.produce import hif_buttons as BTN

    assert B.debug_retry_step(RESULT_R1, dialog=False) == ("debug_retry", BTN.RESULT_RETRY)
    assert B.debug_retry_step(RESULT_R1, dialog=False) == "wait"  # 确认框还没出来：不点「次へ」
    assert B.debug_retry_step(RETRY_DIALOG, dialog=True) == ("debug_retry_confirm", (520, 860))
    assert B.debug_retry_step(RETRY_DIALOG, dialog=True) == "wait"  # 刚点过
    debug_retry["t"] += 3
    assert B.debug_retry_step(RETRY_DIALOG, dialog=True) == ("debug_retry_retap", (520, 860))  # 还在：再点，不计数


def test_debug_retry_respects_plan_and_round(debug_retry):
    B.DEBUG_RETRY["used"] = {"1": 2}
    assert B.debug_retry_step(RESULT_R1, dialog=False) is None  # R1 用满：照常「次へ」
    r2 = [_b("ラウンド2結果", 117, 139)] + RESULT_R1[1:]
    assert B.debug_retry_step(r2, dialog=False)[0] == "debug_retry"
    B.DEBUG_RETRY["plan"] = None
    B.DEBUG_RETRY["pending"] = None
    assert B.debug_retry_step(RESULT_R1, dialog=False) is None  # 没开 debug：不管


def test_debug_retry_never_spends_jewels(debug_retry):
    assert B.debug_retry_step(RETRY_DIALOG, dialog=True) is None  # 不是我们点出来的框
    B.debug_retry_step(RESULT_R1, dialog=False)
    with pytest.raises(B.HoldPage, match="ジュエル"):
        B.debug_retry_step(RETRY_DIALOG + [_b("ジュエル 30", 360, 700)], dialog=True)
    no_button = [b for b in RESULT_R1 if b[0] != "再挑戦"]
    B.DEBUG_RETRY["pending"] = None
    assert B.debug_retry_step(no_button, dialog=False) is None  # 没有再挑戦按钮：照常「次へ」


DEFEAT = [_b("ラウンド1結果", 117, 139), _b("再挑戦確認", 140, 860), _b("プロデュース終了", 233, 1210), _b("再挑戦", 533, 1210)]


def _frame(color):
    import numpy as np

    img = np.zeros((1280, 720, 3), np.uint8)
    img[1180:1240, 440:630] = color  # 「再挑戦」按钮一带
    return img


def test_debug_defeat_retries_until_limit_then_ends(debug_retry):
    """敗退：照样点「再挑戦」（使用者 2026-11-07），这一轮满 3 次后点「プロデュース終了」。"""
    B.debug_retry_step(RESULT_R1, dialog=False)  # 记下轮次
    B.DEBUG_RETRY["pending"] = None
    orange = _frame((40, 140, 250))
    assert B.debug_retry_step(DEFEAT, dialog=True, img=orange) == ("debug_defeat_retry", (533, 1210))
    assert B.debug_retry_step(DEFEAT, dialog=True, img=orange) == "wait"  # 刚点过
    B.DEBUG_RETRY["pending"] = None
    B.DEBUG_RETRY["used"] = {"1": 3}
    assert B.debug_retry_step(DEFEAT, dialog=True, img=orange) == ("debug_defeat_end", (233, 1210))


def test_debug_defeat_grey_retry_ends(debug_retry):
    """次数角标「あと0回」（免费 / 券用完）：点「プロデュース終了」；之后的确认框没有危险字样才点右侧。
    不按按钮颜色判：可点的「再挑戦」本来就是白底（walkthrough/17-retry-confirm.png），灰帧不再算用完。"""
    B.debug_retry_step(RESULT_R1, dialog=False)
    B.DEBUG_RETRY["pending"] = None
    assert B.debug_retry_step(DEFEAT, dialog=True, img=_frame((128, 128, 128)))[0] == "debug_defeat_retry"
    B.DEBUG_RETRY["pending"] = None
    debug_retry["t"] += 20
    assert B.debug_retry_step(DEFEAT + [_b("あと0回", 520, 1210)], dialog=True)[0] == "debug_defeat_end"
    debug_retry["t"] += 3
    confirm = [_b("プロデュース終了確認", 200, 420), _b("キャンセル", 200, 860), _b("終了する", 520, 860)]
    assert B.debug_retry_step(confirm, dialog=True) == ("debug_defeat_end_confirm", (520, 860))
    debug_retry["t"] += 3
    with pytest.raises(B.HoldPage):
        B.debug_retry_step(confirm + [_b("データを破棄", 360, 600)], dialog=True)


def test_debug_learned_xy_used_after_first_find(debug_retry):
    """确认框按钮第一次 OCR 找到后存坐标，之后这一帧没认出按钮文字也照存的坐标点。"""
    B.debug_retry_step(RESULT_R1, dialog=False)
    assert B.debug_retry_step(RETRY_DIALOG, dialog=True) == ("debug_retry_confirm", (520, 860))
    B.DEBUG_RETRY["pending"] = None
    debug_retry["t"] += 20
    B.debug_retry_step(RESULT_R1, dialog=False)
    blurred = [b for b in RETRY_DIALOG if b[0] != "再挑戦する"]
    assert B.debug_retry_step(blurred, dialog=True) == ("debug_retry_confirm", (520, 860))


def test_debug_retry_pending_expires(debug_retry):
    B.debug_retry_step(RESULT_R1, dialog=False)
    debug_retry["t"] += B.DEBUG_RETRY_WAIT_S + 1
    assert B.debug_retry_step(RESULT_R1, dialog=False)[0] == "debug_retry"  # 确认框一直没出：重新点（没计数）


def test_debug_retries_command_sets_and_clears_plan():
    from types import SimpleNamespace as NS

    state = {"total": 1, "preflight": {"idol": B.VEIL_IDOL}, "active": {"run": 1, "debug_retries_used": {"1": 1}}, "completed": []}
    out = B.change(state, NS(command="debug-retries", plan="2,1"))
    assert state["debug_retries"] == {"1": 2, "2": 1} and out["used"] == {"1": 1}
    B.change(state, NS(command="debug-retries", plan="off"))
    assert "debug_retries" not in state


def test_debug_retries_command_writes_state_file(tmp_path, monkeypatch, capsys):
    """codex-20261108-3-c：debug-retries 打印 ok 但文件没变（main 有返回值就跳过 save）。走 main 一遍，核对文件。"""
    import json

    monkeypatch.setattr(B, "STATE_DIR", tmp_path)
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"total": 3, "completed": [], "preflight": {"idol": B.VEIL_IDOL},
                                "active": {"run": 1, "phase": "playing"}, "debug_retries": {"1": 2, "2": 1}}), encoding="utf-8")
    monkeypatch.setattr(B.sys, "argv", ["batch_hif.py", "--batch", "t", "debug-retries", "2,0"])
    assert B.main() == 0
    assert json.loads(path.read_text(encoding="utf-8"))["debug_retries"] == {"1": 2, "2": 0}


def test_debug_retry_on_final_result_page(debug_retry):
    # codex-20261108-3 第 1 局：R2 结束的最终结果页没有「ラウンド2結果」（左上「優勝」+「現在順位」，分数旁带「+620,548」），
    # 原来认不出，计划的 1 次 R2 再挑戦没点
    from hiro.produce import hif_buttons as BTN

    final = [_b("優勝", 90, 100), _b("現在順位", 70, 186), _b("1,518,642", 330, 318), _b("+620,548", 490, 318)] + RESULT_R1[-2:]
    B.DEBUG_RETRY["used"] = {"1": 2}
    assert B.final_result_page(["".join(t.split()) for t, _, _ in final])
    assert B.debug_retry_step(final, dialog=False) == ("debug_retry", BTN.RESULT_RETRY)
    assert B.DEBUG_RETRY["round"] == "2"
    B.DEBUG_RETRY["pending"] = None
    B.DEBUG_RETRY["used"] = {"1": 2, "2": 1}
    assert B.debug_retry_step(final, dialog=False) is None  # R2 用满：照常「次へ」
    assert not B.final_result_page(["ラウンド1結果", "現在順位", "+1,000"])
    assert not B.final_result_page(["現在順位", "898,094"])  # 没有这一轮的「+分数」：不认


def test_ticket_confirm_with_end_warning_is_not_defeat(debug_retry):
    """codex-20261108-3-c 第 1 局 R2 最终结果页：点「再挑戦」后的用券确认框正文也有「プロデュース終了を選択した場合は失敗となります」，
    按钮是「キャンセル / 決定」。原来被当成敗退框、找不到「再挑戦 / プロデュース終了」按钮，一直 wait。现在照常点「決定」。"""
    final = [_b("優勝", 90, 100), _b("現在順位", 70, 186), _b("+620,548", 490, 318)] + RESULT_R1[-2:]
    B.DEBUG_RETRY["used"] = {"1": 2}
    assert B.debug_retry_step(final, dialog=False)[0] == "debug_retry"
    dialog = [_b("再挑戦確認", 140, 843), _b("再挑戦チケットを消費して再挑戦しますか？", 300, 950),
              _b("プロデュース終了を選択した場合は失敗となります", 330, 990), _b("所持 433 → 432", 360, 1060),
              _b("キャンセル", 220, 1161), _b("決定", 500, 1161)]
    assert B.debug_retry_step(dialog, dialog=True) == ("debug_retry_confirm", (500, 1161))
    # 真的敗退框（按钮「プロデュース終了 / 再挑戦」、没有「消費」）照旧走敗退分支
    B.DEBUG_RETRY["pending"] = None
    assert B.debug_retry_step(DEFEAT, dialog=True, img=_frame((40, 140, 250)))[0] == "debug_defeat_retry"


def test_defeat_white_retry_button_is_not_grey(debug_retry):
    """walkthrough/17-retry-confirm.png 实机 OCR：可点的「再挑戦」是白底灰字（饱和度 0），原来被判成变灰、直接点「プロデュース終了」。
    现在看角标：「本日あと1回無料」「あと3回」→ 点再挑戦；「本日あと0回無料」只是免费用完，券还在 → 也点。"""
    real = [_b("再挑戦確認", 132, 860), _b("再挑戦が可能ですが本当に終了しますか？", 288, 960),
            _b("プロデュース終了を選択した場合は失敗となります", 338, 993), _b("プロデュース", 240, 1138), _b("終了", 239, 1175),
            _b("本日あと1回無料", 519, 1083), _b("再挑戦", 531, 1157), _b("あと3回", 513, 1207)]
    B.debug_retry_step(RESULT_R1, dialog=False)
    B.DEBUG_RETRY["pending"] = None
    gray = _frame((222, 222, 222))
    assert B.debug_retry_step(real, dialog=True, img=gray) == ("debug_defeat_retry", (531, 1157))
    assert not B.retry_exhausted("本日あと0回無料再挑戦あと2回")
    assert B.retry_exhausted("再挑戦あと0回")
