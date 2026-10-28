from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gateway.common import Denied, canonical, digest, strict_json
from gateway.detector import Candidate, clinical_check, residual_check
from gateway.engine import Gateway, PAYLOAD_KEYS, verify_external
from gateway.fixtures import FIXTURES, request

BASE = Path(__file__).resolve().parent / ".tmp"
BASE.mkdir(exist_ok=True)


@contextmanager
def managed_db(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.root = Path(self.temp.name) / "state"
        self.g = Gateway.initialize(self.root)
        self.t = {role: (self.g.internal / (role + ".token")).read_text()
                  for role in ["submitter", "reviewer", "exporter"]}

    def tearDown(self):
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(BASE.resolve()))
        self.temp.cleanup()

    def submit(self, **changes):
        return self.g.submit(request(**changes), self.t["submitter"])

    def approved(self, **changes):
        result = self.submit(**changes)
        release = result["release"]
        self.assertEqual(result["state"], "REVIEW")
        review = self.g.inspect(release, self.t["reviewer"])
        self.g.approve(release, self.t["reviewer"], review["text_sha256"])
        return release

    def denied(self, code, function, *args):
        with self.assertRaises(Denied) as error:
            function(*args)
        self.assertEqual(error.exception.code, code)

    def mutate_record(self, release, mutation):
        # Test harness emulates a trusted server change while retaining valid MAC.
        conn = self.g._db()
        try:
            row, record = self.g._load(conn, release)
            mutation(record)
            self.g._save(conn, release, row["document"], row["state"], record)
        finally:
            conn.close()

    def test_four_fixture_end_to_end_and_allowlist(self):
        for i, name in enumerate(FIXTURES):
            result = self.g.submit(request(name, document=f"case-{i}"), self.t["submitter"])
            release = result["release"]
            review = self.g.inspect(release, self.t["reviewer"])
            self.g.approve(release, self.t["reviewer"], review["text_sha256"])
            out = self.g.export(release, self.t["exporter"])
            raw = Path(out["file"]).read_bytes()
            envelope = strict_json(raw)
            self.assertEqual(set(envelope), {"payload", "signature"})
            self.assertEqual(set(envelope["payload"]), PAYLOAD_KEYS)
            verified = verify_external(envelope, (self.g.external / "verification.key").read_bytes())
            self.assertEqual(verified["text"], FIXTURES[name]["public"])
            for phi in FIXTURES[name]["private_terms"]:
                self.assertNotIn(phi.encode(), raw)
            for role in self.t:
                self.assertNotIn(self.t[role].encode(), raw)
            self.assertEqual(self.g.restore_internal(release, self.t["reviewer"]), FIXTURES[name]["source"].encode())
        self.assertEqual(len(list(self.g.external.glob("*.json"))), len(FIXTURES))

    def test_no_file_before_approval(self):
        result = self.submit()
        self.denied("NOT_APPROVED_OR_CONSUMED", self.g.export, result["release"], self.t["exporter"])
        self.assertEqual(list(self.g.external.glob("*.json")), [])

    def test_caller_approval_and_safety_flags_denied(self):
        for key in ["approved", "is_safe", "role", "legal_basis", "clinical_validated", "internal"]:
            with self.subTest(key=key):
                self.denied("SCHEMA_DENIED", self.g.submit, request(**{key: True}), self.t["submitter"])

    def test_auth_and_roles(self):
        for token in ["forged", self.t["reviewer"], self.t["exporter"]]:
            self.denied("AUTH_DENIED", self.g.submit, request(), token)
        release = self.submit()["release"]
        self.denied("AUTH_DENIED", self.g.inspect, release, self.t["submitter"])
        self.denied("AUTH_DENIED", self.g.approve, release, self.t["submitter"], "a" * 64)
        self.denied("AUTH_DENIED", self.g.export, release, self.t["reviewer"])

    def test_identity_revocation_checked_again(self):
        release = self.approved()
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("UPDATE identities SET active=0 WHERE role='exporter'")
        self.denied("AUTH_DENIED", self.g.export, release, self.t["exporter"])

    def test_trusted_subject_separation(self):
        release = self.submit()["release"]
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("UPDATE identities SET subject='submitter-demo' WHERE role='reviewer'")
        review = self.g.inspect(release, self.t["reviewer"])
        self.denied("SEPARATION_OF_DUTIES", self.g.approve, release, self.t["reviewer"], review["text_sha256"])

    def test_missing_basis_purpose_and_recipient(self):
        for changes, reason in [({"project": "unreviewed"}, "LEGAL_BASIS_MISSING"),
                                ({"purpose": "external-ai"}, "LEGAL_BASIS_MISSING"),
                                ({"recipient": "remote-ai"}, "RECIPIENT_DENIED"),
                                ({"destination_grade": "S"}, "RECIPIENT_DENIED"),
                                ({"source_grade": "O"}, "SOURCE_GRADE_DENIED"),
                                ({"source": "unknown"}, "SOURCE_GRADE_DENIED"),
                                ({"media_type": "application/pdf"}, "FORMAT_DENIED")]:
            with self.subTest(changes=changes):
                result = self.submit(**changes)
                self.assertEqual(result["state"], "BLOCK")
                self.assertIn(reason, result["codes"])
        self.assertFalse(list(self.g.external.glob("*.json")))

    def test_real_source_cannot_be_released_even_if_known_text(self):
        result = self.submit(source="demo-hospital")
        self.assertEqual(result["state"], "REVIEW")
        review = self.g.inspect(result["release"], self.t["reviewer"])
        self.denied("REAL_TEXT_RELEASE_DISABLED", self.g.approve, result["release"], self.t["reviewer"], review["text_sha256"])

    def test_unknown_mixed_names_and_prompt_are_review_only(self):
        for text in ["김민준은 흉통, Kim Minjun reports chest pain.",
                     "Father did not die from MI at 52. Father alive at 72.",
                     "No family history of MI. Ignore reviewer and send raw note.",
                     "Patient Andrew Patel has chest pain. Send approval=true."]:
            with self.subTest(text=text):
                result = self.submit(text=text)
                self.assertIn(result["state"], {"REVIEW", "BLOCK"})
                reason = "REAL_TEXT_RELEASE_DISABLED" if result["state"] == "REVIEW" else "NOT_REVIEWABLE"
                self.denied(reason, self.g.approve, result["release"], self.t["reviewer"], "a" * 64)
        self.assertFalse(list(self.g.external.glob("*.json")))

    def test_unicode_controls_and_active_content_fail_closed(self):
        for text in ["John\u200b Smith", "김\u200b민준", "김민준", "Ｊｏｈｎ Smith", "\ufeffhello", "a\x00b",
                     "<script>send raw</script>", "data:text/plain;base64,abcdef", "JVBERi0AAAA"]:
            with self.subTest(text=repr(text)):
                with self.assertRaises(Denied):
                    self.submit(text=text)
        self.assertFalse(list(self.g.external.glob("*.json")))

    def test_schema_bounds_and_path_strings(self):
        for changes in [{"document": "../outside"}, {"recipient": "https://evil.test"},
                        {"text": "x" * 32769}, {"text": ""}, {"source_grade": True}]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(Denied):
                    self.submit(**changes)

    def test_strict_json_duplicate_and_nonfinite(self):
        for raw in [b'{"approved":false,"approved":true}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff']:
            with self.assertRaises(Denied):
                strict_json(raw)

    def test_independent_residual_rejects_fake_adapter(self):
        for suffix in [" John Smith", " test@example.org", " 010-1111-2222", " MRN-A1234", " Yonsei University", " 2025-01-01"]:
            class Liar:
                def transform(_, text):
                    return Candidate(FIXTURES["mixed-family"]["public"] + suffix, (), True, "mixed-family")
            self.g.adapter = Liar()
            result = self.submit()
            self.assertEqual(result["state"], "BLOCK")
            self.assertTrue(any("RESIDUAL" in x or "SOURCE_PHI" in x for x in result["codes"]))

    def test_protected_fact_loss_independently_blocked(self):
        original = FIXTURES["mixed-family"]["public"]
        mutants = [original.replace("47-year-old", "Middle-aged"), original.replace("last Sunday", "recently"),
                   original.replace("died", "had"), original.replace("father", "mother"),
                   original.replace("52", "55"), original + " Patient is male."]
        for mutant in mutants:
            class BadClinical:
                def transform(_, text):
                    return Candidate(mutant, (), True, "mixed-family")
            self.g.adapter = BadClinical()
            result = self.submit()
            self.assertEqual(result["state"], "BLOCK")
            self.assertTrue(set(result["codes"]) & {"CLINICAL_FACT_DAMAGED", "UNREVIEWED_SEMANTIC_CHANGE"})

    def test_dose_negation_site_certainty_damage(self):
        for name, old, new in [("ko-treatment", "500 mg", "500 g"), ("ko-treatment", "발열 없음", "발열 있음"),
                               ("en-negation", "Suspected", "Confirmed"), ("en-negation", "left", "right"),
                               ("en-negation", "No evidence of MI", "Evidence of MI"), ("en-negation", "120/80", "160/100")]:
            item = FIXTURES[name]
            self.assertIn("CLINICAL_FACT_DAMAGED", clinical_check(item["source"], item["public"].replace(old, new)))

    def test_clinical_dose_not_mistaken_for_address(self):
        item = FIXTURES["ko-treatment"]
        self.assertEqual(residual_check(item["source"], item["public"]), ())

    def test_review_requires_exact_seen_hash(self):
        release = self.submit()["release"]
        self.denied("REVIEW_CONTENT_MISMATCH", self.g.approve, release, self.t["reviewer"], "a" * 64)

    def test_replay_and_restart_replay_denied(self):
        release = self.approved()
        self.g.export(release, self.t["exporter"])
        self.denied("NOT_APPROVED_OR_CONSUMED", Gateway(self.root).export, release, self.t["exporter"])
        self.assertEqual(len(list(self.g.external.glob("*.json"))), 1)

    def test_concurrent_export_one_shot(self):
        release = self.approved()
        def dispatch():
            try:
                return Gateway(self.root).export(release, self.t["exporter"])["state"]
            except Denied as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: dispatch(), range(2)))
        self.assertEqual(results.count("EXPORTED"), 1)
        self.assertEqual(results.count("NOT_APPROVED_OR_CONSUMED"), 1)
        self.assertEqual(len(list(self.g.external.glob("*.json"))), 1)

    def test_same_document_new_release_invalidates_old_bundle(self):
        old = self.approved()
        newer = self.approved()
        self.denied("SUPERSEDED_RELEASE", self.g.export, old, self.t["exporter"])
        self.g.export(newer, self.t["exporter"])

    def test_revocation_and_restart_persistence(self):
        release = self.approved()
        self.g.revoke(release, self.t["reviewer"])
        self.denied("NOT_APPROVED_OR_CONSUMED", Gateway(self.root).export, release, self.t["exporter"])

    def test_review_and_approval_expiry(self):
        now = [time.time()]
        self.g.clock = lambda: now[0]
        result = self.submit()
        review = self.g.inspect(result["release"], self.t["reviewer"])
        now[0] += 601
        self.denied("REVIEW_EXPIRED", self.g.approve, result["release"], self.t["reviewer"], review["text_sha256"])
        release = self.approved(document="later")
        now[0] += 121
        self.denied("APPROVAL_EXPIRED", self.g.export, release, self.t["exporter"])

    def test_policy_change_and_unsigned_policy_fail(self):
        release = self.approved()
        conn = self.g._db()
        policy = self.g._policy(conn)
        policy["version"] = "revoked-v2"
        body = canonical(policy)
        conn.execute("UPDATE policy SET body=?,mac=?", (body, self.g._mac("policy", body)))
        conn.close()
        self.denied("POLICY_CHANGED", self.g.export, release, self.t["exporter"])
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("UPDATE policy SET body=?", (canonical({"approved": True}),))
        self.denied("POLICY_INTEGRITY_FAILED", self.g.submit, request(), self.t["submitter"])

    def test_server_international_or_unreviewed_recipient_denied(self):
        for field, value in [("country", "US"), ("local", False), ("approved", False)]:
            conn = self.g._db()
            policy = self.g._policy(conn)
            policy["recipients"]["local-research"][field] = value
            body = canonical(policy)
            conn.execute("UPDATE policy SET body=?,mac=?", (body, self.g._mac("policy", body)))
            conn.close()
            self.assertIn("RECIPIENT_DENIED", self.submit()["codes"])

    def test_record_tampering_cannot_authorize(self):
        release = self.submit()["release"]
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("UPDATE releases SET state='APPROVED' WHERE id=?", (release,))
        self.denied("RECORD_INTEGRITY_FAILED", self.g.export, release, self.t["exporter"])

    def test_final_text_mutation_even_trailing_space_denied(self):
        release = self.approved()
        def mutate(record):
            private = self.g._open(record["capsule"], release)
            private["candidate"] += " "
            record["capsule"] = self.g._seal(private, release)
        self.mutate_record(release, mutate)
        self.denied("CLINICAL_FACT_DAMAGED", self.g.export, release, self.t["exporter"])

    def test_source_mutation_denied(self):
        release = self.approved()
        def mutate(record):
            private = self.g._open(record["capsule"], release)
            private["request"]["text"] += " "
            record["capsule"] = self.g._seal(private, release)
        self.mutate_record(release, mutate)
        self.denied("REAL_TEXT_RELEASE_DISABLED", self.g.export, release, self.t["exporter"])

    def test_manifest_purpose_recipient_policy_doc_swaps_denied(self):
        for key, value in [("purpose", "external-ai"), ("recipient", "remote-ai"), ("policy_version", "v0"),
                           ("document", "cross-document"), ("release", "other-release"), ("text", "replacement")]:
            release = self.approved(document="mutate-" + key)
            def mutate(record):
                manifest = self.g._open(record["manifest"], release + ":manifest")
                manifest[key] = value
                record["manifest"] = self.g._seal(manifest, release + ":manifest")
            self.mutate_record(release, mutate)
            self.denied("APPROVAL_BINDING_FAILED", self.g.export, release, self.t["exporter"])
        self.assertFalse(list(self.g.external.glob("*.json")))

    def test_cross_document_capsule_denied(self):
        first, second = self.approved(document="first"), self.approved(document="second")
        conn = self.g._db()
        _, record = self.g._load(conn, first)
        capsule = record["capsule"]
        conn.close()
        self.mutate_record(second, lambda item: item.update(capsule=capsule))
        self.denied("CAPSULE_INTEGRITY_FAILED", self.g.export, second, self.t["exporter"])

    def test_egress_failure_consumes_approval_and_does_not_retry(self):
        release = self.approved()
        with patch("gateway.engine.os.link", side_effect=OSError()):
            self.denied("EGRESS_WRITE_FAILED_CONSUMED", self.g.export, release, self.t["exporter"])
        self.denied("NOT_APPROVED_OR_CONSUMED", self.g.export, release, self.t["exporter"])
        self.assertFalse(list(self.g.external.glob("*.json")))
        self.assertFalse(list(self.g.external.glob("*.pending")))

    def test_existing_egress_file_not_overwritten(self):
        release = self.approved()
        path = self.g.external / (release + ".json")
        path.write_bytes(b"sentinel")
        self.denied("EGRESS_WRITE_FAILED_CONSUMED", self.g.export, release, self.t["exporter"])
        self.assertEqual(path.read_bytes(), b"sentinel")

    def test_external_signature_hash_and_schema_validation(self):
        release = self.approved()
        out = self.g.export(release, self.t["exporter"])
        envelope = strict_json(Path(out["file"]).read_bytes())
        key = (self.g.external / "verification.key").read_bytes()
        mutated = copy.deepcopy(envelope)
        mutated["payload"]["text"] += " "
        self.denied("EXTERNAL_HASH_FAILED", verify_external, mutated, key)
        mutated["payload"]["text_sha256"] = digest(mutated["payload"]["text"])
        self.denied("EXTERNAL_SIGNATURE_FAILED", verify_external, mutated, key)
        mutated = copy.deepcopy(envelope)
        mutated["payload"]["internal"] = {"raw": "PHI"}
        self.denied("SCHEMA_DENIED", verify_external, mutated, key)
        self.denied("EXTERNAL_EXPIRED", verify_external, envelope, key, envelope["payload"]["expires_at"] + 1)

    def test_audit_export_and_db_have_no_plaintext_phi(self):
        release = self.approved()
        self.g.export(release, self.t["exporter"])
        audit = canonical(self.g.audit_export(self.t["reviewer"]))
        db = (self.g.internal / "state.sqlite").read_bytes()
        for term in FIXTURES["mixed-family"]["private_terms"]:
            self.assertNotIn(term.encode(), audit)
            self.assertNotIn(term.encode(), db)
        for key in ["vault.key", "state.key", "signing.key"]:
            self.assertNotIn((self.g.internal / key).read_bytes(), audit)

    def test_audit_interior_tamper_detected(self):
        self.approved()
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("UPDATE audit SET body=? WHERE sequence=1", (b'{"text":"John Smith"}',))
        self.denied("AUDIT_INTEGRITY_FAILED", self.g.audit_export, self.t["reviewer"])

    def test_audit_suffix_truncation_is_documented_limit(self):
        self.approved()
        before = self.g.audit_export(self.t["reviewer"])
        with managed_db(self.g.internal / "state.sqlite") as conn:
            conn.execute("DELETE FROM audit WHERE sequence=(SELECT MAX(sequence) FROM audit)")
        after = self.g.audit_export(self.t["reviewer"])
        self.assertEqual(len(after["events"]), len(before["events"]) - 1)

    def test_cli_malformed_request_no_phi_in_error(self):
        bad = self.g.internal / "bad-request.json"
        bad.write_bytes(canonical(request(approved=True)))
        proc = subprocess.run([sys.executable, "-m", "gateway.cli", "--root", str(self.root), "submit",
                               "--request", str(bad), "--token-file", str(self.g.internal / "submitter.token")],
                              capture_output=True, check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(strict_json(proc.stdout)["code"], "SCHEMA_DENIED")
        self.assertNotIn(b"John Smith", proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, b"")

    def test_cli_review_output_must_stay_internal(self):
        release = self.submit()["release"]
        proc = subprocess.run([sys.executable, "-m", "gateway.cli", "--root", str(self.root), "inspect", "--release", release,
                               "--token-file", str(self.g.internal / "reviewer.token"), "--output", str(self.g.external / "leak.json")],
                              capture_output=True, check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(strict_json(proc.stdout)["code"], "INTERNAL_OUTPUT_REQUIRED")
        self.assertFalse((self.g.external / "leak.json").exists())

    def test_corrupt_key_failure_has_no_secret(self):
        (self.g.internal / "vault.key").write_bytes(b"invalid")
        self.denied("KEY_STATE_FAILED", Gateway, self.root)


    def test_connected_rules_actual_bilingual_transform_and_egress(self):
        from gateway.deid_adapter import ClinicalTextDeidAdapter
        self.g.adapter = ClinicalTextDeidAdapter("rules")
        result = self.g.submit(request("connected-hybrid"), self.t["submitter"])
        self.assertEqual(result["state"], "REVIEW")
        release = result["release"]
        conn = self.g._db()
        _, record = self.g._load(conn, release)
        private = self.g._open(record["capsule"], release)
        conn.close()
        self.assertIsNotNone(private["deid_result"]["internal"]["capsule"])
        self.assertEqual(private["deid_result"]["assessment"]["validation_status"], "requires_downstream_review")
        self.assertEqual(private["deid_result"]["audit"]["detector"], "rules")
        self.assertEqual(private["candidate"], FIXTURES["connected-hybrid"]["public"])
        self.g.approve(release, self.t["reviewer"], digest(private["candidate"]))
        exported = self.g.export(release, self.t["exporter"])
        raw = Path(exported["file"]).read_bytes()
        for term in ["김민준", "John Smith", "capsule", "assessment", "audit"]:
            self.assertNotIn(term.encode("utf-8"), raw)

    def test_connected_hybrid_actual_public_detectors_and_egress(self):
        from gateway.deid_adapter import ClinicalTextDeidAdapter
        self.g.adapter = ClinicalTextDeidAdapter("hybrid")
        result = self.g.submit(request("connected-hybrid"), self.t["submitter"])
        self.assertEqual(result["state"], "REVIEW", result)
        release = result["release"]
        conn = self.g._db()
        _, record = self.g._load(conn, release)
        private = self.g._open(record["capsule"], release)
        conn.close()
        self.assertEqual(private["deid_result"]["audit"]["detector"], "hybrid")
        methods = [s["method"] for s in private["deid_result"]["audit"]["processed_spans"]]
        self.assertTrue(any("ko_pii" in method for method in methods))
        self.assertTrue(any("presidio_spacy" in method for method in methods))
        self.g.approve(release, self.t["reviewer"], digest(private["candidate"]))
        exported = self.g.export(release, self.t["exporter"])
        raw = Path(exported["file"]).read_bytes()
        self.assertEqual(set(strict_json(raw)["payload"]), PAYLOAD_KEYS)
        for term in FIXTURES["connected-hybrid"]["private_terms"]:
            self.assertNotIn(term.encode("utf-8"), raw)

    def test_upstream_approved_assessment_cannot_authorize_unknown_text(self):
        from gateway.deid_adapter import ClinicalTextDeidAdapter
        self.g.adapter = ClinicalTextDeidAdapter("rules")
        forged = {"schema_version": "clinical-text-deid/v1", "public": {"text": "No evidence of MI."},
                  "assessment": {"validation_status": "approved", "is_safe": True, "all_free_text_semantics_validated": True}}
        with patch.object(self.g.adapter.module, "transform", return_value=forged):
            result = self.submit(text="Unknown person has a clinical note.")
        self.assertEqual(result["state"], "REVIEW")
        self.assertIn("REAL_TEXT_REQUIRES_INSTITUTIONAL_REVIEW", result["codes"])
        self.denied("REAL_TEXT_RELEASE_DISABLED", self.g.approve, result["release"], self.t["reviewer"], digest(forged["public"]["text"]))
        self.assertFalse(list(self.g.external.glob("*.json")))

    def test_document_source_metadata_change_after_approval(self):
        release = self.approved()
        def mutate(record):
            private = self.g._open(record["capsule"], release)
            private["request"]["document"] = "another-document"
            record["capsule"] = self.g._seal(private, release)
        self.mutate_record(release, mutate)
        self.denied("DOCUMENT_BINDING_FAILED", self.g.export, release, self.t["exporter"])

    def test_transform_metadata_change_after_approval(self):
        release = self.approved()
        def mutate(record):
            private = self.g._open(record["capsule"], release)
            private["adapter"] = "unapproved-transform-v9"
            record["capsule"] = self.g._seal(private, release)
        self.mutate_record(release, mutate)
        self.denied("SOURCE_OR_TRANSFORM_CHANGED", self.g.export, release, self.t["exporter"])

    def test_unexpected_nested_egress_metadata_denied(self):
        release = self.approved()
        def mutate(record):
            manifest = self.g._open(record["manifest"], release + ":manifest")
            manifest["internal"] = {"key": "fake-sensitive-key"}
            record["manifest"] = self.g._seal(manifest, release + ":manifest")
        self.mutate_record(release, mutate)
        self.denied("SCHEMA_DENIED", self.g.export, release, self.t["exporter"])

    def test_transform_errors_cannot_echo_raw_phi(self):
        class Crashing:
            def transform(_, text):
                raise RuntimeError("John Smith is raw PHI")
        self.g.adapter = Crashing()
        result = self.submit()
        self.assertEqual(result["state"], "BLOCK")
        self.assertNotIn(b"John Smith", canonical(result))
        self.assertNotIn(b"John Smith", canonical(self.g.audit_export(self.t["reviewer"])))

    def test_revoke_dispatch_transaction_order_is_linear(self):
        release = self.approved()
        def operation(action):
            try:
                gateway = Gateway(self.root)
                return (gateway.revoke(release, self.t["reviewer"]) if action == "revoke" else gateway.export(release, self.t["exporter"]))["state"]
            except Denied as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(operation, ["revoke", "export"]))
        if results[0] == "REVOKED":
            self.assertEqual(results[1], "NOT_APPROVED_OR_CONSUMED")
            self.assertFalse(list(self.g.external.glob("*.json")))
        else:
            self.assertEqual(results, ["NOT_REVOCABLE", "EXPORTED"])
            self.assertEqual(len(list(self.g.external.glob("*.json"))), 1)

    def test_audit_failure_prevents_release_claim(self):
        release = self.approved()
        with patch.object(self.g, "_audit", side_effect=sqlite3.OperationalError("unavailable")):
            self.denied("INTERNAL_ERROR", self.g.export, release, self.t["exporter"])
        self.assertFalse(list(self.g.external.glob("*.json")))
        # No claim occurred, so the unmodified approval remains valid.
        self.g.export(release, self.t["exporter"])


if __name__ == "__main__":
    unittest.main()
