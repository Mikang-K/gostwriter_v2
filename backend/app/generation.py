import asyncio
import hashlib
import json
import time
from dataclasses import replace
from random import random

from .context import build_context
from .errors import AppError
from .memory import extract_memory
from .metrics import log_record, summarize_timing
from .providers import GenerateInput, Provider


def request_hash(request):
    payload = request.model_dump(mode="json", by_alias=True, exclude={"idempotency_key"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Generations:
    def __init__(self, store, settings, factory=None):
        self.store, self.settings = store, settings
        self.factory = factory or self._provider
        self.jobs = {}
        self.local_busy = False

    def _provider(self, kind):
        if kind == "ollama":
            return Provider("ollama", self.settings.ollama_base_url,
                            header_timeout=self.settings.first_token_timeout), self.settings.ollama_model
        if not (self.settings.enable_cloud and self.settings.openai_api_key and self.settings.openai_model):
            raise AppError("CLOUD_NOT_CONFIGURED", 503)
        return Provider("openai", api_key=self.settings.openai_api_key,
                        header_timeout=self.settings.first_token_timeout), self.settings.openai_model

    def targets(self, request):
        primary = self.factory(request.provider)
        fallback = None
        if request.provider == "ollama" and request.allow_cloud and self.settings.enable_cloud:
            fallback = self.factory("openai")
        return primary, fallback

    async def _stream(self, inp, primary, fallback, attempts, task, deadline=None):
        emitted = False
        schedule = [primary, primary] + ([fallback] if fallback else [])
        for index, target in enumerate(schedule):
            provider, model = target
            if task == "memory" and sum(a["task"] == "memory" for a in attempts) >= 12:
                raise AppError("MEMORY_CALL_LIMIT", 422)
            attempt = {"provider": provider.id, "model": model, "task": task,
                       "attempt": index + 1, "status": "running"}
            attempts.append(attempt)
            yield {"type": "provider", "provider": provider.id, "model": model, "attempt": index + 1}
            stream = provider.generate(replace(inp, model=model))
            visible = False
            try:
                while True:
                    limit = self.settings.idle_timeout if visible else self.settings.first_token_timeout
                    try:
                        event = await asyncio.wait_for(anext(stream), limit)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise AppError("TIMEOUT", 504, True) from exc
                    if event["type"] == "delta" and event["text"]:
                        visible = emitted = True
                    yield event
                attempt["status"] = "succeeded"
                return
            except AppError as exc:
                attempt.update(status="failed", errorCode=exc.code)
                if emitted or not exc.retryable or index == len(schedule) - 1:
                    raise
                delay = max(min(.5 * 2**index, 1) + random() * .1, exc.retry_after or 0)
                if deadline is not None and delay >= deadline-time.monotonic():
                    raise
                await asyncio.sleep(delay)
            finally:
                await stream.aclose()

    async def reserve(self, actor, request_id, request):
        self.targets(request)
        await self.store.recover(actor, str(request.project_id))
        reserved = await self.store.reserve(actor, request_id, request, request_hash(request))
        if reserved["kind"] == "created" and request.provider == "ollama":
            if self.local_busy:
                await self.store.terminate(actor, reserved["generationId"], reserved["runEpoch"],
                                           "failed", "LOCAL_BUSY")
                raise AppError("LOCAL_BUSY", 429)
            self.local_busy = True
        return reserved

    async def heartbeat(self, actor, gid, epoch, owner_task):
        try:
            while True:
                await asyncio.sleep(10)
                if not await self.store.touch(actor, gid, epoch):
                    owner_task.cancel()
                    return
        except asyncio.CancelledError:
            pass
        except Exception:
            owner_task.cancel()

    async def run(self, actor, request_id, request, reserved):
        gid, epoch = reserved["generationId"], reserved["runEpoch"]
        job = {"task": asyncio.current_task(), "body": "", "complete": False, "epoch": epoch}
        self.jobs[gid] = job
        attempts = []
        started = time.perf_counter() * 1000
        first, last, usage = None, None, {}
        metrics = {}
        active_target = None
        ctx = {}
        def measured():
            return {**summarize_timing(started, first, last, time.perf_counter()*1000,
                                       usage.get("completionTokens"), usage.get("decodeMs")),
                    "promptTokens": usage.get("promptTokens"), "completionTokens": usage.get("completionTokens"),
                    "provider": active_target[0].id if active_target else request.provider,
                    "model": active_target[1] if active_target else None,
                    "retryCount": sum(a["attempt"] > 1 for a in attempts), "attempts": attempts,
                    "context": {k: v for k, v in ctx.items() if k != "messages"}}
        heartbeat = asyncio.create_task(self.heartbeat(actor, gid, epoch, job["task"]))
        terminal = False
        deadline = time.monotonic() + self.settings.total_timeout
        try:
            async with asyncio.timeout(self.settings.total_timeout):
                await self.store.touch(actor, gid, epoch)
                yield {"type": "accepted", "requestId": request_id, "generationId": gid}
                snapshot = await self.store.snapshot(actor, str(request.project_id))
                ctx = build_context(snapshot, request.direction, request.mode,
                                    self.settings.context_window, self.settings.max_output_tokens)
                primary, fallback = self.targets(request)
                active_target = primary
                inp = GenerateInput(ctx["messages"], primary[1], self.settings.max_output_tokens,
                                    context_window=self.settings.context_window)
                finish = None
                async for event in self._stream(inp, primary, fallback, attempts, "narrative", deadline):
                    if event["type"] == "provider":
                        active_target = primary if event["provider"] == primary[0].id else fallback
                        yield event
                    elif event["type"] == "delta":
                        now = time.perf_counter() * 1000
                        if event["text"]:
                            first = now if first is None else first
                            last = now
                        job["body"] += event["text"]
                        yield event
                    elif event["type"] == "usage":
                        usage = event
                    elif event["type"] == "finish":
                        finish = event["reason"]
                if finish != "stop" or not job["body"].strip():
                    raise AppError("OUTPUT_" + str(finish).upper(), 502)
                job["complete"] = True
                await self.store.save_draft(actor, gid, epoch, job["body"], True)
                yield {"type": "phase", "phase": "finalizing"}
                memory, summary = await self.finalize_memory(snapshot, request, job["body"],
                                                             active_target, attempts, deadline)
                metrics = {**summarize_timing(started, first, last, time.perf_counter()*1000,
                                              usage.get("completionTokens"), usage.get("decodeMs")),
                           "promptTokens": usage.get("promptTokens"),
                           "completionTokens": usage.get("completionTokens"),
                           "provider": active_target[0].id, "model": active_target[1],
                           "retryCount": sum(a["attempt"] > 1 for a in attempts), "attempts": attempts,
                           "context": {k: v for k, v in ctx.items() if k != "messages"}}
                result = await self.store.commit(actor, gid, epoch, job["body"], summary, memory, metrics)
                terminal = True
                yield {"type": "committed", **result}
                yield {"type": "done", "status": "succeeded"}
        except asyncio.CancelledError:
            metrics = measured()
            await self._failure(actor, gid, epoch, job, "cancelled", "CANCELLED", metrics)
            terminal = True
            raise
        except Exception as exc:
            metrics = measured()
            code = exc.code if isinstance(exc, AppError) else "TIMEOUT" if isinstance(exc, TimeoutError) else "INTERNAL"
            await self._failure(actor, gid, epoch, job, "failed", code, metrics)
            terminal = True
            yield {"type": "error", "code": code, "retryable": bool(getattr(exc, "retryable", False)),
                   "draftAvailable": bool(job["body"])}
            yield {"type": "done", "status": "failed"}
        finally:
            metrics = measured()
            heartbeat.cancel()
            self.jobs.pop(gid, None)
            if request.provider == "ollama":
                self.local_busy = False
            if not terminal:
                await self._failure(actor, gid, epoch, job, "interrupted", "INTERRUPTED", metrics)
            try:
                await asyncio.wait_for(asyncio.shield(self.store.record_metrics(actor, gid, epoch, metrics)), 5)
            except (Exception, asyncio.CancelledError):
                pass
            try:
                await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            print(log_record(request_id=request_id, generation_id=gid,
                             status="terminal", generation_ms=time.perf_counter()*1000-started))

    async def _failure(self, actor, gid, epoch, job, status, code, metrics):
        async def save():
            try:
                await self.store.save_draft(actor, gid, epoch, job["body"], job["complete"])
            except Exception:
                pass
            await self.store.terminate(actor, gid, epoch, status, code, metrics)
        try:
            await asyncio.wait_for(asyncio.shield(save()), 5)
        except (Exception, asyncio.CancelledError):
            pass

    async def finalize_memory(self, snapshot, request, body, target, attempts, deadline=None):
        memory_deadline = time.monotonic() + self.settings.memory_timeout
        deadline = min(deadline, memory_deadline) if deadline else memory_deadline
        async def collect(messages, schema):
            inp = GenerateInput(messages, target[1], 1024, context_window=self.settings.context_window,
                                temperature=0, response_schema=schema)
            text, finish = "", None
            async for event in self._stream(inp, target, None, attempts, "memory", deadline):
                if event["type"] == "delta":
                    text += event["text"]
                elif event["type"] == "finish":
                    finish = event["reason"]
            if finish != "stop":
                raise AppError("INVALID_MEMORY", 502)
            return text
        async with asyncio.timeout(self.settings.memory_timeout):
            return await extract_memory(snapshot, body, request.mode, collect, self.settings.context_window)

    async def cancel(self, actor, gid):
        job = self.jobs.get(gid)
        if job:
            try:
                await self.store.save_draft(actor, gid, job["epoch"], job["body"], job["complete"])
            except AppError:
                pass
        result = await self.store.cancel(actor, gid)
        if job:
            job["task"].cancel()
        return result

    async def finalize_saved(self, actor, gid, expected_revision):
        saved = await self.store.generation(actor, gid)
        from .models import GenerateRequest
        request = GenerateRequest.model_validate(saved["input"])
        target, _ = self.targets(request)
        if request.provider == "ollama":
            if self.local_busy:
                raise AppError("LOCAL_BUSY", 429)
            self.local_busy = True
        try:
            reserved = await self.store.reserve_finalization(actor, gid, expected_revision)
        except BaseException:
            if request.provider == "ollama":
                self.local_busy = False
            raise
        epoch = reserved["runEpoch"]
        self.jobs[gid] = {"task": asyncio.current_task(), "body": saved["draftBody"],
                          "complete": True, "epoch": epoch}
        heartbeat = asyncio.create_task(self.heartbeat(actor, gid, epoch, asyncio.current_task()))
        try:
            snapshot = await self.store.snapshot(actor, str(request.project_id))
            memory, summary = await self.finalize_memory(snapshot, request, saved["draftBody"], target, [])
            return await self.store.commit(actor, gid, epoch, saved["draftBody"], summary, memory, saved["metrics"])
        except BaseException:
            await self._failure(actor, gid, epoch, self.jobs[gid], "failed", "FINALIZE_FAILED", saved["metrics"])
            raise
        finally:
            heartbeat.cancel()
            self.jobs.pop(gid, None)
            if request.provider == "ollama":
                self.local_busy = False
            try:
                await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                pass
