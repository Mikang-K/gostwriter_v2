import pytest
from pydantic import ValidationError

from app.models import GenerateRequest, MemoryUpdate, ProjectInput


def test_byte_limits_and_extra_owner():
    with pytest.raises(ValidationError):
        ProjectInput(title="가" * 67)
    with pytest.raises(ValidationError):
        ProjectInput(title="소설", owner_id="attacker")


def test_cloud_consent_and_direction_bytes():
    base = dict(projectId="00000000-0000-4000-8000-000000000001", expectedRevision=0,
                idempotencyKey="00000000-0000-4000-8000-000000000002", mode="initial")
    with pytest.raises(ValidationError):
        GenerateRequest(**base, provider="gemini")
    with pytest.raises(ValidationError):
        GenerateRequest(**base, direction="가" * 683)


def test_metadata_cannot_modify_invariant_name():
    with pytest.raises(ValidationError):
        MemoryUpdate(summary="요약", characterStates=[dict(
            characterId="00000000-0000-4000-8000-000000000002", status="생존", name="변경"
        )])
