#!/usr/bin/env python3
"""Load one or more matched ``metrics_long.csv`` executions.

When multiple files are supplied, numeric outcome fields are averaged across
executions after exact key matching. Descriptive model/protocol metadata must
be identical so configuration drift fails closed instead of being averaged.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable

KEY_FIELDS = ("model_id", "pass", "cohort", "phi_type")
FIXED_FIELDS = {
    *KEY_FIELDS,
    "hf_link", "hf_gguf_repo", "developer", "family",
    "params_total_B", "params_effective_B", "params_active_B",
    "moe", "is_medical", "is_reasoning", "quant", "n_docs",
    "doc_provenance", "surface",
}
ALL_EXPECTED_REFERENCE_FIELDS = {
    "n_notes", "doc_chars_total", "gt_phi_chars", "gt_span_count",
}
FIXED_NUMERIC_FIELDS = {
    "params_total_B", "params_effective_B", "params_active_B", "n_docs",
}


def _load(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not fields or not rows:
        raise ValueError(f"empty metrics file: {path}")
    missing = [field for field in KEY_FIELDS if field not in fields]
    if missing:
        raise ValueError(f"missing metric key columns in {path}: {', '.join(missing)}")
    return fields, rows


def _key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in KEY_FIELDS)


def _index(path: Path, rows: Iterable[dict[str, str]]) -> dict[tuple[str, ...], dict[str, str]]:
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        key = _key(row)
        if key in indexed:
            raise ValueError(f"duplicate metrics row in {path}: {key}")
        indexed[key] = row
    return indexed


def load_averaged_metrics(paths: Iterable[Path]) -> list[dict[str, str]]:
    """Return matched metrics, averaging numeric outcomes over executions."""
    metric_paths = [Path(path) for path in paths]
    if not metric_paths:
        raise ValueError("at least one metrics file is required")
    resolved_paths = [path.resolve() for path in metric_paths]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ValueError("duplicate metrics file supplied as more than one execution")

    loaded = [_load(path) for path in metric_paths]
    fields = loaded[0][0]
    for path, (candidate_fields, _rows) in zip(metric_paths[1:], loaded[1:]):
        if candidate_fields != fields:
            raise ValueError(f"metrics columns or order differ in {path}")

    indexes = [_index(path, rows) for path, (_fields, rows) in zip(metric_paths, loaded)]
    expected = set(indexes[0])
    for path, indexed in zip(metric_paths[1:], indexes[1:]):
        if set(indexed) != expected:
            missing = len(expected - set(indexed))
            extra = len(set(indexed) - expected)
            raise ValueError(f"metrics key mismatch in {path}: missing={missing}, extra={extra}")

    averaged: list[dict[str, str]] = []
    for first_row in loaded[0][1]:
        key = _key(first_row)
        run_rows = [indexed[key] for indexed in indexes]
        output: dict[str, str] = {}
        for field in fields:
            values = [row.get(field, "") for row in run_rows]
            fixed_for_row = field in FIXED_FIELDS or (
                first_row.get("cohort") == "all_expected"
                and field in ALL_EXPECTED_REFERENCE_FIELDS
            )
            if fixed_for_row:
                if any(value != values[0] for value in values[1:]):
                    raise ValueError(f"fixed metadata differs across executions for {key}: {field}")
                if (field in FIXED_NUMERIC_FIELDS or field in ALL_EXPECTED_REFERENCE_FIELDS) \
                        and values[0] not in ("", "None"):
                    try:
                        fixed_number = float(values[0])
                    except ValueError as exc:
                        raise ValueError(f"invalid fixed numeric value for {key}: {field}") from exc
                    if not math.isfinite(fixed_number):
                        raise ValueError(f"non-finite fixed numeric value for {key}: {field}")
                output[field] = values[0]
                continue
            if all(value in ("", "None") for value in values):
                output[field] = values[0]
                continue
            if any(value in ("", "None") for value in values):
                raise ValueError(f"metric defined in only some executions for {key}: {field}")
            try:
                numbers = [float(value) for value in values]
            except ValueError:
                if any(value != values[0] for value in values[1:]):
                    raise ValueError(f"nonnumeric metric differs across executions for {key}: {field}")
                output[field] = values[0]
            else:
                if not all(math.isfinite(number) for number in numbers):
                    raise ValueError(f"non-finite metric for {key}: {field}")
                output[field] = format(sum(numbers) / len(numbers), ".15g")
        averaged.append(output)
    return averaged
