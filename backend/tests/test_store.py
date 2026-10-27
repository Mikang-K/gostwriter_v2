import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict

from app.errors import AppError
from app.models import GenerateRequest, ProjectInput, ProjectPatch
from app.store import Store


@pytest_asyncio.fixture
async def store():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL absent: genuine PostgreSQL tests were not run")
    if not conninfo_to_dict(dsn).get("dbname", "").startswith("ghostwriter_test"):
        pytest.fail("TEST_DATABASE_URL must name an isolated ghostwriter_test* database")
    result = Store(dsn)
    await result.open()
    await result.migrate()
    try:
        yield result
    finally:
        await result.close()


async def project(store):
    actor = str(uuid4())
    return actor, await store.create_project(actor, ProjectInput(title="Store test"))


def request(snapshot, *, key=None):
    return GenerateRequest(project_id=snapshot["projectId"], expected_revision=snapshot["revision"],
                           idempotency_key=key or uuid4(), mode="continue" if snapshot["episodes"] else "initial")


async def running(store, actor, snapshot):
    reservation = await store.reserve(actor, str(uuid4()), request(snapshot), "payload")
    assert await store.touch(actor, reservation["generationId"], reservation["runEpoch"])
    return reservation["generationId"], reservation["runEpoch"]


def test_epoch_guard_rejects_old_and_terminal_runs():
    Store._require_run({"run_epoch": 2, "status": "running", "cancel_requested": False}, 2)
    for row in [{"run_epoch": 2, "status": "running", "cancel_requested": False},
                {"run_epoch": 1, "status": "cancelled", "cancel_requested": True},
                {"run_epoch": 1, "status": "succeeded", "cancel_requested": False}]:
        with pytest.raises(AppError, match="STALE_GENERATION"):
            Store._require_run(row, 1)


@pytest.mark.asyncio
async def test_owner_filters_and_idempotency_precede_revision_validation(store):
    actor, snapshot = await project(store)
    command = request(snapshot)
    original = await store.reserve(actor, "request-1", command, "same")
    duplicate = await store.reserve(actor, "request-2", command, "same")
    assert duplicate == {**original, "kind": "existing"}
    with pytest.raises(AppError, match="IDEMPOTENCY_CONFLICT"):
        await store.reserve(actor, "request-3", command, "changed")
    stranger = str(uuid4())
    assert await store.list_projects(stranger) == []
    for operation in [store.snapshot(stranger, snapshot["projectId"]),
                      store.generation(stranger, original["generationId"]),
                      store.cancel(stranger, original["generationId"])]:
        with pytest.raises(AppError, match="NOT_FOUND"):
            await operation


@pytest.mark.asyncio
async def test_atomic_commit_rolls_back_late_json_failure(store):
    actor, snapshot = await project(store)
    generation, epoch = await running(store, actor, snapshot)
    with pytest.raises(TypeError):
        await store.commit(actor, generation, epoch, "body", "summary", snapshot["memory"],
                           {"invalidJson": object()})
    assert await store.snapshot(actor, snapshot["projectId"]) == snapshot
    assert (await store.generation(actor, generation))["status"] == "running"
    async with store.pool.connection() as conn:
        count = await (await conn.execute(
            "SELECT count(*) AS n FROM ghostwriter.memory_revisions WHERE project_id=%s AND owner_id=%s",
            (snapshot["projectId"], actor))).fetchone()
        assert count["n"] == 1


@pytest.mark.asyncio
async def test_first_undo_restores_baseline_and_preserves_author_settings(store):
    actor, baseline = await project(store)
    generation, epoch = await running(store, actor, baseline)
    memory = {**baseline["memory"], "plotState": "generated"}
    committed = await store.commit(actor, generation, epoch, "chapter body", "summary", memory, {})
    assert committed["revision"] == 1
    patch = ProjectPatch(title="Author edit", notes="Keep this", expected_revision=1)
    edited = await store.update_project(actor, baseline["projectId"], patch)
    undone = await store.undo(actor, baseline["projectId"], edited["revision"])
    assert undone["revision"] == 3
    assert undone["memoryRevision"] == 0
    assert undone["episodes"] == []
    assert undone["memory"] == baseline["memory"]
    assert (undone["title"], undone["notes"]) == ("Author edit", "Keep this")
    assert (await store.generation(actor, generation))["undoneAt"] is not None
    with pytest.raises(AppError, match="NOT_FOUND"):
        await store.episode(actor, baseline["projectId"], committed["episodeId"])


