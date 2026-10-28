#!/usr/bin/env python3
"""Generate the synthetic gold PubAnnotation files for the fixture notes.

Every identifier below is invented; the notes contain no real patient data.
For each note we annotate *every* occurrence of each listed literal (one gold
span per mention, matching the real annotation convention) and attach its
Safe Harbor type. Run this to regenerate ``gold/TESTnnn.json`` from the notes::

    python fixtures/synthetic/build_gold.py

The output schema matches the study gold: top-level ``text``, ``denotations``
(``id`` + ``span`` begin/end), and ``attributes`` (``subj`` -> ``obj`` type
code, ``pred == "identifier_type"``).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTES_DIR = HERE / "notes"
GOLD_DIR = HERE / "gold"

# (literal, Safe Harbor type). Each literal is annotated at every occurrence.
ANNOTATIONS: dict[str, list[tuple[str, str]]] = {
    "TEST001": [
        ("John Archer", "01_NAME"),
        ("Maria Delgado", "01_NAME"),
        ("AB-102938", "09_MEDICAL_RECORD_NUMBER"),
        ("Lakeside General Hospital", "02_GEOGRAPHIC_SUBDIVISION"),
        ("Riverton", "02_GEOGRAPHIC_SUBDIVISION"),
        ("03/14/2047", "03_DATE"),
        ("03/18/2047", "03_DATE"),
        ("03/15/2047", "03_DATE"),
        ("04/02/2047", "03_DATE"),
        ("94-year-old", "04_AGE_OVER_89"),
        ("(555) 204-1188", "05_TELEPHONE_NUMBER"),
        ("john.archer@example.org", "07_EMAIL_ADDRESS"),
    ],
    "TEST002": [
        ("Karen Poole", "01_NAME"),
        ("Robert Lin", "01_NAME"),
        ("2047-05-09", "03_DATE"),
        ("2047-05-23", "03_DATE"),
        ("617-555-0143", "05_TELEPHONE_NUMBER"),
    ],
    "TEST003": [
        ("Emily Sun", "01_NAME"),
        ("Northgate Medical Center", "02_GEOGRAPHIC_SUBDIVISION"),
        ("ZX-77410", "09_MEDICAL_RECORD_NUMBER"),
        ("11/30/2047", "03_DATE"),
        ("91-year-old", "04_AGE_OVER_89"),
        ("emily.sun@example.net", "07_EMAIL_ADDRESS"),
    ],
}


def occurrences(text: str, needle: str) -> list[int]:
    """Start offset of every (possibly overlapping) occurrence of ``needle``."""
    starts: list[int] = []
    cursor = text.find(needle)
    while cursor != -1:
        starts.append(cursor)
        cursor = text.find(needle, cursor + 1)
    return starts


def build_gold(doc_id: str, text: str, annotations: list[tuple[str, str]]) -> dict:
    """Assemble the PubAnnotation gold record for one note."""
    spans: list[tuple[int, int, str]] = []
    for literal, identifier_type in annotations:
        found = occurrences(text, literal)
        if not found:
            raise ValueError(f"{doc_id}: literal not found in note: {literal!r}")
        for start in found:
            spans.append((start, start + len(literal), identifier_type))
    spans.sort()

    denotations = []
    attributes = []
    for index, (start, end, identifier_type) in enumerate(spans, start=1):
        term_id = f"T{index}"
        denotations.append({"id": term_id, "obj": "PHI_IDENTIFIER", "span": {"begin": start, "end": end}})
        attributes.append({"id": f"A{index}", "subj": term_id, "obj": identifier_type, "pred": "identifier_type"})

    return {
        "sourcedb": "synthetic-fixture",
        "sourceid": doc_id,
        "text": text,
        "denotations": denotations,
        "attributes": attributes,
    }


def main() -> None:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    for doc_id, annotations in ANNOTATIONS.items():
        text = (NOTES_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
        gold = build_gold(doc_id, text, annotations)
        out_path = GOLD_DIR / f"{doc_id}.json"
        out_path.write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out_path.relative_to(HERE.parent.parent)} "
              f"({len(gold['denotations'])} spans)")


if __name__ == "__main__":
    main()
