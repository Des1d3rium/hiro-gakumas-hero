"""Durable progress and guarded OCR actions for agent-driven H.I.F batches."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "reports" / "batches"
ANIMATION = ("TAP", "TAPで次へ", "タップして次へ", "スター性獲得", "カスタムPアイテム獲得")
STOP = (
    "開始", "再開", "確認", "AP不足", "AP回復", "不足しています", "選択", "スキルカード",
    "残りターン", "ターン目", "合格条件", "優勝条件", "インターバル", "相談",
    "特別指導", "通信エラー", "日付変更", "NOWLOADING", "移動中",
)


QUICK_ANIMATIONS = {"commu", "GIFT", "ACQUIRE", "UPGRADE_RESULT", "CUSTOMIZE_RESULT", "DELETE_RESULT", "STAR_GAIN"}
QUICK_TRANSITIONS = {
    "message", "tap_hint", "custom_cutin", "select_bonus", "toppa", "custom_pitem", "custom_pitem_get", "pitem_note",
}
VEIL_IDOL = "i_card-skin-hmsz-3-016"
STUDY_DETAIL_TOP = 420  # 授業分支：展开的选项说明在这条线以下、第一行选项以上
STUDY_LINE_TAP = (360, 1000)  # 授業结束后的训练员台词框
# 角落 (12,12) 不点的页面：代理正在看牌组一览 / 考试前说明（要先登记牌组和检查点）。其余页面都点。
CORNER_HOLD_PAGES = {"DECK_LIST", "HIF_EXAM_BRIEFING"}
AUTO_ROUTE_STOP = (
    "開始確認", "再開する", "確認", "キャンセル", "破棄", "リタイア", "ジュエル",
    "通信エラー", "日付変更", "残りターン", "ターン目", "優勝条件", "合格条件",
    "選択してください", "受け取る", "チェンジ", "インターバル",
)


# 必须由代理处理的页面：整帧 OCR 命中就停下（watch 连续读到几次后退出），不点角落。
# 来自全局回放（两局实机 305 帧）：旧规则在考试、相談、受け取る、换卡阶段 2、客制化、确认框、首页上都点了角落。
HOLD_PATTERNS = (
    (r"チェンジで獲得するスキルカードを選んでください", "换卡阶段 1"),
    (r"受け取るPドリンクを選んでください", "差し入れ 选饮料"),
    (r"受け取るスキルカードを選んでください", "差し入れ 选技能卡"),
    (r"(?:選んで|選択して)ください", "选择页"),
    (r"ターン目|残.?ター|優勝条件|合格条件|所持スキルカード", "考试中"),
    (r"ラウンド\d結果|再挑戦|現在順位", "考试结果"),
    (r"強化や削除ができます|Pポイントと交換|Pポイントで利用|交換確認|交換する", "相談 / 商店"),
    (r"カスタマイズ|インターバル|特別指導", "轮间客制化"),
    (r"受け取(?:る|らない)$", "领取选择"),
    (r"Pドリンク詳細|Pドリンク所持上限|捨てる", "饮料栏操作"),
    (r"キャンセル|はい$|いいえ$|決定$", "确认框"),
    (r"獲得可能スキルカード|再生成", "结算回忆"),
    (r"開始確認|アイドル選択|ショップ|ミッション|ジュエル", "首页 / 开局"),
    (r"本戦開始|横画面", "考试前 / LIVE"),
)
HOLD_TEXTS = tuple(p for p, _ in HOLD_PATTERNS[:3])  # 兼容旧名
HOLD_CONFIRM = 3  # watch：同一停点连续读到几次才退出（约 1–2 秒）


class HoldPage(Exception):
    """watch 遇到必须由代理处理的页面：停下、不点角落。"""


def hold_reason(texts):
    """OCR 文本里有必须由代理处理的页面特征时返回「理由：命中文字」，否则 None。"""
    labels = ["".join(unicodedata.normalize("NFKC", t).split()) for t in texts]
    labels.append("".join(labels))  # 标题被 OCR 拆成两段时也能命中
    for pattern, reason in HOLD_PATTERNS:
        for label in labels:
            if m := re.search(pattern, label):
                return f"{reason}：{m.group(0)}"
    return None


def animation_tap(img, *, ocr, blocked, header, quick, fallback, corner):
    if img is None or img.shape[:2] != (1280, 720) or header(img) is not None:
        return None
    page = quick(img)
    if page is not None:
        allowed = page["page"] in QUICK_ANIMATIONS or (
            page["page"] == "TRANSITION" and page["fields"].get("kind") in QUICK_TRANSITIONS
        )
        if not allowed or page["dispatch"] or page["tap"] is None:
            return None
        tap = tuple(page["tap"])
    elif fallback(img) is not None:
        return None
    else:
        tap = corner

    boxes = ocr(img)
    labels = ["".join(unicodedata.normalize("NFKC", t).split()).upper() for t, _, _ in boxes]
    controls = []
    for label, (_, _, box) in zip(labels, boxes):
        if label in ANIMATION:
            continue
        # A recognized result has descriptions and a toast, which can use
        # the same words as the controls on the preceding choice screen.
        if page is not None:
            kind = page["page"]
            if 940 <= box[1] <= box[3] <= 1135 and (
                kind == "UPGRADE_RESULT" and label.endswith("を強化しました")
                or kind == "DELETE_RESULT" and label.endswith("を削除しました")
                or kind == "TRANSITION" and page["fields"].get("kind") == "message"
                and re.search(r"を.+にチェンジしま(?:した)?$", label)
            ):
                continue
            if kind == "ACQUIRE" and (
                re.fullmatch(r"(?:スキルカード|Pドリンク|Pアイテム)(?:を)?獲得(?:しました)?[!！]?", label)
                or label == "スキルカードを選択して強化"
                or re.fullmatch(r"SPレッスン終了時[、,].+", label)
            ):
                continue
        controls.append(label)
    if any(word in label for label in controls for word in STOP) or blocked(controls):
        return None
    if tap == corner and any(x1 < 48 and y1 < 48 for _, _, (x1, y1, _, _) in boxes):
        return None
    if page is None and not any(
        label in ANIMATION and (not label.startswith("TAP") or box[1] >= 1050)
        for label, (_, _, box) in zip(labels, boxes)
    ):
        return None
    return tap


def route_tap(img, boxes, *, action_page):
    """Pick the batch's fixed H.I.F route after the current page has been identified."""
    from hiro.vision.action import _options
    from hiro.vision.event import ROW_HALF, ROW_Y

    labels = [("".join(unicodedata.normalize("NFKC", t).split()), (b[1] + b[3]) / 2) for t, _, b in boxes]
    in_detail = lambda y: STUDY_DETAIL_TOP <= y < ROW_Y[0] - ROW_HALF  # noqa: E731
    for label, y in labels:
        # The action prompt is often read partially (e.g. "を選択してください"), so
        # accept any reading of it inside its own row rather than an exact match.
        if action_page and 810 <= y <= 905 and label.endswith("選択してください") and label in "行動を選択してください":
            continue
        # After the first tap, the study branch expands the option's reward above
        # the rows ("…強気関係のスキルカードにセレクトチェンジ"). That text is
        # checked below instead of stopping on "チェンジ".
        if not action_page and in_detail(y):
            continue
        if any(word in label for word in AUTO_ROUTE_STOP):
            return None
    if action_page:
        if not any("本戦まで" in label and y < 150 for label, y in labels):
            return None
        options = _options(img)
        targets = {(o.kind, o.attr): o.center for o in options}
        if any(o.kind == "open_lesson" for o in options):
            if tap := targets.get(("open_lesson", "vo")):
                return "public_vo", tap
        elif tap := targets.get(("class", "da")):
            return "study_da", tap
        return None
    if not any(label == "授業" and y < 150 for label, y in labels):
        return None
    rows = [
        [label for label, cy in labels if abs(cy - y) <= ROW_HALF and re.search(r"[ぁ-んァ-ン一-龯]", label)]
        for y in ROW_Y
    ]
    if not any(rows):
        # After the lesson: a trainer line under the result ("ダンストレーナー" +
        # one or two lines of speech), no options. Tap the message box to go on.
        texts = [(label, b) for label, (_, _, b) in zip((l for l, _ in labels), boxes)
                 if re.search(r"[ぁ-んァ-ン一-龯]", label)]
        speaker = any(b[0] < 120 and 900 <= (b[1] + b[3]) / 2 <= 960 for _, b in texts)
        speech = any(960 < (b[1] + b[3]) / 2 <= 1090 for _, b in texts)
        return ("study_line", STUDY_LINE_TAP) if speaker and speech else None
    if not rows[0] or not any(rows[1:]) or any(
        label.endswith("レッスン") for row in rows for label in row
    ):
        return None
    # Only text counts: the row's stamina badge ("-5") also sits in this band.
    detail = "".join(label for label, y in labels if in_detail(y) and re.search(r"[ぁ-んァ-ン一-龯]", label))
    if not detail:
        return "study_first", (360, ROW_Y[0])
    # Second tap confirms the expanded first option, but only when its reward
    # is the route's 強気 card change; anything else is left to the agent.
    if "チェンジ" in detail and "強気" in detail:
        return "study_first_confirm", (360, ROW_Y[0])
    return None


