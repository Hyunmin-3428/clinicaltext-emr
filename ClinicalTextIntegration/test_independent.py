"""Regressions selected by an independent contract reviewer."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unicodedata
import unittest

from ClinicalTextDeid.api import restore, transform
from ClinicalTextDeid.detectors import detect
from .connected_demo import run_connected


class IndependentContractTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(32)
        self.context = {"project": "synthetic", "document": "doc1", "release": "r1", "purpose": "research"}

    def transformed(self, text, **options):
        return transform({"text": text, "context": self.context, "detector": "rules"} | options, key=self.key)

    def test_nfd_offsets_do_not_insert_zero_length_names(self):
        text = unicodedata.normalize("NFD", "환자 김민준은 흉통.")
        spans = detect(text, detector="rules")
        self.assertTrue(spans)
        self.assertTrue(all(0 <= s["start"] < s["end"] <= len(text) for s in spans))
        result = self.transformed(text)
        self.assertEqual(result["public"]["text"].count("PERSON"), 1)
        self.assertEqual(restore(result, key=self.key, context=self.context), text.encode("utf-8"))

    def test_negation_uncertainty_does_not_become_positive_family_history(self):
        for text in ["Patient denies that father had MI at 52.", "Possible father had MI at 52.", "If father had MI at 52."]:
            with self.subTest(text=text):
                result = self.transformed(text, policy={"mode": "context_rules"})
                self.assertNotIn("positive family history", result["public"]["text"])
                self.assertIn("father had MI at 52", result["public"]["text"])
                self.assertNotEqual(result["assessment"]["semantic_checks"]["status"], "passed_narrow_checks")

    def test_wrong_hint_cannot_report_insulin_unit_preserved(self):
        text = "insulin 10 U SC"
        start = text.index("10 U")
        result = self.transformed(text, hints=[{"start": start, "end": start + 4, "type": "PERSON"}])
        self.assertFalse(result["assessment"]["semantic_checks"]["dose_preserved"])

    def test_wrong_hint_cannot_report_korean_age_preserved(self):
        text = "Patient 47세, chest pain."
        start = text.index("47세")
        result = self.transformed(text, hints=[{"start": start, "end": start + 3, "type": "PERSON"}])
        self.assertFalse(result["assessment"]["semantic_checks"]["numeric_patient_age_preserved_if_required"])

    def test_saved_capsule_restores_original_bytes_in_new_process(self):
        raw = ("\ufeff환자 " + unicodedata.normalize("NFD", "김민준") + "은 흉통.\r\n🩺  \r\n").encode("utf-8")
        result = transform({"utf8_b64": base64.b64encode(raw).decode(), "context": self.context, "detector": "rules"}, key=self.key)
        with TemporaryDirectory(prefix="deid-independent-restart-") as directory:
            internal = Path(directory) / "internal"
            internal.mkdir()
            (internal / "result.json").write_text(json.dumps(result), encoding="utf-8")
            (internal / "context.json").write_text(json.dumps(self.context), encoding="utf-8")
            (internal / "key.bin").write_bytes(self.key)
            proc = subprocess.run([sys.executable, "-m", "ClinicalTextDeid.cli", "restore", "--input", str(internal / "result.json"),
                                   "--context-file", str(internal / "context.json"), "--key-file", str(internal / "key.bin"),
                                   "--output", str(internal / "restored.txt")], capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, "restore CLI rejected a valid saved capsule")
            self.assertEqual((internal / "restored.txt").read_bytes(), raw)
            self.assertNotIn(self.key, proc.stdout + proc.stderr)

    def test_public_directory_cannot_contain_internal_capsule_or_key(self):
        with TemporaryDirectory(prefix="deid-independent-layout-") as directory:
            root = Path(directory)
            (root / "request.json").write_text(json.dumps({"text": "Patient John Smith has MI.", "context": self.context, "detector": "rules"}), encoding="utf-8")
            public = root / "public"
            internal = public / "internal"
            internal.mkdir(parents=True)
            (internal / "key.bin").write_bytes(self.key)
            proc = subprocess.run([sys.executable, "-m", "ClinicalTextDeid.cli", "transform", "--input", str(root / "request.json"),
                                   "--public-out", str(public / "note.json"), "--internal-out", str(internal / "result.json"),
                                   "--key-file", str(internal / "key.bin")], capture_output=True, timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((internal / "result.json").exists())
            self.assertFalse((public / "note.json").exists())
            self.assertEqual((internal / "key.bin").read_bytes(), self.key)

    def test_connected_public_detector_and_actual_egress(self):
        with TemporaryDirectory(prefix="connected-independent-report-") as directory:
            report = run_connected(directory)
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(report["actual_egress_files"], 1)


if __name__ == "__main__":
    unittest.main()
