"""Copy a bounded source-only dependency set from the existing workspace."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
EXCLUDED = {".venv", ".runtime", ".git", "__pycache__", "docs", "research", "evaluation", "node_modules"}
ALLOWED = {".py", ".json", ".txt", ".md", ".html", ".css", ".js", ".sh", ".cmd", ".toml", ".yml", ".yaml"}
PRIVATE_OPERATIONS = {"fetch_server_results.py", "server_refresh_web.py", "check-server-results.cmd"}

def prepare():
    copied = []
    for package in ("ClinicalTextWeb", "ClinicalTextDeid", "ClinicalCDSGateway", "ClinicalTextIntegration"):
        source = WORKSPACE / package
        if not source.is_dir():
            raise RuntimeError("Run this helper in the development workspace; the public release already contains its dependencies.")
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if not path.is_file() or path.name in PRIVATE_OPERATIONS or any(part in EXCLUDED for part in relative.parts):
                continue
            if path.suffix not in ALLOWED and path.name not in {"LICENSE", "NOTICE", ".gitignore", "py.typed", "legal_dongs.txt.gz"}:
                continue
            destination = ROOT / package / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append(destination.relative_to(ROOT).as_posix())
    manifest = json.loads((ROOT / "ClinicalTextDeid/vendor/ko-pii/MANIFEST.json").read_text(encoding="utf8"))
    for item in manifest["files"]:
        path = ROOT / "ClinicalTextDeid/vendor/ko-pii" / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], str(path)
    print(json.dumps({"source_files":len(copied),"ko_pii_integrity":"verified"}))

if __name__ == "__main__":
    prepare()
