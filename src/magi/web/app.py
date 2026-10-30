import hmac
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from difflib import unified_diff
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from magi.harness.config import load_config
from magi.personal.export import plan_markdown
from magi.personal.locking import ApplicationLock
from magi.personal.models import PersonalError
from magi.web.schemas import (
    Confirmation,
    MemoryCreate,
    MemoryUpdate,
    MemoryVersion,
    TurnInput,
)
from magi.web.service import WebError, WebService


def create_app(settings=None, *, token=None, owner=None, service=None):
    settings = settings or load_config(
        Path(os.getenv("MAGI_CONFIG", "magi.qianwen.toml"))
    )
    token = token or os.getenv("MAGI_API_TOKEN", "")
    owner = owner or os.getenv("MAGI_OWNER", "local")
    if len(token) < 24:
        raise ValueError("MAGI_API_TOKEN 至少 24 字符；请由环境变量提供")
    service = service or WebService(settings, owner)

    @asynccontextmanager
    async def lifespan(app):
        with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
            service.recover()
            yield

    app = FastAPI(title="MAGI", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_INPUT",
                "message": "请求字段不符合要求，请检查输入内容、会话和版本号。",
                "run_id": None,
                "fields": [
                    {"field": ".".join(map(str, e["loc"])), "type": e["type"]}
                    for e in exc.errors()[:8]
                ],
            },
        )

    def identity(authorization: str = Header(default="")):
        if not hmac.compare_digest(
            authorization.encode(), ("Bearer " + token).encode()
        ):
            raise WebError("UNAUTHORIZED", "需要有效服务凭据", 401)

    @app.exception_handler(WebError)
    async def web_error(request: Request, exc: WebError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": str(exc),
                "run_id": getattr(exc, "run_id", None),
            },
        )

    @app.exception_handler(PersonalError)
    async def personal_error(request: Request, exc: PersonalError):
        status = (
            404
            if exc.code == "NOT_FOUND"
            else 422
            if exc.code == "INVALID_INPUT"
            else 409
        )
        return JSONResponse(
            status_code=status,
            content={
                "code": exc.code,
                "message": str(exc),
                "run_id": getattr(exc, "run_id", None),
            },
        )

    @app.get("/")
    def page():
        return FileResponse(Path(__file__).with_name("index.html"))

    @app.get("/app.js")
    def javascript():
        return FileResponse(
            Path(__file__).with_name("app.js"), media_type="text/javascript"
        )

    @app.get("/memory.js")
    def memory_javascript():
        return FileResponse(
            Path(__file__).with_name("memory.js"), media_type="text/javascript"
        )

    @app.get("/health")
    def health():
        with service.store.connect() as db:
            db.execute("SELECT context_epoch FROM app_state").fetchone()
        return {"status": "ready", "single_worker": True}

    guarded = [Depends(identity)]

    @app.get("/v1/memories", dependencies=guarded)
    def memories():
        """只读当前有效的全局记忆；不含已删除正文与其他会话专属内容。"""
        return {
            "items": [asdict(m) for m in service.memories.list_active("global")],
            "context_epoch": service.memories.current_epoch(),
        }

    @app.post("/v1/memories", dependencies=guarded)
    def create_memory(body: MemoryCreate):
        return service.change_memory("add", body)

    @app.post("/v1/memories/{memory_id}/update", dependencies=guarded)
    def update_memory(memory_id: str, body: MemoryUpdate):
        return service.change_memory("update", body, memory_id)

    @app.post("/v1/memories/{memory_id}/delete", dependencies=guarded)
    def delete_memory(memory_id: str, body: MemoryVersion):
        return service.change_memory("delete", body, memory_id)

    @app.post("/v1/chat", dependencies=guarded)
    def chat(body: TurnInput):
        return service.turn(body, "chat")

    @app.post("/v1/deliberations", dependencies=guarded)
    def deliberate(body: TurnInput):
        return service.turn(body, "council")

    @app.get("/v1/chats/{chat_id}/turns", dependencies=guarded)
    def history(chat_id: str):
        return service.history(chat_id)

    @app.get("/v1/turns/{turn_id}", dependencies=guarded)
    def turn(turn_id: str):
        return service.get_turn(turn_id)

    @app.get("/v1/proposals/{proposal_id}", dependencies=guarded)
    def proposal(proposal_id: str, chat_id: str, reply_message_id: str):
        return service.proposal(proposal_id, chat_id, reply_message_id)

    @app.post("/v1/proposals/{proposal_id}/confirm", dependencies=guarded)
    def confirm(proposal_id: str, body: Confirmation):
        return service.confirm(proposal_id, body)

    @app.get("/v1/plans", dependencies=guarded)
    def plans():
        return [asdict(p) for p in service.plans.list_current()]

    @app.get("/v1/plans/{plan_id}", dependencies=guarded)
    def plan(plan_id: str, version: int | None = None):
        return asdict(service.plans.get(plan_id, version))

    @app.get("/v1/plans/{plan_id}/versions", dependencies=guarded)
    def versions(plan_id: str):
        """读取真实版本目录，供网页选择；此接口不请求模型或写入计划。"""
        return service.plans.list_versions(plan_id)

    @app.get("/v1/plans/{plan_id}/export", dependencies=guarded)
    def export_plan(plan_id: str, version: int = Query(ge=1)):
        """下载用户明确选择的已保存版本，不接收任意路径或导出未确认候选。"""
        saved = service.plans.get(plan_id, version)
        return Response(
            content=plan_markdown(saved),
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="magi-plan-{saved.id}-v{saved.version}.md"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/v1/plans/{plan_id}/compare", dependencies=guarded)
    def compare(
        plan_id: str, from_version: int = Query(ge=1), to_version: int = Query(ge=1)
    ):
        """比较两个不可变版本的标题及正文；显式版本避免把旧内容当成最新版。"""
        before = service.plans.get(plan_id, from_version)
        after = service.plans.get(plan_id, to_version)
        changes = "".join(
            unified_diff(
                (before.title + "\n" + before.body + "\n").splitlines(keepends=True),
                (after.title + "\n" + after.body + "\n").splitlines(keepends=True),
                fromfile=f"v{from_version}",
                tofile=f"v{to_version}",
            )
        )
        return {
            "plan_id": plan_id,
            "before": asdict(before),
            "after": asdict(after),
            "diff": changes,
            "identical": not changes,
        }

    return app
