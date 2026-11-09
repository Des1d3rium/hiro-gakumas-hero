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
    monkeypatch.setenv("HIRO_DRINK_INSITU", "0")
    slots = I._read_drink_slots(frame([A, B, None, A]))
    assert [s["state"] for s in slots] == ["unknown", "unknown", "empty", "unknown"]
    assert all("method" not in s for s in slots)


def test_switch_on_resolves_unknown_with_insitu_templates(insitu, monkeypatch):
    monkeypatch.delenv("HIRO_DRINK_INSITU", raising=False)  # 默认开（使用者 2026-11-07）
    slots = I._read_drink_slots(frame([A, B, None, A]))
    assert [s["state"] for s in slots] == ["drink", "drink", "empty", "drink"]
    assert [s["name"] for s in slots] == ["初星ホエイプロテイン", "ブーストエキス", None, "初星ホエイプロテイン"]
    assert all(s["method"] == "insitu" and s["confidence"] >= I.INSITU_MIN for s in slots if s["state"] == "drink")
    # 空槽也用实机模板复查（使用者 2026-11-08「以后直接用实机模板读」），真空槽分数低、仍是空
    assert slots[2]["state"] == "empty" and slots[2]["insitu_score"] < I.INSITU_MIN


def test_unseen_drink_stays_unknown(insitu, monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_INSITU", "1")
    slots = I._read_drink_slots(frame([UNSEEN, None, None, None]))
    assert slots[0]["state"] == "unknown" and slots[0]["insitu_score"] < I.INSITU_MIN


def test_insitu_overrides_original_result(monkeypatch):
    """实机模板为主（使用者 2026-11-08）：原画认成别的，实机模板认准了就以实机模板为准。"""
    entry = {"slot": 0, "state": "drink", "name": "ブーストエキス", "asset": B, "confidence": 0.6}
    lib = I.IconLibrary.from_insitu({A: ICONS[A]}, I.DRINK_SIZE, NAMES)
    out = [dict(entry)]
    I._resolve_insitu(out, [ICONS[A]], lib)
    assert out[0]["asset"] == A and out[0]["method"] == "insitu"


@pytest.mark.synthetic
def test_capture_drinks_build_and_check(tmp_path, monkeypatch, capsys):
    """合成 trace：原画高把握认出的格做成模板；check --holdout 用其余 trace 的模板补认 unknown。"""
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    import capture_drinks as C

    monkeypatch.setattr(C, "art_library", lambda: ({}, {}))  # 只测实机中位图这条路
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
    assert not (folder / I.RENDER_FILE).exists()  # 没有原画可拟合
    assert json.loads((folder / "index.json").read_text()) == NAMES
    capsys.readouterr()
    assert C.main(["check", str(tmp_path / "traces"), "--holdout", "--min-samples", "3"]) == 0
    report = capsys.readouterr().out
    assert "共 12" in report and "仍然 unknown：0" in report


def art(seed: int) -> np.ndarray:
    """合成「原画」：256×256 RGBA，透明底上一个瓶子形状，里面是 pattern 放大的图案。"""
    im = np.zeros((256, 256, 4), np.uint8)
    im[:, :, :3] = cv2.resize(pattern(seed), (256, 256), interpolation=cv2.INTER_NEAREST)
    im[20:237, 60:197, 3] = 255
    return im


ARTS = {A: art(1), B: art(2), UNSEEN: art(3)}


@pytest.fixture
def unified(monkeypatch, tmp_path):
    """原画图库 = 合成原画（A、B、UNSEEN）；实机模板目录按需写。识别层走真实的 _insitu_library。"""
    art_dir, real_dir = tmp_path / "drink", tmp_path / "drink_exam"
    art_dir.mkdir()
    for key, im in ARTS.items():
        cv2.imwrite(str(art_dir / f"{key}.png"), im)
    (art_dir / "index.json").write_text(json.dumps({**NAMES, UNSEEN: "新饮料"}, ensure_ascii=False))
    monkeypatch.setattr(I.assets, "icon_dir", lambda kind: {"drink": art_dir, I.INSITU_KIND: real_dir}.get(kind))
    monkeypatch.setattr(I, "_library", lambda kind, size: Original())
    I._insitu_library.cache_clear()
    import kaa.db.drink

    monkeypatch.setattr(kaa.db.drink.Drink, "from_asset_id", staticmethod(lambda asset: None))
    monkeypatch.setenv("HIRO_DRINK_INSITU", "1")
    yield real_dir
    I._insitu_library.cache_clear()


def rendered_frame(keys) -> np.ndarray:
    """按渲染外观贴进饮料栏（模拟实机里的模糊、底色），叠噪声。"""
    img = np.full((1280, 720, 3), 90, np.uint8)
    h = I.DRINK_SIZE // 2
    for (cx, cy), key in zip(I.DRINK_CENTERS, keys):
        if key is not None:
            img[cy - h : cy + h, cx - h : cx + h] = I.render_insitu(ARTS[key])
    noise = np.random.default_rng(3).integers(-5, 6, img.shape)
    return np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)


