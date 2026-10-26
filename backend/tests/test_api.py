import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


def command(project, **changes):
    return {"projectId": project["projectId"], "expectedRevision": project["revision"],
            "idempotencyKey": str(uuid4()), "mode": "initial", **changes}


def events(response):
    # ASGITransport buffers the response; this asserts SSE framing, not network flush timing.
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


async def new_project(api):
    response = await api.client.post("/api/projects", json={"title": "테스트 소설", "worldBible": "달은 두 개다."})
    assert response.status_code == 201
    return response.json()


async def test_password_cookie_session_logout_and_revocation(api):
    client = api.client
    assert (await client.get("/api/projects")).status_code == 401
    bad = await client.post("/api/auth/login", json={"password": "bad"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_PASSWORD"
    good = await client.post("/api/auth/login", json={"password": api.settings.app_password})
    assert good.status_code == 200
    cookie = good.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=28800" in cookie
    token = client.cookies.get("ghostwriter_session")
    assert (await client.get("/api/auth/session")).json() == {"authenticated": True}
    assert await api.store.session_get(hashlib.sha256(token.encode()).hexdigest()) == api.settings.owner_id
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/auth/session")).status_code == 401
    assert await api.store.session_get(hashlib.sha256(token.encode()).hexdigest()) is None
    client.cookies.set("ghostwriter_session", token)
    assert (await client.get("/api/projects")).status_code == 401


async def test_login_rate_limit(api):
    for _ in range(5):
        assert (await api.client.post("/api/auth/login", json={"password": "bad"})).status_code == 401
    response = await api.client.post("/api/auth/login", json={"password": "bad"})
    assert response.status_code == 429 and response.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.parametrize("origin", [None, "http://attacker.example"])
async def test_mutation_requires_exact_origin(api, origin):
    headers = {} if origin is None else {"origin": origin}
    request = api.client.build_request("POST", "/api/auth/login", json={"password": api.settings.app_password})
    request.headers.pop("origin", None)
    request.headers.update(headers)
    response = await api.client.send(request)
    assert response.status_code == 403 and response.json()["error"]["code"] == "INVALID_ORIGIN"
    assert api.settings.app_password not in response.text


async def test_large_and_invalid_bodies_do_not_reflect_secrets(api):
    marker = api.settings.gemini_api_key
    for content, expected in [(marker + "x" * 65536, 413),
                              (json.dumps({"password": marker, "unexpected": marker}), 400)]:
        response = await api.client.post("/api/auth/login", content=content,
                                         headers={"content-type": "application/json"})
        assert response.status_code == expected
        assert marker not in response.text and api.settings.app_password not in response.text
        assert set(response.json()["error"]) == {"code", "message", "requestId"}


async def test_foreign_owner_cannot_read_or_mutate_projects_or_generations(authenticated):
    api = authenticated
    project = await new_project(api)
    generated = await api.client.post("/api/generations", json=command(project))
    records = events(generated)
    gid = records[0]["generationId"]
    episode = next(record["episodeId"] for record in records if record["type"] == "committed")
    stranger_token = "stranger-session-" + str(uuid4())
    await api.store.session_create(hashlib.sha256(stranger_token.encode()).hexdigest(), str(uuid4()),
                                   datetime.now(timezone.utc) + timedelta(hours=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://localhost",
                                 headers={"origin": api.settings.app_origin},
                                 cookies={"ghostwriter_session": stranger_token}) as client:
        assert (await client.get("/api/projects")).json() == []
        pid = project["projectId"]
        operations = [("GET", f"/api/projects/{pid}", None),
                      ("GET", f"/api/projects/{pid}/episodes/{episode}", None),
                      ("GET", f"/api/generations/{gid}", None),
                      ("POST", f"/api/generations/{gid}/cancel", None),
                      ("POST", f"/api/projects/{pid}/undo", {"expectedRevision": 1}),
                      ("POST", "/api/generations", command(project))]
        for method, path, body in operations:
            response = await client.request(method, path, json=body)
            assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    assert len((await api.store.snapshot(api.settings.owner_id, pid))["episodes"]) == 1


async def test_real_provider_payload_memory_commit_and_idempotency(authenticated):
    api = authenticated
    project = await new_project(api)
    request = command(project, direction="문을 열고 단서를 찾는다.")
    response = await api.client.post("/api/generations", json=request)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    records = events(response)
    assert records[0]["type"] == "accepted"
    assert records[-1] == {"type": "done", "status": "succeeded"}
    assert "그는 문을 열었다." == "".join(record["text"] for record in records if record["type"] == "delta")
    narrative, memory = api.mock.calls
    assert narrative["model"] == "test-local" and narrative["stream"] is True
    assert narrative["options"]["num_ctx"] == 8192
    assert request["direction"] in narrative["messages"][-1]["content"]
    assert "format" not in narrative and memory["format"]["additionalProperties"] is False
    assert memory["options"]["temperature"] == 0
    saved = (await api.client.get(f'/api/projects/{project["projectId"]}')).json()
    assert saved["revision"] == saved["memoryRevision"] == 1
    assert saved["episodes"][0]["body"] == "그는 문을 열었다."
    assert saved["memory"]["plotState"] == "단서 발견"
    replay = await api.client.post("/api/generations", json=request)
    assert replay.status_code == 202 and replay.json()["replayed"] is True
    assert len(api.mock.calls) == 2
    request["direction"] = "변경된 지시"
    assert (await api.client.post("/api/generations", json=request)).status_code == 409


async def test_invalid_memory_leaves_complete_draft_and_finalize_can_recover(authenticated):
    api = authenticated
    project = await new_project(api)
    valid_memory = api.mock.memory
    api.mock.memory = {"summary": "허용하지 않은 설정 변경", "worldBible": "변조"}
    response = await api.client.post("/api/generations", json=command(project))
    records = events(response)
    assert next(record for record in records if record["type"] == "error")["code"] == "INVALID_MEMORY"
    gid = records[0]["generationId"]
    saved = (await api.client.get(f"/api/generations/{gid}")).json()
    assert saved["status"] == "failed" and saved["draftComplete"] is True
    assert saved["draftBody"] == "그는 문을 열었다."
    snapshot = (await api.client.get(f'/api/projects/{project["projectId"]}')).json()
    assert snapshot["episodes"] == [] and snapshot["revision"] == 0
    assert snapshot["worldBible"] == project["worldBible"]
    api.mock.memory = valid_memory
    finalized = await api.client.post(f"/api/generations/{gid}/finalize", json={"expectedRevision": 0})
    assert finalized.status_code == 200 and finalized.json()["revision"] == 1
    assert len(api.mock.calls) == 3


async def test_cloud_without_consent_rejected_before_provider_calls(authenticated):
    api = authenticated
    project = await new_project(api)
    response = await api.client.post("/api/generations", json=command(project, provider="gemini"))
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_INPUT"
    assert api.mock.calls == api.mock.targets == []


async def test_fake_upstream_native_http_chunks_memory_and_cancellation(memory_update):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.fake_upstream:app", "--host", "127.0.0.1", "--port", str(port),
         "--no-access-log"], cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
            async with asyncio.timeout(10):
                while True:
                    assert process.poll() is None, "Fake upstream exited before startup"
                    try:
                        if (await client.get("/api/tags")).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(.05)
            payload = {"model": "test-local", "messages": [{"role": "user", "content": "새로운 이야기"}]}
            arrival, records = [], []
            async with client.stream("POST", "/api/chat", json=payload) as response:
                async for line in response.aiter_lines():
                    arrival.append(time.monotonic())
                    records.append(json.loads(line))
            assert len(records) == 6 and records[-1]["done_reason"] == "stop"
            assert "하린" in records[0]["message"]["content"] and arrival[1] - arrival[0] >= .08
            memory = (await client.post("/api/chat", json={**payload, "format": {"type": "object"}})).text
            extracted = json.loads(json.loads(memory.splitlines()[0])["message"]["content"])
            assert set(extracted) == set(memory_update)
            slow = {**payload, "messages": [{"role": "user", "content": "SLOW"}]}
            async with client.stream("POST", "/api/chat", json=slow) as response:
                async for line in response.aiter_lines():
                    assert json.loads(line)["done"] is False
                    break
            async with asyncio.timeout(3):
                while (await client.get("/stats")).json()["cancelledRequests"] != 1:
                    await asyncio.sleep(.05)
            stats = (await client.get("/stats")).json()
            assert stats == {"calls": 3, "narrativeCalls": 2, "memoryCalls": 1, "cancelledRequests": 1}
    finally:
        process.terminate()
        process.wait(timeout=5)
