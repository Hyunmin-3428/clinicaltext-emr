"""Run settings and expected request records; no clinical text is stored here."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

PROTOCOL_FILES = ("system_prompt.txt", "user_prompt_template.txt", "model_output.schema.json")
SEGMENT_SCHEMA = "expected-segments-v1"


def file_sha256(path: Path) -> str:
    """Hash a file without loading model weights into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checkpoint_records(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Record supplied checkpoint bytes, including standard numbered GGUF shards."""
    files: dict[Path, None] = {}
    for path in paths:
        match = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf", path.name)
        if match:
            prefix, index, total = match.groups()
            if not 1 <= int(index) <= int(total):
                raise ValueError(f"invalid GGUF shard name: {path.name}")
            parts = [path.with_name(f"{prefix}-{n:05d}-of-{int(total):05d}.gguf")
                     for n in range(1, int(total) + 1)]
        else:
            parts = [path]
        for part in parts:
            if not part.is_file():
                raise FileNotFoundError(f"checkpoint file not found: {part}")
            files[part.resolve()] = None
    return [{"filename": path.name, "size_bytes": path.stat().st_size,
             "sha256": file_sha256(path)} for path in files]


def git_revision(root: Path) -> Optional[str]:
    """Return this checkout's revision, or None for an installed/exported copy."""
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if re.fullmatch(r"[0-9a-f]{40,64}", value) else None


def inference_manifest(*, model_id: str, protocol_dir: Path, segment_size: int,
                       overlap: int, use_stub: bool, decoding: Optional[dict[str, Any]],
                       request_timeout: Optional[float], strip_content: bool,
                       checkpoint_files: Sequence[Path] = (),
                       server_version: Optional[str] = None,
                       server_command: Optional[Sequence[str]] = None,
                       name_gazetteer: Optional[Sequence[str]] = None) -> dict[str, Any]:
    """Describe this run, not the original study's unavailable runtime details.

    Checkpoint paths and server details are optional caller-supplied provenance.
    They do not independently prove which bytes an existing endpoint loaded.
    No environment variables, source notes, or model responses are copied.
    The explicit server command can contain local paths or credentials; do not
    supply secrets as provenance, and keep manifests in controlled storage.
    """
    package = Path(__file__).resolve().parent
    protocol = ({name: file_sha256(protocol_dir / name) for name in PROTOCOL_FILES}
                if not use_stub else None)
    return {
        "schema_version": "inference-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": model_id,
        "backend": "offline-stub" if use_stub else "llama-server",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "repository_commit": git_revision(package.parents[1]),
        "code_sha256": {path.name: file_sha256(path) for path in sorted(package.glob("*.py"))},
        "segmentation": {"size": segment_size, "overlap": overlap,
                         "offset_unit": "Unicode codepoint; half-open [start,end)"},
        "protocol_file_sha256": protocol,
        "decoding": decoding if not use_stub else None,
        "request_timeout_seconds": request_timeout if not use_stub else None,
        "raw_content_retained": not strip_content,
        "checkpoint_files": checkpoint_records(checkpoint_files),
        "server": {"version": server_version or None,
                   "command": list(server_command) if server_command else None,
                   "provenance": "caller supplied; launcher records its command and version output"},
        "stub_name_list_sha256": (hashlib.sha256(
            json.dumps(list(name_gazetteer or []), ensure_ascii=False).encode("utf-8")
        ).hexdigest() if use_stub else None),
    }


def segment_manifest(doc_id: str, source: str, representation: str, pass_number: int,
                     segments: Sequence[Any], segment_size: int, overlap: int) -> dict[str, Any]:
    """List every expected segment before issuing requests for a note/pass."""
    return {
        "schema_version": SEGMENT_SCHEMA,
        "document_id": doc_id,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "pass_number": pass_number,
        "representation_length": len(representation),
        "segment_size": segment_size,
        "overlap": overlap,
        "segments": [{"scope_id": f"P{pass_number}-{seg.segment_id}",
                      "start": seg.start, "end": seg.end} for seg in segments],
    }


def expected_scopes(base_dir: Path, doc_id: str, pass_number: int,
                    source_sha256: Optional[str] = None) -> Optional[set[str]]:
    """Read and check the saved plan. A missing legacy plan means unknown coverage."""
    path = base_dir / "expected_segments" / doc_id / "manifest.json"
    if not path.exists():
        return None
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("not an object")
        if (plan.get("schema_version") != SEGMENT_SCHEMA
                or plan.get("document_id") != doc_id
                or type(plan.get("pass_number")) is not int
                or plan["pass_number"] != pass_number):
            raise ValueError("identity mismatch")
        source_hash = plan.get("source_sha256")
        if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ValueError("invalid source hash")
        if source_sha256 is not None and source_hash != source_sha256:
            raise ValueError("source hash mismatch")
        length, size, overlap = (plan.get(key) for key in
                                 ("representation_length", "segment_size", "overlap"))
        if (any(type(value) is not int for value in (length, size, overlap))
                or length < 0 or size <= 0 or not 0 <= overlap < size):
            raise ValueError("invalid segmentation parameters")
        expected = []
        start = 0
        while True:
            end = min(start + size, length)
            expected.append({"scope_id": f"P{pass_number}-S{len(expected) + 1:04d}",
                             "start": start, "end": end})
            if end == length:
                break
            start = end - overlap
        if plan.get("segments") != expected:
            raise ValueError("segment list does not match the recorded windowing")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid expected-segment manifest: {path}") from exc
    return {item["scope_id"] for item in expected}
