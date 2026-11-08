"""Trace：每个决策点落盘「截图 + Observation + GameState + Decision + Preview + ExecutionResult」到 traces/<run>/。

    traces/20261031-120000/steps.jsonl   每行一个 Step（见下）
    traces/20261031-120000/frames/00012.jpg
    traces/20261031-120000/run.json      运行元数据（偶像、模式、适配器、开始时间…）

离线重放（hiro.loop.replay）：按 observations_before → observation → observations_after 的顺序重新 reduce，
每步结束时的 GameState 应与记录的 state 一致；规则部分的决策应与记录一致。
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from hiro.contracts.base import VERSION, Contract
from hiro.contracts.decision import Decision
from hiro.contracts.events import Check, Event, Prediction
from hiro.contracts.execution import ExecutionResult
from hiro.contracts.game_state import GameState
from hiro.contracts.observation import Observation
from hiro.contracts.preview import Preview

TRACES = Path("traces")
_SECRET = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")


def run_config(env=None, root: Path | None = None) -> dict[str, Any]:
    """可信 trace 库（使用者 2026-11-07）：每段运行记下所有 HIRO_* / VEIL_* 环境变量的实际值（名字里带 KEY / TOKEN 等的不记）、
    代码提交号、工作区有没有未提交的改动。原来 run.json 只有偶像 / 轮次，回放分不清当时开着哪些开关、跑的哪版代码。"""
    import os
    import subprocess

    env = os.environ if env is None else env
    flags = {k: v for k, v in sorted(env.items())
             if k.startswith(("HIRO_", "VEIL_")) and not any(w in k.upper() for w in _SECRET)}
    root = root or Path(__file__).resolve().parents[3]

    def git(*args):
        try:
            r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain", "--untracked-files=no")
    return {"env": flags, "commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


@dataclass
class Step(Contract):
    """主循环的一步。decision / execution 为 None 表示这一帧只更新了状态（转场、等待）。"""

    seq: int
    t: float
    observation: Observation
    state: GameState  # 决策输入：observation 并入后、执行前的状态（1.1 起；1.0 记的是执行后的状态）
    decision: Decision | None = None
    previews: list[Preview] = field(default_factory=list)
    execution: ExecutionResult | None = None
    observations_before: list[Observation] = field(default_factory=list)  # 上一步之后、决策点之前并入状态的其他观测（页面帧等），按时间序
    observations_after: list[Observation] = field(default_factory=list)  # 决策点之后并入状态的观测（存档、执行校验帧）
    notes: list[str] = field(default_factory=list)  # 故障恢复、冲突等说明
    timings_ms: dict[str, float] = field(default_factory=dict)  # observe / reduce / decide / act / total
    state_after: GameState | None = None  # 本步结束时的状态（并入 observations_after 之后）；与 state 相同时为 None
    events: list[Event] = field(default_factory=list)  # 1.2：本步产生 / 认出的事件
    predictions: list[Prediction] = field(default_factory=list)  # 1.2：事件之后的预测
    checks: list[Check] = field(default_factory=list)  # 1.2：互验结果（正常模式只写不一致的，调试模式全写）
    version: str = VERSION


class TraceWriter:
    def __init__(self, root: Path = TRACES, name: str | None = None, meta: dict[str, Any] | None = None):
        self.dir = root / (name or datetime.now().strftime("%Y%m%d-%H%M%S"))
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        # 续写同名 trace（run_hif resume --trace <同名>）：seq 接着已有的往下编，不覆盖已有的帧（D43：第三十三局本战续跑从 1 重编，
        # 前两段的 frames/000NN.jpg 被覆盖）；run.json 保留第一次的，这一次记进 segments
        self.seq = _last_seq(self.dir)
        self._steps = (self.dir / "steps.jsonl").open("a", encoding="utf-8")
        run = self.dir / "run.json"
        this = {"started": time.time(), **(meta or {}), "config": run_config()}
        try:
            old = json.loads(run.read_text(encoding="utf-8")) if run.exists() else None
        except (OSError, ValueError):
            old = None
        data = this if old is None else {**old, "segments": [*old.get("segments", []), {**this, "first_seq": self.seq + 1}]}
        run.write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def save_frame(self, seq: int, image) -> str:
        import cv2

        rel = f"frames/{seq:05d}.jpg"
        cv2.imwrite(str(self.dir / rel), image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return rel

    def write(self, step: Step) -> None:
        self._steps.write(step.to_json() + "\n")
        self._steps.flush()

    def close(self) -> None:
        self._steps.close()


def _last_seq(trace_dir: Path) -> int:
    """已有 trace 的最大序号（steps.jsonl 的 seq 与 frames/ 文件名取大的）；新目录为 0。"""
    last = 0
    steps = trace_dir / "steps.jsonl"
    if steps.exists():
        with steps.open(encoding="utf-8") as f:
            for line in f:
                try:
                    last = max(last, int(json.loads(line).get("seq") or 0))
                except (ValueError, AttributeError):
                    continue
    for fr in (trace_dir / "frames").glob("*.jpg"):
        if fr.stem.isdigit():
            last = max(last, int(fr.stem))
    return last


def read_steps(trace_dir: Path | str) -> Iterator[Step]:
    with (Path(trace_dir) / "steps.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Step.from_json(line)