def auto_decide(img, *, ocr, quick, fallback, header):
    """watch / VEIL burst 的一步（纯判定，不点）：返回 (kind, tap)；该停下交给代理时抛 HoldPage；
    其余（帧不对、加载中）返回 None。

    (12,12) 在绝大多数页面无害，所以除了选择页、牌组一览、考试前说明和 fallback 认出的页面，其余都点角落。"""
    from hiro.loop.advance import ADVANCE_TAP

    if img is None or img.shape[:2] != (1280, 720):
        return None
    dialog = header(img) is not None
    page = quick(img)
    fb = fallback(img) if page is None and not dialog else None
    boxes = None
    if page is None and not dialog and fb in {None, "ACTION_SELECT"}:
        # Only read the top strip to identify a study branch. The
        # resident corner tap does not depend on full-frame OCR.
        study = fb is None and any(t.strip() == "授業" for t, _, _ in ocr(img[:150, :220]))
        if fb == "ACTION_SELECT" or study:
            boxes = ocr(img)
            if result := route_tap(img, boxes, action_page=fb == "ACTION_SELECT"):
                return result
            if fb == "ACTION_SELECT":
                raise HoldPage(action_hold(boxes))
            # Other 授業 pages (card change etc.) fall through to the checks below.
    # 整帧读一次：选择页、考试、商店、确认框等都停下交给代理。快识别认成动画的页面也读：
    # 商店 / 轮间选中饮料时会被认成 ACQUIRE，轮间菜单会被认成 CUSTOMIZE_RESULT（全局回放）。
    if reason := hold_reason([t for t, _, _ in (boxes if boxes is not None else ocr(img))]):
        raise HoldPage(reason)
    if fb is not None:
        raise HoldPage(f"fallback 页面 {fb}")
    if page is not None and (page["dispatch"] or page["page"] in CORNER_HOLD_PAGES):
        raise HoldPage(f"页面 {page['page']}")
    if page is not None and not dialog:
        kind = page["fields"].get("kind")
        if page["page"] in QUICK_ANIMATIONS or (page["page"] == "TRANSITION" and kind in QUICK_TRANSITIONS):
            tap = tuple(page["tap"])
            if tap != ADVANCE_TAP:
                return "animation", tap
    return "corner", ADVANCE_TAP


