"""饮料栏统一模板（hiro.vision.items，开关 HIRO_DRINK_INSITU=1）：补原画模板认不出的饮料。

原画图库（assets/pack/icons/drink、KAA 公开数据）是 512px 干净原画缩到 56px；考试里饮料栏的图标模糊、带彩虹圈，
正确答案也只有 0.46–0.61 分。统一模板 = 原画图库里每种饮料按 items.RENDER 渲染成饮料栏外观（以后遇到新饮料不用先采样），
采到过的饮料用实机中位图覆盖。识别层运行时现做，这个工具只负责实机中位图和渲染参数。

做法（不碰游戏，只读 trace）：
- 取 traces/<run>/steps.jsonl 里考试页（HIF_EXAM）的 drink_slots：原画模板已经高把握认出（state=drink、领先 ≥ --min-margin）
  的格，按记录的 rect 从截图裁块，按资源名分组，每组取逐像素中位数（去掉偶尔叠上来的特效）。
- 样本数够（≥ --min-samples，且来自 ≥ --min-traces 个 trace）才写实机中位图，并用它们重新拟合渲染参数（render.json）；
  写到自建素材目录（HIRO_USER_ASSETS，默认 resources/user_assets/）的 icons/drink_exam/。截图和模板都不进仓库。

    python tools/assets/capture_drinks.py build traces/             # 生成 / 更新（--dry-run 只报告）
    python tools/assets/capture_drinks.py check traces/ --holdout   # 新旧识别对比；每个 trace 用其余 trace 的实机图（留一 trace）
    python tools/assets/capture_drinks.py check traces/ --unseen    # 每种采到过的饮料假装没采到、只靠渲染模板认（留一饮料）
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor" / "kaa")]

from hiro.vision import items as I  # noqa: E402

KIND = I.INSITU_KIND


def user_dir(out: str | Path | None = None) -> Path:
    return Path(out or os.environ.get("HIRO_USER_ASSETS") or "resources/user_assets")


def exam_frames(paths: list[str]):
    """→ (trace 名, 截图路径, drink_slots)：只取考试页、有截图的步骤。"""
    for p in map(Path, paths):
        files = sorted(p.rglob("steps.jsonl")) if p.is_dir() else [p]
        for f in files:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        step = json.loads(line)
                    except ValueError:
                        continue
                    obs = step.get("observation")
                    if not isinstance(obs, dict) or (obs.get("page") or {}).get("name") != "HIF_EXAM" or not obs.get("screenshot"):
                        continue
                    slots = (obs.get("fields") or {}).get("drink_slots")
                    if slots:
                        yield f.parent.name, f.parent / obs["screenshot"], slots


def crop(img: np.ndarray, rect) -> np.ndarray | None:
    x, y, w, h = rect
    if x < 0 or y < 0 or y + h > img.shape[0] or x + w > img.shape[1]:
        return None
    return img[y : y + h, x : x + w]


def collect(paths: list[str], *, min_margin: float) -> tuple[dict[str, list[tuple[str, np.ndarray]]], dict[str, str]]:
    """资源名 → [(trace, 裁块)]；只收原画模板高把握认出的格。"""
    groups, names = defaultdict(list), {}
    for trace, shot, slots in exam_frames(paths):
        sure = [s for s in slots if s.get("state") == "drink" and s.get("asset") and (s.get("margin") or 0) >= min_margin]
        if not sure:
            continue
        img = cv2.imread(str(shot))
        if img is None:
            continue
        for s in sure:
            c = crop(img, s["rect"])
            if c is not None and c.shape[:2] == (I.DRINK_SIZE, I.DRINK_SIZE):
                groups[s["asset"]].append((trace, c))
                if s.get("name"):
                    names[s["asset"]] = s["name"]
    return groups, names


def templates(groups, *, min_samples: int, min_traces: int, exclude: str | None = None) -> dict[str, np.ndarray]:
    out = {}
    for asset, samples in groups.items():
        use = [c for t, c in samples if t != exclude]
        if len(use) >= min_samples and len({t for t, _ in samples if t != exclude}) >= min_traces:
            out[asset] = np.median(np.stack(use), axis=0).astype(np.uint8)
    return out


def art_library() -> tuple[dict[str, np.ndarray], dict[str, str]]:
    from hiro.vision import assets

    folder = assets.icon_dir("drink")
    return I.load_art(folder) if folder is not None else ({}, {})


GRID = {"scale": (0.9, 0.95, 1.0, 1.05), "dx": (-1, 0, 1), "dy": (0, 1, 2), "sigma": (0.6, 0.9, 1.2), "opacity": (0.8, 0.9, 1.0)}
BG_RING = (21, 24)  # 底色取实机中位图圆形区域边缘这一圈（半径像素）


def _vec(im: np.ndarray) -> np.ndarray:
    mask = np.repeat(I.insitu_mask(I.DRINK_SIZE)[:, :, None], 3, axis=2)
    v = (I._highpass(im) * mask).ravel()
    v = v - v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


def fit_render(art: dict[str, np.ndarray], real: dict[str, np.ndarray]) -> tuple[dict, float] | None:
    """用采到过的饮料拟合原画 → 饮料栏外观的渲染参数：网格搜索，最大化渲染图与实机中位图的平均相关。"""
    keys = [k for k in real if k in art]
    if not keys:
        return None
    yy, xx = np.mgrid[: I.DRINK_SIZE, : I.DRINK_SIZE]
    r = np.hypot(yy - (I.DRINK_SIZE - 1) / 2, xx - (I.DRINK_SIZE - 1) / 2)
    ring = (r > BG_RING[0]) & (r < BG_RING[1])
    bg = [int(v) for v in np.median(np.concatenate([real[k][ring] for k in keys]), axis=0)]
    target = {k: _vec(real[k]) for k in keys}
    best = None
    for combo in itertools.product(*GRID.values()):
        params = dict(zip(GRID, combo), bg=bg)
        score = float(np.mean([target[k] @ _vec(I.render_insitu(art[k], I.DRINK_SIZE, params)) for k in keys]))
        if best is None or score > best[1]:
            best = (params, score)
    return best


def build(args) -> int:
    groups, names = collect(args.paths, min_margin=args.min_margin)
    tpl = templates(groups, min_samples=args.min_samples, min_traces=args.min_traces)
    art, art_names = art_library()
    folder = user_dir(args.out) / "icons" / KIND
    # 以前采过、这次 trace 里没出现的饮料：保留原来的实机中位图
    old, old_names, _ = I.read_insitu_dir(folder)
    real = {**old, **tpl}
    fitted = fit_render(art, real)
    for asset, samples in sorted(groups.items()):
        mark = "实机中位图" if asset in tpl else "样本不够，用渲染"
        print(f"{names.get(asset, asset)}（{asset}）：{len(samples)} 块，{len({t for t, _ in samples})} 个 trace → {mark}")
    rendered = sorted(set(art) - set(real))
    print(f"统一模板：实机 {len(real)} 种 + 原画渲染 {len(rendered)} 种（共 {len(set(art) | set(real))} 种）")
    if fitted:
        print(f"渲染参数（平均相关 {fitted[1]:.3f}）：{fitted[0]}")
    if args.dry_run:
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    for asset, im in tpl.items():
        cv2.imwrite(str(folder / f"{asset}.png"), im)
    index = folder / "index.json"
    index.write_text(json.dumps({**old_names, **{a: names[a] for a in tpl if a in names}}, ensure_ascii=False, indent=1))
    if fitted:
        (folder / I.RENDER_FILE).write_text(json.dumps(fitted[0]))
    print(f"写入 {len(tpl)} 张实机中位图{'和渲染参数' if fitted else ''}到 {folder}")
    return 0


def check(args) -> int:
    """原画识别（开关关）与补认后（开关开）的结果对比。补认只改 unknown 格，所以只统计 unknown 的去向。"""
    os.environ.pop("HIRO_DRINK_INSITU", None)
    if args.unseen:
        return unseen(args)
    groups = collect(args.paths, min_margin=args.min_margin)[0] if args.holdout else None
    art, art_names = art_library() if args.holdout else ({}, {})
    names = {}
    libs: dict[str | None, I.IconLibrary] = {}
    states, resolved, still, frames = Counter(), Counter(), Counter(), 0
    for trace, shot, _ in exam_frames(args.paths):
        img = cv2.imread(str(shot))
        if img is None:
            continue
        frames += 1
        key = trace if args.holdout else None
        if key not in libs:
            if args.holdout:
                tpl = templates(groups, min_samples=args.min_samples, min_traces=args.min_traces, exclude=trace)
                fitted = fit_render(art, tpl) if args.refit else None
                libs[key] = I.unified_library(art, tpl, params=fitted[0] if fitted else None, names=art_names)
            else:
                libs[key] = I._insitu_library(I.DRINK_SIZE)
        slots = I._read_drink_slots(img)
        states.update(s["state"] for s in slots)
        I._resolve_insitu(slots, [crop(img, s["rect"]) for s in slots], libs[key])
        for s in slots:
            if s.get("method") == "insitu":
                resolved[(s["name"] or s["asset"], s.get("insitu_source"))] += 1
                names[s["asset"]] = s["name"]
            elif s["state"] == "unknown":
                still[(s.get("insitu_score"), s.get("insitu_margin"))] += 1
    print(f"{frames} 帧；原画识别：{dict(states)}")
    print(f"unknown → 统一模板认出（饮料, 来源 real=实机 / render=原画渲染）：{dict(resolved)}（共 {sum(resolved.values())}）")
    print(f"仍然 unknown：{sum(still.values())}" + (f"，实机模板分数 / 领先：{still.most_common(5)}" if still else ""))
    return 0


def unseen(args) -> int:
    """留一饮料：每种采到过的饮料去掉它的实机中位图（只剩原画渲染），用它自己的实机裁块去认，看认得出、会不会认错。"""
    groups, names = collect(args.paths, min_margin=args.min_margin)
    real = templates(groups, min_samples=args.min_samples, min_traces=args.min_traces)
    art, art_names = art_library()
    for asset in sorted(real):
        if asset not in art:
            print(f"{names.get(asset, asset)}：原画图库里没有，跳过")
            continue
        others = {k: v for k, v in real.items() if k != asset}
        fitted = fit_render(art, others) if args.refit else None
        lib = I.unified_library(art, others, params=fitted[0] if fitted else None, names=art_names)
        crops = [c for _, c in groups[asset]][: args.limit]
        tops = lib.match_many(crops)
        ok = sum(t[0][1] == asset and t[0][0] >= I.INSITU_MIN and t[0][0] - t[1][0] >= I.INSITU_MARGIN for t in tops)
        wrong = sum(t[0][1] != asset and t[0][0] >= I.INSITU_MIN and t[0][0] - t[1][0] >= I.INSITU_MARGIN for t in tops)
        sc = np.array([t[0][0] for t in tops])
        mg = np.array([t[0][0] - t[1][0] for t in tops])
        print(f"{names.get(asset, asset)}：{len(tops)} 块，认出 {ok}、认错 {wrong}；分数 p10 {np.percentile(sc, 10):.2f}，"
              f"领先 p10 {np.percentile(mg, 10):.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="capture_drinks", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "check"):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="*", default=["traces"])
        p.add_argument("--min-margin", type=float, default=0.3, help="原画模板领先次佳至少多少才当作标注（默认 0.3）")
        p.add_argument("--min-samples", type=int, default=5)
        p.add_argument("--min-traces", type=int, default=1)
    sub.choices["build"].add_argument("--out", help="自建素材目录（默认 HIRO_USER_ASSETS 或 resources/user_assets）")
    sub.choices["build"].add_argument("--dry-run", action="store_true")
    sub.choices["check"].add_argument("--holdout", action="store_true", help="每个 trace 用其余 trace 的实机图（留一 trace）")
    sub.choices["check"].add_argument("--unseen", action="store_true", help="每种饮料只靠原画渲染认（留一饮料）")
    sub.choices["check"].add_argument("--refit", action="store_true", help="留一时用其余数据重新拟合渲染参数（慢）；默认用 items.RENDER")
    sub.choices["check"].add_argument("--limit", type=int, default=300, help="--unseen 每种饮料最多用多少块")
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else check(a)


if __name__ == "__main__":
    raise SystemExit(main())
