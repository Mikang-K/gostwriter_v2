import asyncio
import json
from uuid import uuid4

import anyio
import pytest

from app.errors import AppError
from app.generation import Generations
from app.models import Character, GenerateRequest, ProjectInput


class ScriptedProvider:
    def __init__(self, scripts, kind="ollama"):
        self.id, self.scripts = kind, list(scripts)
        self.calls = []
        self.closed = 0

    async def generate(self, inp):
        self.calls.append(inp)
        script = self.scripts.pop(0)
        try:
            for item in script:
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, float):
                    await asyncio.sleep(item)
                elif isinstance(item, asyncio.Event):
                    item.set()
                    await asyncio.Event().wait()
                else:
                    yield item
        finally:
            self.closed += 1


def completed(text, reason="stop"):
    return [{"type": "delta", "text": text}, {"type": "finish", "reason": reason}]


async def job(store, settings, provider, *, cloud=None, consent=False):
    snapshot = await store.create_project(settings.owner_id, ProjectInput(
        title="검증 소설", world_bible="하늘은 보라색이다.",
        characters=[Character(name="하린", personality="침착함")]))
    request = GenerateRequest(project_id=snapshot["projectId"], expected_revision=0,
                              idempotency_key=uuid4(), mode="initial", allow_cloud=consent)
    targets = []

    def factory(kind):
        targets.append(kind)
        return (provider if kind == "ollama" else cloud), "test-model"

    engine = Generations(store, settings, factory)
    reservation = await engine.reserve(settings.owner_id, "test-request", request)
    return engine, snapshot, request, reservation, targets


async def run(engine, settings, request, reservation):
    return [event async for event in engine.run(settings.owner_id, "test-request", request, reservation)]


async def test_failure_before_token_retries_once_and_commits(store, settings, memory_update):
    provider = ScriptedProvider([[AppError("UNAVAILABLE", 502, True)], completed("본문"),
                                 completed(json.dumps(memory_update))])
    engine, before, request, reservation, _ = await job(store, settings, provider)
    records = await run(engine, settings, request, reservation)
    assert records[-1] == {"type": "done", "status": "succeeded"}
    assert len(provider.calls) == provider.closed == 3
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert [attempt["status"] for attempt in saved["metrics"]["attempts"]] == ["failed", "succeeded", "succeeded"]
    snapshot = await store.snapshot(settings.owner_id, before["projectId"])
    assert snapshot["worldBible"] == before["worldBible"] and snapshot["characters"] == before["characters"]
    assert snapshot["memory"]["characterStates"] == before["memory"]["characterStates"]


async def test_consent_allows_cloud_only_after_two_local_failures(store, settings, memory_update):
    settings.enable_cloud = True
    local = ScriptedProvider([[AppError("UNAVAILABLE", 502, True)]] * 2)
    cloud = ScriptedProvider([completed("클라우드 본문"), completed(json.dumps(memory_update))], "openai")
    engine, _, request, reservation, targets = await job(store, settings, local, cloud=cloud, consent=True)
    records = await run(engine, settings, request, reservation)
    assert records[-1]["status"] == "succeeded"
    assert len(local.calls) == 2 and len(cloud.calls) == 2 and "openai" in targets
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["metrics"]["provider"] == "openai"
    assert [attempt["provider"] for attempt in saved["metrics"]["attempts"]] == [
        "ollama", "ollama", "openai", "openai"]
    metrics = await store.metrics(settings.owner_id)
    assert metrics[0]["provider"] == metrics[0]["metrics"]["provider"] == "openai"


async def test_no_consent_prevents_cloud_even_when_local_fails(store, settings):
    settings.enable_cloud = True
    local = ScriptedProvider([[AppError("UNAVAILABLE", 502, True)]] * 2)
    cloud = ScriptedProvider([], "openai")
    engine, before, request, reservation, targets = await job(store, settings, local, cloud=cloud)
    records = await run(engine, settings, request, reservation)
    assert records[-1]["status"] == "failed"
    assert len(local.calls) == 2 and cloud.calls == [] and set(targets) == {"ollama"}
    assert (await store.snapshot(settings.owner_id, before["projectId"]))["episodes"] == []


async def test_failure_after_token_never_retries_or_falls_back(store, settings):
    settings.enable_cloud = True
    local = ScriptedProvider([[{"type": "delta", "text": "일부 본문"}, AppError("UNAVAILABLE", 502, True)]])
    cloud = ScriptedProvider([], "openai")
    engine, before, request, reservation, _ = await job(store, settings, local, cloud=cloud, consent=True)
    records = await run(engine, settings, request, reservation)
    assert next(event for event in records if event["type"] == "error")["draftAvailable"] is True
    assert len(local.calls) == local.closed == 1 and cloud.calls == []
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["status"] == "failed" and saved["draftBody"] == "일부 본문"
    assert saved["draftComplete"] is False
    assert (await store.snapshot(settings.owner_id, before["projectId"]))["revision"] == 0


