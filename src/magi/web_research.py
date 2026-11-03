"""官方MCP SDK连接Exa，冻结本轮只读搜索和网页内容。"""

import asyncio
import ipaddress
import json
import re
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlsplit

import httpx
from jsonschema import validate
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field

ENDPOINT = "https://mcp.exa.ai/mcp"
ALLOWED = {"web_search_exa", "web_fetch_exa"}


def public_url(value):
    """只接受无凭据的公开HTTP网址；远端抓取，不在本机解析或访问目标。"""
    if (
        not isinstance(value, str)
        or len(value) > 2000
        or any(c.isspace() for c in value)
    ):
        raise ValueError("网址格式不合法")
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    if url.scheme not in {"https", "http"} or not host or url.username or url.password:
        raise ValueError("只支持无凭据的公开HTTP网址")
    if (
        url.port not in {None, 80, 443}
        or "." not in host
        or host.endswith((".local", ".localhost", ".internal"))
    ):
        raise ValueError("拒绝本机或内部网址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("拒绝非公开IP")
    return value


async def mcp_call(name, arguments):
    """初始化、发现、按服务端schema校验并调用；固定两个工具，40秒总期限。"""
    if name not in ALLOWED:
        raise ValueError("不允许的远端工具")
    async with asyncio.timeout(40):
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            async with streamable_http_client(ENDPOINT, http_client=client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    tool = next((t for t in catalog.tools if t.name == name), None)
                    if tool is None:
                        raise ValueError("远端工具已变化")
                    validate(arguments, tool.inputSchema)
                    return await session.call_tool(name, arguments)


def error_code(exc):
    """SDK异常可能嵌套任务组；仅返回分类，不传播响应、认证或异常正文。"""
    if isinstance(exc, BaseExceptionGroup):
        return error_code(exc.exceptions[0])
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "TIMEOUT"
    if isinstance(exc, httpx.HTTPStatusError):
        return {401: "AUTH_REQUIRED", 403: "ACCESS_DENIED", 429: "RATE_LIMITED"}.get(
            exc.response.status_code, "REMOTE_FAILURE"
        )
    if isinstance(exc, ValueError):
        return "INVALID_INPUT_OR_TOOL"
    return "CONNECTION_OR_PROTOCOL_FAILURE"


def normalize(result, operation, target):
    """冻结有界来源；搜索摘录与读取正文分开，格式变化不冒充无结果。"""
    if result.isError:
        return dict(status="FAILED", error_code="REMOTE_TOOL_ERROR", sources=[])
    text = "\n".join(c.text for c in result.content if c.type == "text")
    if not text.strip():
        return dict(status="NO_RESULTS", sources=[])
    sources = []
    if operation == "fetch":
        # 服务报错有时以普通文字返回；没有页面头不能宣称读到了正文。
        header = re.match(r"# ([^\n]+)\nURL: (\S+)", text)
        if not header:
            return dict(status="UNPARSED", error_code="UNRECOGNIZED_PAGE", sources=[])
        try:
            returned_url = public_url(header[2])
        except ValueError:
            return dict(status="UNPARSED", error_code="INVALID_SOURCE_URL", sources=[])
        sources.append(
            dict(
                title=header[1][:300],
                url=returned_url,
                excerpt=text[:4000],
                truncated=len(text) > 4000,
            )
        )
    else:
        for block in re.split(r"(?m)(?=^Title: )", text):
            match = re.match(r"Title: ([^\n]+)\nURL: (\S+)", block)
            if not match:
                continue
            try:
                url = public_url(match[2])
            except ValueError:
                continue
            sources.append(
                dict(
                    title=match[1][:300],
                    url=url,
                    excerpt=block[:2000],
                    truncated=len(block) > 2000,
                )
            )
            if len(sources) == 3:
                break
    if not sources:
        return dict(status="UNPARSED", error_code="UNRECOGNIZED_RESULT", sources=[])
    return dict(status="OK", sources=sources)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=500)


class FetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=2000)


class WebResearch:
    def __init__(self, persist, transport=mcp_call):
        """每轮独立实例；persist由业务层注入，写该轮来源，不写计划和记忆。"""
        self.persist, self.transport = persist, transport
        self.records = []
        self.lock = Lock()

    def call(self, operation, target, objective=""):
        """先保存尝试，再调用；最多3次，失败不自动重试，结果先存再返回模型。"""
        with self.lock:
            if len(self.records) >= 3:
                return json.dumps(
                    dict(status="FAILED", error_code="CALL_BUDGET_EXCEEDED")
                )
            record = dict(
                id=f"web:{len(self.records) + 1}",
                operation=operation,
                target=target,
                objective=objective if operation == "search" else None,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                status="RUNNING",
                sources=[],
            )
            self.records.append(record)
            self.persist(self.records)
            try:
                if operation == "search":
                    SearchInput(query=target, objective=objective)
                    name, args = (
                        "web_search_exa",
                        dict(query=target, objective=objective, numResults=3),
                    )
                elif operation == "fetch":
                    name, args = (
                        "web_fetch_exa",
                        dict(urls=[public_url(target)], maxCharacters=4000),
                    )
                else:
                    raise ValueError("未知操作")
                result = asyncio.run(self.transport(name, args))
                record.update(normalize(result, operation, target))
            except Exception as exc:
                record.update(status="FAILED", error_code=error_code(exc))
            self.persist(self.records)
            return json.dumps(record, ensure_ascii=False)

    def tools(self):
        """联网开启后提供两个固定工具，远端工具描述不作为模型指令。"""

        def magi_web_search(query: str, objective: str) -> str:
            """搜索公开网络最多三条摘录，objective说明要核对什么；不证明已读全文。"""
            return self.call("search", query, objective)

        def magi_web_fetch(url: str) -> str:
            """通过Exa读取一个公开网页的有界内容；失败不等于页面或事实不存在。"""
            return self.call("fetch", url)

        return [
            StructuredTool.from_function(magi_web_search, args_schema=SearchInput),
            StructuredTool.from_function(magi_web_fetch, args_schema=FetchInput),
        ]
