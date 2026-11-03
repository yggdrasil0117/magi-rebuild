"""网页模型历史按背景隔离，完整消息批次由业务事务提交。"""

import json
import sqlite3
from contextlib import closing

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
    message_to_dict,
    messages_from_dict,
)


def complete_exchange(messages):
    """旧历史只接受完整问答，工具请求必须恰好收到对应的一组结果。"""
    if len(messages) < 2 or not isinstance(messages[-1], AIMessage):
        return False
    final = messages[-1]
    if (
        not final.text.strip()
        or final.tool_calls
        or final.invalid_tool_calls
        or final.response_metadata.get("finish_reason") in {"length", "max_tokens"}
        or final.response_metadata.get("done_reason") == "length"
    ):
        return False
    expected = set()
    for message in messages[1:-1]:
        if (
            isinstance(message, AIMessage)
            and not expected
            and not message.invalid_tool_calls
        ):
            expected = {call["id"] for call in message.tool_calls}
            if (
                not expected
                or None in expected
                or "" in expected
                or len(expected) != len(message.tool_calls)
            ):
                return False
        elif isinstance(message, ToolMessage) and message.tool_call_id in expected:
            expected.remove(message.tool_call_id)
        else:
            return False
    return not expected


class WebChatHistory:
    def __init__(self, store, binding, settings):
        """绑定由服务端根据逻辑会话和当前背景版本解析。"""
        self.store = store
        self.binding = binding
        self.settings = settings

    def _legacy_messages(self):
        """让旧适配器重建增量历史，只读取状态，不执行模型或工具。"""
        if not self.settings.db_path.exists():
            return []
        # 旧库存在时也只为确实有旧记录的会话构造兼容图。
        uri = self.settings.db_path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'"
            ).fetchone()
            if (
                not table
                or not db.execute(
                    "SELECT 1 FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' LIMIT 1",
                    (self.binding.thread_id,),
                ).fetchone()
            ):
                return []
        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )

        from magi.harness.runtime import open_session

        with open_session(
            self.settings,
            self.binding.thread_id,
            model=FakeMessagesListChatModel(responses=[]),
        ) as legacy:
            return legacy.messages()

    def load(self):
        """首次读取时导入一次。后续只读业务库，并保留完整的消息顺序。"""
        thread = self.binding.thread_id
        with self.store.connect() as db:
            imported = db.execute(
                "SELECT 1 FROM web_chat_imports WHERE thread_id=?", (thread,)
            ).fetchone()
        if not imported:
            legacy = self._legacy_messages()
            # 每条用户消息开启新轮次，未结束的尾轮不能进入模型上下文。
            exchanges = []
            exchange = []
            for message in legacy:
                if isinstance(message, HumanMessage):
                    if complete_exchange(exchange):
                        exchanges.append(exchange)
                    exchange = [message]
                elif exchange:
                    exchange.append(message)
            if complete_exchange(exchange):
                exchanges.append(exchange)
            with self.store.transaction() as db:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO web_chat_imports VALUES (?)", (thread,)
                ).rowcount
                if inserted:
                    pending_ids = {
                        row["id"] + "-user"
                        for row in db.execute(
                            "SELECT id FROM web_turns WHERE mode='council' AND synced=0 AND status='COMPLETED'"
                        )
                    }
                    for exchange in exchanges:
                        if exchange[0].id not in pending_ids:
                            self.append(db, None, exchange)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT messages_json FROM web_chat_history WHERE thread_id=? ORDER BY sequence",
                (thread,),
            ).fetchall()
        return [
            message
            for row in rows
            for message in messages_from_dict(json.loads(row["messages_json"]))
        ]

    def append(self, db, turn_id, messages):
        """借用调用方事务，消息入库与网页轮次状态一起提交或回滚。"""
        db.execute(
            "INSERT INTO web_chat_history(thread_id,turn_id,messages_json) VALUES (?,?,?)",
            (
                self.binding.thread_id,
                turn_id,
                json.dumps([message_to_dict(m) for m in messages], ensure_ascii=False),
            ),
        )
