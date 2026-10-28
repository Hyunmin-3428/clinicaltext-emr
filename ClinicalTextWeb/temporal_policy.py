"""Bounded source-grounded clinical dates; no general temporal NLP claim."""
from collections import Counter
import re

POLICY_VERSION = "clinical-dates-v4"

ABSOLUTE_DATE = re.compile(r"(?<!\d)(?:(?:19|20)\d{2}\s*년\s*)?(?:1[0-2]|0?[1-9])\s*월\s*(?:3[01]|[12]\d|0?[1-9])\s*일|(?<!\d)(?:19|20)\d{2}[-/.](?:1[0-2]|0?[1-9])[-/.](?:3[01]|[12]\d|0?[1-9])(?!\d)|(?<!\d)(?:19|20)\d{2}\s*년")
EVENT = re.compile(r"[가-힣]{1,12}(?:절제술|치환술)|수술|입원|퇴원|내원|진료|진단|발병|투약|투여|검사|\b(?:appendectomy|cholecystectomy|surgery|operation|admission|admitted|discharge|discharged|diagnosed|CT|MRI)\b|(?P<exercise>마라톤|장거리\s*운동|지구력\s*운동|\bmarathon\b|\bendurance exercise\b)", re.I)
IDENTITY_DATE = re.compile(r"(?:생년월일|출생일|서명일|출력일|발급일|\bDOB|date\s+of\s+birth)\s*[:=]?\s*$", re.I)


def identity_dates(text):
    return [m for m in ABSOLUTE_DATE.finditer(text) if IDENTITY_DATE.search(text[max(0,m.start()-35):m.start()])]


def protected_calendar_dates(text):
    private = {(m.start(),m.end()) for m in identity_dates(text)}
    return [{"start":m.start(),"end":m.end(),"quote":m.group()} for m in ABSOLUTE_DATE.finditer(text) if (m.start(),m.end()) not in private]


def clinical_dates(text):
    """Tie dates to the nearest explicit medical event in a bounded clause.

    Birth/administrative date labels take precedence. Other dates are not
    automatically approved merely because they occur in a medical note.
    """
    dates = list(ABSOLUTE_DATE.finditer(text))
    result = []
    for i, date in enumerate(dates):
        if IDENTITY_DATE.search(text[max(0, date.start()-35):date.start()]):
            continue
        left = max(0, date.start()-80, dates[i-1].end() if i else 0)
        right = min(len(text), date.end()+80, dates[i+1].start() if i+1 < len(dates) else len(text))
        boundaries = list(re.finditer(r"[.!?;\n]", text[left:date.start()]))
        if boundaries:
            left += boundaries[-1].end()
        boundary = re.search(r"[.!?;\n]", text[date.end():right])
        if boundary:
            right = date.end()+boundary.start()
        events = [m for m in EVENT.finditer(text,left,right)
                  if not m.group("exercise") or re.search(r"통증|부상|골절|증상|\bpain\b|\binjur",text[left:right],re.I)]
        if not events:
            continue
        event = min(events, key=lambda m: min(abs(m.end()-date.start()), abs(m.start()-date.end())))
        key = "endurance_exercise" if event.group("exercise") else event.group().casefold()
        result.append({"start":date.start(), "end":date.end(), "quote":date.group(),
                       "event":event.group(), "event_key":key,
                       "event_start":event.start(), "event_end":event.end()})
    return result


def date_rows(source, output):
    before = Counter((d["quote"], d["event_key"]) for d in clinical_dates(source))
    after = Counter((d["quote"], d["event_key"]) for d in clinical_dates(output))
    rows = []
    for (date, event), count in before.items():
        passed = after[(date,event)] >= count
        event = "장거리 운동과 증상" if event == "endurance_exercise" else event
        rows.append({"kind":"clinical_date", "label":"임상 사건 날짜", "source":f"{date} · {event}",
                     "output":f"{date} · {event}" if passed else "", "status":"passed" if passed else "failed",
                     "detail":"원래 날짜와 임상 사건의 연결을 확인했습니다." if passed else "임상 날짜가 삭제·변경되었거나 원래 사건과의 연결이 달라졌습니다."})
    return rows


def permitted_calendar_occurrences(source, output):
    """Only source-identical clinical dates remain; preserve multiplicity."""
    permitted = Counter(d["quote"] for d in clinical_dates(source))
    remaining = Counter(m.group() for m in ABSOLUTE_DATE.finditer(output))
    return bool(remaining) and not (remaining-permitted) and all(r["status"]=="passed" for r in date_rows(source,output))


def preserved_date_candidate(source, output, candidate):
    """Resolve a date-shaped birth-date detector hit only with source/event proof."""
    return (candidate["type"] in {"DT_BIRTH", "DATE", "DATE_TIME"}
            and permitted_calendar_occurrences(source,output)
            and any(candidate["start"]==d["start"] and candidate["end"]==d["end"] for d in clinical_dates(output)))
