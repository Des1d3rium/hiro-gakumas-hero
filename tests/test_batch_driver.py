"""watch 自动模式：停点 → 命令、意图落盘、重启边界、敗退再挑戦、回血 / 换饮料 / 商店留底。不连游戏。"""
import copy
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import batch_driver as D
import batch_rules as R
import batch_runtime as X

pytestmark = pytest.mark.synthetic

EXAM, INTERVAL, MEMORY = "考试中：ターン目", "轮间客制化：インターバル", "结算回忆：獲得可能スキルカード"


def state(**driver):
    s = {"total": 1, "completed": [], "preflight": {"idol": "i_card-skin-hmsz-3-016"},
         "active": {"run": 1, "phase": "playing", "ledger": {"memory": "verified"}}}
    if driver:
        s["active"]["driver"] = driver
    return s


def rules(**retry):
    r = copy.deepcopy(R.DEFAULTS)
    r["driver"]["enabled"] = True
    r["retry"].update(retry)
    return r


def run(s, reason, result, r=None):
    action = D.next_step(s, reason, r or rules())
    assert action is not None
    saved = []
    D.begin(s, action, lambda: saved.append(copy.deepcopy(s)))
    assert saved[-1]["active"]["driver"]["pending"] == action  # 动手前已落盘
    return action, D.finish(s, action, result, lambda: None)


# ---------------------------------------------------------------- 默认值
def test_defaults_keep_manual_workflow_and_veil_target():
    assert not R.DEFAULTS["driver"]["enabled"]
    assert R.DEFAULTS["interval"]["target_hp"] == 10  # 使用者 2026-11-08
    assert R.DEFAULTS["prep"]["auto_drink_replace"]  # 使用者 2026-11-08 打开
    # 使用者 2026-11-08：敗退默认自动再挑戦（免费 / 券），变灰才「プロデュース終了」
    assert R.DEFAULTS["retry"] == {"on_defeat": "auto", "tickets": True, "end_when_exhausted": True}
    assert D.next_step(state(), EXAM, R.DEFAULTS) is None  # 没开自动模式：照旧交给代理


# ---------------------------------------------------------------- 停点 → 命令
def test_full_run_by_holds_only():
    s = state()
    assert run(s, EXAM, {"ok": True, "rank": 2})[0]["round"] == 1
    assert run(s, EXAM, {"ok": True, "rank": 1})[0]["round"] == 1  # R1 再挑戦仍是 R1
    order = [run(s, INTERVAL, {"ok": True})[0]["command"] for _ in range(5)]
    assert order == ["interval-recover", "customize", "customize", "shop-auto", "interval-end"]
    assert run(s, EXAM, {"ok": True, "rank": 1, "round_score": 1500000})[0]["round"] == 2
    action = D.next_step(s, MEMORY, rules())
    assert action["command"] == "settle" and "R2 1500000 第1" in action["result"]


def test_unrouted_holds_go_to_agent():
    s = state()
    for reason in ("换卡阶段 1：…", "确认框：キャンセル", "首页 / 开局：ジュエル", "选择页：選んでください", MEMORY):
        assert D.next_step(s, reason, rules()) is None  # MEMORY：没考完 R2 不结算
    assert D.next_step(state(stage="r2"), INTERVAL, rules()) is None  # 结束过轮间又见轮间：对不上
    s = state()
    s["active"]["phase"] = "opening"
    assert D.next_step(s, EXAM, rules()) is None


def test_recover_can_be_turned_off():
    r = rules()
    r["interval"]["recover_enabled"] = False
    assert D.next_step(state(), INTERVAL, r)["command"] == "customize"


# ---------------------------------------------------------------- 意图落盘与重启
def test_money_command_failure_blocks_until_resolve():
    s = state(stage="interval", recovered=True, customized=[0, 1])
    action, ok = run(s, INTERVAL, {"ok": False, "why": "P 读不出"})
    assert not ok and s["active"]["driver"]["pending"] == action and s["active"]["driver"]["error"]
    assert D.next_step(s, INTERVAL, rules()) is None  # 不重做购买
    D.resolve(s["active"], True, "画面已买到 ブーストエキス，账本已补")
    assert s["active"]["driver"]["shopped"] and "pending" not in s["active"]["driver"]
    assert D.next_step(s, INTERVAL, rules())["command"] == "interval-end"


