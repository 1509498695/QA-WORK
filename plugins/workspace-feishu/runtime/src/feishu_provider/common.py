from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from capability_contracts.errors import CapabilityError, CapabilityErrorCode


OPEN_API_ORIGIN = "https://open.feishu.cn"
WIKI_NODE_ENDPOINT = "/open-apis/wiki/v2/spaces/get_node"
RESOLVED_OBJECT_TOKEN = re.compile(r"^[A-Za-z0-9_-]{6,256}$")


class WikiNodeResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_token: str
    space_id: str | None = None
    object_type: str
    object_token: str
    title: str | None = None
    node_type: str | None = None
    has_child: bool | None = None


def data_object(response: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            f"Feishu returned invalid JSON for {operation}.",
        ) from exc
    if not isinstance(payload, dict):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            f"Feishu returned an invalid response for {operation}.",
        )
    platform_code = payload.get("code")
    if platform_code not in {0, "0", None}:
        raise platform_error(response.status_code, platform_code, operation)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            f"Feishu returned no data object for {operation}.",
        )
    return data


def http_error(response: httpx.Response, operation: str) -> CapabilityError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    platform_code = payload.get("code") if isinstance(payload, dict) else None
    return platform_error(response.status_code, platform_code, operation)


def platform_error(
    http_status: int,
    platform_code: object,
    operation: str,
) -> CapabilityError:
    normalized_code = str(platform_code) if platform_code is not None else ""
    if normalized_code in {"131006", "1310213", "99991672"}:
        code = CapabilityErrorCode.PERMISSION_DENIED
    elif normalized_code in {"131005", "1310214", "1310249"}:
        code = CapabilityErrorCode.RESOURCE_NOT_FOUND
    elif normalized_code in {"1061045", "99991400"}:
        code = CapabilityErrorCode.RATE_LIMITED
    elif http_status == 401:
        code = CapabilityErrorCode.AUTH_REQUIRED
    elif http_status == 403:
        code = CapabilityErrorCode.PERMISSION_DENIED
    elif http_status == 404:
        code = CapabilityErrorCode.RESOURCE_NOT_FOUND
    elif http_status == 429:
        code = CapabilityErrorCode.RATE_LIMITED
    elif http_status >= 500 or normalized_code.startswith("13152"):
        code = CapabilityErrorCode.PROVIDER_UNAVAILABLE
    else:
        code = CapabilityErrorCode.PROVIDER_CONTRACT_ERROR
    return CapabilityError(
        code,
        f"Feishu rejected the {operation} operation.",
        retryable=code
        in {
            CapabilityErrorCode.RATE_LIMITED,
            CapabilityErrorCode.PROVIDER_UNAVAILABLE,
        },
        details=(
            {"platform_code": platform_code}
            if isinstance(platform_code, (int, str))
            else {}
        ),
    )


def optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def required_text(value: object, field: str, operation: str = "Wiki node") -> str:
    text = optional_text(value)
    if text is None:
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            f"Feishu returned no {field} for the {operation}.",
        )
    return text
