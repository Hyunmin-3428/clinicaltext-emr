"""Deterministic resolver, coordinate mapping, and redaction for the de-id study.

The pipeline turns a model's structured output -- a list of
``(exact_text, identifier_type)`` pairs -- into character-offset predictions
against the source note, then renders redactions. Every step is deterministic
and coordinate-exact:

* Offsets are Unicode codepoints, half-open ``[start, end)``. The source note's
  ``str`` is the reference coordinate system.
* A predicted identifier is only ever grounded by locating the model's verbatim
  ``exact_text`` inside the supplied text (no fuzzy matching, no normalisation).
* Pass 2 runs the model again over a redaction of the note in which Pass-1
  regions have been replaced by a ``[PHI]`` marker. Matches that would span a
  marker cannot be mapped back to original coordinates and are dropped, so the
  cumulative prediction set is the union of Pass 1 and Pass 2.

This module has no third-party dependencies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence


# The study-specific 18-category schema is based on HIPAA Safe Harbor.
# The leading digits are the type codes used throughout scoring.
CANONICAL_TYPES = (
    "01_NAME",
    "02_GEOGRAPHIC_SUBDIVISION",
    "03_DATE",
    "04_AGE_OVER_89",
    "05_TELEPHONE_NUMBER",
    "06_FAX_NUMBER",
    "07_EMAIL_ADDRESS",
    "08_SOCIAL_SECURITY_NUMBER",
    "09_MEDICAL_RECORD_NUMBER",
    "10_HEALTH_PLAN_BENEFICIARY_NUMBER",
    "11_ACCOUNT_NUMBER",
    "12_CERTIFICATE_OR_LICENSE_NUMBER",
    "13_VEHICLE_IDENTIFIER_OR_SERIAL_NUMBER",
    "14_DEVICE_IDENTIFIER_OR_SERIAL_NUMBER",
    "15_WEB_URL",
    "16_IP_ADDRESS",
    "17_BIOMETRIC_IDENTIFIER",
    "18_FULL_FACE_PHOTO_OR_OTHER_UNIQUE_IDENTIFIER",
)

# Optional category-labelled rendering. The runner builds a neutral [PHI]
# representation internally for Pass 2 and saves offsets, not redacted text.
TYPE_MARKERS = {
    "01_NAME": "[NAME]",
    "02_GEOGRAPHIC_SUBDIVISION": "[GEOGRAPHIC_SUBDIVISION]",
    "03_DATE": "[DATE]",
    "04_AGE_OVER_89": "[AGE_OVER_89]",
    "05_TELEPHONE_NUMBER": "[TELEPHONE_NUMBER]",
    "06_FAX_NUMBER": "[FAX_NUMBER]",
    "07_EMAIL_ADDRESS": "[EMAIL_ADDRESS]",
    "08_SOCIAL_SECURITY_NUMBER": "[SOCIAL_SECURITY_NUMBER]",
    "09_MEDICAL_RECORD_NUMBER": "[MEDICAL_RECORD_NUMBER]",
    "10_HEALTH_PLAN_BENEFICIARY_NUMBER": "[HEALTH_PLAN_BENEFICIARY_NUMBER]",
    "11_ACCOUNT_NUMBER": "[ACCOUNT_NUMBER]",
    "12_CERTIFICATE_OR_LICENSE_NUMBER": "[CERTIFICATE_OR_LICENSE_NUMBER]",
    "13_VEHICLE_IDENTIFIER_OR_SERIAL_NUMBER": "[VEHICLE_IDENTIFIER_OR_SERIAL_NUMBER]",
    "14_DEVICE_IDENTIFIER_OR_SERIAL_NUMBER": "[DEVICE_IDENTIFIER_OR_SERIAL_NUMBER]",
    "15_WEB_URL": "[WEB_URL]",
    "16_IP_ADDRESS": "[IP_ADDRESS]",
    "17_BIOMETRIC_IDENTIFIER": "[BIOMETRIC_IDENTIFIER]",
    "18_FULL_FACE_PHOTO_OR_OTHER_UNIQUE_IDENTIFIER": "[OTHER_UNIQUE_IDENTIFIER]",
}

# Study defaults: 3,500-codepoint windows with 400-codepoint overlap. Overlap
# helps retain identifiers near boundaries; it does not guarantee coverage of
# arbitrarily long identifiers. Use --segment-size / --overlap for other runs.
SEGMENT_SIZE = 3500
SEGMENT_OVERLAP = 400

# Neutral marker written into the de-identified note in place of each region.
REDACTION_MARKER = "[PHI]"

PREDICTIONS_SCHEMA_VERSION = "resolved-predictions-v1"


class StrictJSONError(ValueError):
    """Raised when model output violates the strict-JSON contract."""


class PipelineError(RuntimeError):
    """Raised on an internal invariant violation (segmentation, rendering, ...)."""


@dataclass(frozen=True)
class Pair:
    """A unique ``(exact_text, identifier_type)`` pair extracted by the model."""

    exact_text: str
    identifier_type: str


@dataclass(frozen=True, order=True)
class Prediction:
    """A resolved redaction span in source coordinates, half-open ``[start, end)``."""

    start: int
    end: int
    identifier_type: str


@dataclass(frozen=True)
class Segment:
    """One inference window over a note (or over a Pass-2 redacted representation)."""

    segment_id: str
    start: int
    end: int
    text: str


def sha256_text(text: str) -> str:
    """Hex SHA-256 of ``text`` encoded as UTF-8 (used to tag output artifacts)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strict_json_loads(text: str) -> Any:
    """Parse JSON, rejecting duplicate object keys and non-standard numbers.

    The model contract is strict JSON. ``NaN``/``Infinity`` and repeated keys are
    both signs of a malformed or non-conforming response, so we refuse them
    rather than let ``json`` silently accept them.
    """

    def reject_duplicate_keys(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in items:
            if key in seen:
                raise StrictJSONError("duplicate_key")
            seen[key] = value
        return seen

    def reject_constant(_: str) -> None:
        raise StrictJSONError("nonstandard_number")

    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )


