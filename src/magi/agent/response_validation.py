import json


def validate_choice(data):
    if "choice" in data:
        choice = data["choice"]

        if choice in ("A", "B"):
            return "valid"
        else:
            return "invalid"
    else:
        return "missing"


def parse_and_validate_response(content: str) -> dict:
    # 1. 把 JSON 字符串解析成 Python 对象
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("回复不是合法 JSON") from exc

    # 2. 确认顶层结构是字典
    if not isinstance(data, dict):
        raise ValueError("回复必须是 JSON 对象")

    # 3. 确认所有必需字段都存在
    for field in ("choice", "reasoning", "evidence_refs"):
        if field not in data:
            raise ValueError(f"缺少字段：{field}")

    # 4. 检查选项
    choice = data["choice"]
    if not isinstance(choice, str) or choice not in ("A", "B"):
        raise ValueError("choice 必须是 A 或 B")

    # 5. 检查理由
    reasoning = data["reasoning"]
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("reasoning 必须是非空字符串")

    # 6. 检查引用列表及其中每个元素
    evidence_refs = data["evidence_refs"]
    if not isinstance(evidence_refs, list):
        raise ValueError("evidence_refs 必须是列表")

    for evidence_ref in evidence_refs:
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("evidence_refs 中每项必须是非空字符串")

    # 7. 所有检查通过后，返回原字典
    return data
