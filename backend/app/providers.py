import asyncio
import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote, urlparse

import httpx

from .errors import AppError


HEADER_TIMEOUT = 10


@dataclass(frozen=True)
class GenerateInput:
    messages: list[dict[str, str]]
    model: str
    max_output_tokens: int = 1024
    temperature: float = 0.7
    context_window: int = 4096
    seed: int | None = None
    response_schema: dict | None = None


class Provider:
    def __init__(self, kind: Literal["ollama", "gemini", "llamacpp"], base_url: str = "",
                 api_key: str = "", client: httpx.AsyncClient | None = None):
        self.id = kind
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client
        if kind != "gemini":
            url = urlparse(base_url)
            if url.scheme != "http" or url.hostname not in {
                "localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"
            } or url.username or url.password:
                raise AppError("INVALID_PROVIDER_URL")

    async def generate(self, inp: GenerateInput) -> AsyncIterator[dict[str, Any]]:
        if not inp.model or not inp.messages:
            raise AppError("INVALID_PROVIDER_INPUT")
        if self.id == "ollama":
            url = self.base_url + "/api/chat"
            payload: dict[str, Any] = {
                "model": inp.model, "messages": inp.messages, "stream": True, "keep_alive": "5m",
                "options": {"num_ctx": inp.context_window, "num_predict": inp.max_output_tokens,
                            "temperature": inp.temperature},
            }
            if inp.seed is not None:
                payload["options"]["seed"] = inp.seed
            if inp.response_schema:
                payload["format"] = inp.response_schema
            headers = {}
        elif self.id == "gemini":
            if not self.api_key:
                raise AppError("CLOUD_NOT_CONFIGURED", 503)
            url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(inp.model, safe="")
            url += ":streamGenerateContent?alt=sse"
            system = "\n".join(m["content"] for m in inp.messages if m["role"] == "system")
            payload = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                                     "parts": [{"text": m["content"]}]}
                                    for m in inp.messages if m["role"] != "system"],
                       "generationConfig": {"maxOutputTokens": inp.max_output_tokens,
                                            "temperature": inp.temperature}}
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            if inp.response_schema:
                payload["generationConfig"].update(responseMimeType="application/json",
                                                   responseJsonSchema=inp.response_schema)
            headers = {"x-goog-api-key": self.api_key}
        else:
            if inp.response_schema:
                raise AppError("UNSUPPORTED_SCHEMA")
            url = self.base_url + "/v1/chat/completions"
            payload = {"model": inp.model, "messages": inp.messages, "stream": True,
                       "stream_options": {"include_usage": True}, "max_tokens": inp.max_output_tokens,
                       "temperature": inp.temperature}
            if inp.seed is not None:
                payload["seed"] = inp.seed
            headers = {}
        client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10, write=10, pool=5))
        finish = None
        usage = None
        any_text = False
        response = None
        try:
            response = await asyncio.wait_for(
                client.send(client.build_request("POST", url, json=payload, headers=headers), stream=True),
                HEADER_TIMEOUT)
            if response.status_code >= 400:
                code = {401: "PROVIDER_AUTH", 403: "PROVIDER_AUTH", 404: "MODEL_MISSING",
                        429: "RATE_LIMITED"}.get(response.status_code, "UNAVAILABLE")
                raise AppError(code, 502, response.status_code in {429, 502, 503, 504},
                               retry_after(response.headers.get("retry-after")))
            async for data in self._records(response):
                if self.id == "ollama":
                    if data.get("error"):
                        raise AppError("INVALID_OUTPUT", 502)
                    text = data.get("message", {}).get("content", "")
                    if data.get("done"):
                        finish = "length" if data.get("done_reason") == "length" else "stop"
                        usage = {"type": "usage", "promptTokens": data.get("prompt_eval_count"),
                                 "completionTokens": data.get("eval_count"),
                                 "decodeMs": ns_to_ms(data.get("eval_duration")),
                                 "loadMs": ns_to_ms(data.get("load_duration"))}
                elif self.id == "gemini":
                    candidates = data.get("candidates", [])
                    candidate = candidates[0] if candidates else {}
                    text = "".join(part.get("text", "") for part in
                                   candidate.get("content", {}).get("parts", []) if not part.get("thought"))
                    reason = candidate.get("finishReason")
                    if reason:
                        finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(reason, "blocked")
                    if data.get("promptFeedback", {}).get("blockReason"):
                        finish = "blocked"
                    if "usageMetadata" in data:
                        m = data["usageMetadata"]
                        usage = {"type": "usage", "promptTokens": m.get("promptTokenCount"),
                                 "completionTokens": m.get("candidatesTokenCount"),
                                 "decodeMs": None, "loadMs": None}
                else:
                    choices = data.get("choices", [])
                    choice = choices[0] if choices else {}
                    text = choice.get("delta", {}).get("content", "") or ""
                    if choice.get("finish_reason"):
                        finish = {"stop": "stop", "length": "length"}.get(choice["finish_reason"], "blocked")
                    if data.get("usage"):
                        usage = {"type": "usage", "promptTokens": data["usage"].get("prompt_tokens"),
                                 "completionTokens": data["usage"].get("completion_tokens"),
                                 "decodeMs": None, "loadMs": None}
                if not isinstance(text, str):
                    raise AppError("INVALID_OUTPUT", 502)
                if text:
                    any_text = True
                    yield {"type": "delta", "text": text}
            if not finish or (not any_text and finish != "blocked"):
                raise AppError("INVALID_OUTPUT", 502)
            if usage:
                yield usage
            yield {"type": "finish", "reason": finish}
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise AppError("TIMEOUT", 504, True) from exc
        except httpx.HTTPError as exc:
            raise AppError("UNAVAILABLE", 502, True) from exc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AppError("INVALID_OUTPUT", 502) from exc
        finally:
            if response is not None:
                await response.aclose()
            if self.client is None:
                await client.aclose()

    async def _records(self, response: httpx.Response) -> AsyncIterator[dict]:
        pending: list[str] = []
        async for line in response.aiter_lines():
            if self.id == "ollama":
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise AppError("INVALID_OUTPUT", 502)
                    yield value
            elif line == "":
                if pending:
                    raw = "\n".join(pending)
                    pending.clear()
                    if raw != "[DONE]":
                        yield json.loads(raw)
            elif line.startswith("data:"):
                pending.append(line[5:].lstrip())
        if pending:
            raw = "\n".join(pending)
            if raw != "[DONE]":
                yield json.loads(raw)


def ns_to_ms(value):
    return value / 1_000_000 if isinstance(value, (int, float)) and value >= 0 else None


def retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None