def test_crashed_money_command_is_not_repeated():
    s = state(stage="interval", pending={"command": "customize", "index": 0, "card": "インフルエンサー+", "run": 1})
    assert D.next_step(s, INTERVAL, rules()) is None
    D.resolve(s["active"], False, "客制化页没进去，账本无变动")
    assert D.next_step(s, INTERVAL, rules())["command"] == "interval-recover"
    with pytest.raises(D.DriverBlocked):
        D.resolve(s["active"], False, "再清一次")


def test_idempotent_exam_reruns_only_on_same_hold_and_is_bounded():
    s = state(stage="r2", pending={"command": "exam", "round": 2, "deck": None, "run": 1})
    assert D.next_step(s, INTERVAL, rules()) is None  # 画面不是考试：不重跑
    action, ok = run(s, EXAM, {"ok": False, "why": "ResumeBlocked"})
    assert action["round"] == 2 and not ok and "error" not in s["active"]["driver"]  # 第 2 次机会还在
    _, ok = run(s, EXAM, {"ok": False, "why": "ResumeBlocked"})
    assert not ok and s["active"]["driver"]["error"]  # 连续两次不行就交给代理
    assert D.next_step(s, EXAM, rules()) is None


def test_ledger_todo_is_not_success():
    s = state(stage="interval", recovered=True)
    _, ok = run(s, INTERVAL, {"ok": True, "ledger_todo": "同名两张没记到具体哪张"})
    assert not ok and D.next_step(s, INTERVAL, rules()) is None


def test_pending_drink_replace_blocks():
    s = state()
    s["active"]["ledger"]["pending_drink_replace"] = {"discard": 3}
    assert D.next_step(s, EXAM, rules()) is None


# ---------------------------------------------------------------- 运行层（不连设备的部分）
def test_argv_matches_agent_commands():
    assert X.argv("b", 2, {"command": "exam", "round": 1}) == ["--batch", "b", "exam", "2", "--round", "1"]
    assert X.argv("b", 2, {"command": "customize", "card": "巻き返し+", "item": "成長追加", "need_maxed": "制限削除"}) == [
        "--batch", "b", "customize", "2", "--card", "巻き返し+", "--item", "成長追加", "--guarded",
        "--need-maxed", "制限削除", "--where", "once=0"]
    assert X.argv("b", 2, {"command": "shop-auto", "max_refresh": 3, "reserve_p": 20})[-5:] == [
        "--max-refresh", "3", "--reserve-p", "20", "--guarded"]
    assert X.argv("b", 3, {"command": "memory-load", "memory": "veil"})[-2:] == ["--memory", "veil"]


def test_argv_is_accepted_by_batch_parser():
    import batch_hif as B
    parser = B.build_parser()
    for action in ({"command": "exam", "round": 2}, {"command": "interval-end"}, {"command": "interval-recover"},
                   {"command": "customize", "card": "巻き返し+", "item": "成長追加", "need_maxed": "制限削除"},
                   {"command": "shop-auto", "max_refresh": 3, "reserve_p": 0}, {"command": "settle", "result": "x"},
                   {"command": "playing"}, {"command": "memory-load", "memory": "veil"}):
        args = parser.parse_args(X.argv("b", 1, action))
        assert args.command == action["command"]
    assert parser.parse_args(["--batch", "b", "shop-auto", "1", "--guarded"]).driver_guarded is True
    assert parser.parse_args(["--batch", "b", "watch", "--auto"]).auto is True
    assert parser.parse_args(["--batch", "b", "auto-resolve", "1", "applied", "--note", "n"]).outcome == "applied"


@pytest.mark.parametrize("code,stdout,ok", [(0, '{"ok": true, "trust": {"trusted": true}}', True),
                                            (1, '{"ok": false, "why": "x"}', False), (0, '{"ok": false}', False),
                                            (1, "", False), (0, "批量育成停止\n{\"page\": \"opening_review\"}", True)])
