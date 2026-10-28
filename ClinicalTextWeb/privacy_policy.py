"""Explicit policy for the common 'name(age, affiliation/job)' introduction.

This bounded rule does not infer demographic roles throughout a free narrative.
"""
import re
from ClinicalTextDeid.generalize import age_group


def introduction_policy(source, purpose, required_phrases):
    policies = []
    for m in re.finditer(r"(?m)^\s*(?P<name>[가-힣]{2,4})\s*[（(]\s*(?P<age>\d{1,3})\s*세(?P<context>[^)）\n]*)[)）]", source):
        if m["name"] in {"부친","모친","아버지","어머니","형제","자매"} or int(m["age"]) > 120:
            continue
        age_quote = m["age"] + "세"
        keep_age = any(age_quote in phrase for phrase in required_phrases) or bool(re.search(r"환자(?:의)?\s*(?:정확한\s*)?(?:나이|연령)[^.。;\n]{0,60}(?:보존|유지)", purpose))
        jobs = [j for j in ["교수","강사"] if j in m["context"] and not any(j in p for p in required_phrases)]
        policies.append({"start":m.start(),"end":m.end(),"source_quote":m.group(),"name":m["name"],
                         "patient_age_quote":age_quote,"preserve_exact_patient_age":keep_age,
                         "age_band":age_group(int(m["age"]),"ko"),"generalize_job_terms":jobs})
    return policies


def introduction_issues(source, edits, policies):
    issues = []
    for p in policies:
        cursor, parts = p["start"], []
        for e in sorted(edits,key=lambda e:e["start"]):
            if e["start"] <= p["start"] and p["end"] <= e["end"]:
                parts = [e["replacement"]]; cursor=p["end"]; break
            if p["start"] <= e["start"] < e["end"] <= p["end"]:
                parts.extend([source[cursor:e["start"]],e["replacement"]]);cursor=e["end"]
        parts.append(source[cursor:p["end"]])
        rendered="".join(parts)
        reasons=[]
        if p["name"] != "환자" and p["name"] in rendered:
            reasons.append("원래 이름이 남아 있음")
        if p["preserve_exact_patient_age"]:
            if p["patient_age_quote"] not in rendered: reasons.append("연구상 필요한 환자 나이가 누락됨")
        elif p["patient_age_quote"] in rendered or p["age_band"] not in rendered:
            reasons.append("환자의 정확한 나이를 " + p["age_band"] + "이라는 연령대로 일반화해야 함")
        if any(job in rendered for job in p["generalize_job_terms"]):
            reasons.append("세부 직함을 포괄적인 직종으로 일반화해야 함")
        if reasons:
            issues.append({"field":"edits","source_quote":p["source_quote"],"reason":"; ".join(reasons) + ". 이 소개 구절 전체를 QUASI로 자연스럽게 바꾸세요. 가족의 나이는 별도 보존 조건입니다."})
    return issues


def retain_required_age(edits, policies):
    """Restore a required source age when an intro edit used its valid band.

    Only an already validated, source-bound introduction edit is adjusted.
    No age is inferred from family history or from an unlabelled narrative.
    """
    adjustments=[]
    for p in policies:
        if not p["preserve_exact_patient_age"]:
            continue
        for e in edits:
            if e["type"] == "QUASI" and e["start"] <= p["start"] and p["end"] <= e["end"] and p["patient_age_quote"] not in e["replacement"]:
                changed,count=re.subn(re.escape(p["age_band"])+r"(?:\s*성인)?",p["patient_age_quote"],e["replacement"],count=1)
                if count:
                    e["replacement"]=changed
                    adjustments.append({"kind":"required_patient_age","detail":"환자 소개의 연령대를 연구상 필수인 원문 나이로 유지했습니다."})
    return adjustments
