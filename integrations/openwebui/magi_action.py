"""
title: 确认保存 MAGI 候选
author: MAGI local project
version: 0.1.0
requirements: httpx==0.28.1
"""

import json

import httpx
from pydantic import BaseModel


class Action:
    class Valves(BaseModel):
        MAGI_URL: str = "http://127.0.0.1:8765"
        MAGI_TOKEN: str = ""
        OWNER_USER_ID: str = ""

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__=None):
        if (
            not self.valves.OWNER_USER_ID
            or (__user__ or {}).get("id") != self.valves.OWNER_USER_ID
        ):
            raise ValueError("MAGI 单用户身份不匹配")
        chat, reply = body.get("chat_id"), body.get("id")
        async with httpx.AsyncClient(
            base_url=self.valves.MAGI_URL,
            headers={"Authorization": "Bearer " + self.valves.MAGI_TOKEN},
            timeout=20,
            trust_env=False,
        ) as client:
            history = await client.get("/v1/chats/" + str(chat) + "/turns")
            history.raise_for_status()
            matches = [
                t for t in history.json() if t["reply"] == reply and t["proposal_id"]
            ]
            if len(matches) != 1:
                raise ValueError("此消息没有可确认候选")
            turn = matches[0]
            proposal = await client.get(
                "/v1/proposals/" + turn["proposal_id"],
                params={"chat_id": chat, "reply_message_id": reply},
            )
            proposal.raise_for_status()
            p = proposal.json()
            response = await client.post(
                "/v1/proposals/" + p["id"] + "/confirm",
                json={
                    "chat_id": chat,
                    "reply_message_id": reply,
                    "payload_hash": p["payload_hash"],
                },
            )
            if response.status_code != 200:
                error = response.json()
                return {
                    "messages": [
                        {
                            "id": reply,
                            "content": turn["text"]
                            + "\n\n本次未取得保存回执："
                            + error.get("code", "REQUEST_FAILED")
                            + "。可查询计划后再点击重试。",
                            "error": None,
                        }
                    ]
                }
            response.raise_for_status()
            # Replace using the canonical persisted body; repeated clicks do not
            # append duplicate receipts or trust client/model-controlled text.
            content = (
                turn["text"]
                .replace("待确认候选（尚未保存）", "已确认候选")
                .replace("请点击确认保存；当前尚未保存。", "保存状态见下方真实回执。")
                + "\n\n真实保存回执：\n"
                + json.dumps(response.json(), ensure_ascii=False, indent=2)
            )
            return {"messages": [{"id": reply, "content": content, "error": None}]}
