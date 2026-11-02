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
        self._steps = (self.dir / "steps.jsonl").open("a", encoding="utf-8")
        self.seq = 0
        (self.dir / "run.json").write_text(json.dumps({"started": time.time(), **(meta or {})}, ensure_ascii=False, indent=1))

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


def read_steps(trace_dir: Path | str) -> Iterator[Step]:
    with (Path(trace_dir) / "steps.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Step.from_json(line)