def action_hold(boxes):
    """行动页没走固定路线（相談 / 休む / 最后一天等）：说清楚有哪些按钮。"""
    names = [t for t, _, b in boxes if b[1] >= 900 and re.search(r"[ぁ-んァ-ン一-龯]", t)]
    return "行动页需要代理选择：" + "/".join(names[:6])


LAST_CHOICE: dict = {}


def flow_page(boxes, action, *, dialog, keep_memory=False):
    """Return a guarded action from OCR on the current portrait frame."""
    from hiro.produce.ops import button_text, find_text

    boxes = [(unicodedata.normalize("NFKC", t).replace(" ", ""), c, b) for t, c, b in boxes]
    texts = [t for t, _, _ in boxes]

    def has(*words):
        return any(word in t for t in texts for word in words)

    def button(*words, region=(0, 1070, 720, 1230)):
        matches = [b for b in boxes if any(button_text(b[0], w) for w in words)]
        return find_text(matches, [b[0] for b in matches], region=region, exact=True)

    if has("破棄", "リタイア", "通信エラー", "日付変更", "タイトルへ"):
        return "protected", None

    home = (not dialog and button("ホーム", region=(250, 1165, 470, 1279))
            and button("プロデュース", region=(180, 840, 545, 1090))
            and has("コンテスト", "ミッション"))
    if home:
        return "home", button("プロデュース", region=(180, 840, 545, 1090)) if action == "open" else None

    if action == "open":
        if has("AP不足", "APが不足") and has("回復する"):
            return "ap_shortage", None
        if dialog or has("開始確認", "開始する", "プロデュース再開", "再開する", "ジュエル", "購入"):
            return "opening_confirmation", None
        if has("Hatsuboshi", "HATSUBOSHI", "H.I.F", "選抜"):
            if tap := button("本戦", region=(350, 790, 710, 1030)):
                return "main", tap
        if has("アイドル選択"):
            if tap := button("次へ"):
                return "remembered_idol", tap
        return "opening_review", None

    if has("開始確認", "開始する", "プロデュース再開", "再開する", "本戦へ"):
        return "protected", None

    import batch_memory

    if batch_memory.is_choice(texts):
        # 再生成后「再生成前 / 再生成後」二选一：按交接提示词的回忆优先级挑；认不出就停下交代理
        LAST_CHOICE.clear()
        LAST_CHOICE.update(batch_memory.choose(boxes))
        return "memory_choice", LAST_CHOICE.get("tap")

    if has("獲得イベントPt") and has("イベント報酬進捗"):
        return "event_result", button("次へ")
    if has("プロデュースで以下が解放されました") and has("PLV報酬一覧"):
        return "level_up", button("X", "×", "閉じる", region=(280, 1120, 440, 1210))
    if dialog or has("確認"):
        if has("メモリーとして保存しますか") and not has("ジュエル", "購入"):
            return "save_photo", button("決定")
        if has("再生成"):
            free = (any(re.search(r"無料.*再生成|再生成.*無料", t) for t in texts)
                    or has("再生成回数を", "再生成可能回数を"))
            ticket = has("メモリー再生成チケット") and any(
                re.search(r"所持(?:数|枚数)?[:：]?[1-9][0-9,]*", t) for t in texts
            )
            if has("ジュエル", "有償", "購入", "課金", "不足") or not (free or ticket):
                return "regeneration_review", None
            return "confirm_regeneration", button("再生成", "再生成する", "決定", "OK", region=(360, 740, 710, 1220))
        if has("フォロー確認"):
            return "skip_follow", button("キャンセル")
        if has("受取完了"):
            return "reward", button("閉じる")
        return "settlement_review", None

    if has("メモリーにするフォト"):
        return "photo", button("次へ")
    if has("MEMORY") and not has("ジュエル", "購入"):
        if tap := button("生成", region=(240, 880, 480, 1050)):
            return "generate", tap
    if has("獲得可能スキルカード", "アビリティ") and has("プロデュース履歴"):
        reroll = button("再生成", region=(0, 1070, 245, 1230))
        counts = [int(m.group(1)) for t, _, b in boxes
                  if b[0] < 250 and 1040 <= b[1] <= 1150
                  if (m := re.fullmatch(r"あと([0-9]+)回可能", t))]
        if reroll and counts != [0] and not keep_memory:
            if len(counts) == 1 and counts[0] > 0 and not has("ジュエル", "購入", "有償"):
                return "regenerate", reroll
            return "regeneration_review", None
        return "memory_done", button("次へ")
    if has("最終プロデュース評価", "プロデュース履歴", "プロデュース完了", "イベント報酬進捗"):
        return "result", button("次へ", "完了する", "閉じる")
    return "settlement_review", None


