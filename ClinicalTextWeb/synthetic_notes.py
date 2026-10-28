"""Explicitly authored synthetic files; never imports an external patient record."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "examples" / "clinical_notes"
COMMON_PURPOSE = (
    "신생아 호흡 경과 연구. 임상 날짜, GA와 출생체중, 성별, 생후 경과시간, "
    "CPAP·FiO2·SpO2와 호흡수·심박수, 수유량·간격, 증상의 부정, "
    "진단의 의심·배제 필요·호전 여부, 배양 검사 결과의 미확인 또는 음성을 원문대로 보존한다. "
    "원문에 없는 다른 시점의 결과를 추가하지 않는다. 이름과 병록번호는 가명처리한다."
)

NOTES = [
    {"id": "neonatal", "file": "01_neonatal_admission.txt", "label": "신생아 입원초진 · 호흡곤란",
     "date": "2023-03-06", "category": "입원초진기록", "title": "신생아 빈호흡 입원 평가"},
    {"id": "neonatal-course", "file": "02_neonatal_progress.txt", "label": "신생아 경과 · 호흡보조 감량",
     "date": "2023-03-07", "category": "입원경과기록", "title": "신생아 호흡 및 수유 경과"},
    {"id": "neonatal-discharge", "file": "03_neonatal_discharge.txt", "label": "신생아 퇴원요약 · 시간관계",
     "date": "2023-03-09", "category": "퇴원요약지", "title": "신생아 호흡 경과 퇴원요약"},
]


def neonatal_examples():
    return [{**{k:v for k,v in item.items() if k != "file"},
             "department": "신생아과", "purpose": COMMON_PURPOSE,
             "checks": ["measurements", "negation", "time"],
             "text": (ROOT / item["file"]).read_text(encoding="utf8").strip(),
             "note": "독립 작성한 가상 신생아의 합성 기록입니다. 실제 환자의 경과를 전사한 자료가 아닙니다."}
            for item in NOTES]
