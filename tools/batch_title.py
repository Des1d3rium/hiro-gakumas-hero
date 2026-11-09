"""「データ更新」「日付変更」弹窗 → 点「タイトルへ」→ 标题页 → 下载确认 → 关公告 → 首页 →（培育中）再開。

使用者 2026-11-08：「允许一切data更新和日期更新导致的返回主页。」「因为data更新或日期更新回到主页之后，连续点击与settle收尾
回到主页一致的x按钮（接近home键）。使用和watch一样的逻辑：这个点击始终无害。」原来只写在监控 prompt 里，代码把这两种弹窗
当危险字样停下（codex-20261108-3-b 开工前就停在「データ更新があります。」上）。

实机顺序（codex-20261108-3-b 开工前，代理手点，2026-11-08 19:49–19:53，客户端 Ver. 3.5.3）：
  「データ更新 / データ更新があります。/ ✕タイトルへ (353,1162)」→ 点タイトルへ
  → 标题页「TAP TO START」（OCR 读成「AP,TO START」）→ 点 (360,900)
  → 「データダウンロード / ゲームデータのダウンロードを行います / 178.43MB / キャンセル / 決定 (523,1161)」→ 点決定
  → 公告（新偶像、有償ガシャ、有償パック）底部「x」(361,1188) → 点 ✕（= 结算收尾的 SETTLE 点）→ 首页。

不变的底线：
- 「通信エラー」框不点「タイトルへ」：直接停下（HoldPage），本模块什么都不点。
- 「機種変更」「データ連携」弹窗、利用規約、破棄 / リタイア / 别的设备：停下交给使用者。
- 下载确认只在本模块刚点过「タイトルへ」/ TAP TO START 之后的恢复窗口里点，而且框里不能有ジュエル / 有償 / 購入。
- 公告只点 ✕ / 閉じる，有償字样的公告也只点 ✕。
- 回首页后（培育中）只点「プロデュース」和「プロデュース再開」框里的「再開する」；框里有破棄 / 開始する 时停下。
"""

from __future__ import annotations

import time
import unicodedata

UPDATE_TITLES = ("データ更新",)
DATE_TITLES = ("日付変更", "日付が変わりました")
COMM_ERROR = ("通信エラー", "通信工ラー", "通信中にエラー")  # OCR 偶尔把「エ」读成「工」（hiro.loop.comm_error）
TO_TITLE = "タイトルへ"
# 无论在哪一步都停下交给使用者的字样
STOP_WORDS = ("機種変更", "利用規約", "破棄", "リタイア", "別の端末", "他の端末", "別端末", "引き継ぎ")
PAY_WORDS = ("ジュエル", "有償", "購入", "課金")
CLOSE_MARKS = ("x", "×", "X", "✕", "✖")
CLOSE_TAP = (360, 1170)  # = hif_buttons.SETTLE：结算收尾回首页点的「×」(360,1188) 与「次へ」重合处
TITLE_MARKS = ("プロデューサーID", "BANDAI", "NAMCO", "QUALIARTS", "IDOLM@STER")  # 标题页才有；LIVE 的 TAP TO START 没有
TITLE_TAP = (360, 900)  # 标题页「TAP TO START」上方的空处：标题页点哪里都开始（实机代理点的就是这里）
PRODUCE_REGION = (180, 840, 545, 1090)  # 首页「プロデュース」按钮
WINDOW_S = 600.0  # 点了タイトルへ / TAP TO START 之后这么久内算恢复中（下载 178MB 约 30s）
HOME_TAP_EVERY = 6.0  # 首页点「プロデュース」的间隔：点了没反应再点

STATE: dict = {"since": None, "home_tap": -1e9}


class TitleHold(Exception):
    """恢复途中碰到要使用者处理的画面。"""


def _norm(t: str) -> str:
    return "".join(unicodedata.normalize("NFKC", t).split())


def _center(b) -> tuple[int, int]:
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2


