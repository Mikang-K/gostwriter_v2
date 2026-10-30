"""Restore synthetic data into a new local database; never open the user's manuscript database."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from app.models import GenerateRequest, ProjectInput
from app.store import Store
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


async def main(pg_bin: Path):
    config = json.loads(Path(".cache/development-db.json").read_text("utf-8"))
    connection = conninfo_to_dict(config["testDatabaseUrl"])
    if connection.get("host") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local PostgreSQL required")
    if not connection.get("dbname", "").startswith("ghostwriter_test"):
        raise ValueError("Isolated test settings required")
    programs = {name: pg_bin / (name + (".exe" if os.name == "nt" else ""))
                for name in ("pg_dump", "pg_restore")}
    for program in programs.values():
        if not program.is_file():
            raise ValueError(f"Missing client tool: {program.name}")
    run_id = uuid4().hex[:12]
    source, restored = (f"ghostwriter_test_backup_{run_id}_{part}" for part in ("source", "restored"))
    directory = Path("artifacts") / f"backup-smoke-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    dump = directory / "synthetic.dump"
    admin = make_conninfo(**{**connection, "dbname": "postgres"})
    created = []
    async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
        try:
            for database in (source, restored):
                await conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created.append(database)
            actor = str(uuid4())
            store = Store(make_conninfo(**{**connection, "dbname": source}))
            await store.open()
            try:
                await store.migrate()
                project = await store.create_project(actor, ProjectInput(
                    title="백업 복원 검증", world_bible="달빛 아래 문이 열린다.",
                    characters=[{"name": "하린", "personality": "신중하다"}]))
                command = GenerateRequest(project_id=project["projectId"], expected_revision=0,
                                          idempotency_key=uuid4(), mode="initial")
                reservation = await store.reserve(actor, str(uuid4()), command, "synthetic-backup")
                generation, epoch = reservation["generationId"], reservation["runEpoch"]
                assert await store.touch(actor, generation, epoch)
                memory = {**project["memory"], "plotState": "문을 발견했다.", "nextOptions": ["문을 연다"]}
                await store.commit(actor, generation, epoch, "하린은 달빛 아래 문을 발견했다.",
                                   "비밀 문 발견", memory, {"provider": "synthetic", "model": "no-llm"})
                expected = await store.snapshot(actor, project["projectId"])
                episode_id = expected["episodes"][0]["id"]
                episode = await store.episode(actor, project["projectId"], episode_id)
                receipt = await store.generation(actor, generation)
            finally:
                await store.close()
            arguments = ["--host", connection["host"], "--port", connection.get("port", "5432"),
                         "--username", connection["user"], "--no-password"]
            env = {**os.environ, "PGPASSWORD": connection.get("password", "")}
            commands = [
                [str(programs["pg_dump"]), *arguments, "--dbname", source, "--format=custom", "--file", str(dump)],
                [str(programs["pg_restore"]), *arguments, "--dbname", restored, "--no-owner", "--exit-on-error", str(dump)],
            ]
            for command in commands:
                await asyncio.to_thread(subprocess.run, command, env=env, check=True, capture_output=True, timeout=120)
            store = Store(make_conninfo(**{**connection, "dbname": restored}))
            await store.open()
            try:
                await store.migrate()
                assert await store.snapshot(actor, project["projectId"]) == expected
                assert await store.episode(actor, project["projectId"], episode_id) == episode
                assert await store.generation(actor, generation) == receipt
            finally:
                await store.close()
            versions = {}
            for name, path in programs.items():
                versions[name] = (await asyncio.to_thread(subprocess.check_output,
                                                         [str(path), "--version"], text=True)).strip()
            result = {"status": "passed", "syntheticOnly": True, "llmCalls": 0,
                      "checks": ["settings", "characters", "memory", "episode", "generation receipt", "migration"],
                      "dumpSha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                      "clientVersions": versions}
        finally:
            for database in reversed(created):
                await conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
    result["testDatabasesRemoved"] = True
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Backup/restore passed: {directory / 'result.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, required=True)
    asyncio.run(main(parser.parse_args().pg_bin),
                loop_factory=asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop)
