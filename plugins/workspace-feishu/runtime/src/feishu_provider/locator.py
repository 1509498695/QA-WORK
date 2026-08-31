from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict

from capability_contracts.errors import CapabilityError, CapabilityErrorCode


class TargetKind(StrEnum):
    LOCAL = "local"
    FEISHU = "feishu"
    UNKNOWN = "unknown"


class ResourceType(StrEnum):
    LOCAL_FILE = "local_file"
    FEISHU_DOCX = "feishu_docx"
    FEISHU_WIKI = "feishu_wiki"
    FEISHU_SHEET = "feishu_sheet"
    UNKNOWN = "unknown"


class ResourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: TargetKind
    resource_type: ResourceType
    original: str
    resource_id: str | None = None
    worksheet_id: str | None = None
    canonical_url: str | None = None


_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_DOCX_TOKEN = re.compile(r"^(?:doxcn|doccn)[A-Za-z0-9_-]{6,}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{6,256}$")
_FEISHU_HOST_SUFFIXES = (".feishu.cn", ".larksuite.com")
_RESOURCE_SEGMENTS = {
    "docx": ResourceType.FEISHU_DOCX,
    "wiki": ResourceType.FEISHU_WIKI,
    "sheets": ResourceType.FEISHU_SHEET,
}


def classify_locator(value: str) -> ResourceLocator:
    normalized = _validate_text(value)
    if _WINDOWS_PATH.match(normalized):
        return ResourceLocator(
            target=TargetKind.LOCAL,
            resource_type=ResourceType.LOCAL_FILE,
            original=normalized,
            resource_id=normalized,
        )
    if _DOCX_TOKEN.fullmatch(normalized):
        return ResourceLocator(
            target=TargetKind.FEISHU,
            resource_type=ResourceType.FEISHU_DOCX,
            original=normalized,
            resource_id=normalized,
        )

    parsed = urlsplit(normalized)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        hostname = parsed.hostname.lower()
        if _is_feishu_host(hostname):
            segments = [segment for segment in parsed.path.split("/") if segment]
            for index, segment in enumerate(segments[:-1]):
                resource_type = _RESOURCE_SEGMENTS.get(segment.lower())
                if resource_type is None:
                    continue
                resource_id = segments[index + 1]
                if not _TOKEN.fullmatch(resource_id):
                    break
                worksheet_id = None
                if resource_type in {
                    ResourceType.FEISHU_SHEET,
                    ResourceType.FEISHU_WIKI,
                }:
                    worksheet_id = _worksheet_selector(parsed.query)
                canonical_url = f"https://{hostname}/{segment.lower()}/{resource_id}"
                if worksheet_id is not None:
                    canonical_url += "?" + urlencode({"sheet": worksheet_id})
                return ResourceLocator(
                    target=TargetKind.FEISHU,
                    resource_type=resource_type,
                    original=normalized,
                    resource_id=resource_id,
                    worksheet_id=worksheet_id,
                    canonical_url=canonical_url,
                )
            raise CapabilityError(
                CapabilityErrorCode.INVALID_LOCATOR,
                "The Feishu URL does not contain a supported resource identifier.",
            )
        return ResourceLocator(
            target=TargetKind.UNKNOWN,
            resource_type=ResourceType.UNKNOWN,
            original=normalized,
        )

    return ResourceLocator(
        target=TargetKind.UNKNOWN,
        resource_type=ResourceType.UNKNOWN,
        original=normalized,
    )


def resolve_feishu_docx(value: str) -> ResourceLocator:
    locator = classify_locator(value)
    if locator.target is not TargetKind.FEISHU:
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "A Feishu Docx URL or document token is required.",
        )
    if locator.resource_type is not ResourceType.FEISHU_DOCX:
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_RESOURCE,
            "This first Provider slice only supports Feishu Docx resources.",
            details={"resource_type": locator.resource_type.value},
        )
    return locator


def _is_feishu_host(hostname: str) -> bool:
    return hostname in {"feishu.cn", "larksuite.com"} or hostname.endswith(
        _FEISHU_HOST_SUFFIXES
    )


def _worksheet_selector(query: str) -> str | None:
    try:
        parameters = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=32,
        )
    except ValueError as exc:
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "The Feishu URL query is invalid or too large.",
        ) from exc
    selectors = parameters.get("sheet", [])
    if not selectors:
        return None
    if len(selectors) != 1 or not _TOKEN.fullmatch(selectors[0]):
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "The Feishu URL must contain one valid sheet selector.",
        )
    return selectors[0]


def _validate_text(value: object) -> str:
    if not isinstance(value, str):
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "Resource locator must be text.",
        )
    normalized = value.strip()
    if not normalized or len(normalized) > 2048:
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "Resource locator is blank or too long.",
        )
    if any(ord(character) < 32 for character in normalized):
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "Resource locator contains unsupported characters.",
        )
    return normalized
