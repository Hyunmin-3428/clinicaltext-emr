"""Run two extraction passes and save source-offset predictions for scoring.

For each note the runner performs the locked protocol:

1. **Pass 1** -- segment the note, send each segment to the model, validate and
   collect the ``(exact_text, type)`` pairs, then ground them to source offsets
   and write ``resolved/<doc>/resolved_predictions.json``.
2. **Pass 2** -- rebuild the note with Pass-1 regions replaced by ``[PHI]``,
   run the model again over that redacted representation, map any new findings
   back to source coordinates, and union them with Pass 1. New Pass-2 findings
   are only applied for a note whose every segment finished cleanly.

The output tree mirrors what the scorer reads::

    <out>/<model_id>/resolved/<doc>/resolved_predictions.json          # Pass 1
    <out>/<model_id>/pass_2/resolved/<doc>/resolved_predictions.json   # cumulative
    <out>/<model_id>/raw_responses/<doc>/P1-*.raw.json                 # reliability/cost
    <out>/<model_id>/validation/<doc>/P1-*.validation.json
    <out>/<model_id>/pass_2/raw_responses/<doc>/P2-*.raw.json
    <out>/<model_id>/pass_2/validation/<doc>/P2-*.validation.json

The runner builds redacted text internally for Pass 2; it does not save final
redacted text files. ``--offline-stub`` runs a rule-based test substitute, which
was not used for the manuscript's LLM results.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .inference import LlamaServerClient, StubExtractor, TEMPERATURE, SEED, REASONING_EFFORT
from .provenance import inference_manifest, segment_manifest
from .pipeline import (
    fixed_segments,
    identity_map,
    merge_pairs,
    merge_predictions,
    redacted_representation_with_map,
    resolve_pairs,
    resolved_predictions_document,
    SEGMENT_OVERLAP,
    SEGMENT_SIZE,
    strict_json_loads,
    union_regions,
    validate_response,
)

DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def write_private_text(path: Path, text: str) -> None:
    """Atomically write a private file, creating a private parent directory."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as deterministic, private JSON via an atomic replacement."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    write_private_text(path, text)


def load_protocol(protocol_dir: Path) -> tuple[str, str, dict[str, Any]]:
    """Load the frozen system prompt, user template, and JSON schema."""
    system_prompt = (protocol_dir / "system_prompt.txt").read_text(encoding="utf-8")
    user_template = (protocol_dir / "user_prompt_template.txt").read_text(encoding="utf-8")
    schema = json.loads((protocol_dir / "model_output.schema.json").read_text(encoding="utf-8"))
    if user_template.count("{{CLINICAL_TEXT}}") != 1:
        raise ValueError("user_prompt_template.txt must contain exactly one {{CLINICAL_TEXT}} placeholder")
    return system_prompt, user_template, schema


