"""Expose authenticated local HTTP and WebSocket access to a shared runtime."""

import asyncio
import hmac
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gemma_agents.runtime import Runtime, RuntimeBusyError
from gemma_agents.security.approvals import ApprovalBroker, RemoteApprover


class Prompt(BaseModel):
    """Strict request body containing one bounded user prompt."""

    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=16000)


class Approval(BaseModel):
    """Strict request body carrying a human approval decision."""

    model_config = ConfigDict(extra="forbid", strict=True)
    approved: bool


class TaskPrompt(Prompt):
    """Task request with up to ten explicit verification commands."""

    checks: list[str] = Field(default_factory=list, max_length=10)


def create_app(runtime: Runtime, *, scheduler: bool = False) -> FastAPI:
    """Build the local API and optionally start its task scheduler.

    Require a bearer token of at least 24 characters. Application shutdown
    releases pending approvals, waits for background work, and closes runtime.
    """
    token = runtime.settings.api_token
    if len(token) < 24:
        raise ValueError("AGENT_API_TOKEN doit contenir au moins 24 caractères.")
    broker = ApprovalBroker()
    active = set()

    @asynccontextmanager
    async def lifespan(app):
        """Own scheduler startup and orderly approval, worker, and runtime shutdown."""
        stop = asyncio.Event()

        async def worker():
            """Process due tasks off the event loop until shutdown is requested."""
            while not stop.is_set():
                await asyncio.to_thread(runtime.tick)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass

        task = asyncio.create_task(worker()) if scheduler else None
        try:
            yield
        finally:
            stop.set()
            broker.close()
            if task:
                await task
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            runtime.close()

    def valid(headers) -> bool:
        """Require a matching bearer token and reject requests with an Origin header."""
        authorization = headers.get("authorization", "")
        # Reject browser-originated requests and avoid token-prefix timing leaks.
        return not headers.get("origin") and hmac.compare_digest(
            authorization.encode(), ("Bearer " + token).encode(),
        )

    app = FastAPI(title="Gemma Agents V4", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request, call_next):
        """Reject unauthenticated HTTP requests before invoking their route."""
        if not valid(request.headers):
            return JSONResponse({"detail": "Jeton Bearer requis, sans Origin."},
                                status_code=401)
        return await call_next(request)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.state.runtime = runtime
    app.state.approvals = broker

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        """Convert runtime validation errors into HTTP 400 responses."""
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(RuntimeBusyError)
    async def busy(request, error):
        """Report runtime contention as an HTTP 409 conflict."""
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/openapi.json")
    def schema():
        """Return the API schema behind the same authentication as other routes."""
        return app.openapi()

    @app.get("/health")
    def health():
        """Report the runtime version and sandbox configuration and availability."""
        return {"version": "0.4.0", "sandbox": runtime.settings.sandbox,
                "sandbox_available": runtime.runner.available}

    @app.get("/sessions")
    def sessions():
        """List sessions belonging to the runtime workspace."""
        return runtime.sessions.list()

    @app.post("/sessions", status_code=201)
    def create_session():
        """Create a workspace session and return its identifier."""
        return {"id": runtime.sessions.create()}

    @app.get("/sessions/{session_id}/messages")
    def history(session_id: str):
        """Return validated session history in chronological order."""
        return runtime.sessions.history(session_id)

    @app.post("/sessions/{session_id}/messages")
    def chat(session_id: str, body: Prompt):
        """Run a synchronous prompt with approvals handled through the broker."""
        return asdict(runtime.run(session_id, body.prompt,
                                  RemoteApprover(broker, session_id)))

    @app.get("/approvals")
    def approvals():
        """List pending tool calls awaiting a remote decision."""
        return broker.list()

    @app.post("/approvals/{approval_id}")
    def resolve(approval_id: str, body: Approval):
        """Resolve one pending approval, returning HTTP 404 for an unknown identifier.
        """
        try:
            broker.resolve(approval_id, body.approved)
        except KeyError:
            raise HTTPException(404, "Approbation inconnue ou expirée.") from None
        return {"resolved": True}

    @app.get("/tasks")
    def tasks():
        """List persistent tasks in the runtime workspace."""
        return runtime.tasks.list()

    @app.post("/tasks", status_code=201)
    def create_task(body: TaskPrompt):
        """Persist a task and its verification commands, then return its identifier."""
        return {"id": runtime.tasks.create(body.prompt, body.checks)}

    @app.post("/tasks/{task_id}/run")
    def run_task(task_id: str):
        """Run a queued task using existing grants and deny unapproved actions."""
        return asdict(runtime.run_task(task_id))

    @app.get("/schedules")
    def schedules():
        """List recurring task schedules for the workspace."""
        return runtime.tasks.schedules()

    @app.websocket("/sessions/{session_id}/ws")
    async def websocket(websocket: WebSocket, session_id: str):
        """Stream one authenticated prompt while monitoring the client connection.

        Run blocking model work in a thread and relay events through an asyncio
        queue. A disconnect rejects pending approvals and stops later emissions.
        """
        if not valid(websocket.headers):
            await websocket.close(code=1008)
            return
        try:
            runtime.sessions.resume(session_id)
        except ValueError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            body = Prompt.model_validate(await websocket.receive_json())
        except (ValueError, WebSocketDisconnect):
            await websocket.close(code=1008)
            return
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        disconnected = threading.Event()

        def emit(event):
            """Transfer a worker event to the event loop unless the client disconnected.
            """
            if disconnected.is_set():
                raise RuntimeError("Client WebSocket déconnecté.")
            # The model runs in a worker thread; only the event loop may touch
            # its asyncio queue directly.
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def run():
            """Execute the prompt and enqueue errors followed by an end-of-stream
            sentinel.
            """
            try:
                runtime.run(session_id, body.prompt, RemoteApprover(broker, session_id),
                            emit)
            except Exception as error:
                loop.call_soon_threadsafe(queue.put_nowait,
                                         {"type": "error", "content": str(error)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(run))
        active.add(task)
        task.add_done_callback(active.discard)
        receiver = asyncio.create_task(websocket.receive())
        next_event = None
        try:
            while True:
                next_event = asyncio.create_task(queue.get())
                # Observe a disconnect even when generation or approval produces
                # no events; waiting only on the output queue would miss it.
                done, _ = await asyncio.wait([receiver, next_event],
                                              return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    break
                event = next_event.result()
                if event is None:
                    await websocket.close()
                    break
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            disconnected.set()
            # Refuse any outstanding approval for the disconnected run.
            for item in broker.list():
                if item["session_id"] == session_id:
                    with suppress(KeyError, ValueError):
                        broker.resolve(item["id"], False)
            receiver.cancel()
            if next_event:
                next_event.cancel()

    return app
