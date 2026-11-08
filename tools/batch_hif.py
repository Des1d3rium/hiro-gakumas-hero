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
from hiro.produce import hif_buttons as BTN

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
STUDY_LINE_TAP = BTN.TOAST  # 授業结束后的训练员台词框
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
    (r"獲得可能スキルカード", "结算回忆"),
    (r"再生成", "结算回忆"),  # 只在同屏有「次へ / メモリー」时算（H.I.F 首页左侧栏图标标签也会被读成「再生成」，见 hold_reason）
    (r"^MEMORY$", "结算回忆生成"),  # LIVE 后「MEMORY」生成页只有 MEMORY / 生成 两个字（实机第 2 局 watch 在这里一直点角落）
    (r"開始確認|アイドル選択|ショップ|ミッション|ジュエル|イベントモード|選抜試験", "首页 / 开局"),  # 后两个：H.I.F 首页（模式开关、选拔入口）
    (r"本戦開始", "考试前"),
)
HOLD_TEXTS = tuple(p for p, _ in HOLD_PATTERNS[:3])  # 兼容旧名
LIVE_FAIL = ("locked", "nogame", "failed")
LIVE_OCR_EVERY = 2.0  # 横屏帧多久 OCR 一次找「TAP TO START」
# LIVE 跳过（使用者 2026-11-07：「live 开启后，点击一次屏幕唤起跳过按钮，然后点击一次右上角的跳过，再点击橙色按钮」）。
# 横屏帧 1280x720 上的相对位置，按使用者截图（2000x1250，16:9 铺满宽、上下各约 62px 黑边）换算
LIVE_REVEAL = (0.50, 0.50)  # 画面中间：唤出控制条（快门在右侧 x≈0.91，不碰）
# 右上角「⏭」：验证局 codex-20261107-1-b 实机帧（MaaTools 1280x720）上控制条在 y 35–150，⏭ 中心 (1201,105)（帧坐标；
# 点击时减去顶部白条高度 top_bar，见 live_skip_step）。
# 原来按使用者窗口截图换算成 (1203,40)，落在控制条上方，点了 4 次都没出确认框
LIVE_SKIP = (0.938, 0.146)  # 帧坐标（含顶部白条），只作参考
LIVE_SKIP_FIXED = (0.9375, 0.10)  # 点击用：屏幕坐标（使用者 2026-11-07 实机截图 2000x1250 上 ⏭ 在 (1875,125)）
LIVE_END_OK = (0.61, 0.89)  # 「ライブ終了確認」橙色「終了する」(1220,1062) → (781,640)；OCR 认到文字时按文字点
LIVE_SKIP_AFTER = 2.0  # LIVE 开始后等这么久再跳（等曲目页退场）
LIVE_SKIP_EVERY = 4.0  # 没见到确认框时隔这么久重试一次
LIVE_SKIP_MAX = 4
LIVE_END_DANGER = ("ジュエル", "購入", "有償", "破棄", "リタイア")
LIVE_EXIT_AFTER = 1.5  # 点「終了する」后等这么久，按 fn+shift+F 直接回竖屏（使用者 2026-11-07）


LIVE_CONTROLS = re.compile(r"\d{1,2}:\d{2}/\d{1,2}:\d{2}|歌詞")  # 控制条露出来时左上的播放时间「00:20/01:32」/ 左下「歌詞OFF」
LIVE_END_TRIES = 3


def top_bar(img, limit: int = 80) -> int:
    """横屏帧顶部的纯白条高度（MaaTools 横屏帧整体下移、上面补白：实机 1280x720 帧 y 0–34 全白，「歌詞OFF」贴底被截）。
    点击坐标按游戏画面算、不含这条白边——按帧坐标点会低这么多：验证局 1-d 点 ⏭ (1200,105) 触摸落在 y≈140（控制条下方），
    4 次都没出确认框。点之前把 y 减掉它。"""
    import numpy as np

    rows = 0
    for y in range(min(limit, img.shape[0])):
        row = img[y]
        if float(np.mean(row)) < 250 or float(np.std(row)) > 3:
            break
        rows += 1
    return rows


