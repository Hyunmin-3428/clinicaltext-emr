"""Deliberately narrow bilingual clause rules, never unconstrained paraphrasing."""
import re


def age_group(age, language="en"):
    if age < 18:
        return "child or adolescent" if language == "en" else "소아·청소년"
    if age < 40:
        return "young adult" if language == "en" else "청년 성인"
    if age < 65:
        return "middle-aged" if language == "en" else "중년"
    return "older adult" if language == "en" else "고령 성인"


def _unsafe_scope(text, match):
    """Conservative sentence scope veto. This is not a negation NLP parser."""
    # Decimal dots are not relevant to our matched clauses. Semicolons deliberately
    # do not terminate scope: 'denies: ...; father ...' needs a review too.
    prefix = text[:match.start()]
    boundary = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"), prefix.rfind("\n"), prefix.rfind("。"))
    suffix = text[match.end():]
    end = re.search(r"[.!?\n。]", suffix)
    around = text[boundary+1:match.start()] + " " + suffix[:end.start() if end else len(suffix)]
    cue = r"(?i)\b(?:denies|denied|deny|not|never|without|suspected|suspect|suspicion|possible|possibly|probable|probably|uncertain|unclear|unknown|if|conditional|may|might|could|would|planned|plans|reportedly|allegedly)\b|\bno (?:evidence|history|known)\b|의심|부인|불확실|만약|가정|미확인|아니|없다고"
    return bool(re.search(cue, around))


def plan(text, policy, *, blocked=None):
    edits, facts = [], []
    def add(m, kind, replacement, lost=()):
        if _unsafe_scope(text, m):
            if blocked is not None:
                blocked.append({"start": m.start(), "end": m.end(), "type": kind,
                                "reason": "scoped_negation_uncertainty_or_conditional_requires_review"})
            return False
        edits.append({"start": m.start(), "end": m.end(), "type": kind,
                      "method": "context_rule", "replacement": replacement,
                      "loss_categories": list(lost)})
        return True
    if not policy["preserve_numeric_age"]:
        for m in re.finditer(r"\b(\d{1,3})[- ]year[- ]old\b", text):
            age = int(m[1])
            if age <= 120:
                add(m, "AGE_GENERALIZATION", age_group(age), ["exact_patient_age"])
        for m in re.finditer(r"(?<!\d)(\d{1,3})세(?=\s*(?:남성|여성|환자))", text):
            age = int(m[1])
            if age <= 120:
                add(m, "AGE_GENERALIZATION", age_group(age, "ko"), ["exact_patient_age"])
    # Full phrases only. No occupation taxonomy expansion from an unqualified noun.
    for m in re.finditer(r"\bprofessor at (?:[A-Z][A-Za-z]+\s+){1,3}University\b", text):
        add(m, "OCCUPATION_GENERALIZATION", "works in an academic occupation", ["institution", "specific_occupation"])
    for m in re.finditer(r"[가-힣]{2,10}대학교\s*교수", text):
        add(m, "OCCUPATION_GENERALIZATION", "학계 직종 종사자", ["institution", "specific_occupation"])
    for m in re.finditer(r"\bran (?P<event>(?:[A-Z][a-z]+\s+){1,2}Marathon)(?: (?P<time>last Sunday|last Saturday|yesterday|last week))?\b", text):
        timing = m["time"]
        if timing and not policy["preserve_exercise_time"]:
            output = "recently participated in endurance exercise"
            lost = ["specific_event", "exact_exercise_time"]
        else:
            output = "participated in endurance exercise" + (" " + timing if timing else "")
            lost = ["specific_event"]
        add(m, "EXERCISE_GENERALIZATION", output, lost)
    for m in re.finditer(r"(?P<event>[가-힣]{2,10}\s*마라톤)(?P<verb>에 참가(?:했다|함)|을 완주(?:했다|함))", text):
        # Korean rule retains adjacent time exactly; no arbitrary recency inference.
        add(m, "EXERCISE_GENERALIZATION", "지구력 운동에 참가함", ["specific_event"])
    # Family clauses must end here; 'at 52 years ago' is intentionally unsupported.
    for m in re.finditer(r"\b(?P<relation>father|mother|brother|sister) (?P<outcome>died from|had|did not have) (?P<disease>MI|myocardial infarction) at (?P<age>\d{1,3})(?=[,.;]|$)", text):
        relation, outcome, disease, age = m["relation"], m["outcome"], m["disease"], int(m["age"])
        if age > 120:
            continue
        negative = outcome == "did not have"
        fatal = outcome == "died from"
        premature = not negative and age < (55 if relation in {"father", "brother"} else 65)
        qualifier = "negative" if negative else "positive"
        timing = "premature " if premature else ""
        details = f"{relation}; " + ("died at " if fatal else "no MI at " if negative else "MI at ") + str(age)
        output = f"{qualifier} family history of {timing}{disease} ({details})"
        if not add(m, "FAMILY_HISTORY_GENERALIZATION", output):
            continue
        fact = {"relation": relation, "negated": negative, "fatal": fatal, "age": age,
                "disease": "MI", "premature_rule": premature}
        edits[-1]["family_fact"] = fact
        facts.append(fact)
    for m in re.finditer(r"(?P<relation>아버지|어머니|형제|자매)(?:가|는)?\s*(?P<age>\d{1,3})세에\s*(?P<disease>심근경색|MI)(?P<outcome>으로 사망| 발병| 없음)(?=[,.;。]|$)", text):
        relation, age, outcome = m["relation"], int(m["age"]), m["outcome"]
        if age > 120:
            continue
        neg, fatal = outcome == " 없음", outcome == "으로 사망"
        premature = not neg and age < (55 if relation in {"아버지", "형제"} else 65)
        details = f"{relation}; {age}세 " + ("사망" if fatal else "발병 없음" if neg else "발병")
        output = ("음성" if neg else "양성") + " " + ("조기 " if premature else "") + f"심근경색 가족력 ({details})"
        if not add(m, "FAMILY_HISTORY_GENERALIZATION", output):
            continue
        fact = {"relation": relation, "negated": neg, "fatal": fatal, "age": age,
                "disease": "MI", "premature_rule": premature}
        edits[-1]["family_fact"] = fact
        facts.append(fact)
    return edits, facts


def protected_facts(text):
    # Narrow automatic obligations, never a claim of medical semantic equivalence.
    return {
        "doses": re.findall(r"(?i)(?<![\w.])\d+(?:\.\d+)?\s*(?:mcg|mg|µg|μg|g|mL|IU|U|units?|mEq|mmol)(?:/kg)?\b", text),
        "explicit_sex": re.findall(r"\b(?:male|female)\b|남성|여성", text),
        "clinical_negations": re.findall(r"(?i)\b(?:denies|denied|no|not|without)\b|없음|부인|음성", text),
    }