def test_run_command_reads_last_json_line(code, stdout, ok):
    out = X.run_command("b", 1, {"command": "exam", "round": 1},
                        runner=lambda *a, **k: NS(returncode=code, stdout=stdout, stderr=""))
    assert out["ok"] is ok


def test_settle_must_be_recorded_and_next_run_needs_real_start():
    s = state()
    out = X.check_result(s, {"command": "settle", "run": 1}, {"ok": True, "page": "opening_review"})
    assert out["ok"] is False
    s["completed"] = [{"run": 1, "ledger": {"memory": "veil"}}]
    s["active"] = {"run": 2, "phase": "opening"}
    assert [a["command"] for a in X.next_run(s, {"ok": True, "page": "opening_review"}, rules())] == ["playing", "memory-load"]
    assert X.next_run(s, {"ok": True, "page": "opening_confirmation"}, rules()) == []  # 没点开始：交给代理
    assert X.next_run(s, {"ok": True, "page": "opening_review"}, R.DEFAULTS) == []


def test_shop_deck_sync_diff_is_failure():
    out = X.check_result(state(), {"command": "shop-auto"}, {"ok": True, "deck_sync": {"ok": True, "diff": {"x": 1}}})
    assert out["ok"] is False


def test_plan_hold_prechecks_money_commands_once():
    s = state(stage="interval")
    persist = lambda: None  # noqa: E731
    hold = lambda texts: "轮间客制化：インターバル" if "インターバル" in texts else None  # noqa: E731
    action = X.plan_hold(s, INTERVAL, rules(), persist, hold_reason=hold, shoot_texts=lambda: ["インターバル"])
    assert action["command"] == "interval-recover" and s["active"]["driver"]["pending"] == action
    s = state(stage="interval")
    blocked = X.plan_hold(s, INTERVAL, rules(), persist, hold_reason=hold, shoot_texts=lambda: ["インターバル", "ジュエル"])
    assert blocked["blocked"] and s["active"]["driver"]["error"] and "pending" not in s["active"]["driver"]
    s = state(stage="interval")
    moved = X.plan_hold(s, INTERVAL, rules(), persist, hold_reason=hold, shoot_texts=lambda: ["考试"])
    assert moved["blocked"]
    # 考试不是花钱命令：不额外 OCR
    assert X.plan_hold(state(), EXAM, rules(), persist, hold_reason=hold, shoot_texts=lambda: pytest.fail("ocr"))["command"] == "exam"


def test_customize_needs_unique_ledger_instance(monkeypatch):
    import batch_ledger as L
    s = state(stage="interval", recovered=True)
    monkeypatch.setattr(L, "find_instance", lambda *a, **k: {"name": "インフルエンサー+", "once": False})
    monkeypatch.setattr(L, "ensure_deck", lambda _: [{"name": "インフルエンサー+"}, {"name": "インフルエンサー+"}])
    hold = lambda texts: INTERVAL  # noqa: E731
    out = X.plan_hold(s, INTERVAL, rules(), lambda: None, hold_reason=hold, shoot_texts=lambda: ["インターバル"])
    assert out["blocked"] == "同名多张客制化需先核实目标"


def test_guard_checks_pause_only(monkeypatch):
    from hiro.loop import pause_flag
    taps = []
    device = X.GuardedDevice(NS(screenshot=lambda: pytest.fail("每下点击不再整屏截图 / OCR"), click=lambda *xy: taps.append(xy)))
    monkeypatch.setattr(pause_flag, "ENABLED", False)
    device.click(1, 2)
    monkeypatch.setattr(pause_flag, "ENABLED", True)
    monkeypatch.setattr(pause_flag, "reason", lambda: "录屏中断")
    with pytest.raises(D.DriverBlocked):
        device.click(3, 4)
    assert taps == [(1, 2)]


# ---------------------------------------------------------------- 敗退再挑戦（watch 的 debug_retry_step，自动模式分支）
def box(text, y=1100, x=500):
    return text, 1, (x - 30, y - 10, x + 30, y + 10)


DEFEAT = [box("再挑戦確認", 860), box("再挑戦が可能ですが本当に終了しますか？", 960), box("プロデュース", 1138, 240),
          box("終了", 1175, 240), box("再挑戦", 1157, 531)]


