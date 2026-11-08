"""饮料栏实机模板（HIRO_DRINK_INSITU）：原画模板没把握的格用实机模板补认。全部用合成图案，不读游戏截图。"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("kaa")

from hiro.vision import items as I  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def pattern(seed: int) -> np.ndarray:
    """56×56 的合成「饮料图标」：随机色块 + 线条，细节够高通比较。"""
    rng = np.random.default_rng(seed)
    im = np.full((56, 56, 3), 200, np.uint8)
    for _ in range(12):
        x, y = (int(v) for v in rng.integers(4, 52, 2))
        cv2.circle(im, (x, y), int(rng.integers(3, 9)), tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
        cv2.line(im, (x, y), tuple(int(v) for v in rng.integers(0, 56, 2)), tuple(int(c) for c in rng.integers(0, 255, 3)), 2)
    return im


A, B, UNSEEN = "img_general_pdrink_3-001", "img_general_pdrink_2-008", "img_general_pdrink_9-999"
ICONS = {A: pattern(1), B: pattern(2), UNSEEN: pattern(3)}
NAMES = {A: "初星ホエイプロテイン", B: "ブーストエキス"}


def frame(slots) -> np.ndarray:
    """720×1280 考试帧：按 DRINK_CENTERS 贴图案（None = 空槽），叠一点噪声模拟实机。"""
    img = np.full((1280, 720, 3), 90, np.uint8)
    h = I.DRINK_SIZE // 2
    for (cx, cy), key in zip(I.DRINK_CENTERS, slots):
        if key is not None:
            img[cy - h : cy + h, cx - h : cx + h] = ICONS[key]
    noise = np.random.default_rng(7).integers(-6, 7, img.shape)
    return np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)


class Original:
    """原画模板的替身：有图案的格给「没把握」的分数（0.455 / 领先 0.19，实机 初星ホエイプロテイン 那样），空槽给低分。"""

    keys = [A]
    names: dict = {}

    def match_many(self, crops):
        out = []
        for c in crops:
            flat = c is None or float(c.std()) < 10
            out.append([(0.1, A), (0.05, B)] if flat else [(0.455, A), (0.265, B)])
        return out


@pytest.fixture
def insitu(monkeypatch):
    lib = I.IconLibrary.from_insitu({A: ICONS[A], B: ICONS[B]}, I.DRINK_SIZE, NAMES)
    monkeypatch.setattr(I, "_library", lambda kind, size: Original())
    monkeypatch.setattr(I, "_insitu_library", lambda size: lib)
    import kaa.db.drink

    monkeypatch.setattr(kaa.db.drink.Drink, "from_asset_id", staticmethod(lambda asset: None))
    return lib


def test_switch_off_keeps_original_unknown(insitu, monkeypatch):
    monkeypatch.delenv("HIRO_DRINK_INSITU", raising=False)
    slots = I._read_drink_slots(frame([A, B, None, A]))
    assert [s["state"] for s in slots] == ["unknown", "unknown", "empty", "unknown"]
    assert all("method" not in s for s in slots)


def test_switch_on_resolves_unknown_with_insitu_templates(insitu, monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_INSITU", "1")
    slots = I._read_drink_slots(frame([A, B, None, A]))
    assert [s["state"] for s in slots] == ["drink", "drink", "empty", "drink"]
    assert [s["name"] for s in slots] == ["初星ホエイプロテイン", "ブーストエキス", None, "初星ホエイプロテイン"]
    assert all(s["method"] == "insitu" and s["confidence"] >= I.INSITU_MIN for s in slots if s["state"] == "drink")
    assert slots[2]["confidence"] is not None and "insitu_score" not in slots[2]  # 空槽不碰


def test_unseen_drink_stays_unknown(insitu, monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_INSITU", "1")
    slots = I._read_drink_slots(frame([UNSEEN, None, None, None]))
    assert slots[0]["state"] == "unknown" and slots[0]["insitu_score"] < I.INSITU_MIN


def test_confident_original_result_is_not_overridden(monkeypatch):
    """原画已经认出的格不再比实机模板，哪怕实机模板认成别的。"""
    entry = {"slot": 0, "state": "drink", "name": "ブーストエキス", "asset": B, "confidence": 0.6}
    lib = I.IconLibrary.from_insitu({A: ICONS[A]}, I.DRINK_SIZE, NAMES)
    out = [dict(entry)]
    I._resolve_insitu(out, [ICONS[A]], lib)
    assert out == [entry]


@pytest.mark.synthetic
def test_capture_drinks_build_and_check(tmp_path, monkeypatch, capsys):
    """合成 trace：原画高把握认出的格做成模板；check --holdout 用其余 trace 的模板补认 unknown。"""
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    import capture_drinks as C

    monkeypatch.setattr(I, "_library", lambda kind, size: Original())
    import kaa.db.drink

    monkeypatch.setattr(kaa.db.drink.Drink, "from_asset_id", staticmethod(lambda asset: None))
    h = I.DRINK_SIZE // 2
    for run in ("t1", "t2"):
        d = tmp_path / "traces" / run
        (d / "frames").mkdir(parents=True)
        with (d / "steps.jsonl").open("w", encoding="utf-8") as f:
            for seq in range(1, 4):
                cv2.imwrite(str(d / "frames" / f"{seq:05d}.png"), frame([A, B, None, None]))
                slots = [{"slot": i, "state": st, "asset": a, "name": NAMES.get(a), "margin": m,
                          "rect": [cx - h, cy - h, I.DRINK_SIZE, I.DRINK_SIZE]}
                         for i, ((cx, cy), st, a, m) in enumerate(zip(I.DRINK_CENTERS, ["drink", "drink", "empty", "empty"],
                                                                    [A, B, None, None], [0.5, 0.4, 0.0, 0.0]))]
                obs = {"page": {"name": "HIF_EXAM"}, "screenshot": f"frames/{seq:05d}.png", "fields": {"drink_slots": slots}}
                f.write(json.dumps({"seq": seq, "observation": obs}) + "\n")
    out = tmp_path / "assets"
    assert C.main(["build", str(tmp_path / "traces"), "--out", str(out), "--min-samples", "3"]) == 0
    folder = out / "icons" / I.INSITU_KIND
    assert sorted(p.name for p in folder.glob("*.png")) == [f"{B}.png", f"{A}.png"]
    assert json.loads((folder / "index.json").read_text()) == NAMES
    capsys.readouterr()
    assert C.main(["check", str(tmp_path / "traces"), "--holdout", "--min-samples", "3"]) == 0
    report = capsys.readouterr().out
    assert "共 12" in report and "仍然 unknown：0" in report