def recovering(now: float) -> bool:
    since = STATE["since"]
    if since is not None and now - since > WINDOW_S:
        STATE["since"] = None
    return STATE["since"] is not None


def _start(now: float) -> None:
    STATE["since"] = now


def done() -> None:
    STATE["since"] = None


def step(boxes, *, resume: bool, now: float | None = None):
    """一帧 OCR → (kind, tap)、("title_wait", None) 或 None（不归本模块管，照旧判页）。要停下时抛 TitleHold。
    resume：回首页后要不要接着点「プロデュース」→「再開する」（watch 培育中 = True；open 开新局 = False，首页交回 open 自己点）。"""
    now = time.monotonic() if now is None else now
    texts = [(_norm(t), b) for t, _, b in boxes]
    flat = "".join(t for t, _ in texts)

    def has(*words):
        return any(w in flat for w in words)

    def button(word, region=(0, 0, 720, 1280)):
        x1, y1, x2, y2 = region
        hits = [_center(b) for t, b in texts if word in t and x1 <= _center(b)[0] <= x2 and y1 <= _center(b)[1] <= y2]
        return hits[0] if len(hits) == 1 else None

    if has(*COMM_ERROR):
        raise TitleHold("通信エラー：不点「タイトルへ」，交给使用者")
    title_popup = has(*UPDATE_TITLES) or has(*DATE_TITLES)
    if title_popup and has(TO_TITLE):
        if has(*STOP_WORDS, *PAY_WORDS):
            raise TitleHold("数据更新 / 日期变更框里有危险字样，不点")
        tap = button(TO_TITLE, region=(0, 1050, 720, 1280))
        if tap is None:
            return None  # 按钮没读全：下一帧再判
        _start(now)
        return ("title_update" if has(*UPDATE_TITLES) else "title_date"), tap
    if has(*STOP_WORDS):
        if recovering(now):
            raise TitleHold(f"回标题途中出现要使用者处理的画面：{[w for w in STOP_WORDS if w in flat]}")
        return None
    upper = flat.upper()
    if "TOSTART" in upper and any(w in upper for w in TITLE_MARKS) and not has(*PAY_WORDS):
        # 标题页（OCR 实读「AP,TO START」）：点哪里都开始。通信エラー只能「閉じる」回标题的那条路也会到这里，同样允许
        _start(now)
        return "title_start", TITLE_TAP
    if not recovering(now):
        return None
    if has("ダウンロード"):
        if has("決定") and has("ゲームデータ") and not has(*PAY_WORDS):
            if tap := button("決定", region=(360, 1080, 720, 1230)):
                return "title_download", tap
        return "title_wait", None  # 下载中 / 按钮没读全：不点
    home = (button("ホーム", region=(250, 1165, 470, 1279)) and button("プロデュース", region=PRODUCE_REGION)
            and has("コンテスト", "ミッション"))
    if home:
        if not resume:
            done()
            return None  # 开新局：首页交回 open 自己点「プロデュース」
        if now - STATE["home_tap"] < HOME_TAP_EVERY:
            return "title_wait", None
        STATE["home_tap"] = now
        return "title_produce", button("プロデュース", region=PRODUCE_REGION)
    if has("プロデュース再開"):
        if has("開始する") or has(*PAY_WORDS):
            raise TitleHold("「プロデュース再開」框里有開始する / 付费字样，不点")
        tap = next((_center(b) for t, b in texts if t == "再開する"), None)
        if tap is None:
            return "title_wait", None
        done()
        return "title_resume", tap
    if any(t in CLOSE_MARKS and b[1] > 1100 for t, b in texts):
        return "title_close", CLOSE_TAP  # 公告 / 登录奖励的 ✕：按结算收尾同一个点，有償公告也只点 ✕
    if tap := button("閉じる", region=(0, 1000, 720, 1280)):
        if not has(*PAY_WORDS):
            return "title_close", tap
    return None
