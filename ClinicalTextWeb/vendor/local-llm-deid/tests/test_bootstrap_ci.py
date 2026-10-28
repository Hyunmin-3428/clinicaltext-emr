"""Regression tests for multi-execution bootstrap preparation."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bootstrap_ci  # noqa: E402


FIELDS = ["model_id", "doc", "pass_", "char_tp", "gt_phi_chars", "zero_residual"]


def write_run(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class MultiRunBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.run1 = root / "run1.csv"
        self.run2 = root / "run2.csv"
        base = {"model_id": "model-a", "pass_": "cumulative_pass2", "gt_phi_chars": 10}
        write_run(self.run1, [
            {**base, "doc": "A", "char_tp": 5, "zero_residual": 0},
            {**base, "doc": "B", "char_tp": 10, "zero_residual": 1},
        ])
        write_run(self.run2, [
            {**base, "doc": "A", "char_tp": 7, "zero_residual": 0},
            {**base, "doc": "B", "char_tp": 8, "zero_residual": 0},
        ])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_counts_and_completeness_are_averaged_by_note(self) -> None:
        groups = bootstrap_ci.load_notes([self.run1, self.run2], "cumulative_pass2")
        notes = groups[("model-a", "cumulative_pass2")]
        self.assertEqual(notes, [(6.0, 10.0, 0.0), (9.0, 10.0, 0.5)])
        point, _, _, complete, _, _ = bootstrap_ci.bootstrap_intervals(
            notes, resamples=100, seed=20260801, alpha=0.05)
        self.assertEqual(point, 0.75)
        self.assertEqual(complete, 0.25)

    def test_execution_key_mismatch_is_rejected(self) -> None:
        with self.run2.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))[:1]
        write_run(self.run2, rows)
        with self.assertRaisesRegex(ValueError, "execution key mismatch"):
            bootstrap_ci.load_notes([self.run1, self.run2], "cumulative_pass2")

    def test_duplicate_execution_file_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate per-doc file"):
            bootstrap_ci.load_notes([self.run1, self.run1], "cumulative_pass2")

    def test_invalid_counts_and_completeness_are_rejected(self) -> None:
        invalid = Path(self._tmp.name) / "invalid.csv"
        base = {"model_id": "model-a", "pass_": "cumulative_pass2", "doc": "A",
                "gt_phi_chars": 10}
        cases = [
            {**base, "char_tp": 11, "zero_residual": 0},
            {**base, "char_tp": 5, "zero_residual": 0.5},
            {**base, "char_tp": "nan", "zero_residual": 0},
            {**base, "char_tp": 9.5, "zero_residual": 0},
            {**base, "char_tp": 10, "zero_residual": 0},
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                write_run(invalid, [candidate])
                with self.assertRaises(ValueError):
                    bootstrap_ci.load_notes([invalid], "cumulative_pass2")


if __name__ == "__main__":
    unittest.main()
