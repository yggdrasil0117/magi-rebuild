"""
title: MAGI
author: MAGI local project
version: 0.1.0
requirements: httpx==0.28.1
"""

import json

import httpx
from pydantic import BaseModel


class Pipe:
    class Valves(BaseModel):
        MAGI_URL: str = "http://127.0.0.1:8765"
        MAGI_TOKEN: str = ""
        OWNER_USER_ID: str = ""

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [
            {"id": "daily", "name": "MAGI · 日常助手"},
            {"id": "council", "name": "MAGI · 三人格审议"},
        ]

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __chat_id__: str = None,
        __message_id__: str = None,
        __metadata__: dict = None,
        __task__: str = None,
    ):
        if __task__:
            return "MAGI 对话"
        if (
            not self.valves.OWNER_USER_ID
            or (__user__ or {}).get("id") != self.valves.OWNER_USER_ID
        ):
            return "MAGI：未配置或不匹配的单用户身份。"
        metadata = __metadata__ or {}
        chat = __chat_id__ or metadata.get("chat_id")
        message = metadata.get("user_message_id")
        reply = __message_id__ or metadata.get("message_id")
        if not all([chat, message, reply]):
            return "MAGI：缺少稳定 chat/user_message/reply ID；请使用保存的聊天，不支持此调用方式。"
        users = [m for m in body.get("messages", []) if m.get("role") == "user"]
        if not users or not isinstance(users[-1].get("content"), str):
            return "MAGI：首版只支持文本消息。"
        text = users[-1]["content"].strip()
        headers = {"Authorization": "Bearer " + self.valves.MAGI_TOKEN}
        async with httpx.AsyncClient(
            base_url=self.valves.MAGI_URL, headers=headers, timeout=280, trust_env=False
        ) as client:

            async def get(path, **kwargs):
                response = await client.get(path, **kwargs)
                response.raise_for_status()
                return response.json()

            try:
                # Verify the selected UI branch using server-owned chat records.
                # Title tasks returned above never touch these business APIs.
                from open_webui.models.chats import Chats

                history = await get("/v1/chats/" + chat + "/turns")
                if history and not any(t.get("message") == message for t in history):
                    messages_map = await Chats.get_messages_map_by_chat_id(chat)
                    cursor, ancestors = message, set()
                    for _ in range(1000):
                        node = messages_map.get(cursor, {})
                        cursor = node.get("parentId")
                        if not cursor or cursor in ancestors:
                            break
                        ancestors.add(cursor)
                    if history[-1]["reply"] not in ancestors:
                        return "MAGI：不支持旧消息分叉，请新建聊天或继续最新消息。"
                if text == "/计划":
                    return json.dumps(
                        await get("/v1/plans"), ensure_ascii=False, indent=2
                    )
                if text.startswith("/计划 "):
                    parts = text.split()
                    params = {"version": int(parts[2])} if len(parts) == 3 else {}
                    return json.dumps(
                        await get("/v1/plans/" + parts[1], params=params),
                        ensure_ascii=False,
                        indent=2,
                    )
                if text.startswith("/保存 "):
                    proposal_id = text.split()[1]
                    history = await get("/v1/chats/" + chat + "/turns")
                    matches = [t for t in history if t["proposal_id"] == proposal_id]
                    if len(matches) != 1:
                        return "MAGI：候选不属于当前聊天。"
                    p = await get(
                        "/v1/proposals/" + proposal_id,
                        params={
                            "chat_id": chat,
                            "reply_message_id": matches[0]["reply"],
                        },
                    )
                    response = await client.post(
                        "/v1/proposals/" + proposal_id + "/confirm",
                        json={
                            "chat_id": chat,
                            "reply_message_id": p["reply"],
                            "payload_hash": p["payload_hash"],
                        },
                    )
                else:
                    payload = {
                        "chat_id": chat,
                        "message_id": message,
                        "reply_message_id": reply,
                        "user_text": text,
                        "parent_reply_message_id": history[-1]["reply"]
                        if history
                        else None,
                    }
                    mode = (
                        "deliberations"
                        if body.get("model", "").endswith("council")
                        else "chat"
                    )
                    if text.startswith("/预算 "):
                        _, minutes, text = text.split(maxsplit=2)
                        payload.update(time_budget_minutes=int(minutes), user_text=text)
                        mode = "deliberations"
                    if text.startswith("/修订 "):
                        _, target, version, feedback = text.split(maxsplit=3)
                        payload.update(
                            target_plan_id=target,
                            expected_version=int(version),
                            user_text=feedback,
                        )
                        mode = "deliberations"
                    response = await client.post("/v1/" + mode, json=payload)
                response.raise_for_status()
                result = response.json()
                return result.get("text") or json.dumps(
                    result, ensure_ascii=False, indent=2
                )
            except (httpx.HTTPError, ValueError, IndexError):
                return "MAGI：请求失败或命令格式无效。请查询历史/计划后再重试；不要编辑旧消息或重新生成，请新发消息。"
