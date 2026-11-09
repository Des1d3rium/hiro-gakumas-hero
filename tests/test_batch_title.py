"""数据更新 / 日期变更回标题恢复（tools/batch_title.py）。OCR 文字取自 codex-20261108-3-b 开工前代理手点时的实读（2026-11-08）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_hif as B  # noqa: E402
import batch_title as T  # noqa: E402


def box(text, x, y):
    return (text, 1.0, (x - 40, y - 12, x + 40, y + 12))


UPDATE = [box("データ更新", 138, 975), box("データ更新があります。", 187, 1057), box("✕タイトルへ", 353, 1162)]
DATE = [box("日付変更", 138, 975), box("日付が変わりました。", 187, 1057), box("タイトルへ", 360, 1159)]
TITLE = [box("Ver. 3.5.3", 78, 43), box("プロデューサーID：タップで表示", 180, 78), box("データ連携", 96, 121),
         box("BANDAI", 632, 52), box("学国アイドルマスター", 363, 661), box("AP,TO START", 370, 1035), box("メニュー", 647, 1240)]
DOWNLOAD = [box("→ データダウンロード", 181, 820), box("ゲームデータのダウンロードを行います", 280, 902), box("178.43MB", 360, 978),
            box("Xキャンセル", 212, 1161), box("決定", 523, 1161)]
NOTICE_PAID = [box("2.5th アニバーサリー前夜祭パック②", 358, 936), box("購入可能回数1回", 139, 981), box("有償", 272, 1071),
               box("3,000", 383, 1059), box("x", 361, 1188)]
HOME = [box("プレゼント", 650, 499), box("ミッション", 651, 617), box("プロデュース", 360, 1002), box("ショップ", 635, 992),
        box("アイドル", 223, 1175), box("ホーム", 359, 1194), box("コンテスト", 496, 1176)]
RESUME = [box("プロデュース再開", 360, 500), box("キャンセル", 212, 1161), box("再開する", 523, 1161)]
COMM = [box("通信エラー", 138, 975), box("通信中にエラーが発生しました", 300, 1057), box("タイトルへ", 212, 1161), box("リトライ", 523, 1161)]


@pytest.fixture(autouse=True)
def fresh():
    T.done()
    T.STATE["home_tap"] = -1e9
    yield
    T.done()


def test_full_update_flow_resumes_produce():
    assert T.step(UPDATE, resume=True, now=0) == ("title_update", (353, 1162))
    assert T.step(TITLE, resume=True, now=5) == ("title_start", T.TITLE_TAP)
    assert T.step(DOWNLOAD, resume=True, now=10) == ("title_download", (523, 1161))
    assert T.step([box("ダウンロード中", 360, 900), box("45%", 360, 950)], resume=True, now=20) == ("title_wait", None)
    assert T.step(NOTICE_PAID, resume=True, now=60) == ("title_close", T.CLOSE_TAP)  # 有償公告也只点 ✕
    assert T.step(HOME, resume=True, now=70) == ("title_produce", (360, 1002))
    assert T.step(HOME, resume=True, now=72) == ("title_wait", None)  # 点了没反应：隔 HOME_TAP_EVERY 再点
    assert T.step(RESUME, resume=True, now=75) == ("title_resume", (523, 1161))
    assert not T.recovering(76)


def test_date_change_popup():
    assert T.step(DATE, resume=True, now=0) == ("title_date", (360, 1159))
    assert T.recovering(1)


def test_open_mode_hands_home_back():
    T.step(UPDATE, resume=False, now=0)
    assert T.step(HOME, resume=False, now=30) is None
    assert not T.recovering(31)


def test_comm_error_never_taps_title():
    with pytest.raises(T.TitleHold, match="通信エラー"):
        T.step(COMM, resume=True, now=0)
    assert not T.recovering(1)


def test_device_change_popup_stops_during_recovery():
    T.step(UPDATE, resume=True, now=0)
    with pytest.raises(T.TitleHold, match="機種変更"):
        T.step([box("機種変更をした場合は、データ連携", 360, 600), box("閉じる", 360, 1160)], resume=True, now=10)


def test_resume_dialog_with_discard_stops():
    T.step(UPDATE, resume=True, now=0)
    with pytest.raises(T.TitleHold):
        T.step(RESUME + [box("プロデュースデータの破棄", 360, 450)], resume=True, now=10)
    with pytest.raises(T.TitleHold):
        T.step([box("プロデュース再開", 360, 500), box("開始する", 523, 1161)], resume=True, now=11)


def test_download_and_close_only_inside_recovery_window():
    assert T.step(DOWNLOAD, resume=True, now=0) is None
    assert T.step(NOTICE_PAID, resume=True, now=0) is None
    T.step(UPDATE, resume=True, now=0)
    assert T.step(NOTICE_PAID, resume=True, now=T.WINDOW_S + 1) is None  # 窗口过了
    T.step(UPDATE, resume=True, now=0)
    assert T.step(DOWNLOAD + [box("ジュエル", 360, 1000)], resume=True, now=5) == ("title_wait", None)


def test_live_tap_to_start_is_not_title():
    assert T.step([box("TAP TO START", 640, 600), box("作詞", 100, 100)], resume=True, now=0) is None


def _decide(boxes):
    img = np.zeros((1280, 720, 3), np.uint8)
    return B.auto_decide(img, ocr=lambda _: boxes, quick=lambda _: None, fallback=lambda _: None, header=lambda _: None)


def test_watch_taps_title_on_update_popup():
    assert _decide(UPDATE) == ("title_update", (353, 1162))
    assert _decide(TITLE) == ("title_start", T.TITLE_TAP)


def test_watch_holds_on_comm_error():
    with pytest.raises(B.HoldPage, match="通信エラー"):
        _decide(COMM)


def test_watch_rule_off_keeps_old_behaviour(monkeypatch):
    monkeypatch.setenv("HIRO_WATCH_TITLE", "0")
    assert _decide(UPDATE) != ("title_update", (353, 1162))


def test_resume_dialog_with_retire_button_is_resumed():
    """codex-20261109-1-r：日期变更回首页后的「プロデュース再開」框自带右上角「リタイア」按钮（实读），原来按危险字样停下。
    只在这个框里放过「リタイア」：点「再開する」，不碰リタイア；框里有破棄 / 别的设备仍停。"""
    boxes = [box("プロデュース再開", 185, 440), box("リタイア", 582, 439), box("［VEIL］秦谷美鈴", 361, 816),
             box("✕キャンセル", 212, 1162), box("再開する", 521, 1162)]
    T.step(DATE, resume=True, now=0)
    assert T.step(boxes, resume=True, now=30) == ("title_resume", (521, 1162))
    T.step(DATE, resume=True, now=100)
    with pytest.raises(T.TitleHold):
        T.step(boxes + [box("別の端末で", 360, 600)], resume=True, now=130)
