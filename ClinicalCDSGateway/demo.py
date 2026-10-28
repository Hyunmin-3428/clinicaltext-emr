"""Synthetic end-to-end demonstration; fresh state directory required."""
import argparse
import json
from pathlib import Path

from gateway.common import canonical, strict_json
from gateway.engine import Gateway, verify_external
from gateway.fixtures import FIXTURES, request


def run(root, adapter="synthetic"):
    gateway = Gateway.initialize(root)
    if adapter != "synthetic":
        from gateway.deid_adapter import ClinicalTextDeidAdapter
        gateway.adapter = ClinicalTextDeidAdapter("hybrid" if adapter == "deid-hybrid" else "rules")
    tokens = {role: (gateway.internal / (role + ".token")).read_text()
              for role in ["submitter", "reviewer", "exporter"]}
    results = []
    names = FIXTURES if adapter == "synthetic" else ["connected-hybrid"]
    for index, name in enumerate(names):
        submitted = gateway.submit(request(name, document=f"demo-{index}"), tokens["submitter"])
        release = submitted["release"]
        review = gateway.inspect(release, tokens["reviewer"])
        gateway.approve(release, tokens["reviewer"], review["text_sha256"])
        exported = gateway.export(release, tokens["exporter"])
        verified = verify_external(strict_json(Path(exported["file"]).read_bytes()),
                                   (gateway.external / "verification.key").read_bytes())
        results.append({"fixture": name, "release": release, "state": "EXPORTED", "signature_verified": bool(verified)})
    held = gateway.submit(request(document="held", source="demo-hospital"), tokens["submitter"])
    blocked = gateway.submit(request(document="bad-recipient", recipient="remote-ai"), tokens["submitter"])
    summary = {"deployment": "local-synthetic-only", "adapter": adapter, "exports": results,
               "clinical_source": held, "unapproved_recipient": blocked,
               "external_file_count": len(list(gateway.external.glob("*.json")))}
    (gateway.root / "demo-summary.json").write_bytes(canonical(summary))
    (gateway.internal / "audit-export.json").write_bytes(canonical(gateway.audit_export(tokens["reviewer"])))
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--adapter", choices=["synthetic", "deid", "deid-hybrid"], default="synthetic")
    args = parser.parse_args()
    run(args.root, args.adapter)
