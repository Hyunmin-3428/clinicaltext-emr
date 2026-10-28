"""Purpose-directed, evidence-bearing lexical checks; no claim of general NLP."""
from __future__ import annotations

from collections import Counter
import re
from ClinicalTextDeid.clinical_guards import protected_spans
from .temporal_policy import date_rows

CHECKS = {
    "measurements": "수치·단위",
    "negation": "부정·불확실성",
    "time": "임상 날짜·시간 간격",
    "family": "환자·가족 구분",
    "treatment": "수술·약물·치료",
}

PATTERNS = {
    "measurements": r"(?i)\b(?:BP|혈압|HR|SpO2|Cr|creatinine|Hb|HbA1c|eGFR|Na|K|WBC)\s*[:=]?\s*\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?:\s*(?:mg/dL|g/dL|mmol/L|mEq/L|bpm|mmHg|%))?|\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|mL|IU|U)\b|\b\d{1,3}[- ]year[- ]old\b|(?<!\d)\d{1,3}\s*(?:세|살)",
    "negation": r"(?i)\b(?:no evidence of|no family history of|denies|suspected|possible|without)\b[^.!?;\n]*|[^.!?;\n]*(?:없음|없었고|없었다|없다|의심|부인|않았다)[^.!?;\n]*",
    "time": r"(?i)(?:수술\s*후\s*\d+\s*일|수술\s*\d+\s*일\s*후|\d+\s*일\s*후|POD\s*#?\s*\d+|\bpostoperative day\s*\d+|\b\d+\s*days?\s*(?:after|later)|\blast (?:Sunday|Saturday|week)\b|\byesterday\b|지난\s*(?:일요일|토요일|주)|어제)",
    "family": r"(?i)\b(?:father|mother|brother|sister|grandfather|grandmother)\b[^.!?;,\n]*|(?:아버지|어머니|부친|모친|형제|자매)[^.!?;,\n]*",
    "treatment": r"(?i)\b(?:amoxicillin|insulin|metformin|aspirin|cefazolin)\b[^.!?;\n]*|(?:충수절제술|담낭절제술|위절제술|대장절제술|관절치환술|수술\s*시행)[^.!?;\n]*",
}


def recommended_checks(purpose):
    """Transparent keyword presets; selected conditions remain explicit inputs."""
    required = set()
    if re.search(r"수술|수술후|post.?op|surg", purpose, re.I):
        required.update(["treatment", "time", "negation"])
    if re.search(r"약물|투약|용량|medicat|drug", purpose, re.I):
        required.update(["treatment", "measurements"])
    if re.search(r"가족력|family", purpose, re.I):
        required.add("family")
    if re.search(r"혈압|심혈관|cardio|blood pressure", purpose, re.I):
        required.update(["measurements", "negation"])
    return sorted(required)


def _evidence(text, kind):
    return [m.group().strip() for m in re.finditer(PATTERNS[kind], text) if m.group().strip()]


def verify_relations(source, output, checks, purpose, required_phrases):
    required_types = set(recommended_checks(purpose))
    if _evidence(source, "time"):
        required_types.add("time")
    effective = sorted(set(checks) | required_types)
    rows = date_rows(source, output)
    clinical_labels={"clinical_abbreviation":"임상 약어 원문 보존","gestational_age":"재태기간 원문 보존","birth_measurement":"출생체중 보존","clinical_elapsed_time":"생후 경과시간 보존","clinical_sign":"임상 증상·관찰 항목 보존"}
    protected_counts=Counter((p["kind"],source[p["start"]:p["end"]]) for p in protected_spans(source) if p["kind"] in clinical_labels)
    for (kind,value),count in protected_counts.items():
        preserved=output.count(value)>=count
        rows.append({"kind":kind,"label":clinical_labels[kind],"source":value,"output":value if preserved else "","status":"passed" if preserved else "failed","detail":"보호 대상 임상 표현의 원문 표기를 대조했습니다." if preserved else "개인정보 처리와 관계없는 임상 표현 변경이 발견되었습니다."})
    if len(re.findall(r"최근|\brecent(?:ly)?\b",output,re.I)) > len(re.findall(r"최근|\brecent(?:ly)?\b",source,re.I)):
        rows.append({"kind":"temporal_addition","label":"시간 의미 추가","source":"","output":"최근",
                     "status":"failed","detail":"원문에 없는 최근이라는 시간 의미가 추가되었습니다."})
    # Baseline diagnosis protection always applies, irrespective of selected fields.
    diagnoses = Counter(source[span["start"]:span["end"]] for span in protected_spans(source) if span["kind"] == "clinical_diagnosis")
    for value, count in diagnoses.items():
        preserved = output.count(value) >= count
        rows.append({"kind": "diagnosis", "label": "진단명 기본 보존", "source": value,
                     "output": value if preserved else "", "status": "passed" if preserved else "failed",
                     "detail": "원문 진단 구절이 유지되었습니다." if preserved else "진단명이 개인정보로 치환되거나 손상되었습니다."})
    for kind in effective:
        before, after = _evidence(source, kind), _evidence(output, kind)
        if not before:
            status = "unknown" if kind in required_types else "not_present"
            rows.append({"kind": kind, "label": CHECKS[kind], "source": "", "output": "",
                         "status": status, "detail": "연구 목적에 필요한 원문 근거를 확인하지 못했습니다." if status == "unknown" else "원문에서 지원하는 표현이 발견되지 않았습니다."})
            continue
        source_counts, output_counts = Counter(before), Counter(after)
        for value, count in source_counts.items():
            passed = output_counts[value] >= count
            rows.append({"kind": kind, "label": CHECKS[kind], "source": value,
                         "output": value if passed else "", "status": "passed" if passed else "failed",
                         "detail": "근거 표현과 반복 횟수가 유지되었습니다." if passed else "원문의 근거 표현이 누락되거나 변경되었습니다."})
        for value, count in output_counts.items():
            if count > source_counts[value]:
                rows.append({"kind": kind, "label": CHECKS[kind], "source": "", "output": value,
                             "status": "failed", "detail": "원문과 다른 표현 또는 추가된 표현입니다. 별도 확인이 필요합니다."})
    for phrase in required_phrases:
        count = source.count(phrase)
        status = "unknown" if count == 0 else "passed" if output.count(phrase) == count else "failed"
        rows.append({"kind": "custom", "label": "직접 지정한 필수 구절", "source": phrase if count else "",
                     "output": phrase if output.count(phrase) else "", "status": status,
                     "detail": "필수 구절이 유지되었습니다." if status == "passed" else "원문 근거가 없습니다." if status == "unknown" else "필수 구절이 변경되거나 반복 횟수가 달라졌습니다."})
    passed = sum(row["status"] == "passed" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    unknown = sum(row["status"] == "unknown" for row in rows)
    return {"rows": rows, "passed": passed, "failed": failed, "unknown": unknown,
            "effective_checks": effective, "purpose_required_checks": sorted(required_types),
            "eligible": passed > 0 and not failed and not unknown,
            "scope": "선택한 근거 표현의 규칙 검사입니다. 자유서술 전체의 의미 동등성을 보장하지 않습니다."}
