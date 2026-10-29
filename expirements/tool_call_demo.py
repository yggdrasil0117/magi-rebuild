import ollama

from magi.agent.runner import run_agent
from magi.agent.tools import execute_tool, get_tool_definitions


client = ollama.Client(
    host="http://127.0.0.1:11434",
    timeout=60.0,
)

tools = get_tool_definitions()

messages = [
    {
        "role": "system",
        "content": (
            "你可以多轮调用工具。计算任务必须使用工具。"
            "需要前一步结果时，先取得结果再调用下一工具。"
            "全部计算完成后再给出最终回答。"
        ),
    },
    {
        "role": "user",
        "content": "先计算 17 + 25，再把结果乘以 3。",
    },
]

MAX_MODEL_REQUESTS = 5
MAX_TOOL_CALLS = 4
tool_call_count = 0
answer = run_agent(
    chat=client.chat,
    execute_tool=execute_tool,
    model="qwen3.5:4b",
    messages=messages,
    tools=tools,
    max_model_requests=5,
    max_tool_calls=4,
)

print("模型回答：", answer)