@pytest.mark.parametrize("memory_change", [
    {"worldBible": "악의적인 세계관 변경"},
    {"isFinished": True},
    {"characterStates": [{"characterId": str(uuid4()), "status": "새로운 인물"}]},
    {"relationships": [{"subjectId": str(uuid4()), "objectId": str(uuid4()), "tag": "적", "intimacy": 0}]},
])
async def test_generated_memory_cannot_change_authored_invariants(store, settings, memory_update, memory_change):
    provider = ScriptedProvider([completed("본문"), completed(json.dumps({**memory_update, **memory_change}))])
    engine, before, request, reservation, _ = await job(store, settings, provider)
    records = await run(engine, settings, request, reservation)
    assert next(event for event in records if event["type"] == "error")["code"] == "INVALID_MEMORY"
    assert await store.snapshot(settings.owner_id, before["projectId"]) == before
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["draftComplete"] is True and saved["committedEpisodeId"] is None


async def test_length_finish_preserves_incomplete_draft_without_memory_call(store, settings):
    provider = ScriptedProvider([completed("잘린 본문", "length")])
    engine, before, request, reservation, _ = await job(store, settings, provider)
    records = await run(engine, settings, request, reservation)
    assert next(event for event in records if event["type"] == "error")["code"] == "OUTPUT_LENGTH"
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["draftBody"] == "잘린 본문" and not saved["draftComplete"]
    assert len(provider.calls) == 1 and await store.snapshot(settings.owner_id, before["projectId"]) == before


@pytest.mark.parametrize("timeout_kind", ["first", "idle", "total", "memory"])
async def test_timeouts_close_provider_release_busy_and_preserve_revision(store, settings, memory_update,
                                                                         timeout_kind):
    settings.total_timeout = 3
    if timeout_kind == "first":
        settings.first_token_timeout = .01
        scripts = [[1.0], [1.0]]
    elif timeout_kind == "idle":
        settings.idle_timeout = .01
        scripts = [[{"type": "delta", "text": "일부"}, 1.0]]
    elif timeout_kind == "total":
        settings.total_timeout = .03
        scripts = [[{"type": "delta", "text": "일부"}, 1.0]]
    else:
        settings.memory_timeout = .01
        scripts = [completed("완전한 본문"), [1.0]]
    provider = ScriptedProvider(scripts)
    engine, before, request, reservation, _ = await job(store, settings, provider)
    records = await run(engine, settings, request, reservation)
    assert next(event for event in records if event["type"] == "error")["code"] == "TIMEOUT"
    assert engine.jobs == {} and engine.local_busy is False and provider.closed == len(provider.calls)
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["status"] == "failed" and saved["errorCode"] == "TIMEOUT"
    assert saved["draftComplete"] is (timeout_kind == "memory")
    assert await store.snapshot(settings.owner_id, before["projectId"]) == before


async def test_explicit_cancel_closes_upstream_and_preserves_partial_draft(store, settings):
    entered = asyncio.Event()
    provider = ScriptedProvider([[{"type": "delta", "text": "취소 전 본문"}, entered]])
    engine, before, request, reservation, _ = await job(store, settings, provider)
    task = asyncio.create_task(run(engine, settings, request, reservation))
    await asyncio.wait_for(entered.wait(), 2)
    await engine.cancel(settings.owner_id, reservation["generationId"])
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = await store.generation(settings.owner_id, reservation["generationId"])
    assert saved["status"] == "cancelled" and saved["draftBody"] == "취소 전 본문"
    assert saved["committedEpisodeId"] is None and not saved["draftComplete"]
    assert engine.jobs == {} and engine.local_busy is False and provider.closed == 1
    assert await store.snapshot(settings.owner_id, before["projectId"]) == before


async def failed_complete_draft(store, actor):
    snapshot = await store.create_project(actor, ProjectInput(title="Finalize concurrency"))
    request = GenerateRequest(project_id=snapshot["projectId"], expected_revision=0,
                              idempotency_key=uuid4(), mode="initial")
    reserved = await store.reserve(actor, str(uuid4()), request, "finalize-test")
    gid, epoch = reserved["generationId"], reserved["runEpoch"]
    assert await store.touch(actor, gid, epoch)
    await store.save_draft(actor, gid, epoch, "Complete narrative", True)
    await store.terminate(actor, gid, epoch, "failed", "INVALID_MEMORY")
    return snapshot, gid


