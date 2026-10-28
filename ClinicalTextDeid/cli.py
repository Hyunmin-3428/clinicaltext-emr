"""Offline CLI: public allowlist and encrypted internal result in separate files."""
import argparse
import json
import os
from pathlib import Path
import sys

from .api import transform, restore, public_payload


def _read_json(path):
    return json.loads(Path(path).read_bytes().decode("utf-8-sig"))


def _check_destinations(outputs, protected):
    resolved = [Path(p).resolve() for p in outputs]
    if len(set(resolved)) != len(resolved) or set(resolved) & {Path(p).resolve() for p in protected}:
        raise ValueError("Output paths must be distinct from inputs and key")
    if any(p.exists() for p in resolved):
        raise ValueError("Refusing to overwrite an existing output")
    return resolved


def _write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, no accidental overwrite, restrictive POSIX mode where supported.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic-only local prototype; never a release approval")
    sub = parser.add_subparsers(dest="command", required=True)
    trans = sub.add_parser("transform")
    trans.add_argument("--input", required=True)
    trans.add_argument("--public-out", required=True)
    trans.add_argument("--internal-out", required=True)
    trans.add_argument("--key-file", required=True)
    undo = sub.add_parser("restore")
    undo.add_argument("--input", required=True)
    undo.add_argument("--output", required=True)
    undo.add_argument("--key-file", required=True)
    undo.add_argument("--context-file", required=True)
    args = parser.parse_args(argv)
    try:
        key = Path(args.key_file).read_bytes()
        if args.command == "transform":
            public_path, internal_path = _check_destinations([args.public_out, args.internal_out], [args.input, args.key_file])
            if internal_path.parent.is_relative_to(public_path.parent) or public_path.parent.is_relative_to(internal_path.parent):
                raise ValueError("Use separate public and internal directories")
            if not Path(args.key_file).resolve().is_relative_to(internal_path.parent):
                raise ValueError("Key must be held inside the internal directory")
            result = transform(_read_json(args.input), key=key)
            _write_new(internal_path, json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
            _write_new(public_path, json.dumps(public_payload(result), ensure_ascii=False, indent=2).encode("utf-8"))
            print(json.dumps({"status": "created", "validation_status": "requires_downstream_review"}))
        else:
            output, = _check_destinations([args.output], [args.input, args.key_file, args.context_file])
            private_dir = Path(args.input).resolve().parent
            if not output.is_relative_to(private_dir):
                raise ValueError("Restored plaintext must stay in the internal directory")
            raw = restore(_read_json(args.input), key=key, context=_read_json(args.context_file))
            _write_new(output, raw)
            print(json.dumps({"status": "restored_to_internal_file"}))
    except Exception:
        # Never echo input/PHI, crypto data or arbitrary exception messages into logs.
        print(json.dumps({"status": "failed", "reason": "invalid_input_dependency_authentication_or_output_path"}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
