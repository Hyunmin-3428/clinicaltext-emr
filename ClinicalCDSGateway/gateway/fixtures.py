"""Author-created synthetic records only. Exact hashes are a provenance allowlist."""
from .common import digest

FIXTURES = {
    "connected-hybrid": {
        "source": "성명: 김민준. Patient name: John Smith. No evidence of MI. BP 120/80 mmHg.",
        "public": "성명: <PERSON_1>. Patient name: <PERSON_2>. No evidence of MI. BP 120/80 mmHg.",
        "private_terms": ["김민준", "John Smith"],
        "claims": ["mi-absent", "bp-120-80"],
        "origin": "author-created synthetic, no hints, distinct names; no same-person assertion",
        "gold_phi": [{"text": "김민준", "type": "PERSON"}, {"text": "John Smith", "type": "PERSON"}],
        "gold_facts": [{"source_span": "No evidence of MI", "subject": "patient", "polarity": "absent", "event": "MI"},
                       {"source_span": "BP 120/80 mmHg", "subject": "patient", "event": "blood_pressure", "value": "120/80", "unit": "mmHg"}],
    },
    "mixed-family": {
        "source": "47-year-old John Smith, professor at Yonsei University, ran Seoul Marathon last Sunday, father died from MI at 52.",
        "public": "47-year-old adult working in academia; ran a marathon last Sunday; father died from myocardial infarction at age 52.",
        "private_terms": ["John Smith", "Yonsei University", "Seoul Marathon"],
        "claims": ["patient-age-47", "academia", "marathon-last-sunday", "father-fatal-mi-age-52"],
    },
    "ko-treatment": {
        "source": "환자 김민수, 47세, 연락처 010-2345-6789, 병록번호 MRN-A123456. 폐렴 확진. 발열 없음. amoxicillin 500 mg 경구 1일 3회 복용 중.",
        "public": "47세 성인. 폐렴 확진. 발열 없음. amoxicillin 500 mg 경구 1일 3회 복용 중.",
        "private_terms": ["김민수", "010-2345-6789", "MRN-A123456"],
        "claims": ["patient-age-47", "pneumonia-confirmed", "fever-absent", "amoxicillin-500mg-oral-tid-active"],
    },
    "en-negation": {
        "source": "Patient Jane Miller, age 56, email jane@example.org. No evidence of MI. Suspected left lower lobe pneumonia. BP 120/80 mmHg.",
        "public": "Adult, age 56. No evidence of MI. Suspected left lower lobe pneumonia. BP 120/80 mmHg.",
        "private_terms": ["Jane Miller", "jane@example.org"],
        "claims": ["patient-age-56", "mi-absent", "pneumonia-suspected-left-lower", "bp-120-80"],
    },
}


def find_fixture(text: str):
    for fixture_id, item in FIXTURES.items():
        if digest(text) == digest(item["source"]):
            return fixture_id, item
    return None, None


def request(fixture_id="mixed-family", **changes):
    result = dict(text=FIXTURES[fixture_id]["source"], document="doc-001",
                  purpose="scientific-research", source="synthetic-corpus",
                  source_grade="S", destination_grade="O", recipient="local-research",
                  media_type="text/plain", project="demo-research")
    result.update(changes)
    return result
