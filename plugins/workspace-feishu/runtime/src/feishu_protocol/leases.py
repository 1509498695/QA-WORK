from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ref: str = Field(min_length=1, max_length=256)
    profile_ref: str | None = Field(
        default=None,
        pattern=r"^profile_[a-f0-9]{20}$",
    )
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("task_ref")
    @classmethod
    def normalize_task_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("task_ref is invalid")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(item.strip() for item in value if item.strip())))
        if not normalized:
            raise ValueError("At least one capability is required")
        return normalized


class LeaseDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ready"
    lease_ref: str
    task_ref: str
    profile_ref: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    access_token: str = Field(repr=False)
    issued_at: str
    expires_at: str
    token_expires_at: str
