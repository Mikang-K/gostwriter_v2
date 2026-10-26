import json
import asyncio

import httpx
import pytest

from app.errors import AppError
from app.providers import GenerateInput, Provider
from app.providers import retry_after


async def test_ollama_usage_and_hidden_thinking():
    data = [{"message": {"thinking": "secret", "content": "본문"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "prompt_eval_count": 10, "eval_count": 20, "eval_duration": 2_000_000_000,
             "load_duration": 500_000_000}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text="\n".join(json.dumps(d) for d in data)))) as client:
        events = [e async for e in Provider("ollama", "http://localhost:11434", client=client).generate(
            GenerateInput([{"role": "user", "content": "안녕"}], "test"))]
    assert events[0] == {"type": "delta", "text": "본문"}
    assert events[1]["decodeMs"] == 2000
    assert events[1]["loadMs"] == 500
    assert events[-1] == {"type": "finish", "reason": "stop"}


async def test_no_success_without_finish():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text='{"message":{"content":"일부"},"done":false}\n'))) as client:
        with pytest.raises(AppError, match="INVALID_OUTPUT"):
            _ = [e async for e in Provider("ollama", "http://localhost:11434", client=client).generate(
                GenerateInput([{"role": "user", "content": "안녕"}], "test"))]


async def test_gemini_all_parts_and_thought_filter():
    data = {"candidates": [{"content": {"parts": [{"thought": True, "text": "숨김"},
              {"text": "가"}, {"text": "나"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text="data: " + json.dumps(data) + "\n\n"))) as client:
        events = [e async for e in Provider("gemini", api_key="test", client=client).generate(
            GenerateInput([{"role": "user", "content": "안녕"}], "selected-model"))]
    assert events[0] == {"type": "delta", "text": "가나"}
    assert events[1]["completionTokens"] == 2


def test_reject_arbitrary_provider_urls():
    with pytest.raises(AppError, match="INVALID_PROVIDER_URL"):
        Provider("ollama", "http://example.com")


def test_retry_after_rejects_nonfinite_and_parses_delay():
    assert retry_after("2") == 2
    assert retry_after("NaN") is None
    assert retry_after("broken") is None
    assert retry_after("-1") == 0


async def test_retry_after_propagates_without_provider_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        429, headers={"retry-after": "30"}, text="private marker"))) as client:
        with pytest.raises(AppError) as failure:
            _ = [e async for e in Provider("ollama", "http://localhost:11434", client=client).generate(
                GenerateInput([{"role": "user", "content": "안녕"}], "test"))]
        assert failure.value.retry_after == 30
        assert str(failure.value) == "RATE_LIMITED"


async def test_headers_have_a_separate_deadline(monkeypatch):
    monkeypatch.setattr("app.providers.HEADER_TIMEOUT", .01)
    async def stalled_headers(request):
        await asyncio.sleep(1)
        return httpx.Response(200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(stalled_headers)) as client:
        with pytest.raises(AppError, match="TIMEOUT"):
            _ = [e async for e in Provider("ollama", "http://localhost:11434", client=client).generate(
                GenerateInput([{"role": "user", "content": "안녕"}], "test"))]
