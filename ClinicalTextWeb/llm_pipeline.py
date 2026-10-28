"""Frozen LLM purpose planning, source-bound edits, and a separate grounded audit.

Design references: RUPTA (privacy/utility feedback) and KatherLab/deidentifier
(LLM-proposed spans, deterministic source edits). No upstream code is copied.
"""
from __future__ import annotations

from collections import Counter
import copy
import re

from ClinicalCDSGateway.gateway.common import Denied, plain_text
from ClinicalTextDeid.clinical_guards import protected_spans, guard_checks
from ClinicalTextDeid.generalize import protected_facts
from .clinical import CHECKS, verify_relations
from .llm_client import validate_schema
from .temporal_policy import ABSOLUTE_DATE, clinical_dates, permitted_calendar_occurrences, protected_calendar_dates, identity_dates, POLICY_VERSION


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": {"type":"string","maxLength":400}, "maxItems":8}
PLAN_SCHEMA = obj({
    "conditions": {"type": "array", "maxItems":16, "items": obj({"id": STRING, "relation": STRING, "source_quote": STRING})},
    "diagnoses": {"type": "array", "items": {"type": "string", "description": "EXACT original diagnosis text, never a translation or abbreviation expansion. If original says MI, return MI, not 심근경색."}},
    "edits": {"type": "array", "maxItems":64, "items": obj({"quote": {"type":"string","description":"Exact minimal identifier or privacy-bearing context phrase; QUASI may combine name, age and occupation into a natural generalized descriptor."}, "occurrence": {"type": "integer", "description":"Zero-based occurrence of this IDENTICAL quote. Use 0 when it appears once. This is NOT the edit's position in the edits array."},
                "type": {"type": "string", "enum": ["PERSON", "CONTACT", "ID", "ORGANIZATION", "DATE", "LOCATION", "AGE", "EVENT", "OCCUPATION", "QUASI"], "description":"PERSON은 사람 이름, AGE는 나이, DATE는 달력 날짜, EVENT는 행사, OCCUPATION은 직업, QUASI는 복합 문맥 일반화."},
                "replacement": STRING, "reason": STRING})},
    "uncertainties": STRINGS,
})
VERIFY_SCHEMA = obj({
    "checks": {"type": "array", "maxItems":16, "items": obj({"id": STRING, "status": {"type": "string", "enum": ["passed", "failed", "unknown"]},
                "source_quote": STRING, "output_quote": STRING, "explanation": {"type":"string","maxLength":300}})},
    "residual_phi": {"type": "array", "maxItems":32, "items": obj({"quote": STRING, "type": STRING, "reason": {"type":"string","maxLength":300}})},
    "omissions": STRINGS, "additions": STRINGS, "contradictions": STRINGS, "uncertainties": STRINGS,
})
CLINICAL_SCHEMA = obj({key: value for key, value in VERIFY_SCHEMA["properties"].items() if key != "residual_phi"})
PRIVACY_SCHEMA = obj({"assessments":{"type":"array","maxItems":32,"items":obj({
    "quote":STRING,"classification":{"type":"string","enum":["identifier","combination_risk","clinical_or_generic","uncertain"]},
    "type":STRING,"reason":{"type":"string","maxLength":300}})},"uncertainties":STRINGS})


def normalize_privacy(response, candidate):
    validate_schema(response,PRIVACY_SCHEMA)
    residual=[];uncertainties=list(response["uncertainties"])
    for item in response["assessments"]:
        if not item["quote"] or item["quote"] not in candidate:
            uncertainties.append("개인정보 판정의 실제 변환문 근거를 확인하지 못했습니다.")
        elif item["classification"] in {"identifier","combination_risk"}:
            residual.append({key:item[key] for key in ["quote","type","reason"]})
        elif item["classification"]=="uncertain":
            uncertainties.append(item["reason"] or "식별정보 여부 확인이 필요합니다.")
    return {"residual_phi":residual,"uncertainties":uncertainties,"assessments":response["assessments"]}

from .prompts import PLAN_INSTRUCTIONS, VERIFY_INSTRUCTIONS, PRIVACY_INSTRUCTIONS


# Explicit medical event dates are clinical facts, including absolute dates.
AGE_BANDS = [(0, 17, "소아·청소년"), (18, 39, "청년 성인"), (40, 64, "중년"), (65, 120, "고령 성인")]


