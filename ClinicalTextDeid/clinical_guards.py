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

GESTATIONAL_AGE = r"(?i)\b(?:IUP|GA)\s*[:=]?\s*\d{1,2}\s*(?:\+\s*\d\s*(?:주|w(?:eeks?)?)?|(?:주|w(?:eeks?)?)(?:\s*\+\s*\d\s*(?:일|d(?:ays?)?)?)?)"
CLINICAL_ABBREVIATION = r"(?<![A-Za-z0-9_])(?:NSVD|NVD|NICU|PICU|ICU|CPR|ECMO|CT|MRI|EEG|GCS)(?![A-Za-z0-9_])"
CLINICAL_CONTEXT = r"(?i)출생|분만|환기|삽관|소생|전원|도착|신생아|검사|촬영|소견|시행|입실|재원|수술|분만|F/U|delivery|birth|born|resuscit|ventilat|intubat|scan|imaging|admi"
CLINICAL_SIGN = r"빈호흡(?=\s*(?:과|및|관찰|지속|호전|보여))|호흡수(?=\s*(?:와|및|감소|증가|추적|측정))"

def name_anchored(text, start):
    return bool(re.search(r"(?i)(?:성명|환자명|이름|patient\s+name|name)\s*[:=]\s*$",text[max(0,start-35):start]))


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
    for kind, pattern in [
        ("clinical_sign", CLINICAL_SIGN),
        ("gestational_age", GESTATIONAL_AGE),
        ("birth_measurement", r"(?i)(?:출생\s*체중|birth\s*weight)\s*[:=]?\s*\d+(?:\.\d+)?\s*(?:kg|g)\b"),
        ("clinical_elapsed_time", r"생후\s*(?:약\s*)?\d+(?:\.\d+)?\s*(?:분|시간|일|주)"),
    ]:
        for match in re.finditer(pattern,text):
            if not name_anchored(text,match.start()):
                spans.append({"start":match.start(),"end":match.end(),"kind":kind})
    for match in re.finditer(CLINICAL_ABBREVIATION,text):
        if name_anchored(text,match.start()):continue
        # Keep a clinical acronym only in a bounded clinical clause, never as a
        # global exclusion of a string that may occur in a real name field.
        around=text[max(0,match.start()-40):min(len(text),match.end()+40)]
        around=around.replace('\n',' ')
        if re.search(CLINICAL_CONTEXT,around):
            spans.append({"start":match.start(),"end":match.end(),"kind":"clinical_abbreviation"})
    return sorted(spans, key=lambda d: d["start"])


def guard_checks(source, output):
    groups = {kind:True for kind in ["measurement","kinship","medical_eponym_context","clinical_diagnosis","clinical_abbreviation","gestational_age","birth_measurement","clinical_elapsed_time","clinical_sign"]}
    protected = Counter((span["kind"], source[span["start"]:span["end"]]) for span in protected_spans(source))
    for (kind, value), count in protected.items():
        if output.count(value) < count:
            groups[kind] = False
    return groups
