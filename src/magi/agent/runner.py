from collections.abc import Callable
from copy import deepcopy


def run_agent(
        *,
        chat: Callable,
        execute_tool: Callable,
        model: str,
        messages: list,
        tools: list,
        max_model_requests: int = 5,
        max_tool_calls: int = 4,
        events: list | None = None,
        should_stop: Callable | None = None,
) -> str:
    if type(max_model_requests) is not int or max_model_requests < 1:
        raise ValueError("模型请求预算必须是正整数")

    if type(max_tool_calls) is not int or max_tool_calls < 0:
        raise ValueError("工具调用预算必须是非负整数")

    if events is None:
        events = []

    history = deepcopy(messages)
    tool_call_count = 0

    try:
        for request_number in range(1, max_model_requests + 1):
            events.append({
                "type": "model_request",
                "request_number": request_number,
            })

            response = chat(
                model=model,
                messages=history,
                tools=tools,
                stream=False,
                think=False,
            )

            history.append(response.message)
            calls = response.message.tool_calls or []

            events.append({
                "type": "model_response",
                "request_number": request_number,
                "tool_call_count": len(calls),
            })

            if not calls:
                content = response.message.content

                if not isinstance(content, str) or not content.strip():
                    raise ValueError("模型没有返回工具请求或有效回答")

                events.append({
                    "type": "run_completed",
                    "model_requests": request_number,
                    "tool_calls": tool_call_count,
                })
                return content

            if tool_call_count + len(calls) > max_tool_calls:
                raise RuntimeError("工具调用次数将超过上限")

            if request_number == max_model_requests:
                raise RuntimeError("模型请求次数已达到上限，任务尚未完成")

            for tool_call in calls:
                call = tool_call.function
                tool_call_count += 1
                call_number = tool_call_count

                events.append({
                    "type": "tool_started",
                    "call_number": call_number,
                    "name": call.name,
                })

                try:
                    result = execute_tool(call.name, call.arguments)
                    result_text = str(result)
                except Exception as exc:
                    events.append({
                        "type": "tool_failed",
                        "call_number": call_number,
                        "name": call.name,
                        "error_type": type(exc).__name__,
                    })
                    raise

                events.append({
                    "type": "tool_succeeded",
                    "call_number": call_number,
                    "name": call.name,
                    "result_chars": len(result_text),
                })

                history.append({
                    "role": "tool",
                    "tool_name": call.name,
                    "content": result_text,
                })

            # 在整批工具请求都得到回复后，允许上层任务切换阶段。
            if should_stop is not None and should_stop():
                events.append({
                    "type": "run_stopped",
                    "reason": "application_stop_condition",
                    "model_requests": request_number,
                    "tool_calls": tool_call_count,
                })
                return "工具阶段已达到应用停止条件"

        raise RuntimeError("任务未正常结束")

    except Exception as exc:
        events.append({
            "type": "run_failed",
            "error_type": type(exc).__name__,
        })
        raise
