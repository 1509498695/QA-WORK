from __future__ import annotations

import asyncio
import base64
import hashlib

import httpx
import pytest

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.models import OperationStatus, ResourceType
from feishu_auth_service.leases import (
    DOCX_MEDIA_READ_CAPABILITY,
    DOCX_READ_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_provider.docx import FeishuDocxClient
from feishu_provider.lease_client import ProviderTokenLease


DOCX_TOKEN = "doxcn1234567890"
WIKI_TOKEN = "KhbDwPjf9iovDnkD3yscx9M8nAb"
WIKI_DOCX_TOKEN = "Jd92mR7QxA4kLp8VtN6cB3wZ"
PROFILE_REF = "profile_0123456789abcdef0123"


class FakeLeaseClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.closed = False

    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> ProviderTokenLease:
        self.calls.append((task_ref, profile_ref, capabilities))
        return ProviderTokenLease(
            lease_ref="lease_test",
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
            scopes=("docx:document:readonly",),
            access_token="access-token-must-never-render",
            issued_at="2026-08-24T00:00:00+00:00",
            expires_at="2026-08-24T00:10:00+00:00",
            token_expires_at="2026-08-24T01:00:00+00:00",
        )

    async def aclose(self) -> None:
        self.closed = True


def test_docx_read_paginates_and_marks_external_content_incomplete() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == (
            "Bearer access-token-must-never-render"
        )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "document": {
                            "document_id": DOCX_TOKEN,
                            "revision_id": "42",
                            "title": "测试文档",
                        }
                    },
                },
            )
        if request.url.params.get("page_token") is None:
            assert request.url.params["page_size"] == "500"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"block_id": "root", "block_type": 1}],
                        "has_more": True,
                        "page_token": "next-page",
                    },
                },
            )
        assert request.url.params["page_token"] == "next-page"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [{"block_id": "canvas-block", "block_type": 30}],
                    "has_more": False,
                },
            },
        )

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=lease_client, http_client=http)
    result = asyncio.run(
        client.read(
            locator=f"https://example.feishu.cn/docx/{DOCX_TOKEN}",
            task_ref="task-one",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.RETRIEVAL_INCOMPLETE
    assert result.page_count == 2
    assert result.block_count == 2
    assert result.title == "测试文档"
    assert result.revision_id == "42"
    assert result.wiki_resolution is None
    assert result.evidence.retrieval_complete is False
    assert result.evidence.warnings == ("unresolved_content_block_types:30",)
    assert result.evidence.content_hash.startswith("sha256:")
    assert lease_client.calls == [
        ("task-one", PROFILE_REF, ("feishu.docx.read",))
    ]
    assert lease_client.closed is True
    assert len(requests) == 3


def test_docx_read_fails_closed_on_repeated_page_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"document": {"title": "测试文档"}},
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [],
                    "has_more": True,
                    "page_token": "same-page",
                },
            },
        )

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=lease_client, http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=DOCX_TOKEN,
                task_ref="task-one",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.RETRIEVAL_INCOMPLETE


def test_docx_read_maps_permission_error_without_returning_provider_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                403,
                json={"code": 1770032, "msg": "sensitive provider message"},
            )
        raise AssertionError("blocks must not be requested after document rejection")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=DOCX_TOKEN,
                task_ref="task-one",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.PERMISSION_DENIED
    assert error.value.details == {"platform_code": 1770032}
    assert "sensitive provider message" not in str(error.value)


