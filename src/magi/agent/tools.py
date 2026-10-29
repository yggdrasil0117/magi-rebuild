from collections.abc import Callable


def add(a: int, b: int) -> int:
    return a + b


def multiply(a: int, b: int) -> int:
    return a * b


# 保存函数本身，此处不执行函数
TOOL_REGISTRY: dict[str, Callable[[int, int], int]] = {
    "add": add,
    "multiply": multiply,
}


def get_tool_definitions() -> list[dict]:
    descriptions = {
        "add": "计算两个整数之和",
        "multiply": "计算两个整数之积",
    }

    tools = []

    for name in TOOL_REGISTRY:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": descriptions[name],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                },
            }
        )

    return tools


def execute_tool(name: str, arguments: dict) -> int:
    if not isinstance(name, str):
        raise ValueError("工具名称必须是字符串")

    if name not in TOOL_REGISTRY:
        raise ValueError(f"不支持的工具：{name}")

    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是字典")

    if set(arguments) != {"a", "b"}:
        raise ValueError("工具必须恰好包含 a、b 两个参数")

    if not all(type(value) is int for value in arguments.values()):
        raise ValueError("工具参数必须是整数")

    function = TOOL_REGISTRY[name]
    return function(**arguments)