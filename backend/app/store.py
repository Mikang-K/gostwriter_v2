from datetime import datetime
from pathlib import Path
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.errors import AppError
from app.models import GenerateRequest, Memory, ProjectInput, ProjectPatch, initial_memory


ACTIVE = ("pending", "running", "finalizing")


class Store:
    def __init__(self, dsn: str):
        self.pool = AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row})

    async def open(self):
        await self.pool.open(wait=True)

    async def close(self):
        await self.pool.close()

    async def migrate(self):
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(716243821)")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS ghostwriter")
            await conn.execute("CREATE TABLE IF NOT EXISTS ghostwriter.schema_migrations "
                               "(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            row = await (await conn.execute(
                "SELECT version FROM ghostwriter.schema_migrations WHERE version=1")).fetchone()
            if row is None:
                sql = (Path(__file__).resolve().parents[1] / "migrations/001_core.sql").read_text("utf-8")
                await conn.execute(sql, prepare=False)

    async def _project(self, conn, actor, project_id, *, lock=False):
        row = await (await conn.execute(
            "SELECT * FROM ghostwriter.projects WHERE id=%s AND owner_id=%s" +
            (" FOR UPDATE" if lock else ""), (project_id, actor))).fetchone()
        if row is None:
            raise AppError("NOT_FOUND", 404)
        return row

    async def _request(self, conn, actor, generation_id, *, lock=False):
        row = await (await conn.execute(
            "SELECT * FROM ghostwriter.generation_requests WHERE id=%s AND owner_id=%s" +
            (" FOR UPDATE" if lock else ""), (generation_id, actor))).fetchone()
        if row is None:
            raise AppError("NOT_FOUND", 404)
        return row

    async def _locked_request(self, conn, actor, generation_id):
        row = await self._request(conn, actor, generation_id)
        project = await self._project(conn, actor, row["project_id"], lock=True)
        return project, await self._request(conn, actor, generation_id, lock=True)

    async def _editable(self, conn, actor, project, expected_revision):
        if project["revision"] != expected_revision:
            raise AppError("REVISION_CONFLICT", 409)
        active = await (await conn.execute(
            "SELECT id FROM ghostwriter.generation_requests WHERE project_id=%s AND owner_id=%s "
            "AND status IN ('pending','running','finalizing')", (project["id"], actor))).fetchone()
        if active:
            raise AppError("GENERATION_ACTIVE", 409)

    @staticmethod
    def _episode(row, body=True):
        return {"id": str(row["id"]), "episodeNumber": row["episode_number"],
                "body": row["body"] if body else None, "summary": row["summary"],
                "beforeMemoryRevision": row["before_memory_revision"],
                "afterMemoryRevision": row["after_memory_revision"]}

    async def _snapshot(self, conn, actor, project):
        project_id = project["id"]
        chars = await (await conn.execute(
            "SELECT data FROM ghostwriter.characters WHERE project_id=%s AND owner_id=%s ORDER BY position",
            (project_id, actor))).fetchall()
        memory = await (await conn.execute(
            "SELECT memory FROM ghostwriter.memory_revisions WHERE project_id=%s AND owner_id=%s AND revision=%s",
            (project_id, actor, project["head_memory_revision"]))).fetchone()
        episodes = await (await conn.execute(
            "SELECT id, episode_number, summary, before_memory_revision, after_memory_revision, "
            "CASE WHEN row_number() OVER (ORDER BY episode_number DESC) <= 2 THEN body ELSE NULL END AS body "
            "FROM ghostwriter.episodes WHERE project_id=%s AND owner_id=%s AND deleted_at IS NULL "
            "ORDER BY episode_number", (project_id, actor))).fetchall()
        return {**project["settings"], "projectId": str(project_id), "revision": project["revision"],
                "memoryRevision": project["head_memory_revision"], "archived": project["archived"],
                "characters": [row["data"] for row in chars], "memory": memory["memory"],
                "episodes": [self._episode(row) for row in episodes]}

    async def _characters(self, conn, actor, project_id, characters):
        await conn.execute("DELETE FROM ghostwriter.characters WHERE project_id=%s AND owner_id=%s",
                           (project_id, actor))
        for position, character in enumerate(characters):
            await conn.execute(
                "INSERT INTO ghostwriter.characters(project_id,owner_id,id,position,data) VALUES (%s,%s,%s,%s,%s)",
                (project_id, actor, character.id, position, Jsonb(character.model_dump(mode="json", by_alias=True))))

    async def create_project(self, actor: str, data: ProjectInput):
        project_id = uuid4()
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("INSERT INTO ghostwriter.projects(id,owner_id,settings) VALUES (%s,%s,%s)",
                               (project_id, actor, Jsonb(data.model_dump(mode="json", by_alias=True,
                                                                      exclude={"characters"}))))
            await self._characters(conn, actor, project_id, data.characters)
            await conn.execute(
                "INSERT INTO ghostwriter.memory_revisions(project_id,owner_id,revision,memory) VALUES (%s,%s,0,%s)",
                (project_id, actor, Jsonb(initial_memory(data.characters))))
            return await self._snapshot(conn, actor, await self._project(conn, actor, project_id))

    async def list_projects(self, actor: str, archived: bool = False):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id,settings,revision,archived FROM ghostwriter.projects "
                "WHERE owner_id=%s AND archived=%s ORDER BY updated_at DESC", (actor, archived))).fetchall()
            return [{"projectId": str(row["id"]), "title": row["settings"]["title"],
                     "revision": row["revision"], "archived": row["archived"]} for row in rows]

    async def snapshot(self, actor: str, project_id: str):
        async with self.pool.connection() as conn, conn.transaction():
            # Repeatable read keeps settings, episode history and memory at one committed head.
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            return await self._snapshot(conn, actor, await self._project(conn, actor, project_id))

    async def episode(self, actor, project_id, episode_id):
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT * FROM ghostwriter.episodes WHERE id=%s AND project_id=%s AND owner_id=%s "
                "AND deleted_at IS NULL", (episode_id, project_id, actor))).fetchone()
            if row is None:
                raise AppError("NOT_FOUND", 404)
            return self._episode(row)

    async def update_project(self, actor, project_id, patch: ProjectPatch):
        async with self.pool.connection() as conn, conn.transaction():
            project = await self._project(conn, actor, project_id, lock=True)
            await self._editable(conn, actor, project, patch.expected_revision)
            await self._characters(conn, actor, project_id, patch.characters)
            await conn.execute(
                "UPDATE ghostwriter.projects SET settings=%s,archived=%s,revision=revision+1,updated_at=now() "
                "WHERE id=%s AND owner_id=%s",
                (Jsonb(patch.model_dump(mode="json", by_alias=True,
                                       exclude={"characters", "expected_revision", "archived"})),
                 patch.archived, project_id, actor))
            return await self._snapshot(conn, actor, await self._project(conn, actor, project_id))

    async def finish_project(self, actor, project_id, expected_revision: int):
        async with self.pool.connection() as conn, conn.transaction():
            project = await self._project(conn, actor, project_id, lock=True)
            await self._editable(conn, actor, project, expected_revision)
            current = await self._snapshot(conn, actor, project)
            memory = {**current["memory"], "isFinished": True}
            revision = project["revision"] + 1
            await conn.execute(
                "INSERT INTO ghostwriter.memory_revisions(project_id,owner_id,revision,memory) VALUES (%s,%s,%s,%s)",
                (project_id, actor, revision, Jsonb(memory)))
            await conn.execute(
                "UPDATE ghostwriter.projects SET revision=%s,head_memory_revision=%s,updated_at=now() "
                "WHERE id=%s AND owner_id=%s", (revision, revision, project_id, actor))
            return await self._snapshot(conn, actor, await self._project(conn, actor, project_id))

    async def undo(self, actor, project_id, expected_revision: int):
        async with self.pool.connection() as conn, conn.transaction():
            project = await self._project(conn, actor, project_id, lock=True)
            await self._editable(conn, actor, project, expected_revision)
            episode = await (await conn.execute(
                "SELECT * FROM ghostwriter.episodes WHERE project_id=%s AND owner_id=%s AND deleted_at IS NULL "
                "ORDER BY episode_number DESC LIMIT 1 FOR UPDATE", (project_id, actor))).fetchone()
            if episode is None:
                raise AppError("NO_EPISODES", 409)
            await conn.execute("UPDATE ghostwriter.episodes SET deleted_at=now() WHERE id=%s AND owner_id=%s",
                               (episode["id"], actor))
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET undone_at=now() WHERE id=%s AND owner_id=%s",
                (episode["generation_id"], actor))
            await conn.execute(
                "UPDATE ghostwriter.projects SET revision=revision+1,head_memory_revision=%s,updated_at=now() "
                "WHERE id=%s AND owner_id=%s", (episode["before_memory_revision"], project_id, actor))
            return await self._snapshot(conn, actor, await self._project(conn, actor, project_id))

    @staticmethod
    def _reservation(row, kind="existing"):
        return {"kind": kind, "generationId": str(row["id"]), "status": row["status"],
                "runEpoch": row["run_epoch"]}

    async def reserve(self, actor, request_id: str, request: GenerateRequest, payload_hash: str):
        async with self.pool.connection() as conn, conn.transaction():
            # Serialize owner reservations to make both idempotency and the minute quota atomic.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(actor),))
            existing = await (await conn.execute(
                "SELECT * FROM ghostwriter.generation_requests WHERE owner_id=%s AND idempotency_key=%s",
                (actor, request.idempotency_key))).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise AppError("IDEMPOTENCY_CONFLICT", 409)
                return self._reservation(existing)
            project = await self._project(conn, actor, request.project_id, lock=True)
            await self._editable(conn, actor, project, request.expected_revision)
            snapshot = await self._snapshot(conn, actor, project)
            if snapshot["memory"]["isFinished"]:
                raise AppError("PROJECT_FINISHED", 409)
            if project["archived"]:
                raise AppError("PROJECT_ARCHIVED", 409)
            if (request.mode == "initial") != (len(snapshot["episodes"]) == 0):
                raise AppError("INVALID_MODE", 409)
            count = await (await conn.execute(
                "SELECT count(*) AS n FROM ghostwriter.generation_requests WHERE owner_id=%s "
                "AND created_at > now()-interval '1 minute'", (actor,))).fetchone()
            if count["n"] >= 10:
                raise AppError("RATE_LIMITED", 429, True)
            row = await (await conn.execute(
                "INSERT INTO ghostwriter.generation_requests "
                "(id,project_id,owner_id,request_id,idempotency_key,payload_hash,input,base_revision,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *",
                (uuid4(), request.project_id, actor, request_id, request.idempotency_key, payload_hash,
                 Jsonb(request.model_dump(mode="json", by_alias=True)), project["revision"]))).fetchone()
            return self._reservation(row, "created")

    @staticmethod
    def _generation(row):
        result = {"generationId": str(row["id"]), "projectId": str(row["project_id"]),
                  "requestId": row["request_id"], "status": row["status"], "runEpoch": row["run_epoch"],
                  "input": row["input"], "baseRevision": row["base_revision"], "draftBody": row["draft_body"],
                  "draftComplete": row["draft_complete"], "errorCode": row["error_code"],
                  "committedEpisodeId": str(row["committed_episode_id"]) if row["committed_episode_id"] else None,
                  "undoneAt": row["undone_at"].isoformat() if row["undone_at"] else None,
                  "metrics": row["metrics"]}
        for key, column in [("createdAt", "created_at"), ("heartbeatAt", "heartbeat_at"),
                            ("completedAt", "completed_at")]:
            result[key] = row[column].isoformat() if row[column] else None
        return result

    async def generation(self, actor, generation_id):
        async with self.pool.connection() as conn:
            return self._generation(await self._request(conn, actor, generation_id))

    @staticmethod
    def _require_run(row, epoch):
        if row["run_epoch"] != epoch or row["status"] not in ACTIVE or row["cancel_requested"]:
            raise AppError("STALE_GENERATION", 409)

    async def save_draft(self, actor, generation_id, epoch: int, body: str, complete: bool):
        async with self.pool.connection() as conn, conn.transaction():
            _, row = await self._locked_request(conn, actor, generation_id)
            self._require_run(row, epoch)
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET draft_body=%s,draft_complete=%s,heartbeat_at=now(), "
                "status=CASE WHEN %s THEN 'finalizing' ELSE status END "
                "WHERE id=%s AND owner_id=%s", (body, complete, complete, generation_id, actor))

    async def record_metrics(self, actor, generation_id, epoch, metrics):
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET metrics=%s WHERE id=%s AND owner_id=%s "
                "AND run_epoch=%s AND status IN ('succeeded','failed','cancelled','interrupted')",
                (Jsonb(metrics), generation_id, actor, epoch))

    async def commit(self, actor, generation_id, epoch: int, body: str, summary: str, memory: dict, metrics: dict):
        memory = Memory.model_validate(memory).model_dump(mode="json", by_alias=True)
        async with self.pool.connection() as conn, conn.transaction():
            project, row = await self._locked_request(conn, actor, generation_id)
            self._require_run(row, epoch)
            if row["status"] not in ("running", "finalizing"):
                raise AppError("STALE_GENERATION", 409)
            if row["base_revision"] != project["revision"]:
                raise AppError("REVISION_CONFLICT", 409)
            # Generated state may reference authored characters, but cannot introduce new ones.
            chars = await (await conn.execute(
                "SELECT id FROM ghostwriter.characters WHERE project_id=%s AND owner_id=%s",
                (project["id"], actor))).fetchall()
            ids = {str(character["id"]) for character in chars}
            referenced = {state["characterId"] for state in memory["characterStates"]}
            for relation in memory["relationships"]:
                referenced.update((relation["subjectId"], relation["objectId"]))
            for hint in memory["openForeshadowing"]:
                referenced.update(hint["characterIds"])
            if not referenced <= ids:
                raise AppError("INVALID_MEMORY", 422)
            memory["isFinished"] = row["input"]["mode"] == "epilogue"
            count = await (await conn.execute(
                "SELECT count(*) AS n FROM ghostwriter.episodes WHERE project_id=%s AND owner_id=%s "
                "AND deleted_at IS NULL", (project["id"], actor))).fetchone()
            revision, episode_id = project["revision"] + 1, uuid4()
            await conn.execute(
                "INSERT INTO ghostwriter.memory_revisions(project_id,owner_id,revision,memory) VALUES (%s,%s,%s,%s)",
                (project["id"], actor, revision, Jsonb(memory)))
            await conn.execute(
                "INSERT INTO ghostwriter.episodes "
                "(id,project_id,owner_id,generation_id,episode_number,body,summary,before_memory_revision,after_memory_revision) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (episode_id, project["id"], actor, generation_id, count["n"] + 1, body, summary,
                 project["head_memory_revision"], revision))
            await conn.execute(
                "UPDATE ghostwriter.projects SET revision=%s,head_memory_revision=%s,updated_at=now() "
                "WHERE id=%s AND owner_id=%s", (revision, revision, project["id"], actor))
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET status='succeeded',committed_episode_id=%s,"
                "draft_body=%s,draft_complete=true,metrics=%s,error_code=NULL,completed_at=now(),heartbeat_at=now() "
                "WHERE id=%s AND owner_id=%s", (episode_id, body, Jsonb(metrics), generation_id, actor))
            return {"episodeId": str(episode_id), "revision": revision}

    async def terminate(self, actor, generation_id, epoch: int, status: str, error_code: str | None,
                        metrics: dict | None = None):
        if status not in ("failed", "cancelled", "interrupted"):
            raise ValueError("Invalid terminal status")
        async with self.pool.connection() as conn, conn.transaction():
            _, row = await self._locked_request(conn, actor, generation_id)
            if row["run_epoch"] != epoch or row["status"] not in ACTIVE:
                return
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET status=%s,error_code=%s,metrics=%s,completed_at=now(),"
                "cancel_requested=%s WHERE id=%s AND owner_id=%s",
                (status, error_code, Jsonb(metrics if metrics is not None else row["metrics"]),
                 status == "cancelled", generation_id, actor))

    async def cancel(self, actor, generation_id):
        async with self.pool.connection() as conn, conn.transaction():
            _, row = await self._locked_request(conn, actor, generation_id)
            if row["status"] == "succeeded":
                raise AppError("ALREADY_COMMITTED", 409)
            if row["status"] in ACTIVE:
                row = await (await conn.execute(
                    "UPDATE ghostwriter.generation_requests SET status='cancelled',cancel_requested=true,"
                    "error_code='CANCELLED',completed_at=now() WHERE id=%s AND owner_id=%s RETURNING *",
                    (generation_id, actor))).fetchone()
            return self._generation(row)

    async def touch(self, actor, generation_id, epoch: int):
        async with self.pool.connection() as conn, conn.transaction():
            _, row = await self._locked_request(conn, actor, generation_id)
            if row["run_epoch"] != epoch or row["status"] not in ACTIVE or row["cancel_requested"]:
                return False
            await conn.execute(
                "UPDATE ghostwriter.generation_requests SET status=CASE WHEN status='pending' THEN 'running' "
                "ELSE status END,heartbeat_at=now() WHERE id=%s AND owner_id=%s", (generation_id, actor))
            return True

    async def recover(self, actor, project_id):
        async with self.pool.connection() as conn, conn.transaction():
            await self._project(conn, actor, project_id, lock=True)
            result = await conn.execute(
                "UPDATE ghostwriter.generation_requests SET status='interrupted',run_epoch=run_epoch+1,"
                "error_code='INTERRUPTED',completed_at=now() WHERE project_id=%s AND owner_id=%s "
                "AND status IN ('pending','running','finalizing') AND heartbeat_at < now()-interval '60 seconds'",
                (project_id, actor))
            return result.rowcount

    async def reserve_finalization(self, actor, generation_id, expected_revision: int):
        async with self.pool.connection() as conn, conn.transaction():
            project, row = await self._locked_request(conn, actor, generation_id)
            await self._editable(conn, actor, project, expected_revision)
            if row["base_revision"] != project["revision"]:
                raise AppError("REVISION_CONFLICT", 409)
            if row["status"] not in ("failed", "interrupted") or row["cancel_requested"]:
                raise AppError("STALE_GENERATION", 409)
            if not row["draft_complete"]:
                raise AppError("DRAFT_INCOMPLETE", 409)
            row = await (await conn.execute(
                "UPDATE ghostwriter.generation_requests SET status='finalizing',run_epoch=run_epoch+1,"
                "error_code=NULL,completed_at=NULL,heartbeat_at=now() WHERE id=%s AND owner_id=%s RETURNING *",
                (generation_id, actor))).fetchone()
            result = self._reservation(row)
            result.pop("kind")
            return result

    async def metrics(self, actor, days: int = 7):
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id,request_id,status,created_at,"
                "COALESCE(metrics->>'provider',input->>'provider') AS provider,"
                "metrics->>'model' AS model,metrics,error_code FROM ghostwriter.generation_requests "
                "WHERE owner_id=%s AND created_at >= now()-(%s * interval '1 day') ORDER BY created_at DESC",
                (actor, days))).fetchall()
            return [{"generationId": str(row["id"]), "requestId": row["request_id"], "status": row["status"],
                     "createdAt": row["created_at"].isoformat(), "provider": row["provider"],
                     "model": row["model"], "metrics": row["metrics"], "errorCode": row["error_code"]} for row in rows]

    async def session_create(self, token_hash: str, actor: str, expires_at: datetime):
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO ghostwriter.sessions(token_hash,owner_id,expires_at) VALUES (%s,%s,%s)",
                               (token_hash, actor, expires_at))

    async def session_get(self, token_hash):
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT owner_id FROM ghostwriter.sessions WHERE token_hash=%s AND expires_at>now()",
                (token_hash,))).fetchone()
            return str(row["owner_id"]) if row else None

    async def session_delete(self, token_hash):
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM ghostwriter.sessions WHERE token_hash=%s", (token_hash,))