def device_action(action, *, watch_tick=None, auto_route=False):
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
    from hiro.device import connect
    from hiro.loop import ap_refill, pause_flag
    from hiro.loop.advance import ADVANCE_EVERY, ADVANCE_TAP, blocked
    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header
    from hiro.vision.quick import fallback_page, quick_page

    if watch_tick is None:
        pause_flag.wait_until_recording()
    device = connect("maatools")
    try:
        def shoot():
            if watch_tick is None:
                pause_flag.wait_until_recording()
            return device.screenshot()

        def click(x, y):
            if pause_flag.ENABLED and pause_flag.reason() is not None:
                raise ValueError("暂停旗标已出现，重新定位后再操作")
            device.click(x, y)

        if action == "refill":
            # Only called from the actual AP shortage dialog, before starting.
            return ap_refill.refill(shoot, click, ocr_boxes, log=lambda msg: print(msg, file=sys.stderr))
        if action in {"settle", "open"}:
            import batch_memory

            last = None
            unread = 0
            refilled = False
            memories: list[dict] = []  # 结算里看到的每一份回忆的评估（按交接提示词优先级）
            seen_choice = False
            for _ in range(60):
                img = shoot()
                if img is None or img.shape[:2] != (1280, 720):
                    return {"page": "unsupported_frame", "memories": memories}
                boxes = ocr_boxes(img)
                texts = [t for t, _, _ in boxes]
                keep_memory = False
                if any("獲得可能スキルカード" in t for t in texts):
                    ev = batch_memory.evaluate(texts)
                    if not memories or memories[-1] != ev:
                        memories.append(ev)
                    # 命中优先项、又没见过「再生成前 / 後」挑选页（不确定再生成会不会直接替换）：不再再生成，保住命中的这份
                    keep_memory = bool(ev["hit"]) and not seen_choice
                page, tap = flow_page(boxes, action, dialog=dialog_header(img) is not None, keep_memory=keep_memory)
                if page == "memory_choice":
                    seen_choice = True
                    if tap is None:
                        return {"page": page, "choice": dict(LAST_CHOICE), "memories": memories}
                if page == "ap_shortage" and not refilled:
                    result = ap_refill.refill(shoot, click, ocr_boxes, log=lambda msg: print(msg, file=sys.stderr))
                    if not result["ok"]:
                        return {"page": page, **result}
                    refilled = True
                    last = None
                    time.sleep(0.65)
                    continue
                if tap is None and page == "settlement_review":
                    tap = animation_tap(img, ocr=lambda _: boxes, blocked=blocked, header=dialog_header,
                                        quick=quick_page, fallback=fallback_page, corner=ADVANCE_TAP)
                if tap is None:
                    unread += 1
                    if (last is None and not refilled) or page not in {"settlement_review", "opening_review"} or unread >= 5:
                        return {"page": page, "memories": memories}
                else:
                    unread = 0
                    identity = tuple(t for t, _, _ in boxes if page != "regenerate" or "回可能" in t)
                    signature = (page, identity)
                    if signature != last or page == "settlement_review":
                        click(*tap)
                        last = signature
                time.sleep(0.65)
            return {"page": "timeout", "memories": memories}
        def advance():
            img = shoot()
            if auto_route:
                result = auto_decide(img, ocr=ocr_boxes, quick=quick_page, fallback=fallback_page, header=dialog_header)
                if result is not None:
                    click(*result[1])
                return result
            if img is None or img.shape[:2] != (1280, 720):
                return None
            dialog = dialog_header(img) is not None
            page = quick_page(img)
            fallback = fallback_page(img) if page is None and not dialog else None
            tap = animation_tap(
                img, ocr=ocr_boxes, blocked=blocked,
                header=lambda _: dialog, quick=lambda _: page, fallback=lambda _: fallback, corner=ADVANCE_TAP,
            )
            if tap is not None:
                click(*tap)
                return ("corner" if tap == ADVANCE_TAP else "animation"), tap
            return None

        if watch_tick is not None:
            while watch_tick(advance):
                time.sleep(ADVANCE_EVERY)
            return None
        actions = []
        for _ in range(8):
            try:
                result = advance()
            except HoldPage as hold:
                return {"taps": len(actions), "actions": actions, "hold": str(hold)}
            if result is None:
                break
            actions.append(result[0])
            time.sleep(ADVANCE_EVERY)
        return {"taps": len(actions), "actions": actions}
    finally:
        device.stop()


