import json
import math


def summarize_timing(started_at, first_token_at, last_token_at, completed_at, completion_tokens, decode_ms):
    def rate(duration):
        if completion_tokens is None or duration is None or duration <= 0:
            return None
        value = completion_tokens * 1000 / duration
        return value if math.isfinite(value) else None
    return {"ttftMs": None if first_token_at is None else max(0, first_token_at - started_at),
            "generationMs": max(0, completed_at - started_at),
            "endToEndTps": rate(None if last_token_at is None else last_token_at - started_at),
            "providerTps": rate(decode_ms)}


LOG_FIELDS = {"request_id", "generation_id", "provider", "model", "task", "attempt", "status",
              "error_code", "retry_count", "prompt_tokens", "completion_tokens", "ttft_ms",
              "generation_ms", "tokens_per_second"}


def log_record(**fields):
    return json.dumps({k: v for k, v in fields.items() if k in LOG_FIELDS}, ensure_ascii=False)
