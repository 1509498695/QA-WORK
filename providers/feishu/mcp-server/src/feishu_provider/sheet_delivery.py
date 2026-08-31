from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from capability_contracts.errors import CapabilityError, CapabilityErrorCode


SHEET_DELIVERY_SCHEMA_VERSION = "workspace-feishu/sheet-delivery/v1"
MAX_DELIVERY_ROWS = 5_000
MAX_DELIVERY_COLUMNS = 100
MAX_DELIVERY_CELLS = 200_000
MAX_TEXT_CHARACTERS = 40_000
MAX_CANONICAL_SPEC_BYTES = 8 * 1024 * 1024
MAX_STRUCTURAL_RANGES = 500
_COLOR = re.compile(r"^#[0-9A-F]{6}$")
_INVALID_SHEET_TITLE = re.compile(r"[/\\?*\[\]:]")


class PlacementMode(StrEnum):
    ADOPT_BLANK_SHEET = "adopt_blank_sheet"
    CREATE_NEW_SHEET = "create_new_sheet"
    CREATE_NEW_WORKBOOK = "create_new_workbook"


class HorizontalAlignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlignment(StrEnum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class BorderType(StrEnum):
    NONE = "none"
    FULL = "full"
    OUTER = "outer"
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class FormulaCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    formula: StrictStr = Field(min_length=2, max_length=MAX_TEXT_CHARACTERS)

    @field_validator("formula")
    @classmethod
    def validate_formula(cls, value: str) -> str:
        if not value.startswith("=") or _has_unsupported_controls(value):
            raise ValueError("formula must start with '=' and contain no control bytes")
        return value


DeliveryCell = FormulaCell | StrictBool | StrictInt | StrictFloat | StrictStr | None


class GridRange(BaseModel):
    """Zero-based, half-open rectangular range inside the delivery rectangle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row_start: int = Field(ge=0)
    row_end: int = Field(gt=0)
    column_start: int = Field(ge=0)
    column_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> GridRange:
        if self.row_end <= self.row_start or self.column_end <= self.column_start:
            raise ValueError("range endpoints must form a non-empty half-open range")
        return self

    @property
    def cell_count(self) -> int:
        return (self.row_end - self.row_start) * (
            self.column_end - self.column_start
        )

    def contains(self, row_index: int, column_index: int) -> bool:
        return (
            self.row_start <= row_index < self.row_end
            and self.column_start <= column_index < self.column_end
        )

    def overlaps(self, other: GridRange) -> bool:
        return not (
            self.row_end <= other.row_start
            or other.row_end <= self.row_start
            or self.column_end <= other.column_start
            or other.column_end <= self.column_start
        )

    def a1(self, sheet_id: str) -> str:
        return (
            f"{sheet_id}!{_column_name(self.column_start + 1)}{self.row_start + 1}:"
            f"{_column_name(self.column_end)}{self.row_end}"
        )


class CellStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bold: bool = False
    italic: bool = False
    font_size_pt: int = Field(default=10, ge=9, le=36)
    underline: bool = False
    strikethrough: bool = False
    text_color: str = "#000000"
    fill_color: str | None = None
    border_type: BorderType = BorderType.NONE
    border_color: str | None = None
    horizontal_alignment: HorizontalAlignment = HorizontalAlignment.LEFT
    vertical_alignment: VerticalAlignment = VerticalAlignment.TOP
    wrap_text: bool = False

    @field_validator("text_color", "fill_color", "border_color")
    @classmethod
    def normalize_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if not _COLOR.fullmatch(normalized):
            raise ValueError("colors must use #RRGGBB")
        return normalized

    @model_validator(mode="after")
    def validate_border(self) -> CellStyle:
        if self.border_type is BorderType.NONE and self.border_color is not None:
            raise ValueError("border_color requires a non-none border_type")
        if self.border_type is not BorderType.NONE and self.border_color is None:
            raise ValueError("a non-none border_type requires border_color")
        return self


class CellStylePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bold: bool | None = None
    italic: bool | None = None
    font_size_pt: int | None = Field(default=None, ge=9, le=36)
    underline: bool | None = None
    strikethrough: bool | None = None
    text_color: str | None = None
    fill_color: str | None = None
    clear_fill: bool = False
    border_type: BorderType | None = None
    border_color: str | None = None
    horizontal_alignment: HorizontalAlignment | None = None
    vertical_alignment: VerticalAlignment | None = None
    wrap_text: bool | None = None

    @field_validator("text_color", "fill_color", "border_color")
    @classmethod
    def normalize_color(cls, value: str | None) -> str | None:
        return CellStyle.normalize_color(value)

    @model_validator(mode="after")
    def validate_patch(self) -> CellStylePatch:
        fields = self.model_dump(exclude_none=True)
        if fields == {"clear_fill": False}:
            raise ValueError("style patch must change at least one field")
        if self.clear_fill and self.fill_color is not None:
            raise ValueError("clear_fill and fill_color are mutually exclusive")
        if self.border_type is BorderType.NONE and self.border_color is not None:
            raise ValueError("border_color cannot accompany border_type=none")
        return self

    def resolve(self, base: CellStyle) -> CellStyle:
        update = self.model_dump(exclude_none=True)
        clear_fill = bool(update.pop("clear_fill", False))
        if clear_fill:
            update["fill_color"] = None
        if update.get("border_type") is BorderType.NONE:
            update["border_color"] = None
        if (
            update.get("border_type") not in {None, BorderType.NONE}
            and "border_color" not in update
        ):
            update["border_color"] = base.border_color
        return CellStyle.model_validate({**base.model_dump(), **update})


class StyleRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    range: GridRange
    style: CellStylePatch


class DimensionSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_index: int = Field(ge=0)
    end_index: int = Field(gt=0)
    pixel_size: int = Field(ge=8, le=1_000)

    @model_validator(mode="after")
    def validate_order(self) -> DimensionSpan:
        if self.end_index <= self.start_index:
            raise ValueError("dimension span must be non-empty and half-open")
        return self

    def overlaps(self, other: DimensionSpan) -> bool:
        return not (
            self.end_index <= other.start_index
            or other.end_index <= self.start_index
        )


class SheetDeliverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SHEET_DELIVERY_SCHEMA_VERSION]
    row_count: int = Field(ge=1, le=MAX_DELIVERY_ROWS)
    column_count: int = Field(ge=1, le=MAX_DELIVERY_COLUMNS)
    values: tuple[tuple[DeliveryCell, ...], ...]
    merges: tuple[GridRange, ...] = Field(
        default=(), max_length=MAX_STRUCTURAL_RANGES
    )
    base_style: CellStyle = Field(default_factory=CellStyle)
    style_ranges: tuple[StyleRange, ...] = Field(
        default=(), max_length=MAX_STRUCTURAL_RANGES
    )
    default_row_height_px: int = Field(default=24, ge=8, le=1_000)
    default_column_width_px: int = Field(default=100, ge=8, le=1_000)
    row_heights: tuple[DimensionSpan, ...] = Field(
        default=(), max_length=MAX_STRUCTURAL_RANGES
    )
    column_widths: tuple[DimensionSpan, ...] = Field(
        default=(), max_length=MAX_STRUCTURAL_RANGES
    )
    frozen_row_count: int = Field(default=0, ge=0)
    frozen_column_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_complete_spec(self) -> SheetDeliverySpec:
        if self.row_count * self.column_count > MAX_DELIVERY_CELLS:
            raise ValueError(f"delivery rectangle exceeds {MAX_DELIVERY_CELLS} cells")
        if len(self.values) != self.row_count or any(
            len(row) != self.column_count for row in self.values
        ):
            raise ValueError("values must exactly cover the declared delivery rectangle")
        if self.frozen_row_count > self.row_count:
            raise ValueError("frozen_row_count exceeds the delivery rectangle")
        if self.frozen_column_count > self.column_count:
            raise ValueError("frozen_column_count exceeds the delivery rectangle")
        for row in self.values:
            for cell in row:
                if isinstance(cell, float) and not math.isfinite(cell):
                    raise ValueError("floating-point cells must be finite")
                if isinstance(cell, str):
                    if len(cell) > MAX_TEXT_CHARACTERS:
                        raise ValueError("text cell exceeds the safe character limit")
                    if cell.startswith("="):
                        raise ValueError(
                            "literal text beginning with '=' is ambiguous; use FormulaCell"
                        )
                    if _has_unsupported_controls(cell):
                        raise ValueError("text cell contains unsupported control bytes")
        self._validate_grid_ranges(self.merges, "merge", require_multiple_cells=True)
        for merge in self.merges:
            for row_index in range(merge.row_start, merge.row_end):
                for column_index in range(merge.column_start, merge.column_end):
                    if (
                        row_index == merge.row_start
                        and column_index == merge.column_start
                    ):
                        continue
                    if self.values[row_index][column_index] not in {None, ""}:
                        raise ValueError(
                            "only the top-left cell of a merge may contain a value"
                        )
        style_grid_ranges = tuple(item.range for item in self.style_ranges)
        self._validate_grid_ranges(style_grid_ranges, "style", require_multiple_cells=False)
        self._validate_dimension_spans(self.row_heights, self.row_count, "row")
        self._validate_dimension_spans(
            self.column_widths, self.column_count, "column"
        )
        if len(self.canonical_bytes()) > MAX_CANONICAL_SPEC_BYTES:
            raise ValueError("canonical delivery spec exceeds the safe byte limit")
        return self

    def _validate_grid_ranges(
        self,
        ranges: tuple[GridRange, ...],
        label: str,
        *,
        require_multiple_cells: bool,
    ) -> None:
        for index, current in enumerate(ranges):
            if current.row_end > self.row_count or current.column_end > self.column_count:
                raise ValueError(f"{label} range escapes the delivery rectangle")
            if require_multiple_cells and current.cell_count < 2:
                raise ValueError("merge range must contain at least two cells")
            if any(current.overlaps(previous) for previous in ranges[:index]):
                raise ValueError(f"{label} ranges must not overlap")

    @staticmethod
    def _validate_dimension_spans(
        spans: tuple[DimensionSpan, ...],
        extent: int,
        label: str,
    ) -> None:
        for index, current in enumerate(spans):
            if current.end_index > extent:
                raise ValueError(f"{label} dimension span escapes the delivery rectangle")
            if any(current.overlaps(previous) for previous in spans[:index]):
                raise ValueError(f"{label} dimension spans must not overlap")

    @property
    def delivery_range(self) -> GridRange:
        return GridRange(
            row_start=0,
            row_end=self.row_count,
            column_start=0,
            column_end=self.column_count,
        )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def remote_values(self) -> list[list[Any]]:
        return [
            [
                cell.formula
                if isinstance(cell, FormulaCell)
                else ""
                if cell is None
                else cell
                for cell in row
            ]
            for row in self.values
        ]

    def typed_remote_cells(self) -> list[list[dict[str, Any]]]:
        """Serialize cells for Feishu's typed ``set_cell_range`` tool."""
        return [
            [
                {"formula": cell.formula}
                if isinstance(cell, FormulaCell)
                else {}
                if cell is None
                else {"value": cell}
                for cell in row
            ]
            for row in self.values
        ]

    @property
    def has_boolean_values(self) -> bool:
        return any(
            isinstance(cell, bool) for row in self.values for cell in row
        )

    def resolved_style_ranges(self) -> tuple[tuple[GridRange, CellStyle], ...]:
        return tuple(
            (item.range, item.style.resolve(self.base_style))
            for item in self.style_ranges
        )

    def style_at(self, row_index: int, column_index: int) -> CellStyle:
        for range_, style in reversed(self.resolved_style_ranges()):
            if range_.contains(row_index, column_index):
                return style
        return self.base_style

    def validate_current_provider_support(self) -> None:
        resolved_styles = (
            self.base_style,
            *(style for _, style in self.resolved_style_ranges()),
        )
        unsupported: list[str] = []
        if any(style.wrap_text for style in resolved_styles):
            unsupported.append("wrap_text=true")
        unsupported_borders = sorted(
            {
                style.border_type.value
                for style in resolved_styles
                if style.border_type not in {BorderType.NONE, BorderType.FULL}
            }
        )
        unsupported.extend(
            f"border_type={border_type}" for border_type in unsupported_borders
        )
        if unsupported:
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
                "The current Feishu public style API contract cannot safely set and verify part of this style specification.",
                details={"unsupported_fields": unsupported},
            )

    def summary(self) -> dict[str, int | str]:
        formulas = sum(
            isinstance(cell, FormulaCell) for row in self.values for cell in row
        )
        nonempty = sum(
            cell not in {None, ""}
            for row in self.values
            for cell in row
            if not isinstance(cell, FormulaCell)
        ) + formulas
        return {
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "rows": self.row_count,
            "columns": self.column_count,
            "cells": self.row_count * self.column_count,
            "nonempty_cells": nonempty,
            "formula_cells": formulas,
            "merge_ranges": len(self.merges),
            "style_ranges": len(self.style_ranges),
            "row_height_overrides": len(self.row_heights),
            "column_width_overrides": len(self.column_widths),
        }


def validate_sheet_title(value: str) -> str:
    if not isinstance(value, str):
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
            "A new worksheet title must be text.",
        )
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > 100
        or _INVALID_SHEET_TITLE.search(normalized)
        or any(ord(character) < 32 for character in normalized)
    ):
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
            "The new worksheet title is blank, padded, too long, or contains a forbidden character.",
        )
    return normalized


def validate_workbook_title(value: str) -> str:
    if not isinstance(value, str):
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
            "A new workbook title must be text.",
        )
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > 100
        or _INVALID_SHEET_TITLE.search(normalized)
        or any(ord(character) < 32 for character in normalized)
    ):
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
            "The new workbook title is blank, padded, too long, or contains a forbidden character.",
        )
    return normalized


def _column_name(column_number: int) -> str:
    if column_number < 1:
        raise ValueError("column_number must be positive")
    result = ""
    current = column_number
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _has_unsupported_controls(value: str) -> bool:
    return any(
        ord(character) < 32 and ord(character) not in {9, 10, 13}
        for character in value
    )
