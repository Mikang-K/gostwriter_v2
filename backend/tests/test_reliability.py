import time

import pytest

from app.config import Settings
from app.errors import AppError
from app.generation import Generations
from app.providers import GenerateInput


def test_provider_header_budget_matches_configured_first_token_timeout():
    settings = Settings(_env_file=None, first_token_timeout=95, enable_cloud=True,
                        openai_api_key="test-key", openai_model="test-model")
    engine = Generations(None, settings)
    assert engine._provider("ollama")[0].header_timeout == 95
    assert engine._provider("openai")[0].header_timeout == 95


async def test_retry_after_exceeding_remaining_budget_fails_without_second_call():
    class Limited:
        id = "ollama"
        calls = 0
        async def generate(self, inp):
            self.calls += 1
            raise AppError("RATE_LIMITED", 502, True, retry_after=60)
            yield  # pragma: no cover

    provider = Limited()
    engine = Generations(None, Settings(_env_file=None))
    with pytest.raises(AppError, match="RATE_LIMITED"):
        _ = [e async for e in engine._stream(GenerateInput([], "test"), (provider, "test"), None,
                                             [], "narrative", deadline=time.monotonic()+1)]
    assert provider.calls == 1