def checked_age_generalization(quote, replacement):
    """Verify newly introduced named age bands against the edited source span."""
    ages = [int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,3})\s*세|(?<!\d)(\d{1,3})[- ]year[- ]old", quote, re.I) if m.group(1)]
    ages.extend(int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{1,3})[- ]year[- ]old", quote, re.I))
    for lower, upper, label in AGE_BANDS:
        replacement = re.sub(rf"(?<!\d){lower}\s*[~–-]\s*{upper}\s*세", label, replacement)
        if label in replacement and label not in quote and not any(lower <= age <= upper for age in ages):
            reason = ("이 원문 구절에는 나이가 없습니다. 연령대와 직업을 추측해 추가하지 마세요. 독립된 성명 필드는 이름만 PERSON 유형으로 처리하세요." if not ages else "제시한 연령대가 이 원문 구절의 실제 나이를 포함하지 않습니다. 올바른 연령대로 수정하세요.")
            raise pipeline_issue("LLM_EDIT_INVALID", [{"field":"edits", "reason":reason}])
    return replacement


def pipeline_issue(code, feedback):
    error = Denied(code)
    error.feedback = feedback
    return error


def apply_edits(source, plan, detector_candidates):
    validate_schema(plan, PLAN_SCHEMA)
    if not plan["conditions"] or len(plan["conditions"]) > 50 or len(plan["edits"]) > 100:
        raise Denied("LLM_EVIDENCE_INVALID")
    ids = [condition["id"] for condition in plan["conditions"]]
    if len(ids) != len(set(ids)) or any(not condition["source_quote"] or condition["source_quote"] not in source for condition in plan["conditions"]):
        raise pipeline_issue("LLM_EVIDENCE_INVALID", [{"field":"conditions", "reason":"Every id must be unique and every source_quote must be a nonempty EXACT original substring."}])
    if any(re.fullmatch(r"\s*\d+(?:\.\d+)?\s*(?:세|살|일|years? old|days?)\s*", c["source_quote"], re.I) for c in plan["conditions"]):
        raise pipeline_issue("LLM_EVIDENCE_INVALID", [{"field":"conditions", "reason":"나이·일수만 인용하면 주체를 검증할 수 없습니다. 환자/가족 또는 사건이 포함된 실제 원문 구절을 인용하세요."}])
    clinical_calendar = clinical_dates(source)
    protected = protected_spans(source) + [{"start":d["start"], "end":d["end"], "kind":"clinical_date"} for d in protected_calendar_dates(source)]
    for diagnosis in plan["diagnoses"]:
        if not diagnosis or diagnosis not in source:
            raise pipeline_issue("LLM_EVIDENCE_INVALID", [{"field":"diagnoses", "invalid_quote":diagnosis, "reason":"This is not in the original. Use the original abbreviation/spelling, not a translation. diagnoses may be empty when no diagnosis text is present."}])
        for match in re.finditer(re.escape(diagnosis), source):
            protected.append({"start": match.start(), "end": match.end(), "kind": "llm_diagnosis"})
    edits = []
    replacements = {}
    token_counts = Counter()
    for edit in plan["edits"]:
        quote, replacement = edit["quote"], edit["replacement"]
        if quote and quote == replacement:
            continue
        replacement = checked_age_generalization(quote, replacement)
        matches = list(re.finditer(re.escape(quote), source)) if quote else []
        if not 0 <= edit["occurrence"] < len(matches) or len(replacement) > 150:
            raise pipeline_issue("LLM_EDIT_INVALID", [{"field":"edits", "quote":quote, "actual_match_count":len(matches), "reason":"Use a literal original identifier and zero-based occurrence. If it appears once, occurrence MUST be 0 regardless of edit-array position."}])
        match = matches[edit["occurrence"]]
        if edit["type"] == "DATE" and not ABSOLUTE_DATE.fullmatch(quote):
            raise pipeline_issue("LLM_EDIT_INVALID", [{"field":"edits", "quote":quote, "reason":"DATE는 명확한 달력 날짜·연도에만 사용하세요. 나이, 경과일, 생후 일수는 DATE가 아닙니다."}])
        for p in protected:
            if p["start"] < match.end() and match.start() < p["end"]:
                # A context phrase may be generalized only while the complete
                # protected clinical fragment survives verbatim in replacement.
                fragment = source[p["start"]:p["end"]]
                if p["kind"] == "clinical_date" and match.start() <= p["start"] and p["end"] <= match.end() and replacement.count(fragment) >= quote.count(fragment):
                    continue
                if edit["type"] != "QUASI" or not (match.start() <= p["start"] and p["end"] <= match.end()) or replacement.count(fragment) < quote.count(fragment):
                    raise pipeline_issue("LLM_CLINICAL_EDIT_DENIED", [{"field":"edits", "quote":fragment,
                        "reason":"수술 등 임상 사건의 실제 날짜, 경과일, 진단과 수치를 수정하지 마세요. 날짜를 토큰으로 바꾸어도 정보 손실입니다."}])
        identity = (edit["type"], quote.casefold())
        if edit["type"] in {"PERSON", "CONTACT", "ID"}:
            # Token spelling and exact-string identity are code responsibilities.
            # Never let two distinct identifiers collapse into one LLM token.
            if identity not in replacements:
                token_counts[edit["type"]] += 1
                candidate_token = f"<{edit['type']}_{token_counts[edit['type']]}>"
                while candidate_token in source or candidate_token in replacements.values():
                    token_counts[edit["type"]] += 1
                    candidate_token = f"<{edit['type']}_{token_counts[edit['type']]}>"
                replacements[identity] = candidate_token
            replacement = replacements[identity]
        if (identity in replacements and replacements[identity] != replacement) or (edit["type"] in {"PERSON", "CONTACT", "ID"} and quote.casefold() in replacement.casefold()):
            raise Denied("LLM_EDIT_INVALID")
        replacements[identity] = replacement
        edits.append({"start": match.start(), "end": match.end(), "type": edit["type"], "replacement": replacement})
    # A whole context generalization can subsume a detector's smaller identity
    # edit. Drop only strict children whose original text is already removed.
    # Partial overlaps and equal-range duplicates remain invalid below.
    edits = [e for e in edits if not any(
        p["type"] == "QUASI" and p["start"] <= e["start"] and e["end"] <= p["end"]
        and (p["start"] < e["start"] or e["end"] < p["end"])
        and source[e["start"]:e["end"]].casefold() not in p["replacement"].casefold()
        for p in edits)]
    for span in detector_candidates:
        if span["type"] == "PERSON":
            name = source[span["start"]:span["end"]]
            if any(e["start"] <= span["start"] and span["end"] <= e["end"] and name.casefold() in e["replacement"].casefold() for e in edits):
                raise pipeline_issue("LLM_EDIT_INVALID", [{"field":"edits", "quote":name, "reason":"일반화한 구절 안에 원래의 사람 이름이 남아 있습니다. 이름은 제거하세요."}])
    # Strong direct-identifier evidence cannot be waved away by an LLM claim.
    hard_types = {"RRN", "FRN", "PHONE", "FAX", "EMAIL", "MRN", "PASSPORT", "CARD", "ACCOUNT", "US_SSN"}
    date_candidates = [{"start": m.start(), "end": m.end(), "type": "DATE", "method": "default_calendar_policy"} for m in identity_dates(source)]
    for span in [*detector_candidates, *date_candidates]:
        if span["type"] not in hard_types and span["method"] != "default_calendar_policy" and not (span["type"] == "PERSON" and "clinical_regex" in span["method"]):
            continue
        if any(e["start"] <= span["start"] and span["end"] <= e["end"] for e in edits):
            continue
        if any(e["start"] < span["end"] and span["start"] < e["end"] for e in edits):
            raise Denied("LLM_EDIT_INVALID")
        quote = source[span["start"]:span["end"]]
        replacement = replacements.get((span["type"], quote.casefold()), f"<{span['type']}_AUTO_{len(edits)+1}>")
        replacements[(span["type"], quote.casefold())] = replacement
        edits.append({"start": span["start"], "end": span["end"], "type": span["type"], "replacement": replacement})
    edits.sort(key=lambda edit: edit["start"])
    if any(a["end"] > b["start"] for a, b in zip(edits, edits[1:])):
        raise pipeline_issue("LLM_EDIT_INVALID", [{"field":"edits", "reason":"수정 구간이 겹칩니다. 복합 소개 구절을 QUASI로 바꾸면 그 안의 이름·기관명에 대한 별도 편집을 중복하지 마세요."}])
    return render_edits(source,edits), edits