def save(path, state):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def change(state, args, *, persist=None):
    cmd = args.command
    if cmd == "preflight":
        if state["active"] or state["completed"] or state["preflight"]:
            raise ValueError("首检只能在第一局开始前记录一次")
        if args.ap < 0 or not all(v.strip() for v in (args.idol, args.memory)):
            raise ValueError("必须记录实测 AP、偶像和回忆")
        state["preflight"] = {"frame": "MaaTools 1717", "ap": args.ap, "skip": "verified",
                              "idol": args.idol, "memory": args.memory}
        return
    if not state["preflight"]:
        raise ValueError("先完成并记录首检")
    active = state["active"]
    if cmd == "deck-base":
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
        from hiro.produce.veil_exam import validate_deck

        if state["preflight"]["idol"] != VEIL_IDOL:
            raise ValueError("基准牌组只用于 [VEIL] 秦谷美玲")
        base = json.loads(args.deck.read_text(encoding="utf-8"))
        validate_deck(base)
        if state.get("veil_base_deck") not in (None, base):
            raise ValueError("此批次已有不同的基准牌组，请为新编成创建新批次")
        state["veil_base_deck"] = base
        return
    if cmd == "memory-save":
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa"), str(ROOT / "tools")]
        import batch_ledger as L

        deck = json.loads(args.deck.read_text(encoding="utf-8"))
        if state["preflight"]["idol"] == VEIL_IDOL:
            from hiro.produce.veil_exam import validate_deck

            validate_deck(deck)
        path = L.save_memory(args.memory, deck, state["preflight"]["idol"], overwrite=args.overwrite)
        return {"ok": True, "memory": args.memory, "file": str(path), "size": len(deck["cards"])}
    if cmd == "begin":
        if active or len(state["completed"]) == state["total"]:
            raise ValueError("已有未结算局，或已达到总次数")
        state["active"] = {"run": len(state["completed"]) + 1, "phase": "opening"}
        return
    if cmd in {"complete", "settle"}:
        previous = next((r for r in state["completed"] if r["run"] == args.run), None)
        if previous is not None:
            if previous["result"] == args.result:
                if cmd == "settle" and active and active["run"] == args.run + 1 and active["phase"] == "opening":
                    return device_action("open")
                return
            raise ValueError("该局已记账，结果不一致")
    if not active or active["run"] != args.run:
        raise ValueError("局号与当前未结算局不符")
    if cmd == "deck-change":
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
        from hiro.produce.veil_exam import apply_deck_change

        base = state.get("veil_base_deck")
        if base is None:
            raise ValueError("先登记基准牌组")
        change = json.loads(args.deck.read_text(encoding="utf-8"))
        apply_deck_change(base, change)
        existing = active.get("veil_deck_change")
        if existing is not None and any(existing.get(key) != change.get(key) for key in ("remove", "add", "add_once")):
            raise ValueError("本局已登记不同的变卡，请核对并提交该轮完整清单")
        active["veil_deck_change"] = change
    elif cmd in {"memory-load", "ledger", "ledger-edit"}:
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa"), str(ROOT / "tools")]
        import batch_ledger as L

        if cmd == "memory-load":
            if active.get("ledger") is not None and not args.reload:
                raise ValueError("本局已有账本；确认要丢掉已记的变动时加 --reload")
            drinks = [d.strip() or None for d in args.drinks.split(",")] if args.drinks else None
            active["ledger"] = L.new_ledger(L.load_memory(args.memory), drinks)
            persist()
            return {"ok": True, **L.summary(active["ledger"])}
        ledger = active.get("ledger")
        if ledger is None:
            raise ValueError("本局还没有账本，先 memory-load")
        if cmd == "ledger-edit":
            why = args.why or "代理手动记账"
            if args.add:
                L.add_card(ledger, args.add, args.once, why=why)
            if args.remove:
                L.remove_card(ledger, args.remove, why=why)
            if args.change:
                L.change_card(ledger, args.change[0], args.change[1], args.once, why=why)
            if args.upgrade:
                L.upgrade_card(ledger, args.upgrade, why=why)
            if args.drink_remove:  # 先去掉再收：栏满时换一瓶
                L.drink_remove(ledger, args.drink_remove - 1, why=why)
            if args.drink_add:
                L.drink_add(ledger, args.drink_add, why=why)
            if args.drink_set is not None:
                L.drink_set(ledger, [d.strip() or None for d in args.drink_set.split(",")], why=why)
            if args.rebound_repeatable is not None:
                ledger["rebound_repeatable"] = args.rebound_repeatable == "true"
                L._log(ledger, "rebound", repeatable=ledger["rebound_repeatable"], why=why)
            persist()
        out = {"ok": True, **L.summary(ledger)}
        if cmd == "ledger" and args.read_drinks:
            import batch_shop
            from hiro.device import connect

            device = connect("maatools")
            try:
                out["drink_mismatch"] = L.compare_drinks(ledger, batch_shop.read_bar(device))
            finally:
                device.stop()
        if cmd == "ledger" and args.log:
            out["log"] = ledger["log"]
        return out
    elif cmd in {"change-scan", "change-pick", "shop-scan", "shop-buy", "shop-refresh", "shop-auto", "drink-drop", "drink-overflow", "customize", "memory-abilities"}:
        if active["phase"] != "playing":
            raise ValueError(f"{cmd} 只允许在 playing 阶段使用")
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa"), str(ROOT / "tools")]
        import batch_change
        import batch_ledger as L
        import batch_shop
        from hiro.device import connect

        ledger = active.get("ledger")
        device = connect("maatools")
        try:
            if cmd == "change-scan":
                return batch_change.scan(device, args.batch, args.run)
            if cmd == "shop-scan":
                return batch_shop.scan(device)
            if cmd == "memory-abilities":
                from hiro.vision.status import read_memory_abilities

                read = L.static_abilities(read_memory_abilities(device.screenshot()))
                if not read:
                    return {"ok": False, "why": "状态栏没读到回忆能力（要在本战考试开局、状态栏可见时读）"}
                out = {"ok": True, "abilities": read}
                if ledger is not None:
                    ledger["abilities"] = read
                    L._log(ledger, "abilities", abilities=[a["card"] for a in read], why="memory-abilities 开局状态栏")
                    _, out["saved"] = L.save_abilities(ledger["memory"], read, overwrite=args.overwrite)
                    persist()
                return out
            if cmd == "customize":
                import batch_customize

                out = batch_customize.customize(device, args.card, args.item, need_maxed=args.need_maxed)
                if out["ok"] and ledger is not None:
                    L._log(ledger, "customize", card=args.card, item=out["item"])
                    if "制限削除" in out["item"] and L.norm(args.card).rstrip("+") == "巻き返し":
                        ledger["rebound_repeatable"] = True
                    persist()
                return out
            if cmd == "shop-refresh":
                return batch_shop.refresh(device)
            if cmd == "shop-auto":
                def on_buy(name):
                    if ledger is not None:
                        L.drink_add(ledger, name, why="shop-auto 买高价值饮料")
                        persist()

                free = (lambda: ledger["drinks"].count(None)) if ledger is not None else (lambda: 4)
                return batch_shop.auto(device, free_slots=free, on_buy=on_buy, max_refresh=args.max_refresh)
            if cmd == "shop-buy":
                if ledger is not None and None not in ledger["drinks"]:
                    raise ValueError(f"账本饮料栏已满（{ledger['drinks']}），先 drink-drop 再买")
                out = batch_shop.buy(device, args.index)
                if out["ok"] and ledger is not None:
                    out["slot"] = L.drink_add(ledger, out["drink"], why=f"{out['page']} 买第 {args.index} 格") + 1
                    persist()
                return out
            if cmd == "drink-drop":
                out = batch_shop.drop(device, args.slot - 1)
                if out["ok"] and ledger is not None:
                    L.drink_remove(ledger, args.slot - 1, why=f"丢掉（画面 {out['drink']}）")
                    persist()
                return out
            if cmd == "drink-overflow":
                out = batch_shop.overflow(device, args.discard - 1)
                if out["ok"] and ledger is not None:
                    L.drink_remove(ledger, args.discard - 1, why=f"溢出页丢掉（画面 {out['dropped']}）")
                    for name in out["incoming"]:
                        if name:
                            L.drink_add(ledger, name, why="溢出页收下")
                        else:
                            out["ledger_todo"] = "新饮料没认出，用 ledger-edit --drink-add 补记"
                    persist()
                return out
            out = batch_change.pick(device, args.batch, args.run, col=args.col, row=args.row, commit=args.commit,
                                    scan_file=args.scan)  # fmt: skip
            if ledger is not None and out.get("ok"):
                if out.get("stage") == 1:
                    ledger["pending_add"] = {"name": out["picked"]["name"], "upgrade": out["picked"].get("upgrade"),
                                             "once": out["picked"]["once"]}  # fmt: skip
                    persist()
                elif "changed" in out:
                    got, pending = out["changed"], ledger.get("pending_add")
                    if not got.get("name") or not pending or not pending.get("name"):
                        out["ledger_todo"] = "换出或换入的卡没认出，用 ledger-edit --change 旧 新 --once/--not-once 补记"
                    else:
                        L.change_card(ledger, L.card_name(got["name"], got.get("upgrade")),
                                      L.card_name(pending["name"], pending.get("upgrade")), pending["once"], why="授業换卡")
                        ledger["pending_add"] = None
                    persist()
            return out
        finally:
            device.stop()
    elif cmd == "playing":
        active["phase"] = "playing"
    elif cmd == "checkpoint":
        active["checkpoint"] = args.note
    elif cmd == "exam":
        if active["phase"] != "playing":
            raise ValueError("exam 只允许在 playing 阶段使用")
        sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
        from hiro.brain.veil import IDOL
        from hiro.loop.errors import ResumeBlocked
        from hiro.produce.veil_exam import apply_deck_change, run_round

        if state["preflight"]["idol"] != IDOL:
            raise ValueError("此考试入口仅适用于首检记录为 [VEIL] 秦谷美玲的批次")
        deck, ledger = args.deck, active.get("ledger")
        if deck is None and ledger is not None:
            sys.path.insert(0, str(ROOT / "tools"))
            import batch_ledger as L

            deck = L.to_deck(ledger)
        if deck is None:
            base, change = state.get("veil_base_deck"), active.get("veil_deck_change")
            if base is None or change is None:
                raise ValueError("先 memory-load 建本局账本，或登记基准牌组和本局变卡记录，或用 --deck 提交本轮完整清单")
            deck = apply_deck_change(base, change)
        try:
            out = run_round(args.round, deck, f"batch-{args.batch}-{args.run}-exam{args.round}",
                            drinks=ledger["drinks"] if ledger else None,
                            abilities=ledger.get("abilities") if ledger else None)  # fmt: skip
        except ResumeBlocked as e:
            raise ValueError(str(e)) from e
        if ledger is not None and out.get("drinks") is not None:
            L.after_exam(ledger, out["drinks"], why=f"R{args.round} 考完（喝掉的去掉、往左挤）")
            out["ledger_drinks"] = ledger["drinks"]
            persist()
        if ledger is not None and out.get("abilities_source") == "screen" and out.get("abilities"):
            # 开局状态栏读到的回忆能力：账本和回忆栏都没记过时记下（记过且不同时不覆盖，交代理核对）
            read = L.static_abilities(out["abilities"])
            if not ledger.get("abilities"):
                ledger["abilities"] = read
                L._log(ledger, "abilities", abilities=[a["card"] for a in read], why=f"R{args.round} 开局状态栏")
                persist()
            _, saved = L.save_abilities(ledger["memory"], read)
            out["abilities_saved"] = saved
        return out
    elif cmd in {"complete", "settle"}:
        if active["phase"] != "playing" or not args.result.strip():
            raise ValueError("须确认本局已开始并提供结算记录")
        outcome = None
        if cmd == "settle":
            outcome = device_action("settle")
            if outcome["page"] != "home":
                return outcome
        record = {"run": args.run, "result": args.result}
        if outcome and outcome.get("memories"):
            record["memories"] = outcome["memories"]  # 结算里每份回忆的评估（交接提示词优先级），最后一份是保存的
        state["completed"].append(record)
        state["active"] = None
        if cmd == "settle" and len(state["completed"]) < state["total"]:
            state["active"] = {"run": args.run + 1, "phase": "opening"}
            persist()
            return device_action("open")
    else:
        phase = "opening" if cmd in {"refill", "open"} else "playing"
        if active["phase"] != phase:
            raise ValueError(f"{cmd} 只允许在 {phase} 阶段使用")
        if cmd == "burst" and state["preflight"]["idol"] == VEIL_IDOL:
            return device_action(cmd, auto_route=True)
        return device_action(cmd)


