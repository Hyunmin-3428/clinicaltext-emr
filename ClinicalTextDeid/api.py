"""JSON-friendly transformation with encrypted full-byte restoration capsule."""
from __future__ import annotations

import base64
import copy
import json
import os
import re
from collections import Counter

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .detectors import detect, CLINICAL_EXCLUSIONS
from .generalize import plan, protected_facts
from .clinical_guards import guard_checks

SCHEMA = "clinical-text-deid/v1"
FORMAT = "clinical-text-deid-aes256gcm/v1"
MAX_BYTES = 200_000
DEFAULT_POLICY = {"mode": "direct", "preserve_numeric_age": True, "preserve_exercise_time": True}


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _key(key):
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Expected a random 32-byte key supplied by caller")


def _context(context):
    if not isinstance(context, dict) or set(context) != {"project", "document", "release", "purpose"}:
        raise ValueError("Expected project/document/release/purpose context")
    if any(not isinstance(v, str) or not v or len(v) > 128 for v in context.values()):
        raise ValueError("Invalid opaque context")
    if context["purpose"] not in {"research", "external_ai"}:
        raise ValueError("Invalid purpose")


def _policy(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_POLICY):
        raise ValueError("Unknown policy fields")
    result = DEFAULT_POLICY | value
    if result["mode"] not in {"direct", "context_rules"}:
        raise ValueError("Unknown policy mode")
    if any(type(result[k]) is not bool for k in ["preserve_numeric_age", "preserve_exercise_time"]):
        raise ValueError("Policy flags must be booleans")
    return result


def _raw(request):
    if not isinstance(request, dict) or set(request) - {"text", "utf8_b64", "context", "policy", "hints", "detector"}:
        raise ValueError("Unknown request fields")
    if ("text" in request) == ("utf8_b64" in request):
        raise ValueError("Supply text or utf8_b64, exclusively")
    try:
        if "text" in request:
            if not isinstance(request["text"], str):
                raise ValueError()
            raw = request["text"].encode("utf-8", errors="strict")
        else:
            if not isinstance(request["utf8_b64"], str) or len(request["utf8_b64"]) > MAX_BYTES * 2:
                raise ValueError()
            raw = base64.b64decode(request["utf8_b64"], validate=True)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        text = raw.decode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Expected bounded valid UTF-8 input") from exc
    return raw, text