@pytest.fixture
def retry(monkeypatch, tmp_path):
    import batch_hif as B
    monkeypatch.setattr(B, "DEBUG_XY_FILE", tmp_path / "xy.json")
    monkeypatch.setattr(B, "RETRY_FRAME_DIR", tmp_path / "frames")
    monkeypatch.setitem(B.DEBUG_RETRY, "plan", None)
    monkeypatch.setitem(B.DEBUG_RETRY, "auto", True)
    monkeypatch.setitem(B.DEBUG_RETRY, "tickets", False)
    monkeypatch.setitem(B.DEBUG_RETRY, "auto_used", {})
    monkeypatch.setitem(B.DEBUG_RETRY, "pending", None)
    monkeypatch.setitem(B.DEBUG_RETRY, "ending", None)
    monkeypatch.setitem(B.DEBUG_RETRY, "round", "1")
    return B


def test_auto_defeat_uses_free_retry(retry):
    kind, tap = retry.debug_retry_step(DEFEAT + [box("本日あと1回無料", 1083)], True)
    assert kind == "auto_defeat_retry" and tap == (531, 1157)


def test_auto_defeat_ticket_needs_rule_and_never_ends(retry):
    with pytest.raises(retry.HoldPage, match="再挑戦チケット"):
        retry.debug_retry_step(DEFEAT, True)
    retry.DEBUG_RETRY["tickets"] = True
    assert retry.debug_retry_step(DEFEAT, True)[0] == "auto_defeat_retry"
    retry.DEBUG_RETRY.update(pending=None, auto_used={"1": 3})
    with pytest.raises(retry.HoldPage, match="不点「プロデュース終了」"):
        retry.debug_retry_step(DEFEAT + [box("本日あと1回無料", 1083)], True)
    with pytest.raises(retry.HoldPage):
        retry.debug_retry_step(DEFEAT + [box("ジュエル", 700)], True)


def test_auto_mode_result_page_still_taps_next(retry):
    assert retry.debug_retry_step([box("ラウンド1結果", 100), box("現在順位", 200), box("再挑戦", 1135, 200)], False) is None
    assert retry.DEBUG_RETRY["round"] == "1"


def test_auto_defeat_on_real_frame(retry):
    root = Path(__file__).resolve().parent / "fixtures" / "walkthrough" / "17-retry-confirm.png"
    if not root.exists():
        pytest.skip("缺截图样例 walkthrough/17-retry-confirm.png")
    import cv2
    from hiro.produce.ops import ocr_boxes
    img = cv2.imread(str(root))
    kind, tap = retry.debug_retry_step(ocr_boxes(img), True, img)
    assert kind == "auto_defeat_retry" and 480 <= tap[0] <= 580 and 1140 <= tap[1] <= 1175


# ---------------------------------------------------------------- 回血 / 换饮料 / 商店留底（沿用原实现）
@pytest.mark.parametrize("values", [(None, 36, 100, 10, 0), (3, 36, 9, 10, 0), (3, 36, 100, None, 0), (3, 36, 100, 10, 95)])
def test_recovery_requires_known_affordable_target(values):
    with pytest.raises(D.DriverBlocked):
        D.recovery_plan(*values)


def test_recovery_budget_and_cap():
    assert D.recovery_plan(3, 36, 100, 10, 40) == {"target_hp": 10, "cost": 40, "count": 4}
    assert D.recovery_plan(12, 36, 0, 10, 0)["cost"] == 0
    assert D.recovery_plan(5, 8, 20, 10, 0)["target_hp"] == 8


def test_replacement_is_strict_known_and_normalized():
    priority = ["soda", "boost", "whey", "low"]
    assert D.replacement(["soda", "boost", "whey", "low"], ["whey", "soda"], priority, priority[:3]) == {
        "pick": 1, "discard": 3, "incoming": "soda", "held": "low"}
    assert D.replacement(["soda"] * 4, ["soda"], priority, priority[:3]) is None
    assert D.replacement(["soda", None, "whey", "low"], ["soda"], priority, priority[:3]) is None
    assert D.replacement(["soda", "boost", "unknown1", "unknown2"], ["soda"], priority, priority[:3]) is None
    up = str.lower
    assert D.replacement(["SODA", "boost", "whey", "low"], ["Soda"], priority, priority[:3], norm=up)["discard"] == 3


