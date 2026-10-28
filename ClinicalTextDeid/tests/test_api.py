from __future__ import annotations

import base64
import copy
import json
import os
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from ClinicalTextDeid.api import MAX_BYTES, transform, restore, public_payload
from ClinicalTextDeid.detectors import detect, normalized_view
from ClinicalTextDeid.evaluate import CONTEXT, finite_semantic_checks, gold_spans, load_fixtures, char_counts, strict_counts


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.context = CONTEXT | {"document": "regression-test"}

    def apply(self, text, **kwargs):
        return transform({"text": text, "context": self.context, "detector": "rules", **kwargs}, key=self.key)

    def test_all_synthetic_exact_restore(self):
        for case in load_fixtures():
            for mode in ["direct", "context_rules"]:
                with self.subTest(case=case["id"], mode=mode):
                    result = self.apply(case["text"], policy={"mode": mode})
                    self.assertEqual(restore(result, key=self.key, context=self.context), case["text"].encode("utf-8"))

    def test_byte_source_bom_nfd_crlf_whitespace(self):
        raw = "\ufeff성명: 김민준\r\n\t🙂  \n".encode("utf-8")
        result = transform({"utf8_b64": base64.b64encode(raw).decode(), "context": self.context, "detector": "rules"}, key=self.key)
        self.assertEqual(restore(result, key=self.key, context=self.context), raw)
        self.assertNotIn("김민준", result["public"]["text"])

    def test_fresh_nonce_for_same_input(self):
        first, second = self.apply("Name: John Smith."), self.apply("Name: John Smith.")
        self.assertNotEqual(first["internal"]["capsule"]["nonce_b64"], second["internal"]["capsule"]["nonce_b64"])
        self.assertNotEqual(first["internal"]["capsule"]["ciphertext_b64"], second["internal"]["capsule"]["ciphertext_b64"])

    def test_public_allowlist(self):
        result = self.apply("Name: John Smith; MRN: AB-12345.")
        payload = public_payload(result)
        self.assertEqual(set(payload), {"schema_version", "text", "validation_status"})
        self.assertEqual(payload["validation_status"], "requires_downstream_review")
        encoded = json.dumps(payload)
        for value in ["John Smith", "AB-12345", "ciphertext", "nonce_b64", "capsule", "internal"]:
            self.assertNotIn(value, encoded)
        self.assertNotIn("text", result["audit"]["processed_spans"][0])

    def test_json_round_trip_restores(self):
        result = self.apply("성명: 김민준. metformin 500 mg.")
        deserialized = json.loads(json.dumps(result, ensure_ascii=False))
        self.assertEqual(restore(deserialized, key=self.key, context=self.context), "성명: 김민준. metformin 500 mg.".encode())

    def test_ciphertext_nonce_and_tag_tamper_rejected(self):
        original = self.apply("Name: John Smith.")
        for field, position in [("ciphertext_b64", 0), ("ciphertext_b64", -1), ("nonce_b64", 0)]:
            with self.subTest(field=field, position=position):
                result = copy.deepcopy(original)
                data = bytearray(base64.b64decode(result["internal"]["capsule"][field]))
                data[position] ^= 1
                result["internal"]["capsule"][field] = base64.b64encode(data).decode()
                with self.assertRaises(InvalidTag):
                    restore(result, key=self.key, context=self.context)

    def test_wrong_key_rejected(self):
        with self.assertRaises(InvalidTag):
            restore(self.apply("Name: John Smith."), key=os.urandom(32), context=self.context)

    def test_cross_context_restore_rejected(self):
        result = self.apply("Name: John Smith.")
        for field, value in [("project", "different"), ("document", "different"), ("release", "different"), ("purpose", "external_ai")]:
            with self.subTest(field=field):
                with self.assertRaises(InvalidTag):
                    restore(result, key=self.key, context=self.context | {field: value})

    def test_public_audit_policy_assessment_tamper_rejected(self):
        original = self.apply("Name: John Smith.")
        for field in ["public", "audit", "assessment", "policy"]:
            with self.subTest(field=field):
                result = copy.deepcopy(original)
                result[field]["extra"] = "tamper"
                with self.assertRaises(InvalidTag):
                    restore(result, key=self.key, context=self.context)

    def test_swapped_capsule_rejected(self):
        first, second = self.apply("Name: John Smith."), self.apply("Name: Alice Johnson.")
        first["internal"] = second["internal"]
        with self.assertRaises(InvalidTag):
            restore(first, key=self.key, context=self.context)

    def test_same_context_same_public_same_length_capsule_swap_rejected(self):
        first, second = self.apply("Name: John Smith"), self.apply("Name: Mark Jones")
        self.assertEqual(first["public"], second["public"])
        self.assertEqual(first["audit"], second["audit"])
        self.assertNotEqual(first["transformation_id"], second["transformation_id"])
        first["internal"] = second["internal"]
        with self.assertRaises(InvalidTag):
            restore(first, key=self.key, context=self.context)

    def test_one_repeated_age_or_exercise_time_removed_is_failed(self):
        for text, phrase, flag in [("47세. 47세.", "47세", "numeric_patient_age_preserved_if_required"),
                                  ("last Sunday. last Sunday.", "last Sunday", "exercise_time_preserved_if_required")]:
            with self.subTest(flag=flag):
                start = text.rindex(phrase)
                result = self.apply(text, hints=[{"start": start, "end": start + len(phrase), "type": "PERSON"}])
                checks = result["assessment"]["semantic_checks"]
                self.assertEqual(checks["status"], "failed")
                self.assertFalse(checks[flag])

    def test_unknown_capsule_schema_rejected(self):
        result = self.apply("clinical text")
        for field in ["schema_version", "format"]:
            altered = copy.deepcopy(result)
            if field == "format":
                altered["internal"]["capsule"][field] = "unknown"
            else:
                altered[field] = "unknown"
            with self.assertRaises(ValueError):
                restore(altered, key=self.key, context=self.context)

    def test_malformed_capsule_rejected(self):
        original = self.apply("clinical text")
        for field, value in [("nonce_b64", "!!!"), ("nonce_b64", base64.b64encode(b"short").decode()), ("ciphertext_b64", "!!!")]:
            altered = copy.deepcopy(original)
            altered["internal"]["capsule"][field] = value
            with self.assertRaises(ValueError):
                restore(altered, key=self.key, context=self.context)

    def test_key_length_and_type(self):
        for key in [b"short", b"0" * 31, b"0" * 33, "0" * 32, None]:
            with self.subTest(length=len(key) if key is not None else None):
                with self.assertRaises(ValueError):
                    transform({"text": "", "context": self.context, "detector": "rules"}, key=key)

    def test_input_validation(self):
        valid = {"text": "", "context": self.context, "detector": "rules"}
        invalid = [valid | {"unknown": True}, valid | {"utf8_b64": ""},
                   {"context": self.context}, valid | {"text": 3}, valid | {"text": "\ud800"},
                   valid | {"text": "a" * (MAX_BYTES + 1)}, valid | {"text": "한" * (MAX_BYTES // 3 + 1)},
                   {"utf8_b64": "!!!", "context": self.context},
                   {"utf8_b64": base64.b64encode(b"\xff").decode(), "context": self.context}]
        for request in invalid:
            with self.subTest(keys=list(request)):
                with self.assertRaises(ValueError):
                    transform(request, key=self.key)

    def test_invalid_policy_and_context(self):
        for policy in [{"mode": "llm"}, {"unknown": True}, {"preserve_numeric_age": 1}, []]:
            with self.assertRaises(ValueError):
                self.apply("", policy=policy)
        for context in [None, {}, self.context | {"purpose": "unknown"}, self.context | {"project": ""}, self.context | {"extra": "bad"}]:
            with self.assertRaises(ValueError):
                transform({"text": "", "context": context, "detector": "rules"}, key=self.key)

    def test_hint_validation(self):
        for hints in [{}, [{"start": 0, "end": 1}], [{"start": False, "end": 1, "type": "PERSON"}],
                      [{"start": 0, "end": 9, "type": "PERSON"}], [{"start": 0, "end": 1, "type": "unknown"}]]:
            with self.assertRaises(ValueError):
                self.apply("abc", hints=hints)

    def test_union_overlaps_does_not_leak_tails(self):
        text = "abcdefghij"
        hints = [{"start": 1, "end": 5, "type": "PERSON"}, {"start": 3, "end": 8, "type": "ADDRESS"}]
        result = self.apply(text, hints=hints)
        span = result["audit"]["processed_spans"][0]
        self.assertEqual((span["start"], span["end"], span["type"]), (1, 8, "MIXED_PHI"))
        self.assertEqual(result["public"]["text"], "a<MIXED_PHI_1>ij")
        self.assertIn("caller_hints_used_not_autonomous_detection", result["assessment"]["warnings"])

    def test_hint_crossing_generalization_wins(self):
        text = "professor at Yonsei University, father died from MI at 52."
        start, end = text.index("University"), text.index("father") + len("father")
        result = self.apply(text, hints=[{"start": start, "end": end, "type": "PERSON"}], policy={"mode": "context_rules"})
        self.assertNotIn("works in an academic occupation", result["public"]["text"])
        self.assertNotIn("positive family history", result["public"]["text"])
        self.assertEqual(result["assessment"]["semantic_checks"]["family_rule_facts"], [])
        self.assertEqual(restore(result, key=self.key, context=self.context), text.encode())

    def test_default_keeps_age_exercise_time_gender_and_dose(self):
        text = "47-year-old female ran Seoul Marathon last Sunday; aspirin 81 mg. father died from MI at 52."
        result = self.apply(text, policy={"mode": "context_rules"})
        output = result["public"]["text"]
        for token in ["47-year-old female", "last Sunday", "aspirin 81 mg", "father; died at 52"]:
            self.assertIn(token, output)

    def test_opt_in_information_loss_exactly_restores(self):
        text = "47-year-old John Smith, professor at Yonsei University, ran Seoul Marathon last Sunday, father died from MI at 52."
        result = self.apply(text, policy={"mode": "context_rules", "preserve_numeric_age": False, "preserve_exercise_time": False})
        output = result["public"]["text"]
        for phrase in ["middle-aged", "works in an academic occupation", "recently participated in endurance exercise", "father; died at 52"]:
            self.assertIn(phrase, output)
        for phrase in ["47-year-old", "last Sunday", "male", "female", "John Smith", "Yonsei University", "Seoul Marathon"]:
            self.assertNotIn(phrase, output)
        self.assertIn("explicit_generalization_information_loss", result["assessment"]["warnings"])
        self.assertEqual(restore(result, key=self.key, context=self.context), text.encode())

    def test_name_never_infers_gender(self):
        for name in ["John Smith", "Alice Johnson"]:
            result = self.apply(f"Name: {name}, 47-year-old.", policy={"mode": "context_rules", "preserve_numeric_age": False})
            self.assertNotRegex(result["public"]["text"], r"\b(?:male|female)\b|남성|여성")

    def test_supported_family_facts_and_output(self):
        for case in load_fixtures():
            if "family" in case:
                with self.subTest(case=case["id"]):
                    result = self.apply(case["text"], policy={"mode": "context_rules"})
                    failed = [name for name, ok in finite_semantic_checks(case, result) if not ok]
                    self.assertEqual(failed, [])

    def test_unsupported_family_kept_and_reviewed(self):
        for case in load_fixtures():
            if case.get("unsupported_family"):
                with self.subTest(case=case["id"]):
                    result = self.apply(case["text"], policy={"mode": "context_rules"})
                    self.assertEqual(result["assessment"]["semantic_checks"]["family_rule_facts"], [])
                    self.assertIn("family_clause_outside_rules", result["assessment"]["warnings"])
                    for phrase in case["sentinels"]:
                        self.assertIn(phrase, result["public"]["text"])

    def test_scoped_negation_uncertainty_conditional_veto(self):
        texts = ["Patient denies that father had MI at 52.", "It is uncertain whether father had MI at 52.",
                 "If father had MI at 52, reassess risk.", "father had MI at 52, reportedly.",
                 "아버지 52세에 심근경색 발병, 불확실."]
        for text in texts:
            with self.subTest(text=text):
                result = self.apply(text, policy={"mode": "context_rules"})
                self.assertEqual(result["assessment"]["semantic_checks"]["family_rule_facts"], [])
                self.assertEqual(result["assessment"]["semantic_checks"]["status"], "requires_review")
                self.assertTrue(result["assessment"]["withheld_generalizations"])
                self.assertNotIn("positive family history", result["public"]["text"])

    def test_negation_scope_separate_sentence(self):
        result = self.apply("Patient denies pain. father died from MI at 52.", policy={"mode": "context_rules"})
        self.assertIn("positive family history", result["public"]["text"])
        self.assertEqual(len(result["assessment"]["semantic_checks"]["family_rule_facts"]), 1)

    def test_no_safe_release_claim(self):
        result = self.apply("Rare story with an unknown name.")
        a = result["assessment"]
        self.assertEqual(a["validation_status"], "requires_downstream_review")
        self.assertEqual(a["residual_risk"], "unquantified")
        self.assertFalse(a["semantic_checks"]["all_free_text_semantics_validated"])
        self.assertIn("not_release_approval", a["warnings"])
        self.assertIn("unrecognized_names_and_addresses", a["unsupported"])

    def test_nfd_fullwidth_zerowidth_offsets_nonempty(self):
        cases = [c for c in load_fixtures() if c["id"] in {"unicode_nfd_ko", "unicode_fullwidth_phone", "unicode_zerowidth_name", "unicode_fullwidth_email"}]
        for case in cases:
            with self.subTest(case=case["id"]):
                spans = detect(case["text"], detector="rules")
                self.assertTrue(spans)
                for span in spans:
                    self.assertTrue(0 <= span["start"] < span["end"] <= len(case["text"]))
                gold = gold_spans(case)
                self.assertEqual(char_counts(spans, gold)["fn"], 0)

    def test_normalized_view_maps_to_original(self):
        text = "Ａ 김 김​민​준"
        view, offsets = normalized_view(text)
        self.assertEqual(view, "A 김 김민준")
        self.assertEqual(text[offsets[2][0]:offsets[2][1]], "김")
        self.assertEqual(len(view), len(offsets))

    def test_known_failure_is_not_hidden(self):
        case = next(c for c in load_fixtures() if c["id"] == "unknown_email_words")
        spans = detect(case["text"], detector="rules")
        self.assertGreater(char_counts(spans, gold_spans(case))["fn"], 0)
        result = self.apply(case["text"])
        self.assertIn("unrecognized_names_and_addresses", result["assessment"]["unsupported"])
        self.assertEqual(result["assessment"]["residual_risk"], "unquantified")

    def test_wrong_hints_mark_selected_clinical_losses_failed(self):
        cases = [("Insulin 10 U", "10 U", "dose_preserved"),
                 ("47세, 추적 관찰", "47세", "numeric_patient_age_preserved_if_required"),
                 ("당뇨 치료", "당뇨", "clinical_dictionary_terms_preserved"),
                 ("Cr 1.2 mg/dL", "Cr 1.2 mg/dL", "clinical_lexical_guard_checks")]
        for text, clinical, flag in cases:
            with self.subTest(flag=flag):
                start = text.index(clinical)
                result = self.apply(text, hints=[{"start": start, "end": start + len(clinical), "type": "PERSON"}])
                semantic = result["assessment"]["semantic_checks"]
                self.assertEqual(semantic["status"], "failed")
                self.assertIn("protected_clinical_fact_changed", result["assessment"]["warnings"])
                if flag == "clinical_lexical_guard_checks":
                    self.assertFalse(semantic[flag]["measurement"])
                else:
                    self.assertFalse(semantic[flag])
                self.assertEqual(restore(result, key=self.key, context=self.context), text.encode())

    def test_medical_measurement_kinship_eponym_guards_preserve(self):
        text = "Cr 1.2 mg/dL; Uncle had MI. Behçet disease; Boston brace. 당뇨 투여 복용."
        result = self.apply(text)
        self.assertEqual(result["public"]["text"], text)
        self.assertTrue(all(result["assessment"]["semantic_checks"]["clinical_lexical_guard_checks"].values()))

    def test_explicit_name_anchor_wins_over_ambiguous_eponym_guard(self):
        text = "Name: John Smith syndrome"
        for detector in ["rules", "hybrid"]:
            with self.subTest(detector=detector):
                result = transform({"text": text, "context": self.context, "detector": detector}, key=self.key)
                self.assertNotIn("John Smith", result["public"]["text"])
                checks = result["assessment"]["semantic_checks"]
                self.assertEqual(checks["status"], "failed")
                self.assertFalse(checks["clinical_lexical_guard_checks"]["medical_eponym_context"])
                self.assertIn("protected_clinical_fact_changed", result["assessment"]["warnings"])
                self.assertEqual(restore(result, key=self.key, context=self.context), text.encode())

    def test_additional_units_and_all_korean_age_expressions(self):
        text = "47세, 62 살. insulin 10U, potassium 20 mEq, sodium 5 mmol."
        result = self.apply(text)
        self.assertEqual(result["public"]["text"], text)
        checks = result["assessment"]["semantic_checks"]
        self.assertTrue(checks["dose_preserved"])
        self.assertTrue(checks["numeric_patient_age_preserved_if_required"])

    def test_metrics_do_not_hide_partial_or_wrong_type(self):
        gold = [{"start": 1, "end": 5, "type": "PERSON"}]
        partial = [{"start": 1, "end": 3, "type": "PERSON"}]
        self.assertEqual(dict(char_counts(partial, gold)), {"tp": 2, "fp": 0, "fn": 2})
        self.assertEqual(dict(strict_counts(partial, gold)), {"tp": 0, "fp": 1, "fn": 1})
        wrong = [{"start": 1, "end": 5, "type": "ADDRESS"}]
        self.assertEqual(strict_counts(wrong, gold)["tp"], 0)
        self.assertEqual(strict_counts(wrong, gold, typed=False)["tp"], 1)


class HybridTests(unittest.TestCase):
    def test_boston_brace_and_measurement_model_false_positives_guarded(self):
        key = os.urandom(32)
        text = "Boston brace recommended; Cr 1.2 mg/dL. Uncle had MI. Behçet disease."
        result = transform({"text": text, "context": CONTEXT, "detector": "hybrid"}, key=key)
        for phrase in ["Boston brace", "Cr 1.2 mg/dL", "Uncle", "Behçet disease"]:
            self.assertIn(phrase, result["public"]["text"])
        self.assertTrue(all(result["assessment"]["semantic_checks"]["clinical_lexical_guard_checks"].values()))

    def test_local_english_detector_runs_with_network_blocked(self):
        # Local inference must work even if DNS and new network connections fail.
        with patch("socket.socket.connect", side_effect=AssertionError("Network access attempted")), patch("socket.getaddrinfo", side_effect=AssertionError("DNS access attempted")):
            spans = detect("John Smith takes aspirin 81 mg.", detector="hybrid")
        self.assertTrue(any("presidio_spacy" in s["method"] for s in spans))

    def test_public_english_detector_unanchored_name(self):
        text = "John Smith presents with chest pain; aspirin 81 mg."
        spans = detect(text, detector="hybrid")
        names = [s for s in spans if "presidio_spacy" in s["method"]]
        self.assertTrue(any(s["start"] == 0 and s["end"] >= len("John Smith") for s in names))

    def test_hybrid_clinical_default_preservation(self):
        key = os.urandom(32)
        text = "Name: John Smith, 47-year-old male. Aspirin 81 mg, denies pain. father died from MI at 52."
        result = transform({"text": text, "context": CONTEXT, "policy": {"mode": "context_rules"}}, key=key)
        for token in ["47-year-old male", "Aspirin 81 mg", "denies pain", "father; died at 52"]:
            self.assertIn(token, result["public"]["text"])
        self.assertEqual(restore(result, key=key, context=CONTEXT), text.encode())


if __name__ == "__main__":
    unittest.main()
