"""Finite clinical lexical guards; these are not clinical entity recognition."""
import re
from collections import Counter

# Phrase-level guards, not a global allowlist of words that could also be names.
DIAGNOSIS_PATTERN = (
    r"(?i)\b(?:hypoxic[- ](?:ischaemic|ischemic)\s+encephalopathy(?:\s+of\s+newborn)?"
    r"|(?:acute\s+|chronic\s+|neonatal\s+)?respiratory\s+failure(?:\s+of\s+newborn)?"
    r"|metabolic\s+acidosis|respiratory\s+acidosis|lactic\s+acidosis"
    r"|neonatal\s+sepsis|hypoxic\s+brain\s+injury)\b"
)


def protected_spans(text):
    spans = []
    patterns = [
        ("clinical_diagnosis", DIAGNOSIS_PATTERN),
        ("measurement", r"(?i)\b(?:Cr|creatinine|Hb|hemoglobin|HbA1c|eGFR|Na|K|Cl|AST|ALT|WBC|platelets?|BP|HR|SpO2)\s*[:=]?\s*\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?:\s*(?:mg/dL|g/dL|mmol/L|mEq/L|bpm|mmHg|%))?"),
        ("kinship", r"(?i)\b(?:father|mother|brother|sister|uncle|aunt|cousin|grandfather|grandmother|son|daughter|husband|wife)\b|아버지|어머니|부친|모친|형제|자매|삼촌|이모|고모|조부|조모"),
        ("medical_eponym_context", r"\b(?:[A-Z][^\W\d_]*(?:[-'’][^\W\d_]+)?\s+){1,3}(?:disease|syndrome|lymphoma|brace|thyroiditis)\b"),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text):
            spans.append({"start": match.start(), "end": match.end(), "kind": kind})
    return sorted(spans, key=lambda d: d["start"])


def guard_checks(source, output):
    groups = {"measurement": True, "kinship": True, "medical_eponym_context": True, "clinical_diagnosis": True}
    protected = Counter((span["kind"], source[span["start"]:span["end"]]) for span in protected_spans(source))
    for (kind, value), count in protected.items():
        if output.count(value) < count:
            groups[kind] = False
    return groups
