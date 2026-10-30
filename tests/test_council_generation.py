import json

import pytest

pytest.importorskip("langchain_ollama")
from httpx import ReadTimeout
from langchain_core.messages import AIMessage

from magi.council.generation import generation_schema, model_snapshot
from magi.council.service import (
    Candidate,
    Council,
    Opinion,
    Synthesis,
    render,
)
from magi.council.sources import source_options
from magi.harness.config import HarnessConfig

OPINION = {
    "view": "可行性",
    "recommendation": "先诊断",
    "concerns": [],
    "unknowns": [],
    "evidence_refs": [],
}
SYNTHESIS = {
    "agreement": ["先做诊断"],
    "disagreements": [],
    "unknowns": [],
    "candidate": {
        "title": "调整计划",
        "steps": [
            {"action": "诊断", "done_when": "记录卡点", "minutes": 30, "kind": "work"}
        ],
        "assumptions": [],
        "revision_reason": "缩减实验，保留必要诊断",
    },
}


@pytest.fixture
def settings(tmp_path):
    return HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
    )


def snapshot():
    return {
        "run_id": "TRACE-ID-NOT-EVIDENCE",
        "logical_session_id": "internal-thread",
        "epoch": 3,
        "question": "只剩30分钟，调整计划",
        "time_budget_minutes": 30,
        "memories": [],
        "history": [],
        "old_plan": None,
        "evidence_ids": ["user:current"],
    }


def test_generation_schema_limits_known_refs_and_preserves_business_title():
    original = Opinion.model_json_schema()
    opinion = generation_schema(Opinion, snapshot())
    assert opinion["properties"]["evidence_refs"]["items"]["enum"] == ["user:current"]
    assert Opinion.model_json_schema() == original
    synthesis = generation_schema(Synthesis, snapshot())
    candidate = synthesis["properties"]["candidate"]
    assert "title" in candidate["properties"] and "title" in candidate["required"]
    step = candidate["properties"]["steps"]["items"]
    assert "minutes" in step["required"]
    assert step["properties"]["minutes"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 30,
    }
    assert "$ref" not in json.dumps(synthesis)
    assert candidate["properties"]["steps"]["maxItems"] == 6
    assert "kind" not in step["required"]
    assert "source_excerpts" not in candidate["properties"]
    assert "source_ids" in candidate["required"]
    assert "unknowns" in candidate["required"]
    # Read historical candidates under the original domain contract.
    old = SYNTHESIS["candidate"] | {
        "title": "旧计划" * 40,
        "steps": [{"action": "旧步骤", "done_when": "复核"}],
    }
    assert Candidate(**old).steps[0].minutes is None


def test_no_evidence_allows_only_empty_refs():
    schema = generation_schema(Opinion, snapshot() | {"evidence_ids": []})
    assert schema["properties"]["evidence_refs"]["maxItems"] == 0


def test_actual_model_receives_grounded_schema_without_execution_ids(
    settings, monkeypatch
):
    seen = {}

    class Model:
        def bind(self, **kwargs):
            seen.update(kwargs)
            return self

        def invoke(self, messages):
            seen["messages"] = messages
            return AIMessage(
                content=json.dumps(OPINION),
                response_metadata={"done_reason": "stop", "eval_count": 80},
            )

    monkeypatch.setattr("magi.council.service.build_model", lambda settings: Model())
    council = Council(settings)
    reply = council._invoke("角色指令", json.dumps(snapshot()), Opinion, 768, 60)
    assert reply.response_metadata["eval_count"] == 80
    assert (
        json.dumps(seen["format"], ensure_ascii=False, separators=(",", ":"))
        in seen["messages"][0].text
    )
    assert "TRACE-ID-NOT-EVIDENCE" not in seen["messages"][1].text
    assert json.loads(seen["messages"][1].text) == model_snapshot(snapshot())
    council._invoke(
        "综合",
        json.dumps({"snapshot": snapshot(), "opinions": []}),
        Synthesis,
        1536,
        60,
    )
    data = json.loads(seen["messages"][1].text)
    assert data["snapshot"]["source_options"] == source_options(snapshot())
    props = seen["format"]["properties"]["candidate"]["properties"]
    assert "source_excerpts" not in props
    assert props["source_ids"]["items"]["enum"] == ["source:0"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("invalid_json", "INVALID_JSON"),
        ("invalid_field", "INVALID_FIELDS"),
        ("unknown_ref", "UNKNOWN_EVIDENCE"),
        ("truncated", "TRUNCATED"),
        ("timeout", "TIMEOUT"),
        ("forbidden_tool", "FORBIDDEN_TOOL"),
    ],
)
def test_persona_failures_are_specific_and_never_create_candidate(
    settings, mode, expected
):
    calls = []

    def invoke(*args):
        calls.append(args)
        if mode == "timeout":
            raise ReadTimeout("private-transport-details")
        if mode == "invalid_json":
            return "private-invalid-model-output"
        if mode == "invalid_field":
            return json.dumps(
                OPINION | {"view": "", "secret": "private-invalid-model-output"}
            )
        if mode == "unknown_ref":
            return json.dumps(OPINION | {"evidence_refs": ["invented"]})
        if mode == "forbidden_tool":
            return AIMessage(
                content="", tool_calls=[{"name": "write_file", "args": {}, "id": "bad"}]
            )
        return AIMessage(
            content=json.dumps(OPINION),
            response_metadata={"done_reason": "length", "eval_count": 768},
        )

    result = Council(settings, invoke).run(snapshot())
    assert result["status"] == "FAILED" and result["candidate"] is None
    assert len(calls) == 3
    assert all(p["error_code"] == expected for p in result["personas"])
    assert "private-invalid-model-output" not in json.dumps(result)
    assert "private-transport-details" not in json.dumps(result)
    assert expected in render(result)
    if mode == "truncated":
        assert result["personas"][0]["diagnostics"]["eval_count"] == 768
    if mode == "invalid_field":
        assert {e["field"] for e in result["personas"][0]["validation_errors"]} == {
            "view",
            "secret",
        }


