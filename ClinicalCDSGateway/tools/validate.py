"""Run the substantive suite and save compact, PHI-free result evidence."""
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.entries = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.entries.append({"test": test.id(), "status": "passed"})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.entries.append({"test": test.id(), "status": "failed"})

    def addError(self, test, error):
        super().addError(test, error)
        self.entries.append({"test": test.id(), "status": "error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.entries.append({"test": test.id(), "status": "skipped"})


started = time.monotonic()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=EvidenceResult).run(suite)
report = {"checked_date": "2026-10-08", "timezone": "Asia/Seoul", "python": platform.python_version(),
          "platform": platform.system(), "count": result.testsRun, "failures": len(result.failures),
          "errors": len(result.errors), "skipped": len(result.skipped),
          "seconds": round(time.monotonic() - started, 3), "tests": result.entries,
          "scope": "synthetic only; audit suffix rollback test records an admitted limitation"}
(ROOT / "docs" / "TEST_RESULTS.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ["count", "failures", "errors", "skipped"]}))
sys.exit(0 if result.wasSuccessful() else 1)
