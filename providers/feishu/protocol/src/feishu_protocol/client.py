from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LocalClientIdentity:
    """Execution-client credentials delivered by the local deployment binding."""

    client_ref: str
    client_secret: str = field(repr=False)
