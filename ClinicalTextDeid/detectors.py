"""Pinned public detectors and explicit clinical regex supplements; no model API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import unicodedata
from .clinical_guards import protected_spans

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor" / "ko-pii"


def verify_vendor():
    manifest = json.loads((VENDOR / "MANIFEST.json").read_bytes())
    if manifest.get("commit") != "9516cabd6f582935cae1536ccbd8c5448afae679":
        raise RuntimeError("Unexpected ko-pii revision")
    for entry in manifest["files"]:
        path = (VENDOR / entry["path"]).resolve()
        if not path.is_relative_to(VENDOR.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Modified ko-pii runtime file")


verify_vendor()
sys.path.insert(0, str(VENDOR / "src"))
from ko_pii import detect_all

DIRECT = {"PERSON", "RRN", "FRN", "PHONE", "FAX", "EMAIL", "ADDRESS", "PASSPORT",
          "CARD", "ACCOUNT", "DRIVER_LICENSE", "MEDICAL_INSURANCE", "PRESCRIPTION_ID",
          "IP", "URL", "VEHICLE", "DT_BIRTH", "POSTAL_CODE", "EMPLOYEE_ID", "PNU"}
CLINICAL_EXCLUSIONS = {"메트포르민", "이부프로펜", "박출률", "이뇨제", "김치", "당뇨", "복용",
    "투여", "흉통", "발열", "호흡곤란", "심근경색", "고혈압", "저혈압", "심부전", "폐렴",
    "아스피린", "인슐린", "와파린", "항응고제", "수술", "진단", "치료", "입원", "퇴원"}
_engine = None
_lock = threading.Lock()


def normalized_view(text):
    """NFKC on Unicode combining/Hangul jamo clusters with original offsets."""
    clusters = []
    for i, char in enumerate(text):
        if char in "\u200b\u200c\u200d\ufeff\u2060\u00ad":
            continue
        joins = unicodedata.combining(char) or (0x1160 <= ord(char) <= 0x11FF)
        if clusters and joins:
            clusters[-1][0] += char
            clusters[-1][2] = i + 1
        else:
            clusters.append([char, i, i + 1])
    chars, offsets = [], []
    for cluster, start, end in clusters:
        folded = unicodedata.normalize("NFKC", cluster)
        chars.extend(folded)
        offsets.extend([(start, end)] * len(folded))
    return "".join(chars), offsets


def _english_engine():
    global _engine
    with _lock:
        if _engine is None:
            try:
                from presidio_analyzer import AnalyzerEngine
                from presidio_analyzer.nlp_engine import NlpEngineProvider
                # Fail before provider can automatically download any model.
                import spacy
                if not spacy.util.is_package("en_core_web_sm"):
                    raise RuntimeError("English model is not installed")
                provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]})
                _engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
            except (ImportError, OSError) as exc:
                raise RuntimeError("Install pinned local Presidio/spaCy dependencies or explicitly select rules") from exc
    return _engine


def _add_matches(spans, text, offsets, pattern, kind, group=0, flags=0):
    for match in re.finditer(pattern, text, flags):
        a, b = match.span(group)
        if a < b:
            spans.append({"start": offsets[a][0], "end": offsets[b-1][1],
                          "type": kind, "method": "clinical_regex"})


def detect(text: str, hints=None, *, detector="hybrid"):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 200_000:
        raise ValueError("Expected bounded UTF-8 text")
    if detector not in {"hybrid", "rules", "ko_pii"}:
        raise ValueError("Unknown detector")
    view, offsets = normalized_view(text)
    # Call public code on both original and NFD-composed view, retain original offsets.
    spans = [{"start": d.start, "end": d.end, "type": d.label, "method": "ko_pii"}
             for d in detect_all(text, include=DIRECT, person_exclusions=CLINICAL_EXCLUSIONS)]
    if view != text:
        for d in detect_all(view, include=DIRECT, person_exclusions=CLINICAL_EXCLUSIONS):
            if not 0 <= d.start < d.end <= len(view):
                continue
            spans.append({"start": offsets[d.start][0], "end": offsets[d.end-1][1],
                          "type": d.label, "method": "ko_pii_normalized"})
    if detector == "hybrid":
        engine = _english_engine()
        # General English NER and English structural recognizers, not Korean NER.
        with _lock:
            found = engine.analyze(text=view, language="en", score_threshold=0.35,
                # Presidio EMAIL uses tldextract's lazy network PSL update. EMAIL is
                # covered by pinned ko-pii locally; do not enable network validation.
                entities=["PERSON", "LOCATION", "PHONE_NUMBER", "US_SSN",
                          "CREDIT_CARD", "IP_ADDRESS", "US_PASSPORT", "US_DRIVER_LICENSE"])
        rename = {"EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE", "LOCATION": "ADDRESS",
                  "CREDIT_CARD": "CARD", "IP_ADDRESS": "IP", "US_SSN": "US_SSN",
                  "US_PASSPORT": "PASSPORT", "US_DRIVER_LICENSE": "DRIVER_LICENSE"}
        for item in found:
            value = view[item.start:item.end]
            # This English model can label an entire Korean clinical sentence PERSON.
            # Korean-only/mixed CJK model spans are outside its supported language;
            # the Korean public detector and explicit anchors remain responsible.
            if any(unicodedata.name(c, "").startswith(("HANGUL", "CJK", "HIRAGANA", "KATAKANA")) for c in value):
                continue
            # Restricted eponym guard, not a universal medical vocabulary filter.
            if item.entity_type == "PERSON" and value in {"Hodgkin", "Parkinson", "Alzheimer", "Crohn", "Wilson", "Down", "Graves", "Hashimoto"}:
                if re.match(r"(?:'s)?\s+(?:lymphoma|disease|syndrome|thyroiditis)\b", view[item.end:], re.I):
                    continue
            spans.append({"start": offsets[item.start][0], "end": offsets[item.end-1][1],
                          "type": rename.get(item.entity_type, item.entity_type), "method": "presidio_spacy"})
    if detector != "ko_pii":
        # Context anchors avoid pretending these regexes recognize arbitrary English names.
        name = r"([A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?(?: [A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?){1,2})"
        _add_matches(spans, view, offsets, r"(?:[Pp]atient(?: name)?|[Nn]ame|성명|이름)\s*[:=]\s*" + name, "PERSON", 1)
        _add_matches(spans, view, offsets, r"\b\d{1,3}[- ]year[- ]old\s+(?:male\s+|female\s+)?"+name, "PERSON", 1)
        _add_matches(spans, view, offsets, r"(?:성명|이름|환자)\s*[:=]\s*([가-힣]{2,4})(?=\b|[은는이가의,.;])", "PERSON", 1)
        _add_matches(spans, view, offsets, r"(?m)^\s*([가-힣]{2,4})(?=\s*[（(]\s*\d{1,3}\s*세)", "PERSON", 1)
        _add_matches(spans, view, offsets, r"(?i)\b(?:MRN|patient ID|chart number|등록번호|병록번호)\s*[:=]\s*([A-Z0-9-]{4,30})", "MRN", 1)
        _add_matches(spans, view, offsets, r"\b(?:[A-Z][A-Za-z]+\s+){0,3}(?:University|Hospital)\b", "ORGANIZATION")
        _add_matches(spans, view, offsets, r"(?:[가-힣]{2,10})(?:대학교|대학병원)\b", "ORGANIZATION")
        _add_matches(spans, view, offsets, r"\b(?:[A-Z][a-z]+\s+){1,2}Marathon\b", "EVENT")
    if hints is not None:
        if not isinstance(hints, list) or len(hints) > 1000:
            raise ValueError("Invalid hints")
        for hint in hints:
            if not isinstance(hint, dict) or set(hint) != {"start", "end", "type"}:
                raise ValueError("Invalid hint schema")
            a, b, kind = hint["start"], hint["end"], hint["type"]
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
                raise ValueError("Invalid hint offsets")
            if kind not in DIRECT | {"MRN", "ORGANIZATION", "EVENT", "US_SSN"}:
                raise ValueError("Invalid hint type")
            spans.append({"start": a, "end": b, "type": kind, "method": "caller_hint"})
    # Clinical guards only veto whole-contained model/rule PERSON or location candidates.
    # A broader match remains visible as a risk and fails narrow preservation checks.
    protected = protected_spans(text)
    spans = [d for d in spans if d["method"] == "caller_hint" or (d["method"] == "clinical_regex" and d["type"] == "PERSON")
             or d["type"] not in {"PERSON", "ADDRESS"}
             or not any(p["start"] <= d["start"] < d["end"] <= p["end"] for p in protected)]
    # Union every connected overlapping interval so partial overlaps cannot leak tails.
    # Mixed-type interval is explicit; do not silently discard a wider lower-score match.
    spans = [d for d in spans if 0 <= d["start"] < d["end"] <= len(text)]
    spans.sort(key=lambda d: (d["start"], d["end"], d["type"]))
    merged = []
    for span in spans:
        if merged and span["start"] < merged[-1]["end"]:
            prev = merged[-1]
            prev["end"] = max(prev["end"], span["end"])
            if prev["type"] != span["type"]:
                prev["type"] = "MIXED_PHI"
            prev["method"] = "+".join(sorted(set(prev["method"].split("+") + [span["method"]])))
        else:
            merged.append(dict(span))
    return merged
