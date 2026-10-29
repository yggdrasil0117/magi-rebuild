import hmac
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from magi.harness.config import load_config
from magi.personal.locking import ApplicationLock
from magi.personal.models import PersonalError
from magi.web.schemas import Confirmation, TurnInput
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

    @app.get("/health")
    def health():
        with service.store.connect() as db:
            db.execute("SELECT context_epoch FROM app_state").fetchone()
        return {"status": "ready", "single_worker": True}

    guarded = [Depends(identity)]

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

    return app
