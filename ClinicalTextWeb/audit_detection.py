"""Small, hand-labelled synthetic audit of real detectors, without LLM calls."""
from __future__ import annotations
import json
from pathlib import Path
from ClinicalTextDeid.detectors import detect, detect_all, DIRECT, CLINICAL_EXCLUSIONS
from .llm_service import SURROGATE, NEONATAL
from .risk_candidates import detect_for_research

CASES = [
    ("diagram", SURROGATE, ["김민수"], ["연세대학교", "2023년 3월 10일", "2018년", "서울마라톤", "47세"],
     ["충수절제술", "수술 3일 후", "발열은 없었고", "F/U CT", "무릎 통증", "부친", "52세", "심근경색"]),
    ("bilingual", "성명: 김민준. Patient name: John Smith. No evidence of MI. BP 120/80 mmHg.", ["김민준", "John Smith"], [], ["No evidence of MI", "BP 120/80 mmHg"]),
    ("direct_fields", "성명: 박지훈. 전화: 010-4827-6193. 이메일: jihun.park@example.org. 병록번호: AB123456.", ["박지훈", "010-4827-6193", "jihun.park@example.org", "AB123456"], [], []),
    ("unanchored_korean", "어제 김도윤이 외래에 방문했다. 폐렴 의심으로 추적관찰한다.", ["김도윤"], [], ["폐렴 의심"]),
    ("korean_hospital", "성명: 이서연. 서울대학교병원에서 시행한 검사에서 No evidence of MI.", ["이서연"], ["서울대학교병원"], ["No evidence of MI"]),
    ("patient_prefix", "환자 김민수는 충수절제술을 받았다. 수술 3일 후 발열 없음.", ["김민수"], [], ["충수절제술", "수술 3일 후 발열 없음"]),
    ("neonatal", NEONATAL, ["김민준"], [], ["Hypoxic ischaemic encephalopathy of newborn", "Respiratory failure of newborn", "Metabolic acidosis", "생후 1일", "No evidence of neonatal sepsis"]),
    ("english_clinical", "Patient name: Amy Lee. Respiratory failure of newborn. Metabolic acidosis. Father died from MI at 52.", ["Amy Lee"], [], ["Respiratory failure of newborn", "Metabolic acidosis", "Father", "MI"]),
    ("clinical_like_name_field", "Name: Metabolic Acidosis. BP 120/80 mmHg.", ["Metabolic Acidosis"], [], ["BP 120/80 mmHg"]),
]


def all_occurrences(text, phrase):
    start = 0
    while (start := text.find(phrase, start)) >= 0:
        yield start, start + len(phrase)
        start += len(phrase)


def run(out):
    report = {"scope": "Author-created synthetic cases; real ko-pii + Presidio/spaCy + clinical supplements; no LLM", "cases": []}
    for name, text, direct, quasi, protected in CASES:
        raw = [{"start": x.start, "end": x.end, "type": x.label} for x in detect_all(text, include=DIRECT, person_exclusions=CLINICAL_EXCLUSIONS)]
        combined = detect(text, detector="hybrid")
        research = detect_for_research(text)
        def covered(phrase, spans):
            return all(any(x["start"] <= a and b <= x["end"] for x in spans) for a,b in all_occurrences(text,phrase))
        clinical_errors = [phrase for phrase in protected if any(x["start"] < b and a < x["end"] for a,b in all_occurrences(text,phrase) for x in combined)]
        report["cases"].append({"case":name,"text":text,
            "direct":[{"phrase":p,"ko_pii":covered(p,raw),"hybrid":covered(p,combined),"research_candidates":covered(p,research)} for p in direct],
            "quasi":[{"phrase":p,"ko_pii":covered(p,raw),"hybrid":covered(p,combined),"research_candidates":covered(p,research)} for p in quasi],
            "clinical_false_positives":clinical_errors,
            "spans":[{**s,"quote":text[s["start"]:s["end"]]} for s in combined],
            "research_spans":[{**s,"quote":text[s["start"]:s["end"]]} for s in research]})
    report["summary"] = {
        "notes":len(CASES),
        "direct_targets":sum(len(c["direct"]) for c in report["cases"]),
        "direct_detected":sum(p["hybrid"] for c in report["cases"] for p in c["direct"]),
        "raw_ko_direct_detected":sum(p["ko_pii"] for c in report["cases"] for p in c["direct"]),
        "quasi_targets":sum(len(c["quasi"]) for c in report["cases"]),
        "quasi_detected":sum(p["hybrid"] for c in report["cases"] for p in c["quasi"]),
        "research_direct_detected":sum(p["research_candidates"] for c in report["cases"] for p in c["direct"]),
        "research_quasi_detected":sum(p["research_candidates"] for c in report["cases"] for p in c["quasi"]),
        "protected_clinical_phrases":sum(len(c[4]) for c in CASES),
        "clinical_false_positives":sum(len(c["clinical_false_positives"]) for c in report["cases"])}
    Path(out).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"summary":report["summary"],"misses":[{"case":c["case"],"direct":[p["phrase"] for p in c["direct"] if not p["hybrid"]],"quasi":[p["phrase"] for p in c["quasi"] if not p["hybrid"]],"clinical_false_positives":c["clinical_false_positives"]} for c in report["cases"]]},ensure_ascii=True))
    return report


if __name__ == "__main__":
    run("ClinicalTextWeb/.runtime/detection-audit.json")
