"""Independent, unassisted synthetic detector and byte-restoration evaluation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from ClinicalTextDeid.api import public_payload, restore, transform
from .cases import CASES, gold_spans


def positions(spans):
    return {i for s in spans for i in range(s["start"], s["end"])}


def rate(num, den):
    return round(num / den, 6) if den else None


def rejected(action):
    try:
        action()
    except Exception:
        # Only report rejection. Exceptions may contain content and are not logged.
        return True
    return False


def evaluate(detector="hybrid"):
    key = os.urandom(32)
    totals = {"tp": 0, "fp": 0, "fn": 0, "gold_entities": 0, "exact_extent": 0, "exact_extent_type": 0,
              "restored": 0, "fragments": 0, "retained_fragments": 0}
    rows, integrity = [], []
    for case in CASES:
        context = {"project": "independent-synthetic", "document": case.id, "release": "r1", "purpose": "research"}
        result = transform({"text": case.text, "context": context, "detector": detector, "policy": {"mode": "direct"}}, key=key)
        pred, gold = result["audit"]["processed_spans"], gold_spans(case)
        structural_ok = all(0 <= s["start"] < s["end"] <= len(case.text) for s in pred)
        if not structural_ok:
            raise AssertionError(f"Invalid span bounds: {case.id}")
        p, g = positions(pred), positions(gold)
        tp, fp, fn = len(p & g), len(p - g), len(g - p)
        output = result["public"]["text"]
        retained = [value for value in case.protected if value in output]
        restored = restore(json.loads(json.dumps(result)), key=key, context=context) == case.text.encode("utf-8")
        exact = sum(any((s["start"], s["end"]) == (t["start"], t["end"]) for t in pred) for s in gold)
        typed = sum(any(s == {k: t[k] for k in ["start", "end", "type"]} for t in pred) for s in gold)
        misses = [{"start": s["start"], "end": s["end"], "type": s["type"],
                   "coverage": rate(len(set(range(s["start"], s["end"])) & p), s["end"] - s["start"])}
                  for s in gold if not set(range(s["start"], s["end"])) <= p]
        for name, value in {"tp": tp, "fp": fp, "fn": fn, "gold_entities": len(gold), "exact_extent": exact,
                            "exact_extent_type": typed, "restored": int(restored), "fragments": len(case.protected),
                            "retained_fragments": len(retained)}.items():
            totals[name] += value
        public = public_payload(result)
        allowlist_ok = set(public) == {"schema_version", "text", "validation_status"}
        rows.append({"id": case.id, "language": case.language, "gold_entities": len(gold),
                     "predicted_entities": len(pred), "tp_chars": tp, "fp_chars": fp, "fn_chars": fn,
                     "misses": misses, "lost_fragments": [v for v in case.protected if v not in output],
                     "byte_restored": restored, "allowlist_ok": allowlist_ok, "output": output})
        if not restored or not allowlist_ok:
            raise AssertionError(f"Restoration/public schema failure: {case.id}")
        if case.id == "I11":
            for field in ["public", "audit", "assessment", "policy", "schema_version"]:
                modified = copy.deepcopy(result)
                if field == "schema_version":
                    modified[field] = "invalid"
                else:
                    modified[field]["tampered"] = True
                integrity.append({"attack": f"aad_{field}", "rejected": rejected(lambda r=modified: restore(r, key=key, context=context))})
            other_context = context | {"purpose": "external_ai"}
            integrity.append({"attack": "purpose_swap", "rejected": rejected(lambda: restore(result, key=key, context=other_context))})
            integrity.append({"attack": "wrong_key", "rejected": rejected(lambda: restore(result, key=os.urandom(32), context=context))})
    precision, recall = rate(totals["tp"], totals["tp"] + totals["fp"]), rate(totals["tp"], totals["tp"] + totals["fn"])
    code = Path(__file__).read_bytes()
    case_digest = hashlib.sha256(json.dumps([vars(c) for c in CASES], ensure_ascii=True, sort_keys=True).encode()).hexdigest()
    language_results = {}
    for language in sorted({c.language for c in CASES}):
        selected = [r for r in rows if r["language"] == language]
        tp, fp, fn = (sum(r[k] for r in selected) for k in ["tp_chars", "fp_chars", "fn_chars"])
        language_results[language] = {"notes": len(selected), "gold_entities": sum(r["gold_entities"] for r in selected),
                                      "tp": tp, "fp": fp, "fn": fn,
                                      "character_precision": rate(tp, tp + fp), "character_recall": rate(tp, tp + fn)}
    snapshot_files = ["ClinicalTextDeid/api.py", "ClinicalTextDeid/detectors.py", "ClinicalTextDeid/generalize.py", "ClinicalTextDeid/clinical_guards.py"]
    code_snapshot = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in snapshot_files if Path(name).is_file()}
    return {"scope": "24 independent hand-annotated synthetic notes; no hints; direct transformation",
            "not_measured": ["population reidentification", "all clinical semantics", "real patient performance", "legal adequacy"],
            "detector": detector, "cases_sha256": case_digest, "evaluator_sha256": hashlib.sha256(code).hexdigest(),
            "notes": len(CASES), "totals": totals, "character_precision": precision, "character_recall": recall,
            "character_f1": round(2 * precision * recall / (precision + recall), 6) if precision is not None and recall is not None and precision + recall else None,
            "exact_entity_extent_recall": rate(totals["exact_extent"], totals["gold_entities"]),
            "exact_entity_extent_type_recall": rate(totals["exact_extent_type"], totals["gold_entities"]),
            "language_results": language_results, "code_snapshot": code_snapshot,
            "integrity": integrity, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", choices=["hybrid", "rules", "ko_pii"], default="hybrid")
    parser.add_argument("--out", type=Path, default=Path("ClinicalTextIntegration/reports/deid_independent.json"))
    args = parser.parse_args()
    report = evaluate(args.detector)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["notes", "detector", "character_precision", "character_recall", "character_f1", "totals"]}, ensure_ascii=True))
    if not all(item["rejected"] for item in report["integrity"]):
        raise SystemExit("An authenticated-field tamper was accepted")


if __name__ == "__main__":
    main()
