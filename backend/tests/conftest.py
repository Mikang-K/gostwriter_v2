import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings
from app.main import create_app
from app.providers import Provider
from app.store import Store


def pytest_asyncio_loop_factories(config, item):
    return {"selector": asyncio.SelectorEventLoop}


@pytest_asyncio.fixture
async def store():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL absent: genuine PostgreSQL tests were not run")
    if not conninfo_to_dict(dsn).get("dbname", "").startswith("ghostwriter_test"):
        pytest.fail("TEST_DATABASE_URL must name an isolated ghostwriter_test* database")
    database = Store(dsn)
    await database.open()
    await database.migrate()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url="", app_password="test-password-private-marker",
                    owner_id=str(uuid4()), app_origin="http://localhost:3000",
                    gemini_api_key="test-key-private-marker", gemini_model="test-cloud",
                    enable_cloud=False, context_window=8192, max_output_tokens=256)


@pytest.fixture
def memory_update():
    return {"summary": "새로운 단서를 찾았다.", "characterStates": [], "plotState": "단서 발견",
            "openForeshadowing": [], "relationships": [], "nextOptions": ["단서를 따라간다"]}


@pytest_asyncio.fixture
async def api(store, settings, memory_update):
    mock = SimpleNamespace(calls=[], targets=[], memory=memory_update)

    def respond(request):
        payload = json.loads(request.content)
        mock.calls.append(payload)
        content = json.dumps(mock.memory, ensure_ascii=False) if "format" in payload else "그는 문을 열었다."
        records = [{"message": {"content": content}, "done": False},
                   {"message": {"content": ""}, "done": True, "done_reason": "stop",
                    "prompt_eval_count": 10, "eval_count": 12, "eval_duration": 1000000}]
        return httpx.Response(200, text="\n".join(json.dumps(record) for record in records))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:
        def factory(kind):
            mock.targets.append(kind)
            assert kind == "ollama", "Unexpected cloud provider construction"
            return Provider("ollama", "http://127.0.0.1:11435", client=upstream), "test-local"

        app = create_app(settings, store, factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost",
                                     headers={"origin": settings.app_origin}) as client:
            yield SimpleNamespace(client=client, app=app, settings=settings, store=store, mock=mock)


@pytest_asyncio.fixture
async def authenticated(api):
    response = await api.client.post("/api/auth/login", json={"password": api.settings.app_password})
    assert response.status_code == 200
    return api
