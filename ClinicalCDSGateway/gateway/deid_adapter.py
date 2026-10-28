"""Read-only optional connection to the sibling ClinicalTextDeid contract.

The sibling assessment is retained internally, never used as approval evidence.
Rules mode avoids model downloads and heavyweight dependencies.
"""
import importlib
from pathlib import Path
import sys
import subprocess

from .common import Denied, canonical, digest, exact_schema, plain_text, strict_json
from .detector import Candidate
from .fixtures import find_fixture


class ClinicalTextDeidAdapter:
    def __init__(self, detector="rules"):
        if detector not in {"rules", "hybrid"}:
            raise Denied("DEID_DETECTOR_DENIED")
        self.detector = detector
        self.runner = None
        if detector == "hybrid":
            # Use the already-installed sibling environment; no models downloaded.
            sibling_python = Path(__file__).resolve().parents[2] / "ClinicalTextDeid" / ".venv" / "Scripts" / "python.exe"
            self.runner = sibling_python if sibling_python.is_file() else Path(sys.executable)
            return
        parent = str(Path(__file__).resolve().parents[2])
        if parent not in sys.path:
            sys.path.insert(0, parent)
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            self.module = importlib.import_module("ClinicalTextDeid.api")
        except Exception:
            raise Denied("DEID_ADAPTER_UNAVAILABLE") from None
        finally:
            sys.dont_write_bytecode = previous

    def transform_bound(self, text, key, context):
        # Direct mode retains exact protected phrases. Clinical context rewrite is
        # an independently reviewed future policy, not enabled via caller flags.
        request = {"text": text, "context": context,
            "policy": {"mode": "direct", "preserve_numeric_age": True, "preserve_exercise_time": True},
            "detector": self.detector}
        if self.runner:
            import base64
            try:
                proc = subprocess.run([str(self.runner), "-I", str(Path(__file__).with_name("deid_runner.py"))],
                    input=canonical({"request": request, "key": base64.b64encode(key).decode("ascii")}),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False)
                if proc.returncode != 0 or len(proc.stdout) > 70000:
                    raise Denied("DEID_EXECUTION_FAILED")
                result = strict_json(proc.stdout)
            except (OSError, subprocess.TimeoutExpired):
                raise Denied("DEID_EXECUTION_FAILED") from None
        else:
            result = self.module.transform(request, key=key)
        if result.get("schema_version") != "clinical-text-deid/v1":
            raise Denied("DEID_SCHEMA_CHANGED")
        exact_schema(result["public"], {"text"})
        plain_text(result["public"]["text"])
        fixture_id, fixture = find_fixture(text)
        supported = fixture is not None and fixture["public"] == result["public"]["text"]
        # This narrow support decision uses server-authored expected bytes, never
        # result.assessment, validation_status or an upstream approval field.
        fingerprint = digest((Path(__file__).resolve().parents[2] / "ClinicalTextDeid" / "api.py").read_bytes())[:16]
        return Candidate(result["public"]["text"], (), supported, fixture_id,
                         "clinical-text-deid/v1-" + self.detector + "-direct-" + fingerprint, result)

