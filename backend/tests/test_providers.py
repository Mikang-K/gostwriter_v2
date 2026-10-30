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


@pytest.mark.parametrize("kind,payload", [
    ("ollama", '{"message":{"content":"일부"},"done":false}\n'),
    ("openai", 'data: {"type":"response.output_text.delta","delta":"일부"}\n\n'),
])
async def test_no_success_without_finish(kind, payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text=payload))) as client:
        with pytest.raises(AppError, match="INVALID_OUTPUT"):
            _ = [e async for e in Provider(kind, "http://localhost:11434", api_key="test", client=client).generate(
                GenerateInput([{"role": "user", "content": "안녕"}], "test"))]


async def test_openai_responses_payload_usage_and_hidden_reasoning():
    data = [{"type": "response.reasoning_summary_text.delta", "delta": "private reasoning"},
            {"type": "response.output_text.delta", "delta": "가나"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 10, "output_tokens": 2}}}]

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test"
        payload = json.loads(request.content)
        assert payload["model"] == "selected-model" and payload["store"] is False
        assert payload["input"] == [{"role": "user", "content": "안녕"}]
        assert payload["max_output_tokens"] == 1024 and payload["stream"] is True
        assert "temperature" not in payload and "seed" not in payload
        return httpx.Response(200, text="".join("data: " + json.dumps(d) + "\n\n" for d in data))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [e async for e in Provider("openai", api_key="test", client=client).generate(
            GenerateInput([{"role": "user", "content": "안녕"}], "selected-model"))]
    assert events[0] == {"type": "delta", "text": "가나"}
    assert events[1]["completionTokens"] == 2
    assert events[1]["promptTokens"] == 10
    assert events[-1] == {"type": "finish", "reason": "stop"}


@pytest.mark.parametrize("reason,expected", [("max_output_tokens", "length"), ("content_filter", "blocked")])
async def test_openai_incomplete_is_never_success(reason, expected):
    data = [{"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.incomplete", "response": {"incomplete_details": {"reason": reason}}}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text="".join("data: " + json.dumps(d) + "\n\n" for d in data)))) as client:
        events = [e async for e in Provider("openai", api_key="test", client=client).generate(
            GenerateInput([{"role": "user", "content": "test"}], "test"))]
    assert events[-1] == {"type": "finish", "reason": expected}


@pytest.mark.parametrize("event", ["response.failed", "error"])
async def test_openai_failure_hides_provider_body(event):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text='data: ' + json.dumps({"type": event, "message": "private marker"}) + '\n\n'))) as client:
        with pytest.raises(AppError, match="^INVALID_OUTPUT$"):
            _ = [e async for e in Provider("openai", api_key="test", client=client).generate(
                GenerateInput([{"role": "user", "content": "test"}], "test"))]


async def test_openai_memory_schema_and_refusal():
    from app.models import MemoryUpdate

    schema = MemoryUpdate.model_json_schema(by_alias=True)

    def respond(request):
        output_format = json.loads(request.content)["text"]["format"]
        assert output_format == {"type": "json_schema", "name": "story_memory", "schema": schema, "strict": False}
        events = [{"type": "response.refusal.delta", "delta": "private refusal"},
                  {"type": "response.completed", "response": {}}]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = [e async for e in Provider("openai", api_key="test", client=client).generate(
            GenerateInput([{"role": "user", "content": "JSON"}], "test", response_schema=schema))]
    assert not any(e["type"] == "delta" for e in events)
    assert events[-1] == {"type": "finish", "reason": "blocked"}


async def test_openai_requires_key_before_transport():
    with pytest.raises(AppError, match="CLOUD_NOT_CONFIGURED"):
        _ = [e async for e in Provider("openai").generate(
            GenerateInput([{"role": "user", "content": "test"}], "test"))]


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


async def test_cold_model_headers_can_use_configured_first_response_budget(monkeypatch):
    monkeypatch.setattr("app.providers.HEADER_TIMEOUT", .001)

    async def loading_model(request):
        await asyncio.sleep(.02)
        return httpx.Response(200, text=json.dumps({
            "message": {"content": "문이 열렸다."}, "done": True, "done_reason": "stop"
        }))

    async with httpx.AsyncClient(transport=httpx.MockTransport(loading_model)) as client:
        events = [e async for e in Provider("ollama", "http://localhost:11434", client=client,
                                            header_timeout=1).generate(
            GenerateInput([{"role": "user", "content": "안녕"}], "test"))]
    assert any(e.get("text") == "문이 열렸다." for e in events)
