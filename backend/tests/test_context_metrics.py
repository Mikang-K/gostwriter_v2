import pytest

from app.context import build_context, narrative_phase
from app.errors import AppError
from app.metrics import log_record, summarize_timing


def test_required_core_does_not_silently_truncate():
    with pytest.raises(AppError, match="CONTEXT_BUDGET_EXCEEDED"):
        build_context({"worldBible": "가" * 2000, "memory": {}, "episodes": []}, "", "initial")
    assert narrative_phase(2, 10) == "기"
    assert narrative_phase(6, 10) == "승"
    assert narrative_phase(9, 10) == "전"
    assert narrative_phase(10, 10) == "결"


def test_timing_uses_tokens_and_separate_clocks():
    assert summarize_timing(0, 1000, 3000, 3500, 40, 2000) == {
        "ttftMs": 1000, "generationMs": 3500, "endToEndTps": 40/3, "providerTps": 20}
    assert summarize_timing(0, None, None, 10, None, None)["providerTps"] is None
    assert "secret" not in log_record(prompt="secret", cookie="secret", status="failed")
