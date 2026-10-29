from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from magi.agent.runner import run_agent
from magi.agent.tools import execute_tool


def tool_response(name, arguments):
    return NS(
        message=NS(
            content="",
            tool_calls=[
                NS(function=NS(name=name, arguments=arguments))
            ],
        )
    )


def answer_response(content):
    return NS(message=NS(content=content, tool_calls=[]))


def make_fake_chat(responses):
    remaining = iter(responses)
    requests = []

    def fake_chat(**kwargs):
        requests.append(deepcopy(kwargs))
        return next(remaining)

    return fake_chat, requests


def test_two_tools_then_answer():
    first = tool_response("add", {"a": 17, "b": 25})
    second = tool_response("multiply", {"a": 42, "b": 3})

    chat, requests = make_fake_chat([
        first,
        second,
        answer_response("126"),
    ])

    original_messages = [{"role": "user", "content": "计算两步"}]

    result = run_agent(
        chat=chat,
        execute_tool=execute_tool,
        model="fake-model",
        messages=original_messages,
        tools=[],
    )

    assert result == "126"
    assert len(requests) == 3

    # 第二次请求收到第一轮的模型调用记录和工具结果
    history = requests[1]["messages"]
    assert history[1].tool_calls[0].function.name == "add"
    assert history[2] == {
        "role": "tool",
        "tool_name": "add",
        "content": "42",
    }

    # 第三次请求收到第二轮工具结果
    assert requests[2]["messages"][-1]["content"] == "126"

    # 函数没有修改调用方传入的消息
    assert original_messages == [
        {"role": "user", "content": "计算两步"}
    ]


def test_direct_answer():
    chat, requests = make_fake_chat([answer_response("你好")])

    result = run_agent(
        chat=chat,
        execute_tool=execute_tool,
        model="fake-model",
        messages=[],
        tools=[],
    )

    assert result == "你好"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "budget, error",
    [
        ({"max_model_requests": 1}, "模型请求次数"),
        ({"max_tool_calls": 0}, "工具调用次数"),
    ],
)
def test_budget_stops_before_execution(budget, error):
    chat, requests = make_fake_chat([
        tool_response("add", {"a": 1, "b": 2}),
    ])
    executed = []

    def recording_executor(name, arguments):
        executed.append(name)
        return 3

    with pytest.raises(RuntimeError, match=error):
        run_agent(
            chat=chat,
            execute_tool=recording_executor,
            model="fake-model",
            messages=[],
            tools=[],
            **budget,
        )

    assert executed == []
    assert len(requests) == 1


def test_invalid_arguments_stop_the_loop():
    chat, requests = make_fake_chat([
        tool_response("add", {"a": "错误", "b": 2}),
    ])

    with pytest.raises(ValueError, match="工具参数必须是整数"):
        run_agent(
            chat=chat,
            execute_tool=execute_tool,
            model="fake-model",
            messages=[],
            tools=[],
        )

    assert len(requests) == 1


def test_empty_answer_is_rejected():
    chat, _ = make_fake_chat([answer_response("   ")])

    with pytest.raises(ValueError, match="有效回答"):
        run_agent(
            chat=chat,
            execute_tool=execute_tool,
            model="fake-model",
            messages=[],
            tools=[],
        )
def test_application_can_finish_tool_phase_before_another_model_request():
    chat, requests = make_fake_chat([
        tool_response("add", {"a": 1, "b": 2}),
    ])
    events = []
    run_agent(
        chat=chat,
        execute_tool=execute_tool,
        model="fake",
        messages=[],
        tools=[],
        events=events,
        should_stop=lambda: True,
    )
    assert len(requests) == 1
    assert events[-1]["type"] == "run_stopped"
    assert events[-2]["type"] == "tool_succeeded"
    assert not any(event["type"] == "run_completed" for event in events)


def test_records_successful_execution():
    chat, _ = make_fake_chat([
        tool_response("add", {"a": 17, "b": 25}),
        answer_response("42"),
    ])
    events = []

    result = run_agent(
        chat=chat,
        execute_tool=execute_tool,
        model="fake-model",
        messages=[],
        tools=[],
        events=events,
    )

    assert result == "42"
    assert [event["type"] for event in events] == [
        "model_request",
        "model_response",
        "tool_started",
        "tool_succeeded",
        "model_request",
        "model_response",
        "run_completed",
    ]
    assert events[3] == {
        "type": "tool_succeeded",
        "call_number": 1,
        "name": "add",
        "result_chars": 2,
    }
    assert events[-1]["model_requests"] == 2
    assert events[-1]["tool_calls"] == 1


def test_records_failure_and_preserves_exception():
    chat, requests = make_fake_chat([
        tool_response("read_file", {"path": "missing.txt"}),
    ])
    events = []
    original_error = FileNotFoundError("测试文件不存在")

    def failing_executor(name, arguments):
        raise original_error

    with pytest.raises(FileNotFoundError) as caught:
        run_agent(
            chat=chat,
            execute_tool=failing_executor,
            model="fake-model",
            messages=[],
            tools=[],
            events=events,
        )

    assert caught.value is original_error
    assert len(requests) == 1
    assert [event["type"] for event in events][-2:] == [
        "tool_failed",
        "run_failed",
    ]
    assert events[-2]["error_type"] == "FileNotFoundError"
    assert not any(
        event["type"] == "run_completed" for event in events
    )