def test_shop_reserve_prevents_purchase_and_refresh(monkeypatch):
    import batch_shop as S
    from hiro.produce import shop
    monkeypatch.setattr(S, "_page", lambda _: ("interval", None))
    monkeypatch.setattr(shop, "read_p", lambda *_: 100)
    monkeypatch.setattr(S, "buy", lambda *_: pytest.fail("reserve spent"))
    monkeypatch.setattr(S, "_executor", lambda *_: pytest.fail("reserve refreshed away"))
    monkeypatch.setattr(S, "_shelf", lambda *_: ({"interval": {"refresh": {"price": 20}}},
        [{"name": "ブーストエキス", "price": 100, "purchased": False, "index": 0}]))
    assert S.auto(None, free_slots=lambda: 1, on_buy=lambda _: None, reserve_p=10)["ok"]
    monkeypatch.setattr(S, "_shelf", lambda *_: ({"interval": {"refresh": {"price": 20}}}, []))
    assert S.auto(None, free_slots=lambda: 1, on_buy=lambda _: None, reserve_p=90)["ok"]


@pytest.mark.parametrize("actual_hp,actual_p,ok", [(10, 40, True), (8, 40, False), (10, 30, False), (None, 40, False)])
def test_recovery_verifies_hp_and_spending(monkeypatch, actual_hp, actual_p, ok):
    from hiro.produce import interval, shop
    from hiro.vision import pages, shop as vision_shop
    device = NS(screenshot=lambda: None, click=lambda *_: None)
    health = iter([(3, 36), None if actual_hp is None else (actual_hp, 36)])
    points = iter([80, actual_p])
    monkeypatch.setattr(interval, "on_interval", lambda *_: True)
    monkeypatch.setattr(interval, "read_hp", lambda *_: next(health))
    monkeypatch.setattr(shop, "read_p", lambda *_: next(points))
    monkeypatch.setattr(pages, "dialog_header", lambda _: None)
    monkeypatch.setattr(vision_shop, "read_interval_shelf", lambda _: {"complete": True, "interval": {"recover": {"available": True}}})
    calls = []
    monkeypatch.setattr(interval, "execute_interval", lambda action, fields, **kw: calls.append(action) or NS(ok=True))
    assert X.recover(device, rules())["ok"] is ok
    assert calls[0].args == {"service": "recover", "target_hp": 10}


@pytest.mark.parametrize("direct", [True, False])
def test_interval_end_requires_positive_r2_evidence(monkeypatch, direct):
    import batch_hif as B
    from hiro.produce import interval
    from hiro.vision import pages, hif
    frame, taps = {"value": "interval"}, []

    def click(*xy):
        taps.append(xy)
        frame["value"] = "r2" if direct else "unknown"
    device = NS(screenshot=lambda: frame["value"], click=click)
    monkeypatch.setattr(interval, "on_interval", lambda img, _: img == "interval")
    monkeypatch.setattr(pages, "dialog_header", lambda _: None)
    monkeypatch.setattr(hif, "is_hif_exam", lambda img: img == "r2")
    monkeypatch.setattr(hif, "read_hif_exam", lambda _: {"finals_round": 2})
    monkeypatch.setattr(pages, "classify_page", lambda _: None)
    clock = iter(range(20))
    monkeypatch.setattr(B.time, "time", lambda: next(clock))
    monkeypatch.setattr(B.time, "sleep", lambda _: None)
    assert B.interval_end(device, guarded=True)["ok"] is direct
    assert taps == [B.BTN.SHOP_END]


