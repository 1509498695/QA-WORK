from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.locator import classify_locator
from capability_contracts.models import (
    OperationEvidence,
    OperationStatus,
    ResourceLocator,
    ResourceType,
    TargetKind,
)
from feishu_auth_service.leases import (
    DOCX_MEDIA_READ_CAPABILITY,
    DOCX_READ_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_provider.common import (
    OPEN_API_ORIGIN,
    RESOLVED_OBJECT_TOKEN as _RESOLVED_OBJECT_TOKEN,
    WIKI_NODE_ENDPOINT,
    WikiNodeResolution,
    data_object as _data_object,
    http_error as _http_error,
    optional_text as _optional_text,
    required_text as _required_text,
)
from feishu_provider.lease_client import LeaseClient, LoopbackLeaseClient


DOCUMENT_ENDPOINT = "/open-apis/docx/v1/documents/{document_id}"
BLOCKS_ENDPOINT = "/open-apis/docx/v1/documents/{document_id}/blocks"
MEDIA_DOWNLOAD_ENDPOINT = "/open-apis/drive/v1/medias/{file_token}/download"
MAX_PAGES = 1000
PAGE_SIZE = 500
DEFAULT_MAX_ASSET_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ASSET_COUNT = 64
DEFAULT_MEDIA_DOWNLOAD_INTERVAL_SECONDS = 0.21

# These blocks require additional semantic readers that are not in this slice.
_UNRESOLVED_CONTENT_BLOCK_TYPES = {18, 20, 21, 26, 28, 29, 30, 33, 40}
import re


_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class BinaryAssetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    block_type: int
    asset_kind: Literal["image", "file"]
    file_token: str
    name: str | None = None
    media_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    content_base64: str | None = Field(default=None, repr=False)
    retrieval_complete: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class _AssetDescriptor:
    block_id: str
    block_type: int
    asset_kind: Literal["image", "file"]
    file_token: str
    name: str | None


class DocxReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = "feishu"
    provider_version: str = "0.4.1"
    operation_id: str = "feishu_docx_read"
    status: OperationStatus
    task_ref: str
    profile_ref: str
    source: ResourceLocator
    wiki_resolution: WikiNodeResolution | None = None
    document_id: str
    title: str
    revision_id: str | None = None
    blocks: list[dict[str, Any]]
    block_count: int = Field(ge=0)
    page_count: int = Field(ge=1)
    assets: list[BinaryAssetSnapshot] = Field(default_factory=list)
    asset_count: int = Field(default=0, ge=0)
    asset_total_bytes: int = Field(default=0, ge=0)
    evidence: OperationEvidence


class DocxReader(Protocol):
    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> DocxReadResult: ...


class FeishuDocxClient:
    def __init__(
        self,
        *,
        lease_client: LeaseClient,
        http_client: httpx.AsyncClient | None = None,
        open_api_origin: str = OPEN_API_ORIGIN,
        timeout_seconds: float = 15.0,
        max_asset_bytes: int = DEFAULT_MAX_ASSET_BYTES,
        max_total_asset_bytes: int = DEFAULT_MAX_TOTAL_ASSET_BYTES,
        max_asset_count: int = DEFAULT_MAX_ASSET_COUNT,
        media_download_interval_seconds: float = (
            DEFAULT_MEDIA_DOWNLOAD_INTERVAL_SECONDS
        ),
    ) -> None:
        if max_asset_bytes < 1 or max_total_asset_bytes < 1 or max_asset_count < 1:
            raise ValueError("Asset limits must be positive")
        if not 0 <= media_download_interval_seconds <= 10:
            raise ValueError("Media download interval must be between 0 and 10 seconds")
        self._lease_client = lease_client
        self._origin = open_api_origin.rstrip("/")
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._max_asset_bytes = max_asset_bytes
        self._max_total_asset_bytes = max_total_asset_bytes
        self._max_asset_count = max_asset_count
        self._media_download_interval_seconds = media_download_interval_seconds

    @classmethod
    def default(cls) -> FeishuDocxClient:
        return cls(lease_client=LoopbackLeaseClient.default())

    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> DocxReadResult:
        source = classify_locator(locator)
        if source.target is not TargetKind.FEISHU:
            raise CapabilityError(
                CapabilityErrorCode.INVALID_LOCATOR,
                "A Feishu Docx or Wiki URL is required.",
            )
        if source.resource_type not in {
            ResourceType.FEISHU_DOCX,
            ResourceType.FEISHU_WIKI,
        }:
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                "Feishu Docx read supports only Docx resources or Wiki nodes.",
                details={"resource_type": source.resource_type.value},
            )
        if source.resource_id is None:
            raise CapabilityError(
                CapabilityErrorCode.INVALID_LOCATOR,
                "The Feishu locator has no resource identifier.",
            )
        capabilities = (DOCX_READ_CAPABILITY,)
        if source.resource_type is ResourceType.FEISHU_WIKI:
            capabilities = (DOCX_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY)
        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
        )
        resolved_profile_ref = lease.profile_ref
        headers = {
            "Authorization": f"Bearer {lease.access_token}",
            "Accept": "application/json",
        }
        wiki_resolution: WikiNodeResolution | None = None
        resolved_document = source
        if source.resource_type is ResourceType.FEISHU_WIKI:
            wiki_resolution = await self._get_wiki_node(source.resource_id, headers)
            if wiki_resolution.object_type != "docx":
                raise CapabilityError(
                    CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                    "The Feishu Wiki node does not point to a Docx document.",
                    details={"wiki_object_type": wiki_resolution.object_type},
                )
            resolved_document = _resolved_docx_locator(wiki_resolution.object_token)
        if resolved_document.resource_id is None:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "The resolved Feishu Docx has no document identifier.",
            )
        document_id = resolved_document.resource_id
        document = await self._get_document(document_id, headers)
        blocks, page_count = await self._get_blocks(document_id, headers)
        assets, asset_warnings = await self._get_binary_assets(
            blocks,
            task_ref=task_ref,
            profile_ref=resolved_profile_ref,
        )
        revision_id = _optional_text(document.get("revision_id"))
        title = _optional_text(document.get("title")) or (
            wiki_resolution.title if wiki_resolution is not None else ""
        ) or ""
        unresolved_types = sorted(
            {
                block_type
                for block in blocks
                if isinstance((block_type := block.get("block_type")), int)
                and block_type in _UNRESOLVED_CONTENT_BLOCK_TYPES
            }
        )
        malformed_blocks = sum(
            1
            for block in blocks
            if not isinstance(block.get("block_id"), str)
            or not isinstance(block.get("block_type"), int)
        )
        warnings = []
        if unresolved_types:
            warnings.append(
                "unresolved_content_block_types:" + ",".join(map(str, unresolved_types))
            )
        if malformed_blocks:
            warnings.append(f"malformed_blocks:{malformed_blocks}")
        warnings.extend(asset_warnings)
        retrieval_complete = not warnings
        canonical = json.dumps(
            {
                "wiki_resolution": (
                    wiki_resolution.model_dump(mode="json")
                    if wiki_resolution is not None
                    else None
                ),
                "document": document,
                "blocks": blocks,
                "assets": [
                    asset.model_dump(mode="json", exclude={"content_base64"})
                    for asset in assets
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence = OperationEvidence(
            observed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            content_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
            provider_revision=revision_id,
            retrieval_complete=retrieval_complete,
            warnings=tuple(warnings),
        )
        return DocxReadResult(
            status=(
                OperationStatus.OK
                if retrieval_complete
                else OperationStatus.RETRIEVAL_INCOMPLETE
            ),
            task_ref=task_ref,
            profile_ref=resolved_profile_ref,
            source=source,
            wiki_resolution=wiki_resolution,
            document_id=document_id,
            title=title,
            revision_id=revision_id,
            blocks=blocks,
            block_count=len(blocks),
            page_count=page_count,
            assets=assets,
            asset_count=len(assets),
            asset_total_bytes=sum(
                asset.byte_count or 0
                for asset in assets
                if asset.retrieval_complete
            ),
            evidence=evidence,
        )

    async def aclose(self) -> None:
        await self._lease_client.aclose()
        if self._owns_http_client:
            await self._http.aclose()

    async def _get_document(
        self,
        document_id: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = await self._request(
            DOCUMENT_ENDPOINT.format(document_id=document_id),
            headers=headers,
            operation="Docx read",
        )
        data = _data_object(response, "docx_document")
        document = data.get("document")
        if not isinstance(document, dict):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid Docx document contract.",
            )
        return document

    async def _get_blocks(
        self,
        document_id: str,
        headers: dict[str, str],
    ) -> tuple[list[dict[str, Any]], int]:
        blocks: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for page_count in range(1, MAX_PAGES + 1):
            params: dict[str, str | int] = {"page_size": PAGE_SIZE}
            if page_token:
                params["page_token"] = page_token
            response = await self._request(
                BLOCKS_ENDPOINT.format(document_id=document_id),
                headers=headers,
                params=params,
                operation="Docx read",
            )
            data = _data_object(response, "docx_blocks")
            items = data.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "Feishu returned an invalid Docx block page.",
                )
            blocks.extend(items)
            if not bool(data.get("has_more", False)):
                return blocks, page_count
            next_token = data.get("page_token")
            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                raise CapabilityError(
                    CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
                    "Feishu Docx pagination did not provide a new page token.",
                )
            seen_tokens.add(next_token)
            page_token = next_token
        raise CapabilityError(
            CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
            "Feishu Docx pagination exceeded the safety limit.",
        )

    async def _get_wiki_node(
        self,
        node_token: str,
        headers: dict[str, str],
    ) -> WikiNodeResolution:
        response = await self._request(
            WIKI_NODE_ENDPOINT,
            headers=headers,
            params={"token": node_token},
            operation="Wiki node resolution",
        )
        data = _data_object(response, "wiki_node")
        node = data.get("node")
        if not isinstance(node, dict):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid Wiki node contract.",
            )
        returned_node_token = _required_text(node.get("node_token"), "node_token")
        object_type = _required_text(node.get("obj_type"), "obj_type").lower()
        object_token = _required_text(node.get("obj_token"), "obj_token")
        has_child = node.get("has_child")
        if has_child is not None and not isinstance(has_child, bool):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid Wiki has_child value.",
            )
        return WikiNodeResolution(
            node_token=returned_node_token,
            space_id=_optional_text(node.get("space_id")),
            object_type=object_type,
            object_token=object_token,
            title=_optional_text(node.get("title")),
            node_type=_optional_text(node.get("node_type")),
            has_child=has_child,
        )

    async def _get_binary_assets(
        self,
        blocks: list[dict[str, Any]],
        *,
        task_ref: str,
        profile_ref: str,
    ) -> tuple[list[BinaryAssetSnapshot], list[str]]:
        descriptors, malformed_count = _asset_descriptors(blocks)
        warnings: list[str] = []
        if malformed_count:
            warnings.append(f"malformed_asset_blocks:{malformed_count}")
        if not descriptors:
            return [], warnings

        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=(DOCX_MEDIA_READ_CAPABILITY,),
        )
        headers = {
            "Authorization": f"Bearer {lease.access_token}",
            "Accept": "*/*",
        }
        remaining_total = self._max_total_asset_bytes
        assets: list[BinaryAssetSnapshot] = []
        downloaded_once = False
        for index, descriptor in enumerate(descriptors):
            if index >= self._max_asset_count:
                assets.append(
                    _incomplete_asset(descriptor, "asset_count_limit_exceeded")
                )
                continue
            if remaining_total <= 0:
                assets.append(
                    _incomplete_asset(
                        descriptor,
                        "asset_total_size_limit_exceeded",
                    )
                )
                continue
            if downloaded_once and self._media_download_interval_seconds:
                await asyncio.sleep(self._media_download_interval_seconds)
            allowed_bytes = min(self._max_asset_bytes, remaining_total)
            limit_warning = (
                "asset_total_size_limit_exceeded"
                if remaining_total < self._max_asset_bytes
                else "asset_size_limit_exceeded"
            )
            asset = await self._download_binary_asset(
                descriptor,
                headers=headers,
                allowed_bytes=allowed_bytes,
                limit_warning=limit_warning,
            )
            downloaded_once = True
            assets.append(asset)
            if asset.retrieval_complete and asset.byte_count is not None:
                remaining_total -= asset.byte_count

        warning_counts = Counter(
            asset.warning for asset in assets if asset.warning is not None
        )
        warnings.extend(
            f"{warning}:{count}" for warning, count in sorted(warning_counts.items())
        )
        return assets, warnings

    async def _download_binary_asset(
        self,
        descriptor: _AssetDescriptor,
        *,
        headers: dict[str, str],
        allowed_bytes: int,
        limit_warning: str,
    ) -> BinaryAssetSnapshot:
        request_headers = {
            **headers,
            "Range": f"bytes=0-{allowed_bytes}",
        }
        try:
            async with self._http.stream(
                "GET",
                (
                    f"{self._origin}"
                    f"{MEDIA_DOWNLOAD_ENDPOINT.format(file_token=descriptor.file_token)}"
                ),
                headers=request_headers,
            ) as response:
                if response.status_code not in {200, 206}:
                    await response.aread()
                    raise _http_error(response, "Docx media download")
                declared_size = _declared_asset_size(response)
                media_type = _media_type(response.headers.get("content-type"))
                if declared_size is not None and declared_size > allowed_bytes:
                    return _incomplete_asset(
                        descriptor,
                        limit_warning,
                        media_type=media_type,
                        byte_count=declared_size,
                    )
                chunks: list[bytes] = []
                downloaded_size = 0
                async for chunk in response.aiter_bytes():
                    downloaded_size += len(chunk)
                    if downloaded_size > allowed_bytes:
                        return _incomplete_asset(
                            descriptor,
                            limit_warning,
                            media_type=media_type,
                            byte_count=declared_size,
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                if not _response_contains_complete_asset(response, len(content)):
                    return _incomplete_asset(
                        descriptor,
                        "asset_partial_response",
                        media_type=media_type,
                        byte_count=declared_size,
                    )
        except CapabilityError:
            raise
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                "Feishu could not be reached for Docx media download.",
                retryable=True,
            ) from exc

        return BinaryAssetSnapshot(
            block_id=descriptor.block_id,
            block_type=descriptor.block_type,
            asset_kind=descriptor.asset_kind,
            file_token=descriptor.file_token,
            name=descriptor.name,
            media_type=media_type,
            byte_count=len(content),
            content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            content_base64=base64.b64encode(content).decode("ascii"),
            retrieval_complete=True,
        )

    async def _request(
        self,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int] | None = None,
        operation: str,
    ) -> httpx.Response:
        try:
            response = await self._http.get(
                f"{self._origin}{path}",
                headers=headers,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise _http_error(response, operation)
        return response


def _resolved_docx_locator(object_token: str) -> ResourceLocator:
    if not _RESOLVED_OBJECT_TOKEN.fullmatch(object_token):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "Feishu returned an invalid Docx token for the Wiki node.",
        )
    return ResourceLocator(
        target=TargetKind.FEISHU,
        resource_type=ResourceType.FEISHU_DOCX,
        original=object_token,
        resource_id=object_token,
    )


