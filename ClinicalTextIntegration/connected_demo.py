"""Actual public detector -> server review -> signed one-shot local egress.

The temporary internal vault is removed after the run. Only a public synthetic
envelope and a content-free result report are retained.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ClinicalTextDeid.api import restore
from ClinicalCDSGateway.gateway.common import Denied, digest
from ClinicalCDSGateway.gateway.deid_adapter import ClinicalTextDeidAdapter
from ClinicalCDSGateway.gateway.engine import Gateway, PAYLOAD_KEYS, verify_external
from ClinicalCDSGateway.gateway.fixtures import request


class ObservedAdapter(ClinicalTextDeidAdapter):
    def __init__(self):
        super().__init__("hybrid")
        self.calls = []

    def transform_bound(self, text, key, context):
        candidate = super().transform_bound(text, key, context)
        self.calls.append((context, candidate))
        return candidate


def denial(action, expected):
    try:
        action()
    except Denied as error:
        if error.code not in expected:
            raise AssertionError(f"Unexpected denial code: {error.code}") from None
        return error.code
    raise AssertionError("Expected denial did not occur")


def run_connected(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    checks = {}
    with TemporaryDirectory(prefix="clinical-cds-integration-") as directory:
        adapter = ObservedAdapter()
        Gateway.initialize(directory)
        gateway = Gateway(directory, adapter=adapter)
        tokens = {role: (gateway.internal / f"{role}.token").read_text(encoding="ascii")
                  for role in ["submitter", "reviewer", "exporter"]}
        source_request = request("connected-hybrid", document="connected-001")
        submission = gateway.submit(source_request, tokens["submitter"])
        if submission["state"] != "REVIEW" or submission["codes"] != ["HUMAN_APPROVAL_REQUIRED"]:
            raise AssertionError(f"Connected transformation not reviewable: {submission}")
        release = submission["release"]
        review = gateway.inspect(release, tokens["reviewer"])
        if len(adapter.calls) != 1:
            raise AssertionError("The public-code adapter was not invoked once")
        context, candidate = adapter.calls[0]
        if candidate.internal_result is None or "hybrid" not in candidate.adapter:
            raise AssertionError("Synthetic adapter substituted for public hybrid code")
        checks["real_public_hybrid_adapter_called"] = True
        checks["candidate_equals_actual_deid_output"] = candidate.text == review["candidate"]
        checks["task1_capsule_original_byte_restore"] = restore(candidate.internal_result, key=gateway.vault_key, context=context) == source_request["text"].encode("utf-8")
        checks["gateway_internal_original_byte_restore"] = gateway.restore_internal(release, tokens["reviewer"]) == source_request["text"].encode("utf-8")
        checks["no_egress_before_approval"] = not list(gateway.external.glob("*.json"))
        denied = {}
        denied["caller_approved_flag"] = denial(lambda: gateway.submit(source_request | {"approved": True}, tokens["submitter"]), {"SCHEMA_DENIED"})
        denied["submitter_approves"] = denial(lambda: gateway.approve(release, tokens["submitter"], review["text_sha256"]), {"AUTH_DENIED"})
        denied["wrong_review_hash"] = denial(lambda: gateway.approve(release, tokens["reviewer"], digest(review["candidate"] + " ")), {"REVIEW_CONTENT_MISMATCH"})
        denied["unapproved_export"] = denial(lambda: gateway.export(release, tokens["exporter"]), {"NOT_APPROVED_OR_CONSUMED"})
        gateway.approve(release, tokens["reviewer"], review["text_sha256"])

        def concurrent_export(_):
            try:
                return Gateway(directory).export(release, tokens["exporter"])
            except Denied as error:
                return {"state": "DENIED", "code": error.code}

        with ThreadPoolExecutor(max_workers=2) as pool:
            race = list(pool.map(concurrent_export, range(2)))
        checks["concurrent_one_shot"] = sum(r["state"] == "EXPORTED" for r in race) == 1 and sum(r.get("code") == "NOT_APPROVED_OR_CONSUMED" for r in race) == 1
        files = list(gateway.external.glob("*.json"))
        checks["one_actual_egress_file"] = len(files) == 1
        if not files:
            raise AssertionError("Actual egress was not written")
        actual_bytes = files[0].read_bytes()
        envelope = json.loads(actual_bytes)
        payload = verify_external(envelope, (gateway.external / "verification.key").read_bytes())
        checks["public_allowlist_only"] = set(envelope) == {"payload", "signature"} and set(payload) == PAYLOAD_KEYS
        checks["both_language_gold_names_removed"] = all(name not in payload["text"] for name in ["김민준", "John Smith"])
        checks["signed_exact_final_text"] = payload["text"] == review["candidate"] and payload["text_sha256"] == digest(review["candidate"])
        checks["no_raw_or_internal_material_in_egress"] = (
            source_request["text"].encode("utf-8") not in actual_bytes
            and all(word not in actual_bytes for word in [b'"capsule"', b'"mapping"', b'"deid_result"', b'"internal"', b'"key"'])
            and gateway.vault_key not in actual_bytes
            and all(token.encode("ascii") not in actual_bytes for token in tokens.values()))
        denied["replay"] = denial(lambda: gateway.export(release, tokens["exporter"]), {"NOT_APPROVED_OR_CONSUMED"})
        tamper_cases = [("text", payload["text"] + " ", {"EXTERNAL_HASH_FAILED"}),
                        ("purpose", "external_ai", {"EXTERNAL_SCHEMA_FAILED", "EXTERNAL_SIGNATURE_FAILED"}),
                        ("recipient", "other-research", {"EXTERNAL_SCOPE_FAILED", "EXTERNAL_SIGNATURE_FAILED"})]
        for field, value, expected in tamper_cases:
            changed = copy.deepcopy(envelope)
            changed["payload"][field] = value
            denied[f"external_{field}_tamper"] = denial(lambda e=changed: verify_external(e, (gateway.external / "verification.key").read_bytes()), expected)
        # A mixed note outside the server's reviewed synthetic corpus is submitted
        # through the same actual public-code adapter but cannot be released.
        unknown = request(text="Patient Xochitl Quispe reports 흉통. No family history of MI.", document="unknown-001")
        unsupported = gateway.submit(unknown, tokens["submitter"])
        unknown_review = gateway.inspect(unsupported["release"], tokens["reviewer"])
        denied["unregistered_mixed_note"] = denial(lambda: gateway.approve(unsupported["release"], tokens["reviewer"], unknown_review["text_sha256"]), {"REAL_TEXT_RELEASE_DISABLED", "NOT_REVIEWABLE"})
        checks["no_egress_for_unregistered_mixed_note"] = len(list(gateway.external.glob("*.json"))) == 1
        audit = gateway.audit_export(tokens["reviewer"])
        checks["audit_contains_export_and_no_phi_fields"] = (set(audit) == {"schema_version", "events"}
            and any(e["event"] == "EXPORTED" for e in audit["events"])
            and all(set(e) == {"sequence", "event", "release", "codes", "time"} for e in audit["events"]))
        (out_dir / "connected_public_example.json").write_bytes(actual_bytes)
        report = {"scope": "Local synthetic boundary; installed ko-pii + Presidio/spaCy adapter; real local file egress",
                  "accepted_request": "server-registered connected-hybrid synthetic fixture",
                  "automatic_release_of_arbitrary_notes": False,
                  "checks": checks, "denials": denied,
                  "concurrent_results": [{k: v for k, v in r.items() if k != "file"} for r in race],
                  "actual_egress_files": len(files), "public_payload_keys": sorted(payload),
                  "upstream_adapter": candidate.adapter,
                  "not_measured": ["OS or physical network isolation", "real clinical privacy adequacy", "population reidentification", "audit suffix rollback"]}
    if not all(checks.values()):
        raise AssertionError("An independent connected check failed")
    (out_dir / "connected_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("ClinicalTextIntegration/reports"))
    args = parser.parse_args()
    report = run_connected(args.out_dir)
    print(json.dumps({"checks_passed": len(report["checks"]), "denials_checked": len(report["denials"]),
                      "actual_egress_files": report["actual_egress_files"]}))


if __name__ == "__main__":
    main()