def test_wiki_docx_read_resolves_node_and_reuses_one_user_lease() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == (
            "Bearer access-token-must-never-render"
        )
        if request.url.path.endswith("/wiki/v2/spaces/get_node"):
            assert request.url.params["token"] == WIKI_TOKEN
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "node": {
                            "space_id": "space-one",
                            "node_token": WIKI_TOKEN,
                            "obj_token": WIKI_DOCX_TOKEN,
                            "obj_type": "docx",
                            "title": "知识库标题",
                            "node_type": "origin",
                            "has_child": False,
                        }
                    },
                },
            )
        if request.url.path.endswith(f"/documents/{WIKI_DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "document": {
                            "document_id": WIKI_DOCX_TOKEN,
                            "revision_id": "7",
                            "title": "Docx 标题",
                        }
                    },
                },
            )
        if request.url.path.endswith(f"/documents/{WIKI_DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"block_id": "root", "block_type": 1}],
                        "has_more": False,
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=lease_client, http_client=http)
    result = asyncio.run(
        client.read(
            locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
            task_ref="task-wiki",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.OK
    assert result.source.resource_type is ResourceType.FEISHU_WIKI
    assert result.document_id == WIKI_DOCX_TOKEN
    assert result.title == "Docx 标题"
    assert result.revision_id == "7"
    assert result.wiki_resolution is not None
    assert result.wiki_resolution.node_token == WIKI_TOKEN
    assert result.wiki_resolution.object_type == "docx"
    assert result.wiki_resolution.object_token == WIKI_DOCX_TOKEN
    assert result.evidence.retrieval_complete is True
    assert lease_client.calls == [
        (
            "task-wiki",
            PROFILE_REF,
            (DOCX_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY),
        )
    ]
    assert len(requests) == 3


def test_wiki_read_rejects_non_docx_target_without_reading_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": WIKI_TOKEN,
                        "obj_token": "shtcn1234567890",
                        "obj_type": "sheet",
                    }
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
                task_ref="task-wiki",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.UNSUPPORTED_RESOURCE
    assert error.value.details == {"wiki_object_type": "sheet"}
    assert len(requests) == 1


def test_wiki_permission_error_is_mapped_from_platform_code_and_redacted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 131006, "msg": "sensitive wiki permission details"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
                task_ref="task-wiki",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.PERMISSION_DENIED
    assert error.value.details == {"platform_code": 131006}
    assert "sensitive wiki permission details" not in str(error.value)


def test_wiki_read_rejects_unsafe_resolved_docx_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": WIKI_TOKEN,
                        "obj_token": "../unsafe-token",
                        "obj_type": "docx",
                    }
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
                task_ref="task-wiki",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.PROVIDER_CONTRACT_ERROR
    assert len(requests) == 1