def _run_segments(client, segments) -> tuple[list, list[dict[str, Any]]]:
    """Send each segment to the model; return accepted pairs and per-segment records."""
    pairs = []
    records: list[dict[str, Any]] = []
    for segment in segments:
        envelope = client.complete(segment.text)
        if not isinstance(envelope, dict):
            envelope = {
                "choices": [{"index": 0, "finish_reason": "error",
                             "message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "object": "chat.completion",
                "response_error": "non_object_envelope",
            }
        choices = envelope.get("choices")
        choice = choices[0] if (isinstance(choices, list) and choices
                                and isinstance(choices[0], dict)) else {}
        finish_reason = choice.get("finish_reason") or "error"
        message = choice.get("message")
        content_value = message.get("content") if isinstance(message, dict) else ""
        content = content_value if isinstance(content_value, str) else ""
        usage_value = envelope.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}

        usable = strict = False
        # Format usability describes the returned content, independently of why
        # generation stopped. The finish reason remains a separate operational
        # signal and is still part of the all-segments Pass-2 application gate.
        if content:
            try:
                validated = validate_response(strict_json_loads(content), segment.text)
                usable = validated["envelope_usable"]
                strict = validated["strict_schema_valid"]
                pairs.extend(validated["pairs"])
            except ValueError:
                pass  # malformed JSON -> unusable segment

        records.append({
            "segment": segment,
            "envelope": envelope,
            "finish_reason": finish_reason,
            "envelope_usable": usable,
            "strict_schema_valid": strict,
            "prompt_tokens": (usage.get("prompt_tokens")
                              if type(usage.get("prompt_tokens")) is int else 0),
            "completion_tokens": (usage.get("completion_tokens")
                                  if type(usage.get("completion_tokens")) is int else 0),
        })
    return pairs, records


def _telemetry_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Project a server response onto the non-content fields the scorer uses.

    This is an allowlist rather than a content-field denylist. Unexpected API
    fields are therefore not persisted if a server echoes part of the prompt or
    generated answer somewhere outside ``choices[].message.content``.
    """
    choices_value = envelope.get("choices")
    choice = (choices_value[0] if isinstance(choices_value, list) and choices_value
              and isinstance(choices_value[0], dict) else {})
    usage_value = envelope.get("usage")
    usage = usage_value if isinstance(usage_value, dict) else {}

    telemetry: dict[str, Any] = {
        "object": (envelope.get("object")
                   if isinstance(envelope.get("object"), str) else "chat.completion"),
        "model": envelope.get("model") if isinstance(envelope.get("model"), str) else "",
        "choices": [{
            "index": choice.get("index") if type(choice.get("index")) is int else 0,
            "finish_reason": (choice.get("finish_reason")
                              if isinstance(choice.get("finish_reason"), str) else "error"),
            "message": {"role": "assistant", "content": ""},
        }],
        "usage": {
            key: value for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type((value := usage.get(key))) is int
        },
        "content_retained": False,
    }
    for key in ("latency_seconds",):
        value = envelope.get(key)
        if type(value) in (int, float):
            telemetry[key] = value
    for key in ("response_error", "transport_error"):
        value = envelope.get(key)
        if isinstance(value, str):
            telemetry[key] = value
    return telemetry


def _persist_segments(base_dir: Path, doc_id: str, pass_number: int, records: list[dict[str, Any]],
                      strip_content: bool = True) -> None:
    """Write the raw-response and validation artifacts the scorer reads."""
    for record in records:
        scope_id = f"P{pass_number}-{record['segment'].segment_id}"
        if strip_content:
            envelope = _telemetry_envelope(record["envelope"])
        else:
            envelope = dict(record["envelope"])
            envelope["content_retained"] = True
        write_json(base_dir / "raw_responses" / doc_id / f"{scope_id}.raw.json", envelope)
        write_json(base_dir / "validation" / doc_id / f"{scope_id}.validation.json", {
            "document_id": doc_id,
            "scope_id": scope_id,
            "envelope_usable": record["envelope_usable"],
            "strict_schema_valid": record["strict_schema_valid"],
            "finish_reason": record["finish_reason"],
            "contains_literal_text": False,
        })


def process_document(client, doc_id: str, source: str, model_dir: Path,
                     segment_size: int = SEGMENT_SIZE, overlap: int = SEGMENT_OVERLAP,
                     strip_content: bool = True) -> None:
    """Run both passes for one note and write all artifacts.

    ``segment_size`` and ``overlap`` control the segmentation windows. Their
    defaults (3,500 and 400 codepoints) are the values used for the study; change
    them here or with the ``--segment-size`` / ``--overlap`` command-line flags.
    ``strip_content`` persists an allowlisted telemetry-only response by default.
    Retaining the full response requires explicit acknowledgement
    because real-data responses can quote protected health information.
    """
    # --- Pass 1: over the original note ---
    segments = fixed_segments(source, segment_size, overlap)
    write_json(model_dir / "expected_segments" / doc_id / "manifest.json",
               segment_manifest(doc_id, source, source, 1, segments, segment_size, overlap))
    pairs, records = _run_segments(client, segments)
    _persist_segments(model_dir, doc_id, 1, records, strip_content)

    pass1 = resolve_pairs(source, identity_map(source), merge_pairs(pairs))
    write_json(model_dir / "resolved" / doc_id / "resolved_predictions.json",
               resolved_predictions_document(doc_id, source, pass1))

    # --- Pass 2: over the note with Pass-1 regions blanked to [PHI] ---
    pass2_dir = model_dir / "pass_2"
    representation, coordinate_map = redacted_representation_with_map(source, union_regions(pass1))
    segments2 = fixed_segments(representation, segment_size, overlap)
    write_json(pass2_dir / "expected_segments" / doc_id / "manifest.json",
               segment_manifest(doc_id, source, representation, 2, segments2, segment_size, overlap))
    pairs2, records2 = _run_segments(client, segments2)
    _persist_segments(pass2_dir, doc_id, 2, records2, strip_content)

    # Apply new Pass-2 findings only when every Pass-2 segment finished cleanly.
    operational_complete = bool(records2) and all(
        r["finish_reason"] == "stop" and r["envelope_usable"] for r in records2)
    new_predictions = resolve_pairs(representation, coordinate_map, merge_pairs(pairs2))
    applied = new_predictions if operational_complete else []
    cumulative = merge_predictions(pass1, applied)
    write_json(pass2_dir / "resolved" / doc_id / "resolved_predictions.json",
               resolved_predictions_document(doc_id, source, cumulative))


def make_client(model_id: str, protocol_dir: Path, use_stub: bool,
                api_base: str, name_gazetteer: Optional[list[str]],
                request_timeout: Optional[float] = None):
    """Build the requested back-end: the llama-server client, or the test stub."""
    if use_stub:
        return StubExtractor(model_id=model_id, name_gazetteer=name_gazetteer)
    system_prompt, user_template, schema = load_protocol(protocol_dir)
    return LlamaServerClient(model_id, system_prompt, user_template, schema,
                             api_base=api_base, request_timeout=request_timeout)


def run_model(model_id: str, notes_dir: Path, out_dir: Path, protocol_dir: Path,
              use_stub: bool = False, api_base: str = "http://127.0.0.1:8081",
              name_gazetteer: Optional[list[str]] = None,
              segment_size: int = SEGMENT_SIZE, overlap: int = SEGMENT_OVERLAP,
              model_config: Optional[Path] = None, strip_content: bool = True,
              request_timeout: Optional[float] = None,
              overwrite_model_output: bool = False,
              allow_remote_api: bool = False,
              acknowledge_phi_risk: bool = False,
              checkpoint_files: Optional[list[Path]] = None,
              server_version: Optional[str] = None,
              server_command: Optional[list[str]] = None) -> Path:
    """Run ``model_id`` over every ``*.txt`` note in ``notes_dir``."""
    if not MODEL_ID_PATTERN.fullmatch(model_id):
        raise ValueError(
            "model_id must be one safe path component containing only letters, "
            "numbers, '.', '_', and '-'"
        )
    if not strip_content and not acknowledge_phi_risk:
        raise ValueError(
            "retaining model response content may persist PHI; also pass "
            "--acknowledge-phi-risk after confirming approved controlled storage"
        )
    parsed_api = urllib.parse.urlparse(api_base)
    api_host = parsed_api.hostname
    if not use_stub:
        if parsed_api.scheme not in {"http", "https"} or api_host is None:
            raise ValueError("api_base must be an absolute HTTP(S) URL with a hostname")
        if parsed_api.username is not None or parsed_api.password is not None:
            raise ValueError("api_base must not contain embedded credentials")
        is_loopback = api_host in {"127.0.0.1", "localhost", "::1"}
        if not is_loopback:
            if parsed_api.scheme != "https":
                raise ValueError("refusing non-loopback api_base without HTTPS")
            if not allow_remote_api:
                raise ValueError(
                    "refusing non-loopback api_base for clinical text; pass --allow-remote-api "
                    "only after confirming the endpoint and transport are institutionally approved"
                )
    notes = sorted(notes_dir.glob("*.txt"))
    if not notes:
        raise FileNotFoundError(f"no notes found in {notes_dir}")

    model_config_text: Optional[str] = None
    if model_config is not None:
        if not model_config.exists():
            raise FileNotFoundError(f"model config not found: {model_config}")
        # Read before a possible narrowly scoped overwrite in case the caller
        # supplied a config path inside the existing per-model subtree.
        model_config_text = model_config.read_text(encoding="utf-8")

    # Validate configuration and load the protocol before an explicitly
    # requested overwrite removes any prior artifacts.
    fixed_segments("", segment_size, overlap)
    client = make_client(model_id, protocol_dir, use_stub, api_base, name_gazetteer, request_timeout)
    manifest = inference_manifest(
        model_id=model_id, protocol_dir=protocol_dir, segment_size=segment_size,
        overlap=overlap, use_stub=use_stub,
        decoding={"temperature": TEMPERATURE, "seed": SEED,
                  "reasoning_effort": REASONING_EFFORT, "response_format": "json_schema"},
        request_timeout=request_timeout, strip_content=strip_content,
        checkpoint_files=checkpoint_files or [], server_version=server_version,
        server_command=server_command, name_gazetteer=name_gazetteer,
    )

    if out_dir.is_symlink():
        raise ValueError(f"refusing symlinked output directory: {out_dir}")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)

    model_dir = out_dir / model_id
    if model_dir.is_symlink():
        raise ValueError(f"refusing to use symlinked model output directory: {model_dir}")
    if model_dir.exists():
        if not overwrite_model_output:
            raise FileExistsError(
                f"model output already exists: {model_dir}; use a new output directory "
                "or pass --overwrite-model-output to replace only this model's artifacts"
            )
        if not model_dir.is_dir():
            raise ValueError(f"refusing to overwrite non-directory model output: {model_dir}")
        # The model id is constrained to one safe path component above, so this
        # removes only the explicitly selected per-model subtree.
        shutil.rmtree(model_dir)

    model_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    model_dir.chmod(0o700)

    write_json(model_dir / "inference_manifest.json", manifest)

    # Optional metadata: copy the model_config.json the scorer carries into results.
    if model_config_text is not None:
        write_private_text(model_dir / "model_config.json", model_config_text)

    for note_path in notes:
        doc_id = note_path.stem
        if not DOC_ID_PATTERN.match(doc_id):
            raise ValueError(f"unexpected note id: {doc_id!r}")
        source = note_path.read_text(encoding="utf-8")
        process_document(client, doc_id, source, model_dir, segment_size, overlap, strip_content)
        print(f"{model_id}: {doc_id} done ({len(source)} codepoints)", flush=True)

    return model_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one model over a set of notes (two passes).")
    parser.add_argument("--model-id", required=True, help="run label / model identifier")
    parser.add_argument("--notes-dir", type=Path, required=True, help="directory of <doc>.txt notes")
    parser.add_argument("--out-dir", type=Path, required=True, help="output root (per-model subdir created)")
    parser.add_argument("--protocol-dir", type=Path, default=Path("protocol"),
                        help="directory holding the frozen prompt + schema")
    parser.add_argument("--offline-stub", action="store_true",
                        help="use the offline deterministic test stub instead of llama-server")
    parser.add_argument("--api-base", default="http://127.0.0.1:8081",
                        help="llama-server base URL (ignored with --offline-stub)")
    parser.add_argument("--name-file", type=Path, default=None,
                        help="optional newline-separated name gazetteer for --offline-stub")
    parser.add_argument("--segment-size", type=int, default=SEGMENT_SIZE,
                        help=f"segmentation window width in codepoints (study default: {SEGMENT_SIZE})")
    parser.add_argument("--overlap", type=int, default=SEGMENT_OVERLAP,
                        help=f"overlap between windows in codepoints (study default: {SEGMENT_OVERLAP})")
    parser.add_argument("--model-config", type=Path, default=None,
                        help="model_config.json to copy into the output dir (metadata for the scorer)")
    parser.add_argument("--checkpoint-file", type=Path, action="append", default=None,
                        help="checkpoint to hash for this run; repeat for multiple files")
    parser.add_argument("--server-version", default=None,
                        help="recorded llama-server --version output, when known")
    parser.add_argument("--server-command", nargs=argparse.REMAINDER, default=None,
                        help="record the server launch command; must be the final option")
    raw_group = parser.add_mutually_exclusive_group()
    raw_group.add_argument(
        "--strip-raw-content", dest="strip_raw_content", action="store_true",
        help="blank model text in raw_responses (default; retained for compatibility)",
    )
    raw_group.add_argument(
        "--retain-raw-content", dest="strip_raw_content", action="store_false",
        help="retain verbatim model responses; may contain PHI and requires controlled storage",
    )
    parser.set_defaults(strip_raw_content=True)
    parser.add_argument("--request-timeout", type=float, default=None,
                        help="per-request timeout in seconds (default: none; set it to fail a hung server)")
    parser.add_argument(
        "--overwrite-model-output", action="store_true",
        help="replace only this model's existing output subtree (default: fail if it exists)",
    )
    parser.add_argument(
        "--allow-remote-api", action="store_true",
        help="allow a non-loopback model endpoint after institutional privacy/security approval",
    )
    parser.add_argument(
        "--acknowledge-phi-risk", action="store_true",
        help="required with --retain-raw-content; confirms approved controlled storage",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    gazetteer = None
    if args.name_file and args.name_file.exists():
        gazetteer = [line.strip() for line in args.name_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    model_dir = run_model(
        args.model_id, args.notes_dir, args.out_dir, args.protocol_dir,
        use_stub=args.offline_stub, api_base=args.api_base, name_gazetteer=gazetteer,
        segment_size=args.segment_size, overlap=args.overlap,
        model_config=args.model_config, strip_content=args.strip_raw_content,
        request_timeout=args.request_timeout,
        overwrite_model_output=args.overwrite_model_output,
        allow_remote_api=args.allow_remote_api,
        acknowledge_phi_risk=args.acknowledge_phi_risk,
        checkpoint_files=args.checkpoint_file, server_version=args.server_version,
        server_command=args.server_command,
    )
    print(f"wrote artifacts under {model_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