def watch(path, run):
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
    from hiro.loop import pause_flag

    last_idle = False
    holds: list[str] = []

    def tick(advance):
        nonlocal last_idle
        with (STATE_DIR / ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            state = json.loads(path.read_text(encoding="utf-8"))
            active = state.get("active") or {}
            if len(state["completed"]) >= state["total"] or (run is not None and active.get("run") != run):
                return False
            if (state.get("preflight") or {}).get("idol") != VEIL_IDOL:
                raise ValueError("常驻快点仅适用于首检记录为 [VEIL] 秦谷美玲的批次")
            if pause_flag.ENABLED and pause_flag.reason() is not None:
                return True
            try:
                result = advance() if active.get("phase") == "playing" else None
            except HoldPage as hold:
                # 同一个停点连续 HOLD_CONFIRM 次才退出：过场里半帧的行动页 / 选择页不算
                holds.append(str(hold))
                if len(holds) >= HOLD_CONFIRM and len(set(holds[-HOLD_CONFIRM:])) == 1:
                    raise
                return True
            holds.clear()
            if result is not None:
                kind, tap = result
                print(json.dumps({"run": active.get("run"), "action": kind, "tap": tap}, ensure_ascii=False), flush=True)
            elif not last_idle:
                print(json.dumps({"run": active.get("run"), "action": "waiting"}), flush=True)
            last_idle = result is None
        return True

    with (STATE_DIR / ".watch.lock").open("a") as resident:
        fcntl.flock(resident, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            device_action("watch", watch_tick=tick, auto_route=True)
        except KeyboardInterrupt:
            pass
        except HoldPage as hold:
            print(json.dumps({"action": "hold", "reason": str(hold)}, ensure_ascii=False), flush=True)
            return 3
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, help="当前对话批次 ID")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").add_argument("total", type=int)
    sub.add_parser("status")
    p = sub.add_parser("preflight", help="实际核对实时画面、AP、跳过选项、偶像和回忆后才记录")
    p.add_argument("--ap", required=True, type=int)
    p.add_argument("--idol", required=True)
    p.add_argument("--memory", required=True)
    sub.add_parser("begin")
    sub.add_parser("deck-base").add_argument("--deck", type=Path, required=True)
    p = sub.add_parser("change-scan", help="换卡页：阶段 1 一帧读候选；阶段 2 滑一遍拼出整副行列表")
    p.add_argument("run", type=int)
    p = sub.add_parser("change-pick", help="换卡页：阶段 1 按 --col 选候选并点「次へ」；阶段 2 按 --row/--col 选要换出的卡")
    p.add_argument("run", type=int)
    p.add_argument("--col", type=int, required=True)
    p.add_argument("--row", type=int)
    p.add_argument("--commit", action="store_true", help="阶段 2 核对选中后直接点「チェンジ」→「決定」")
    p.add_argument("--scan", type=Path, help="指定 change-scan 的 JSON，默认本局最新一份")
    p = sub.add_parser("memory-save", help="本战开局：把这份回忆带来的整副牌组存进回忆栏（reports/memories/）")
    p.add_argument("--memory", required=True, help="回忆名（自定，整个批次和以后的批次复用）")
    p.add_argument("--deck", type=Path, required=True, help="牌组清单 JSON（同 deck-base 格式，开局牌组一览核对）")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("memory-load", help="开局：从回忆栏载入整副牌组，建本局账本")
    p.add_argument("run", type=int)
    p.add_argument("--memory", required=True)
    p.add_argument("--drinks", help="开局饮料栏，逗号分隔，从第 1 格起（空的不写）")
    p.add_argument("--reload", action="store_true", help="丢掉本局已记的变动，重新载入")
    p = sub.add_parser("ledger", help="看本局账本：整副牌组、一次性、饮料栏 1–4")
    p.add_argument("run", type=int)
    p.add_argument("--read-drinks", action="store_true", help="同时读实机饮料栏与账本比对（只读）")
    p.add_argument("--log", action="store_true", help="附上逐条变动记录")
    p = sub.add_parser("ledger-edit", help="手动记账：拿卡、删卡、强化、饮料（自动记不到的变动）")
    p.add_argument("run", type=int)
    p.add_argument("--add", help="拿到一张卡（带 + 写全名），配 --once / --not-once")
    p.add_argument("--remove", help="删掉一张卡")
    p.add_argument("--change", nargs=2, metavar=("OLD", "NEW"), help="换卡：换出 OLD、换入 NEW，配 --once / --not-once")
    p.add_argument("--upgrade", help="强化一张卡：X → X+")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--once", dest="once", action="store_true", default=None, help="新卡是レッスン中1回")
    g.add_argument("--not-once", dest="once", action="store_false", help="新卡不是一次性")
    p.add_argument("--drink-add", help="收下一瓶饮料（放进最左空格）")
    p.add_argument("--drink-remove", type=int, metavar="SLOT", help="去掉第 SLOT 格（1–4），剩下的往左挤")
    p.add_argument("--drink-set", help="按实机整栏覆盖，逗号分隔（核对后才用）")
    p.add_argument("--rebound-repeatable", choices=("true", "false"), help="巻き返し+ 是否已删除一次性限制")
    p.add_argument("--why", help="来由 / 证据路径")
    for name, help_text in (("shop-scan", "相談 / 轮间：读货架上的饮料，标出高价值三种"),
                            ("shop-refresh", "相談 / 轮间：用 P 刷新货架（要ジュエル会取消）后重读")):  # fmt: skip
        sub.add_parser(name, help=help_text).add_argument("run", type=int)
    p = sub.add_parser("shop-auto", help="相談 / 轮间：一条命令补给——买高价值饮料、P 够就刷新，直到饮料栏满 / P 不够（自动记账）")
    p.add_argument("run", type=int)
    p.add_argument("--max-refresh", type=int, default=10)
    p = sub.add_parser("shop-buy", help="相談 / 轮间：买第 INDEX 格的饮料（固定像素流程，自动记账）")
    p.add_argument("run", type=int)
    p.add_argument("--index", type=int, required=True)
    p = sub.add_parser("memory-abilities", help="本战考试开局：读状态栏的回忆能力（最多 4 项），写进账本和回忆栏")
    p.add_argument("run", type=int)
    p.add_argument("--overwrite", action="store_true", help="回忆栏已记的不同时覆盖")
    p = sub.add_parser("customize", help="轮间：スキルカードカスタマイズ，按卡名找卡、按项名选菜单，实行并回到轮间（固定像素）")
    p.add_argument("run", type=int)
    p.add_argument("--card", required=True, help="卡名（带 +），如 巻き返し+")
    p.add_argument("--item", required=True, help="菜单项名（部分即可），如 成長追加、強気追加、制限削除")
    p.add_argument("--need-maxed", help="同名多张时：只选这一项已做满的那张，如 制限削除")
    p = sub.add_parser("drink-drop", help="培育页：丢饮料栏第 SLOT 格（1–4，固定像素，自动记账）")
    p.add_argument("run", type=int)
    p.add_argument("--slot", type=int, required=True, choices=(1, 2, 3, 4))
    p = sub.add_parser("drink-overflow", help="饮料溢出页：丢已有的第 N 瓶（1 起，从上往下）、收下新的，自动记账")
    p.add_argument("run", type=int)
    p.add_argument("--discard", type=int, required=True)
    for cmd in ("playing", "checkpoint", "complete", "settle", "open", "burst", "watch", "refill", "deck-change", "exam"):
        p = sub.add_parser(cmd)
        if cmd == "watch":
            p.add_argument("run", type=int, nargs="?", help="省略局号时跟随整个批次")
        else:
            p.add_argument("run", type=int)
        if cmd in {"complete", "settle"}:
            p.add_argument("--result", required=True)
        if cmd == "checkpoint":
            p.add_argument("--note", required=True)
        if cmd == "deck-change":
            p.add_argument("--deck", type=Path, required=True)
        if cmd == "exam":
            p.add_argument("--round", type=int, choices=(1, 2), required=True)
            p.add_argument("--deck", type=Path, help="可选。覆盖该轮的完整牌组 JSON")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.batch):
        parser.error("批次 ID 仅支持字母、数字、下划线和连字符")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{args.batch}.json"
    try:
        if args.command == "watch":
            return watch(path, args.run)
        with (STATE_DIR / ".lock").open("a") as lock:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            if args.command == "init":
                if args.total < 1 or path.exists():
                    raise ValueError("总次数必须为正，且批次 ID 尚未使用")
                state = {"total": args.total, "preflight": None, "active": None, "completed": []}
                save(path, state)
            else:
                state = json.loads(path.read_text(encoding="utf-8"))
                if args.command != "status":
                    result = change(state, args, persist=lambda: save(path, state))
                    if result is not None:
                        print(json.dumps(result, ensure_ascii=False))
                        return int(result.get("ok") is False)
                    save(path, state)
        completed = state["completed"]
        summary = {
            "file": str(path), "total": state["total"], "completed": len(completed),
            "preflight": state["preflight"], "active": state["active"],
            "last_result": completed[-1] if completed else None,
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as e:
        print(f"批量育成停止：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
