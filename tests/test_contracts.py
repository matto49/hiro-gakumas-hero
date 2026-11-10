"""契约：往返、schema 校验、与现有结构的互转、数据集样本。"""

import json
from pathlib import Path

import pytest

import hiro  # noqa: F401
from hiro.contracts import (
    CONTRACTS, Action, ActionKind, Candidate, Decision, ExecutionResult, GameState, LLMTrace, Observation, PageObs,
    PageState, Preview, SchemaError, Source, Step, step_type,
)  # fmt: skip
from hiro.contracts.schemas import OUT, dump

FIX = Path(__file__).with_name("fixtures")


def test_schema_files_match_code():
    for cls in CONTRACTS:
        assert (OUT / f"{cls.__name__}.json").read_text(encoding="utf-8") == dump(cls), f"运行 python -m hiro.contracts.schemas 更新 {cls.__name__}"


def test_action_roundtrip_and_legal_keys():
    for s in ("play:2", "drink:0", "skip"):
        a = Action.from_legal(s)
        assert a.legal_key() == s and Action.from_json(a.to_json()) == a
    a = Action.produce_next("training", attr="Da", sp=True)
    assert a.args["step_type"] == "OpenLessonDanceSp" and a.legal_key() is None
    assert step_type("class", "Vi") == "EventSchoolVisual" and step_type("rest") == "Refresh"
    assert step_type("training", "Vo", None) == "OpenLessonVocalNormal"
    with pytest.raises(ValueError):
        Action.from_legal("ProduceNext")
    with pytest.raises(SchemaError):
        Action.from_dict({"kind": "PlayCard", "args": {}, "version": "1.0", "slot": 1})  # 未知字段




def test_game_state_roundtrip_and_produce_state():
    g = GameState(mode="produce", page=PageState("ACTION_SELECT", "produce", 10, True, 3),
                  produce={"stamina": 22, "max_stamina": 33}, sources={"produce.stamina": Source("screen", 10)}, seq=10)  # fmt: skip
    g.validate()
    assert GameState.from_json(g.to_json()) == g
    assert g.produce_state() is None  # 字段不全
    from hiro.state.demo import SAMPLE

    full = GameState(mode="produce", produce=SAMPLE.to_dict())
    assert full.produce_state() == SAMPLE
    assert GameState(mode="nowhere").problems()
    assert GameState(sources={"x": Source("guess", 1)}).problems()




def test_observation_rejects_bad_source_and_unregistered_field():
    obs = Observation(1, 0.0, PageObs("HIF_EXAM"), fields={"hp": 3, "mana": 1}, source="guess")
    errs = obs.problems(strict=True)
    assert any("mana" in e for e in errs) and any("source" in e for e in errs)






def test_dataset_samples_validate(tmp_path):
    from hiro.contracts.samples import from_manifest

    ds = Path("datasets/20261029-224103-hif-qualify")
    if not ds.exists():
        pytest.skip("数据集不在本机")
    n = 0
    for obs in from_manifest(ds, limit=400):
        assert not obs.problems(strict=True), (obs.seq, obs.problems(strict=True))
        assert Observation.from_json(obs.to_json()) == obs
        n += 1
    assert n == 400