def live_skip_step(img, boxes, live, click):
    """横屏 LIVE 播放中，每次 OCR 后按画面走一步（不盲点）：
    - 「ライブ終了確認」框在：点「終了する」（按文字；框里有付费 / 破棄字样不点）。框没消失下一次再点，最多 LIVE_END_TRIES 次；
    - 控制条露着（播放时间 / 歌詞）：点右上角 ⏭；
    - 都没有：点画面中间唤出控制条，下一次 OCR 看到控制条再点 ⏭（控制条已经露着时点中间会把它收起来）。
    框消失后才记 ended（之后 live_step 按 fn+shift+F 回竖屏）。返回 (kind, tap) 交给 advance 点，没事做返回 None。"""
    h, w = img.shape[:2]
    dy = top_bar(img)
    at = lambda rel: (int(w * rel[0]), int(h * rel[1]) - dy)  # noqa: E731
    texts = ["".join(unicodedata.normalize("NFKC", t).split()) for t, _, _ in boxes]
    now = time.monotonic()
    if any("ライブ終了確認" in t or "ライブを終了しますか" in t for t in texts):
        if any(d in t for t in texts for d in LIVE_END_DANGER):
            return None
        if live.get("end_tries", 0) >= LIVE_END_TRIES:
            return None  # 点了几次框都还在：停着，watch 照常按停点报给代理
        ok = next((((b[0] + b[2]) // 2, (b[1] + b[3]) // 2 - dy) for t, (_, _, b) in zip(texts, boxes) if t.endswith("終了する")), None)
        live["end_tries"] = live.get("end_tries", 0) + 1
        live["end_seen"] = now
        return "live_end", ok or at(LIVE_END_OK)
    if live.get("end_seen") and not live.get("ended"):
        live["ended"] = now  # 点过「終了する」、框已消失：LIVE 结束
        return None
    started = live.setdefault("started", now)  # watch 中途接手（没看到 TAP TO START）时从第一帧横屏算
    if live.get("ended") or now - started < LIVE_SKIP_AFTER:
        return None
    # 使用者 2026-11-07：进 LIVE 后所有点击都点 ⏭ 的固定位置，直到退出 LIVE（第一下唤出控制条，之后点中 ⏭ 出确认框）。
    # 不再按控制条 / 中间唤出分支，也不限次数。坐标按屏幕（不含帧顶白条）：使用者实机截图 ⏭ 在 (0.9375, 0.10)
    live["skips"] = live.get("skips", 0) + 1
    return "live_skip", (int(w * LIVE_SKIP_FIXED[0]), int(h * LIVE_SKIP_FIXED[1]))


def live_fail_reason(op, result) -> str:
    """LIVE 切全屏失败的停点提示：进入 / 退出给不同处置（N9：退出失败时原来也写「手动点全屏按钮」）。"""
    if result == "locked":
        return f"LIVE：屏幕锁住 / 显示器休眠（{result}），解锁后 watch 会接着切"
    if op == "exit":
        return (f"LIVE 结束后退出全屏失败（{result}）：手动按 fn+shift+F 或点窗口左上角绿色按钮退出全屏，"
                "回到竖屏后 watch 自动接着推进")
    return f"LIVE 黑屏：进入全屏失败（{result}），手动点游戏窗口的全屏按钮，LIVE 结束回竖屏后再退出全屏"


HOME_NAV = (250, 1165, 470, 1279)  # 首页底栏「ホーム」
SETTLE_LEAD_MAX = 300.0  # settle 前段（结果页之后 → 回忆页，含优胜 LIVE）最长推进时间
SETTLE_FAST_GAP = 0.3  # 回忆定下后只点不读时的间隔


# 批量开局自动点「プロデュース開始」（HIRO_BATCH_AUTO_START=0 关掉）。命中这些字样一律不点
sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_rules  # noqa: E402  周回模式默认规则（conf/batch-rules.json，使用者可随时改）


def _env_flag(name, rule):
    v = os.environ.get(name)
    return batch_rules.get(rule, True) if v is None else v != "0"


AUTO_START = False  # 本次命令是否允许自动点「プロデュース開始」：change() 开头按 start_allowed(state) 设


def start_allowed(state) -> bool:
    """open.auto_start：repeat（默认）= 每批第一局由代理核对后手点，之后的重复局自动点；true / false = 每局都自动 / 都不自动。
    环境变量 HIRO_BATCH_AUTO_START=0 / 1 临时覆盖。"""
    env = os.environ.get("HIRO_BATCH_AUTO_START")
    mode = (env != "0") if env is not None else batch_rules.get("open.auto_start", "repeat")
    if mode == "repeat":
        return len(state.get("completed") or []) >= 1
    return bool(mode)
START_DANGER = ("破棄", "リタイア", "別の端末", "他の端末", "別端末", "再開", "ジュエル", "購入", "有償", "不足", "データ")


def same_frame(a, b) -> bool:
    """点之前重新截的帧和刚分析的帧是否同一画面（缩略图差 < OcrGate.SAME）。"""
    return a is not None and b is not None and a.shape == b.shape and OcrGate.diff(OcrGate.thumb(a), OcrGate.thumb(b)) < OcrGate.SAME


def watch_gap(default: float) -> float:
    """watch 连点间隔：HIRO_WATCH_TAP_GAP（秒，0.1–1.0）覆盖，没设用 advance.ADVANCE_EVERY。实机对比 0.4 / 0.2 用。"""
    try:
        v = float(os.environ.get("HIRO_WATCH_TAP_GAP") or batch_rules.get("watch.tap_gap", default))
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.1, v))
SETTLE_STUCK = 4  # 连点这么多次画面都没变：整帧读一次（可能是认不出的按钮页）
SETTLE_FAST_MAX = 200  # 只点不读最多这么多下（约 1 分钟多），之后全部回到逐步读
SETTLE_POPUP_TAP = (360, 1170)  # 结算后活动弹窗底部按钮（1-e 代理手点这里回到首页）
INTERVAL_GO = (513, 1160)  # 「インターバル終了確認」右「本戦へ」（hiro.produce.interval 实测）


def interval_end(device, timeout: float = 15.0) -> dict:
    """轮间 → R2（使用者 2026-11-07：「終了」不做 OCR，直接点固定坐标；1-h 读成「X終了」没点上）。
    只在左上是「インターバル」时点；確認框用像素标题条判出现，点「本戦へ」后等标题消失。"""
    from hiro.produce import interval
    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header

    if not interval.on_interval(device.screenshot(), ocr_boxes):
        return {"ok": False, "why": "左上不是「インターバル」，不点"}
    device.click(*BTN.SHOP_END)
    deadline = time.time() + 5
    while dialog_header(device.screenshot()) is None:
        if time.time() >= deadline:
            return {"ok": False, "why": "点了「終了」，5s 内没出确认框"}
        time.sleep(0.3)
    device.click(*INTERVAL_GO)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if not interval.on_interval(device.screenshot(), ocr_boxes):
            return {"ok": True}
    return {"ok": False, "why": f"点了「本戦へ」，{timeout:.0f}s 后左上仍是「インターバル」"}


SETTLE_TAP = BTN.SETTLE  # 回忆定下后结算页：「×」(360,1188) 与后面「次へ」圆角框 (y 1125–1195) 的重合处（= hif_buttons.SETTLE）
TAP_THROUGH = tuple(batch_rules.get("watch.tap_through_holds", []))  # 停点确认期间仍点角落
HOLD_CONFIRM = 3  # watch：同一停点连续读到几次才退出（约 1–2 秒）


class HoldPage(Exception):
    """watch 遇到必须由代理处理的页面：停下、不点角落。"""


# 获得演出（饮料 / 技能卡）：物品条盖在页面中下部，下面还露着「受け取る」按钮或所持一览。这些帧只是动画，
# 点角落就过（实机 codex-20261107-6 第 2 局：差し入れ领了「初星スーパーソーダ」后、公開レッスン获得センブリソーダ时都误停）
BANNER_PASS = ("领取选择", "考试中")


def acquire_banner(boxes) -> bool:
    """物品条下的效果说明行：中心 y 在 905–1070、左缘 x < 170、宽 > 150。选择页这一带只有图标，没有这种长文字行。"""
    return any(905 <= (b[1] + b[3]) / 2 <= 1070 and b[0] < 170 and b[2] - b[0] > 150 for _, _, b in boxes)


def result_next(boxes, dialog: bool):
    """考试结果页（ラウンドN結果 / 現在順位 + 底部「次へ」）：先点一次「次へ」。顺利晋级就没有弹窗，直接往下走；
    敗退会弹「再挑戦確認」（有弹窗标题条 → 这里不点，照旧停下交给代理）。使用者 2026-11-07：「如果下一步之后会有这个弹窗，才唤起你」。"""
    from hiro.produce import hif_buttons as BTN

    if dialog or not batch_rules.get("watch.result_next_first", True):
        return None
    labels = ["".join(unicodedata.normalize("NFKC", t).split()) for t, _, _ in boxes]
    if any("再挑戦確認" in t or "プロデュース終了" in t for t in labels):
        return None
    page = any(re.search(r"ラウンド\d結果|現在順位", t) for t in labels)
    if not page:
        return None
    if any(t.endswith("次へ") and not t.startswith("タップ") and 1090 <= (b[1] + b[3]) / 2 <= 1180
           for t, (_, _, b) in zip(labels, boxes)):
        return BTN.RESULT_NEXT
    # 清单 5：考前「現在順位」页底部是「タップして次へ」（y≈1187，不是按钮），按文字位置点
    hint = next((b for t, (_, _, b) in zip(labels, boxes)
                 if re.fullmatch(r"(?:タップして|TAPで)次へ", t) and 1090 <= (b[1] + b[3]) / 2 <= 1230), None)
    if hint:
        RANK_SEEN["at"] = None
        return (hint[0] + hint[2]) // 2, (hint[1] + hint[3]) // 2
    # 「タップして次へ」淡入前几帧既没按钮也没提示：先等 RANK_WAIT_S 秒再让它落到停点（验证局 R1 / R2 各误报一次）
    now = time.monotonic()
    if RANK_SEEN["at"] is None or now - RANK_SEEN["last"] > 3.0:  # 离开这页 3s 以上再回来才重新计时
        RANK_SEEN["at"] = now
    RANK_SEEN["last"] = now
    return "wait" if now - RANK_SEEN["at"] < RANK_WAIT_S else None


# ---------------------------------------------------------------- debug：本战主动再挑戦攒 trace（使用者 2026-11-07）
# 批次设了 debug_retries（如 R1 2 次、R2 1 次）时，晋级的结果页也按计划点「再挑戦」，一局打出 3 次 R1、2 次 R2 的 trace，
# 快速建可信 trace 库。只用免费 / 再挑戦チケット：确认框里有ジュエル、或是敗退的「プロデュース終了」框都不点。
# 每轮最多 3 次（同 AGENTS.md 再挑戦上限）。计数存在 active.debug_retries_used，watch 重启不重来。
DEBUG_RETRY_MAX = 3
DEBUG_RETRY: dict = {"plan": None, "used": {}, "pending": None, "ending": None, "round": None}  # pending：{"round", "at", "confirmed"}；tick 每步从批次状态填 plan / used
DEBUG_RETRY_WAIT_S = 15.0  # 点了「再挑戦」后这么久内等确认框
DEBUG_RETRY_RETAP_S = 2.0  # 点了确认框还在：隔这么久再点（不重复计数）
RETRY_CONFIRM_WORDS = ("再挑戦する", "決定", "はい", "OK")
END_CONFIRM_WORDS = ("終了する", "決定", "はい", "OK", "プロデュース終了")
DEBUG_DANGER = ("ジュエル", "破棄", "リタイア", "タイトルへ")
# 确认框按钮第一次靠 OCR 找到后存下坐标，之后直接点（使用者 2026-11-07：「找到后确定坐标，不用每次都找了」）。
# 危险字样照样每帧 OCR 核对。文件在 reports/batches/（不进仓库），删掉就重新找
DEBUG_XY_FILE = Path(__file__).resolve().parents[1] / "reports" / "batches" / "debug-retry-xy.json"


def _learned_xy(key, found):
    """found：这一帧 OCR 找到的按钮中心或 None。存过的坐标优先；没存过、这次找到了就存下。"""
    try:
        saved = json.loads(DEBUG_XY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if key in saved:
        return tuple(saved[key])
    if found is not None:
        saved[key] = list(found)
        try:
            DEBUG_XY_FILE.parent.mkdir(parents=True, exist_ok=True)
            DEBUG_XY_FILE.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return found


def _button(labels, boxes, words, *, right=True):
    return next((((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for t, (_, _, b) in zip(labels, boxes)
                 if t in words and (b[1] + b[3]) / 2 > 600 and (((b[0] + b[2]) / 2 > 360) == right)), None)


def parse_debug_retries(text: str) -> dict | None:
    """「2,1」→ {"1": 2, "2": 1}（R1、R2 各主动再挑戦几次）；「off」/「0,0」→ None。"""
    if text.strip().lower() in ("off", "none", ""):
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("--debug-retries 写成「R1次数,R2次数」，如 2,1；关掉写 off")
    plan = {"1": int(parts[0]), "2": int(parts[1])}
    if any(n > DEBUG_RETRY_MAX for n in plan.values()):
        raise ValueError(f"每轮最多再挑戦 {DEBUG_RETRY_MAX} 次")
    return plan if any(plan.values()) else None


def button_grey(img, xy, half=(70, 18), max_sat=40) -> bool:
    """按钮变灰（不能点）：中心附近一块的平均饱和度很低。可点的「再挑戦」是彩色按钮。阈值未在实机核对。"""
    import cv2

    x, y = xy
    patch = img[max(0, y - half[1]):y + half[1], max(0, x - half[0]):x + half[0]]
    if patch.size == 0:
        return False
    return float(cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[..., 1].mean()) < max_sat


def debug_retry_step(boxes, dialog: bool, img=None):
    """按 debug 计划主动再挑戦：结果页点「再挑戦」→ ("debug_retry", tap)；随后的确认框核对后点确认 → ("debug_retry_confirm", tap)；
    等确认框 / 等确认框关掉 → "wait"；不归这里管 → None。要花ジュエル时抛 HoldPage。"""
    from hiro.produce import hif_buttons as BTN

    plan = DEBUG_RETRY["plan"]
    if not plan:
        return None
    labels = ["".join(unicodedata.normalize("NFKC", t).split()) for t, _, _ in boxes]
    flat = "".join(labels)
    pend, now = DEBUG_RETRY["pending"], time.monotonic()
    if pend is not None and now - pend["at"] > DEBUG_RETRY_WAIT_S:
        DEBUG_RETRY["pending"] = pend = None
    ending = DEBUG_RETRY.get("ending")
    if ending is not None and now - ending > DEBUG_RETRY_WAIT_S:
        DEBUG_RETRY["ending"] = ending = None
    if dialog and "再挑戦確認" in flat and "プロデュース終了" in flat:
        # 敗退（使用者 2026-11-07）：debug 攒 trace 时照样点「再挑戦」，直到「再挑戦」变灰（免费 / 券用完）或这一轮已再挑戦
        # DEBUG_RETRY_MAX 次，才点「プロデュース終了」接着结算开下一局。ジュエル 照样不花
        if any(w in flat for w in DEBUG_DANGER):
            raise HoldPage("debug 敗退框里有危险字样，不点")
        if (ending is not None and now - ending < DEBUG_RETRY_RETAP_S) or (pend is not None and pend["confirmed"] is not None):
            return "wait"
        rnd = DEBUG_RETRY.get("round")
        used = DEBUG_RETRY["used"].get(rnd, 0) if rnd else DEBUG_RETRY_MAX
        retry_at = _learned_xy("defeat_retry", _button(labels, boxes, ("再挑戦",)))
        if used < DEBUG_RETRY_MAX and retry_at is not None and not (img is not None and button_grey(img, retry_at)):
            DEBUG_RETRY["pending"] = {"round": rnd, "at": now, "confirmed": now}  # 之后若有确认框只补点、不重复计数
            return "debug_defeat_retry", retry_at
        found = _button(labels, boxes, ("プロデュース終了",), right=False)
        tap = _learned_xy("defeat_end", found)
        if tap is None:
            return "wait"
        DEBUG_RETRY["ending"] = now
        DEBUG_RETRY["pending"] = None
        return "debug_defeat_end", tap
    if dialog and ending is not None:
        # 「プロデュース終了」之后可能还有一层确认：没有危险字样才点右侧的「終了する / 決定 / はい」
        if any(w in flat for w in DEBUG_DANGER):
            raise HoldPage("debug 敗退后的确认框里有危险字样，不点")
        if now - ending < DEBUG_RETRY_RETAP_S:
            return "wait"
        tap = _learned_xy("defeat_end_confirm", _button(labels, boxes, END_CONFIRM_WORDS))
        if tap is None:
            return "wait"
        DEBUG_RETRY["ending"] = now
        return "debug_defeat_end_confirm", tap
    if dialog:
        if pend is None:
            return None  # 不是我们点出来的框
        if any(w in flat for w in DEBUG_DANGER):
            DEBUG_RETRY["pending"] = None
            raise HoldPage("debug 再挑戦：确认框要消耗ジュエル（或有危险字样），不点")
        hit = _learned_xy("retry_confirm", _button(labels, boxes, RETRY_CONFIRM_WORDS))
        if hit is None:
            return "wait"
        if pend["confirmed"] is not None:
            if now - pend["confirmed"] < DEBUG_RETRY_RETAP_S:
                return "wait"
            pend["confirmed"] = now
            return "debug_retry_retap", hit
        pend["confirmed"] = now
        return "debug_retry_confirm", hit
    m = next((re.search(r"ラウンド(\d)結果", t) for t in labels if re.search(r"ラウンド(\d)結果", t)), None)
    if m is None:
        return None
    rnd = m.group(1)
    DEBUG_RETRY["round"] = rnd
    if pend is not None:
        # 点了「再挑戦」、确认框还没出来，或确认后结果页还没退：别再点、也别点「次へ」
        return "wait" if pend["confirmed"] is None or now - pend["confirmed"] < DEBUG_RETRY_WAIT_S else None
    if DEBUG_RETRY["used"].get(rnd, 0) >= min(plan.get(rnd, 0), DEBUG_RETRY_MAX):
        return None
    if "ジュエル" in flat:
        raise HoldPage("debug 再挑戦：结果页出现ジュエル，不点")
    btn = next((b for t, (_, _, b) in zip(labels, boxes)
                if t.endswith("再挑戦") and 1090 <= (b[1] + b[3]) / 2 <= 1180 and (b[0] + b[2]) / 2 < 300), None)
    if btn is None:
        return None  # 没有再挑戦按钮（免费 / 券都没了？）：照常「次へ」
    DEBUG_RETRY["pending"] = {"round": rnd, "at": now, "confirmed": None}
    return "debug_retry", BTN.RESULT_RETRY


RANK_WAIT_S = 5.0
RANK_SEEN = {"at": None, "last": 0.0}
BRIEFING_TAP = (360, 300)  # 考前说明页：点上方空白进入考试（同 batch_prep.BRIEFING_TAP）


def briefing_step(boxes, dialog: bool):
    """考前说明页（優勝条件 / 合格条件，没有回合数）：「タップして次へ」出来了就点进考试 → ("exam_briefing", tap)；
    之前是倍率ボーナス演出 → "wait"（不点、也不停）。不是这页返回 None。清单 5：原来两轮都停下由代理手点。"""
    if dialog or not batch_rules.get("prep.exam_briefing", True):
        return None
    labels = ["".join(unicodedata.normalize("NFKC", t).split()) for t, _, _ in boxes]
    if not any("優勝条件" in t or "合格条件" in t for t in labels):
        return None
    if any(re.search(r"残.?ター|ターン目|再挑戦|ジュエル|破棄|リタイア", t) for t in labels):
        return None
    if any(t.endswith("次へ") and "タップ" in t or t.startswith("TAP") for t in labels):
        return "exam_briefing", BRIEFING_TAP
    return "wait"


def photo_save_error(texts) -> bool:
    """结算回忆的照片保存失败框：标题「エラー」+「フォトを保存できませんでした」+「リトライ」，且没有别的危险字样。"""
    flat = "".join("".join(unicodedata.normalize("NFKC", t).split()) for t in texts)
    return ("フォトを保存できませんでした" in flat and "リトライ" in flat
            and not any(w in flat for w in ("リタイア", "通信エラー", "タイトルへ", "ジュエル", "プロデュースデータ", "育成データ")))


def hold_reason(texts):
    """OCR 文本里有必须由代理处理的页面特征时返回「理由：命中文字」，否则 None。"""
    labels = ["".join(unicodedata.normalize("NFKC", t).split()) for t in texts]
    labels.append("".join(labels))  # 标题被 OCR 拆成两段时也能命中
    memory_page = any("次へ" in t or t == "メモリー" for t in labels[:-1])  # 首页说明文字里也有「…メモリーが獲得できます」
    for pattern, reason in HOLD_PATTERNS:
        if pattern == r"再生成" and not memory_page:
            continue
        for label in labels:
            if m := re.search(pattern, label):
                return f"{reason}：{m.group(0)}"
    return None


def commu_confirmed(boxes) -> bool:
    """剧情底栏要 OCR 也认得出：「早送り」+ 任一（ログ / AUTO / SKIP）。清单 4：白字模板在授業分支页淡入帧上误中，
    按剧情点 SKIP 的位置 (225,1200) 正好是饮料栏第 2 格（同 hiro.loop.controller._skip_commu 的判据）。"""
    labels = ["".join(unicodedata.normalize("NFKC", t).split()).upper() for t, _, b in boxes if b[1] >= 1100]
    return any("早送" in t for t in labels) and any("ログ" in t or "AUT" in t or "SKI" in t for t in labels)


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
    if page is not None and page["page"] == "commu" and not commu_confirmed(boxes):
        return None
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


class OcrGate:
    """watch 的 OCR 节流（整帧 OCR 一次约 270ms，分条带读反而更慢——每次调用有约 50ms 固定开销，所以只能少读）：
    - 同一画面（缩略图与上次 OCR 的帧差 < SAME）沿用上次的 OCR 结果：停点要连续确认 3 次，后两次不再读；
    - 画面在动（与上一帧差 > MOVING）、上一步判定是被动页（点角落 / 动画）、又没有弹窗，且距上次 OCR 不到 BLIND_S：
      不读，直接照上一步点（使用者：一直点左上角能加快动画）；
    - 所以最多每 BLIND_S 秒整帧读一次，考试舞台这种一直在动的停点页也会在 BLIND_S 内认出来。"""

    SAME = 1.5
    MOVING = 4.0
    BLIND_S = 1.5
    PASSIVE = ("corner", "animation")

    def __init__(self, ocr, clock=time.monotonic):
        self._ocr, self.clock = ocr, clock
        self.prev = self.ocr_thumb = self.boxes = self.last = None
        self.ocr_at = -1e9
        self.calls = 0

    @staticmethod
    def thumb(img):
        import cv2

        return cv2.cvtColor(cv2.resize(img, (36, 64), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype("float32")

    @staticmethod
    def diff(a, b):
        return float(abs(a - b).mean())

    def ocr(self, img):
        th = self.thumb(img)
        if self.ocr_thumb is not None and self.boxes is not None and self.diff(th, self.ocr_thumb) < self.SAME:
            return self.boxes
        self.calls += 1
        self.boxes, self.ocr_thumb, self.ocr_at = self._ocr(img), th, self.clock()
        return self.boxes

    def blind(self, img, header):
        """画面在动、上一步是被动页时，不读直接照上一步点：返回 (kind, tap) 或 None。"""
        if img is None or img.shape[:2] != (1280, 720):
            return None
        th = self.thumb(img)
        prev, self.prev = self.prev, th
        last = self.last
        if (last is None or last[0] not in self.PASSIVE or prev is None or self.diff(th, prev) <= self.MOVING
                or self.clock() - self.ocr_at >= self.BLIND_S or header(img) is not None or acquire_bar(img)):
            return None
        return last

    def decide(self, img, decide, header):
        """一步：能盲点就盲点，否则交给 decide（用 self.ocr 读）。停点（HoldPage）后清掉上一步，保证停点每次都重新确认。"""
        if (hit := self.blind(img, header)) is not None:
            return hit
        try:
            self.last = decide(img, self.ocr)
        except HoldPage:
            self.last = None
            raise
        return self.last


def acquire_bar(img) -> bool:
    """获得演出的名称栏（像素，约 1ms）：有它就不盲点，走完整判定，好把饮料记账（验证局：P 道具在公開レッスン 结束时白送的
    随机饮料只有获得演出，盲点连点角落时一帧都没识别，账本漏 2 瓶）。"""
    import cv2

    from hiro.vision.quick import _acquire_bar

    return img is not None and img.shape[:2] == (1280, 720) and _acquire_bar(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))


def auto_decide(img, *, ocr, quick, fallback, header):
    """watch / VEIL burst 的一步（纯判定，不点）：返回 (kind, tap)；该停下交给代理时抛 HoldPage；
    其余（帧不对、加载中）返回 None。

    (12,12) 在绝大多数页面无害，所以除了选择页、牌组一览、考试前说明和 fallback 认出的页面，其余都点角落。"""
    from hiro.loop.advance import ADVANCE_TAP

    if img is None or img.shape[:2] != (1280, 720):
        return None
    dialog = header(img) is not None
    page = quick(img)
    boxes = None
    if page is not None and page["page"] == "commu":
        boxes = ocr(img)
        if not commu_confirmed(boxes):
            page = None  # 不是剧情（授業分支页淡入等）：按没认出处理，下面照常判授業分支 / 停点
    fb = fallback(img) if page is None and not dialog else None
    if page is None and not dialog and fb in {None, "ACTION_SELECT"}:
        # Only read the top strip to identify a study branch. The
        # resident corner tap does not depend on full-frame OCR.
        study = fb is None and any(t.strip() == "授業" for t, _, _ in ocr(img[:150, :220]))
        if fb == "ACTION_SELECT" or study:
            boxes = boxes if boxes is not None else ocr(img)
            if result := route_tap(img, boxes, action_page=fb == "ACTION_SELECT"):
                return result
            if fb == "ACTION_SELECT":
                raise HoldPage(action_hold(boxes))
            # Other 授業 pages (card change etc.) fall through to the checks below.
    # 整帧读一次：选择页、考试、商店、确认框等都停下交给代理。快识别认成动画的页面也读：
    # 商店 / 轮间选中饮料时会被认成 ACQUIRE，轮间菜单会被认成 CUSTOMIZE_RESULT（全局回放）。
    boxes = boxes if boxes is not None else ocr(img)
    if step := debug_retry_step(boxes, dialog, img):
        return None if step == "wait" else step
    if tap := result_next(boxes, dialog):
        return None if tap == "wait" else ("result_next", tap)
    if step := briefing_step(boxes, dialog):
        return None if step == "wait" else step
    if page is not None and page["page"] in CORNER_HOLD_PAGES:
        # 牌组一览底部「スキルカード(23)」标签行会被 acquire_banner 当成效果说明行，「所持スキルカード」又在 BANNER_PASS 的
        # 「考试中」里 → 原来当获得演出点角落（验证局代理开一览核对的 1 分钟点了 104 下）。先停。
        raise HoldPage(f"页面 {page['page']}")
    if reason := hold_reason([t for t, _, _ in boxes]):
        kind = reason.split("：")[0]
        # 考试出牌页选中一张牌时，牌的说明行也落在 acquire_banner 那一带（验证局 1-c：exam 退出后代理手选牌，watch 2 分钟点了
        # 126 下角落、手点的牌没生效）→ 「考试中」还要看到获得演出的名称栏（像素）才放行
        if not (acquire_banner(boxes) and kind in BANNER_PASS and (kind != "考试中" or acquire_bar(img))):
            raise HoldPage(reason)
        note_drink(page, dialog)
        return "corner", ADVANCE_TAP
    if fb is not None:
        raise HoldPage(f"fallback 页面 {fb}")
    if page is not None and (page["dispatch"] or page["page"] in CORNER_HOLD_PAGES):
        raise HoldPage(f"页面 {page['page']}")
    note_drink(page, dialog)
    if page is not None and not dialog:
        kind = page["fields"].get("kind")
        if page["page"] in QUICK_ANIMATIONS or (page["page"] == "TRANSITION" and kind in QUICK_TRANSITIONS):
            tap = tuple(page["tap"])
            if page["page"] == "commu":
                return "commu", tap  # 不进 OcrGate.PASSIVE：剧情 SKIP 不盲点重复（剧情结束后同一位置是饮料栏）
            if tap != ADVANCE_TAP:
                return "animation", tap
    return "corner", ADVANCE_TAP


ACQUIRED: list[dict] = []  # auto_decide 看到的饮料获得演出（名字 + 时间），watch tick 取走记账
ACQUIRE_SAME_S = 8.0  # 同名饮料的获得演出这么久内只算一次（同一个演出连着好几帧）


def note_drink(page, dialog) -> None:
    """清单 10：事件 / 公開レッスン 等直接给的饮料只有获得演出、不经过选择页，原来不进账本。只在当作动画点过去的帧上记
    （商店 / 相談 选中饮料也会被认成 ACQUIRE，但那些页先按停点处理，到不了这里）；watch tick 去重后记账。"""
    if page is None or dialog or page["page"] != "ACQUIRE":
        return
    f = page["fields"]
    if f.get("kind") == "drink":
        note_acquire(f.get("name"))
    elif f.get("kind") == "card" and f.get("name"):
        # 第一层（使用者 2026-11-07：随机事件给的卡 / 强化漏记）：卡的获得演出也记，名称栏带强化次数
        name = "".join(unicodedata.normalize("NFKC", f["name"]).split()).rstrip("+") + "+" * int(f.get("upgrade") or 0)
        note_acquire(name, kind="card", card_id=f.get("card_id"))


def note_acquire(name, *, kind: str = "drink", card_id: str | None = None) -> None:
    if not name:
        return
    now = time.monotonic()
    if ACQUIRED and ACQUIRED[-1]["name"] == name and now - ACQUIRED[-1]["t"] < ACQUIRE_SAME_S:
        ACQUIRED[-1]["t"] = now
        return
    ACQUIRED.append({"name": name, "t": now, "new": True, "kind": kind, "card_id": card_id})


def action_hold(boxes):
    """行动页没走固定路线（相談 / 休む / 最后一天等）：说清楚有哪些按钮。"""
    names = [t for t, _, b in boxes if b[1] >= 900 and re.search(r"[ぁ-んァ-ン一-龯]", t)]
    return "行动页需要代理选择：" + "/".join(names[:6])


LAST_CHOICE: dict = {}


def flow_page(boxes, action, *, dialog, keep_memory=False):
    """Return a guarded action from OCR on the current portrait frame."""
    from hiro.produce.ops import button_text, find_text

    # OCR 常把「次へ」的平假名へ读成片假名ヘ（实机フォト选择页「次ヘ」）
    boxes = [(unicodedata.normalize("NFKC", t).replace(" ", "").replace("次ヘ", "次へ"), c, b) for t, c, b in boxes]
    texts = [t for t, _, _ in boxes]

    def has(*words):
        return any(word in t for t in texts for word in words)

    def button(*words, region=(0, 1070, 720, 1230)):
        matches = [b for b in boxes if any(button_text(b[0], w) for w in words)]
        return find_text(matches, [b[0] for b in matches], region=region, exact=True)

    if action == "settle" and photo_save_error(texts):
        # 结算选回忆照片后「エラーによりフォトを保存できませんでした / リトライしますか？」（使用者 2026-11-07）：点一次左边
        # 「キャンセル」，交还 settle 继续。正文的「破棄」指拍下的照片（改用模板图生成回忆），不是培育数据
        return "photo_error", button("キャンセル", region=(0, 1100, 360, 1230))
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
        if (AUTO_START and not dialog and has("プロデュース開始") and has("育成情報")
                and not has(*START_DANGER)):
            # 重复育成（使用者 2026-11-07：「重复育成完全可以使用这个」）：沿用首检核对过的偶像 / 回忆 / 编成，
            # 开局确认页没有弹窗、没有破棄 / 别的设备 / 再開 / 付费字样时直接点「プロデュース開始」。
            # 别的设备有进度、AP 不足等都会以弹窗出现：有弹窗就不点，照旧交给代理 / AP 流程
            return "start", button("プロデュース開始", region=(150, 980, 570, 1110))
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

    if has("メモリー確定確認") and not has("ジュエル", "購入", "有償"):
        # 回忆挑选后的「メモリー確定確認」：batch_regen.decide 按偏移点；漏了（框滑入时点空）这里按文字补点
        return "memory_confirm", button("生成する", region=(360, 1080, 720, 1230))

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
        # 最終プロデュース評価页只有底部「TAPで次へ」，没有「次へ」按钮（实机 codex-20261107-6 第 1 局）
        return "result", button("次へ", "完了する", "閉じる") or button("TAPで次へ")
    if tap := button("TAP", region=(200, 1000, 520, 1230)):
        # 回忆生成后逐项揭示能力，底部只有「TAP」（实机 codex-20261107-6 第 1 局）
        return "memory_reveal", tap
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

        raw = {"m": None}

        def live_click(x, y):
            """LIVE 横屏全屏的点击：不走竖屏缩放器（逻辑 720x1280 和横屏物理分辨率对不上，点不到该点的地方），按屏幕像素直接给
            MaaTools（MaaToolsImpl.click 自己再除 Catalyst 0.77）。(x, y) 是 1280x720 的屏幕坐标（不含帧顶白条）。
            实测（验证局 1-e，2026-11-07）：⏭ (1200,72) → 原始 (2700,180) 确认框弹出；「終了する」同法点中。"""
            if pause_flag.ENABLED and pause_flag.reason() is not None:
                raise ValueError("暂停旗标已出现，重新定位后再操作")
            from hiro.device import MaaToolsImpl

            if raw["m"] is None:
                raw["m"] = MaaToolsImpl(None)
                raw["m"].start()
            h, w = raw["m"].screenshot().shape[:2]
            if h > w:  # 已回竖屏：按普通点击
                device.click(x, y)
                return
            raw["m"].click(round(x * w / 1280), round(y * h / 720))

        if action == "refill":
            # Only called from the actual AP shortage dialog, before starting.
            return ap_refill.refill_fast(shoot, click, ocr_boxes, dialog_header, log=lambda msg: print(msg, file=sys.stderr))
        if action in {"settle", "open"}:
            import batch_memory

            last = None
            unread = 0
            refilled = False
            memories: list[dict] = []  # 结算里看到的每一份回忆的评估（按交接提示词优先级）
            seen_choice = False
            regen_done = False
            fast = {"thumb": None, "same": 0, "taps": 0}
            slow = {"thumb": None, "same": 0}
            steps = 0
            # 只按整帧读的步数算 60 步上限：回忆定下后的只点不读（最多 SETTLE_FAST_MAX 下）原来也占这 60 步，
            # 点 60 下就报 timeout（1-e、1-f 两局 settle 都是这样超时，之后画面其实自己回到首页）
            while steps < 60:
                img = shoot()
                if img is None or img.shape[:2] != (1280, 720):
                    return {"page": "unsupported_frame", "memories": memories}
                if action == "settle" and regen_done and fast["taps"] < SETTLE_FAST_MAX:
                    # 回忆定下之后：只点不读。弹窗（像素）、底栏像首页（局部 OCR「ホーム」）、连点几下画面不变时才整帧读一次
                    th = OcrGate.thumb(img)
                    fast["same"] = fast["same"] + 1 if fast["thumb"] is not None and OcrGate.diff(th, fast["thumb"]) < OcrGate.SAME else 0
                    fast["thumb"] = th
                    x1, y1, x2, y2 = HOME_NAV
                    near_home = any("ホーム" in t for t, _, _ in ocr_boxes(img[y1:y2, x1:x2]))
                    if dialog_header(img) is None and not near_home and fast["same"] < SETTLE_STUCK:
                        click(*SETTLE_TAP)
                        fast["taps"] += 1
                        time.sleep(SETTLE_FAST_GAP)
                        continue
                    fast["same"] = 0
                steps += 1
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
                if action == "settle" and not regen_done and (
                        page in {"generate", "regenerate", "memory_reveal", "memory_choice"}
                        or any("獲得するメモリーを選択" in t for t in texts)):
                    # LIVE 后的生成 / 再生成 / 挑选交给 batch_regen：快进 + 局部 OCR + 固定坐标，用完次数后逐个版本挑
                    import batch_regen

                    regen = batch_regen.run(shoot, click, ocr_boxes, dialog_header, log=lambda m: print(m, file=sys.stderr))
                    regen_done = True
                    memories.extend({"label": v["label"], "hit": v["hit"], "card": v["card"], "name": v.get("name"), "frame": v.get("frame")} for v in regen.get("versions") or [])
                    if not regen["ok"]:
                        return {"page": regen["page"], "regen": regen, "memories": memories}
                    last = None
                    time.sleep(0.65)
                    continue
                if page == "memory_choice":
                    seen_choice = True
                    if tap is None:
                        return {"page": page, "choice": dict(LAST_CHOICE), "memories": memories}
                if page == "ap_shortage" and not refilled:
                    result = ap_refill.refill_fast(shoot, click, ocr_boxes, dialog_header, log=lambda msg: print(msg, file=sys.stderr))
                    if not result["ok"]:
                        return {"page": page, **result}
                    refilled = True
                    last = None
                    time.sleep(0.65)
                    continue
                close_x = any(t.strip() in ("x", "×", "X", "✕") and b[1] > 1100 for t, _, b in boxes)
                th = OcrGate.thumb(img)
                slow["same"] = slow["same"] + 1 if slow["thumb"] is not None and OcrGate.diff(th, slow["thumb"]) < OcrGate.SAME else 0
                slow["thumb"] = th
                if action == "settle" and regen_done and slow["same"] >= SETTLE_STUCK:
                    # 回忆定下后画面连着几轮不变（1-e：活动弹窗「あさり先生シール」点 × / 次へ 重合处不动，超时）：
                    # 点画面上任何一个「×」/「閉じる」，没有就点弹窗底部按钮
                    shut = next((b for t, _, b in boxes if t.strip() in ("x", "×", "X", "✕", "閉じる", "OK")), None)
                    click(*((int((shut[0] + shut[2]) / 2), int((shut[1] + shut[3]) / 2)) if shut else SETTLE_POPUP_TAP))
                    slow["same"] = 0
                    time.sleep(0.65)
                    continue
                if tap is None and page == "settlement_review" and (regen_done or close_x):
                    regen_done = regen_done or close_x  # 底部有「×」= 回忆已定（新进程续跑时也能接上快收尾）
                    # 回忆定下之后：一直点「×」和它后面模糊的「次へ」圆角框重合处，有 × 关 ×、没有就点到次へ，一路回到首页
                    #（实机 codex-20261107-6 第 1 局：あさり先生シール获得页点左上角不动，点这里 36 步回首页）
                    tap = SETTLE_TAP
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
                    # 开局：同一类页面只点一次（不按 OCR 文字去重——淡出中的帧文字会变，第 2 局曾疑似因此把偶像页「次へ」
                    # 补点到了紧接着的开始确认页「プロデュース開始」上，两者位置几乎重合）
                    signature = (page,) if action == "open" else (page, identity)
                    if signature != last or page == "settlement_review":
                        if action == "open" and not same_frame(img, shoot()):
                            last = None  # OCR 期间画面变了：这一下不点，下一轮按新画面重新判断
                            time.sleep(0.65)
                            continue
                        click(*tap)
                        last = signature
                time.sleep(0.65)
            return {"page": "timeout", "memories": memories}
        live = {"guard": None, "ocr_at": -1e9}

        def live_step(img):
            """横屏 LIVE（PlayCover 横屏黑屏）：黑屏持续就切全屏（hiro.loop.landscape：全屏按钮 → AXFullScreen → 菜单，逐个核对），
            LIVE 结束回竖屏后退出全屏；横屏有画面时每 2s OCR 一次，认到 TAP TO START 就点。
            返回 None = 不是 LIVE，交给 auto_decide；否则返回 (kind, tap 或 None)。切换失败 / 屏幕锁住时停下交给使用者。"""
            from hiro.loop.controller import live_start_tap
            from hiro.loop.landscape import LandscapeGuard

            g = live["guard"] = live["guard"] or LandscapeGuard(shoot=device.screenshot)
            if g.last_result in LIVE_FAIL:
                if not g.failure_cleared(img):
                    raise HoldPage(live_fail_reason(g.last_op, g.last_result))
                if g.last_op == "exit":
                    live["exited"] = True
            if img is None:
                return None
            switched = g.tick(img)
            if g.last_result in LIVE_FAIL:
                raise HoldPage(live_fail_reason(g.last_op, g.last_result))
            if switched:
                return "live_fullscreen", None
            if live.get("ended") and not live.get("exited") and time.monotonic() - live["ended"] >= LIVE_EXIT_AFTER:
                # 跳过 LIVE 后直接回竖屏：fn+shift+F（landscape.METHODS 退出时先试它），失败下一帧按 LIVE_FAIL 停下交给使用者
                live["exited"] = True
                g.exit_now()
                if g.last_result in LIVE_FAIL:
                    raise HoldPage(live_fail_reason(g.last_op, g.last_result))
                return "live_portrait", None
            if not g.in_live(img):
                return None
            if img.shape[1] > img.shape[0] and time.monotonic() - live["ocr_at"] >= LIVE_OCR_EVERY:
                live["ocr_at"] = time.monotonic()
                boxes = ocr_boxes(img)
                if tap := live_start_tap(img, boxes):
                    live["started"] = time.monotonic()
                    return "live_start", (tap[0], tap[1] - top_bar(img))  # 帧坐标 → 屏幕坐标（live_click 按屏幕点）
                if tap := live_skip_step(img, boxes, live, click):
                    return tap
            return "live", None

        gate = OcrGate(ocr_boxes)

        def advance():
            img = shoot()
            if auto_route:
                result = live_step(img)
                if result is None:
                    try:
                        result = gate.decide(img, lambda im, ocr: auto_decide(im, ocr=ocr, quick=quick_page, fallback=fallback_page,
                                                                             header=dialog_header), dialog_header)
                    except HoldPage as hold:
                        if str(hold).startswith("行动页"):
                            gate.ocr_thumb = None  # N2：行动页淡入帧变化很小，OCR 会沿用旧读数；停点确认期间每帧重读
                        # 使用者建议：这几类停点在确认退出的那几轮里照样点角落——是获得动画就跳过，真是选择页 (12,12) 也无害
                        if (str(hold).split("：")[0] in TAP_THROUGH and dialog_header(img) is None
                                and str(hold) != HELD["reason"]):  # 已确认的停点不再点
                            click(*ADVANCE_TAP)
                        raise
                elif result[1] is None:
                    return result if result[0] != "live" else None  # LIVE 演出中：不点，watch 只打印一次 waiting
                if result is not None:
                    (live_click if result[0].startswith("live_") else click)(*result[1])
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

        if action == "settle-lead":
            # 使用者 2026-11-07（1-h）：结算前段不用 settle 自己的 OCR 判页——偶像名片「TAPで次へ」被当未知弹窗停下、
            # 优胜 LIVE 黑屏要另起 watch。改用 burst / watch 同一套规则（auto_route，含 LIVE 全屏 / 跳过 / 回竖屏）一路推进，
            # 推到回忆页（「结算回忆」停点）再交给 settle。别的停点连续 3 次同一个就交回代理
            deadline = time.monotonic() + SETTLE_LEAD_MAX
            taps, held, same = 0, None, 0
            while time.monotonic() < deadline:
                try:
                    result = advance()
                except HoldPage as hold:
                    reason = str(hold)
                    if reason.startswith("结算回忆"):
                        return {"ok": True, "taps": taps, "hold": reason}
                    same = same + 1 if reason == held else 1
                    held = reason
                    if same >= 3:
                        return {"ok": False, "taps": taps, "hold": reason}
                    time.sleep(ADVANCE_EVERY)
                    continue
                held, same = None, 0
                if result is not None:
                    taps += 1
                time.sleep(ADVANCE_EVERY)
            return {"ok": False, "taps": taps, "why": f"{SETTLE_LEAD_MAX:.0f}s 内没到回忆页"}
        if watch_tick is not None:
            gap = watch_gap(ADVANCE_EVERY)
            print(json.dumps({"t": round(time.time(), 2), "action": "start", "gap": gap}), flush=True)
            while watch_tick(advance):
                time.sleep(gap)
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


LOCK_WAIT = 30.0  # 命令行等批次文件锁的上限（秒）


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
    global AUTO_START
    AUTO_START = start_allowed(state)
    cmd = args.command
    if cmd == "debug-retries":
        plan = parse_debug_retries(args.plan)
        if plan:
            state["debug_retries"] = plan
        else:
            state.pop("debug_retries", None)
        return {"ok": True, "debug_retries": plan, "used": (state.get("active") or {}).get("debug_retries_used")}
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
    elif cmd in {"change-scan", "change-pick", "shop-scan", "shop-buy", "shop-refresh", "interval-end", "shop-auto", "drink-drop", "drink-overflow", "customize",
                 "memory-abilities", "deck-sync"}:
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
            if cmd == "deck-sync":
                out = deck_sync(device, ledger, why=args.why)
                if out.get("diff") is not None:
                    persist()
                return out
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
            if cmd == "interval-end":
                return interval_end(device)
            if cmd == "shop-refresh":
                return batch_shop.refresh(device)
            if cmd in {"shop-auto", "shop-buy"} and ledger is not None:
                if synced := L.sync_drinks(ledger, batch_shop.read_bar(device), why=cmd):
                    persist()
                    print(json.dumps({"action": "drink_sync", "diff": synced}, ensure_ascii=False), file=sys.stderr)
            if cmd == "shop-auto":
                def on_buy(name):
                    if ledger is not None:
                        L.drink_add(ledger, name, why="shop-auto 买高价值饮料")
                        persist()

                def make_room():
                    # 丢一瓶低价值饮料：账本和画面都认得、都不是高价值的才丢（HIRO_SHOP_DROP_LOW=0 关掉）
                    if ledger is None or os.environ.get("HIRO_SHOP_DROP_LOW", "1") == "0":
                        return None
                    bar = batch_shop.read_bar(device)
                    for i, name in enumerate(ledger["drinks"]):
                        seen = bar[i].get("name") if i < len(bar) and bar[i].get("state") == "drink" else None
                        if not name or not seen or L.norm(seen) != L.norm(name) or batch_shop.high_value(name):
                            continue
                        out = batch_shop.drop(device, i)
                        if not out["ok"]:
                            return None
                        L.drink_remove(ledger, i, why=f"shop-auto 腾格子买高价值饮料（画面 {out['drink']}）")
                        persist()
                        return name
                    return None

                free = (lambda: ledger["drinks"].count(None)) if ledger is not None else (lambda: 4)
                out = batch_shop.auto(device, free_slots=free, on_buy=on_buy, max_refresh=args.max_refresh, make_room=make_room)
                if out.get("ok") and ledger is not None and batch_rules.get("shop.deck_sync", True):
                    # 使用者 2026-11-07：退出商店 / 轮间前记账——补给完、按「終了」前整副牌组对账（相談在 R1 前，轮间在 R2 前）
                    where = {"interval": "R2 前（轮间）"}.get(out.get("page"), "R1 前（相談）")
                    try:
                        out["deck_sync"] = deck_sync(device, ledger, why=where)
                    except Exception as e:  # noqa: BLE001  对账失败不挡补给
                        out["deck_sync"] = {"ok": False, "why": f"deck-sync 出错：{e}"}
                    if (out["deck_sync"] or {}).get("diff") is not None:
                        persist()
                return out
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
                out["slot"] = args.slot  # 对外和 --slot 一样从 1 数（原来输出 0 起的格号，1-d 代理看到 --slot 2 输出 1）
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
                    # 游戏里已经换了：记账失败也不抛错（否则整次换卡丢账），写进 ledger_todo 交给代理补
                    if todo := L.record_change(ledger, out["changed"], ledger.get("pending_add"), why="授業换卡"):
                        out["ledger_todo"] = todo
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
        if ledger is not None:
            sys.path.insert(0, str(ROOT / "tools"))
            import batch_ledger as L

            if deck is None:
                deck = L.to_deck(ledger)
        if deck is None:
            base, change = state.get("veil_base_deck"), active.get("veil_deck_change")
            if base is None or change is None:
                raise ValueError("先 memory-load 建本局账本，或登记基准牌组和本局变卡记录，或用 --deck 提交本轮完整清单")
            deck = apply_deck_change(base, change)
        # 开考前饮料栏快照（每轮第一次 exam 时记）：再挑戦时游戏把饮料栏恢复到开考前，账本也恢复（1-f 代理手动改的）
        snaps = active.setdefault("exam_start_drinks", {})
        if ledger is not None and str(args.round) not in snaps:
            # 每轮开考前读一次实机饮料栏，以画面为准写账本（使用者 2026-11-07：饮料识别可靠，考前读数说了算；
            # 中途靠获得演出逐条记的账只作参考。验证局 1-i：公開レッスン 白送的ブーストエキス 没演出可认，账本少一瓶）。
            # 续跑同一轮不再读：考试中途喝掉的由考试记忆管
            if synced := exam_drink_sync(ledger, args.round):
                persist()
                print(json.dumps({"action": "drink_sync", "round": args.round, "diff": synced}, ensure_ascii=False), file=sys.stderr)
            snaps[str(args.round)] = list(ledger["drinks"])
            persist()
        try:
            out = run_round(args.round, deck, f"batch-{args.batch}-{args.run}-exam{args.round}",
                            drinks=ledger["drinks"] if ledger else None,
                            abilities=ledger.get("abilities") if ledger else None,
                            retry_drinks=snaps.get(str(args.round)))  # fmt: skip
        except ResumeBlocked as e:
            raise ValueError(str(e)) from e
        if out.get("trace"):
            # 可信 trace 自检（使用者 2026-11-07）：结论写进 <trace>/trust.json，这里只带摘要；自检出错不影响考试结果
            try:
                sys.path.insert(0, str(ROOT / "tools" / "audit"))
                import trace_trust

                t = trace_trust.check(Path(out["trace"]))
                out["trust"] = {"trusted": t["trusted"], "steps": t["steps"], "bad_steps": t["bad_steps"], "first": t["problems"][:3]}
            except Exception as e:  # noqa: BLE001
                out["trust"] = {"error": f"{type(e).__name__}: {e}"}
        if ledger is not None:
            snapshot = snaps.get(str(args.round))
            restored_drinks = bool(out.get("retry") and snapshot is not None)
            if restored_drinks:
                L.drink_set(ledger, snapshot, why=f"R{args.round} 再挑戦：饮料栏恢复到开考前")
            if out.get("drinks") is not None:
                L.after_exam(ledger, out["drinks"], why=f"R{args.round} 考完（喝掉的去掉、往左挤）")
                out["ledger_drinks"] = ledger["drinks"]
            if restored_drinks or out.get("drinks") is not None:
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
            lead = device_action("settle-lead", auto_route=True)
            if not lead.get("ok") and str(lead.get("hold") or "").startswith("考试结果"):
                return {"page": "hold", "lead": lead}  # 还在结果页 / 再挑戦確認：交给代理（再挑戦由使用者定），不进结算
            # 其他停点（首页、确认框等）照旧交给 settle 自己的流程（续跑时可能已过了回忆页）
            outcome = device_action("settle")
            outcome["lead"] = lead
            if outcome["page"] != "home":
                return outcome
        record = {"run": args.run, "result": args.result}
        if active.get("ledger") is not None:
            record["ledger"] = active["ledger"]  # 本局账本（含逐条日志）随结算记录存档，复盘用（验证局 1-i 结算后账本丢了）
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


ACTION_HOLD_S = float(batch_rules.get("watch.action_hold_s", 10.0))  # N2：行动页停点持续这么久才报（首日淡入帧 7.7s 后自己认出）
CHOICE_HOLDS = ("差し入れ 选饮料", "领取选择", "选择页", "饮料栏操作", "相談 / 商店", "轮间客制化")
CHOICE_SKIP_S = 20.0  # 选择页 / 商店处理完这么久内的饮料获得演出是那一次的（已由代理 / prep 记账），不重复记
CHOICE = {"at": -1e9}


def exam_drink_sync(ledger, round_no, read=None) -> list[str]:
    """考试出牌页读饮料栏 → batch_ledger.sync_drinks（读清的格以画面为准，读不清的保留账本）。返回改动说明。"""
    import batch_ledger as L

    if read is None:
        import batch_shop
        from hiro.device import connect

        device = connect("maatools")
        try:
            observed = batch_shop.read_bar(device)
        finally:
            device.stop()
    else:
        observed = read()
    return L.sync_drinks(ledger, observed, why=f"R{round_no} 开考前读饮料栏")


def deck_sync(device, ledger, *, why: str) -> dict:
    """第二层（使用者 2026-11-07）：在有牌组按钮的培育页（相談 / 选行动页 / 轮间）打开「所持スキルカード」一览，
    整副读全后和账本对账（batch_ledger.sync_deck：漏记的补、强化差「+」的改、多出的只记）。读不全不改账本。"""
    import batch_ledger as L

    from hiro.produce import deck_review
    from hiro.produce.ops import ocr_boxes
    from hiro.state.catalog import catalog
    from hiro.state.knowledge import is_once

    if ledger is None:
        return {"ok": False, "why": "本局没有账本"}
    from hiro.vision.hif import is_hif_exam

    text = os.environ.get("HIRO_DECK_TEXT") == "1"
    if is_hif_exam(device.screenshot()) and not text:
        # 考试里的一览按 保留 / 手札 / 山札 / 捨札 / 除外 分组，网格读法对不上（1-e R2 出牌页跑过：滑过头、6 张认不出）
        return {"ok": False, "why": "考试页的牌组一览是分组的，网格读法不能对账；在相談 / 选行动页 / 轮间跑，或开 HIRO_DECK_TEXT=1", "diff": None}
    deck_review.SAVE_DIR = ROOT / "reports" / "live" / "deck-list"

    def ocr_texts(img):
        return [(t, ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)) for t, _, b in ocr_boxes(img)]

    reader = None
    if text:
        # HIRO_DECK_TEXT=1（默认关，使用者 2026-11-07）：一览切到列表显示，按卡名文字对账；考试页的分组一览也能读
        from hiro.produce import deck_text

        reader = lambda: deck_text.read_list(  # noqa: E731
            shoot=device.screenshot, click=device.click, swipe=lambda x1, y1, x2, y2, d: device.swipe(x1, y1, x2, y2, d),
            ocr_boxes=ocr_boxes, view_diff=deck_review._view_diff, save=deck_review._save)
    r = deck_review.review_deck(shoot=device.screenshot, click=device.click,
                                swipe=lambda x1, y1, x2, y2, d: device.swipe(x1, y1, x2, y2, d), ocr_texts=ocr_texts,
                                text_reader=reader)
    if reader is not None:
        t = r.get("text") or {}
        out = {"ok": bool(t.get("complete")) and r["closed"], "opened": r["opened"], "closed": r["closed"], "mode": "text",
               "deck_size": t.get("total"), "problems": r["problems"], "diff": None, "groups": t.get("groups")}
        if not out["ok"]:
            return out  # 读不全不改账本
        screen = [(n, _once_by_name(n)) for n in t["cards"]]
        out["diff"] = L.sync_deck(ledger, screen, why=why)
        out["cards"] = len(screen)
        return out
    out = {"ok": r["deck"] is not None and r["closed"], "opened": r["opened"], "closed": r["closed"],
           "deck_size": r["deck_size"], "problems": r["problems"], "diff": None}
    if r["deck"] is None:
        return out
    screen = []
    for c in r["deck"]:
        try:
            name = catalog().card(c["card_id"], int(c.get("upgrade") or 0))["name"]
        except Exception:  # noqa: BLE001
            out["ok"] = False
            out["problems"].append(f"主数据查不到 {c.get('card_id')}+{c.get('upgrade')}，不对账")
            return out
        screen.append((name, is_once(c["card_id"])))
    out["diff"] = L.sync_deck(ledger, screen, why=why)
    out["cards"] = len(screen)
    return out


def _once_by_name(name: str) -> bool | None:
    """卡名（带「+」）→ 是否レッスン中1回（主数据 playMovePositionType=Lost）；查不到 None。"""
    try:
        from hiro.state.context import catalog

        row = catalog().db.execute("SELECT playMovePositionType FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return None if row is None else "Lost" in str(row[0])


def _just_booked(ledger, name: str, last: int = 6) -> bool:
    """账本最近几条记录里有这个名字（去「+」比）：选择页 / prep 刚记过它。没有账本时按旧规则当作记过。"""
    if ledger is None:
        return True
    base = "".join(unicodedata.normalize("NFKC", name).split()).rstrip("+")
    for entry in (ledger.get("log") or [])[-last:]:
        names = [entry.get(k) for k in ("card", "drink")]
        if any(isinstance(n, str) and n.rstrip("+") == base for n in names):
            return True
    return False


def drain_acquired(state, active, persist) -> list[dict]:
    """把 auto_decide 攒下的饮料获得演出记进账本（清单 10），返回要打印的日志行。"""
    out = []
    ledger = active.get("ledger")
    for e in ACQUIRED:
        if not e.pop("new", False):
            continue
        card = e.get("kind") == "card"
        action, key = ("card_acquired", "card") if card else ("drink_acquired", "drink")
        if e["t"] - CHOICE["at"] < CHOICE_SKIP_S and _just_booked(ledger, e["name"]):
            # 只跳过选择页刚记过的那张 / 那瓶；别的名字照记（1-f：换卡阶段 2 后 11s 支援卡事件给的 ティーパーティ+ 被当成换卡的演出跳过）
            out.append({"action": action, key: e["name"], "ledger": "choice"})
            continue
        if ledger is None:
            out.append({"action": action, key: e["name"], "ledger": "no_ledger"})
            continue
        sys.path.insert(0, str(ROOT / "tools"))
        import batch_ledger as L

        if card:
            from hiro.state.knowledge import is_once

            res = L.acquire_card(ledger, e["name"], is_once(e.get("card_id")),
                                 why="获得演出（事件等，watch 自动记）")
            persist()
            out.append({"action": action, key: e["name"], "ledger": res})
            continue
        res = L.acquire_drink(ledger, e["name"], why="获得演出（事件 / 公開レッスン 等，watch 自动记）")
        persist()
        out.append({"action": "drink_acquired", "drink": e["name"], "ledger": res})
    del ACQUIRED[:-1]  # 留最后一条做同名去重
    return out


STAY = _env_flag("HIRO_WATCH_STAY", "watch.stay")  # 常驻：停点不退出（使用者 2026-11-07），设 0 恢复退出码 3
HELD = {"reason": None, "prep": None}
EMPTY = {"seen": False}
PREP = _env_flag("HIRO_WATCH_PREP", "prep.enabled")  # 前六周固定流程（使用者 2026-11-07），设 0 关掉


def prep_step(path, state):
    sys.path[:0] = [str(ROOT / "tools")]
    import batch_prep
    from hiro.device import connect
    from hiro.produce.ops import ocr_boxes

    device = connect("maatools")
    try:
        return batch_prep.handle(device, ocr_boxes, change, state, path.stem, state["active"]["run"],
                                 persist=lambda: save(path, state), log=lambda m: print(m, file=sys.stderr))
    except Exception as e:  # noqa: BLE001 —— 固定流程出错就按原样停下交给代理
        print(f"prep 失败：{e!r}", file=sys.stderr)
        return None
    finally:
        device.stop()


def watch(path, run):
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]
    from hiro.loop import pause_flag

    last_idle = False
    holds: list[str] = []
    streak = {"reason": None, "since": 0.0}

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
            from hiro.device import MaaToolsEmptyFrame

            DEBUG_RETRY["plan"] = state.get("debug_retries")
            DEBUG_RETRY["used"] = dict(active.get("debug_retries_used") or {})
            try:
                result = advance() if active.get("phase") == "playing" else None
            except MaaToolsEmptyFrame as e:
                # 切屏 / 上滑：设备层已等过一阵还是空帧，常驻 watch 不退出，报一次后接着等
                if not EMPTY["seen"]:
                    EMPTY["seen"] = True
                    print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": "screen_lost",
                                      "reason": str(e)}, ensure_ascii=False), flush=True)
                time.sleep(2.0)
                return True
            except HoldPage as hold:
                # 同一个停点连续 HOLD_CONFIRM 次才退出：过场里半帧的行动页 / 选择页不算
                holds.append(str(hold))
                if streak["reason"] != holds[-1]:
                    streak.update(reason=holds[-1], since=time.monotonic())
                if PREP and len(holds) >= 2 and holds[-1] == holds[-2] and HELD["prep"] != holds[-1]:
                    HELD["prep"] = holds[-1]  # 同一停点只试一次固定流程
                    # 前六周固定流程（tools/batch_prep.py）：差し入れ / 相談 / 换卡 / 选饮料选卡，watch 自己做完不唤醒代理
                    done = prep_step(path, state)
                    if done:
                        holds.clear()
                        if any(w in done for w in ("饮料", "技能卡", "换卡")):
                            CHOICE["at"] = time.monotonic()  # 选择页之后的获得演出已由 prep 记账，不再重复记
                        print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": "prep", "done": done},
                                         ensure_ascii=False), flush=True)
                        return True
                waited = time.monotonic() - streak["since"]
                if (len(holds) >= HOLD_CONFIRM and len(set(holds[-HOLD_CONFIRM:])) == 1
                        and (not holds[-1].startswith("行动页") or waited >= ACTION_HOLD_S)):
                    if not STAY:
                        raise
                    if HELD["reason"] != holds[-1]:
                        # 常驻：停点只报一次，不退出也不再点；页面一变（代理处理完）就自动接着推进，不丢动画时间
                        HELD["reason"] = holds[-1]
                        print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": "hold",
                                          "reason": holds[-1]}, ensure_ascii=False), flush=True)
                    del holds[:-HOLD_CONFIRM]
                return True
            holds.clear()
            streak["reason"] = None
            HELD["prep"] = None
            EMPTY["seen"] = False
            if HELD["reason"] is not None:
                if HELD["reason"].split("：")[0] in CHOICE_HOLDS:
                    CHOICE["at"] = time.monotonic()
                HELD["reason"] = None
                print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": "resume"}), flush=True)
            if result is not None:
                kind, tap = result
                extra = {}
                if kind in ("debug_retry_confirm", "debug_defeat_retry") and DEBUG_RETRY["pending"] is not None \
                        and DEBUG_RETRY["pending"]["round"]:
                    rnd = DEBUG_RETRY["pending"]["round"]
                    used = active.setdefault("debug_retries_used", {})
                    used[rnd] = used.get(rnd, 0) + 1
                    save(path, state)
                    extra = {"round": int(rnd), "used": used[rnd], "plan": state["debug_retries"].get(rnd)}
                print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": kind, "tap": tap, **extra},
                                 ensure_ascii=False), flush=True)
            elif not last_idle:
                print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), "action": "waiting"}), flush=True)
            last_idle = result is None
            for line in drain_acquired(state, active, lambda: save(path, state)):
                print(json.dumps({"t": round(time.time(), 2), "run": active.get("run"), **line}, ensure_ascii=False), flush=True)
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
    p = sub.add_parser("init")
    p.add_argument("total", type=int)
    p.add_argument("--debug-retries", help="debug：本战每局主动再挑戦几次「R1,R2」（如 2,1 = 打 3 次 R1、2 次 R2，攒 trace）")
    sub.add_parser("debug-retries", help="debug：改本批次的主动再挑戦计划「R1,R2」，off 关掉").add_argument("plan")
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
    sub.add_parser("interval-end", help="轮间主画面：固定像素点「終了」→「本戦へ」，等左上「インターバル」消失").add_argument("run", type=int)
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
    p = sub.add_parser("deck-sync", help="相談 / 选行动页 / 轮间：打开牌组一览整副读一遍，和账本对账（漏记补上、强化按画面、多出只记）")
    p.add_argument("run", type=int)
    p.add_argument("--why", default="考前对账")
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
            # watch 每步（含前六周固定流程、几秒到十几秒）都拿着这把锁；原来只等 2s，等不到报 Errno 35，
            # 代理当成命令静默没生效（1-d `ledger-edit --drink-set`）。改成最多等 LOCK_WAIT 秒，等不到明确说是 watch 占着
            deadline = time.monotonic() + LOCK_WAIT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(f"批次文件被占用 {LOCK_WAIT:.0f}s（watch 正在执行一步），本命令没有执行，稍后重跑") from None
                    time.sleep(0.05)
            if args.command == "init":
                if args.total < 1 or path.exists():
                    raise ValueError("总次数必须为正，且批次 ID 尚未使用")
                state = {"total": args.total, "preflight": None, "active": None, "completed": []}
                if args.debug_retries and (plan := parse_debug_retries(args.debug_retries)):
                    state["debug_retries"] = plan
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
            **({"debug_retries": state["debug_retries"]} if state.get("debug_retries") else {}),
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as e:
        print(f"批量育成停止：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
