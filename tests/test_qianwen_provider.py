import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_openai")
from langchain_core.messages import HumanMessage

from magi.council.service import Council, Opinion
from magi.harness.config import HarnessConfig, load_config
from magi.harness.model_factory import build_model
from magi.harness.runtime import open_personal_session


@pytest.fixture
def settings(tmp_path):
    return HarnessConfig(
        provider="qianwen",
        model="qwen3.8-max",
        base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
    )


def completion(content="测试回答", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "test",
        "model": "qwen3.8-max",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": message,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def stub_transport(model, create, monkeypatch):
    def raw(**kwargs):
        return SimpleNamespace(parse=lambda: create(**kwargs), headers={})

    transport = SimpleNamespace(create=raw, parse=raw)
    completions = SimpleNamespace(with_raw_response=transport)
    monkeypatch.setattr(model, "client", completions)
    monkeypatch.setattr(
        model,
        "root_client",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


def test_missing_key_fails_without_network(settings, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        build_model(settings)


@pytest.mark.parametrize(
    "url",
    [
        "http://maas.qianwenaiapi.com/compatible-mode/v1",
        "https://example.test/v1",
        "https://maas.qianwenaiapi.com:444/compatible-mode/v1",
    ],
)
def test_key_cannot_be_sent_to_unverified_endpoint(settings, url):
    with pytest.raises(ValueError, match="官方 HTTPS"):
        replace(settings, base_url=url)


def test_config_accepts_environment_name_not_literal_key(settings):
    with pytest.raises(ValueError, match="环境变量名称"):
        replace(settings, api_key_env="sk-ws-not-a-variable")


def test_factory_request_uses_cloud_parameters_and_no_retries(settings, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-test-key")
    model = build_model(settings)
    captured = []
    stub_transport(
        model, lambda **kwargs: captured.append(kwargs) or completion(), monkeypatch
    )
    assert model.invoke([HumanMessage(content="你好")]).text == "测试回答"
    assert model.max_retries == 0
    request = captured[0]
    assert request["extra_body"] == {
        "enable_thinking": False,
        "max_tokens": settings.num_predict,
    }
    assert "num_ctx" not in request and "num_predict" not in request
    assert "fake-test-key" not in repr(settings)


def test_council_uses_response_format_instead_of_ollama_format(settings, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-test-key")
    model = build_model(settings)
    captured = []
    stub_transport(
        model,
        lambda **kwargs: (
            captured.append(kwargs)
            or completion(
                json.dumps(
                    {
                        "view": "可行",
                        "recommendation": "先做",
                        "concerns": [],
                        "unknowns": [],
                        "evidence_refs": [],
                    }
                )
            )
        ),
        monkeypatch,
    )
    monkeypatch.setattr("magi.council.service.build_model", lambda config: model)
    snapshot = {
        "question": "测试",
        "time_budget_minutes": 30,
        "evidence_ids": ["user:current"],
    }
    reply = Council(settings)._invoke(
        "严格输出JSON", json.dumps(snapshot), Opinion, 768, 60
    )
    assert Opinion.model_validate_json(reply.text).view == "可行"
    request = captured[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"]
    assert "format" not in request


def test_cloud_harness_preserves_read_only_boundary(settings, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-test-key")
    model = build_model(settings)
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return completion(
                None,
                [
                    {
                        "id": "read-plans",
                        "type": "function",
                        "function": {"name": "magi_list_plans", "arguments": "{}"},
                    }
                ],
            )
        return completion("暂无计划")

    stub_transport(model, create, monkeypatch)
    with open_personal_session(settings, "cloud-test", model=model) as session:
        assert session.ask("查询计划") == "暂无计划"
    names = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert names == {
        "magi_read_file",
        "magi_list_files",
        "magi_get_plan",
        "magi_list_plans",
    }
    assert len(requests) == 2
    events = settings.events_path.read_text(encoding="utf-8")
    assert '"type": "tool_started"' in events
    assert "fake-test-key" not in events


def test_example_config_contains_no_credential():
    from pathlib import Path

    config = load_config(Path(__file__).resolve().parents[1] / "magi.qianwen.toml")
    assert config.provider == "qianwen" and config.api_key_env == "DASHSCOPE_API_KEY"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AUTHENTICATION_FAILED"),
        (403, "MODEL_ACCESS_DENIED"),
        (429, "RATE_LIMITED"),
    ],
)
def test_provider_failures_hide_response_secrets(status, code):
    import httpx
    import openai

    from magi.council.generation import failure_details

    response = httpx.Response(
        status,
        request=httpx.Request(
            "POST", "https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions"
        ),
    )
    error = openai.APIStatusError(
        "private-provider-message", response=response, body={"secret": "private"}
    )
    assert failure_details(error) == (code, [])