def fixed_segments(text: str, size: int = SEGMENT_SIZE, overlap: int = SEGMENT_OVERLAP) -> list[Segment]:
    """Split ``text`` into fixed-width overlapping windows.

    Every window is exactly ``size`` codepoints wide except the last, and each
    starts ``size - overlap`` codepoints after the previous one. The windows
    fully cover the text. Invariants are checked so a silent coverage gap can
    never reach the model.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise PipelineError("segmentation_parameters_invalid")

    segments: list[Segment] = []
    start = 0
    while True:
        end = min(start + size, len(text))
        segments.append(Segment(f"S{len(segments) + 1:04d}", start, end, text[start:end]))
        if end == len(text):
            break
        start = end - overlap

    if segments[0].start != 0 or segments[-1].end != len(text):
        raise PipelineError("segmentation_coverage_invalid")
    for left, right in zip(segments, segments[1:]):
        if left.end - left.start != size or right.start != left.end - overlap:
            raise PipelineError("segmentation_stride_invalid")
    if any(seg.text != text[seg.start:seg.end] for seg in segments):
        raise PipelineError("segmentation_slice_invalid")
    return segments


def all_occurrence_starts(haystack: str, needle: str) -> list[int]:
    """Return every start index at which ``needle`` occurs in ``haystack``.

    Occurrences may overlap (the cursor advances by one), matching the
    document-wide propagation rule: an identifier the model reports once is
    redacted at every place its exact text appears.
    """
    if not needle:
        return []
    starts: list[int] = []
    cursor = 0
    while True:
        found = haystack.find(needle, cursor)
        if found < 0:
            return starts
        starts.append(found)
        cursor = found + 1


def validate_response(parsed: Any, supplied_text: str) -> dict[str, Any]:
    """Validate one model response against the output contract.

    Returns a summary with the accepted, de-duplicated ``pairs`` and counts of
    each rejection reason. An item is accepted only if it is an object with
    exactly ``exact_text`` and ``identifier_type`` keys, a non-empty string
    ``exact_text`` that occurs verbatim in ``supplied_text``, and a valid type.
    """
    if not isinstance(parsed, dict) or not isinstance(parsed.get("identifiers"), list):
        return {
            "envelope_usable": False,
            "strict_schema_valid": False,
            "pairs": [],
            "counts": {"raw_items": 0, "accepted_unique_pairs": 0, "rejected_items": 0,
                       "duplicate_pairs": 0, "ungrounded_items": 0, "invalid_type_items": 0},
        }

    strict = set(parsed) == {"identifiers"}
    accepted: dict[tuple[str, str], None] = {}
    rejected = duplicates = ungrounded = invalid_type = 0

    for item in parsed["identifiers"]:
        if not isinstance(item, dict):
            strict = False
            rejected += 1
            continue
        if set(item) != {"exact_text", "identifier_type"}:
            strict = False
            rejected += 1
            continue

        literal = item.get("exact_text")
        label = item.get("identifier_type")
        if not isinstance(literal, str) or not literal:
            strict = False
            rejected += 1
        elif label not in CANONICAL_TYPES:
            strict = False
            invalid_type += 1
            rejected += 1
        elif literal not in supplied_text:
            ungrounded += 1
            rejected += 1
        else:
            key = (literal, label)
            if key in accepted:
                strict = False
                duplicates += 1
            else:
                accepted[key] = None

    pairs = [Pair(literal, label) for literal, label in accepted]
    return {
        "envelope_usable": True,
        "strict_schema_valid": strict,
        "pairs": pairs,
        "counts": {
            "raw_items": len(parsed["identifiers"]),
            "accepted_unique_pairs": len(pairs),
            "rejected_items": rejected,
            "duplicate_pairs": duplicates,
            "ungrounded_items": ungrounded,
            "invalid_type_items": invalid_type,
        },
    }


def merge_pairs(pairs: Iterable[Pair]) -> list[Pair]:
    """Collapse pairs from several segments into one de-duplicated list."""
    unique: dict[tuple[str, str], None] = {}
    for pair in pairs:
        unique[(pair.exact_text, pair.identifier_type)] = None
    return [Pair(text, label) for text, label in unique]


def identity_map(text: str) -> list[Optional[int]]:
    """Coordinate map for Pass 1: each representation index maps to itself."""
    return list(range(len(text)))


def map_interval(coordinate_map: Sequence[Optional[int]], start: int, end: int) -> Optional[tuple[int, int]]:
    """Map ``[start, end)`` from representation to source coordinates.

    Returns ``None`` if any position is unmapped (i.e. falls on a ``[PHI]``
    marker inserted for Pass 2) or if the mapped positions are not contiguous.
    This is what prevents a Pass-2 match from spanning an already-redacted
    region.
    """
    values = coordinate_map[start:end]
    if not values or any(value is None for value in values):
        return None
    if any(right != left + 1 for left, right in zip(values, values[1:])):
        return None
    return int(values[0]), int(values[-1]) + 1


def resolve_pairs(representation: str, coordinate_map: Sequence[Optional[int]], pairs: Sequence[Pair]) -> list[Prediction]:
    """Ground each pair to source-coordinate predictions.

    For every occurrence of a pair's exact text in ``representation`` we map the
    interval back to source coordinates via ``coordinate_map``; occurrences that
    cannot be mapped contiguously are skipped. Duplicate ``(start, end, type)``
    predictions collapse.
    """
    resolved: set[tuple[int, int, str]] = set()
    for pair in pairs:
        for rep_start in all_occurrence_starts(representation, pair.exact_text):
            mapped = map_interval(coordinate_map, rep_start, rep_start + len(pair.exact_text))
            if mapped is None:
                continue
            resolved.add((mapped[0], mapped[1], pair.identifier_type))
    return [Prediction(start, end, label) for start, end, label in sorted(resolved)]


def merge_predictions(*groups: Sequence[Prediction]) -> list[Prediction]:
    """Union several prediction groups (e.g. Pass 1 and Pass 2)."""
    merged: set[tuple[int, int, str]] = set()
    for group in groups:
        for item in group:
            merged.add((item.start, item.end, item.identifier_type))
    return [Prediction(start, end, label) for start, end, label in sorted(merged)]


def union_regions(predictions: Iterable[Prediction]) -> list[tuple[int, int]]:
    """Merge predicted spans into disjoint ``[start, end)`` redaction regions."""
    intervals = sorted({(item.start, item.end) for item in predictions})
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start >= merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def overlap_components(predictions: Sequence[Prediction]) -> list[tuple[int, int, tuple[str, ...]]]:
    """Merge overlapping predictions and collect the types covering each region."""
    components: list[tuple[int, int, set[str]]] = []
    ordered = sorted(predictions, key=lambda p: (p.start, p.end, p.identifier_type))
    for item in ordered:
        if not components or item.start >= components[-1][1]:
            components.append((item.start, item.end, {item.identifier_type}))
        else:
            start, end, labels = components[-1]
            labels.add(item.identifier_type)
            components[-1] = (start, max(end, item.end), labels)
    return [(start, end, tuple(sorted(labels))) for start, end, labels in components]


def render(source: str, regions: Sequence[tuple[int, int]], markers: Sequence[str]) -> str:
    """Replace each ``regions[i]`` in ``source`` with ``markers[i]``.

    Regions must be sorted, disjoint, and within bounds.
    """
    if len(regions) != len(markers):
        raise PipelineError("render_length_mismatch")
    pieces: list[str] = []
    cursor = 0
    for (start, end), marker in zip(regions, markers):
        if start < cursor or not (0 <= start < end <= len(source)):
            raise PipelineError("render_region_invalid")
        pieces.append(source[cursor:start])
        pieces.append(marker)
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def neutral_redaction(source: str, regions: Sequence[tuple[int, int]]) -> str:
    """Render predicted regions with ``[PHI]``; accuracy is scored from offsets."""
    return render(source, regions, [REDACTION_MARKER] * len(regions))


def typed_redaction(source: str, predictions: Sequence[Prediction]) -> str:
    """Render category-labelled text for inspection; the runner does not save it."""
    components = overlap_components(predictions)
    markers = []
    for _, _, labels in components:
        if len(labels) == 1:
            markers.append(TYPE_MARKERS[labels[0]])
        else:
            markers.append("[MULTIPLE_PHI:" + "|".join(labels) + "]")
    regions = [(start, end) for start, end, _ in components]
    return render(source, regions, markers)


def redacted_representation_with_map(source: str, regions: Sequence[tuple[int, int]]) -> tuple[str, list[Optional[int]]]:
    """Build the Pass-2 input: source with Pass-1 regions replaced by ``[PHI]``.

    Returns the representation string and a coordinate map from each
    representation index back to its source index (``None`` for the inserted
    marker characters, so a Pass-2 match that touches a marker is unmappable).
    """
    pieces: list[str] = []
    coordinate_map: list[Optional[int]] = []
    cursor = 0
    for start, end in regions:
        if start < cursor or not (0 <= start < end <= len(source)):
            raise PipelineError("coordinate_region_invalid")
        pieces.append(source[cursor:start])
        coordinate_map.extend(range(cursor, start))
        pieces.append(REDACTION_MARKER)
        coordinate_map.extend([None] * len(REDACTION_MARKER))
        cursor = end
    pieces.append(source[cursor:])
    coordinate_map.extend(range(cursor, len(source)))

    representation = "".join(pieces)
    if len(representation) != len(coordinate_map):
        raise PipelineError("coordinate_map_length_invalid")
    return representation, coordinate_map


def resolved_predictions_document(document_id: str, source: str, predictions: Sequence[Prediction]) -> dict[str, Any]:
    """Assemble a literal-text-free ``resolved_predictions.json`` payload.

    This is the artifact the scorer consumes: offsets and types only, never the
    identifier text. Document identifiers and source hashes can nevertheless be
    sensitive linkage data and should remain in controlled storage.
    """
    return {
        "schema_version": PREDICTIONS_SCHEMA_VERSION,
        "document_id": document_id,
        "source_sha256": sha256_text(source),
        "offset_unit": "Unicode codepoint; half-open [start,end)",
        "contains_literal_text": False,
        "predictions": [
            {"start": p.start, "end": p.end, "identifier_type": p.identifier_type}
            for p in sorted(predictions)
        ],
    }