def render_edits(source, edits):
    parts, cursor = [], 0
    for edit in edits:
        parts.extend([source[cursor:edit["start"]], edit["replacement"]])
        cursor = edit["end"]
    parts.append(source[cursor:])
    output = "".join(parts)
    plain_text(output)
    return output


def redaction_markup_only(value, generated_tokens):
    """Only trusted generated placeholders plus explicit field labels/punctuation.

    Mixed strings containing an actual name/value are NEVER waived.
    """
    remainder = value
    seen = False
    for token in sorted(generated_tokens, key=len, reverse=True):
        if token in remainder:
            remainder = remainder.replace(token, "")
            seen = True
    remainder = re.sub(r"(?i)(?:patient\s+name|patient\s+id|name|MRN|성명|환자명|이름|연락처|병록번호)\s*[:=]", "", remainder)
    return seen and not re.search(r"\w", remainder)


def audit_evidence(source, output, purpose, checks, phrases, plan, audit, *, applied_edits=None):
    validate_schema(plan, PLAN_SCHEMA)
    validate_schema(audit, VERIFY_SCHEMA)
    base = verify_relations(source, output, checks, purpose, phrases)
    rows = base["rows"]
    findings = []
    from ClinicalTextDeid.detectors import detect
    for identifier in detect(source,detector="hybrid"):
        if identifier["type"] in {"PERSON","PHONE","EMAIL","MRN","RRN","FRN","US_SSN","PASSPORT","ACCOUNT","CARD"}:
            literal=source[identifier["start"]:identifier["end"]]
            if literal and literal in output:
                findings.append("원문에서 탐지한 직접 식별정보가 결과에 남아 있습니다: " + identifier["type"])
    from .risk_candidates import contextual_candidates
    for risk in contextual_candidates(source):
        value = source[risk["start"]:risk["end"]]
        if risk["type"] in {"ORGANIZATION", "EVENT"} and value.casefold() in output.casefold():
            findings.append("원문에서 탐지한 구체적인 기관·행사 단서가 남아 있습니다: " + risk["type"])
    if ABSOLUTE_DATE.search(output) and not permitted_calendar_occurrences(source,output):
        findings.append("원문에 근거하지 않은 날짜 또는 보존 대상으로 확인되지 않은 날짜가 남아 있습니다.")
    if len(re.findall(r"최근|\brecent(?:ly)?\b", output, re.I)) > len(re.findall(r"최근|\brecent(?:ly)?\b", source, re.I)):
        findings.append("원문에 없는 최근이라는 시간 의미가 추가되었습니다.")
    generated_tokens = {edit["replacement"] for edit in (applied_edits or [])
                        if edit["replacement"] not in source and re.fullmatch(r"<[A-Z_0-9]+>|\[[^\[\]\n]{1,40}\]", edit["replacement"])}
    before_facts, after_facts = protected_facts(source), protected_facts(output)
    for key in ["doses", "explicit_sex", "clinical_negations"]:
        if Counter(before_facts[key]) != Counter(after_facts[key]):
            findings.append("수치·용량·성별 또는 부정 표현의 기본 보존 검사에 실패했습니다: " + key)
    conditions = {item["id"]: item for item in plan["conditions"]}
    observations = {item["id"]: item for item in audit["checks"]}
    if len(conditions) != len(plan["conditions"]) or len(observations) != len(audit["checks"]) or set(conditions) != set(observations) or not conditions:
        findings.append("LLM 검사 항목의 누락·중복 또는 알 수 없는 항목이 있습니다.")
    for identifier, condition in conditions.items():
        evidence = observations.get(identifier)
        status = evidence["status"] if evidence else "unknown"
        grounded = (evidence is not None and bool(condition["source_quote"]) and condition["source_quote"] in source
                    and evidence["source_quote"] == condition["source_quote"]
                    and (status != "passed" or bool(evidence["output_quote"]) and evidence["output_quote"] in output))
        if not grounded:
            status = "unknown"
        rows.append({"kind": "llm_relation", "label": condition["relation"], "source": condition["source_quote"],
                     "output": evidence["output_quote"] if evidence and evidence["output_quote"] in output else "",
                     "status": status, "detail": evidence["explanation"] if evidence and grounded else "LLM 판정의 실제 원문·변환문 근거를 확인하지 못했습니다."})
    for quote in plan["diagnoses"]:
        valid = bool(quote) and quote in source
        passed = valid and output.count(quote) >= source.count(quote)
        rows.append({"kind": "diagnosis", "label": "LLM 추출 진단명", "source": quote if valid else "",
                     "output": quote if passed else "", "status": "passed" if passed else "failed" if valid else "unknown",
                     "detail": "진단 구절을 코드로 대조했습니다."})
    for group, passed in guard_checks(source, output).items():
        if not passed:
            findings.append("기본 임상정보 보호 검사에서 손상이 발견되었습니다: " + group)
    findings.extend(plan["uncertainties"])
    for key in ["omissions", "additions", "contradictions", "uncertainties"]:
        findings.extend(value for value in audit[key] if not redaction_markup_only(value, generated_tokens))
    residual_phi = [item for item in audit["residual_phi"] if not redaction_markup_only(item["quote"], generated_tokens)]
    for item in residual_phi:
        findings.append("LLM 잔여 식별정보 검토가 필요합니다." if item["quote"] and item["quote"] in output else "잔여 식별정보 판정의 출력 근거를 확인하지 못했습니다.")
    for finding in findings:
        rows.append({"kind": "llm_audit", "label": "별도 검증 호출", "source": "", "output": "", "status": "unknown", "detail": finding})
    passed = sum(row["status"] == "passed" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    unknown = sum(row["status"] == "unknown" for row in rows)
    return {**base, "rows": rows, "passed": passed, "failed": failed, "unknown": unknown,
            "eligible": passed > 0 and not failed and not unknown,
            "residual_phi_count": len(residual_phi),
            "scope": "LLM의 목적별 관계 대조와 코드의 근거·진단명·수치 검사를 결합합니다. 임상적 정확성의 보장은 아닙니다."}


class PurposePipeline:
    def __init__(self, client):
        self.client = client

    def run(self, source, purpose, checks, phrases, detector_candidates):
        from .privacy_policy import introduction_policy, introduction_issues, retain_required_age
        intro_policy = introduction_policy(source, purpose, phrases)
        feedback = []
        for attempt in range(2):
            plan = self.client.complete("plan", PLAN_INSTRUCTIONS,
                {"purpose": purpose, "selected_conditions": [CHECKS[key] for key in checks], "required_phrases": phrases,
                 "original": source, "detector_candidates": [{**span, "quote": source[span["start"]:span["end"]]} for span in detector_candidates],
                 "patient_introduction_policy": intro_policy, "protected_clinical_dates":clinical_dates(source),
                 "repair_feedback": feedback}, PLAN_SCHEMA)
            try:
                output, edits = apply_edits(source, plan, detector_candidates)
                adjustments = retain_required_age(edits,intro_policy)
                if adjustments:
                    output=render_edits(source,edits)
                policy_errors = introduction_issues(source, edits, intro_policy)
                if policy_errors:
                    raise pipeline_issue("LLM_EDIT_INVALID", policy_errors)
            except Denied as error:
                if attempt == 0:
                    feedback = getattr(error, "feedback", [error.code])
                    continue
                raise
            audit = self.client.complete("verify", VERIFY_INSTRUCTIONS,
                {"purpose": purpose, "original": source, "output": output, "conditions": plan["conditions"],
                 "protected_diagnoses": plan["diagnoses"], "required_phrases": phrases}, CLINICAL_SCHEMA)
            privacy = normalize_privacy(self.client.complete("privacy", PRIVACY_INSTRUCTIONS,
                {"candidate": output, "approved_placeholders": sorted({e["replacement"] for e in edits if re.fullmatch(r"<[A-Z_0-9]+>|\[[^\[\]\n]{1,40}\]", e["replacement"])})}, PRIVACY_SCHEMA),output)
            audit["residual_phi"] = privacy["residual_phi"]
            audit["uncertainties"].extend(privacy["uncertainties"])
            validation = audit_evidence(source, output, purpose, checks, phrases, plan, audit, applied_edits=edits)
            bundle = {"policy_version":POLICY_VERSION, "plan": plan, "audit": audit, "privacy_audit": privacy, "applied_edits": edits, "policy_adjustments":adjustments}
            if validation["eligible"] or attempt == 1:
                return {"output": output, "edits": edits, "validation": validation, "bundle": bundle,
                        "attempts": attempt + 1, "calls": self.client.calls, "metrics": getattr(self.client, "metrics", [])}
            feedback = [{"condition": row["label"], "reason": row["detail"]} for row in validation["rows"] if row["status"] in {"failed", "unknown"}]
        raise Denied("LLM_EVIDENCE_INVALID")
