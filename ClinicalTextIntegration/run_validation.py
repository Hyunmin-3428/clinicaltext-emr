"""Run the authorized local synthetic checks and capture a fixed-code manifest."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .connected_demo import run_connected
from .evaluate_deid import evaluate

WORKSPACE = Path(__file__).resolve().parents[1]
REPORTS = WORKSPACE / "ClinicalTextIntegration" / "reports"


def snapshot():
    files = []
    for directory in ["ClinicalTextDeid", "ClinicalCDSGateway/gateway", "ClinicalCDSGateway/tests", "ClinicalTextIntegration"]:
        for path in (WORKSPACE / directory).glob("*.py"):
            files.append(path)
    files.extend((WORKSPACE / "ClinicalTextDeid/tests").glob("*.py"))
    for name in ["ClinicalTextDeid/requirements.txt", "ClinicalTextDeid/vendor/RUNTIME_MANIFEST.json", "ClinicalTextDeid/vendor/ko-pii/MANIFEST.json", "ClinicalCDSGateway/requirements.txt"]:
        if (WORKSPACE / name).is_file():
            files.append(WORKSPACE / name)
    return {str(path.relative_to(WORKSPACE)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(files))}


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    suite_specs = [
        ("task1", WORKSPACE, WORKSPACE / "ClinicalTextDeid/.venv/Scripts/python.exe", ["-m", "unittest", "discover", "-s", "ClinicalTextDeid/tests", "-v"]),
        ("task2", WORKSPACE / "ClinicalCDSGateway", WORKSPACE / "ClinicalCDSGateway/.venv/Scripts/python.exe", ["-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("independent", WORKSPACE, Path(sys.executable), ["-m", "unittest", "ClinicalTextIntegration.test_independent", "-v"]),
    ]
    suites = []
    for name, cwd, executable, args in suite_specs:
        started = time.monotonic()
        proc = subprocess.run([str(executable), *args], cwd=cwd, capture_output=True, timeout=120,
                              env=os.environ | {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        (REPORTS / (name + "_tests.txt")).write_text(output.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        match = re.search(r"Ran (\d+) tests? in", output)
        skip_match = re.search(r"\bskipped=(\d+)", output)
        tests = int(match[1]) if match else None
        skipped = int(skip_match[1]) if skip_match else 0
        result = {"suite": name, "exit_code": proc.returncode, "tests": int(match[1]) if match else None,
                  "passed": tests - skipped if proc.returncode == 0 and tests is not None else 0,
                  "skipped": skipped,
                  "seconds": round(time.monotonic() - started, 3), "log": name + "_tests.txt"}
        suites.append(result)
        print(json.dumps(result), flush=True)
        if proc.returncode:
            break
    successful = len(suites) == len(suite_specs) and all(s["exit_code"] == 0 for s in suites)
    if successful:
        connected = run_connected(REPORTS)
        independent = evaluate("hybrid")
        (REPORTS / "deid_independent.json").write_text(json.dumps(independent, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        connected = independent = None
    after = snapshot()
    manifest = {"scope": "Synthetic local validation; no real patients or remote AI", "suites": suites,
                "tests_run": sum(s["tests"] or 0 for s in suites),
                "tests_passed": sum(s["passed"] for s in suites),
                "tests_skipped": sum(s["skipped"] for s in suites),
                "stable_code_snapshot": before == after, "code_snapshot": after,
                "successful": successful and before == after,
                "connected_checks": len(connected["checks"]) if connected else None,
                "connected_denials": len(connected["denials"]) if connected else None,
                "independent_notes": independent["notes"] if independent else None}
    (REPORTS / "validation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ["successful", "tests_run", "tests_passed", "tests_skipped", "stable_code_snapshot", "connected_checks", "connected_denials", "independent_notes"]}))
    return 0 if manifest["successful"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
