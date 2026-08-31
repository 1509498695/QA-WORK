from __future__ import annotations

import pytest
from pydantic import ValidationError

from capability_contracts import CapabilityError, CapabilityErrorCode
from feishu_provider.sheet_delivery import (
    SHEET_DELIVERY_SCHEMA_VERSION,
    FormulaCell,
    SheetDeliverySpec,
    validate_sheet_title,
)


def _spec(**overrides: object) -> SheetDeliverySpec:
    payload: dict[str, object] = {
        "schema_version": SHEET_DELIVERY_SCHEMA_VERSION,
        "row_count": 2,
        "column_count": 2,
        "values": [["标题", "值"], [1, {"formula": "=A2+1"}]],
        "base_style": {
            "font_size_pt": 10,
            "text_color": "#000000",
            "border_type": "full",
            "border_color": "#D0D5DD",
            "horizontal_alignment": "left",
            "vertical_alignment": "middle",
            "wrap_text": False,
        },
        "style_ranges": [
            {
                "range": {
                    "row_start": 0,
                    "row_end": 1,
                    "column_start": 0,
                    "column_end": 2,
                },
                "style": {"bold": True, "fill_color": "#F2F4F7"},
            }
        ],
        "merges": [],
        "default_row_height_px": 24,
        "default_column_width_px": 100,
        "row_heights": [
            {"start_index": 0, "end_index": 1, "pixel_size": 32}
        ],
        "column_widths": [],
        "frozen_row_count": 1,
        "frozen_column_count": 0,
    }
    payload.update(overrides)
    return SheetDeliverySpec.model_validate(payload)


def test_delivery_spec_normalizes_hash_summary_and_remote_values() -> None:
    first = _spec()
    second = _spec()

    assert first.content_hash == second.content_hash
    assert first.remote_values() == [["标题", "值"], [1, "=A2+1"]]
    assert isinstance(first.values[1][1], FormulaCell)
    assert first.summary() == {
        "schema_version": SHEET_DELIVERY_SCHEMA_VERSION,
        "content_hash": first.content_hash,
        "rows": 2,
        "columns": 2,
        "cells": 4,
        "nonempty_cells": 4,
        "formula_cells": 1,
        "merge_ranges": 0,
        "style_ranges": 1,
        "row_height_overrides": 1,
        "column_width_overrides": 0,
    }
    assert first.style_at(0, 0).bold is True
    assert first.style_at(1, 0).bold is False
    assert first.delivery_range.a1("sheet-one") == "sheet-one!A1:B2"


def test_remote_values_serializes_blank_cells_as_feishu_empty_strings() -> None:
    spec = _spec(values=[[None, "值"], [True, 2]])

    assert spec.values[0][0] is None
    assert spec.remote_values() == [["", "值"], [True, 2]]
    assert spec.typed_remote_cells() == [
        [{}, {"value": "值"}],
        [{"value": True}, {"value": 2}],
    ]
    assert spec.has_boolean_values is True


def test_delivery_spec_without_boolean_does_not_require_typed_write() -> None:
    assert _spec().has_boolean_values is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"values": [["only one row"]]},
        {"values": [["=ambiguous", None], [None, None]]},
        {"values": [["bad\u001bcontrol", None], [None, None]]},
        {
            "merges": [
                {
                    "row_start": 0,
                    "row_end": 3,
                    "column_start": 0,
                    "column_end": 1,
                }
            ]
        },
        {
            "style_ranges": [
                {
                    "range": {
                        "row_start": 0,
                        "row_end": 2,
                        "column_start": 0,
                        "column_end": 2,
                    },
                    "style": {"bold": True},
                },
                {
                    "range": {
                        "row_start": 1,
                        "row_end": 2,
                        "column_start": 1,
                        "column_end": 2,
                    },
                    "style": {"italic": True},
                },
            ]
        },
    ],
)
def test_delivery_spec_rejects_incomplete_or_ambiguous_structures(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _spec(**overrides)


def test_current_provider_rejects_wrap_instead_of_ignoring_it() -> None:
    spec = _spec(
        base_style={
            "font_size_pt": 10,
            "text_color": "#000000",
            "wrap_text": True,
        }
    )

    with pytest.raises(CapabilityError) as error:
        spec.validate_current_provider_support()

    assert error.value.code is CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC
    assert error.value.details == {"unsupported_fields": ["wrap_text=true"]}


def test_current_provider_rejects_unproven_border_variants() -> None:
    spec = _spec(
        base_style={
            "font_size_pt": 10,
            "text_color": "#000000",
            "border_type": "outer",
            "border_color": "#000000",
        }
    )

    with pytest.raises(CapabilityError) as error:
        spec.validate_current_provider_support()

    assert error.value.details == {"unsupported_fields": ["border_type=outer"]}


def test_new_sheet_title_is_explicit_and_safe() -> None:
    assert validate_sheet_title("测试用例") == "测试用例"

    for invalid in ("", " padded ", "bad/name", "x" * 101):
        with pytest.raises(CapabilityError):
            validate_sheet_title(invalid)
