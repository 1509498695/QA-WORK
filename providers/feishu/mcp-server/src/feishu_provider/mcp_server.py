from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import RootModel

from capability_contracts.errors import (
    CapabilityError,
    CapabilityErrorCode,
    CapabilityFailure,
)
from capability_contracts.models import CapabilityManifest
from feishu_provider.docx import DocxReadResult, DocxReader, FeishuDocxClient
from feishu_provider.lease_client import DEFAULT_AUTHORIZATION_URL
from feishu_provider.locator import (
    ResourceLocator,
    ResourceType,
    classify_locator,
)
from feishu_provider.sheets import FeishuSheetsClient, SheetsReader, SheetsReadResult


PROVIDER_VERSION = "0.4.1"
CONTRACT_VERSION = "workspace-capabilities/v1alpha1"


class DocxToolResult(RootModel[DocxReadResult | CapabilityFailure]):
    """Top-level Docx success/failure union without FastMCP's scalar wrapper."""


class SheetsToolResult(RootModel[SheetsReadResult | CapabilityFailure]):
    """Top-level Sheets success/failure union without FastMCP's scalar wrapper."""


def build_server(
    docx_reader: DocxReader | None = None,
    sheets_reader: SheetsReader | None = None,
) -> FastMCP:
    server = FastMCP(
        "workspace-feishu-provider",
        instructions=(
            "Independent Feishu Provider. Read tools return structured evidence and never "
            "reuse lg-feishu credentials, profiles, tools, or runtime."
        ),
        log_level="WARNING",
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    @server.tool(
        name="feishu_provider_manifest",
        title="Feishu Provider capability manifest",
        annotations=read_only,
    )
    def provider_manifest() -> CapabilityManifest:
        """Report the public contracts implemented by this Provider build."""
        return CapabilityManifest(
            provider_id="feishu",
            provider_version=PROVIDER_VERSION,
            contract_versions=(CONTRACT_VERSION,),
            operations=(
                "feishu_provider_manifest",
                "feishu_resource_resolve",
                "feishu_docx_read",
                "feishu_sheets_read",
            ),
            resource_types=(
                ResourceType.FEISHU_DOCX,
                ResourceType.FEISHU_WIKI,
                ResourceType.FEISHU_SHEET,
            ),
            development_only=True,
            notes=(
                "local-dev-v0",
                "user access tokens are delivered by task-scoped loopback leases",
                "Wiki nodes are resolved online and only Docx targets are read",
                "Docx image and file blocks are returned as bounded in-memory assets",
                "Sheets return bounded worksheet metadata, merges, formulas, and values",
                "Sheets styles, charts, comments, and embedded media are outside v0.4",
            ),
        )

    @server.tool(
        name="feishu_resource_resolve",
        title="Resolve a local or Feishu resource locator",
        annotations=read_only,
    )
    def resource_resolve(locator: str) -> ResourceLocator:
        """Classify a Windows path or supported Feishu resource URL without reading it."""
        return classify_locator(locator)

    @server.tool(
        name="feishu_docx_read",
        title="Read a Feishu Docx snapshot from Docx or Wiki",
        annotations=read_only,
    )
    async def docx_read(
        locator: str,
        task_ref: str,
        profile_ref: str | None = None,
    ) -> DocxToolResult:
        """Resolve an optional Wiki node and read every Docx block page."""
        try:
            if docx_reader is not None:
                return DocxToolResult(
                    await docx_reader.read(
                        locator=locator,
                        task_ref=task_ref,
                        profile_ref=profile_ref,
                    )
                )
            reader = FeishuDocxClient.default()
            try:
                return DocxToolResult(
                    await reader.read(
                        locator=locator,
                        task_ref=task_ref,
                        profile_ref=profile_ref,
                    )
                )
            finally:
                await reader.aclose()
        except CapabilityError as exc:
            return DocxToolResult(_public_failure(exc))

    @server.tool(
        name="feishu_sheets_read",
        title="Read a Feishu Sheets snapshot from Sheets or Wiki",
        annotations=read_only,
    )
    async def sheets_read(
        locator: str,
        task_ref: str,
        profile_ref: str | None = None,
    ) -> SheetsToolResult:
        """Resolve an optional Wiki node and read bounded worksheet values."""
        try:
            if sheets_reader is not None:
                return SheetsToolResult(
                    await sheets_reader.read(
                        locator=locator,
                        task_ref=task_ref,
                        profile_ref=profile_ref,
                    )
                )
            reader = FeishuSheetsClient.default()
            try:
                return SheetsToolResult(
                    await reader.read(
                        locator=locator,
                        task_ref=task_ref,
                        profile_ref=profile_ref,
                    )
                )
            finally:
                await reader.aclose()
        except CapabilityError as exc:
            return SheetsToolResult(_public_failure(exc))

    return server


def _public_failure(error: CapabilityError) -> CapabilityFailure:
    extra_details = None
    if (
        error.code is CapabilityErrorCode.AUTH_REQUIRED
        and "authorization_url" not in error.details
    ):
        extra_details = {"authorization_url": DEFAULT_AUTHORIZATION_URL}
    return error.to_failure(extra_details=extra_details)


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
