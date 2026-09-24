"""Transport-independent records and validation."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scope = Literal["global", "project", "domain"]
Kind = Literal["profile", "preference", "fact", "episodic", "decision"]
ContextView = Literal["full", "compact"]
# "strict" keeps the historical keyword behaviour and stays the default. "auto" may run one
# bounded relaxed pass when the strict candidate pool is empty; see retrieval.py.
SearchMode = Literal["strict", "auto"]
Text = Annotated[str, Field(min_length=1, max_length=100_000)]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone, e.g. 2026-09-06T00:00:00Z")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    title: Annotated[str, Field(min_length=1, max_length=500)]
    content: Text
    scope: Scope = "global"
    scope_id: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    type: Kind = "fact"
    valid_from: str = Field(default_factory=now)
    valid_to: str | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    supersedes: str | None = None
    source: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("title", "content", "scope_id")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Value must not be blank")
        return value

    @field_validator("valid_from", "valid_to")
    @classmethod
    def dates(cls, value):
        return timestamp(value) if value is not None else None

    @field_validator("tags")
    @classmethod
    def tag_values(cls, value):
        if any(not tag.strip() or len(tag) > 100 for tag in value):
            raise ValueError("Tags must contain 1..100 nonblank characters")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def check_scope_and_time(self):
        if (self.scope == "global") != (self.scope_id is None):
            raise ValueError("global requires null scope_id; project/domain require a stable scope_id")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from (exclusive upper bound)")
        return self


class Memory(MemoryInput):
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    forgotten_at: str | None = None

    @field_validator("created_at", "updated_at", "forgotten_at")
    @classmethod
    def record_dates(cls, value):
        return timestamp(value) if value is not None else None


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=2000)
    query_variants: list[str] = Field(default_factory=list, max_length=5)
    scope: Scope = "global"
    scope_id: str | None = None
    include_global: bool = True
    type: Kind | None = None
    as_of: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    search_mode: SearchMode = "strict"

    @field_validator("query_variants")
    @classmethod
    def variant_values(cls, value):
        """Trim, drop duplicates, reject blanks; each variant is at most 100 characters."""
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("query_variants must contain strings")
            variant = item.strip()
            if not variant:
                raise ValueError("query_variants must not contain blank entries")
            if len(variant) > 100:
                raise ValueError("Each query_variants entry must be at most 100 characters")
            if variant not in cleaned:
                cleaned.append(variant)
        return cleaned

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.scope == "global") != (self.scope_id is None):
            raise ValueError("global requires null scope_id; project/domain require scope_id")
        if self.scope_id is not None and not self.scope_id.strip():
            raise ValueError("scope_id must not be blank")
        if self.as_of is not None:
            self.as_of = timestamp(self.as_of)
        return self
