"""Verify wheel hashes against official PyPI JSON and emit a platform lock."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
records, lines = [], []
for wheel in sorted((ROOT / "wheelhouse").glob("*.whl")):
    name, version = wheel.name.split("-")[:2]
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=20) as response:
        metadata = json.load(response)
    authoritative = next(item for item in metadata["urls"] if item["filename"] == wheel.name)
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if sha != authoritative["digests"]["sha256"]:
        raise SystemExit("Wheel integrity failed")
    records.append({"name": name, "version": version, "wheel": wheel.name,
                    "sha256": sha, "primary_metadata": url, "download": authoritative["url"],
                    "license": metadata["info"].get("license_expression") or metadata["info"].get("license")})
    lines.append(f"{name}=={version} --hash=sha256:{sha}")
(ROOT / "requirements-win-py311.lock").write_text("\n".join(lines) + "\n", encoding="ascii")
(ROOT / "docs" / "SUPPLY_CHAIN_MANIFEST.json").write_text(json.dumps({"checked": "2026-10-08", "platform": "Windows AMD64 Python3.11", "wheels": records}, indent=2), encoding="utf-8")
print("Verified", len(records), "dependency wheels against PyPI")
