"""换卡卡格图片认卡（hiro.vision.card_cells）的离线检查：在使用者自己的截图上比 KAA 与图片认卡，并做画质扰动。

    PYTHONPATH=src python tools/assets/check_card_cells.py <截图或目录>... [--kind auto|grid|dialog|change1] [--perturb] [--lib cache/card_icons.npz]

每张截图按 --kind 找卡格（auto：先按换卡阶段 1 的三张候选，再按选卡弹窗卡格，最后按一览网格），对每格输出
KAA 结果、图片认卡结果（分数 / 领先 / 是否认出）和二者关系：agree（一致）、fill（KAA 认不出、图片认出）、conflict（都认出但不同）、
miss（图片没认出）。--perturb 另把每格缩放到 87 / 124 / 148 像素、模糊、压暗一半、JPEG 质量 50、框往左上 / 右下偏 3 像素后再认，
报告结果是同一张、认不出还是认成别的。
截图只在本地读，不写出任何图片。
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

CHANGE2_GRID = (66, 623, 641, 1106)  # 同 hiro.loop.kaa_patches.CHANGE2_CARD_AREA
CHANGE1_CELL = 124


def frames(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        out += sorted(q for q in p.rglob("*") if q.suffix.lower() in (".png", ".jpg")) if p.is_dir() else [p]
    return out


def cells(img, kind: str) -> list[tuple[str, tuple[int, int, int, int], str | None]]:
    """→ [(kind, rect, KAA card_id)]。"""
    from hiro.vision import deck

    if kind == "auto" and deck.is_deck_list(img):
        kind = "grid"
    if kind in ("auto", "change1"):
        r = deck.read_change_candidates(img)
        if len(r["candidates"]) == 3 or kind == "change1":
            return [("change1", (c["center"][0] - CHANGE1_CELL // 2, c["center"][1] - CHANGE1_CELL // 2, CHANGE1_CELL, CHANGE1_CELL),
                     c["card_id"]) for c in r["candidates"]]  # fmt: skip
    if kind in ("auto", "dialog"):
        got = deck.read_card_grid(img, CHANGE2_GRID, "dialog")
        if got or kind == "dialog":
            return [("dialog", c["rect"], c["card_id"]) for c in got]
    if kind == "auto":
        return []
    got = deck.read_card_grid(img, deck.LIST_VIEW, "list")
    return [("grid", c["rect"], c["card_id"]) for c in got if c["rect"][1] >= GRID_TOP]


GRID_TOP = 500  # 一览网格：详情框展开时盖到 y≈500，上面露半行的卡格不算


def perturbations(cell: np.ndarray) -> dict[str, np.ndarray]:
    out = {f"size{s}": cv2.resize(cell, (s, s), interpolation=cv2.INTER_AREA if s < cell.shape[0] else cv2.INTER_CUBIC) for s in (87, 124, 148)}
    out["blur"] = cv2.GaussianBlur(cell, (0, 0), 1.5)
    out["dim"] = cv2.convertScaleAbs(cell, alpha=0.5)
    ok, buf = cv2.imencode(".jpg", cell, [cv2.IMWRITE_JPEG_QUALITY, 50])
    out["jpeg50"] = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--kind", default="auto", choices=("auto", "grid", "dialog", "change1"))
    ap.add_argument("--perturb", action="store_true")
    ap.add_argument("--lib", help="渲染库路径（默认 cache/card_icons.npz）")
    ap.add_argument("--json", help="逐格结果写到这个 JSON")
    a = ap.parse_args(argv)
    import os

    if a.lib:
        os.environ["HIRO_CARD_ICON_LIB"] = a.lib
    from hiro.vision import card_cells

    lib = card_cells.library()
    if lib is None:
        print("没有渲染库：先运行 python -m hiro.vision.card_icon_render build（或用 --lib 指定）", file=sys.stderr)
        return 2
    rows, seen, t_total, n_cells = [], set(), 0.0, 0
    for f in frames(a.paths):
        img = cv2.imread(str(f))
        if img is None or img.mean() < 20:
            continue
        key = hash(img.tobytes())
        if key in seen:
            continue
        seen.add(key)
        for kind, rect, kaa in cells(img, a.kind):
            t = time.perf_counter()
            m = card_cells.match(img, rect, lib)
            t_total += time.perf_counter() - t
            n_cells += 1
            relation = ("agree" if kaa in m["ids"] else "conflict") if kaa and m["known"] else ("fill" if m["known"] else "miss")
            row = {"file": str(f), "kind": kind, "rect": list(rect), "kaa": kaa, **m, "relation": relation}
            if a.perturb and m["known"]:
                x, y, w, h = rect
                cell = img[y : y + h, x : x + w]
                row["perturb"] = {}
                shifted = {f"shift{d:+d}": img[y + d : y + d + h, x + d : x + d + w] for d in (-3, 3)}
                for name, p in {**perturbations(cell), **shifted}.items():
                    r = card_cells.match(img, (x + int(name[5:]), y + int(name[5:]), w, h), lib) if name.startswith("shift") else card_cells.match(p, None, lib)
                    row["perturb"][name] = "same" if r["known"] and r["ids"] == m["ids"] else ("wrong" if r["known"] else "unknown")
            rows.append(row)
    by = collections.Counter((r["kind"], r["relation"]) for r in rows)
    print(f"{len(seen)} 张截图，{n_cells} 格，平均每格 {t_total / max(n_cells, 1) * 1000:.0f} ms")
    for kind in sorted({k for k, _ in by}):
        print(f"  {kind}: " + "、".join(f"{rel} {by[(kind, rel)]}" for rel in ("agree", "fill", "conflict", "miss") if by[(kind, rel)]))
    for r in rows:
        if r["relation"] in ("conflict", "fill"):
            print(f"  {r['relation']:8s} {Path(r['file']).name} {r['rect'][:2]} KAA={r['kaa']} 图片={r['ids']} 分 {r['score']} 领先 {r['margin']}")
    if a.perturb:
        stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for r in rows:
            for name, res in (r.get("perturb") or {}).items():
                stats[name][res] += 1
        print("  扰动（对认出的格子）：同一张 / 认不出 / 认成别的")
        for name, c in stats.items():
            print(f"    {name:7s} {c['same']} / {c['unknown']} / {c['wrong']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
