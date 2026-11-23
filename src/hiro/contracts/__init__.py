"""模块间数据契约的单一真源。

    Observation      识别 → 状态      hiro.vision.observe 产出
    GameState        状态 → 决策      只由 hiro.state.reduce 修改
    Action           决策 → 操作      对齐 Api.Produce*Request，不含坐标
    Decision         决策层输出
    Preview          预演层输出
    ExecutionResult  操作 → 主循环
    Step / Trace     每个决策点的落盘记录

每个对象：dataclass + JSON schema（schema()）+ version + to_json/from_json + validate()。
schema 文件：python -m hiro.contracts.schemas 生成到 contracts/schemas/*.json。字段变更记入 docs/contracts.md。
"""

from hiro.contracts.action import EXAM_KINDS, Action, ActionKind, step_type
from hiro.contracts.base import VERSION, Contract, SchemaError, check, schema_for
from hiro.contracts.decision import Candidate, Decision, LLMTrace, llm_trace_from_call
from hiro.contracts.events import EVENT_KINDS, Check, Event, Expect, Prediction
from hiro.contracts.execution import ExecutionResult
from hiro.contracts.game_state import MODES, PRIORITY, ExamSession, GameState, PageState, Source
from hiro.contracts.observation import FIELD_CATALOG, FieldMeta, Observation, PageObs
from hiro.contracts.preview import Preview
from hiro.contracts.trace import Step, TraceWriter, read_steps

CONTRACTS = (Observation, GameState, Action, Decision, Preview, ExecutionResult, Step, Event, Prediction, Check)

__all__ = [
    "CONTRACTS",
    "EVENT_KINDS",
    "EXAM_KINDS",
    "FIELD_CATALOG",
    "MODES",
    "PRIORITY",
    "VERSION",
    "Action",
    "ActionKind",
    "Candidate",
    "Check",
    "Contract",
    "Decision",
    "Event",
    "ExamSession",
    "ExecutionResult",
    "Expect",
    "FieldMeta",
    "GameState",
    "LLMTrace",
    "Observation",
    "PageObs",
    "PageState",
    "Prediction",
    "Preview",
    "SchemaError",
    "Source",
    "Step",
    "TraceWriter",
    "check",
    "llm_trace_from_call",
    "read_steps",
    "schema_for",
    "step_type",
]  # fmt: skip
