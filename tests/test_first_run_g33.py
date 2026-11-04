# 来源：digital-hiro tests/test_first_run_g33.py（fix_matto_prep_watchdog 5b5adff）
"""第三十三局：本地设置被重置后，开始确认页点「プロデュース開始」依次弹 4 个首次设置框（总控实机截图 tests/fixtures/first_run/）。
- ボイス再生確認：「決定」左边的对勾被 OCR 读成「く決定」，以前按全等找「決定」找不到，不点（设置框会一直盖着）；
- コミュ早送り設定 / 演出スキップ設定：skip_settings 照常处理；
- サポートイベント設定：HiroController._first_run_settings 选「強化済みで獲得する」→「決定」（这一下就开始培育）。"""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

from hiro.loop import skip_settings as ss

FIX = Path(__file__).parent / "fixtures" / "first_run"


def _boxes(name):
    from hiro.produce.ops import ocr_boxes

    return ocr_boxes(cv2.resize(cv2.imread(str(FIX / f"g33-{name}.jpg")), (720, 1280)))


def test_ok_text_tolerates_check_icon():
    assert ss.is_ok_text("決定") and ss.is_ok_text("く決定") and ss.is_ok_text(" 決 定")
    assert not ss.is_ok_text("決定しますか") and not ss.is_ok_text("キャンセル") and not ss.is_ok_text("未決定")


@pytest.mark.parametrize(("name", "kind", "option_y"), [
    ("1-voice", "ボイス再生確認", 818),  # ボイスありで再生（不是「なし」那行 y≈894）
    ("2-commu-ff", "早送り設定", 818),  # コミュを早送りする（不是「しない」那行 y≈742）
    ("3-produce-skip", "演出スキップ設定", None),
])  # fmt: skip
def test_real_frames_read(name, kind, option_y):
    r = ss.read(_boxes(name))
    assert r is not None and r["kind"] == kind
    assert r["ok"] is not None and abs(r["ok"][1] - 1160) <= 30, r
    if option_y is None:
        assert r["option"] is None
    else:
        assert r["option"] is not None and abs(r["option"][1] - option_y) <= 20, r


def test_voice_dialog_clicks_option_then_ok():
    boxes = _boxes("1-voice")
    clicks = []
    r = ss.handle(boxes, shoot=lambda: None, ocr=lambda img: boxes, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None)
    assert r["ok"] and r["option"]
    assert abs(clicks[0][1] - 818) <= 20 and clicks[-1][0] > 400 and abs(clicks[-1][1] - 1160) <= 30, clicks  # 右边的「決定」，不是キャンセル


def test_support_event_dialog_real_frame(monkeypatch):
    from hiro.loop.controller import HiroController

    raw = _boxes("4-support-event")
    assert ss.read(raw) is None  # 不归 skip_settings，归 _first_run_settings
    boxes = [NS(text=t, rect=((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, 1, 1)) for t, _, b in raw]
    clicks = []
    monkeypatch.setitem(sys.modules, "kotonebot", NS(device=NS(click=lambda r: clicks.append(r[:2]), screenshot=lambda: None),
                                                     ocr=NS(ocr=lambda: boxes)))  # fmt: skip
    monkeypatch.setattr("hiro.loop.controller.time.sleep", lambda s: None)
    c = HiroController.__new__(HiroController)
    c._texts = [b.text for b in boxes]
    assert c._first_run_settings()
    assert len(clicks) == 2 and abs(clicks[0][1] - 892) <= 20 and abs(clicks[1][1] - 1160) <= 30, clicks  # 強化済み（不是未強化 y≈966）→ 決定


def test_quick_page_leaves_first_run_dialogs_alone():
    from hiro.vision.quick import quick_page

    for name in ("1-voice", "2-commu-ff", "3-produce-skip", "4-support-event"):
        assert quick_page(cv2.resize(cv2.imread(str(FIX / f"g33-{name}.jpg")), (720, 1280))) is None, name
