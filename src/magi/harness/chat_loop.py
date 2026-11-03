"""网页聊天的执行顺序由 MAGI 控制，消息和工具协议沿用现有适配层。"""

import json

from httpx import ConnectError, ConnectTimeout
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langsmith import tracing_context
from pydantic import BaseModel

from magi.harness.runtime import validate_model_message


class ChatCapacityError(ValueError):
    """携带可安全显示给用户的容量失败原因。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ChatLoop:
    def __init__(self, session, settings, model, prompt, tools, summary):
        """宿主提供存储、模型和本轮可用工具，模型不能自行注册工具。"""
        self.session = session
        self.settings = settings
        self.model = model
        self.system = SystemMessage(content=prompt)
        self.tools = {tool.name: tool for tool in tools}
        self.specs = [convert_to_openai_tool(tool) for tool in tools]
        self.summary = summary

    def model_input(self, history):
        """按完整轮次压缩输入，原始消息仍用于持久化；容量估算使用字节。"""
        messages = [self.system, *history]
        budget = self.settings.num_ctx
        if self.input_cost(messages) <= budget * 0.85:
            return messages
        starts = [
            i for i, message in enumerate(history) if isinstance(message, HumanMessage)
        ]
        if starts and self.input_cost([self.system, *history[starts[-1] :]]) > budget:
            raise ChatCapacityError(
                "CURRENT_TURN_TOO_LARGE",
                "当前消息或工具结果已超出容量，请缩短问题或减少工具范围。",
            )
        # 短对话仍放得下时保留全文，避免工具定义的固定成本触发无益摘要。
        if len(starts) <= 3 and self.input_cost(messages) <= budget:
            return messages
        if self.summary and len(starts) > 1:
            keep = min(3, len(starts) - 1)
            # 近期窗口给摘要预留空间；当前轮及其工具交互始终完整保留。
            while (
                keep > 1
                and self.input_cost([self.system, *history[starts[-keep] :]])
                > budget - 512
            ):
                keep -= 1
            messages = [
                self.system,
                *self.summary.compact(history, keep_turns=keep, max_batches=3),
            ]
        if self.input_cost(messages) > budget:
            raise ChatCapacityError(
                "CHAT_HISTORY_TOO_LARGE", "旧对话压缩后仍超出容量，请新建会话继续。"
            )
        return messages

    def input_cost(self, messages):
        """摘要触发与硬限制共用估算，计入工具规格和输出预留。"""
        payload = {
            "messages": [message.model_dump(mode="json") for message in messages],
            "tools": self.specs,
        }
        size = len(json.dumps(payload, ensure_ascii=False).encode())
        return size + self.settings.num_predict + 512 + 128 * len(messages)

    def validate_batch(self, calls):
        """整批通过白名单、关联 ID 和参数检查后，才允许任何工具运行。"""
        seen = set()
        for call in calls:
            if call["name"] not in self.tools:
                raise PermissionError("模型请求了本轮未开放的工具")
            if not call["id"] or call["id"] in seen:
                raise ValueError("同批工具请求必须有唯一调用 ID")
            seen.add(call["id"])
            schema = self.tools[call["name"]].get_input_schema()
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                schema.model_validate(call["args"])

    def ask(self, text):
        """返回回答和整轮消息，调用方在业务事务里统一保存。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("消息必须是非空字符串")
        events = self.session.events
        events.start()
        stage = "load_history"
        try:
            history = self.session.messages()
            pending = [HumanMessage(content=text)]
            remaining_tools = self.settings.max_tool_calls
            retried = False
            with tracing_context(enabled=False):
                model = self.model.bind_tools(list(self.tools.values()))
                for attempt in range(1, self.settings.max_model_calls + 1):
                    stage = "context"
                    messages = self.model_input([*history, *pending])
                    stage = "model"
                    events.emit("model_request", attempt=attempt)
                    try:
                        reply = model.invoke(messages)
                    except Exception as exc:
                        # 仅在连接未建立时重试；读取超时可能已在上游执行。
                        connection_error = isinstance(
                            exc if exc.__cause__ is None else exc.__cause__,
                            (ConnectError, ConnectTimeout),
                        )
                        if (
                            not connection_error
                            or retried
                            or attempt >= self.settings.max_model_calls
                        ):
                            raise
                        retried = True
                        events.emit("model_retry", attempt=attempt, reason="connect")
                        continue
                    stage = "model_response"
                    if not isinstance(reply, AIMessage):
                        raise ValueError("模型没有返回助手消息")
                    validate_model_message(reply)
                    calls = reply.tool_calls
                    events.emit("model_response", tool_call_count=len(calls))
                    pending.append(reply)
                    if not calls:
                        if not reply.text.strip():
                            raise ValueError("本轮没有生成完整文本回答")
                        events.emit("run_completed", scope="execution_only")
                        return reply.text, pending
                    stage = "tool_validation"
                    if (
                        len(calls) > remaining_tools
                        or attempt >= self.settings.max_model_calls
                    ):
                        raise RuntimeError("剩余预算不足以执行工具并继续回答")
                    self.validate_batch(calls)
                    remaining_tools -= len(calls)
                    stage = "tool_execution"
                    for call in calls:
                        fields = {"name": call["name"], "call_id": call["id"]}
                        events.emit("tool_started", **fields)
                        try:
                            result = self.tools[call["name"]].invoke(call)
                            if (
                                not isinstance(result, ToolMessage)
                                or result.tool_call_id != call["id"]
                                or result.status == "error"
                            ):
                                raise ValueError("工具结果错误或未关联请求")
                        except Exception as exc:
                            events.emit(
                                "tool_failed", error_type=type(exc).__name__, **fields
                            )
                            raise
                        pending.append(result)
                        events.emit("tool_succeeded", **fields)
        except (Exception, KeyboardInterrupt) as exc:
            events.emit("run_failed", stage=stage, error_type=type(exc).__name__)
            raise
