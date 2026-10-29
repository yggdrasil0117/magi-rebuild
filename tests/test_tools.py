import pytest

from magi.agent.tools import execute_tool, get_tool_definitions


@pytest.mark.parametrize(
    "name, arguments, expected",
    [
        ("add", {"a": 17, "b": 25}, 42),
        ("multiply", {"a": 6, "b": 7}, 42),
        ("multiply", {"a": -3, "b": 4}, -12),
    ],
)
def test_execute_tool(name, arguments, expected):
    assert execute_tool(name, arguments) == expected


@pytest.mark.parametrize(
    "name, arguments, error",
    [
        ("unknown", {"a": 1, "b": 2}, "不支持的工具"),
        ("add", None, "工具参数必须是字典"),
        ("add", {"a": 1}, "恰好包含"),
        ("add", {"a": 1, "b": 2, "c": 3}, "恰好包含"),
        ("add", {"a": "1", "b": 2}, "参数必须是整数"),
        ("add", {"a": True, "b": 2}, "参数必须是整数"),
    ],
)
def test_reject_invalid_call(name, arguments, error):
    with pytest.raises(ValueError, match=error):
        execute_tool(name, arguments)


def test_advertised_tools_can_be_executed():
    for tool in get_tool_definitions():
        name = tool["function"]["name"]
        assert type(execute_tool(name, {"a": 2, "b": 3})) is int