@pytest.mark.synthetic
def test_new_drink_recognized_from_rendered_art_without_samples(unified):
    """没采到过实机样本的饮料（包括以后新出的），只要原画图库里有，就按渲染模板认。"""
    slots = I._read_drink_slots(rendered_frame([UNSEEN, A, None, B]))
    assert [s["name"] for s in slots] == ["新饮料", "初星ホエイプロテイン", None, "ブーストエキス"]
    assert {s["insitu_source"] for s in slots if s["state"] == "drink"} == {"render"}


@pytest.mark.synthetic
def test_real_median_overrides_render_and_render_params_are_read(unified):
    unified.mkdir()
    cv2.imwrite(str(unified / f"{A}.png"), I.render_insitu(ARTS[A]))
    (unified / "index.json").write_text(json.dumps({A: NAMES[A]}, ensure_ascii=False))
    lib = I._insitu_library(I.DRINK_SIZE)
    assert lib.sources == {A: "real", B: "render", UNSEEN: "render"}
    slots = I._read_drink_slots(rendered_frame([A, B, None, None]))
    assert [s.get("insitu_source") for s in slots[:2]] == ["real", "render"]
    # render.json 覆盖默认渲染参数
    (unified / I.RENDER_FILE).write_text(json.dumps({"sigma": 0.0, "opacity": 1.0}))
    I._insitu_library.cache_clear()
    plain = I._insitu_library(I.DRINK_SIZE)
    assert not np.allclose(plain.templates[plain.keys.index(B)], lib.templates[lib.keys.index(B)])


def test_render_keeps_size_and_background():
    out = I.render_insitu(ARTS[A])
    assert out.shape == (I.DRINK_SIZE, I.DRINK_SIZE, 3) and out.dtype == np.uint8
    corner = out[:4, :4].reshape(-1, 3).mean(axis=0)  # 原画透明处 = 底色（模糊后仍接近）
    assert np.allclose(corner, I.RENDER["bg"], atol=8)


@pytest.mark.synthetic
def test_fit_render_recovers_parameters():
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    import capture_drinks as C

    truth = {"scale": 0.95, "dx": 1, "dy": 0, "sigma": 1.2, "opacity": 0.8, "bg": [240, 230, 200]}
    real = {k: I.render_insitu(ARTS[k], I.DRINK_SIZE, truth) for k in (A, B)}
    params, score = C.fit_render(ARTS, real)
    assert score > 0.99
    # 不透明度只整体缩放对比度，归一化相关看不出来，不断言；几何和模糊要拟合回来
    assert {k: params[k] for k in ("scale", "dx", "dy", "sigma")} == {k: truth[k] for k in ("scale", "dx", "dy", "sigma")}
