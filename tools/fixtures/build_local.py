"""把使用者自己的截图 / trace 帧整理成 tests/fixtures 下的本地样例（目录已被 .gitignore 挡住，不进仓库）。

    PYTHONPATH=src .venv/bin/python tools/fixtures/build_local.py --walkthrough ~/Desktop/未命名.md --traces traces

两类来源：
- 流程截图笔记（Typora markdown，按顺序贴一张截图写一句说明）：每张按 WALKTHROUGH 的顺序和名字缩放成 720×1280，
  写到 tests/fixtures/walkthrough/<名>.png。截图是手动框选的，边缘可能多 / 少几个像素，用例按容差比位置。
- trace 帧（traces/<run>/frames/NNNNN.jpg，实机 720×1280）：按 TRACE_FRAMES 原样复制到 tests/fixtures/trace/<名>.jpg。
  标注值写在用例里（看图核对过，不是照抄识别结果）。
笔记或 trace 缺哪张就跳过哪张，用例也只跳过那一条（conftest 按文件判断）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures"

# 笔记里截图的顺序 → 样例名（2026-11-07 使用者的 H.I.F 流程笔记，21 张；第 6 张是牌组局部，不是整屏）
WALKTHROUGH = [
    "01-hif-top", "02-idol-select", "03-start-confirm", "04-lesson-three", "05-lesson-options", None,
    "07-public-lesson", "08-drink-select", "09-outing-supply", "10-card-acquire", "11-consult-action",
    "12-consult-shop", "13-r1-result", "14-interval-shop", "15-r2-exam", "16-r2-lost", "17-retry-confirm",
    "18-final-evaluation", "19-memory-generate", "20-memory-result", "21-produce-complete",
]

# (样例名, trace 目录, 帧号)：看图核对过的考试 / 结算 / 轮间画面
TRACE_FRAMES = [
    ("exam-strong2-anim", "batch-codex-20261107-1-1-exam1", 24),
    ("exam-warm2", "batch-codex-20261107-1-1-exam1", 19),
    ("exam-strong1", "batch-codex-20261107-1-1-exam1", 3),
    ("exam-open-mixed-plus", "batch-codex-20261107-1-1-exam1", 1),
    ("r1-result-first", "batch-codex-20261107-1-1-exam1", 44),
    ("interval-shop-empty-bar", "veil-hif-20261107-r4", 27),
    ("produce-end", "veil-hif-20261107-r9", 33),
]

_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def walkthrough(md: Path) -> int:
    paths = [Path(p) for p in _IMG.findall(md.read_text(encoding="utf-8"))]
    if len(paths) != len(WALKTHROUGH):
        print(f"笔记里有 {len(paths)} 张截图，WALKTHROUGH 写了 {len(WALKTHROUGH)} 个名字：按顺序对应，多出的忽略", file=sys.stderr)
    done = 0
    (OUT / "walkthrough").mkdir(parents=True, exist_ok=True)
    for name, path in zip(WALKTHROUGH, paths):
        if name is None:
            continue
        img = cv2.imread(str(path))
        if img is None:
            print(f"缺截图：{path}", file=sys.stderr)
            continue
        cv2.imwrite(str(OUT / "walkthrough" / f"{name}.png"), cv2.resize(img, (720, 1280), interpolation=cv2.INTER_AREA))
        done += 1
    return done


def traces(root: Path) -> int:
    done = 0
    (OUT / "trace").mkdir(parents=True, exist_ok=True)
    for name, run, seq in TRACE_FRAMES:
        src = root / run / "frames" / f"{seq:05d}.jpg"
        if not src.is_file():
            print(f"缺 trace 帧：{src}", file=sys.stderr)
            continue
        shutil.copyfile(src, OUT / "trace" / f"{name}.jpg")
        done += 1
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--walkthrough", type=Path, help="流程截图笔记（markdown）")
    ap.add_argument("--traces", type=Path, help="traces 目录")
    a = ap.parse_args(argv)
    if a.walkthrough:
        print(f"walkthrough：{walkthrough(a.walkthrough.expanduser())} 张")
    if a.traces:
        print(f"trace：{traces(a.traces.expanduser())} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
