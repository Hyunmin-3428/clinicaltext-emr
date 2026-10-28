"""Hand-specified synthetic PHI and clinical fragments; never patient records.

The literal gold strings are annotations, not the detector's own output.
Fragments measure selected-expression retention, not semantic equivalence.
"""
from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class Case:
    id: str
    language: str
    text: str
    phi: tuple[tuple[str, str], ...] = ()
    protected: tuple[str, ...] = ()


CASES = (
    Case("I01", "mixed", "환자 김민준은 chest pain을 호소. BP 120/80 mmHg, HR 82.", (("김민준", "PERSON"),), ("chest pain", "BP 120/80 mmHg", "HR 82")),
    Case("I02", "mixed", "환자 김민준(Minjun Kim, same patient)은 metformin 500mg BID 복용 중.", (("김민준", "PERSON"), ("Minjun Kim", "PERSON")), ("same patient", "metformin 500mg BID", "복용 중")),
    Case("I03", "mixed", "Patient Andrew Patel reports fever; no cough. amoxicillin 500 mg PO TID.", (("Andrew Patel", "PERSON"),), ("fever", "no cough", "amoxicillin 500 mg PO TID")),
    Case("I04", "en", "Élodie O’Connor reports dizziness; denies taking warfarin 2.5 mg PO qd.", (("Élodie O’Connor", "PERSON"),), ("dizziness", "denies taking warfarin 2.5 mg PO qd")),
    Case("I05", "en", "Mary-Jane Wilson has suspected pneumonia, not confirmed infection.", (("Mary-Jane Wilson", "PERSON"),), ("suspected pneumonia", "not confirmed infection")),
    Case("I06", "en", "Dr. Abdul Rahman examined the patient; Parkinson disease remains suspected.", (("Abdul Rahman", "PERSON"),), ("Parkinson disease", "suspected")),
    Case("I07", "mixed", "John Smith and John Brown은 서로 다른 환자. First has asthma; second has COPD.", (("John Smith", "PERSON"), ("John Brown", "PERSON")), ("서로 다른 환자", "First has asthma", "second has COPD")),
    Case("I08", "ko", "환자 박지훈의 보호자 김하늘에게 설명. 환자는 페니실린 알레르기 있음.", (("박지훈", "PERSON"), ("김하늘", "PERSON")), ("보호자", "환자는 페니실린 알레르기 있음")),
    Case("I09", "mixed", "성명: 이서준; 연락처 010-2837-6491; email seo.test@example.invalid. No evidence of MI.", (("이서준", "PERSON"), ("010-2837-6491", "PHONE"), ("seo.test@example.invalid", "EMAIL")), ("No evidence of MI",)),
    Case("I10", "mixed", "MRN: HX-784512; patient 이름: 정수아. HbA1c 6.5%, Cr 1.2 mg/dL.", (("HX-784512", "MRN"), ("정수아", "PERSON")), ("HbA1c 6.5%", "Cr 1.2 mg/dL")),
    Case("I11", "en", "47-year-old John Smith, professor at Yonsei University, ran Seoul Marathon last Sunday, father died from MI at 52.", (("John Smith", "PERSON"), ("Yonsei University", "ADDRESS"), ("Seoul Marathon", "EVENT")), ("47-year-old", "last Sunday", "father died from MI at 52")),
    Case("I12", "en", "No family history of MI. Father alive at 72.", (), ("No family history of MI", "Father alive at 72")),
    Case("I13", "en", "Father had MI at 52, died of pneumonia at 74.", (), ("Father had MI at 52", "died of pneumonia at 74")),
    Case("I14", "en", "Father did not die from MI at 52.", (), ("Father did not die from MI at 52",)),
    Case("I15", "en", "Mother had MI at 54; maternal aunt had MI at 52.", (), ("Mother had MI at 54", "maternal aunt had MI at 52")),
    Case("I16", "en", "Uncle had MI at 52; father history unknown.", (), ("Uncle had MI at 52", "father history unknown")),
    Case("I17", "mixed", "47세 여성 환자 최유진; 지난 일요일 마라톤 완주 후 어제 흉통 시작.", (("최유진", "PERSON"),), ("47세 여성", "지난 일요일", "어제 흉통 시작")),
    Case("I18", "en", "warfarin 2.5 mg PO qd; insulin 10 U SC; fentanyl 25 µg/h; no allergy.", (), ("warfarin 2.5 mg PO qd", "insulin 10 U SC", "fentanyl 25 µg/h", "no allergy")),
    Case("I19", "mixed", "Allergy: penicillin; denies taking metformin 500mg BID. 발열 없음.", (), ("Allergy: penicillin", "denies taking metformin 500mg BID", "발열 없음")),
    Case("I20", "ko", unicodedata.normalize("NFD", "환자 김민준은 흉통."), ((unicodedata.normalize("NFD", "김민준"), "PERSON"),), (unicodedata.normalize("NFD", "흉통"),)),
    Case("I21", "mixed", "환자 김\u200b민준은 흉통. Patient John\u200b Smith reports dyspnea.", (("김\u200b민준", "PERSON"), ("John\u200b Smith", "PERSON")), ("흉통", "reports dyspnea")),
    Case("I22", "mixed", "\ufeff환자 " + unicodedata.normalize("NFD", "김민준") + "은 흉통.\r\nPatient Andrew Patel: insulin 10 U SC. 🩺  \r\n", ((unicodedata.normalize("NFD", "김민준"), "PERSON"), ("Andrew Patel", "PERSON")), ("흉통", "insulin 10 U SC", "🩺")),
    Case("I23", "mixed", "Patient Naomi Brooks 주소 25 Willow Street; follow-up after 3 weeks.", (("Naomi Brooks", "PERSON"), ("25 Willow Street", "ADDRESS")), ("follow-up after 3 weeks",)),
    Case("I24", "en", "Parkinson disease, Hodgkin lymphoma, Behçet disease. BP 120/80, HR 82.", (), ("Parkinson disease", "Hodgkin lymphoma", "Behçet disease", "BP 120/80", "HR 82")),
)


def gold_spans(case):
    spans = []
    for literal, kind in case.phi:
        start = case.text.index(literal)
        if case.text.count(literal) != 1:
            raise ValueError("Gold literal must occur exactly once")
        spans.append({"start": start, "end": start + len(literal), "type": kind})
    return spans
