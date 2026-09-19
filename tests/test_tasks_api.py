"""Test task lifecycle, recurring schedules, authenticated APIs, and approvals."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from conftest import response
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gemma_agents.api import create_app
from gemma_agents.security.approvals import ApprovalBroker
from gemma_agents.tasks import TaskStore


def test_task_claim_is_atomic_and_failure_is_saved(runtime):
    """Persist task failures, permit explicit recovery, and reject completed-task
    claims.
    """
    task_id = runtime.tasks.create("Échoue")
    with pytest.raises(IndexError):
        runtime.run_task(task_id)
    assert runtime.tasks.get(task_id)["status"] == "failed"
    runtime.tasks.recover(task_id)
    runtime.llm.responses = [response()]
    assert runtime.run_task(task_id).status == "completed"
    with pytest.raises(ValueError):
        runtime.run_task(task_id)


def test_background_denial_is_blocked_not_completed(runtime):
    """Keep unattended denied actions blocked despite the model's success claim."""
    task_id = runtime.tasks.create("Lance les tests")
    runtime.llm.responses = [
        response(tools=[("run_command", {"command": "uv run pytest"})]),
        response("Les tests ont réussi."),
    ]
    assert runtime.tick() == [task_id]
    assert runtime.tasks.get(task_id)["status"] == "blocked"


def test_schedule_timezone_atomic_enqueue_pause_and_missed_ticks(runtime):
    """Convert local cron times to UTC and coalesce missed ticks without duplicates."""
    next_run = TaskStore.next_due("0 9 * * *", "Europe/Paris",
                                  datetime(2026, 9, 18, 0, tzinfo=UTC))
    assert next_run == "2026-09-18T07:00:00+00:00"
    schedule_id = runtime.tasks.schedule("Inspecte", "* * * * *")
    future = datetime.now(UTC) + timedelta(days=1)
    ids = runtime.tasks.enqueue_due(future)
    assert len(ids) == 1
    assert runtime.tasks.enqueue_due(future) == []
    runtime.tasks.pause(schedule_id)
    assert runtime.tasks.enqueue_due(future + timedelta(days=1)) == []
    with pytest.raises(ValueError):
        runtime.tasks.schedule("Inspecte", "not cron")


def test_http_auth_sessions_and_tasks(runtime):
    """Enforce token, origin, host, and body validation across session and task routes.
    """
    runtime.llm.responses = [response("Bonjour")]
    with TestClient(create_app(runtime)) as client:
        assert client.get("/health").status_code == 401
        headers = {"Authorization": "Bearer " + runtime.settings.api_token}
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/health", headers={**headers, "Origin": "https://evil.test"}
                          ).status_code == 401
        assert client.get("/health", headers={**headers, "Host": "evil.test"}
                          ).status_code == 400
        session = client.post("/sessions", headers=headers).json()["id"]
        result = client.post(f"/sessions/{session}/messages", headers=headers,
                             json={"prompt": "Bonjour"})
        assert result.status_code == 200
        assert result.json()["content"] == "Bonjour"
        history = client.get(f"/sessions/{session}/messages", headers=headers).json()
        assert len(history) == 2
        assert client.post(f"/sessions/{session}/messages", headers=headers,
                           json={"prompt": "", "extra": True}).status_code == 422
        assert client.get("/openapi.json", headers=headers).status_code == 200
        task = client.post("/tasks", json={"prompt": "Travaille"}, headers=headers)
        assert task.status_code == 201
        assert client.get("/tasks", headers=headers).json()[0]["status"] == "pending"


def test_websocket_auth_and_event_stream(runtime):
    """Reject unauthenticated sockets and stream ordered tool events for valid clients.
    """
    runtime.llm.responses = [response(tools=[("list_files", {})]), response("Fini")]
    session = runtime.sessions.create()
    with TestClient(create_app(runtime)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/sessions/{session}/ws"):
                pass
        with client.websocket_connect(f"/sessions/{session}/ws", headers={
                "Authorization": "Bearer " + runtime.settings.api_token}) as ws:
            ws.send_json({"prompt": "Inspecte"})
            events = []
            while True:
                event = ws.receive_json()
                events.append(event["type"])
                if event["type"] == "final":
                    assert event["content"] == "Fini"
                    break
        assert events == ["step", "context", "tool_request", "tool_result",
                          "step", "context", "final"]


def test_approval_broker_single_call_and_denial_on_close():
    """Resolve one pending approval and reject reuse after its waiter has completed."""
    broker = ApprovalBroker()
    result = []
    ready = threading.Event()
    original = broker.pending

    class NotifyDict(dict):
        """Signal when an approval enters the pending map so the test avoids a race."""

        def __setitem__(self, key, value):
            """Store the request before waking the thread that will resolve its
            approval.
            """
            super().__setitem__(key, value)
            ready.set()

    broker.pending = NotifyDict(original)
    thread = threading.Thread(target=lambda: result.append(
        broker.request("session", "run_command", {"command": "pwd"}, "test")))
    thread.start()
    assert ready.wait(2)
    item = broker.list()[0]
    broker.resolve(item["id"], True)
    thread.join(2)
    assert result == [True]
    assert broker.list() == []
    with pytest.raises(KeyError):
        broker.resolve(item["id"], True)
