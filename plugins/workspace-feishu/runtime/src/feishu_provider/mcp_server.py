from __future__ import annotations

from capability_contracts.errors import (
    CapabilityError,
    CapabilityErrorCode,
    CapabilityFailure,
)
from capability_contracts.models import CapabilityManifest
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, RootModel

from feishu_provider.docx import DocxReader, DocxReadResult, FeishuDocxClient
from feishu_provider.lease_client import DEFAULT_AUTHORIZATION_URL
from feishu_provider.locator import (
    ResourceLocator,
    ResourceType,
    classify_locator,
)
from feishu_provider.managed_sheets import (
    ConfirmationAction,
    ManagedSheetApplyResult,
    ManagedSheetPreviewResult,
    ManagedSheetWriteService,
)
from feishu_provider.operation_store import OperationStoreError
from feishu_provider.sheet_delivery import PlacementMode, SheetDeliverySpec
from feishu_provider.sheet_revision import (
    ManagedSheetRegistrationResolveResult,
    ManagedSheetRevisionResult,
    ManagedSheetRevisionService,
)
from feishu_provider.sheets import FeishuSheetsClient, SheetsReader, SheetsReadResult

PROVIDER_VERSION = "0.7.0"
CONTRACT_VERSION = "workspace-capabilities/v1alpha1"


class DocxToolResult(RootModel[DocxReadResult | CapabilityFailure]):
    """Top-level Docx success/failure union without FastMCP's scalar wrapper."""


class SheetsToolResult(RootModel[SheetsReadResult | CapabilityFailure]):
    """Top-level Sheets success/failure union without FastMCP's scalar wrapper."""


class ManagedSheetPreviewToolResult(
    RootModel[ManagedSheetPreviewResult | CapabilityFailure]
):
    """Top-level managed-sheet preview success/failure union."""


class ManagedSheetApplyToolResult(
    RootModel[ManagedSheetApplyResult | CapabilityFailure]
):
    """Top-level managed-sheet apply success/failure union."""


class ManagedSheetRegistrationResolveToolResult(
    RootModel[ManagedSheetRegistrationResolveResult | CapabilityFailure]
):
    """Top-level managed-sheet registration resolution union."""


class ManagedSheetRevisionToolResult(
    RootModel[ManagedSheetRevisionResult | CapabilityFailure]
):
    """Top-level managed-sheet revision success/failure union."""


class _McpWriteAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")


