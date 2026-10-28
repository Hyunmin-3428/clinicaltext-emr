"""Purpose-dependent context candidates, separate from direct-ID redactions.

AGE and EVENT candidates are prompts for policy review, not commands to mask.
"""
import re
from ClinicalTextDeid.detectors import detect


def contextual_candidates(text):
    from .temporal_policy import ABSOLUTE_DATE, clinical_dates
    clinical = {(d["start"],d["end"]):d["event"] for d in clinical_dates(text)}
    patterns = [
        ("DATE", ABSOLUTE_DATE),
        ("AGE", re.compile(r"(?<!\d)\d{1,3}\s*세")),
        ("ORGANIZATION", re.compile(r"[가-힣]{2,12}(?:대학교(?:병원)?|대학병원|종합병원|병원)(?=에서|으로|에|의|은|는|이|가|와|과|[\s,.;]|$)")),
        ("EVENT", re.compile(r"(?<![가-힣])(?!(?:하프|울트라|장거리|지구력|풀코스)마라톤)[가-힣]{2,12}마라톤")),
    ]
    candidates = []
    for kind, pattern in patterns:
        for m in pattern.finditer(text):
            item = {"start":m.start(),"end":m.end(),"type":kind,"method":"research_context_regex",
                    "context_quote":text[max(0,m.start()-35):min(len(text),m.end()+35)],"policy":"purpose_dependent"}
            if kind == "DATE" and (m.start(),m.end()) in clinical:
                item.update({"policy":"preserve_clinical_date", "event_hint":clinical[(m.start(),m.end())]})
            if kind == "AGE":
                prefix = re.split(r"[.!?;,\n]", text[:m.start()])[-1]
                item["role_hint"] = "family_age" if re.search(r"부친|모친|아버지|어머니|father|mother|brother|sister",prefix,re.I) else "age_requires_subject_context"
            candidates.append(item)
    return candidates


def detect_for_research(text):
    candidates = detect(text, detector="hybrid")
    seen = {(s["start"],s["end"],s["type"]) for s in candidates}
    for s in contextual_candidates(text):
        if (s["start"],s["end"],s["type"]) not in seen:
            candidates.append(s)
            seen.add((s["start"],s["end"],s["type"]))
    return sorted(candidates,key=lambda s:(s["start"],s["end"],s["type"]))
