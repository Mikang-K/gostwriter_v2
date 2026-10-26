import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .errors import AppError
from .generation import Generations
from .models import GenerateRequest, Model, ProjectInput, ProjectPatch, RevisionInput
from .store import Store


class Login(Model):
    password: str = Field(min_length=1, max_length=1024)


def failure(code, status, request_id):
    return JSONResponse({"error": {"code": code, "message": code, "requestId": request_id}}, status)


class RequestBoundary:
    def __init__(self, app, origin):
        self.app, self.origin = app, origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope["headers"])
        async def respond(code, status):
            await failure(code, status, request_id)(scope, receive, send)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            if headers.get(b"origin", b"").decode() != self.origin:
                return await respond("INVALID_ORIGIN", 403)
            size, parts = 0, []
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                part = message.get("body", b"")
                size += len(part)
                if size > 65536:
                    return await respond("BODY_TOO_LARGE", 413)
                parts.append(part)
                if not message.get("more_body", False):
                    break
            sent = False
            original_receive = receive
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": b"".join(parts), "more_body": False}
                return await original_receive()
            receive = replay
        original_send = send
        async def tagged(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [(b"x-request-id", request_id.encode())]
            await original_send(message)
        await self.app(scope, receive, tagged)


def create_app(settings=None, store=None, provider_factory=None):
    settings = settings or Settings()
    database = store or (Store(settings.database_url) if settings.database_url else None)

    @asynccontextmanager
    async def lifespan(app):
        if database and store is None:
            await database.open()
            await database.migrate()
        yield
        if database and store is None:
            await database.close()

    app = FastAPI(title="Ghostwriter v2 API", lifespan=lifespan)
    app.add_middleware(RequestBoundary, origin=settings.app_origin)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "api", "[::1]"])
    app.state.store = database
    app.state.settings = settings
    generations = Generations(database, settings, provider_factory)
    app.state.generations = generations
    login_attempts = {}

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return failure(exc.code, exc.status, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return failure("INVALID_INPUT", 400, request.state.request_id)

    @app.exception_handler(Exception)
    async def internal(request, exc):
        return failure("INTERNAL", 500, request.state.request_id)

    def db():
        if database is None:
            raise AppError("DATABASE_NOT_CONFIGURED", 503)
        return database

    async def actor(request: Request):
        token = request.cookies.get("ghostwriter_session", "")
        if not token:
            raise AppError("UNAUTHENTICATED", 401)
        owner = await db().session_get(hashlib.sha256(token.encode()).hexdigest())
        if not owner:
            raise AppError("UNAUTHENTICATED", 401)
        return owner

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "fastapi", "configured": settings.configured()}

    @app.post("/api/auth/login")
    async def login(data: Login, request: Request):
        if len(settings.app_password) < 16:
            raise AppError("PASSWORD_NOT_CONFIGURED", 503)
        ip = request.client.host if request.client else "local"
        now = time.monotonic()
        failures = [t for t in login_attempts.get(ip, []) if now-t < 60]
        if len(failures) >= 5:
            raise AppError("RATE_LIMITED", 429)
        if not hmac.compare_digest(hashlib.sha256(data.password.encode()).digest(),
                                   hashlib.sha256(settings.app_password.encode()).digest()):
            login_attempts[ip] = failures + [now]
            raise AppError("INVALID_PASSWORD", 401)
        login_attempts.pop(ip, None)
        token = secrets.token_urlsafe(32)
        await db().session_create(hashlib.sha256(token.encode()).hexdigest(), settings.owner_id,
                                  datetime.now(timezone.utc) + timedelta(hours=8))
        response = JSONResponse({"authenticated": True})
        response.set_cookie("ghostwriter_session", token, httponly=True, samesite="strict", max_age=28800,
                            secure=settings.app_origin.startswith("https:"), path="/")
        return response

    @app.get("/api/auth/session")
    async def session(owner=Depends(actor)):
        return {"authenticated": True}

    @app.post("/api/auth/logout")
    async def logout(request: Request, owner=Depends(actor)):
        await db().session_delete(hashlib.sha256(request.cookies["ghostwriter_session"].encode()).hexdigest())
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("ghostwriter_session", path="/")
        return response

    @app.get("/api/projects")
    async def projects(archived: bool = False, owner=Depends(actor)):
        return await db().list_projects(owner, archived)

    @app.post("/api/projects", status_code=201)
    async def create_project(data: ProjectInput, owner=Depends(actor)):
        return await db().create_project(owner, data)

    @app.get("/api/projects/{project_id}")
    async def project(project_id: UUID, owner=Depends(actor)):
        return await db().snapshot(owner, str(project_id))

    @app.patch("/api/projects/{project_id}")
    async def update(project_id: UUID, data: ProjectPatch, owner=Depends(actor)):
        await db().recover(owner, str(project_id))
        return await db().update_project(owner, str(project_id), data)

    @app.get("/api/projects/{project_id}/episodes/{episode_id}")
    async def episode(project_id: UUID, episode_id: UUID, owner=Depends(actor)):
        return await db().episode(owner, str(project_id), str(episode_id))

    @app.post("/api/projects/{project_id}/undo")
    async def undo(project_id: UUID, data: RevisionInput, owner=Depends(actor)):
        await db().recover(owner, str(project_id))
        return await db().undo(owner, str(project_id), data.expected_revision)

    @app.post("/api/projects/{project_id}/finish")
    async def finish(project_id: UUID, data: RevisionInput, owner=Depends(actor)):
        await db().recover(owner, str(project_id))
        return await db().finish_project(owner, str(project_id), data.expected_revision)

    @app.post("/api/generations")
    async def generate(data: GenerateRequest, request: Request, owner=Depends(actor)):
        reserved = await generations.reserve(owner, request.state.request_id, data)
        if reserved["kind"] == "existing":
            return JSONResponse({**reserved, "replayed": True,
                                 "statusUrl": "/api/generations/" + reserved["generationId"]}, 202)
        async def body():
            queue = asyncio.Queue(maxsize=32)
            async def produce():
                try:
                    async for event in generations.run(owner, request.state.request_id, data, reserved):
                        await queue.put(event)
                finally:
                    if not asyncio.current_task().cancelling():
                        await queue.put(None)
            task = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                    except TimeoutError:
                        yield b": ping\n\n"
                        continue
                    if event is None:
                        break
                    yield ("event: " + event["type"] + "\ndata: " +
                           json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/api/generations/{generation_id}")
    async def generation(generation_id: UUID, owner=Depends(actor)):
        saved = await db().generation(owner, str(generation_id))
        await db().recover(owner, saved["projectId"])
        return await db().generation(owner, str(generation_id))

    @app.post("/api/generations/{generation_id}/cancel")
    async def cancel(generation_id: UUID, owner=Depends(actor)):
        return await generations.cancel(owner, str(generation_id))

    @app.post("/api/generations/{generation_id}/finalize")
    async def finalize(generation_id: UUID, data: RevisionInput, owner=Depends(actor)):
        return await generations.finalize_saved(owner, str(generation_id), data.expected_revision)

    @app.get("/api/metrics")
    async def metrics(days: int = 7, owner=Depends(actor)):
        if not 1 <= days <= 30:
            raise AppError("INVALID_INPUT")
        return await db().metrics(owner, days)

    return app


app = create_app()
