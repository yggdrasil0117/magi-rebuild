"""Provider-facing schema and diagnostics; stored business models stay compatible."""

from copy import deepcopy

import httpx
from pydantic import ValidationError


def generation_schema(model, snapshot):
    """Constrain generation without narrowing already persisted plan revisions."""
    original = model.model_json_schema()
    definitions = original.get("$defs", {})

    def inline(node):
        if isinstance(node, list):
            return [inline(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(deepcopy(definitions[node["$ref"].rsplit("/", 1)[-1]]))
        result = {
            k: inline(v)
            for k, v in node.items()
            if k not in {"$defs", "default"}
            and not (k == "title" and isinstance(v, str))
        }
        if result.get("type") == "string":
            result["maxLength"] = min(result.get("maxLength", 160), 160)
        if result.get("type") == "array":
            limit = (
                6 if result.get("items", {}).get("properties", {}).get("kind") else 3
            )
            result["maxItems"] = min(result.get("maxItems", limit), limit)
        properties = result.get("properties", {})
        if "evidence_ref" in properties:
            ids = snapshot.get("evidence_ids", [])
            if ids:
                properties["evidence_ref"]["enum"] = ids
        if "evidence_refs" in properties:
            ids = snapshot.get("evidence_ids", [])
            properties["evidence_refs"]["items"] = (
                {"type": "string", "enum": ids} if ids else {"type": "string"}
            )
            if not ids:
                properties["evidence_refs"]["maxItems"] = 0
        if "kind" in properties:
            properties["kind"] = {
                "type": "string",
                "enum": ["work", "rest", "review", "buffer"],
            }
        if "minutes" in properties:
            properties["minutes"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": snapshot.get("time_budget_minutes") or 10080,
            }
            result["required"] = list(
                dict.fromkeys([*result.get("required", []), "minutes"])
            )
        if "source_excerpts" in properties or "kind" in properties:
            result["required"] = list(properties)
        return result

    return inline(original)


def model_snapshot(snapshot):
    """Execution IDs are trace metadata, not evidence the model should cite."""
    result = {
        key: snapshot.get(key)
        for key in (
            "question",
            "time_budget_minutes",
            "memories",
            "history",
            "old_plan",
            "evidence_ids",
        )
    }
    if result["old_plan"]:
        result["old_plan"] = {
            key: result["old_plan"][key]
            for key in ("title", "body", "version")
            if key in result["old_plan"]
        }
    return result


def failure_details(exc):
    # Optional cloud SDK: local-only installations need not install it.
    try:
        import openai
    except ImportError:
        openai = None
    if openai is not None:
        if isinstance(exc, openai.APITimeoutError):
            return "TIMEOUT", []
        if isinstance(exc, openai.APIConnectionError):
            return "MODEL_UNAVAILABLE", []
        if isinstance(exc, openai.APIStatusError):
            return {
                401: "AUTHENTICATION_FAILED",
                403: "MODEL_ACCESS_DENIED",
                429: "RATE_LIMITED",
            }.get(exc.status_code, "MODEL_FAILURE"), []
    if isinstance(exc, ValidationError):
        errors = exc.errors(
            include_url=False, include_context=False, include_input=False
        )
        code = (
            "INVALID_JSON"
            if any(e["type"] == "json_invalid" for e in errors)
            else "INVALID_FIELDS"
        )
        return code, [
            {"field": ".".join(map(str, e["loc"])), "type": e["type"]}
            for e in errors[:6]
        ]
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "TIMEOUT", []
    if isinstance(exc, httpx.RequestError):
        return "MODEL_UNAVAILABLE", []
    message = str(exc)
    if "生成长度上限" in message:
        return "TRUNCATED", []
    return {
        "unknown evidence reference": "UNKNOWN_EVIDENCE",
        "candidate exceeds explicit time budget": "TIME_BUDGET_EXCEEDED",
        "candidate lacks step minutes": "MISSING_STEP_MINUTES",
        "candidate lacks step kind": "MISSING_STEP_KIND",
        "unmatched source excerpt": "UNMATCHED_SOURCE_EXCERPT",
        "council tools are forbidden": "FORBIDDEN_TOOL",
    }.get(message, "MODEL_FAILURE"), []


FAILURE_MESSAGES = {
    "AUTHENTICATION_FAILED": "模型平台鉴权失败，请检查密钥与接入地址",
    "MODEL_ACCESS_DENIED": "当前账号无权调用此模型",
    "RATE_LIMITED": "模型平台限制了请求或额度，请检查平台状态后再试",
    "INVALID_JSON": "模型返回的 JSON 不完整或格式无效",
    "INVALID_FIELDS": "模型输出字段未通过校验",
    "UNKNOWN_EVIDENCE": "模型引用了快照中不存在的证据",
    "TRUNCATED": "模型输出达到长度上限，未作为完整结果接受",
    "TIMEOUT": "模型请求或整体审议超时",
    "MODEL_UNAVAILABLE": "无法连接模型服务",
    "TIME_BUDGET_EXCEEDED": "候选计划的总时长超过本次预算",
    "MISSING_STEP_MINUTES": "候选步骤未填写预计用时，无法核对预算",
    "MISSING_STEP_KIND": "候选步骤未区分任务、休息、复盘或缓冲",
    "UNMATCHED_SOURCE_EXCERPT": "候选的用户陈述摘录无法匹配本轮原文或有效记忆",
    "FORBIDDEN_TOOL": "模型请求了审议阶段不允许的工具",
    "MODEL_FAILURE": "模型调用未完成",
}


def failure_message(code):
    return FAILURE_MESSAGES.get(code, "本阶段未完成")