def test_docx_read_downloads_image_and_file_as_bounded_in_memory_assets() -> None:
    image_token = "imgtoken123456"
    file_token = "filetoken123456"
    image_content = b"image-bytes"
    file_content = b"attachment-bytes"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == (
            "Bearer access-token-must-never-render"
        )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "document": {
                            "document_id": DOCX_TOKEN,
                            "revision_id": "media-1",
                            "title": "媒体文档",
                        }
                    },
                },
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"block_id": "root_block", "block_type": 1},
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": image_token},
                            },
                            {
                                "block_id": "file_block_01",
                                "block_type": 23,
                                "file": {
                                    "token": file_token,
                                    "name": "attachment.txt",
                                },
                            },
                        ],
                        "has_more": False,
                    },
                },
            )
        assert request.headers["range"] == "bytes=0-64"
        if request.url.path.endswith(f"/medias/{image_token}/download"):
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=image_content,
            )
        if request.url.path.endswith(f"/medias/{file_token}/download"):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                content=file_content,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(
        lease_client=lease_client,
        http_client=http,
        max_asset_bytes=64,
        max_total_asset_bytes=128,
        media_download_interval_seconds=0,
    )
    result = asyncio.run(
        client.read(
            locator=DOCX_TOKEN,
            task_ref="task-media",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.OK
    assert result.asset_count == 2
    assert result.asset_total_bytes == len(image_content) + len(file_content)
    assert result.evidence.retrieval_complete is True
    assert result.evidence.warnings == ()
    image, attachment = result.assets
    assert image.asset_kind == "image"
    assert image.media_type == "image/png"
    assert image.byte_count == len(image_content)
    assert base64.b64decode(image.content_base64 or "") == image_content
    assert image.content_hash == "sha256:" + hashlib.sha256(image_content).hexdigest()
    assert "aW1hZ2UtYnl0ZXM=" not in repr(image)
    assert attachment.asset_kind == "file"
    assert attachment.name == "attachment.txt"
    assert attachment.media_type == "text/plain"
    assert base64.b64decode(attachment.content_base64 or "") == file_content
    assert lease_client.calls == [
        ("task-media", PROFILE_REF, (DOCX_READ_CAPABILITY,)),
        ("task-media", PROFILE_REF, (DOCX_MEDIA_READ_CAPABILITY,)),
    ]
    assert len(requests) == 4


def test_docx_read_rejects_oversized_partial_asset_without_returning_base64() -> None:
    image_token = "imgtoken123456"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "媒体文档"}}},
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": image_token},
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        assert request.url.path.endswith(f"/medias/{image_token}/download")
        assert request.headers["range"] == "bytes=0-4"
        return httpx.Response(
            206,
            headers={
                "Content-Type": "image/png",
                "Content-Range": "bytes 0-4/10",
            },
            content=b"12345",
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(
        lease_client=FakeLeaseClient(),
        http_client=http,
        max_asset_bytes=4,
        max_total_asset_bytes=32,
        media_download_interval_seconds=0,
    )
    result = asyncio.run(
        client.read(
            locator=DOCX_TOKEN,
            task_ref="task-media",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.RETRIEVAL_INCOMPLETE
    assert result.asset_count == 1
    assert result.asset_total_bytes == 0
    assert result.evidence.warnings == ("asset_size_limit_exceeded:1",)
    asset = result.assets[0]
    assert asset.retrieval_complete is False
    assert asset.warning == "asset_size_limit_exceeded"
    assert asset.byte_count == 10
    assert asset.content_base64 is None
    assert asset.content_hash is None


def test_docx_read_accepts_full_ranged_asset_response() -> None:
    image_token = "imgtoken123456"
    content = b"abc"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "媒体文档"}}},
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": image_token},
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        return httpx.Response(
            206,
            headers={
                "Content-Type": "image/png",
                "Content-Range": "bytes 0-2/3",
            },
            content=content,
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(
        lease_client=FakeLeaseClient(),
        http_client=http,
        max_asset_bytes=3,
        max_total_asset_bytes=3,
        media_download_interval_seconds=0,
    )
    result = asyncio.run(
        client.read(
            locator=DOCX_TOKEN,
            task_ref="task-media",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.OK
    assert result.asset_total_bytes == 3
    assert base64.b64decode(result.assets[0].content_base64 or "") == content


def test_docx_read_marks_malformed_asset_block_without_requesting_media() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "媒体文档"}}},
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": "../unsafe-token"},
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        raise AssertionError("media must not be requested for a malformed asset block")

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(lease_client=lease_client, http_client=http)
    result = asyncio.run(
        client.read(
            locator=DOCX_TOKEN,
            task_ref="task-media",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.RETRIEVAL_INCOMPLETE
    assert result.asset_count == 0
    assert result.evidence.warnings == ("malformed_asset_blocks:1",)
    assert lease_client.calls == [
        ("task-media", PROFILE_REF, (DOCX_READ_CAPABILITY,))
    ]
    assert len(requests) == 2


def test_docx_media_permission_error_is_redacted() -> None:
    image_token = "imgtoken123456"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "媒体文档"}}},
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": image_token},
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        return httpx.Response(
            403,
            json={"code": 1061003, "msg": "sensitive media permission details"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(
        lease_client=FakeLeaseClient(),
        http_client=http,
        media_download_interval_seconds=0,
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=DOCX_TOKEN,
                task_ref="task-media",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.PERMISSION_DENIED
    assert error.value.details == {"platform_code": 1061003}
    assert "sensitive media permission details" not in str(error.value)


def test_docx_read_caps_asset_count_without_downloading_excess_blocks() -> None:
    first_token = "imgtoken123456"
    second_token = "imgtoken654321"
    media_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"document": {"title": "媒体文档"}}},
            )
        if request.url.path.endswith(f"/documents/{DOCX_TOKEN}/blocks"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "image_block_01",
                                "block_type": 27,
                                "image": {"token": first_token},
                            },
                            {
                                "block_id": "image_block_02",
                                "block_type": 27,
                                "image": {"token": second_token},
                            },
                        ],
                        "has_more": False,
                    },
                },
            )
        media_requests.append(request.url.path)
        assert request.url.path.endswith(f"/medias/{first_token}/download")
        return httpx.Response(200, content=b"one")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuDocxClient(
        lease_client=FakeLeaseClient(),
        http_client=http,
        max_asset_count=1,
        media_download_interval_seconds=0,
    )
    result = asyncio.run(
        client.read(
            locator=DOCX_TOKEN,
            task_ref="task-media",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.RETRIEVAL_INCOMPLETE
    assert result.asset_count == 2
    assert result.asset_total_bytes == 3
    assert result.assets[0].retrieval_complete is True
    assert result.assets[1].warning == "asset_count_limit_exceeded"
    assert result.evidence.warnings == ("asset_count_limit_exceeded:1",)
    assert len(media_requests) == 1