@pytest.mark.parametrize(
    "minutes,expected", [(None, "MISSING_STEP_MINUTES"), (31, "TIME_BUDGET_EXCEEDED")]
)
def test_invalid_budget_is_not_silently_repaired(settings, minutes, expected):
    def invoke(system, payload, schema, *args):
        if schema is Opinion:
            return json.dumps(OPINION)
        result = json.loads(json.dumps(SYNTHESIS))
        result["candidate"]["steps"][0]["minutes"] = minutes
        return json.dumps(result)

    result = Council(settings, invoke).run(snapshot())
    assert result["status"] == "PARTIAL" and result["candidate"] is None
    assert result["calls"] == 4 and result["synthesis_error_code"] == expected
    assert expected in render(result)
    if minutes is not None:
        assert result["synthesis_diagnostics"]["total_minutes"] == minutes


def test_success_retains_usage_and_four_call_limit(settings):
    def invoke(system, payload, schema, *args):
        return AIMessage(
            content=json.dumps(OPINION if schema is Opinion else SYNTHESIS),
            response_metadata={
                "done_reason": "stop",
                "eval_count": 90,
                "prompt_eval_count": 500,
            },
        )

    result = Council(settings, invoke).run(snapshot())
    assert result["status"] == "COMPLETED" and result["calls"] == 4
    assert result["synthesis_diagnostics"]["total_minutes"] == 30
    assert result["synthesis_diagnostics"]["prompt_eval_count"] == 500


def test_selected_source_is_resolved_from_original_instead_of_model_copy(settings):
    def invoke(system, payload, schema, *args):
        if schema is Opinion:
            return json.dumps(OPINION)
        result = json.loads(json.dumps(SYNTHESIS))
        result["candidate"]["source_ids"] = ["source:0"]
        return json.dumps(result)

    result = Council(settings, invoke).run(snapshot())
    assert result["status"] == "COMPLETED"
    assert result["candidate"]["source_excerpts"] == [
        {"evidence_ref": "user:current", "quote": snapshot()["question"]}
    ]


@pytest.mark.parametrize(
    "ids,excerpts,code",
    [
        (["source:404"], [], "UNKNOWN_SOURCE_SELECTION"),
        (["source:0", "source:0"], [], "UNKNOWN_SOURCE_SELECTION"),
        (
            ["source:0"],
            [{"evidence_ref": "user:current", "quote": "只剩30分钟"}],
            "MIXED_SOURCE_FORMATS",
        ),
    ],
)
def test_invalid_source_selection_is_not_repaired(settings, ids, excerpts, code):
    def invoke(system, payload, schema, *args):
        if schema is Opinion:
            return json.dumps(OPINION)
        value = json.loads(json.dumps(SYNTHESIS))
        value["candidate"].update(source_ids=ids, source_excerpts=excerpts)
        return json.dumps(value)

    result = Council(settings, invoke).run(snapshot())
    assert result["candidate"] is None and result["synthesis_error_code"] == code


def test_source_catalog_is_bounded_and_never_invents_quotes():
    snap = snapshot() | {"question": "原文。" * 2000}
    options = source_options(snap)
    assert len(options) == 24
    assert all(
        o["quote"] in snap["question"] and len(o["quote"]) <= 160 for o in options
    )
    assert source_options(snap | {"evidence_ids": []}) == []


@pytest.mark.parametrize(
    "exception,phase",
    [
        ("ConnectTimeout", "connect"),
        ("ReadTimeout", "read"),
        ("WriteTimeout", "write"),
        ("PoolTimeout", "pool"),
    ],
)
def test_timeout_diagnostics_follow_cause_without_leaking_details(
    settings, exception, phase
):
    import httpx

    def invoke(*args):
        try:
            raise getattr(httpx, exception)("private-request-secret")
        except httpx.TimeoutException as exc:
            raise TimeoutError("private-sdk-error") from exc

    result = Council(settings, invoke).run(snapshot())
    assert result["candidate"] is None and result["calls"] == 3
    assert all(p["diagnostics"]["timeout_phase"] == phase for p in result["personas"])
    assert all(
        p["diagnostics"]["timeout_scope"] == "request" for p in result["personas"]
    )
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("late_stage", ["persona", "synthesis"])
def test_results_arriving_after_workflow_deadline_are_not_accepted(
    settings, monkeypatch, late_stage
):
    now = [0.0]
    monkeypatch.setattr("magi.council.service.time.monotonic", lambda: now[0])

    def invoke(system, payload, schema, *args):
        if (schema is Opinion) == (late_stage == "persona"):
            now[0] += 241
        return json.dumps(OPINION if schema is Opinion else SYNTHESIS)

    result = Council(settings, invoke).run(snapshot())
    assert result["candidate"] is None
    if late_stage == "persona":
        assert result["calls"] == 1
        assert all(
            p["diagnostics"]["timeout_scope"] == "workflow" for p in result["personas"]
        )
    else:
        assert result["calls"] == 4
        assert result["synthesis_diagnostics"]["timeout_scope"] == "workflow"
