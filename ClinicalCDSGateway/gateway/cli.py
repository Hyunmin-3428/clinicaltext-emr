import argparse
import json
from pathlib import Path
import sys

from .common import Denied, canonical, strict_json
from .engine import Gateway, verify_external


def internal_write(root, output, data):
    output = Path(output).absolute()
    internal = (Path(root).absolute() / "internal").resolve()
    if output.resolve() != output or internal not in output.parents:
        raise Denied("INTERNAL_OUTPUT_REQUIRED")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as file:
        file.write(data)


def main():
    parser = argparse.ArgumentParser(description="Local synthetic-only clinical release gateway")
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    submit = sub.add_parser("submit")
    submit.add_argument("--request", required=True)
    submit.add_argument("--token-file", required=True)
    submit.add_argument("--adapter", choices=["synthetic", "deid", "deid-hybrid"], default="synthetic")
    for command in ["inspect", "approve", "export", "revoke", "restore"]:
        item = sub.add_parser(command)
        item.add_argument("--release", required=True)
        item.add_argument("--token-file", required=True)
        if command == "approve":
            item.add_argument("--expected-text-sha256", required=True)
        if command in ["inspect", "restore"]:
            item.add_argument("--output", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--token-file", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            Gateway.initialize(args.root)
            result = {"state": "INITIALIZED", "deployment": "local-synthetic-only"}
        else:
            adapter = None
            if getattr(args, "adapter", None) in {"deid", "deid-hybrid"}:
                from .deid_adapter import ClinicalTextDeidAdapter
                adapter = ClinicalTextDeidAdapter("hybrid" if args.adapter == "deid-hybrid" else "rules")
            gateway = Gateway(args.root, adapter=adapter)
            token = Path(args.token_file).read_text(encoding="ascii").strip() if hasattr(args, "token_file") else None
            if args.command == "submit":
                result = gateway.submit(strict_json(Path(args.request).read_bytes()), token)
            elif args.command == "inspect":
                reviewed = gateway.inspect(args.release, token)
                internal_write(args.root, args.output, canonical(reviewed))
                result = {"state": "INTERNAL_REVIEW_SAVED", "release": args.release}
            elif args.command == "approve":
                result = gateway.approve(args.release, token, args.expected_text_sha256)
            elif args.command == "export":
                result = gateway.export(args.release, token)
            elif args.command == "revoke":
                result = gateway.revoke(args.release, token)
            elif args.command == "restore":
                internal_write(args.root, args.output, gateway.restore_internal(args.release, token))
                result = {"state": "INTERNAL_ORIGINAL_SAVED", "release": args.release}
            elif args.command == "audit":
                result = gateway.audit_export(token)
            elif args.command == "verify":
                payload = verify_external(strict_json(Path(args.file).read_bytes()),
                                          (gateway.external / "verification.key").read_bytes())
                result = {"state": "SIGNATURE_VERIFIED", "release": payload["release"]}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Denied as exc:
        print(json.dumps({"state": "DENIED", "code": exc.code}))
        return 2
    except Exception:
        # Never echo exceptions, file contents, tokens, paths from malformed input.
        print(json.dumps({"state": "DENIED", "code": "INTERNAL_ERROR"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
