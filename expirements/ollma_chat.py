import json

import ollama

from magi.agent import response_validation
from magi.agent.response_validation import validate_choice, parse_and_validate_response

client = ollama.Client(host="http://127.0.0.1:11434")

response = client.chat(
    model="qwen3.5:4b",
    format="json",

    messages=[
        {
            "role": "system",
            "content": "你是 MAGI 系统中的 MELCHIOR。你应从证据、逻辑和可行性角度分析问题。证据不足时，应明确指出，不得编造依据。"
        },
        {
            "role": "user",
            "content": (
                "方案A：直接使用LangGraph。"
                "方案B：先使用Ollama Python SDK。"
                "已知我已经成功调用本地模型。"
                "请仅输出JSON对象，包含choice、reasoning、evidence_refs三个字段。"
                "choice只能为A或B，reasoning为理由字符串，"
                "evidence_refs为证据ID数组，没有可核验的证据ID时使用空数组。"
                "MAGI是我自己开发的项目，不存在外部官方开发规范。"
            )
        }
    ]
)
# content = response.message.content
content = response.message.content

try:
    data = parse_and_validate_response(content)
except ValueError as exc:
    print("模型回复校验失败：", exc)
else:
    print("通过校验的选择：", data["choice"])
    print("通过校验的理由：", data["reasoning"])
print(content)
print(validate_choice({"choice": "A"}))
print(validate_choice({"choice": "C"}))
print(validate_choice({"reasoning": "证据不足"}))