def build_server(
    docx_reader: DocxReader | None = None,
    sheets_reader: SheetsReader | None = None,
    managed_sheet_writer: ManagedSheetWriteService | None = None,
    managed_sheet_reviser: ManagedSheetRevisionService | None = None,
) -> FastMCP:
    server = FastMCP(
        "workspace-feishu-provider",
        instructions=(
            "Independent Feishu Provider. Read tools return structured evidence. Managed "
            "worksheet writes require exact registration, MCP elicitation, persistent "
            "checkpoints, and API plus XLSX readback. This runtime never reuses lg-feishu credentials, "
            "profiles, tools, or runtime."
        ),
        log_level="WARNING",
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    preview_write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
    managed_write = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
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
                "feishu_managed_sheet_preview",
                "feishu_managed_sheet_apply",
                "feishu_managed_sheet_registration_resolve",
                "feishu_managed_sheet_revise",
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
                "managed writes target exactly one worksheet in a user-owned workbook",
                "create_new_workbook creates one Sheet file beneath an exact Wiki parent node",
                "write delivery uses a versioned structured specification, not raw API payloads",
                "blank adoption remains a non-atomic local-development capability",
                "delivery is complete only after API and ephemeral XLSX verification",
                "managed revisions require caller-held base and next v1 specifications",
                "no-change revisions double-verify without elicitation or remote mutation",
                "revision shrinkage clears retired content and styles without deleting axes",
                "Sheets embedded cell images are returned as bounded in-memory assets",
                "charts, comments, floating images, and wrapped text are outside v0.7",
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

    @server.tool(
        name="feishu_managed_sheet_registration_resolve",
        title="Resolve one managed worksheet registration",
        annotations=read_only,
    )
    async def managed_sheet_registration_resolve(
        locator: str,
        task_ref: str,
        profile_ref: str | None = None,
    ) -> ManagedSheetRegistrationResolveToolResult:
        """Map an exact Sheet/Wiki locator to one local managed registration."""
        reviser = managed_sheet_reviser or ManagedSheetRevisionService.default()
        try:
            return ManagedSheetRegistrationResolveToolResult(
                await reviser.resolve_registration(
                    locator=locator,
                    task_ref=task_ref,
                    profile_ref=profile_ref,
                )
            )
        except CapabilityError as exc:
            return ManagedSheetRegistrationResolveToolResult(_public_failure(exc))
        except OperationStoreError:
            return ManagedSheetRegistrationResolveToolResult(
                _operation_store_failure()
            )
        finally:
            if managed_sheet_reviser is None:
                await reviser.aclose()

    @server.tool(
        name="feishu_managed_sheet_preview",
        title="Preview one managed worksheet delivery",
        annotations=preview_write,
    )
    async def managed_sheet_preview(
        locator: str,
        task_ref: str,
        placement_mode: PlacementMode,
        spec: SheetDeliverySpec,
        profile_ref: str | None = None,
        requested_sheet_title: str | None = None,
        requested_workbook_title: str | None = None,
    ) -> ManagedSheetPreviewToolResult:
        """Validate the target and persist a ten-minute, zero-remote-write preview."""
        writer = managed_sheet_writer or ManagedSheetWriteService.default()
        try:
            return ManagedSheetPreviewToolResult(
                await writer.preview(
                    locator=locator,
                    task_ref=task_ref,
                    profile_ref=profile_ref,
                    placement_mode=placement_mode,
                    requested_sheet_title=requested_sheet_title,
                    spec=spec,
                    requested_workbook_title=requested_workbook_title,
                )
            )
        except CapabilityError as exc:
            return ManagedSheetPreviewToolResult(_public_failure(exc))
        except OperationStoreError:
            return ManagedSheetPreviewToolResult(_operation_store_failure())
        finally:
            if managed_sheet_writer is None:
                await writer.aclose()

    @server.tool(
        name="feishu_managed_sheet_apply",
        title="Confirm and apply one managed worksheet delivery",
        annotations=managed_write,
    )
    async def managed_sheet_apply(
        operation_ref: str,
        task_ref: str,
        spec: SheetDeliverySpec,
        ctx: Context,
    ) -> ManagedSheetApplyToolResult:
        """Apply an unchanged preview only after this MCP request is accepted."""
        writer = managed_sheet_writer or ManagedSheetWriteService.default()

        async def confirm(request):  # type: ignore[no-untyped-def]
            try:
                response = await ctx.elicit(
                    request.message(),
                    _McpWriteAcknowledgement,
                )
            except Exception as exc:
                raise CapabilityError(
                    CapabilityErrorCode.CONFIRMATION_REQUIRED,
                    "This MCP client did not complete the required write confirmation; no remote write was attempted.",
                ) from exc
            if response.action == "decline":
                return ConfirmationAction.DECLINE
            if response.action == "cancel":
                return ConfirmationAction.CANCEL
            return ConfirmationAction.ACCEPT

        try:
            return ManagedSheetApplyToolResult(
                await writer.apply(
                    operation_ref=operation_ref,
                    task_ref=task_ref,
                    spec=spec,
                    confirmer=confirm,
                )
            )
        except CapabilityError as exc:
            return ManagedSheetApplyToolResult(_public_failure(exc))
        except OperationStoreError:
            return ManagedSheetApplyToolResult(_operation_store_failure())
        finally:
            if managed_sheet_writer is None:
                await writer.aclose()

    @server.tool(
        name="feishu_managed_sheet_revise",
        title="Verify, confirm, and revise one managed worksheet",
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def managed_sheet_revise(
        registration_ref: str,
        task_ref: str,
        next_spec: SheetDeliverySpec,
        ctx: Context,
        base_spec: SheetDeliverySpec | None = None,
    ) -> ManagedSheetRevisionToolResult:
        """Double-verify a managed baseline and apply one immutable revision."""
        reviser = managed_sheet_reviser or ManagedSheetRevisionService.default()

        async def confirm(request):  # type: ignore[no-untyped-def]
            try:
                response = await ctx.elicit(
                    request.message(),
                    _McpWriteAcknowledgement,
                )
            except Exception as exc:
                raise CapabilityError(
                    CapabilityErrorCode.CONFIRMATION_REQUIRED,
                    "This MCP client did not complete the required revision confirmation; no new remote write was attempted.",
                ) from exc
            if response.action == "decline":
                return ConfirmationAction.DECLINE
            if response.action == "cancel":
                return ConfirmationAction.CANCEL
            return ConfirmationAction.ACCEPT

        try:
            return ManagedSheetRevisionToolResult(
                await reviser.revise(
                    registration_ref=registration_ref,
                    task_ref=task_ref,
                    base_spec=base_spec,
                    next_spec=next_spec,
                    confirmer=confirm,
                )
            )
        except CapabilityError as exc:
            return ManagedSheetRevisionToolResult(_public_failure(exc))
        except OperationStoreError:
            return ManagedSheetRevisionToolResult(_operation_store_failure())
        finally:
            if managed_sheet_reviser is None:
                await reviser.aclose()

    return server


def _public_failure(error: CapabilityError) -> CapabilityFailure:
    extra_details = None
    if (
        error.code is CapabilityErrorCode.AUTH_REQUIRED
        and "authorization_url" not in error.details
    ):
        extra_details = {"authorization_url": DEFAULT_AUTHORIZATION_URL}
    return error.to_failure(extra_details=extra_details)


def _operation_store_failure() -> CapabilityFailure:
    return CapabilityFailure(
        status=CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
        message="The local Feishu write state store is unavailable or cannot be trusted.",
        retryable=False,
    )


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
