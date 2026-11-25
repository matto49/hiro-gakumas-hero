"""Trace：每个决策点落盘「截图 + Observation + GameState + Decision + Preview + ExecutionResult」到 traces/<run>/。

    traces/20261031-120000/steps.jsonl   每行一个 Step（见下）
    traces/20261031-120000/frames/00012.jpg
    traces/20261031-120000/run.json      运行元数据（偶像、模式、适配器、开始时间…）

离线重放（hiro.loop.replay）：按 observations_before → observation → observations_after 的顺序重新 reduce，
每步结束时的 GameState 应与记录的 state 一致；规则部分的决策应与记录一致。
"""

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hiro import imgio
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
    observations_before: list[Observation] = field(
        default_factory=list
    )  # 上一步之后、决策点之前并入状态的其他观测（页面帧等），按时间序
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
        self._native_known = False
        this = {"started": time.time(), "platform": _platform_meta(), **(meta or {})}
        try:
            old = json.loads(run.read_text(encoding="utf-8")) if run.exists() else None
        except (OSError, ValueError):
            old = None
        data = (
            this
            if old is None
            else {**old, "segments": [*old.get("segments", []), {**this, "first_seq": self.seq + 1}]}
        )
        run.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def save_frame(self, seq: int, image) -> str:
        import cv2

        rel = f"frames/{seq:05d}.jpg"
        imgio.imwrite(str(self.dir / rel), image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self._native_frame(seq, image)
        return rel

    def _native_frame(self, seq: int, image) -> None:
        """这一帧是刚截的那张：第一次补 run.json 的原生尺寸；HIRO_TRACE_NATIVE=1 时另存原生 PNG（OCR 测试集采集用）。出错不影响存帧。"""
        try:
            from hiro.devices.base import native_for

            native, size = native_for(image)
            if size is None:
                return
            if not self._native_known:
                self._native_known = True
                _set_native(self.dir / "run.json", list(size))
            if native is not None:
                imgio.imwrite(str(self.dir / f"frames/{seq:05d}.native.png"), native)
        except Exception:  # noqa: BLE001  采集旁路不能打断对局
            pass

    def write(self, step: Step) -> None:
        self._steps.write(step.to_json() + "\n")
        self._steps.flush()

    def close(self) -> None:
        self._steps.close()


def _platform_meta() -> dict[str, Any]:
    from hiro.devices.base import platform_meta

    return platform_meta()


def _set_native(run: Path, size: list[int]) -> None:
    """run.json 里这一段的 platform.native 还空着就填上（续写的 trace 填最后一段的）。"""
    try:
        data = json.loads(run.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    target = data["segments"][-1] if data.get("segments") else data
    plat = target.get("platform")
    if isinstance(plat, dict) and plat.get("native") is None:
        plat["native"] = size
        run.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


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
