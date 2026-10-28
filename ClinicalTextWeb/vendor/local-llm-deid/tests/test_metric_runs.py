"""Tests for matched multi-execution metric averaging."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from metric_runs import load_averaged_metrics  # noqa: E402
import make_results_table  # noqa: E402


FIELDS = [
    "model_id", "pass", "cohort", "phi_type", "developer", "n_docs",
    "n_notes", "doc_chars_total", "gt_phi_chars", "gt_span_count",
    "char_tp", "char_recall",
]


def write_metrics(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(**updates: str) -> dict[str, str]:
    base = {
        "model_id": "model-a", "pass": "cumulative_pass2",
        "cohort": "all_expected", "phi_type": "ALL", "developer": "Example",
        "n_docs": "100", "n_notes": "100", "doc_chars_total": "10000",
        "gt_phi_chars": "1000", "gt_span_count": "300",
        "char_tp": "800", "char_recall": "0.8",
    }
    base.update(updates)
    return base


class MetricRunAveragingTest(unittest.TestCase):
    def test_numeric_outcomes_are_averaged_after_exact_matching(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "run1.csv", root / "run2.csv"
            write_metrics(first, [row()])
            write_metrics(second, [row(char_tp="1000", char_recall="1.0")])
            averaged = load_averaged_metrics([first, second])[0]
            self.assertEqual(averaged["char_tp"], "900")
            self.assertEqual(averaged["char_recall"], "0.9")

    def test_key_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "run1.csv", root / "run2.csv"
            write_metrics(first, [row()])
            write_metrics(second, [row(model_id="model-b")])
            with self.assertRaisesRegex(ValueError, "metrics key mismatch"):
                load_averaged_metrics([first, second])

    def test_duplicate_execution_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "run.csv"
            write_metrics(path, [row()])
            with self.assertRaisesRegex(ValueError, "duplicate metrics file"):
                load_averaged_metrics([path, path])

    def test_all_expected_reference_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "run1.csv", root / "run2.csv"
            write_metrics(first, [row()])
            write_metrics(second, [row(gt_phi_chars="999")])
            with self.assertRaisesRegex(ValueError, "fixed metadata differs.*gt_phi_chars"):
                load_averaged_metrics([first, second])

    def test_nonfinite_outcome_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "run1.csv", root / "run2.csv"
            write_metrics(first, [row()])
            write_metrics(second, [row(char_recall="nan")])
            with self.assertRaisesRegex(ValueError, "non-finite metric"):
                load_averaged_metrics([first, second])

    def test_results_table_delta_uses_averaged_all_expected_runs(self) -> None:
        result_fields = [
            "model_id", "pass", "cohort", "phi_type", "params_total_B",
            "params_effective_B", "params_active_B", "n_docs", "n_notes",
            "doc_chars_total", "gt_phi_chars", "gt_span_count", "char_recall",
            "char_precision", "char_f1", "relaxed_span_f1",
            "zero_residual_notes", "usable_rate",
        ]

        def result_row(pass_label: str, recall: str) -> dict[str, str]:
            return {
                "model_id": "model-a", "pass": pass_label, "cohort": "all_expected",
                "phi_type": "ALL", "params_total_B": "8", "params_effective_B": "",
                "params_active_B": "", "n_docs": "100", "n_notes": "100",
                "doc_chars_total": "10000", "gt_phi_chars": "1000",
                "gt_span_count": "300", "char_recall": recall,
                "char_precision": "0.8", "char_f1": "0.85",
                "relaxed_span_f1": "0.75", "zero_residual_notes": "50",
                "usable_rate": "1.0",
            }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "run1.csv", root / "run2.csv"
            for path, rows in (
                (first, [result_row("pass1", "0.7"), result_row("cumulative_pass2", "0.9")]),
                (second, [result_row("pass1", "0.8"), result_row("cumulative_pass2", "1.0")]),
            ):
                with path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=result_fields)
                    writer.writeheader()
                    writer.writerows(rows)
            selected = make_results_table.select_rows(
                [first, second], "cumulative_pass2", "all_expected"
            )
            self.assertEqual(len(selected), 1)
            self.assertAlmostEqual(float(selected[0]["char_recall"]), 0.95)
            self.assertAlmostEqual(float(selected[0]["delta_recall_pass2"]), 0.20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
