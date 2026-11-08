"""从自己的考试 trace 生成饮料栏的「实机模板」，补原画模板认不出的饮料（hiro.vision.items，开关 HIRO_DRINK_INSITU=1）。

原画图库（assets/pack/icons/drink、KAA 公开数据）是 512px 干净原画缩到 56px；考试里饮料栏的图标模糊、带彩虹圈，
正确答案也只有 0.46–0.61 分。用实机裁块做模板，同一种饮料 0.85–0.96 分、空槽 ≤ 0.1。

做法（不碰游戏，只读 trace）：
- 取 traces/<run>/steps.jsonl 里考试页（HIF_EXAM）的 drink_slots：原画模板已经高把握认出（state=drink、领先 ≥ --min-margin）
  的格，按记录的 rect 从截图裁块，按资源名分组，每组取逐像素中位数（去掉偶尔叠上来的特效）。
- 样本数够（≥ --min-samples，且来自 ≥ --min-traces 个 trace）才写模板，写到自建素材目录（HIRO_USER_ASSETS，
  默认 resources/user_assets/）的 icons/drink_exam/<资源名>.png + index.json。截图和模板都不进仓库。

    python tools/assets/capture_drinks.py build traces/            # 生成 / 更新模板（--dry-run 只报告）
    python tools/assets/capture_drinks.py check traces/ --holdout  # 新旧识别对比；--holdout 每个 trace 用其余 trace 建的模板（留一验证）
"""

from __future__ import annotations

import argparse
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


def build(args) -> int:
    groups, names = collect(args.paths, min_margin=args.min_margin)
    tpl = templates(groups, min_samples=args.min_samples, min_traces=args.min_traces)
    for asset, samples in sorted(groups.items()):
        mark = "写入" if asset in tpl else "样本不够，跳过"
        print(f"{names.get(asset, asset)}（{asset}）：{len(samples)} 块，{len({t for t, _ in samples})} 个 trace → {mark}")
    if args.dry_run or not tpl:
        return 0
    folder = user_dir(args.out) / "icons" / KIND
    folder.mkdir(parents=True, exist_ok=True)
    for asset, im in tpl.items():
        cv2.imwrite(str(folder / f"{asset}.png"), im)
    index = folder / "index.json"
    known = json.loads(index.read_text()) if index.exists() else {}
    index.write_text(json.dumps({**known, **{a: names[a] for a in tpl if a in names}}, ensure_ascii=False, indent=1))
    print(f"写入 {len(tpl)} 张模板到 {folder}")
    return 0


def check(args) -> int:
    """原画识别（开关关）与补认后（开关开）的结果对比。补认只改 unknown 格，所以只统计 unknown 的去向。"""
    os.environ.pop("HIRO_DRINK_INSITU", None)
    groups = collect(args.paths, min_margin=args.min_margin)[0] if args.holdout else None
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
                libs[key] = I.IconLibrary.from_insitu(tpl, I.DRINK_SIZE)
            else:
                libs[key] = I._insitu_library(I.DRINK_SIZE)
        slots = I._read_drink_slots(img)
        states.update(s["state"] for s in slots)
        I._resolve_insitu(slots, [crop(img, s["rect"]) for s in slots], libs[key])
        for s in slots:
            if s.get("method") == "insitu":
                resolved[s["name"] or s["asset"]] += 1
                names[s["asset"]] = s["name"]
            elif s["state"] == "unknown":
                still[(s.get("insitu_score"), s.get("insitu_margin"))] += 1
    print(f"{frames} 帧；原画识别：{dict(states)}")
    print(f"unknown → 实机模板认出：{dict(resolved)}（共 {sum(resolved.values())}）")
    print(f"仍然 unknown：{sum(still.values())}" + (f"，实机模板分数 / 领先：{still.most_common(5)}" if still else ""))
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
    sub.choices["check"].add_argument("--holdout", action="store_true", help="每个 trace 用其余 trace 建的模板（留一验证）")
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else check(a)


if __name__ == "__main__":
    raise SystemExit(main())