async def test_concurrent_finalizers_claim_local_slot_before_database_await(
        store, settings, memory_update, monkeypatch):
    first_project, first_gid = await failed_complete_draft(store, settings.owner_id)
    second_project, second_gid = await failed_complete_draft(store, settings.owner_id)
    provider = ScriptedProvider([completed(json.dumps(memory_update))] * 2)
    engine = Generations(store, settings, lambda kind: (provider, "test-local"))
    entered, release = asyncio.Event(), asyncio.Event()
    reserve = store.reserve_finalization
    calls = []

    async def paused_reserve(actor, gid, revision):
        calls.append(gid)
        if gid == first_gid:
            entered.set()
            await release.wait()
        return await reserve(actor, gid, revision)

    monkeypatch.setattr(store, "reserve_finalization", paused_reserve)
    first = asyncio.create_task(engine.finalize_saved(settings.owner_id, first_gid, 0))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(AppError, match="LOCAL_BUSY"):
            await engine.finalize_saved(settings.owner_id, second_gid, 0)
        assert calls == [first_gid] and provider.calls == []
        assert engine.local_busy is True
    finally:
        release.set()
        outcome = (await asyncio.gather(first, return_exceptions=True))[0]
    assert isinstance(outcome, dict) and outcome["revision"] == 1
    assert len((await store.snapshot(settings.owner_id, first_project["projectId"]))["episodes"]) == 1
    assert (await store.snapshot(settings.owner_id, second_project["projectId"]))["episodes"] == []
    assert engine.local_busy is False and engine.jobs == {}


async def test_failed_finalization_reservation_releases_local_slot(store, settings, memory_update):
    _, gid = await failed_complete_draft(store, settings.owner_id)
    provider = ScriptedProvider([completed(json.dumps(memory_update))])
    engine = Generations(store, settings, lambda kind: (provider, "test-local"))
    with pytest.raises(AppError, match="REVISION_CONFLICT"):
        await engine.finalize_saved(settings.owner_id, gid, 1)
    assert engine.local_busy is False and engine.jobs == {} and provider.calls == []
    assert (await engine.finalize_saved(settings.owner_id, gid, 0))["revision"] == 1
    assert engine.local_busy is False


async def test_repeated_scope_cancellation_releases_slot_before_async_cleanup(store, settings, memory_update):
    provider = ScriptedProvider([[{"type": "delta", "text": "Before cancellation"}, 1.0],
                                 completed("After cancellation"), completed(json.dumps(memory_update))])
    engine, _, request, reservation, _ = await job(store, settings, provider)
    with anyio.CancelScope() as scope:
        async for event in engine.run(settings.owner_id, "cancelled-scope", request, reservation):
            if event["type"] == "delta":
                scope.cancel()
    assert engine.local_busy is False and engine.jobs == {} and provider.closed == 1
    snapshot = await store.create_project(settings.owner_id, ProjectInput(title="Next local job"))
    next_request = GenerateRequest(project_id=snapshot["projectId"], expected_revision=0,
                                   idempotency_key=uuid4(), mode="initial")
    reserved = await engine.reserve(settings.owner_id, "after-cancel", next_request)
    records = await run(engine, settings, next_request, reserved)
    assert records[-1] == {"type": "done", "status": "succeeded"}


async def test_finalizer_scope_cancellation_releases_slot_and_allows_retry(
        store, settings, memory_update, monkeypatch):
    _, gid = await failed_complete_draft(store, settings.owner_id)
    provider = ScriptedProvider([completed(json.dumps(memory_update))])
    engine = Generations(store, settings, lambda kind: (provider, "test-local"))
    finalize = engine.finalize_memory
    with anyio.CancelScope() as scope:
        async def cancelled_memory(*args, **kwargs):
            scope.cancel()
            await asyncio.sleep(0)
        monkeypatch.setattr(engine, "finalize_memory", cancelled_memory)
        await engine.finalize_saved(settings.owner_id, gid, 0)
    assert engine.local_busy is False and engine.jobs == {} and provider.calls == []
    async with asyncio.timeout(2):
        while (await store.generation(settings.owner_id, gid))["status"] != "failed":
            await asyncio.sleep(.01)
    monkeypatch.setattr(engine, "finalize_memory", finalize)
    assert (await engine.finalize_saved(settings.owner_id, gid, 0))["revision"] == 1
    assert engine.local_busy is False
