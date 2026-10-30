from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)


def bounded(value: str, limit: int) -> str:
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"UTF-8 limit {limit} bytes exceeded")
    return value


class Character(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=100)
    gender: str = ""
    age_group: str = ""
    personality: str = ""
    speech_style: str = ""
    background: str = ""

    @model_validator(mode="after")
    def size(self):
        bounded(self.model_dump_json(), 4096)
        return self


class ProjectInput(Model):
    title: str = Field(min_length=1)
    genre: str = ""
    language: Literal["ko", "en", "ja"] = "ko"
    notes: str = ""
    world_bible: str = ""
    style_examples: list[str] = Field(default_factory=list, max_length=3)
    target_episodes: int | None = Field(default=None, ge=1, le=10000)
    characters: list[Character] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def limits(self):
        if not self.title.strip():
            raise ValueError("Title is required")
        for value, limit in [(self.title, 200), (self.genre, 2048), (self.notes, 10240),
                             (self.world_bible, 10240)]:
            bounded(value, limit)
        for sample in self.style_examples:
            bounded(sample, 5120)
        if len({c.id for c in self.characters}) != len(self.characters):
            raise ValueError("Duplicate character ID")
        return self


class ProjectPatch(ProjectInput):
    expected_revision: int = Field(ge=0)
    archived: bool = False


class RevisionInput(Model):
    expected_revision: int = Field(ge=0)


class GenerateRequest(Model):
    project_id: UUID
    expected_revision: int = Field(ge=0)
    idempotency_key: UUID
    mode: Literal["initial", "continue", "epilogue"]
    direction: str = ""
    provider: Literal["ollama", "openai"] = "ollama"
    allow_cloud: bool = False

    @field_validator("direction")
    @classmethod
    def direction_limit(cls, v):
        return bounded(v, 2048)

    @model_validator(mode="after")
    def consent(self):
        if self.provider == "openai" and not self.allow_cloud:
            raise ValueError("Cloud consent is required")
        return self


class CharacterState(Model):
    character_id: UUID
    status: str = Field(max_length=500)
    traits: list[str] = Field(default_factory=list, max_length=20)
    appearance: str = Field(default="", max_length=1000)
    is_active: bool = True


class Relationship(Model):
    subject_id: UUID
    object_id: UUID
    tag: str = Field(max_length=100)
    intimacy: int = Field(ge=-100, le=100)
    description: str = Field(default="", max_length=1000)


class Foreshadowing(Model):
    id: UUID = Field(default_factory=uuid4)
    text: str = Field(max_length=1000)
    status: Literal["open", "resolved"] = "open"
    character_ids: list[UUID] = Field(default_factory=list, max_length=10)


class MemoryUpdate(Model):
    summary: str
    character_states: list[CharacterState] = Field(default_factory=list, max_length=10)
    plot_state: str = Field(default="", max_length=4000)
    open_foreshadowing: list[Foreshadowing] = Field(default_factory=list, max_length=100)
    relationships: list[Relationship] = Field(default_factory=list, max_length=100)
    next_options: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def size(self):
        bounded(self.summary, 2048)
        bounded(self.model_dump_json(), 65536)
        return self


class Memory(Model):
    schema_version: Literal[1] = 1
    character_states: list[CharacterState] = Field(default_factory=list)
    plot_state: str = ""
    open_foreshadowing: list[Foreshadowing] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    next_options: list[str] = Field(default_factory=list)
    is_finished: bool = False


def initial_memory(characters: list[Character]) -> dict:
    return Memory(character_states=[CharacterState(character_id=c.id, status="초기 상태")
                                   for c in characters]).model_dump(mode="json", by_alias=True)