@pytest.mark.asyncio
async def test_recovery_and_finalization_fence_old_epoch(store):
    actor, snapshot = await project(store)
    generation, epoch = await running(store, actor, snapshot)
    await store.save_draft(actor, generation, epoch, "durable draft", True)
    async with store.pool.connection() as conn:
        await conn.execute("UPDATE ghostwriter.generation_requests SET heartbeat_at=now()-interval '61 seconds' "
                           "WHERE id=%s AND owner_id=%s", (generation, actor))
    assert await store.recover(actor, snapshot["projectId"]) == 1
    finalization = await store.reserve_finalization(actor, generation, snapshot["revision"])
    assert finalization["runEpoch"] > epoch
    assert not await store.touch(actor, generation, epoch)
    with pytest.raises(AppError, match="STALE_GENERATION"):
        await store.save_draft(actor, generation, epoch, "stale overwrite", True)
    with pytest.raises(AppError, match="STALE_GENERATION"):
        await store.commit(actor, generation, epoch, "stale", "summary", snapshot["memory"], {})
    await store.terminate(actor, generation, epoch, "failed", "OLD_WORKER")
    state = await store.generation(actor, generation)
    assert state["status"] == "finalizing"
    assert state["draftBody"] == "durable draft"
    await store.commit(actor, generation, finalization["runEpoch"], state["draftBody"], "summary",
                       snapshot["memory"], {})


@pytest.mark.asyncio
async def test_cancel_and_commit_are_atomic_in_either_order(store):
    actor, snapshot = await project(store)
    generation, epoch = await running(store, actor, snapshot)
    results = await asyncio.gather(
        store.cancel(actor, generation),
        store.commit(actor, generation, epoch, "body", "summary", snapshot["memory"], {}),
        return_exceptions=True)
    state = await store.generation(actor, generation)
    current = await store.snapshot(actor, snapshot["projectId"])
    assert state["status"] in ("cancelled", "succeeded")
    assert len(current["episodes"]) == (1 if state["status"] == "succeeded" else 0)
    assert sum(isinstance(value, AppError) for value in results) == 1
    assert not await store.touch(actor, generation, epoch)


@pytest.mark.asyncio
async def test_concurrent_reservations_allow_one_active_project_run(store):
    actor, snapshot = await project(store)
    results = await asyncio.gather(
        store.reserve(actor, "one", request(snapshot), "one"),
        store.reserve(actor, "two", request(snapshot), "two"), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, AppError)]
    assert len(failures) == 1 and failures[0].code == "GENERATION_ACTIVE"


@pytest.mark.asyncio
async def test_restart_durability_history_projection_and_owner_episode(store):
    actor, snapshot = await project(store)
    project_id = snapshot["projectId"]
    first_id = None
    for number in range(3):
        generation, epoch = await running(store, actor, snapshot)
        committed = await store.commit(actor, generation, epoch, f"body {number}", "summary", snapshot["memory"], {})
        first_id = first_id or committed["episodeId"]
        snapshot = await store.snapshot(actor, project_id)
    assert [ep["body"] for ep in snapshot["episodes"]] == [None, "body 1", "body 2"]
    restarted = Store(os.environ["TEST_DATABASE_URL"])
    await restarted.open()
    try:
        await restarted.migrate()
        assert await restarted.snapshot(actor, project_id) == snapshot
        assert (await restarted.episode(actor, project_id, first_id))["body"] == "body 0"
        with pytest.raises(AppError, match="NOT_FOUND"):
            await restarted.episode(str(uuid4()), project_id, first_id)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_sessions_expire_and_delete(store):
    actor = str(uuid4())
    token, expired = uuid4().hex, uuid4().hex
    await store.session_create(token, actor, datetime.now(timezone.utc) + timedelta(hours=1))
    await store.session_create(expired, actor, datetime.now(timezone.utc) - timedelta(seconds=1))
    assert await store.session_get(token) == actor
    assert await store.session_get(expired) is None
    await store.session_delete(token)
    assert await store.session_get(token) is None


@pytest.mark.asyncio
async def test_finished_project_and_active_mutations_are_rejected(store):
    actor, snapshot = await project(store)
    generation, epoch = await running(store, actor, snapshot)
    with pytest.raises(AppError, match="GENERATION_ACTIVE"):
        await store.finish_project(actor, snapshot["projectId"], 0)
    await store.terminate(actor, generation, epoch, "failed", "TEST")
    finished = await store.finish_project(actor, snapshot["projectId"], 0)
    assert finished["revision"] == 1 and finished["memory"]["isFinished"]
    with pytest.raises(AppError, match="PROJECT_FINISHED"):
        await store.reserve(actor, "finished", request(finished), "new")


async def test_metrics_provider_uses_execution_record_with_request_fallback(store):
    actor, snapshot = await project(store)
    generation, epoch = await running(store, actor, snapshot)
    before = (await store.metrics(actor))[0]
    assert before["provider"] == "ollama" and before["metrics"] == {}
    await store.terminate(actor, generation, epoch, "failed", "TEST")
    await store.record_metrics(actor, generation, epoch, {"provider": "openai", "model": "fallback-model"})
    after = (await store.metrics(actor))[0]
    assert after["provider"] == "openai" and after["model"] == "fallback-model"
    assert after["provider"] == after["metrics"]["provider"]