def _asset_descriptors(
    blocks: list[dict[str, Any]],
) -> tuple[list[_AssetDescriptor], int]:
    descriptors: list[_AssetDescriptor] = []
    malformed_count = 0
    for block in blocks:
        block_type = block.get("block_type")
        if block_type not in {23, 27}:
            continue
        block_id = block.get("block_id")
        asset_kind: Literal["image", "file"] = (
            "file" if block_type == 23 else "image"
        )
        body = block.get(asset_kind)
        file_token = body.get("token") if isinstance(body, dict) else None
        if (
            not isinstance(block_id, str)
            or not _RESOLVED_OBJECT_TOKEN.fullmatch(block_id)
            or not isinstance(file_token, str)
            or not _RESOLVED_OBJECT_TOKEN.fullmatch(file_token)
        ):
            malformed_count += 1
            continue
        name = None
        if asset_kind == "file" and isinstance(body, dict):
            name = _safe_metadata_text(body.get("name"), max_length=1024)
        descriptors.append(
            _AssetDescriptor(
                block_id=block_id,
                block_type=block_type,
                asset_kind=asset_kind,
                file_token=file_token,
                name=name,
            )
        )
    return descriptors, malformed_count


def _incomplete_asset(
    descriptor: _AssetDescriptor,
    warning: str,
    *,
    media_type: str | None = None,
    byte_count: int | None = None,
) -> BinaryAssetSnapshot:
    return BinaryAssetSnapshot(
        block_id=descriptor.block_id,
        block_type=descriptor.block_type,
        asset_kind=descriptor.asset_kind,
        file_token=descriptor.file_token,
        name=descriptor.name,
        media_type=media_type,
        byte_count=byte_count,
        retrieval_complete=False,
        warning=warning,
    )


def _declared_asset_size(response: httpx.Response) -> int | None:
    content_range = response.headers.get("content-range")
    if content_range:
        match = _CONTENT_RANGE.fullmatch(content_range.strip())
        if match is not None and match.group(3) != "*":
            return int(match.group(3))
    if response.status_code == 200:
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit():
            return int(content_length)
    return None


def _response_contains_complete_asset(
    response: httpx.Response,
    downloaded_size: int,
) -> bool:
    if response.status_code == 200:
        declared_size = _declared_asset_size(response)
        return declared_size is None or declared_size == downloaded_size
    content_range = response.headers.get("content-range")
    if not content_range:
        return False
    match = _CONTENT_RANGE.fullmatch(content_range.strip())
    if match is None or match.group(3) == "*":
        return False
    start, end, total = map(int, match.groups())
    return start == 0 and end + 1 == total == downloaded_size


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    return _safe_metadata_text(value.split(";", 1)[0], max_length=255)


def _safe_metadata_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
    ):
        return None
    return normalized