def transform(request: dict, *, key: bytes) -> dict:
    _key(key)
    raw, text = _raw(request)
    context = copy.deepcopy(request.get("context"))
    _context(context)
    policy = _policy(request.get("policy"))
    detector = request.get("detector", "hybrid")
    phi = detect(text, request.get("hints"), detector=detector)
    blocked = []
    generalizations, family = plan(text, policy, blocked=blocked) if policy["mode"] == "context_rules" else ([], [])
    edits = [dict(e) for e in generalizations]
    counts = {}
    for span in phi:
        # Never suppress a partially crossing PHI match in favour of a grammar rule.
        inside = any(e["start"] <= span["start"] and span["end"] <= e["end"] for e in edits)
        if inside:
            continue
        crossed = [e for e in edits if e["start"] < span["end"] and span["start"] < e["end"]]
        for e in crossed:
            edits.remove(e)
        kind = span["type"]
        counts[kind] = counts.get(kind, 0) + 1
        edits.append(span | {"replacement": f"<{kind}_{counts[kind]}>"})
    edits.sort(key=lambda e: e["start"])
    if any(a["end"] > b["start"] for a, b in zip(edits, edits[1:])):
        raise ValueError("Overlapping edit plan")
    parts, cursor = [], 0
    for edit in edits:
        parts.extend([text[cursor:edit["start"]], edit["replacement"]])
        cursor = edit["end"]
    parts.append(text[cursor:])
    output = "".join(parts)
    obligations = protected_facts(text)
    after = protected_facts(output)
    selected_generalizations = [e for e in edits if e["method"] == "context_rule"]
    family = [e["family_fact"] for e in selected_generalizations if "family_fact" in e]
    dose_ok = obligations["doses"] == after["doses"]
    sex_ok = obligations["explicit_sex"] == after["explicit_sex"]
    # Numerals in protected patient-age expressions, not all private identifiers.
    ages = re.findall(r"\b\d{1,3}[- ]year[- ]old\b|(?<!\d)\d{1,3}\s*(?:세|살)", text)
    numeric_ok = not policy["preserve_numeric_age"] or all(output.count(age) >= n for age, n in Counter(ages).items())
    terms_ok = all(text.count(term) == output.count(term) for term in CLINICAL_EXCLUSIONS if term != "김치")
    lexical_checks = guard_checks(text, output)
    lexical_ok = all(lexical_checks.values())
    exercise_times = re.findall(r"\b(?:last Sunday|last Saturday|yesterday|last week)\b|지난\s*(?:일요일|토요일|주)|어제", text)
    exercise_ok = not policy["preserve_exercise_time"] or all(output.count(t) >= n for t, n in Counter(exercise_times).items())
    residual = detect(output, detector=detector)
    risks = ["unrecognized_names_and_addresses", "rare_combinations_and_narrative_reidentification",
             "date_and_numeric_clinical_quasi_identifiers", "ocr_and_unicode_obfuscation_variants",
             "english_ner_excludes_cjk_spans_and_limited_medical_eponyms",
             "clinical_dictionary_exclusions_can_hide_name_homonyms"]
    warnings = ["not_release_approval", "synthetic_evaluation_only", "no_population_reidentification_estimate",
                "grammar_generalization_is_not_free_text_semantic_understanding"]
    if not dose_ok or not sex_ok or not numeric_ok or not exercise_ok or not terms_ok or not lexical_ok:
        warnings.append("protected_clinical_fact_changed")
    if residual:
        warnings.append("residual_detector_candidates")
    if blocked:
        warnings.append("generalization_withheld_for_scoped_negation_uncertainty_or_conditional")
        risks.append("scoped_negation_uncertainty_conditional_generalization")
    if policy["mode"] == "context_rules" and len(re.findall(r"\b(?:father|mother|brother|sister)\b|아버지|어머니|형제|자매", text)) > len(family):
        warnings.append("family_clause_outside_rules")
    if any(e["loss_categories"] for e in selected_generalizations):
        warnings.append("explicit_generalization_information_loss")
    if request.get("hints"):
        warnings.append("caller_hints_used_not_autonomous_detection")
    if re.search(r"(?i)\b(?:same patient|aka|also known as)\b|동일\s*환자|별칭", text):
        warnings.append("alias_coreference_requires_review")
        risks.append("cross_language_alias_coreference_not_resolved")
    result = {"schema_version": SCHEMA, "transformation_id": os.urandom(16).hex(), "public": {"text": output},
        "audit": {"detector": detector, "processed_spans": phi,
                  "applied_edits": edits, "generalizations": selected_generalizations},
        "assessment": {"validation_status": "requires_downstream_review", "residual_risk": "unquantified",
            "unsupported": risks, "warnings": warnings, "semantic_checks": {
                "status": "failed" if not (dose_ok and sex_ok and numeric_ok and exercise_ok and terms_ok and lexical_ok) else "requires_review" if blocked else "passed_narrow_checks",
                "dose_preserved": dose_ok, "explicit_sex_preserved": sex_ok,
                "clinical_dictionary_terms_preserved": terms_ok,
                "clinical_lexical_guard_checks": lexical_checks,
                "numeric_patient_age_preserved_if_required": numeric_ok,
                "exercise_time_preserved_if_required": exercise_ok,
                "family_rule_facts": family,
                "family_rule_policy": "preserve_subject_negation_age_fatality",
                "all_free_text_semantics_validated": False},
            "residual_candidates": residual,
            "withheld_generalizations": blocked,
            "generalization_coverage": "matched_clauses_only" if policy["mode"] == "context_rules" else "not_requested"},
        "policy": policy}
    # Entire original bytes encrypted: preserve whitespace, BOM, NFD, and every lost detail.
    nonce = os.urandom(12)
    aad = canonical({"result": result, "context": context, "format": FORMAT})
    ciphertext = AESGCM(key).encrypt(nonce, raw, aad)
    result["internal"] = {"capsule": {"format": FORMAT, "nonce_b64": base64.b64encode(nonce).decode(),
                             "ciphertext_b64": base64.b64encode(ciphertext).decode()}}
    return result


def restore(result: dict, *, key: bytes, context: dict) -> bytes:
    _key(key)
    _context(context)
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA:
        raise ValueError("Unknown capsule schema")
    if not isinstance(result.get("transformation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", result["transformation_id"]):
        raise ValueError("Invalid transformation binding")
    try:
        cap = result["internal"]["capsule"]
        if set(result["internal"]) != {"capsule"} or set(cap) != {"format", "nonce_b64", "ciphertext_b64"} or cap["format"] != FORMAT:
            raise ValueError("Invalid capsule format")
        nonce = base64.b64decode(cap["nonce_b64"], validate=True)
        ciphertext = base64.b64decode(cap["ciphertext_b64"], validate=True)
        if len(nonce) != 12 or len(ciphertext) > MAX_BYTES + 16:
            raise ValueError("Invalid capsule bounds")
        bound = {k: v for k, v in result.items() if k != "internal"}
        aad = canonical({"result": bound, "context": context, "format": FORMAT})
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid capsule") from exc


def public_payload(result: dict) -> dict:
    """An allowlist, not a release decision. Never serialize internal material."""
    if result.get("schema_version") != SCHEMA:
        raise ValueError("Unknown result schema")
    return {"schema_version": SCHEMA, "text": result["public"]["text"],
            "validation_status": result["assessment"]["validation_status"]}
