"""Build a synthetic note for boundary and literal-propagation checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

DOC_ID = "LONG001"
NAMES = ["Dana Kim", "May", "Rae Cole"]


def build_note() -> tuple[str, list[tuple[int, int, str]]]:
    """Return 7,200 codepoints and four reference spans; no patient data is used."""
    filler = "Synthetic example. Symptoms improved with rest. Café is ordinary text.\n"
    text = list((filler * (7200 // len(filler) + 1))[:7200])

    def place(start: int, value: str) -> None:
        text[start:start + len(value)] = value

    place(0, "SYNTHETIC TEST NOTE; NOT A PATIENT RECORD.\n")
    place(886, "Reviewed by Dr. May. ")        # surname starts at 902
    place(3487, "Patient: Dana Kim. ")          # name crosses the 3,500 boundary
    place(5700, "May improve with rest. ")      # same literal, not an identifier
    place(6585, "Follow-up: 01/02/2021. ")     # date crosses the 6,600 boundary
    place(6888, "Signed by: Rae Cole. ")        # a later source-coordinate check
    source = "".join(text)
    spans = [(902, 905, "01_NAME"), (3496, 3504, "01_NAME"),
             (6596, 6606, "03_DATE"), (6899, 6907, "01_NAME")]
    assert len(source) == 7200
    assert [source[a:b] for a, b, _ in spans] == ["May", "Dana Kim", "01/02/2021", "Rae Cole"]
    return source, spans


def write_fixture(destination: Path) -> None:
    """Write a separate corpus, leaving the original three-note fixture unchanged."""
    source, spans = build_note()
    for folder in ("notes", "gold"):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    (destination / "notes" / f"{DOC_ID}.txt").write_text(source, encoding="utf-8")
    gold = {
        "sourcedb": "long-synthetic-fixture", "sourceid": DOC_ID, "text": source,
        "denotations": [{"id": f"T{i}", "obj": "PHI_IDENTIFIER",
                         "span": {"begin": start, "end": end}}
                        for i, (start, end, _) in enumerate(spans, 1)],
        "attributes": [{"id": f"A{i}", "subj": f"T{i}", "pred": "identifier_type", "obj": label}
                       for i, (_, _, label) in enumerate(spans, 1)],
    }
    (destination / "gold" / f"{DOC_ID}.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "name_gazetteer.txt").write_text("\n".join(NAMES) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("out/long-fixture"))
    args = parser.parse_args(argv)
    write_fixture(args.out_dir)
    print(f"Synthetic notes and reference annotations: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
