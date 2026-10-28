"""Bounded synthetic adapter plus separate residual and clinical validators.

Generic regex output is a REVIEW candidate, never release evidence.
No LLM/network/model/data download occurs.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from .common import plain_text
from .fixtures import find_fixture


@dataclass(frozen=True)
class Candidate:
    text: str
    mapping: tuple[tuple[str, str], ...]
    supported: bool
    fixture_id: str | None
    adapter: str = "synthetic-regex-v1"
    internal_result: dict | None = None


class SyntheticAdapter:
    def transform(self, source: str) -> Candidate:
        fixture_id, fixture = find_fixture(source)
        if fixture:
            return Candidate(fixture["public"], tuple((x, "[REMOVED]") for x in fixture["private_terms"]), True, fixture_id)
        mappings = []
        patterns = [r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"(?:\+82[- ]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}",
                    r"\d{6}[- ]?[1-4]\d{6}", r"\bMRN[- :]*[A-Za-z0-9-]+", r"(?<=환자 )[가-힣]{2,4}",
                    r"(?i)(?<=Patient )[A-Z][a-z]+ [A-Z][a-z]+"]
        text = source
        for pattern in patterns:
            def replace(match):
                mappings.append((match.group(), "[REMOVED]"))
                return "[REMOVED]"
            text = re.sub(pattern, replace, text)
        return Candidate(text, tuple(mappings), False, None)


def residual_check(source: str, candidate: str) -> tuple[str, ...]:
    """Independent rules do not call adapter.transform or trust its supported flag."""
    plain_text(candidate)
    reasons = set()
    patterns = {
        "RESIDUAL_CONTACT": r"(?i)@|(?:\+\d{1,3}[- .]?)?\d{2,4}[- .]\d{3,4}[- .]\d{4}|https?://",
        "RESIDUAL_ID": r"(?i)\b(?:MRN|SSN|patient id|passport)\b|\d{6}[- ]?\d{7}|병록|주민등록|여권",
        "RESIDUAL_NAME": r"(?i)\b(?:John Smith|Jane Miller)\b|김민수|김민준|\b(?:patient|name)\s*[:=]\s*[a-z가-힣]|(?:성명|이름)\s*[:=]\s*[a-z가-힣]",
        "RESIDUAL_QUASI": r"(?i)\b(?:University|Seoul Marathon|Street|Road)\b|대학교|[가-힣]{2,}(?:구|동)\s+\d+",
        "RESIDUAL_DATE": r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b",
    }
    for code, pattern in patterns.items():
        if re.search(pattern, candidate):
            reasons.add(code)
    _, fixture = find_fixture(source)
    if fixture and any(term.casefold() in candidate.casefold() for term in fixture["private_terms"]):
        reasons.add("SOURCE_PHI_SURVIVED")
    return tuple(sorted(reasons))


def clinical_claims(text: str) -> set[str]:
    """Limited fact parser: polarity, certainty, family relationship, dose/site.

    These rules only validate the three controlled fixtures, not arbitrary notes.
    """
    rules = {
        "patient-age-47": r"47-year-old|47세",
        "patient-age-56": r"age 56",
        "academia": r"(?i)working in academia|professor at",
        "marathon-last-sunday": r"ran (?:Seoul Marathon|a marathon) last Sunday",
        "father-fatal-mi-age-52": r"father died from (?:MI at 52|myocardial infarction at age 52)",
        "pneumonia-confirmed": r"폐렴 확진",
        "fever-absent": r"발열 없음",
        "amoxicillin-500mg-oral-tid-active": r"amoxicillin 500 mg 경구 1일 3회 복용 중",
        "mi-absent": r"No evidence of MI",
        "pneumonia-suspected-left-lower": r"Suspected left lower lobe pneumonia",
        "bp-120-80": r"BP 120/80 mmHg",
    }
    return {key for key, pattern in rules.items() if re.search(pattern, text)}


def clinical_check(source: str, public: str) -> tuple[str, ...]:
    _, fixture = find_fixture(source)
    if not fixture:
        return ("CLINICAL_VALIDATION_UNSUPPORTED",)
    expected = set(fixture["claims"])
    if clinical_claims(source) != expected or clinical_claims(public) != expected:
        return ("CLINICAL_FACT_DAMAGED",)
    # Canonical fixture output also catches additions not represented in this parser.
    if public != fixture["public"]:
        return ("UNREVIEWED_SEMANTIC_CHANGE",)
    return ()