@pytest.mark.parametrize("verified", [True, False])
def test_drink_swap_integration_records_only_verified_bar(monkeypatch, verified):
    import numpy as np
    import batch_prep as P
    import batch_shop as S
    from hiro.produce import drink_swap
    before = ["センブリソーダ", "ブーストエキス", "初星ホエイプロテイン", "初星水"]
    after = before[:3] + ["センブリソーダ"]
    page = [box("受け取るPドリンクを選んでください", 300), box("受け取らない", 1000), box("受け取る", 1100)]
    names = iter(["初星水", "センブリソーダ", "初星水"])

    def ocr(img):
        return page if img.shape[0] == 1280 else [box(next(names), 10)]
    device = NS(screenshot=lambda: np.zeros((1280, 720, 3), dtype=np.uint8), click=lambda *_: None)
    state0 = state()
    state0["active"]["ledger"].update(drinks=before[:], log=[])
    monkeypatch.setattr(P.time, "sleep", lambda _: None)
    old_get = P.batch_rules.get
    monkeypatch.setattr(P.batch_rules, "get", lambda key, default=None: True if key == "prep.auto_drink_replace" else old_get(key, default))
    bars = iter([before, after if verified else before])
    monkeypatch.setattr(S, "read_bar", lambda _: [{"state": "drink", "name": n, "center": [i * 50, 1200]} for i, n in enumerate(next(bars))])
    saved, calls = [], []

    def swap(discard, pick, **kwargs):
        assert saved[-1]["active"]["ledger"]["pending_drink_replace"]
        calls.append((discard, pick))
        return NS(ok=True)
    monkeypatch.setattr(drink_swap, "discard_then_receive", swap)
    result = P.handle(device, ocr, None, state0, "test", 1, lambda: saved.append(copy.deepcopy(state0)))
    assert calls == [(3, 1)]
    ledger = state0["active"]["ledger"]
    assert ledger["drinks"] == (after if verified else before)
    assert bool(ledger.get("pending_drink_replace")) is not verified
    assert ("账本待补" in result) is not verified


def test_synthetic_case_replay():
    from replay_batch_driver import replay_cases
    results = replay_cases(Path(__file__).resolve().parents[1] / "examples/cases/batch-driver.json")
    assert len(results) == 4 and all(r["ok"] for r in results)


def test_retry_exhausted_reads_badge_not_colour():
    import batch_hif as B
    assert not B.retry_exhausted("本日あと1回無料再挑戦あと3回")
    assert not B.retry_exhausted("本日あと0回無料再挑戦あと2回")  # 免费用完、券还有
    assert B.retry_exhausted("再挑戦あと0回")


def test_zero_free_left_counts_as_ticket(retry):
    with pytest.raises(retry.HoldPage, match="再挑戦チケット"):
        retry.debug_retry_step(DEFEAT + [box("本日あと0回無料", 1083)], True)


def test_finish_after_final_settle_clears_active():
    """codex-20261108-1：settle 记完最后一局，批次完成、active 清空，finish 原来 AttributeError、watch 退出。"""
    s = state(stage="after_r2")
    action = D.next_step(s, MEMORY, rules())
    assert action["command"] == "settle"
    D.begin(s, action, lambda: None)
    s["active"] = None  # settle 自己把批次记完成
    assert D.finish(s, action, {"ok": True}, lambda: None) is True


def test_photo_pages_route_to_settle():
    """结算选回忆照片 / 保存确认：归到「结算回忆」，自动模式跑 settle（原来停在「选择页」「确认框」等代理）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import batch_hif as B

    photo = B.hold_reason(["このフォトをアルバムに保存", "メモリーにするフォトを選んでください", "端末保存", "次へ"])
    save = B.hold_reason(["確認", "選択したフォトをメモリーとして保存しますか？", "キャンセル", "決定"])
    assert photo.startswith("结算回忆") and save.startswith("结算回忆")
    s = state(stage="after_r2")
    assert D.next_step(s, photo, rules())["command"] == "settle"


def test_interval_end_without_dialog_then_exam_page_counts_as_done():
    """codex-20261108-1 / 1-b：interval-end 点了「終了」没等到确认框，游戏直接进 R2。看到考试页就按轮间已结束接着跑 R2。"""
    s = state(stage="interval", recovered=True, customized=[0, 1], shopped=True)
    action, ok = run(s, INTERVAL, {"ok": False, "why": "点了「終了」，5s 内没出确认框"})
    assert action["command"] == "interval-end" and not ok
    step = D.next_step(s, EXAM, rules())
    assert step == {"command": "exam", "round": 2, "deck": None}
    driver = s["active"]["driver"]
    assert driver["stage"] == "r2" and "pending" not in driver and driver["resolved"][-1]["applied"]
