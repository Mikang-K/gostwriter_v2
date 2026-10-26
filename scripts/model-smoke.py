"""Explicit real Ollama + isolated PostgreSQL smoke; synthetic story only."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg.conninfo import conninfo_to_dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import Settings
from app.main import create_app
from app.store import Store


async def main():
    config = json.loads(Path(".cache/development-db.json").read_text("utf8"))
    dsn = config["testDatabaseUrl"]
    if not conninfo_to_dict(dsn).get("dbname", "").startswith("ghostwriter_test"):
        raise ValueError("Isolated PostgreSQL test database required")
    settings = Settings(_env_file=None, database_url=dsn, app_password=config["appPassword"],
                        owner_id=str(uuid4()), context_window=8192, max_output_tokens=512)
    store = Store(dsn)
    await store.open()
    await store.migrate()
    try:
        app = create_app(settings, store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                     headers={"origin": settings.app_origin}) as client:
            login = await client.post("/api/auth/login", json={"password": settings.app_password})
            login.raise_for_status()
            response = await client.post("/api/projects", json={"title": "실제 모델 검증: 달빛의 문",
                "worldBible": "달빛이 닿아야 문이 열린다.", "characters": [{"name": "하린", "personality": "신중하다"}]})
            response.raise_for_status()
            project = response.json()
            response = await client.post("/api/generations", json={"projectId": project["projectId"],
                "expectedRevision": 0, "idempotencyKey": str(uuid4()), "mode": "initial",
                "direction": "하린이 달빛 아래 문을 발견한다. 80자 이내의 짧은 소설 한 문단으로 완결하라.",
                "provider": "ollama", "allowCloud": False})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            accepted = next(e for e in events if e["type"] == "accepted")
            generation = (await client.get(f"/api/generations/{accepted['generationId']}")).json()
            saved = (await client.get(f"/api/projects/{project['projectId']}")).json()
            result = {"model": settings.ollama_model, "status": generation["status"],
                      "errorCode": generation["errorCode"], "metrics": generation["metrics"],
                      "draftComplete": generation["draftComplete"], "body": generation["draftBody"],
                      "episodeCount": len(saved["episodes"]), "memory": saved["memory"]}
            output = Path("artifacts/model-smoke.json")
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
            assert generation["status"] == "succeeded", generation["errorCode"]
            assert len(saved["episodes"]) == 1 and saved["episodes"][0]["body"] == generation["draftBody"]
            assert len(saved["memory"]["characterStates"]) == 1
            print("Real Ollama + PostgreSQL smoke passed; artifacts/model-smoke.json")
    finally:
        await store.close()


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(main())
