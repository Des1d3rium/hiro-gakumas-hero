# 来源：digital-hiro tests/test_play_game.py（c3fc27f）
"""整局脚本：从开始确认页读选拔回忆数值（第十六局实机帧：1670 / 1828 / 1011）。"""

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_parse_memory_real_frame():
    from play_game import parse_memory

    from hiro.produce.ops import ocr_boxes

    img = cv2.resize(cv2.imread(str(Path(__file__).parent / "fixtures/start-confirm-q16.png")), (720, 1280))
    assert parse_memory(ocr_boxes(img)) == (1670, 1828, 1011)


def test_home_move_confirm_variants():
    """第十八局：标题读不全、按钮读成「→OK」也要认出「ホーム移動確認」。"""
    from hiro.loop.resume import is_home_move_confirm

    assert is_home_move_confirm(["ホーム移動確認", "閉じる", "→OK"])
    assert is_home_move_confirm(["ホームに移動しますか？", "※ホームに移動後は「プロデュース > 本戦」から開始す"])
    assert not is_home_move_confirm(["開始確認", "本戦へ"])


def test_vote_memory_needs_agreement():
    from play_game import vote_memory

    assert vote_memory([(1434, 2296, 1179), (1454, 2296, 1179), (1434, 2296, 1179)]) == (1434, 2296, 1179)
    assert vote_memory([(1434, 2296, 1179)]) is None
    assert vote_memory([(1434, 2296, 1179), (1454, 2290, 1179)]) is None


def test_memory_value_seen_tolerates_one_digit_confusion():
    """第十八局：开始确认页 1434 被 OCR 读成「⑨1454」，核对要放行；差两位或无关数字不放行。"""
    from hiro.loop.kaa_patches import memory_value_seen

    page = "Pアイドル設定⑨145445.5%229644.4%Q2117941.3%"
    assert memory_value_seen("1434", page) and memory_value_seen("2296", page) and memory_value_seen("1179", page)
    assert not memory_value_seen("1510", page) and not memory_value_seen("1424", "x1454x")


def test_child_env_keeps_caller_pythonpath(monkeypatch):
    """GKMS 第四局：PYTHONPATH 被覆盖成 src，插件 hiro_gkms_advisor 在本战考试导入失败。src 放最前，原有路径保留。"""
    import os

    import play_game

    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/x/plugins", "/y"]))
    parts = play_game.child_env()["PYTHONPATH"].split(os.pathsep)
    assert parts == [str(play_game.ROOT / "src"), "/x/plugins", "/y"]
    monkeypatch.delenv("PYTHONPATH")
    assert play_game.child_env()["PYTHONPATH"] == str(play_game.ROOT / "src")
