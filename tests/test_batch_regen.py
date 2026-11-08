"""LIVE 后回忆生成 / 再生成 / 挑选（tools/batch_regen.py）：用假游戏走一遍实机看到的页面顺序。"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_regen as R  # noqa: E402

IMG = np.zeros((1280, 720, 3), np.uint8)
ENT = ["エンターテイナー+", "体力消費5強気に変更パラメータ+12"]
PLAIN = ["インフルエンサー", "メンタルスキルカードの元気値増加+5"]


class Game:
    """页面：corner（快进中）→ gen → memory（左下再生成，次数 left）→ confirm → gen → … → select。"""

    def __init__(self, left, versions, corner_frames=2):
        self.left, self.versions, self.idx = left, versions, 0
        self.page = "corner"
        self.corner = corner_frames
        self.taps = []
        self.regen_done = 0

    def shoot(self):
        self.frame = self.page
        return IMG

    def header(self, img):
        return {"confirm": (550, 580), "final": (869, 933)}.get(self.page)

    def ocr(self, img):
        h, w = img.shape[:2]
        p = self.page
        if (h, w) == (560, 720) and p == "select":  # 卡面区
            return [(t, 1, None) for t in self.versions[self.idx]]
        if (h, w) == (1280, 720):  # 整帧
            if p == "confirm":
                return [(t, 1, (0, 0, 1, 1)) for t in ("メモリー再生成確認", "メモリー再生成チケット", "残り再生成回数")]
            if p == "final":
                label = "再生成前" if self.idx == 0 else f"再生成{self.idx}回目"
                return [(t, 1, (0, 0, 1, 1)) for t in ("メモリー確定確認", f"{label}の内容でメモリーを生成します。")]
            return [(t, 1, (0, 0, 1, 1)) for t in self.versions[self.idx]]
        crop = (h, w)
        if crop == (110, 240) and p in ("memory", "select") and self.left > 0:
            return [(f"あと{self.left}回可能", 1, None), ("再生成", 1, None)]
        if crop == (80, 160) and p == "gen":
            return [("生成", 1, None)]
        if crop == (70, 720) and p == "select":
            return [("獲得するメモリーを選択してください", 1, None)]
        if crop == (55, 200) and p == "select":
            return [("再生成前" if self.idx == 0 else f"再生成{self.idx}回目", 1, None)]
        return []

    def click(self, x, y):
        self.taps.append((x, y))
        p = self.page
        if (x, y) == R.CORNER and p == "corner":
            self.corner -= 1
            if self.corner <= 0:
                self.page = "gen"
        elif (x, y) == R.GEN_TAP and p == "gen":
            self.page = "memory" if self.regen_done == 0 else "select"
        elif (x, y) == R.REGEN_TAP and p in ("memory", "select") and self.left > 0:
            self.page = "confirm"
        elif (x, y) == (R.CONFIRM_X, 550 + R.CONFIRM_DY) and p == "confirm":
            self.left -= 1
            self.regen_done += 1
            self.page = "gen"
        elif (x, y) == R.PREV_TAP and p == "select":
            self.idx = max(0, self.idx - 1)
        elif (x, y) == R.NEXT_TAP and p == "select":
            self.idx = min(len(self.versions) - 1, self.idx + 1)
        elif (x, y) == R.DECIDE_TAP and p == "select":
            self.page = "final"
        elif (x, y) == (R.FINAL_X, 869 + R.FINAL_DY) and p == "final":
            self.page = "memory_page"
        elif (x, y) == R.NEXT_TAP_PAGE and p == "memory_page":
            self.page = "decided"


GAME = {}


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    monkeypatch.setattr(R, "FRAMES_ROOT", tmp_path / "traces")  # 存帧不落到仓库 traces/
    monkeypatch.setattr(R, "dots", lambda img: (len(GAME["g"].versions), GAME["g"].idx) if GAME["g"].page == "select" else (0, None))


def run(g, **kw):
    GAME["g"] = g
    return R.run(g.shoot, g.click, g.ocr, g.header, log=lambda m: None, **kw)


def test_uses_all_regens_then_picks_hit():
    g = Game(2, [PLAIN, ENT, PLAIN])
    out = run(g)
    assert out["ok"] and out["page"] == "memory_decided" and out["regens"] == 2
    assert out["pick"] == 1 and g.page == "decided" and g.idx == 1 and g.left == 0


def test_no_hit_stops_without_decide_when_rules_say_hold(tmp_path, monkeypatch):
    rules = tmp_path / "rules.json"
    rules.write_text('{"memory": {"no_hit": "hold"}}', encoding="utf-8")
    monkeypatch.setenv("HIRO_BATCH_RULES", str(rules))
    g = Game(1, [PLAIN, PLAIN])
    out = run(g)
    assert not out["ok"] and out["page"] == "memory_choice" and R.DECIDE_TAP not in g.taps


def test_no_hit_picks_first_version_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_BATCH_RULES", str(tmp_path / "missing.json"))
    g = Game(1, [PLAIN, PLAIN])
    out = run(g)
    assert out.get("pick") == 0 and R.DECIDE_TAP in g.taps


def test_paid_confirm_is_not_clicked():
    g = Game(1, [PLAIN])
    g.ocr_orig = g.ocr
    g.ocr = lambda img: ([("メモリー再生成確認", 1, None), ("ジュエル", 1, None)] if g.page == "confirm" and img.shape[0] == 1280
                         else g.ocr_orig(img))
    out = run(g)
    assert not out["ok"] and out["page"] == "regen_confirm" and g.left == 1


@pytest.mark.parametrize("texts, want", [
    (["あと2回可能", "再生成"], (True, 2)),
    (["再生成"], (True, None)),
    (["決定"], (False, None)),
])
def test_regen_left(texts, want):
    assert R.regen_left(texts) == want


def test_dots_reads_count_and_current():
    import cv2

    img = np.full((1280, 720, 3), 255, np.uint8)
    for i, x in enumerate((333, 359, 386)):
        cv2.circle(img, (x, 1050), 6, (0, 118, 255) if i == 2 else (203, 203, 201), -1)
    import importlib

    assert importlib.reload(R).dots(img) == (3, 2)


class Lingering(Game):
    """点「再生成する」后框不马上消失：先有一帧标题条认不出（淡出闪烁），之后还能认出 linger 帧。
    miss=True 时第一下没点上，框一直在，直到补点。"""

    def __init__(self, *a, linger=3, miss=False, **kw):
        super().__init__(*a, **kw)
        self.linger, self.miss, self.ghost, self.flicker = linger, miss, 0, False
        self.confirm_taps, self.dim = 0, False

    def shoot(self):
        self.dim = False
        if self.ghost:
            if self.flicker:
                self.flicker, self.dim = False, True  # 这一帧标题条认不出
            else:
                self.ghost -= 1
                if not self.ghost:
                    self.page = "gen"
        return super().shoot()

    def header(self, img):
        if self.dim:
            return None
        return super().header(img)

    def click(self, x, y):
        if (x, y) == (R.CONFIRM_X, 550 + R.CONFIRM_DY) and self.page == "confirm":
            self.confirm_taps += 1
            if self.ghost:
                return  # 框在淡出：点了也没用
            if self.miss and self.confirm_taps == 1:
                return  # 第一下没点上
            self.left -= 1
            self.regen_done += 1
            self.ghost, self.flicker = self.linger, True
            return
        super().click(x, y)


def test_fading_confirm_dialog_is_not_counted_twice():
    """实机 codex-20261107-1：上限 2 次却打了「再生成第 3 次」。框淡出时闪一帧不能让同一个框被再点、再记一次。"""
    g = Lingering(2, [PLAIN, PLAIN, ENT])
    logs = []
    GAME["g"] = g
    out = R.run(g.shoot, g.click, g.ocr, g.header, log=logs.append)
    assert out["ok"] and out["regens"] == 2 and out["pick"] == 2
    assert g.confirm_taps == 2 and sum(m.startswith("再生成第") for m in logs) == 2


def test_missed_confirm_is_retried_without_counting(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(R.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(R.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    g = Lingering(1, [PLAIN, ENT], linger=1, miss=True)
    logs = []
    GAME["g"] = g
    out = R.run(g.shoot, g.click, g.ocr, g.header, log=logs.append)
    assert out["ok"] and out["regens"] == 1 and g.left == 0 and g.confirm_taps == 2
    assert any("补点" in m for m in logs)


def test_versions_keep_raw_text_name_and_frame():
    head = ["獲得可能スキルカード", "開始時に獲得"]
    g = Game(1, [head + ["オーバードライブ+", "強気に変更"], head + ENT])
    out = run(g)
    v0, v1 = out["versions"]
    assert v0["hit"] is None and v0["card"] is None and v0["name"] == "オーバードライブ+"
    assert v0["texts"][:3] == ["獲得可能スキルカード", "開始時に獲得", "オーバードライブ+"]
    assert v1["name"] == "エンターテイナー+" and v1["hit"]
    assert all(Path(v["frame"]).is_file() for v in out["versions"])
    assert Path(v0["frame"]).parent.parent == R.FRAMES_ROOT


def test_regen_count_follows_dots_when_clicks_disagree():
    """进程中途接手（没看到再生成点击）：版本数按分页圆点。"""
    g = Game(0, [PLAIN, PLAIN, ENT])
    g.page = "select"
    out = run(g)
    assert out["regens"] == 2 and out["pick"] == 2


@pytest.mark.parametrize("texts, want", [
    (["獲得可能スキルカード", "中間試験/1次オーディション後に獲得", "インフルエンサー", "アノマリーのみ"], "インフルエンサー"),
    (["獲得可能スキルカード", "開始時に獲得", "オーバードライブ+"], "オーバードライブ+"),
    (["強気に変更"], None),
])
def test_card_name(texts, want):
    import batch_memory

    assert batch_memory.card_name(texts) == want
